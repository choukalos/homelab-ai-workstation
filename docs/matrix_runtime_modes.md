# Matrix Runtime Modes

> Documented: 2026-07-03, updated 2026-08-25 (Qwen3.8-27B NVFP4 promotion + speed fix; primary container renamed `qwen36` → `qwen38`), **updated 2026-11-21 (2-GPU era: GPU 1 for media/copywriter, daily bumped to 0.95/seqs5/256K, `legacy-daily` = smaller-footprint rollback — see add_card_plan.md Phases 1–4)**

## How Modes Work

Matrix has two GPUs: **GPU 0 = RTX PRO 5000 (72 GB)** — the daily LLM (vLLM, :8000) — and
**GPU 1 = RTX PRO 4000 (24 GB)** — media pipeline (ComfyUI) XOR Ollama copywriter (gemma4:31b),
XOR-enforced by the media pipeline's headroom guard. Each mode defines which models run, how much
VRAM each gets, and which LiteLLM aliases are valid. Mode switches require stopping/restarting
containers. Modes that don't list the media stack **stop it** (converge-to-target); restore with
`docker compose -f compose/comfyui.yml --profile image up -d`.

The `matrix-coder` LiteLLM alias always points at port 8000 — whatever model is running
there is "the main model" to clients.

---

## `daily` — Normal Chat/Coding Use

**What's running (2-GPU steady state):**

| Service | Container | GPU | Model | Port | Backend | VRAM |
|---|---|---|---|---|---|---|
| Primary model | `qwen38` | 0 | unsloth/Qwen3.8-27B-NVFP4 | 8000 | vLLM | ~69 GB (0.95 util) |
| Media stack | `comfyui_backend` + `media_pipeline` | 1 | LTXV 13B + SeedVR2 + Qwen-Image | 8188/8189 | ComfyUI | ~10–12 GB (models resident) |
| Copywriter | `ollama` | 1 | gemma4:31b (Q4_K_XL) | 11434 | Ollama | ~22 GB (on demand, XOR media) |
| Embeddings | `ollama` | 1 | nomic-embed-text (F16) | 11434 | Ollama | ~274 MB |
| Metrics | `node-exporter`, `dcgm-exporter` | — | — | 9100, 9400 | — | N/A |

**vLLM args (from compose/qwen-coder.yml — bumped 2026-11-21, add_card_plan Phase 4):**
- `--gpu-memory-utilization 0.95` → ~69 GB reserved (GPU 0 is dedicated to the daily LLM)
- `--max-model-len 262144` (256K)
- `--max-num-seqs 5`
- `--max-num-batched-tokens 8192`
- `--kv-cache-dtype fp8`
- `--enable-prefix-caching`
- `--enable-chunked-prefill`
- `--enable-auto-tool-choice`
- `--tool-call-parser qwen3_coder`
- `--reasoning-parser qwen3`
- `--speculative-config '{"method":"mtp","num_speculative_tokens":2}'`
- `--default-chat-template-kwargs '{"preserve_thinking": true, "reasoning_effort": "high"}'`
- `--override-generation-config '{"temperature": 0.7, "top_p": 0.95}'`

**Ollama config:**
- `OLLAMA_MAX_LOADED_MODELS=2`
- `OLLAMA_KEEP_ALIVE` = 5m (secondary models release VRAM when idle)
- Models loaded on demand; gemma4:26b loads when first requested

**LiteLLM aliases valid:** `matrix-coder`, `matrix-gemma4-moe`, `embeddings`

**Startup (rebuild after reboot):**
```bash
cd /home/chuck/homelab
docker compose -f compose/metrics.yml up -d
docker compose -f compose/qwen-coder.yml up -d
docker compose -f compose/comfyui.yml --profile image up -d   # media stack (GPU 1)
# Ollama is a plain container (ollama:latest) — start with `docker start ollama`
```

**Health checks:**
```bash
curl -s http://localhost:8000/v1/models        # vLLM Qwen
curl -s http://localhost:11434                  # Ollama
curl -s http://localhost:9100/metrics | head -1 # node-exporter
curl -s http://localhost:9400/metrics | head -1 # dcgm-exporter
```

**Expected downtime for switch to another mode:** ~5-10 min (vLLM stop + VRAM release + new container)

---

## `qwen-coder` — Best Coding Performance

**Goal:** Give Qwen as much VRAM as possible for better context window and throughput.

