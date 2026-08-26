# ComfyUI — Image Generation & Editing (Qwen-Image)

> Updated: 2026-08-26 (supersedes the old "images mode" / stop-vLLM model)
> Compose: `compose/comfyui.yml` (profile: `image`)
> Profile: `models/profiles/comfyui.yaml`
> **API reference for tooling: `docs/matrix_comfyui_media_api.md`**

## Overview

ComfyUI runs **concurrently with vLLM** — no mode switch, no stopping vLLM, no
downtime. ComfyUI is capped at a ~12 GB VRAM budget via `--reserve-vram 60`
(reserves 60 GB for other software), and Qwen-Image's dynamic-VRAM streaming
(9.4 GB encoder + 11.9 GB DiT are never resident at the same time) keeps it
inside the budget.

**`matrix-coder` (vLLM) stays fully online during all image work.**

| Service | Container | Port | VRAM |
|---|---|---|---|
| vLLM (Qwen3.8-27B NVFP4) | `matrix` | 8000 | ~56 GB (untouched by ComfyUI) |
| ComfyUI | `comfyui_backend` | 8188 | ~12 GB budget; 9.3–14.4 GB measured peaks |
| Gemma4 MoE + embeddings | `ollama` | 11434 | on demand |
| Metrics | `node-exporter`, `dcgm-exporter` | 9100, 9400 | N/A |

Measured total-GPU peaks during image jobs: **70.2–71.2 GB of 73.4 GB** —
under the ~70 GB acceptance gate, vLLM memory identical before/after.

## What it does

- **Create image**: text prompt → 1920×1080 PNG (720p render → 4x upscale →
  lanczos). Qwen-Image-2512 Q4_0 GGUF + 4-step Lightning LoRA. ~15–40 s.
- **Edit image**: existing image + text instruction → edited image.
  Qwen-Image-Edit-2511 Q4_0 GGUF + 8-step Lightning LoRA. ~45–60 s.
- Legible in-image text (verified by OCR), stable iteration loop for edits.

Full API contract (endpoints, workflow JSON, reference client, error handling):
**`docs/matrix_comfyui_media_api.md`**.

## Operations

### Start / stop

```bash
cd /home/chuck/homelab
docker compose -f compose/comfyui.yml --profile image up -d     # start
curl -s http://localhost:8188/system_stats | head -c 100        # verify
docker compose -f compose/comfyui.yml --profile image down      # stop (frees ~0.7 GB idle cache)
```

- ComfyUI is **not** in the default `docker compose up` — start it explicitly
  when image work is needed. It is safe to leave running (idle cost ~0.7 GB).
- Models are **not** downloaded on first run — all files are pre-downloaded
  (see inventory in the API doc §6). No `HF_TOKEN` needed.

### Health / troubleshooting

```bash
curl -s http://localhost:8188/system_stats          # liveness + version
curl -s http://localhost:8188/queue                 # queue depth
docker logs comfyui_backend --tail 50               # errors
nvidia-smi                                          # VRAM
```

| Symptom | Action |
|---|---|
| Port 8188 not responding | `up -d` above; check logs |
| Job errors with OOM | Retry (transient); or use the Q3_K_M fallback config (API doc §4.3) |
| Job queued but slow | `GET /queue` (job ahead), `docker logs` |
| `comfyui-mmaudio` import warning at startup | Fixed 2026-08-26 (numba 0.67 / numpy 2.5). If it reappears: `sudo -u comfy /comfy/mnt/venv/bin/pip install -U numba llvmlite` in the container |

### Model management

- Models live in `/home/chuck/data/comfyui/basedir/models/` (owned by uid 1024
  `comfy`). Add files as the `comfy` user inside the container:
  `docker exec comfyui_backend sudo -u comfy sh -c '…'`.
- Model lists refresh automatically when files appear — no restart needed.
- venv for pip installs: `/comfy/mnt/venv` (always `sudo -u comfy`).

### Security

- **Do NOT expose port 8188 publicly.** No authentication by default.
- `SECURITY_LEVEL=weak` is intentional (LAN only) — allows file access for
  workspace operations.

## VRAM budget

| Component | VRAM | Notes |
|---|---|---|
| vLLM | ~56.3 GB | Committed baseline; never displaced |
| ComfyUI | ~12 GB cap | `--reserve-vram 60` (soft budget, dynamic VRAM) |
| Measured peaks | 9.3–14.4 GB attributable | 71.2 GB total worst case |
| GPU total | 72 GB (73,415 MiB) | Gate: total peak ≤ ~70 GB — all runs passed |

Idle ComfyUI retains ~0.7 GB (model cache) — normal.

## History

- 2026-07-03: original "images mode" — ComfyUI (FLUX) at 30–40 GB required
  stopping vLLM; manual mode switch with downtime. **Superseded.**
- 2026-08-26: Qwen-Image-2512 + Qwen-Image-Edit-2511 (GGUF) at a 12 GB budget
  via `--reserve-vram 60`; full coexistence with vLLM; create + edit flows
  verified end-to-end (VRAM, timing, OCR).