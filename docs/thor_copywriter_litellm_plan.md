# Plan: Expose Gemma 4 31B (GPU 1 copywriter) via Thor LiteLLM

> Created: 2026-11-25
> Status: **CLOSED (2026-11-26)** — all steps executed on Thor and verified end-to-end
> (serves / GPU-1 load / idle-unload / fallback / evict-on-media / manual swap).
> Option A from the 2026-11-25 GPU-1 sidecar discussion: Ollama tenant on GPU 1,
> exposed through Thor LiteLLM with a fallback to `matrix-coder`.

## Goal

`gemma4:31b` (the GPU 1 copywriter, already pulled + validated) becomes a selectable
model in Thor LiteLLM as **`matrix-copywriter`**, with:

- **Automatic VRAM coordination** (already live on Matrix — no work):
  media-job headroom guard evicts it (`server.py _ensure_vram_headroom`),
  `OLLAMA_KEEP_ALIVE=5m` idle-unload, on-demand load (~10–46 s) on first request.
- **Graceful degradation**: if the model can't load because media owns GPU 1,
  the request falls back to `matrix-coder` (72 GB daily driver) instead of erroring.
- **Manual override** unchanged: `model-manager gpu1 swap {media|copywriter}`.

## What already exists (do NOT redo)

| Piece | Where | State |
|---|---|---|
| `gemma4:31b` weights | Matrix Ollama (GPU 1, `device_ids: ['1']`) | Pulled, validated (46 s load, ~30 tok/s, 22.1 GB @ 64K) |
| Idle unload | `OLLAMA_KEEP_ALIVE=5m` in `compose/gemma4-moe.yml` | Live |
| Evict-on-media | `media-pipeline/server.py` VRAM headroom guard | Live (Phase 6) |
| Manual swap | `model-manager gpu1 swap {media\|copywriter}` | Live |
| Ollama endpoint for Thor | `http://matrix:11434` (contract §2) | Live |

The ONLY missing piece is the Thor LiteLLM entry (+ fallback). The
`ollama-copywriter-gpu1` profile has carried the note "Thor entry" since Phase 3 —
this plan closes it.

---

## Step 1 — Thor: add the model entry

In Thor's `thor.litellm.config.yml` (the file that already defines `matrix-coder`,
`matrix-gemma4-moe`, `embeddings`), add to `model_list`:

```yaml
  - model_name: matrix-copywriter
    litellm_params:
      model: ollama/gemma4:31b
      api_base: http://matrix:11434
      timeout: 180        # first request after idle-unload triggers a ~46 s load
```

Mirrors the existing `matrix-gemma4-moe` entry pattern (`ollama/<model>` →
`http://matrix:11434`) — same provider, same endpoint, bigger model.

## Step 2 — Thor: add the fallback

In the same file, under `litellm_settings` (create the key if absent):

```yaml
litellm_settings:
  model_group_fallbacks:
    - matrix-copywriter: [matrix-coder]
```

Semantics: if `matrix-copywriter` fails (model can't load — GPU 1 held by media,
or Ollama down), LiteLLM retries the request on `matrix-coder`. The caller gets an
answer from the 72 GB daily driver, not an error. Optionally add
`num_retries: 1` on the copywriter entry so the fallback fires quickly instead of
retrying a load that can't fit.

## Step 3 — Thor: reload LiteLLM

Restart/reload however LiteLLM runs on Thor (docker compose / systemd). Confirm it
came up clean:

```bash
curl -s http://localhost:<litellm-port>/model/info | python3 -m json.tool | grep -A3 copywriter
```

Expect `matrix-copywriter` listed with `litellm_model_name: ollama/gemma4:31b`.

## Step 4 — Verification checklist

Run 1–3 from Thor, 4–7 from Matrix. All must pass before closing the TODO.

- [x] **1. Listed**: `matrix-copywriter` appears in `/model/info` (above).
- [x] **2. Serves**:
      ```bash
      curl -s http://localhost:<litellm-port>/v1/chat/completions \
        -H "Content-Type: application/json" \
        -d '{"model":"matrix-copywriter","messages":[{"role":"user","content":"Say exactly: GPU1 OK"}],"max_tokens":512}'
      ```
      → coherent reply. (First call may take ~46 s — the on-demand load.)
- [x] **3. Loaded on GPU 1** (Matrix):
      ```bash
      docker exec ollama ollama ps        # gemma4:31b listed, PROCESSOR 100% GPU
      nvidia-smi -i 1 --query-gpu=memory.used --format=csv   # ~22 GB
      ```
- [x] **4. Idle unload** (Matrix): wait 5 min with no requests → `ollama ps` empty,
      `nvidia-smi -i 1` back to media-pipeline baseline.
- [x] **5. Fallback**: with GPU 1 held (ComfyUI hot at ~18 GB, or a media job
      running), request `matrix-copywriter` from Thor → reply comes back from
      `qwen38-27b` (check LiteLLM logs for the fallback event; the response content
      is the tell — 31B vs 27B style). No 5xx to the caller.
- [x] **6. Evict-on-media**: load the copywriter (step 2), then queue a small media
      job (e.g., TTS) → pipeline log shows the headroom-guard `ollama stop`, job
      completes, no OOM.
- [x] **7. Manual swap still works**: `model-manager gpu1 swap media` then
      `model-manager gpu1 swap copywriter` — clean both ways.

## Step 5 — Doc updates (this repo, after verification)

1. **`docs/matrix_thor_contract.md` → v1.5** (done 2026-11-26; the contract was at v1.4 when this plan was written): added the row to §3 alias table:
   ```
   | `matrix-copywriter` | `ollama/gemma4:31b` → `http://matrix:11434` | Ollama port 11434 (GPU 1) | `daily`, `llms` — XOR-tenant with media-gpu1; falls back to `matrix-coder` when GPU 1 is media-held |
   ```
   and a note under the alias table: `matrix-copywriter` has
   `model_group_fallbacks → [matrix-coder]` (2026-11-26).
2. **`models/profiles/ollama-copywriter-gpu1.yaml`**: change
   `alias: matrix-copywriter  # NEW LiteLLM alias (Phase 3/4 adds the Thor entry)`
   → `alias: matrix-copywriter  # LIVE in Thor LiteLLM since 2026-11-26 (fallback → matrix-coder)`. (Done 2026-11-26.)
3. **`TODO.md`**: close the checkbox (see entry below).

## Caveats / known behavior

- **Reasoning model**: gemma4:31b is a reasoning model — it needs a generous output
  budget (profile note). Clients that cap `max_tokens` low will see truncated
  reasoning; if that bites, raise the cap per-client or set a sensible
  `max_tokens` default in the `litellm_params` entry.
- **First-request latency**: ~10–46 s cold load after idle-unload. Thor-side clients
  with aggressive timeouts (pi/codex) should tolerate ≥ 60 s on this model, or
  accept that the fallback may fire on a slow load.
- **Hard XOR with hot ComfyUI**: ComfyUI resident (~18 GB) + 22 GB model > 24 GB —
  the model simply cannot load while ComfyUI is hot. That's by design; the fallback
  makes it painless. The headroom guard covers the GPU *workers* (music/tts/
  transcribe); it does not (and should not) kill ComfyUI mid-job.
- **No daemon added**: per the model-manager design rule ("NOT an automation
  daemon"), all automation here is the existing guard + keep_alive + on-demand
  load. `gpu1 swap` remains the manual override.
- **Swap-in later**: the entry is model-agnostic in shape — pointing
  `matrix-copywriter` at a different Ollama model (e.g., `inkling2-mini` GGUF) is a
  one-line change on Thor when Experiment 9's model graduates.
