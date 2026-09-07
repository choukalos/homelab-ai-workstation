# Media-Pipeline HTTP API (port 8189) — THE CONTRACT

> **Audience:** media-mcp developers, integrators, and anyone calling the
> media-pipeline service.
> **Status:** verified live 2026-09-07 (all 12 job flows + 6 sync endpoints +
> metering; QA suite `media-pipeline/qa_part1.py`, 38/38).
> **Supersedes:** the API contract section of the old `workspace/media-todo.md`
> (deleted 2026-09-06 — project complete).
> **Related:** `docs/matrix_images_mode.md` §Media pipeline orchestrator (ops),
> `docs/matrix_comfyui_media_api.md` (low-level ComfyUI :8188 API),
> `docs/matrix_validation_log.md` (2026-09-06 run — metering verification + calibration),
> `media-mcp-client/README.md` (remote-side client + MCP tools).

The **media-pipeline** service (container `media_pipeline`, image
`media-pipeline:latest`) is a thin FastAPI orchestrator on the GPU host with no
GPU of its own. It owns the GPU job queue, drives ComfyUI (:8188) + vLLM
(:8000), spawns TTS/ACE-Step workers, and does ffmpeg assembly.

Base URL: `http://<gpu-host>:8189` (LAN-only, no auth — never expose publicly.
Public auth is the Caddy layer on thor only; see `auth_todo.md`).

---

## 1. Job model (all flows are async)

Every `POST /<flow>` returns immediately:

```json
{"job_id": "a8530f40171b"}
```

Poll:

```
GET /jobs/{job_id}
→ {"job_id":"...","status":"queued|running|done|error|timeout",
   "output":{...},"error":"...","user":"...","client":"..."}
```

- `status=done` → `output` holds result paths (host paths on the GPU host).
- `status=error` → `error` holds the message.
- `status=timeout` → the worker exceeded its per-flow timeout (added 2026-09-06;
  previously timeouts surfaced as `error`).
- **Identity:** every job POST accepts optional `user` and `client` fields
  (JSON body or Form fields). They are recorded on the job and in the metering
  output (§5). Omitted → `unknown`. The remote media-mcp server should forward
  the caller's identity (see `media-mcp-client/`).
- GPU flows are **serialized** by an internal GPU lock (`gpu_locked` in
  `/health`); the bounded queue (§3) serializes all flows.

## 2. Endpoints

