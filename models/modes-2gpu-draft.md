# 2-GPU Mode Definitions — APPLIED (Phase 3, 2026-11-21)

> APPLIED to `scripts/model-manager` + `scripts/preflight.sh` in Phase 3 (2026-11-21).
> Companion: `add_card_plan.md` §4.1 + §6 (Phase 3).
> Rollback drill verified the same day: `state/phase3_drill_20261121.log`
> (qwen-coder → legacy-daily → qwen-coder, timed, logged; vLLM `qwen38` container
> untouched throughout — `State.StartedAt` identical pre/post).
>
> **Phase 3 implementation notes (deviations from this draft):**
> - `cmd_mode_switch` now treats the `media-pipeline` backend as the ComfyUI family
>   (media-gpu1 is the ComfyUI compose's companion service, managed as one stack),
>   so `daily` keeps the running media stack instead of stopping it.
> - `preflight.sh` is 2-GPU aware: both-GPUs-visible check for modes containing
>   `gpu: 1` profiles; per-GPU VRAM budget (GPU 0 additive; GPU 1 XOR tenants —
>   media ⇄ copywriter — checked individually against the 24 GB card).
> - `model-manager gpu1 {status|swap <media|copywriter>}` added (explicit swap).
> - `profile validate` now verifies `device_ids` pinning for `gpu: 1` profiles.
> - Pre-existing bug fixed: `docker compose … --profile image --timeout 30 down` →
>   `--timeout` is a `down` subcommand flag in Compose v5 (was silently no-op'ing
>   the stop via `|| true`; caught by the Phase 3 drill).
>
> Current `MODE_PROFILES` (in `scripts/model-manager`, for reference):
> ```
> MODE_PROFILES[daily]="matrix-coder matrix-gemma4-moe embeddings"
> MODE_PROFILES[qwen-coder]="matrix-coder embeddings"
> MODE_PROFILES[qwen-long]="qwen-long embeddings"
> MODE_PROFILES[llms]="matrix-coder matrix-gemma4-moe embeddings"
> MODE_PROFILES[experiment]="experiment embeddings"
> MODE_PROFILES[images]="comfyui media-pipeline matrix-gemma4-moe embeddings"
> ```

## New / changed modes

### `legacy-daily` (NEW — the rollback target = today's single-card setup)
GPU 0 only. vLLM 27B daily driver + Ollama (gemma4:26b, on-demand) + embeddings.
Media is reachable via the existing `images` mode (on GPU 0). This is the exact
current ledger (`add_card_plan.md` §2) — the guaranteed rollback.
```
MODE_PROFILES[legacy-daily]="matrix-coder matrix-gemma4-moe embeddings"
```
- Compose: `compose/qwen-coder.yml` (GPU 0), `compose/gemma4-moe.yml` (GPU 0),
  `compose/comfyui.yml` (GPU 0, for `images`).
- No GPU 1 involvement. Works with the 2nd card idle or removed.

### `daily` (REDEFINED — the new 2-GPU steady state)
GPU 0 = promoted daily model; GPU 1 = media + copywriter (swappable).
```
# If the bumped 27B wins (Phase 4/5):
MODE_PROFILES[daily]="matrix-coder media-gpu1 ollama-copywriter-gpu1 embeddings"
# If Flash-Next (QNF) wins (Phase 5):
MODE_PROFILES[daily]="matrix-coder-flashnext media-gpu1 ollama-copywriter-gpu1 embeddings"
```
- GPU 0 compose: `compose/qwen-coder.yml` (bumped 27B — promoted from the
  `qwen-coder-bumped.yml` draft in Phase 4, 2026-11-21) **or**
  `compose/experiments/qwen38-flash-next-gguf.yml` (QNF).
- GPU 1 compose: `compose/comfyui-gpu1.yml` (media) + `compose/ollama-gpu1.yml`
  (copywriter). Both pinned `device_ids: ['1']`.
- `embeddings` (nomic-embed-text) stays on the Ollama container (GPU 1).

### `experiment` (UNCHANGED contract)
Candidate on :8000, daily driver stopped. Win: full 72 GB (media moved to GPU 1).
```
MODE_PROFILES[experiment]="experiment embeddings"
```

## New profiles (drafted in `models/profiles/`)
| Profile | File | GPU | Role |
|---|---|---|---|
| `matrix-coder-flashnext` | `models/profiles/matrix-coder-flashnext.yaml` | 0 | QNF daily candidate (llama.cpp) |
| `media-gpu1` | `models/profiles/media-gpu1.yaml` | 1 | Media pipeline (ComfyUI + media-pipeline) |
| `ollama-copywriter-gpu1` | `models/profiles/ollama-copywriter-gpu1.yaml` | 1 | Copywriter (gemma4:31b) |

## model-manager changes (Phase 3 applies)
1. Add `MODE_PROFILES[legacy-daily]` (above).
2. Redefine `MODE_PROFILES[daily]` to the 2-GPU profile set (above).
3. Add a `gpu1 swap {media|copywriter}` subcommand: stops one GPU 1 tenant, starts the
   other (the explicit swap; the idle-unload + headroom guard handle the implicit case).
4. Profile validation: for GPU 1 profiles, assert `device_ids: ['1']` is present in the
   compose (catch a mis-pin to GPU 0).
5. `preflight <MODE>` for `daily` must verify BOTH GPUs are visible (nvidia-smi shows 2)
   before switching.

## model-manager integration (Phase 3 — open question §7.1, RESOLVED)
`homelab.sh` **is `scripts/model-manager`** (settled 2026-11-21, Chuck: no
separate wrapper script exists — the mode-switch entry point is the model-manager
script itself). External callers keep working by calling:
```
model-manager mode switch --yes {legacy-daily|daily|experiment}
```
**Status (2026-11-21):** resolved — the call shape above is verified working for
all three modes (Phase 3 drill exercised `legacy-daily`; `daily` and `experiment`
verified via plan display + preflight). The script header documents this as the
external call shape.