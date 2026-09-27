# ComfyUI — Image Generation & Editing (Qwen-Image)

> Updated: 2026-09-23 (Qwen-Image-2.1 default; supersedes the old "images mode" / stop-vLLM model)
> Compose: `compose/comfyui.yml` (profile: `image`)
> Profile: `models/profiles/comfyui.yaml`
> **API reference for tooling: `docs/matrix_comfyui_media_api.md`**
> **Upgrade runbook: `media_todo.md`** (Qwen-Image-2.1, 2026-09-23)

## Overview

ComfyUI runs **concurrently with vLLM** — no mode switch, no stopping vLLM, no
downtime. ComfyUI is capped at a ~12 GB VRAM budget via `--reserve-vram 60`
(reserves 60 GB for other software). Since 2026-09-23 the default image model
is **Qwen-Image-2.1** (7B single-stream DiT int8_convrot + Qwen3-VL 8B int8
text encoder + 64-ch RGBA VAE, ComfyUI v0.37.0 native nodes) — smaller than
the legacy 20B GGUFs and fast to stream in/out. The legacy
Qwen-Image-2512/2511 Q4_0 GGUF + Lightning path remains available as
`model=legacy` (per request or `MEDIA_IMAGE_MODEL=legacy` in `.env`).

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

- **Create image** (default, since 2026-09-23): text prompt → PNG. **Qwen-Image-2.1**
  (7B unified DiT, int8_convrot), 25 steps, cfg=1.0, euler/simple. ~1 MP
  (1280×720) render; larger outputs via the existing SeedVR2/4xUltrasharp
  upscale path. ~30–120 s per image (no distilled LoRA at launch).
- **Edit image** (default): existing image + text instruction → edited image,
  **same Qwen-Image-2.1 model** (unified T2I + edit), canvas follows the edited
  image. Supports **up to 9 additional reference images** (10 total) for
  character/product consistency across shots (`references` field on
  `POST /images/edit`).
- **Legacy path** (`model=legacy`): Qwen-Image-2512 Q4_0 GGUF + 4-step Lightning
  LoRA (create), Qwen-Image-Edit-2511 Q4_0 GGUF + 8-step Lightning LoRA (edit).
  ~15–60 s. Kept as the rollback/fallback during the transition.
- Legible in-image text (verified by OCR), stable iteration loop for edits.

Full API contract (endpoints, workflow JSON, reference client, error handling):
**`docs/matrix_comfyui_media_api.md`**.

## Media pipeline orchestrator (port 8189)

The **media-pipeline** service (container `media_pipeline`, image `media-pipeline:latest`) runs in the
**same compose file + `image` profile** as ComfyUI, so it starts/stops with it. It is a thin FastAPI
orchestrator (no GPU of its own) that drives ComfyUI + vLLM to produce full media: storyboard →
keyframes → I2V shots → TTS/music/SFX → SeedVR2 upscale → ffmpeg assembly. It binds `0.0.0.0:8189`
(LAN/remote-MCP only) and uses host networking + the docker socket to reach ComfyUI/VLLM and run
`docker exec`/`cp` into `comfyui_backend`.

```bash
curl -s http://localhost:8189/health                 # {ok, max_concurrent, max_queue_depth, max_pending, pending, running, queued, queue_depth}
model-manager rebuild media-pipeline                 # rebuild image from source + recreate (after code changes)
```

Bounded job queue: at most `MAX_CONCURRENT_JOBS` (default 1) media jobs run at once; the rest wait
with `status=queued`. Waiting depth is capped by `MAX_QUEUE_DEPTH` (default 5) — total in-flight
= `MAX_CONCURRENT_JOBS + MAX_QUEUE_DEPTH` (default 6); when full, new jobs are rejected with
`HTTP 503` + a `retry_after_seconds` back-off. Both set in `/home/chuck/homelab/.env`.
See `media-pipeline/` (build context) and the remote client in `media-mcp-client/` (pointer to `mcp/servers/media` in the homelab-ai-harness repo).

**Full API contract + metering:** `docs/matrix_media_pipeline_api.md` (endpoints, job model,
queue, VRAM budget, commercial recipe). Since 2026-09-06 the pipeline attributes every job to
its `user`/`client` (optional fields on every job POST) and measures work units (mpix_steps /
mpix_frames / audio_seconds) at calibrated full-cost rates: `GET /metrics` (Prometheus
`media_*` job/cost metrics, scraped by Thor) + one durable line per job at
`run/media_jobs/metrics/jobs.jsonl`. Kill switch: `MEDIA_METRICS_ENABLED=false` in `.env`.
Verification + calibration log: `docs/matrix_validation_log.md` (2026-09-06 run).

**Since 2026-09-07 (Part 1 gap-fill):** the pipeline also runs **CPU ffmpeg job flows**
(`trim`, `freeze`, `caption` — no generative model, metered as model `ffmpeg` at 0 GPU work
units), an extended `/assemble` (object shots with in/out trims, timestamped SFX list,
`vo_start`, `loudnorm`), and **sync endpoints** for client file transfer: `GET /info` (ffprobe),
`POST /upload_local` (basedir-confined copy into `media_jobs/uploads/`), `POST /download`
(URL ingest), `POST /upload` (multipart, 500 MB cap), `POST /dl_token` + `GET /dl/{token}`
(signed, path-bound, time-limited pull URLs for off-LAN clients — :8189 itself stays
unauthenticated; public auth is the Caddy layer on thor). QA: 38/38
(`media-pipeline/qa_part1.py`, 2026-09-07 run in `docs/matrix_validation_log.md`).

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
- 2026-08-27: **media-pipeline** orchestrator containerized (Docker, `image` profile) and integrated
  into `model-manager` (starts/stops with ComfyUI; `model-manager rebuild media-pipeline`).
- 2026-09-23: **Qwen-Image-2.1 upgrade** — ComfyUI pinned to v0.37.0 (day-0
  release, native `TextEncodeQwenImage21`/`QwenImage21Cache` nodes), int8_convrot
  weights (7.26 GB DiT + 9.35 GB Qwen3-VL 8B int8 encoder + 0.68 GB RGBA VAE),
  new default for create + edit (unified model, up to 10 images in the edit
  flow). Legacy 2512/2511 GGUF + Lightning kept behind `model=legacy` /
  `MEDIA_IMAGE_MODEL=legacy`. Runbook: `media_todo.md`;
  `scripts/qwen21_upgrade_matrix.sh` (matrix side).