| Method | Path | Inputs | `output` on done |
|---|---|---|---|
| GET | `/health` | — | `{ok, gpu_locked, jobs, max_concurrent, max_queue_depth, max_pending, pending, running, queued, queue_depth}` |
| GET | `/jobs/{id}` | — | job status (above; `queue_position` while queued) |
| GET | `/files/{name:path}` | — | file bytes (download; name = path relative to the run dir) |
| GET | `/metrics` | — | Prometheus text format (metering; 404 when disabled — §5) |
| POST | `/storyboard` | JSON `{brief, n_shots=5, aspect="16:9"}` | `{"storyboard":"<path>/storyboard.json","n_shots":N,"usage":{prompt_tokens,completion_tokens,total_tokens}}` |
| POST | `/images` | JSON `{prompt, width=1280, height=720, seed=42, lora?, steps=4}` | `{"image":"<path>.png"}` |
| POST | `/images/edit` | multipart `file, prompt, seed=42, steps=8` | `{"image":"<path>.png"}` |
| POST | `/shots` | multipart `file(keyframe), prompt, width=768, height=512, frames=97, fps=24, steps=8, strength=0.7, seed=42` | `{"video":"<path>.mp4","frames":N,"fps":F}` |
| POST | `/tts` | JSON `{text, voice="trailer"}` | `{"audio":"<path>/vo.wav"}` |
| POST | `/music` | JSON `{prompt, lyrics="", duration=30, seed=42}` | `{"audio":"<path>/music.wav"}` |
| POST | `/sfx` | multipart `file(video), duration=8, steps=25, cfg=4.5, seed=42, prompt="", negative_prompt="", fps=24` | `{"audio":"<path>.flac"}` |
| POST | `/upscale` | multipart `file(video), pipeline="b"\|"a2", resolution=1080, noise_scale=0.0, fps=24, seed=42` | `{"video":"<path>.mp4"}` |
| POST | `/assemble` | JSON `{shots, vo?, music?, sfx?, width=1920, height=1080, fps=24, vo_volume=1.0, music_volume=0.35, sfx_volume=0.9, vo_start=0.0, loudnorm=false, upscale_each=false, upscale_resolution=1080, upscale_noise_scale=0.0, upscale_fps=24, upscale_seed=42, text_overlays=[{text,start,end,position,size,color}]}` — `shots` items may be paths OR objects `{path, in?, out?, duration?}` (still images need `duration`); `sfx` may be a single path OR a list `[{path, at}]` (timestamped SFX); `vo_start` delays the VO (silence before it) | `{"video":"<path>/final.mp4"}` (+ `final_titled.mp4` when `text_overlays` given) |
| POST | `/trim` | JSON `{source, start=0.0, end \| duration, fps?, width?, height?}` — exactly one of `end` (absolute seconds) / `duration` (length) | `{"video":"<path>/mp_<jid>_00001.mp4"}` |
| POST | `/freeze` | JSON `{source, duration=2.0, frame?=0, fps=24, width=1280, height=720}` — still image, or a video + `frame` (frame index) | `{"video":"<path>/mp_<jid>_00001.mp4"}` |
| POST | `/caption` | JSON `{source, text, start?, end?, position="bottom", font_size?, color="white", outline?=3}` (drawtext; multiline via `textfile`) | `{"video":"<path>/mp_<jid>_00001.mp4","caption":"<path>/caption.txt"}` |
| GET | `/info?path=` | sync (not a job) — ffprobe metadata for any media file | `{duration_s, width, height, fps, video_codec, audio_codecs, size_bytes, bitrate_bps}` (404 missing, 400 unprobeable) |
| POST | `/upload_local` | JSON `{source, subdirectory?}` — sync bridge of a **basedir/** host file into `media_jobs/uploads/` (source confined to the ComfyUI basedir; 400 otherwise) | `{"path":"<media_jobs>/uploads/<ts>_<name>"}` |
| POST | `/download` | JSON `{url, subdirectory?}` — sync ingest of an http(s) URL into `media_jobs/uploads/` | `{"path":"<media_jobs>/uploads/<ts>_<name>"}` |
| POST | `/upload` | multipart `file` (+ optional `subdirectory`) — sync client file upload into `media_jobs/uploads/` (cap `MEDIA_UPLOAD_MAX_MB`, default 500 → 413) | `{"path":"<media_jobs>/uploads/<ts>_<name>"}` |
| POST | `/dl_token` | JSON `{path, ttl_hours=24}` — sync mint of an HMAC-SHA256 signed pull token (max ttl 168h → 400; path confined to media_jobs → 404; secret unset → 503) | `{"token","url_path","expires_at"}` |
| GET | `/dl/{token}` | sync signed download (Range-capable; token is path-bound + time-limited; bad/expired → 404, nothing logged) | file bytes |

Notes:
- `shots`, `upscale`, `sfx`, `images/edit` accept **multipart file uploads**
  (the server copies them into the job dir). `assemble`/`storyboard`/`images`/
  `tts`/`music` accept **JSON** with host paths (or paths relative to the run
  dir). All 12 job routes accept optional `user` + `client` (JSON field or Form
  field).
- `pipeline` for `/upscale`: `b` = SeedVR2 3B (quality, ~5 min), `a2` =
  4xUltrasharp (fast, ~1 min).
- `voice` for `/tts`: `trailer` (bundled movie-trailer reference, zero-shot
  clone) or a path to a custom reference wav.
- `strength` for `/shots`: how strongly the keyframe anchors the clip
  (default **0.7** — empirical warble knee; lower = less warble, less motion).
  Prompt visual **style**, not fast motion.
- `upscale_each` for `/assemble`: B-upscale (SeedVR2) each shot before concat —
  the recommended path for 1080p-quality commercials (never stretch raw
  768×512 shots).
- Output paths are **host paths** on the GPU host. The remote MCP server
  fetches them via `GET /files/{name}` (name = path relative to the run dir) or
  directly if it has filesystem access. **Off-LAN alternative (2026-09-07):**
  `POST /dl_token` mints a signed `GET /dl/{token}` URL (HMAC-SHA256,
  path-bound, time-limited, default 24h) that needs no auth on matrix and no
  filesystem access — the token IS the credential.
- **Sync endpoints** (`/info`, `/upload_local`, `/download`, `/upload`,
  `/dl_token`, `/dl/{token}`) return directly — no `job_id`, no queue. Job
  flows (`/trim`, `/freeze`, `/caption` and the original nine) are async.
- `/trim`/`/freeze`/`/caption` are CPU-only (ffmpeg via `docker exec -u comfy
  comfyui_backend`); they share the same bounded FIFO queue as GPU jobs
  (no fast lane — VRAM-constrained design, see `media_pipeline_gaps.md`).

## 3. Queue & back-pressure

- Bounded FIFO queue + fixed worker pool. At most `MAX_CONCURRENT_JOBS`
  (default **1**, set in `/home/chuck/homelab/.env`) jobs run at once; the rest
  wait with `status=queued` (visible position via `/health` + `queue_position`).
- Waiting depth capped by `MAX_QUEUE_DEPTH` (default **5**); total in-flight =
  `MAX_CONCURRENT_JOBS + MAX_QUEUE_DEPTH` (default 6). When full, new jobs are
  rejected with **HTTP 503** + a `retry_after_seconds` back-off hint (linear
  estimate `~90 s/job`, floored 120 s, capped 900 s). No job record is created.
- FIFO, no priority (deferred).

## 4. Models (per flow)

| Flow | Model |
|---|---|
| `storyboard` | Qwen3.8-27B via vLLM :8000 (strict JSON shot list) |
| `images` | Qwen-Image-2512 (GGUF Q4) + Lightning 4-step LoRA |
| `images/edit` | Qwen-Image-Edit-2511 (Q4) + Lightning 8-step LoRA (Kontext resolution, e.g. 16:9 → 1392×752) |
| `shots` | LTXV 2B 0.9.6 distilled (8-step sigma schedule) + T5-XXL fp8 |
| `tts` | XTTS-v2 (separate venv, releases VRAM on exit) |
| `music` | ACE-Step 1.5 (short-lived server per job) |
| `sfx` | MMAudio large-44k-v2 (in ComfyUI) |
| `upscale` | SeedVR2 3B FP8 (`b`) / 4xUltrasharp (`a2`) |
| `assemble` | ffmpeg (in-container, CPU) |
| `trim` / `freeze` / `caption` | ffmpeg (in-container, CPU; 0 work units, model label `ffmpeg`) |

## 5. Metering (implemented + calibrated 2026-09-06)

The pipeline attributes each job to its `user`/`client` and measures the work:

- **`GET /metrics`** — Prometheus text format, scraped by Thor's Prometheus
  (15 s). 404 when `MEDIA_METRICS_ENABLED=false` (kill switch = zero behavior
  change). Metrics:

  | Metric | Type | Labels |
  |---|---|---|
  | `media_jobs_total` | counter | `user, client, stage, status` |
  | `media_job_duration_seconds` | histogram | `user, stage` |
  | `media_tokens_total` | counter | `user, stage, kind` (storyboard only; real vLLM tokens) |
  | `media_cost_usd_total` | counter | `user, stage` |
  | `media_work_units_total` | counter | `user, stage, kind` |
  | `media_queue_depth` / `media_jobs_active` / `media_up` | gauge | — |

- **Work units** (deterministic, not energy-based — DCGM is broken on this
  driver/GPU): `images`/`images_edit` = steps × output MP (`mpix_steps`);
  `shots`/`upscale`/`assemble`-with-`upscale_each` = frames × output MP
  (`mpix_frames`); `tts`/`music`/`sfx` = output audio seconds via ffprobe
  (`audio_seconds`); `storyboard` = real vLLM prompt/completion tokens;
  `assemble` (no upscale) = 0; `trim`/`freeze`/`caption` = 0 (CPU-only
  ffmpeg, model label `ffmpeg`).
- **Rates** (full-cost: electricity + GPU amortization; calibrated 2026-09-06
  against 1 Hz `nvidia-smi power.draw`, baseline-subtracted):
  `$0.000053`/mpix_step, `$0.0000058`/mpix_frame, `$0.000031`/audio_s;
  storyboard priced at the live `matrix-coder` LiteLLM rate
  ($0.75/M in / $4.50/M out). Env vars: `MEDIA_PRICE_*`,
  `MEDIA_MATRIX_CODER_*_USD` in `/home/chuck/homelab/.env`.
- **`jobs.jsonl`** — durable per-job record at
  `/home/chuck/data/comfyui/run/media_jobs/metrics/jobs.jsonl` (10 MB cap,
  keep-newest rotation). One line per completed job:
  `ts, job_id, flow, status, user, client, model, work_units, work_unit_kind,
  cost_usd, tokens{prompt,completion}, duration_s, queue_wait_s, params, error`.

## 6. VRAM & disk budget (flows run sequentially, never simultaneously)

Free VRAM for media work: ~14 GB (72 GB − vLLM's ~56–66 GB). Per-flow peaks
(measured where noted):

| Flow | Peak VRAM | Notes |
|---|---|---|
| Qwen-Image-2512 gen/edit | ~12–14 GB | proven in production (GGUF Q4 + offload) |
| LTXV 0.9.6 2B shot gen | ~10–12 GB | T5 fp8 encoder; distilled 8-step |
| ACE-Step music | ~6–8 GB | separate short-lived server |
| TTS VO (XTTS-v2) | ~4–6 GB | separate venv, releases on exit |
| MMAudio SFX | ~2–4 GB | in ComfyUI |
| SeedVR2 upscale (b) | ~10.4 GB | measured |
| A2 upscale (4xUltrasharp) | ~8.4 GB | measured |
| Assembly | 0 | ffmpeg (in-container), CPU |
| Storyboard | 0 (ComfyUI) | vLLM's own share, separate process |

**No flow exceeds the 14 GB limit.** Rule: the pipeline serializes GPU work
(one flow at a time); ComfyUI unloads models between jobs; separate-process
workers release VRAM on exit. Per-flow OOM fallbacks: offload text encoder to
CPU → lower res/frames → (last resort, ask first) lower vLLM
`gpu-memory-utilization`.

## 7. End-to-end commercial recipe (what the LLM should call)

1. `media_storyboard(brief, n_shots=6)` → shot list
2. (consistency) `media_generate_image` character sheets → `media_edit_image`
   each keyframe from the sheet
3. per shot: `media_generate_shot(keyframe, style_prompt)` (768×512, ~97 f) —
   **style prompt, not fast motion**
4. `media_text_to_speech(vo_script, voice="trailer")` → VO
5. `media_generate_music(prompt, duration=total)` → music
6. (optional) `media_sfx(video, description)` per shot
7. `media_assemble(shots=[raw shots], vo, music, sfx, width=1920, height=1080,
   upscale_each=true)` — B-upscale each shot inside assemble (SeedVR2)
8. `text_overlays=[{text, start, end, position, size, color}]` — titles
   composited in post (readable; video diffusion can't render text)

Post tools (2026-09-07): `media_trim` (cut a clip to a time range),
`media_freeze` (still image or video frame → static N-second clip — the
"hero product still" shot), `media_caption` (burn text into a clip),
`media_info` (probe metadata). Assemble now also takes object shots
(`{path, in, out, duration}`), a timestamped SFX list (`[{path, at}]`),
`vo_start`, and `loudnorm`.

Result: sharp 1080p, multi-shot, 24–40 s, readable text, minimal warble.

## 8. Deployment (GPU host)

Docker container (`media_pipeline`, image `media-pipeline:latest`) in
`compose/comfyui.yml` under the **`image` profile** — starts/stops as a unit
with ComfyUI via `model-manager`. Host networking (reaches ComfyUI/vLLM on the
host loopback; binds `0.0.0.0:8189`), `/var/run/docker.sock` mounted (for
`docker exec`/`cp` into `comfyui_backend`), comfy `run`/`basedir` +
`/home/chuck/homelab/.env` (ro) volumes, `restart: unless-stopped`.

```bash
model-manager images                   # up   (ComfyUI + media-pipeline)
model-manager qwen-coder               # down (stops them)
model-manager rebuild media-pipeline   # rebuild image from media-pipeline/ + recreate
curl -s http://127.0.0.1:8189/health   # {"ok":true,...}
```

Build context: `homelab/media-pipeline/` (`server.py`, `workflows.py`,
`comfy_client.py`, `metering.py`, `workers/`, `Dockerfile`, `requirements.txt`).
The container runs as root and shells out to `docker` (workers + ffmpeg); job
dirs are created in the 1024-owned run dir via `docker exec -u comfy`.

## 9. Changelog

| Date | Change |
|---|---|
| 2026-09-07 | **Part-1 gap fill** (plan `media_pipeline_gaps.md`): `/trim`, `/freeze`, `/caption` job flows (ffmpeg, CPU); `/assemble` extensions — object shots `{path,in,out,duration}`, timestamped SFX list `[{path,at}]`, `vo_start`, `loudnorm` (backward compatible); sync endpoints `/info`, `/upload_local` (basedir-confined), `/download`, `/upload` (multipart, 500 MB cap), `/dl_token` + `GET /dl/{token}` (HMAC-signed, path-bound, time-limited pull URLs); ffmpeg flows metered at 0 work units (model `ffmpeg`). QA: `media-pipeline/qa_part1.py` 38/38. |
| 2026-09-06 | **Work-unit metering** (spec `matrix_media_work.md` v2.1): `/metrics` endpoint, `user`/`client` on all 9 job routes, `timeout` status, `jobs.jsonl`, calibrated rates. |
| 2026-08-28 | Bounded queue depth (`MAX_QUEUE_DEPTH=5`) + HTTP 503 back-pressure; `strength` default 1.0 → 0.7 (warble knee). |
| 2026-08-27 | Containerized (model-manager managed); `upscale_each` + `text_overlays` on `/assemble`; all 10 flows verified end-to-end. |