**What's running:**

| Service | Container | GPU | Model | Port | Backend | VRAM |
|---|---|---|---|---|---|---|
| Primary model | `qwen38` | 0 | unsloth/Qwen3.8-27B-NVFP4 | 8000 | vLLM | ~69 GB (gpu-mem 0.95) |
| Embeddings | `ollama` | 1 | nomic-embed-text | 11434 | Ollama | ~274 MB |

**Changes from daily:**
- Same model and vLLM args as daily (`compose/qwen-coder.yml`)
- Ollama runs embeddings only (copywriter unloaded from GPU 1)
- The media stack (ComfyUI) is not part of this mode — if it's running it keeps running;
  a switch *from* a mode that lists it will stop it (see How Modes Work)

**LiteLLM aliases valid:** `matrix-coder`, `embeddings`
**Aliases offline:** `matrix-gemma4-moe` (gemma4 unloaded from Ollama)

**Startup:**
```bash
# Stop Gemma in Ollama
docker exec ollama ollama stop gemma4:26b
# vLLM: standard production config
docker compose -f compose/qwen-coder.yml down
docker compose -f compose/qwen-coder.yml up -d
```

**Health check:** `curl -s http://localhost:8000/v1/models`

**Rollback to daily:** Restart Ollama with gemma4 loaded (vLLM unchanged).

---

## `legacy-daily` — Smaller-Footprint Rollback (pre-Phase-4 config)

> Added 2026-11-21 (add_card_plan Phase 4). The pre-bump daily config, preserved as the
> rollback target: `--gpu-memory-utilization 0.75`, `--max-num-seqs 3`, `--max-model-len 196608`
> (`compose/legacy/compose.qwen-coder-075-14gb-media.yml` + profile `matrix-coder-partial`).
> Note: the 0.75 config existed to leave ~14 GB on GPU 0 for the small (GPU-0) media pipeline —
> that co-location no longer exists (media is on GPU 1), so the headroom is now just headroom.

**What's running:** same as `daily` except the vLLM args (0.75/seqs3/192K, ~56 GB on GPU 0)
and gemma4:26b loaded in Ollama.

**Switch:** `model-manager mode switch --yes legacy-daily` (~3 min; vLLM swap + gemma4 load).
**Verified:** Phase 4 drill 2026-11-21 (`state/phase4_drill_20261121.log`) — 187 s to the
partial config (max_model_len 196608, GPU 0 56.2 GB), 147 s back, media stack restored in ~15 s.
Transparent to clients: the LiteLLM alias `matrix-coder` serves `qwen38-27b` either way.

---

## ~~`qwen-long` — Long-Context Work~~ — RETIRED 2026-11-21

> **Retired (add_card_plan Phase 4):** the mode was never verified end-to-end, and the
> bumped 27B daily now serves **256K natively** (was 192K) — the use case (long-context
> work on Qwen3.6-27B INT4 240K) is covered by the daily. `compose/qwen-long.yml` +
> `models/profiles/qwen-long.yaml` deleted 2026-11-21; mode removed from
> `model-manager` / `preflight.sh` / `matrix_health.sh`. The 18 GB
> `Lorbus/Qwen3.6-27b-int4-AutoRound` weights remain on disk (re-downloadable from HF —
> optional cleanup). Revisit only if >256K contexts become needed.

---

## `llms` — Multi-Model Tool Experiments

**Goal:** Run Qwen + Gemma simultaneously for multi-model experiments.

**What's running:**

| Service | Container | Model | Port | Backend | VRAM |
|---|---|---|---|---|---|
| Primary model | `qwen38` | unsloth/Qwen3.8-27B-NVFP4 | 8000 | vLLM | ~54 GB |
| Light model | `ollama` | gemma4:26b | 11434 | Ollama | ~17 GB |
| Embeddings | `ollama` | nomic-embed-text | 11434 | Ollama | ~274 MB |

**Note:** This is essentially the same as `daily` mode. The distinction is semantic:
in `llms` mode you're actively using both models together.

**LiteLLM aliases valid:** `matrix-coder`, `matrix-gemma4-moe`, `embeddings`

**Current production state as of 2026-11-21: `qwen-coder`** (daily's vLLM config, copywriter
on-demand on GPU 1, media stack running).

---

## `experiment` — Candidate Model Swap-Out

