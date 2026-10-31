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
> `media-mcp-client/` (pointer to the `mcp_media` MCP server — `mcp/servers/media` in the homelab-ai-harness repo).

The **media-pipeline** service (container `media_pipeline`, image
`media-pipeline:latest`) is a thin FastAPI orchestrator on the GPU host with no
GPU of its own. It owns the GPU job queue, drives ComfyUI (:8188) + vLLM
(:8000), spawns TTS/ACE-Step workers, and does ffmpeg assembly.

Base URL: `http://<gpu-host>:8189` (LAN-only, no auth — never expose publicly.
Public auth is the Caddy layer on thor only; the MCP layer authenticates per-user
(key = user, key passed to the pipeline), and finished files are exposed publicly
via `siri.choukalos.com` / `choukalos.com/files`).

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
  the caller's identity (see `media-mcp-client/` — `mcp/servers/media` in the homelab-ai-harness repo).
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
| POST | `/images` | JSON `{prompt, width=1280, height=720, seed=42, steps=25, model?="qwen21"\|"legacy", lora? (legacy only)}` | `{"image":"<path>.png","model":"<model>"}` |
| POST | `/images/edit` | multipart `file, prompt, seed=42, steps=25, model?="qwen21"\|"legacy", references? (qwen21 only)` | `{"image":"<path>.png","model":"<model>","references":N}` |
| POST | `/shots` | multipart `file(keyframe), prompt, model?="ltxv2b"\|"wan14b", width?, height?, frames?, fps?, steps=8, strength=0.7, seed=42` — omitted dims/frames/fps fall back to **per-model native defaults** (ltxv2b: 768×512/97f/25 fps; wan14b: 832×480/81f/16 fps); wan14b is **480p only** — any other dims → clean 400 | `{"video":"<path>.mp4","frames":N,"fps":F}` |
| POST | `/tts` | JSON `{text, voice="trailer"}` — `voice` = library name (see `/voices`) **or** a reference wav path (host absolute under run/basedir, or run-dir-relative) | `{"audio":"<path>/vo.wav"}` |
| GET | `/voices` | sync (not a job) — list the TTS voice library | `{"voices":[{name, description, gender, style, added, protected, ref_exists, sample}]}` |
| POST | `/voices` | JSON `{name, source, description?, gender?, style?, sample_text?}` — register a new voice from a reference wav (3–15 s; QC'd, normalized to 16 kHz mono, sample generated on GPU) | `job_id` → `{"voice","ref","sample","ref_duration_s"}` |
| DELETE | `/voices/{name}` | sync — remove a voice (ref + sample + manifest entry). `trailer`/`default` are protected → 400 | `{"deleted":"<name>"}` |
| POST | `/music` | JSON `{prompt, lyrics="", duration=30, seed=42}` | `{"audio":"<path>/music.wav"}` |
| POST | `/sfx` | multipart `file(video), duration=8, steps=25, cfg=4.5, seed=42, prompt="", negative_prompt="", fps=24` | `{"audio":"<path>.flac"}` |
| POST | `/upscale` | multipart `file(video), pipeline="b"\|"a2", resolution=1080, noise_scale=0.0, fps=24, seed=42` | `{"video":"<path>.mp4"}` |
| POST | `/assemble` | JSON `{shots, vo?, music?, sfx?, width=1920, height=1080, fps=24, vo_volume=1.0, music_volume=0.35, sfx_volume=0.9, vo_start=0.0, loudnorm=false, upscale_each=false, upscale_resolution=1080, upscale_noise_scale=0.0, upscale_fps=24, upscale_seed=42, text_overlays=[{text,start,end,position,size,color}]}` — `shots` items may be paths OR objects `{path, in?, out?, duration?}` (still images need `duration`); `sfx` may be a single path OR a list `[{path, at}]` (timestamped SFX); `vo_start` delays the VO (silence before it) | `{"video":"<path>/final.mp4"}` (+ `final_titled.mp4` when `text_overlays` given) |
| POST | `/trim` | JSON `{source, start=0.0, end \| duration, fps?, width?, height?}` — exactly one of `end` (absolute seconds) / `duration` (length) | `{"video":"<path>/mp_<jid>_00001.mp4"}` |
| POST | `/freeze` | JSON `{source, duration=2.0, frame?=0, fps=24, width=1280, height=720}` — still image, or a video + `frame` (frame index) | `{"video":"<path>/mp_<jid>_00001.mp4"}` |
| POST | `/caption` | JSON `{source, text, start?, end?, position="bottom", font_size?, color="white", outline?=3}` (drawtext; multiline via `textfile`) | `{"video":"<path>/mp_<jid>_00001.mp4","caption":"<path>/caption.txt"}` |
| POST | `/transcribe` | JSON `{path, instrument="guitar", tuning="standard", lyrics=true, stems=false, full_notes=false, whisper_model?}` — `path` = media file under `media_jobs` (audio **or** video; the audio track is extracted). v1: `instrument=guitar` + `tuning=standard` only (anything else → 400 at submit). Song → guitar tab (6-string, standard tuning) + timestamped lyrics; ~3–8 min for a 5-min song | `{"analysis":"<path>/analysis.json","tab":"<path>/tab.md","lyrics":"<path>/lyrics.txt","midi":"<path>/song.mid","audio":"<path>/audio.wav"}` |
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
- `model` for `/images` + `/images/edit` (2026-09-23): `qwen21` (default,
  Qwen-Image-2.1) or `legacy` (Qwen-Image-2512/2511 GGUF + Lightning). Server
  default follows `MEDIA_IMAGE_MODEL` in `.env` (unset → `qwen21`). qwen21
  `steps` is clamped to [10, 50] (default 25 — no distilled LoRA at launch);
  legacy keeps its 4/8 defaults. `lora` only applies to legacy.
- `references` for `/images/edit` (2026-09-23, qwen21 only): comma-separated
  list of up to 9 reference images for character/product consistency (10 image
  slots total incl. the edit target). Each entry is either a filename already
  in ComfyUI `input/` or a media_jobs-relative path (e.g.
  `media_jobs/<jid>/mp_<jid>_00001.png`) — the pipeline stages the latter into
  ComfyUI `input/` server-side. The canvas follows the uploaded edit target.
- `pipeline` for `/upscale`: `b` = SeedVR2 3B (quality, ~5 min), `a2` =
  4xUltrasharp (fast, ~1 min).
- `voice` for `/tts` (2026-09-25): a **library name** from `GET /voices`
  (e.g. `trailer`, `default`, `narrator_f`, `deep_m`) or a **reference wav
  path** (host absolute under the run/basedir dirs, or run-dir-relative). The
  worker is unchanged — `trailer`/`default` pass through as `--voice`,
  everything else resolves to `--reference-audio`. Unknown name → clean 400
  listing available voices (previously: a worker crash with an argparse
  traceback). Raw-path support now actually works (previously the server never
  forwarded `--reference-audio`).
- **Voice library** (2026-09-25): manifest at
  `<basedir>/models/tts/voices/manifest.json` (auto-seeded on first boot with
  the protected `trailer` + `default` entries). `POST /voices` QC's the
  reference (3–15 s), normalizes it to 16 kHz mono, installs
  `<name>_ref.wav`, and generates a `<name>_sample.wav` audition via the TTS
  worker on the GPU. Re-POSTing a name re-registers it (keeps `added`).
  `DELETE /voices/{name}` removes ref + sample + manifest entry (protected
  names → 400). The retention sweeper only touches `media_jobs/` — the voices
  dir is permanent. Current portfolio: `trailer` (deep male, ~123 Hz F0),
  `default` (stock male, ~193 Hz), `deep_m` (warm deep male, ~150 Hz),
  `narrator_f` (female, ~212 Hz — pitch-shifted clone of the stock sample).
  All XTTS-v2 zero-shot clones of the same stock speaker; add a real recorded
  reference (3–15 s clean speech) via `POST /voices` for a truly new speaker.
- **`/transcribe`** (2026-10-31, spec `song_transcribe_matrix_handoff.md`):
  song → deterministic guitar tab + timestamped lyrics. Stages (one worker
  process, `venv-transcribe`): ffmpeg ingest → basic-pitch (ONNX) note events
  → beat tracking (60–180 half/double-time guard; beat frames converted to
  seconds — `beat_track` returns hop-grid frames) → CQT chord-template
  matching (1-beat window; a chord is only *reliable* when it beats the best
  template of any other root by ≥1.5 pts — same-root quality variants like
  Dmaj7 vs D7 are near-ties by construction (7th/9th is CQT-ambiguous) — and
  a change commits when the reliable new root beats the incumbent's own
  template in the *same* window by ≥3 pts for 2 consecutive beats; the
  same-window comparison suppresses the random flapping CQT matching
  produces on sparse/dense mixes) → key (multi-signal: chord-root tonic when
  chords are plentiful — K-S correlation breaks root-count ties (I–V–vi–iii
  loops), else the strongest bass-root pitch class when the runner-up is a
  perfect 5th above (dominant-confirmed), else Krumhansl–Schmuckler; mode from
  tonic chord qualities, else the 3rd decision — major-3rd vs minor-3rd CQT
  energy) → deterministic section segmentation (quiet-runs, then chord-change
  boundaries, then fixed ~20 s division for continuous music) → [demucs
  htdemucs vocals] → faster-whisper (word timestamps, VAD) → deterministic
  string/fret mapping → matrix-coder polish (section labels, lyrics cleanup,
  confidence notes — may only *lower* confidence).
  **Design principle (binding):** the tab *data* is computed deterministically
  from the audio (reproducible, verifiable against `analysis.json`/
  `song.mid`); the LLM only labels/cleans/annotates, is diff-checked against
  the deterministic output, and a failed LLM step falls back to the
  deterministic rendering (`polish_fallback: true`) — the job is never
  blocked on the LLM. `lyrics=false` skips ASR (`lyrics.txt` carries a
  `# lyrics disabled` header); `stems=true` runs demucs first (cleaner lyrics
  on dense mixes, +1–2 min). Distinct job errors: no decodable audio track,
  `audio too short` (<10 s), whisper model missing. Job budget 30 min
  (`MEDIA_TRANSCRIBE_TIMEOUT_S`). VRAM headroom gate 4 GiB (demucs peak).
  Config envs: `MEDIA_TRANSCRIBE_WHISPER_MODEL` (default `large-v3`),
  `MEDIA_TRANSCRIBE_WHISPER_DEVICE` (`cuda`|`cpu`), `MEDIA_TRANSCRIBE_MAX_FRET`
  (15), `MEDIA_TRANSCRIBE_MIN_SECTION_S` (4), `MEDIA_TRANSCRIBE_DEMUCS_MODEL`
  (`htdemucs`).
- `model` for `/shots` (2026-10-31): `ltxv2b` (LTXV 2B, fast, ~15 s/clip, 768×512) or
  `wan14b` (Wan 2.2 I2V A14B, quality, ~10–12 min/clip, **832×480 only** —
  720p OOM'd the 14 GB media VRAM gate and is rejected with a clean 400).
  Server default from `MEDIA_SHOT_MODEL` env (currently `wan14b` — quality
  by default, user decision 2026-10-25); per-request `model` always wins.
  `strength` is **ltxv2b-only** (wan14b ignores it).
- `strength` for `/shots` (ltxv2b): how strongly the keyframe anchors the clip
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

### 3.1 Job-dir retention

`media_jobs/` grows without bound (one dir per job: inputs, outputs,
intermediates, plus `uploads/`). A background sweeper thread runs every hour
and reclaims stale entries:

- **Job dirs** whose mtime is older than `MEDIA_JOB_RETENTION_DAYS` days
  (default **14**) are deleted (`shutil.rmtree`). Age = dir mtime ≈ last
  output write. Live jobs (`status` queued/running in the in-memory table) are
  never swept.
- **`uploads/`** top-level files older than the same window are deleted.
- **`metrics/`** (the `jobs.jsonl` billing/audit trail) is **never touched**.

Config (env, read from `/home/chuck/homelab/.env`):

| Var | Default | Meaning |
|---|---|---|
| `MEDIA_JOB_RETENTION_DAYS` | `14` | age window (days) before job dirs/uploads are reclaimed |
| `MEDIA_RETENTION_SWEEP_INTERVAL_S` | `3600` | sweep period (seconds) |

`/health` exposes `retention_days`, `retention_last_run`,
`retention_last_deleted`, `retention_last_bytes_freed`.

**Implication:** finished deliverables older than the window are NOT durable —
pull them out (e.g. via `media_pull` / `/dl/{token}`) or move them to
`data/media/projects/` while the job is fresh.

## 4. Models (per flow)

| Flow | Model |
|---|---|
| `storyboard` | Qwen3.8-27B via vLLM :8000 (strict JSON shot list) |
| `images` | **Qwen-Image-2.1** (7B unified DiT, int8_convrot) + Qwen3-VL 8B int8 encoder + 64-ch RGBA VAE — 25 steps, cfg=1.0 (default since 2026-09-23; `model=legacy` → Qwen-Image-2512 Q4 GGUF + 4-step Lightning) |
| `images/edit` | **same Qwen-Image-2.1 model** (unified T2I + edit; canvas follows the edited image; up to 10 images incl. references; `model=legacy` → Qwen-Image-Edit-2511 Q4 + 8-step Lightning) |
| `shots` | **LTXV 2B 0.9.6** distilled (8-step sigma schedule) + T5-XXL fp8 — `model=ltxv2b` (fast path, ~15 s/clip) · **Wan 2.2 I2V A14B** Q5_K_M GGUF (dual HighNoise/LowNoise models, 40 steps) + UMT5-XXL fp8 + Wan 2.1 VAE — `model=wan14b` (quality, 480p only, ~10–12 min/clip; default since 2026-10-31 via `MEDIA_SHOT_MODEL`) |
| `tts` | XTTS-v2 (separate venv, releases VRAM on exit) |
| `music` | ACE-Step 1.5 (short-lived server per job) |
| `transcribe` | basic-pitch (ONNX, ~1 GB CPU) + faster-whisper `large-v3` (~3 GB, `venv-transcribe`) + matrix-coder polish (existing vLLM) — deterministic tab data; the LLM only labels/cleans/annotates (validated, deterministic fallback) |
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
  driver/GPU): `images`/`images_edit` = **effective steps** × output MP
  (`mpix_steps`; qwen21 steps are the clamped [10,50] value, legacy the
  requested 4/8);
  `shots`/`upscale`/`assemble`-with-`upscale_each` = frames × output MP
  (`mpix_frames`); `tts`/`music`/`sfx` = output audio seconds via ffprobe
  (`audio_seconds`); `transcribe` = input audio seconds via ffprobe
  (`transcribe` kind, placeholder rate $3.1e-5/s — spec §8); `storyboard` = real vLLM prompt/completion tokens;
  `assemble` (no upscale) = 0; `trim`/`freeze`/`caption` = 0 (CPU-only
  ffmpeg, model label `ffmpeg`).
- **Rates** (full-cost: electricity + GPU amortization; calibrated 2026-09-06
  against 1 Hz `nvidia-smi power.draw`, baseline-subtracted):
  `$0.000053`/mpix_step, `$0.0000058`/mpix_frame, `$0.000031`/audio_s;
  `transcribe` = `$0.000031`/s placeholder (spec §8 — calibrate after the
  first real runs, energy-based, report back to Thor);
  storyboard priced at the live `matrix-coder` LiteLLM rate
  ($0.75/M in / $4.50/M out). Env vars: `MEDIA_PRICE_*`,
  `MEDIA_MATRIX_CODER_*_USD` in `/home/chuck/homelab/.env`.
  The `transcribe` LLM polish call is **not** metered in media $ (counted in
  LLM $ on the Thor side — no double counting, permanent rule).
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
| **Wan 2.2 I2V A14B shot gen** | **~14 GB** (measured 2026-10-31: +13.9 GB delta, 14.4 GB peak, 480p/81f) | 720p OOMs the gate (rejected, 400); ~10 GiB stays resident after the job |
| ACE-Step music | ~6–8 GB | separate short-lived server |
| TTS VO (XTTS-v2) | ~4–6 GB | separate venv, releases on exit |
| Transcribe (demucs + whisper) | ~4–5 GB | separate venv (`venv-transcribe`); peak = demucs htdemucs; basic-pitch ONNX runs on CPU |
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

**VRAM headroom guard (2026-10-31):** ComfyUI keeps its last-loaded model
resident (a wan14b shot leaves ~10 GiB), which starves the out-of-ComfyUI GPU
workers (ACE-Step music ~6.5 GiB, TTS): vLLM ~56 GiB + Wan 10 GiB + worker
> 71.12 GiB → CUDA OOM (found in E2E 2026-10-31). `server.py` now measures
free VRAM (`docker exec comfyui_backend nvidia-smi`) before `music` (≥8 GiB
required) and `tts`/`voices-add` (≥2 GiB) and `transcribe` (≥4 GiB — demucs
peak), and, if headroom is low and the
ComfyUI queue is idle, **restarts `comfyui_backend`** (~40 s) to drop the
resident model, then waits for it to come back. No-op when headroom is
sufficient or unmeasurable. Sfx is a ComfyUI flow — ComfyUI's own
OOM-offload handles it; LTXV shots are small enough not to trigger this.

## 7. End-to-end commercial recipe (what the LLM should call)

1. `media_storyboard(brief, n_shots=6)` → shot list
2. (consistency) `media_generate_image` character sheets → `media_edit_image`
   each keyframe from the sheet
3. per shot: `media_generate_shot(keyframe, style_prompt)` (768×512, ~97 f) —
   **style prompt, not fast motion** — or `model="wan14b"` (832×480, 81 f,
   ~10–12 min/clip) for higher quality (the server default since 2026-10-31)
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
| 2026-10-31 | **`/transcribe` chord detection fix** (QA found 0 chord changes on the 40 s test track, 1 on the 204 s track — "chord data is empty for all sections"): three defects in `workers/transcribe_worker.py`. (1) `stage_bpm`: `librosa.beat.beat_track` returns beat **frames** (hop grid), not seconds — the old code treated them as seconds, so `stage_chords`' `b >= dur` break fired on beat 0 and the loop never ran (0 chords). Now converted via `frames_to_time`. (2) `stage_chords`: the new chord was compared against the incumbent's score from the window it was *committed in* (stale, non-comparable) — after a high-scoring first chord nothing could beat `incumbent + MARGIN`, so even with correct beats only 1 change committed. Now the incumbent's own template is re-scored in the **same** window (normalized scores are only comparable within a window; matches spec §5.3), 1-beat windows (the old 2-beat window straddled chord changes), and reliability = margin vs the best template of a **different root** (same-root quality variants like Dmaj7 vs D7 are near-ties by construction and were blocking every commit). (3) `stage_key`: K-S correlation tiebreak when chord-root counts tie (typical I–V–vi–iii loops). Verified on the QA inputs: 40 s track → 20 changes (Dmaj7–Amaj7–Bm7–Gmaj7 loop ×5, conf 0.86–0.94, key D major); 204 s track → 19 changes, all diatonic to C# major, key C# major. `transcribe_qa_check.py` PASS on both; deterministic fields byte-identical across runs and across the API path. |
| 2026-10-31 | **`/transcribe` — song → guitar tab + lyrics** (spec `song_transcribe_matrix_handoff.md`): new job flow + worker (`workers/transcribe_worker.py`, separate venv `venv-transcribe`: librosa, basic-pitch ONNX, faster-whisper large-v3, demucs, pretty_midi). Stages: ffmpeg ingest → basic-pitch note events (`song.mid` + `notes.json`) → beat tracking (60–180 half/double-time guard) → CQT chord-template matching (2-beat window; reliable = beats runner-up ≥1.5 pts; change commits ≥3 pts for 2 consecutive beats) → multi-signal key (chord-root tonic → dominant-confirmed bass-root → Krumhansl–Schmuckler; mode from chord qualities → 3rd decision) → deterministic section segmentation (quiet-runs → chord-changes → fixed ~20 s division) → [demucs htdemucs vocals] → faster-whisper (word timestamps, VAD, beam 5) → deterministic guitar mapping (role + string/fret, invariant-asserted) → matrix-coder polish (thinking OFF). **Binding design principle:** tab data is deterministic from the audio; the LLM only labels sections / cleans ASR lyrics / writes confidence notes (may only lower confidence) — its output is diff-checked (section count/order, lyric word-subset, ±1 s timestamps) with a deterministic fallback (`polish_fallback: true`); the job is never blocked on the LLM. Outputs: `analysis.json` (v1 schema), `tab.md` (deterministic 16th-note grid), `lyrics.txt`, `song.mid`, `audio.wav`. Whisper-hallucination guard (sections with >10 s without onsets lose their lines). Config: `MEDIA_TRANSCRIBE_WHISPER_MODEL/DEVICE`, `MEDIA_TRANSCRIBE_MAX_FRET`, `MEDIA_TRANSCRIBE_MIN_SECTION_S`, `MEDIA_TRANSCRIBE_DEMUCS_MODEL`, `MEDIA_TRANSCRIBE_VLLM_URL/MODEL`, `MEDIA_TRANSCRIBE_TIMEOUT_S` (1800). Metering: new `transcribe` work-unit kind (input audio seconds, placeholder rate $3.1e-5/s); LLM polish not metered in media $ (LLM $ on Thor side — no double counting). VRAM headroom gate 4 GiB. v1: `instrument=guitar` + `tuning=standard` only (else clean 400 at submit). QA: `media-pipeline/transcribe_qa_check.py` (schema checker, spec §9.3) — all 9 spec §9 items verified (happy path, fallback, `lyrics=false`, error cases, metering). |
| 2026-10-31 | **Wan 2.2 I2V A14B shots** (plan `wan_shots_todo.md`, phases 1–3): `/shots` gains `model` (`ltxv2b`\|`wan14b`; per-request > `MEDIA_SHOT_MODEL` env > built-in default — env set to `wan14b`, quality by default); per-model native defaults (ltxv2b 768×512/97f/25 fps, wan14b 832×480/81f/16 fps; wan frames must be 4n+1); wan14b 480p-only gate (720p OOM'd the 14 GB gate → clean 400); `strength` ltxv2b-only; metering label `wan2.2-14b-i2v-gguf-q5`. E2E: storyboard → 2× qwen21 keyframes → 2× wan14b shots (625 s + 530 s) → TTS → music → assemble (SeedVR2 upscale_each + loudnorm) → 1920×1080/24 fps final. **VRAM headroom guard**: pre-flight free-VRAM check before `music` (≥8 GiB) / `tts`+`voices-add` (≥2 GiB); low headroom + idle ComfyUI queue → auto `docker restart comfyui_backend` (~40 s) to drop the resident model (fixes the post-wan-shot music/sfx OOM). Verified: no-op path (15.7 GiB free, no restart) and restart path (2.8 GiB free → restart → music OK → qwen21 image OK). |
| 2026-09-25 | **TTS voice library** (plan `voices_todo.md`): `GET/POST/DELETE /voices` + manifest at `<basedir>/models/tts/voices/manifest.json` (auto-seeded; `trailer`/`default` protected); `/tts` `voice` now accepts a library name **or** a reference wav path (raw-path support fixed — the server previously never forwarded `--reference-audio`); unknown voice → clean 400 instead of a worker crash; `POST /voices` QC (3–15 s) + 16 kHz mono normalization + GPU sample generation; seeded `narrator_f` (female, ~212 Hz F0) + `deep_m` (~150 Hz) as pitch-shifted clones of the stock sample. Worker CLI unchanged; all logic in `server.py`. | **Qwen-Image-2.1 upgrade**: `/images` + `/images/edit` default to Qwen-Image-2.1 (ComfyUI v0.37.0 native nodes, int8_convrot weights, 25 steps, cfg=1.0); unified model for create + edit; `/images/edit` gains `references` (up to 9, 10 total) for cross-shot consistency; `model` field (`qwen21`\|`legacy`) + `MEDIA_IMAGE_MODEL` env on both image flows; legacy 2512/2511 GGUF+Lightning kept as fallback; qwen21 steps clamped [10,50]; metering model label + work units follow the selected model/effective steps. Runbook: `media_todo.md`. |
| 2026-09-21 | **Job-dir retention** (`MEDIA_JOB_RETENTION_DAYS`, default 14 d): hourly background sweeper reclaims job dirs + stale `uploads/` files; `metrics/` (jobs.jsonl) and live jobs never touched; `/health` exposes sweep state. One-off: removed `acestep_test/`/`ltxv_test/`/`tts_test/` scratch dirs. |
| 2026-09-07 | **Part-1 gap fill** (plan `media_pipeline_gaps.md`): `/trim`, `/freeze`, `/caption` job flows (ffmpeg, CPU); `/assemble` extensions — object shots `{path,in,out,duration}`, timestamped SFX list `[{path,at}]`, `vo_start`, `loudnorm` (backward compatible); sync endpoints `/info`, `/upload_local` (basedir-confined), `/download`, `/upload` (multipart, 500 MB cap), `/dl_token` + `GET /dl/{token}` (HMAC-signed, path-bound, time-limited pull URLs); ffmpeg flows metered at 0 work units (model `ffmpeg`). QA: `media-pipeline/qa_part1.py` 38/38. |
| 2026-09-06 | **Work-unit metering** (spec `matrix_media_work.md` v2.1): `/metrics` endpoint, `user`/`client` on all 9 job routes, `timeout` status, `jobs.jsonl`, calibrated rates. |
| 2026-08-28 | Bounded queue depth (`MAX_QUEUE_DEPTH=5`) + HTTP 503 back-pressure; `strength` default 1.0 → 0.7 (warble knee). |
| 2026-08-27 | Containerized (model-manager managed); `upscale_each` + `text_overlays` on `/assemble`; all 10 flows verified end-to-end. |