**Goal:** Temporarily replace Qwen on port 8000 with a different model for testing.

**What's running:**

| Service | Container | Model | Port | Backend | VRAM |
|---|---|---|---|---|---|
| Experiment | `qwen38` (same container, different model) | *[candidate]* | 8000 | vLLM | varies |
| Light/embed | `ollama` | depends on candidate size | 11434 | Ollama | varies |

**Key principle:** The LiteLLM alias `matrix-coder` points at port 8000. Whatever model
serves port 8000 IS `matrix-coder` to clients. No config changes needed.

**Startup:**
```bash
# Stop current vLLM
docker compose -f compose/qwen-coder.yml down
# Start vLLM with different model (manual command or new compose)
docker run --rm -d --name qwen38 \
  --gpus all --shm-size 16g -p 8000:8000 \
  -v /home/chuck/data/models:/data/models \
  -e HF_HOME=/data/models \
  vllm/vllm-openai:latest \
  --model <candidate-model> --host 0.0.0.0 --port 8000 ...
```

**⚠️ Operator responsibility:** Track which model is actually live. When done,
restore the production Qwen model immediately.

**Rollback to daily:**
```bash
docker compose -f compose/qwen-coder.yml down
docker compose -f compose/qwen-coder.yml up -d
```

---

## Image generation (ComfyUI) — not a mode

> Updated 2026-08-27: image generation no longer requires stopping vLLM.
> **Updated 2026-11-21 (Phase 1):** the media stack now runs on **GPU 1** (24 GB card) —
> `device_ids: ['1']`, no `--reserve-vram`. GPU 0 is untouched. The media pipeline's
> headroom guard enforces the GPU-1 XOR with the Ollama copywriter (frees ComfyUI's VRAM
> before a copywriter load if <8 GB is free).

**Start / stop:**
```bash
docker compose -f compose/comfyui.yml --profile image up -d    # start (ComfyUI + media-pipeline)
curl -s http://localhost:8188/system_stats | head -c 100       # verify ComfyUI
curl -s http://localhost:8189/health                           # verify media-pipeline
docker compose -f compose/comfyui.yml --profile image down     # stop (both)
```

**VRAM (GPU 1):** models resident ~10–12 GB when idle after use; peaks ~16 GB (480p Wan
upscale). Idle cost ~0.7 GB — safe to leave running.

**Media-pipeline rebuild** (after code changes): `model-manager rebuild media-pipeline`.

**Details:** [ComfyUI Media API](matrix_comfyui_media_api.md) (tooling contract) ·
[ComfyUI Ops](matrix_images_mode.md) (operations) · `media-pipeline/` (service) ·
`media-mcp-client/` (pointer to the `mcp_media` MCP server — homelab-ai-harness repo, `mcp/servers/media`)

---

## Mode Switch Matrix

| Target mode | Stop containers | Start containers | Aliases affected |
|---|---|---|---|
| `daily` | (none — this is current) | (none) | all valid |
| `qwen-coder` | Ollama gemma4 (unload) | vLLM @ 0.95/seqs5/256K | gemma4-moe offline |
| `legacy-daily` | vLLM (swap to 0.75/seqs3/192K) | vLLM partial + gemma4:26b | coder → partial (same served name) |
| `llms` | (same as daily) | (same as daily) | all valid |
| `experiment` | vLLM | vLLM with new model | coder points to experiment |

(Image generation is not a mode — see the section above.)

---

## VRAM Budget (2-GPU)

**GPU 0 (72 GB) — daily LLM:**

| Mode | vLLM | Headroom |
|---|---|---|
| `daily` / `qwen-coder` / `llms` | ~69 GB (0.95 util, Phase 4) | ~3 GB (KV pool pre-allocated) |
| `legacy-daily` | ~56 GB (0.75 util) | ~16 GB |
| `experiment` | varies | varies |

**GPU 1 (24 GB) — media XOR copywriter:**

| Tenant | VRAM | Notes |
|---|---|---|
| Media stack (ComfyUI + pipeline) | ~10–12 GB resident, ~16 GB peaks | LTXV/SeedVR2/Qwen-Image models |
| Copywriter (gemma4:31b) | ~22 GB @ 32K ctx (64K default) | Ollama, unloads after 5 min idle |
| Both (XOR violation) | — | headroom guard frees ComfyUI VRAM before a copywriter load if <8 GB free |
