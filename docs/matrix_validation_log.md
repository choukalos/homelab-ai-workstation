# Matrix Validation Log

> Run: 2026-07-03T02:02 UTC

## Read-Only Checks

| Check | Result | Notes |
|---|---|---|
| `hostname -I` | ✅ 192.168.4.55 | LAN address reachable |
| `nvidia-smi` | ✅ RTX PRO 5000 72GB | Driver 595.71.05, CUDA 13.2 |
| `docker ps` | ✅ 4 running containers | qwen36, ollama, node-exporter, dcgm-exporter |
| `docker compose ls` | ✅ `homelab` running(4) | compose.qwen36.yml, compose.ollama.yml, compose.metrics.yml |
| `docker network ls` | ✅ `homelab_default` bridge | Network exists for inter-container comms |
| `docker volume ls` | ✅ None | All state is bind-mounted (good for auditability) |
| `df -h` | ✅ 14% used on root | 1.7 TB NVMe, plenty of space |
| `free -h` | ✅ 54 GiB available | 62 GiB total, plenty of RAM |

## Service Health

| Service | Endpoint | Status | Notes |
|---|---|---|---|
| vLLM Qwen3.6 | http://localhost:8000/v1/models | ✅ Running | Serves `qwen36-27b` |
| Ollama | http://localhost:11434/v1/models | ✅ Running | 3 models loaded: nomic-embed-text, qwen3.6:27b, gemma4:26b |
| node-exporter | http://localhost:9100/metrics | ✅ Running | Prometheus node metrics (scraped by Thor) |
| dcgm-exporter | http://localhost:9400/metrics | ✅ Running | GPU metrics (scraped by Thor) |

**Note:** Grafana and Prometheus run on Thor, not Matrix. Matrix exporters are scraped remotely.

## Discrepancies

| # | Finding | Severity | Status |
|---|---|---|---|
| 1 | ~~`switch.sh` references non-existent compose files~~ | ~~**High**~~ | **RESOLVED** — switch.sh deleted in Phase 1 |
| 2 | `.env` `QWEN_GPU_MEM=0.56` but compose hardcodes `0.66` | Low — compose wins | Documented |
| 3 | ~~Ollama compose `KEEP_ALIVE=-1m` vs container `5m`~~ | ~~Medium~~ | **RESOLVED** — aligned to `5m` |
| 4 | Two stopped vLLM containers consuming disk | Low | Documented |
| 5 | ~~Grafana/Prometheus not running on Matrix~~ | ~~Medium~~ | **RESOLVED** — Grafana/Prometheus on Thor |
| 6 | ~~switch.sh mode names don't match plan~~ | ~~Medium~~ | **RESOLVED** — switch.sh deleted; model manager uses plan names |

## Next Validation Run

Re-run after any production changes in Phase 15.

---

# Run: 2026-09-06 — media-pipeline work-unit metering (spec v2.1, since deleted; contract now in `docs/matrix_media_pipeline_api.md`)

Change: new `media-pipeline/metering.py` + `server.py` integration — `/metrics` (Prometheus),
`user`/`client` on all 9 job routes, `timeout` status, `jobs.jsonl` durable log, calibrated
work-unit rates. Image rebuilt via `model-manager rebuild media-pipeline`.

## Verification (all passed)

| Check | Result | Notes |
|---|---|---|
| `/health` | ✅ ok | queue fields present |
| `/metrics` | ✅ Prometheus text | gauges `media_queue_depth`, `media_jobs_active`, `media_up=1`; counters after first job |
| storyboard (user=chuck, client=pi-test) | ✅ | `media_tokens_total` 208 prompt / 761 completion, cost $0.0035805, JSONL `model=qwen38-27b` |
| images 4×1280×720 | ✅ | 3.6864 `mpix_steps` |
| images/edit (multipart Form) | ✅ | output measured 1392×752 → 8.3743 `mpix_steps` (edit model outputs its own resolution — output-based metering is correct by design) |
| tts | ✅ | 4.6803 `audio_seconds` (ffprobe of vo.wav via `docker exec comfyui_backend`) |
| error path (upscale `pipeline=bogus`, Form) | ✅ | `status=error`, JSONL line, `cost_usd=0.0`, `work_units=null` |
| kill switch `MEDIA_METRICS_ENABLED=false` | ✅ | `/metrics` → 404, no JSONL line, job completes normally; restored to `true` |
| JSONL | ✅ | one line per job, all fields, `params` excludes user/client; 10 MB keep-newest rotation verified (offline logic test) |

## Calibration (1 Hz `nvidia-smi power.draw` via `docker exec comfyui_backend`, baseline-subtracted; idle baseline ≈ 76–96 W with vLLM resident)

| unit | reference job | dur | peak | energy above base | measured rate | rate set (`.env`) |
|---|---|---|---|---|---|---|
| `mpix_steps` | images 4×1280×720 (3.6864 u) | 6 s | 300 W (cap) | 952 J | $0.00005285/u | **0.000053** |
| `mpix_frames` | shots 97f 768×512 (38.142 u) | 8 s | 249 W | 395 J | $0.00000580/u | **0.0000058** |
| `audio_seconds` | music 30 s (30 u) | 36 s | 157 W | 409 J | $0.00003069/s | **0.000031** |

Notes:
1. Amortization dominates (≈$0.0913/h GPU = $4000/43800h); electricity negligible at these job sizes.
2. First images attempt was contaminated — a concurrent job from another session (shared GPU) lifted the
   "idle" baseline to 282 W. Re-ran after a clean-idle gate (10 consecutive <120 W samples → 79.6 W).
3. LTXV-2B-distilled is very fast on this card (97 frames 768×512 in 8 s), so the shots rate is low.
4. Post-calibration sanity (verified live): image ≈ $0.0002 (measured $0.000195 = 3.6864 × 0.000053),
   shot ≈ $0.0002, 30 s music ≈ $0.0009, storyboard (208+761 tok) ≈ $0.0036 — LLM cost dominates
   media cost, as intended.
5. `upscale`/`sfx` reuse the `mpix_frames`/`audio_seconds` rates (no separate reference jobs).
6. Storyboard priced at the live `matrix-coder` LiteLLM rate ($0.75/M in / $4.50/M out,
   confirmed on Thor 2026-09-05) — `MEDIA_MATRIX_CODER_IN_USD=0.00000075` /
   `MEDIA_MATRIX_CODER_OUT_USD=0.0000045`.
7. Calibration jobs are recorded in `jobs.jsonl` at the pre-calibration placeholder rates (historical
   record; no backfill) — rate change only affects new jobs.

## Discrepancies

| # | Finding | Severity | Status |
|---|---|---|---|
| 1 | DCGM field polling frozen on driver 595.71.05 (production exporter + fresh sessions) | Ops | DCGM 3.x pin in pursuit (Thor-side); energy metering demoted to stretch goal |
| 2 | `nvidia-smi energy.consumed` not a valid field on this GPU/driver | — | Documented; power sampling via `docker exec comfyui_backend nvidia-smi` |
| 3 | 4 of 9 job endpoints are multipart (spec v2.1 table originally said 3 — `/upscale` corrected) | Low | Fixed in `docs/matrix_media_pipeline_api.md` |
| 4 | `thor.litellm.config.yml` repo copy was stale ($1/M vs live $0.75/M) | Low | **RESOLVED** — file removed from repo 2026-09-06 (authoritative copy lives on Thor) |

# Run: 2026-09-07 — media-pipeline Part 1 build (M1–M9 of `media_pipeline_gaps.md`)

Change: 3 new job flows (`/trim`, `/freeze`, `/caption` — CPU ffmpeg via the existing
`run_ffmpeg` helper, metered as model `ffmpeg` at 0 GPU work units), `/assemble`
extensions (object shots `{path,in,out,duration}`, timestamped SFX list `[{path,at}]`,
`vo_start`, `loudnorm`), 6 new sync endpoints (`/info`, `/upload_local` — basedir-confined,
`/download`, `/upload` — 500 MB cap, `/dl_token` — HMAC-SHA256 path-bound time-limited,
`/dl/{token}` — Range-capable). Image rebuilt (`media-pipeline:latest`), container
recreated. QA: `media-pipeline/qa_part1.py` (fixtures in `media_jobs/qa_tests/`).

## Results — 38/38 checks passing

| Area | Checks | Result |
|---|---|---|
| M1 `/trim` | 4 | ✅ duration exact (2.000s), fps/width/height normalization, error cases (both end+duration, missing) |
| M2 `/freeze` | 4 | ✅ duration exact (1.5s), still→clip, video-frame→clip, PSNR>40dB between frames (static) |
| M3 `/caption` | 3 | ✅ burn-in present (OCR-free visual check via frame diff), multiline text, time window |
| M4 `/assemble` | 10 | ✅ old-style backward compat, object shots (in/out), still `duration`, still no-duration=0s, sfx list at 1.0s/5.5s (silencedetect-verified), `vo_start` (silence-verified), `loudnorm`, audio-never-truncates-video (apad) |
| M5 `/info` | 4 | ✅ video summary, audio-only file (no crash), 404 missing, 400 non-media |
| M6 `/upload_local` | 3 | ✅ basedir file → media_jobs, **/etc/passwd → 400 (exfil vector closed)**, missing source → 400 |
| M7 `/download` | 3 | ✅ URL ingest (local HTTP server), 400 on unreachable, subdirectory |
| M8 file transfer | 7 | ✅ multipart upload, 413 over cap, `/dl_token` mint, `/dl` 200 + Range 206, tampered token → 404, expired token → 404, outside media_jobs → 404, ttl cap 168h |
| Client | 3 | ✅ `info`, `trim`, `freeze`, `caption`, new-style `assemble`, `upload_file` → `dl_token` → `fetch_dl` round-trip (144,828 bytes), `download_url` |

## Bugs found & fixed during QA

| # | Finding | Severity | Status |
|---|---|---|---|
| 1 | `/assemble` `-shortest` could truncate video when audio was shorter (unexplained 11.458s cut observed) | **High** | **RESOLVED** — audio mix now ends with `apad` (pads to infinity) so `-shortest` always fires at video EOF; verified old-style 21.5s and new-style 9.5s finals |
| 2 | `MEDIA_DL_SECRET` silently empty — M8 constants read before the local `load_dotenv()` ran (config ordering, not a missing python-dotenv) | **High** | **RESOLVED** — config block moved after `load_dotenv()`; `/dl_token` verified live |
| 3 | `/upload_local` accepted arbitrary host paths (data-exfiltration vector via `/dl_token`/`/files`) | **High** | **RESOLVED** — source confined to the ComfyUI basedir (realpath check → 400); one `/etc/passwd` leaked during early QA was purged from `media_jobs/uploads/` |
| 4 | `GET /info` crashed (AttributeError) on audio-only files | Medium | **RESOLVED** — None-stream guard in `_probe_summary` |
| 5 | Client `media_pipeline_client.upload_file` built malformed multipart (CRLF header terminator mangled) | Medium | **RESOLVED** — CRLF fixed; round-trip verified |
| 6 | Client `assemble()` was missing `upscale_each`/`text_overlays` entirely | Medium | **RESOLVED** — added (plus `vo_start`, `loudnorm`, object shots, sfx list) |
| 7 | Freeze QA initially used exact pixel-hash equality — impossible for CRF-18 static clips (P-frame quantization drift; ~67dB PSNR between frames measured) | Low (QA methodology) | **RESOLVED** — check is now PSNR>40dB between frames |

## Notes

1. Static-clip QA: exact pixel identity is unachievable with libx264 CRF-18 (P-frame
   quantization drift on a still image); PSNR>40dB is the correct bar.
2. `server.py` reads its own `.env` via a local `load_dotenv()` (the image has no
   python-dotenv dependency) — new env-driven constants must be defined after the
   `load_dotenv()` call.
3. Multipart uploads (client or curl) must use CRLF line endings; the boundary in the
   `Content-Type` header must match the body exactly.
4. `media_jobs/PIPELINE_CHANGES.md` is the change log; canonical contract:
   `docs/matrix_media_pipeline_api.md` (12 job flows + 6 sync endpoints).
5. Part 2 (thor) not started — self-contained THOR HANDOFF section in `media_pipeline_gaps.md`.

# Run: 2026-09-25 — TTS voice library + media MCP client (Part 2) verification

Change: voice-library endpoints (`/voices` GET/POST/DELETE) + Qwen-Image-2.1 defaults
shipped on the matrix pipeline (2026-09-25, see `docs/matrix_media_pipeline_api.md`
changelog); the **thor MCP client** (`media-mcp-client/`) was redeployed with the three
new voice tools (`media_list_voices` / `media_add_voice` / `media_delete_voice`) and the
updated image/TTS contracts (steps default 4/8 → 25, `model` + `references` passthrough,
`voice` = library name **or** reference wav path). This run verifies the deployed MCP
tools end-to-end. All jobs submitted **via the MCP tools** (payloads inspected via
`GET /jobs/{id}` to confirm the client sent the right fields); slow jobs polled on the
pipeline since the MCP client aborts long tool calls (see Findings).

## Results — 15/15 checks passing

| # | Check | Job / evidence | Result |
|---|---|---|---|
| 1 | `media_list_voices` (sync) | manifest | ✅ 4 voices: `trailer`, `default`, `narrator_f`, `deep_m` (ref + sample + metadata each) |
| 2 | `media_generate_image` qwen21 default (25 steps) | `7f7eb7d1249b` | ✅ done ~42 s; payload `steps=25`; vision QA: coherent scene (typical model artifacts on bike wheels) |
| 3 | `media_text_to_speech` `voice="narrator_f"` | `68e0d8b97398` | ✅ done; F0 ≈ 245 Hz (bright female, matches portfolio) |
| 4 | `media_text_to_speech` `voice="default"` → trailer | `b9c387752ad5` | ✅ done; F0 ≈ 133 Hz (deep male trailer voice) |
| 5 | `media_edit_image` single image | `5d1ce0d1296d` | ✅ done; payload `steps=25`, `references=null`, output `model=qwen21, references=0`; vision QA 5/5 (sunset-beach edit, subject preserved) |
| 6 | `media_edit_image` with `references` | `8af46ff03ae0` | ✅ done; payload `references=[media_jobs/7f7eb7d1249b/…]` passed through; output `references=1`; vision QA 4/5 (second bicycle added, style-consistent) |
| 7 | `media_generate_image` `model="legacy"` (regression) | `3c61bae9f27d` | ✅ done; payload `model=legacy` passed through; vision QA: coherent image (legacy 2512 GGUF path intact) |
| 8 | `media_text_to_speech` `voice=<wav path>` (previously-broken contract) | `326835ff1e5e` | ✅ done; reference wav resolved + `--reference-audio` forwarded (server fix verified live) |
| 9 | unknown voice → clean 400 | `voice="nope"` | ✅ `Pipeline HTTP 400: unknown voice 'nope'. Available: deep_m, default, narrator_f, trailer (or pass a reference wav path, or 'reference_audio')` — no worker crash |
| 10 | `media_add_voice` `test_v` (7.2 s ref wav) | `d3f6410fc88b` | ✅ done; manifest entry + `test_v_ref.wav` + `test_v_sample.wav` created; `ref_duration_s=7.2` |
| 11 | `media_delete_voice` `test_v` (sync) | — | ✅ `{"deleted": "test_v"}`; manifest + files removed; protected names untouched |
| 12 | `media_freeze` (keyframe → 2 s static clip) | `7c1b74781e11` | ✅ done |
| 13 | `media_assemble` smoke (frozen shot + TTS VO) | `b829e9a1d4c9` | ✅ `final.mp4` 2.0 s 1280×720 h264+aac (VO mixed in) |
| 14 | `media_pull` signed URL | job ids | ✅ 48 h / 2 h tokens minted, URLs resolve (used for all vision QA above) |
| 15 | `media_info` (sync) | `b829e9a1d4c9` | ✅ duration/codec/resolution/fps reported |

## Findings (operational, not pipeline bugs)

| # | Finding | Severity | Status |
|---|---|---|---|
| 1 | **MCP tool calls abort at 30 s** (client/gateway side — exact 30.000 s observed: 17:46:17.457 → 17:46:47.457) while pipeline jobs continue and complete server-side. Long flows (image 30–120 s, shots/upscale minutes) need a submit-and-poll pattern or a raised tool timeout on the MCP host. | Medium (thor) | **FIXED 2026-09-25** (thor `mcp_media` tool) — submit-and-poll: every job tool takes `non_blocking` (returns `{job_id}` in ~1 s; `false` = legacy blocking) + new `media_job_result(job_id, wait_seconds≤25)` blocks ≤25 s per poll, under the 30 s client abort. Retested live 2026-09-25: image job `14802a5b1eee` (qwen21, 25 steps) submitted non-blocking, polled to `done` over 2 polls — no client abort |
| 2 | `media_pull` accepts a **job id** but 404s on `media_jobs/<job_id>/<file>` paths, though the tool description says "media_jobs file (or job_id)". | Low (thor) | **FIXED 2026-09-25** (thor `mcp_media` tool) — verified: `media_pull` now mints a signed URL for `media_jobs/8fb134a2ea1b/mp_8fb134a2ea1b_00001_.png` |
| 3 | Pipeline runs a **single worker queue** (`MAX_CONCURRENT_JOBS=1`): CPU ffmpeg jobs (freeze/assemble) queue behind GPU jobs. Expected behavior, but plan long commercial builds accordingly. | Info | Documented |

## Notes

1. F0 estimates computed on the pipeline host (autocorrelation on the voiced mid-section);
   ±10 Hz accuracy — sufficient for voice-identity confirmation, not pitch measurement.
2. Vision QA used `vision_analyze_image` over `media_pull` signed URLs (the vision server
   cannot read matrix paths directly).
3. Working docs `update_media_mcp_todo.md`, `voices_todo.md`, `voices_thor_handoff.md`
   deleted post-completion (repo convention: completed working docs removed, evidence kept
   here + in `docs/matrix_media_pipeline_api.md` changelog).

# Run: 2026-11-04 — job visibility (`GET /jobs` + job-ID logging + structured 404)

> Post-incident hardening after the 2026-11-02 "A Peanut Risky Business" film
> run (4 client-side orchestration crashes, 9 lost job IDs, ~1 h GPU + ~1 h
> recovery). Context: `docs/media_film_skill_spec.md` §1; contract:
> `docs/matrix_media_pipeline_api.md` (changelog 2026-11-04).

## Results — all checks passing (live, container `media_pipeline` rebuilt via `model-manager rebuild media-pipeline`)

| # | Check | Result |
|---|---|---|
| 1 | `GET /jobs?limit=3` after a fresh container restart (in-memory registry empty, `health.jobs=0`) | ✅ 3 finished jobs returned from the durable `jobs.jsonl` ledger with `created` (≈ `done − queue_wait − duration`), `done`, `model`, `duration_s`, `cost_usd`, `outputs` (run-dir-relative files from disk) |
| 2 | `GET /jobs?flow=shots&status=done&limit=2` | ✅ 2 rows, both `wan2.2-14b-i2v-gguf-q5`, durations 626.6 s / 546.41 s |
| 3 | `GET /jobs?client=pi&limit=200` / `?since=2026-11-03T00:00:00Z&limit=200` | ✅ 200 rows (cap) / 51 rows, oldest `2026-11-03T13:35:41Z` |
| 4 | `GET /jobs?status=running,queued` with no in-flight jobs | ✅ `{count: 0, jobs: []}` |
| 5 | `GET /jobs/badbadbadbad` (unknown ID) | ✅ HTTP 404 `{detail:{error:"unknown_job", message, hint}}` — hint points at `GET /jobs?since=…` |
| 6 | `GET /jobs?status=bogus` / `?since=notadate` | ✅ clean HTTP 400 with usage text |
| 7 | Full lifecycle: TTS job `5477b654a523` (`client=qa_jobs_ep`) — submit → `running` visible in `GET /jobs?client=qa_jobs_ep` → `done` with `output` (host path) + `outputs` (`5477b654a523/vo.wav`) + ledger row (`tts done qa_jobs_ep 15.26 s`) | ✅ all three sources agree |
| 8 | Container log lines | ✅ `job submitted: 5477b654a523 flow=tts client=qa_jobs_ep user=chuck` (01:36:21) + `job finished: 5477b654a523 status=done error=-` (01:36:36) in `docker logs media_pipeline` |
| 9 | **Restart durability:** `docker restart media_pipeline` → job `5477b654a523` still returned by `GET /jobs?client=qa_jobs_ep` with `status=done` + `outputs` from disk (`output` null — in-memory registry wiped, ledger + disk intact) | ✅ |

## Decisions

- **SQLite job registry deferred** — the existing `jobs.jsonl` ledger (10 MB cap,
  keep-newest rotation, never retention-swept) + `GET /jobs` covers the
  incident's recovery needs at current volume (~615 jobs / 400 KB since
  2026-09-06). Revisit only for submit-time persistence (in-flight jobs after
  a pipeline restart) or >10 MB history.
- **`GET /jobs` stays LAN-only** — the Mac reaches it via the thor MCP proxy;
  public `/dl/{token}` remains the off-LAN artifact path. No auth change on
  :8189 (LAN trust, consistent with the rest of the API).
- Thor-side MCP additions (`media_list_jobs`, SUBMIT/DONE log lines, 60 s
  timeout docs, `media_fetch` collision fix, mcp_vision fix) are **pending on
  thor** — spec: `docs/thor_media_mcp_handoff.md`. Client-side orchestration
  skill (state file, retry wrapper, resume protocol) is **pending on the
  Mac** — spec: `docs/media_film_skill_spec.md`.

# Run: 2026-11-27 — Films B1: NAS-durable film project store + storyboard approval gate

> Plan: `media-pipeline/films_plan_matrix.md` (batch B1 of the films plan).
> QA: `media-pipeline/qa_films_b1.py` (live API + on-disk + NAS assertions).
> Log: `/home/chuck/workspace/films-plan/logs/qa_b1.log`.
> Container `media_pipeline` rebuilt via `model-manager rebuild media-pipeline`;
> `/health` ok after every rebuild. **No other container touched**
> (comfyui_backend / qwen38 / ollama untouched; only `docker exec` ffmpeg).

## Results — 35/35 checks passing

| # | Check | Result |
|---|---|---|
| 1 | `/health` ok + `features` includes `projects` | ✅ |
| 2 | `POST /projects` create (slug/title/logline) → 200, dir on disk, layout dirs (`bible/`, `storyboard/`, `production/clips/`) | ✅ |
| 3 | Duplicate slug → 409; bad slug → 400 | ✅ |
| 4 | Asset upload (char sheet + env plate) into `bible/` (multipart, project-relative path) | ✅ |
| 5 | Path traversal (`../`) → 400 | ✅ |
| 6 | Shotplan save (valid two-tier plan) → 200 + motion-verb `schema_warnings` | ✅ |
| 7 | Invalid shotplan → **400 with `validation` field list** (bad enum; frames not 4k+1; action_impact >49f; duplicate shot_id) — never 500 | ✅ (all four) |
| 8 | Re-save good shotplan after invalid attempts (state not clobbered) | ✅ |
| 9 | 6 keyframes uploaded to `storyboard/keyframes/` | ✅ |
| 10 | `POST /storyboard_pages` → 2 grid pages; dims verified (1920×748 = 3×2 cells, 640×388 = 1 cell + footer bar) | ✅ |
| 11 | `POST /approve` (storyboard) → 200; GET shows `storyboard_approved` + approval ts; bogus `what` → 400 | ✅ |
| 12 | NAS sync: files present on `/mnt/lego/media_projects/<slug>/`, `nas_sync` state ok | ✅ |
| 13 | **NAS failure path** (`MEDIA_NAS_ROOT=/tmp/…` override + restart — no sudo for a live unmount): mutation succeeds, `nas_sync.pending=true`, forced sync → `ok:false, pending:true, reason:"nas not mounted"`; restore env + restart → sync recovers `ok:true, pending:false` | ✅ |
| 14 | `df --output=source /mnt/lego` inside the container shows a `//` CIFS source (mount probe basis) | ✅ |
| 15 | **Regression:** `/images` job submit → done (existing flow unaffected) | ✅ |

## Bugs found & fixed during B1 QA

1. `import projects` ran **before** `load_dotenv()` → `MEDIA_NAS_ROOT`/`MEDIA_PROJECTS_ROOT`
   env overrides never applied (projects reads config at import time). Fixed: import moved
   after `load_dotenv()` (same pattern as `metering`).
2. `nas_sync(force=True)` **skipped the mount probe** → a forced sync while the share was
   unmounted would rsync into a plain local dir (silent wrong-location write). Fixed: the
   probe ALWAYS runs; `force` only means "sync now".
3. `validate_shotplan` raised `AttributeError` (→ 500) on malformed shapes (beat/micro_shot
   not a dict). Fixed: defensive `isinstance` checks → 400 with field list.
4. Storyboard grid: `hstack`/`vstack` require ≥2 inputs → single-cell rows use a `null`
   filter passthrough; single-page footer math fixed.
5. ffmpeg `drawtext` `y=ih-24` invalid → `y=h-24` (main output ref).
6. Permission mismatch: service runs as **root**, ffmpeg as **comfy (uid 1024)** → project
   dirs + asset-upload parents `chmod 777` so both can write.
7. `-frames:v 1` required for the image2 muxer with lavfi inputs.

## Notes

- **NAS target decision:** `/mnt/lego/media-projects/` → actually `/mnt/lego/media_projects/`
  (CIFS `//lego.local/backup`, verified writable). Env-overridable via `MEDIA_NAS_ROOT`.
- **No sudo on matrix** → the B1 unmount QA test is implemented as a `MEDIA_NAS_ROOT`
  env-override + restart (exercises the same mount-probe → pending → retry path end-to-end).
- LUTs (B2) delivered by a `pi -p` subagent: **ffmpeg 6.1 `lut3d` reads interleaved RGB
  triples (Iridas), NOT the 3-section R/G/B layout** — verified empirically (identity test
  maxdiff=1; channel-swap test exact). 3× 33³ `.cube` starting points in
  `media-pipeline/luts/` + README.

# Run: 2026-11-27 — Films B2: FLF2V `end_keyframe`, `/animatic`, `/extract_frame`, `/assemble` LUT/grain

> Plan: `media-pipeline/films_plan_matrix.md` (batch B2 of the films plan).
> QA: `media-pipeline/qa_films_b2.py` (CPU tools) + `qa_films_b2_flf2v.py` /
> `qa_films_b2_flf2v_part2.py` (FLF2V GPU).
> Logs: `/home/chuck/workspace/films-plan/logs/qa_b2.log`, `qa_b2_flf2v.log`,
> `qa_b2_flf2v_p2.log`.
> Container `media_pipeline` rebuilt via `model-manager rebuild media-pipeline`;
> `/health` ok after every rebuild. **No other container touched**
> (comfyui_backend / qwen38 / ollama untouched; only `docker exec` ffmpeg).
> API strictly additive — existing flows unchanged; submit-time validation added for
> the new B2 flows (bad move / exactly-one-of / bad lut / grain>1 → clean 400).

## Results — CPU tools 24/24 + FLF2V GPU 5/5 (all passing)

### `/animatic`, `/extract_frame`, `/assemble` grade — `qa_films_b2.py` 24/24

| # | Check | Result |
|---|---|---|
| 1 | `/animatic` submit (3 shots, push_in/pull_out/shake) → 200 job_id | ✅ |
| 2 | `/animatic` done; video path on disk | ✅ |
| 3 | `/animatic` duration ≈ sum of shot durations | ✅ |
| 4 | `/animatic` dims 1280×720 | ✅ |
| 5 | `/animatic` frames differ (zoompan motion present) | ✅ |
| 6 | `/animatic` bad `move` → 400 (submit-time) | ✅ |
| 7 | `/extract_frame` `frame` index → 1 frame, pixel-identical to ffmpeg-direct | ✅ |
| 8 | `/extract_frame` `time` (s) → 1 frame | ✅ |
| 9 | `/extract_frame` `positions` [first,middle,last] → 3 frames | ✅ |
| 10 | `/extract_frame` `n=5` even spacing → 5 frames | ✅ |
| 11 | `/extract_frame` `frame`+`time` both → 400 (exactly-one-of) | ✅ |
| 12 | `/extract_frame` none → 400 | ✅ |
| 13 | `/assemble` (no grade) submit → done | ✅ |
| 14 | `/assemble` (graded: lut+grain) submit → done | ✅ |
| 15 | `/assemble` grade: duration identical to ungraded | ✅ |
| 16 | `/assemble` grade: LUT shifts color (U/V differ) | ✅ |
| 17 | `/assemble` bad `lut` → 400 (submit-time) | ✅ |
| 18 | `/assemble` `grain>1` → 400 (submit-time) | ✅ |

### FLF2V GPU — `qa_films_b2_flf2v*.py`

| # | Check | Result |
|---|---|---|
| 19 | 480p FLF2V (start+end) done; video on disk; `end_keyframe:true` | ✅ |
| 20 | 480p FLF2V metered (ledger: `model=wan2.2-14b-i2v-gguf-q8`, `cost>0`) | ✅ |
| 21 | **End-pose landing:** last frame closer to END than START (MSE 43.6 vs 29244.8; first≈start, last≈end visually) | ✅ |
| 22 | i2v 480p (same start + seed, NO end_keyframe) done; video; no `end_keyframe` flag | ✅ |
| 23 | **720p FLF2V (start+end) done → 720p ADMITTED** (peak 23349 MiB total on the 24 GB card) | ✅ |
| 24 | `end_keyframe` + `model=ltxv2b` → 400 (wan14b only) | ✅ |

## VRAM gate (GPU 1, 24 GB card) — FLF2V fits at 480p AND 720p

| Render | Peak (total on card, MiB) | Wall | Note |
|---|---|---|---|
| 480p FLF2V (start+end) | 23066 | 1068 s (~17.8 min) | slowed by early Ollama offload |
| 480p i2v (start only) | 23028 | ~12 min | existing path, unchanged |
| **720p FLF2V (start+end)** | **23349** | 3377 s (~56 min) | ≈ i2v 720p precedent (19.6 GB clean) + Ollama share |

**Decision: 720p FLF2V is ADMITTED** (not 480p-restricted). The absolute peaks include
Ollama's transient co-residency; ComfyUI's own footprint ≈ the i2v precedent (same model +
one extra end-frame latent).

## Caveat — Ollama co-residency on GPU 1 during QA

Ollama's copywriter (`gemma4:31b`, ~14 GB) was repeatedly loaded on GPU 1 (the
ComfyUI-dedicated card) during this QA window, unloading/reloading on its ~5 min idle
timeout. Effect: (1) early CPU offload slowed the renders (480p FLF2V 17.8 min vs the
i2v ~10 min precedent); (2) the VRAM baseline was contaminated (absolute peaks include
Ollama's share). The pipeline's existing VRAM headroom guard stops the copywriter for
music/tts/transcribe but **not** for `/shots` (adding it would change existing flow
behavior — out of scope for the additive B2). All renders completed without OOM; the
720p gate result is valid (it fit with the copywriter co-resident, so it fits a fortiori
on a clean card).

## Notes

- **FLF2V sampling:** `WanFirstLastFrameToVideo` uses **shift 8.0 / cfg 4.0** (the
  FLF2V-specific sampling) vs i2v's shift 5.0 / cfg 5.0 — from the official Comfy-Org
  `video_wan2_2_14B_flf2v` template. Dual-expert (high+low noise), 20+20 steps split at 10.
- **`/animatic` 3× pre-scale:** pre-scale to 3× before `zoompan` to avoid jittery
  pixel-stepping on the camera moves.
- **`/extract_frame` `n` is an independent mode** (even spacing across the duration), not
  part of `positions` — exactly one of `frame`/`time`/`positions`/`n`.
- **`signalstats` output format** (ffmpeg 6.1): `lavfi.signalstats.YAVG=<v>` (requires
  `,metadata=print` in the filter chain), not `YAVG:<v>`.
- **LUT delivery:** bundled `.cube` names resolve via `resolve_lut()` (bundled name /
  `.cube` filename / RUN_DIR-relative / absolute); staged into `RUN_DIR/luts/` at startup
  so comfyui_backend's ffmpeg reads them at `/comfy/mnt/luts/<name>.cube`.

# Run: 2026-11-27 — Films B3: `/qc_montage` clip QC montage

> Plan: `media-pipeline/films_plan_matrix.md` (batch B3 of the films plan).
> QA: `media-pipeline/qa_films_b3.py` (live API + on-disk assertions, CPU/ffmpeg only).
> Log: `/home/chuck/workspace/films-plan/logs/qa_b3.log`.
> Container `media_pipeline` rebuilt via `model-manager rebuild media-pipeline`;
> `/health` ok after every rebuild. **No other container touched.**
> API strictly additive — existing flows unchanged; submit-time validation added
> (bad n / missing source → clean 400).

## Results — 19/19 checks passing

| # | Check | Result |
|---|---|---|
| 1 | `/qc_montage` n=3 submit → 200 job_id | ✅ |
| 2 | `/qc_montage` n=3 done; single hstack PNG on disk | ✅ |
| 3 | `/qc_montage` n=3 dims = 3×cell (1440×270) | ✅ |
| 4 | `/qc_montage` n=3 returns 3 timestamps | ✅ |
| 5 | **QC frame[0] == `/extract_frame` first (pixel-identical)** | ✅ |
| 6 | **QC frame[1] == `/extract_frame` middle (pixel-identical)** | ✅ |
| 7 | `/qc_montage` n=5 done; dims = 5×cell (2400×270) | ✅ |
| 8 | `/qc_montage` n=5 timestamps even (first≈0, last≈4.9) | ✅ |
| 9 | `/qc_montage` label=false → done | ✅ |
| 10 | `/qc_montage` n=1 → 400 (submit-time) | ✅ |
| 11 | `/qc_montage` n=13 → 400 | ✅ |
| 12 | `/qc_montage` n=abc → 400 | ✅ |
| 13 | `/qc_montage` missing source → 400 | ✅ |

## Bugs found & fixed during B3 QA

1. **Last-frame extraction overshoot:** the original even-spacing clamp
   (`dur - 0.04`) put the last `-ss` seek **past** the last decodable frame on a
   5 s/24 fps clip (last frame at 4.958 s; seek to 4.960 → no frame → hstack
   "No such file or directory"). Fixed: `_even_frame_times()` clamps the last
   timestamp to `dur - 0.1` (safely before the final frame at any common frame
   rate). This is the new shared helper for B3/B5; the committed B2
   `/extract_frame` is unchanged.

## Notes

- **Single hstack PNG** (one row), not a grid — B5 `/montage` is the grid variant;
  both reuse `_extract_even_frames()`.
- **Labels** = `"<index>  <timestamp>s"` (e.g. `0  0.0s`), white on a black box,
  top-left of each cell (verified on a solid-color clip; the testsrc pattern's own
  frame counter is separate).
- **Frame identity:** the QC frames are extracted with the same `-ss <ts>` seek as
  `/extract_frame` positions, so first/middle are pixel-identical (the last frame
  uses the safer clamp and is not compared).

---

# Run: 2026-11-27 — Films B5: `/montage` generic video → montage grid

> Plan: `media-pipeline/films_plan_matrix.md` (batch B5 of the films plan).
> QA: `media-pipeline/qa_films_b5.py` (live API + on-disk assertions, CPU/ffmpeg only).
> Log: `/home/chuck/workspace/films-plan/logs/qa_b5.log` (fresh run 2026-11-27 22:0x,
> post-rebuild).
> Regression: `/home/chuck/workspace/films-plan/regress_b5.py` →
> `/home/chuck/workspace/films-plan/logs/regress_b5.log`.
> Container `media_pipeline` rebuilt via `docker compose -f compose/comfyui.yml
> --profile image build media-pipeline && up -d media-pipeline`; `/health` ok after
> the restart, `features` includes `montage`. **No other container touched.**
> API strictly additive — existing flows unchanged; submit-time validation added
> (bad n / missing source → clean 400). No new Python deps (ffmpeg-only).

## Results — 22/22 QA checks + 13/13 regression checks passing

| # | Check | Result |
|---|---|---|
| 1 | `/montage` n=9 (60 s clip) submit → 200 job_id | ✅ |
| 2 | n=9 done; grid PNG on disk | ✅ |
| 3 | n=9 grid dims = 3×3 cells (1440×810) | ✅ |
| 4 | n=9 `grid` field = `3x3` | ✅ |
| 5 | n=9 returns 9 timestamps | ✅ |
| 6 | n=9 timestamps evenly spaced (0, 7.5, 15, …, 59.9) | ✅ |
| 7 | n=9 last timestamp correct vs `/info` duration (dur−0.1 clamp) | ✅ |
| 8 | `/montage` n=4 done; grid dims = 2×2 (960×540) | ✅ |
| 9 | n=4 `grid` field = `2x2` | ✅ |
| 10 | `/montage` n=5 done; grid dims = 3×2 (1440×540, 1 black pad cell) | ✅ |
| 11 | n=5 `grid` field = `3x2` | ✅ |
| 12 | `/montage` label=false → done | ✅ |
| 13 | `/montage` n=1 → 400 (submit-time) | ✅ |
| 14 | `/montage` n=37 → 400 | ✅ |
| 15 | `/montage` n=abc → 400 | ✅ |
| 16 | `/montage` missing source → 400 | ✅ |

## Regression (cheap existing flows only, per TASK.md)

| # | Check | Result |
|---|---|---|
| 1 | `/health` ok + `features` includes `montage` + all pre-B5 feature flags | ✅ |
| 2 | `/trim` (M1) 60 s clip → 5 s cut: done, `/info` duration ≈ 5 s | ✅ |
| 3 | `/freeze` (M2) still → 2 s clip: done, `/info` duration ≈ 2 s | ✅ |
| 4 | `/qc_montage` (B3, shares the `_extract_even_frames()` helper) n=3: done, image on disk | ✅ |

(13/13 checks, log `logs/regress_b5.log`.)

## Notes

- **Grid geometry:** `cols=ceil(√n)`, `rows=ceil(n/cols)`; the last row is padded
  with black cells (generated via `lavfi color`) so every row is `cols` wide and
  `vstack` is uniform. n=5 → 3×2 with 1 pad cell; n=9 → 3×3; n=4 → 2×2.
- **Shared helper:** frame extraction reuses B3's `_even_frame_times()` /
  `_extract_even_frames()` (last frame clamped to `dur − 0.1`); no new extraction
  code path, no new deps (ffmpeg-only).
- **Session incident:** the previous autonomous session (2026-11-27 19:15) died on
  a provider 400 (image budget) after B3; B5's implementation + QA script were
  written and the first QA run passed, but the batch was not closed out. This run
  re-verified everything fresh on the rebuilt container before committing.
- **B5 DONE (2026-11-27):** committed per batch convention (`media: add /montage
  generic video montage grid (Films B5)`).

# Run: 2026-11-27 — Films B4: `/lipsync` (MuseTalk v1.5, dedicated GPU 1 worker)

Plan: `media-pipeline/films_plan_matrix.md` §5 (B4). Phase-0 VRAM gate PASSED
2026-11-27 22:54 (MuseTalk v1.5 peak **9,079 MiB** on GPU 1, 15.4 GB headroom vs
the 20 GB gate — `logs/b4_gate_result.json` + `b4_gate_run.log`). Integration:
new `musetalk` compose service (idle `python:3.12-slim` host, GPU 1 pinned
`device_ids ['1']`, b4 worker area mounted at `/b4`, job dirs at the same host
path) — the orchestrator `docker exec`s `scripts.inference` with the host venv
+ weights; **no new deps in media-pipeline proper** (plan §7). QA:
`media-pipeline/qa_films_b4.py`, log `logs/qa_b4.log` (fresh run on the rebuilt
container, 2026-11-27 23:5x).

## Results — 23/23 checks passing

| # | Check | Result |
|---|---|---|
| 1 | `/health` ok + `lipsync` in `features` | ✅ |
| 2 | TTS line (voice `trailer`) via `/tts` → done, wav 3..15 s | ✅ |
| 3 | `/lipsync` {video: talking-head clip (yongen.mp4, the gate demo pair), audio: TTS wav, `save_coord=true`} → done (GPU 1, ~90 s) | ✅ |
| 4 | Output `/info`: h264 704×1216 + aac audio stream; duration ≈ TTS wav duration (±0.6 s) — audio pass-through intact | ✅ |
| 5 | Lip-motion alignment at 3 timestamps (deterministic, no vision) | ✅ (see below) |
| 6 | 400 paths: missing `video` / missing `audio` / `face_box` len 3 / non-int / inverted | ✅ (5/5) |
| 7 | Metering row: `flow=lipsync`, `model=musetalk-v1.5`, `work_unit_kind=mpix_frames`, units > 0 | ✅ |
| 8 | Regression (cheap): `/health` still ok + `/montage` n=4 on the lipsync output → done | ✅ |

## Lip-motion alignment at 3 timestamps (deterministic)

Method: per-run face boxes from the MuseTalk `--saved_coord` pkl (list of
(x1,y1,x2,y2) per source frame). Regions: upper face = top 45% of the box
(forehead/eyes), lower face = bottom 55% (nose/mouth/chin), mouth = central
60% width × bottom 40% height. 96×96 crops compared in raw RGB. Thresholds
calibrated on the Phase-0 gate demo pair (yongen.mp4 + yongen.wav): upper MSE
8.5–10.2, lower MSE 180.9–238.2, mouth MAD 2.4–6.8 → QA thresholds: lower
MSE > 30 (≥2 of 3 ts), upper MSE < 40 (all 3), mouth MAD > 1.5 (≥2 of 3).

| t (s) | face box | upper MSE (out vs in) | lower MSE (out vs in) | mouth MAD (t vs t+0.2 s) |
|---|---|---|---|---|
| 1.63 | [223,278,466,526] | 7.8 (preserved) | 214.6 (re-rendered) | 3.2 (moving) |
| 2.72 | [225,278,464,526] | 11.2 (preserved) | 184.7 (re-rendered) | 4.6 (moving) |
| 3.80 | [223,276,464,524] | 10.9 (preserved) | 247.6 (re-rendered) | 8.2 (moving) |

Interpretation: the lower face is re-animated to the TTS line (MSE 185–248 ≫
encode noise), the upper face is untouched (MSE 8–11 ≈ encode noise), and the
mouth region moves across frames in step with the speech — lip motion aligned
at all 3 timestamps. No vision calls (deterministic checks only — Qwen image
budget).

## Notes

- **Audio pass-through:** MuseTalk re-renders the video frames and muxes the
  original wav in (`/info`: aac stereo, output duration ≈ TTS wav duration) —
  the source audio is never re-encoded/modified.
- **Metering:** one diffusion frame per output frame → `mpix_frames` (demo
  8 s pair: 171.2 units, $0.000993 — `jobs.jsonl`).
- **Headroom guard:** extended to `lipsync` (≥10 GiB on the media card; stops
  the Ollama copywriter if resident — same pattern as music/tts/transcribe).
- **Known behavior:** output duration ≈ audio length (one frame per audio
  chunk at the source fps); audio longer than the video cycles the face —
  documented in the API contract (keep the line shorter than the shot).
- **Session incident:** the previous worker session (attempt 1, 2026-11-27)
  timed out at 900 s after implementing the flow + compose service + metering
  and completing one full reference run (gate demo pair, job 69113402d792,
  72.3 s wall). This run re-verified fresh (QA 23/23) before closing out.
- **B4 DONE (2026-11-27):** committed as `fd18fad` (`media: add /lipsync
  (Films B4)`; worker sessions run with commits disabled — orchestrator commits).

# Run: 2026-11-27 — Films plan FINAL wrap-up (plan close)

Plan: `media-pipeline/films_plan_matrix.md` §8 (Definition of done). All DoD
items verified; plan closed. Evidence:

- **NAS mirror verified append-only (DoD item):** live test via the service API
  (project `qa_final_mirror`, 2026-11-27 23:34 UTC):
  - CIFS mount probe active — `df --output=source /mnt/lego` = `//lego.local/backup`.
  - New files propagate: asset upload + `POST /projects/{slug}/sync` →
    `/mnt/lego/media_projects/qa_final_mirror/bible/mirror_probe.txt` present,
    md5-identical to local.
  - **Local deletions do NOT propagate:** local `rm` of `mirror_probe.txt` +
    re-sync → file still on the NAS, content md5 intact; sync report
    `files:4, local_files:3` (NAS keeps the extra). Code: `projects.py` rsync
    is `-a --ignore-existing` (never `--delete`); `DELETE /projects/{slug}`
    keeps the NAS mirror by design.
  - Pre-existing B1 QA project (`qa_films_b1_164155`) intact on the NAS (9/9
    files).
- **Final regression 26/26 PASS** (`regress_final.py`, log
  `logs/regress_final.log`): `/health` ok + all 8 Films features; one cheap job
  per CPU flow — `/trim` (60 s → 5 s cut), `/freeze` (still → 2 s),
  `/extract_frame` (n=3, 3 PNGs on disk), `/animatic` (2 shots + 5 s wav,
  dur ≈ 5 s), `/assemble` (5 s window + `lut=cinematic_teal_amber` +
  `grain=0.3`), `/qc_montage` (n=3), `/montage` (n=4 → 2×2 grid), `/projects`
  (list + manifest + `nas_sync.ok`). GPU flows (shots/images/upscale/tts/music/
  sfx/transcribe/lipsync) were each exercised end-to-end in their per-batch QA
  (qa_b1–qa_b4 logs) — not re-run here (GPU minutes, no new coverage).
- **`/extract_frame` fix (found by the final regression):** the `n`
  even-spacing path and `positions: "last"` still used the pre-B3-fix `dur−0.04`
  clamp — on a 24 fps clip the last `-ss` (59.960 s of a 60 s clip) lands past
  the last decodable frame (59.9583 s), so ffmpeg produced no frame and the job
  reported success with a dangling path. Fix: clamp to `dur−0.1` (same as B3's
  `_even_frame_times()`) + every extraction path raises a job error when no
  frame is produced. media-pipeline rebuilt + restarted; `/health` ok after
  restart; final regression re-run → 26/26 (first run was 25/26, the
  `extract_frame` dangling-path check failing — the bug above).
- **DoD (§8) met:** B1–B3+B5 shipped & QA'd ✓ (7d0b7f6, f63563b, fff9bbb,
  4892af1) · B4 shipped with gate measurement ✓ (1058754 + fd18fad) · docs
  updated ✓ · NAS mirror verified append-only ✓ · regression clean ✓.
  Thor can pick up `docs/thor_films_handoff.md`.
- **Closing commit:** `docs: close films plan (B1–B5 shipped; B4 shipped)`
  (made via bash per the TODO's global instructions: "Commits are made
  explicitly via bash per TODO").

# Run: 2026-11-28 — T4 verification: agent-job key pass-through (5 checks, restricted SSH transport)

Context: `docs/matrix_key_passthrough.md` (contract of record) + Thor
handoff `thor_key_passthrough_handoff.md` §T4. Run from Thor (192.168.4.54)
over the now-live restricted Thor→Matrix SSH transport: dedicated
`matrix-agent` ssh alias, command-restricted to
`/home/chuck/homelab/scripts/run-with-key.sh` (no shell), job envelope on
ssh stdin (line 1 = caller key, line 2 = pi args JSON). Test key:
`t4-test-20261128` (user_id `t4-test`, hash `eb2ce4b2…`, max_budget $1.0,
24 h expiry) minted via the LiteLLM admin API; **revoked + cleaned up at
the end of the run** (keys referenced by name/prefix only — no key values
in this log).

| # | Check | Result |
|---|---|---|
| 1 | Trivial job with the test key (`matrix-agent-launch.sh -- -p "Reply OK"`) | ❌ **BLOCKED (wrapper)** — exit 127: `run-with-key.sh: line 68: exec: pi: not found` (diagnosis below) |
| 2 | Spend lands on the TEST key, not chuck's | ⚠️ **PARTIAL (proxy-side only)** — test key spend **$0.00** (no LLM call occurred — the job never reached `pi`); no test-job spend on chuck's key; end-to-end attribution unverified until check 1 passes |
| 3 | Keyless launch fails with no fallback | ✅ **PASS** — `ssh matrix-agent </dev/null` → exit 2, `run-with-key.sh: no key on stdin (keyless launches are rejected)`; Thor-side intake also rejects keyless (exit 2, `no caller key`) |
| 4 | Revoked/over-budget key fails fast with proxy 401/403, no fallback | ⚠️ **PARTIAL (proxy-side only)** — revoked test key → **HTTP 401 in 0.048 s** (`token_not_found_in_db`); session-level launch blocked by the same wrapper issue (fails at `exec pi` before any LLM call) |
| 5 | Evidence recorded in both validation logs | ✅ **PASS** — this entry + Thor-side entry in `docs/thor_validation_log.md` (part 15) |

**Check 1 evidence (captured, output redacted by the launch script):**

```
$ MATRIX_CALLER_KEY=<t4-test-20261128> scripts/matrix-agent-launch.sh -- -p "Reply OK"
matrix-agent-launch: launching agent job on matrix-agent (/home/chuck/homelab/scripts/run-with-key.sh)
matrix-agent-launch: pi args (redacted): -p Reply OK
matrix-agent-launch: key: via stdin only (env/stdin, never argv); not stored; all output redacted
/home/chuck/homelab/scripts/run-with-key.sh: line 68: exec: pi: not found
matrix-agent-launch: job ended with exit code 127 (output above is redacted)
exit=127 elapsed=0s
```

**Check 3 evidence (captured):**

```
$ ssh matrix-agent </dev/null
Pseudo-terminal will not be allocated because stdin is not a terminal.
run-with-key.sh: no key on stdin (keyless launches are rejected)
exit=2

$ MATRIX_CALLER_KEY= scripts/matrix-agent-launch.sh -- -p "Reply OK" </dev/null
matrix-agent-launch: ERROR: no caller key — set MATRIX_CALLER_KEY or pipe the key on stdin (keyless launches are rejected; T3)
exit=2
```

**Check 4 evidence (captured, proxy-side):**

```
$ POST /key/delete {"keys":["eb2ce4b2…"]}   # master key, admin API
{"deleted_keys":["eb2ce4b2…"]}

$ curl -w '%{http_code} %{time_total}s' /v1/models -H "Authorization: Bearer <revoked test key>"
http=401 time=0.047990s
{"error":{"message":"Authentication Error, Invalid proxy server token passed. … Unable to find token in cache or `LiteLLM_VerificationTokenTable`","type":"token_not_found_in_db",…}}

$ GET /key/list → total_count 8 (was 9) — test key absent
$ GET /spend/keys → no `t4-test` row
```

**Check 2 evidence (proxy-side spend snapshots, `/spend/keys`, master key):**
baseline → post-run: chuck `1310.66` → `1313.15` (delta = the verification
session's own usage on chuck's key — the test job never made an LLM call);
test key `t4-test` present at **$0.00** throughout; all other keys
unchanged. No spend was attributed to the test job on any key.

## Blocker — Matrix-side wrapper fix required (2 items)

1. **`pi` not in the forced-command PATH.** In the ssh forced-command
   context (non-interactive, non-login), `~/.pi/agent/bin` is not on the
   PATH, so `exec pi` (line 68 of the live stdin-patched wrapper) fails
   with 127. Fix in `scripts/run-with-key.sh`: `export PATH="$HOME/.pi/agent/bin:$PATH"`
   (or `exec "$HOME/.pi/agent/bin/pi"`) before the `exec pi` line. The
   stdin envelope mechanics themselves work (key read from stdin line 1,
   keyless rejected with exit 2 — check 3).
2. **Base URL must be the LAN proxy, not the public route.** The committed
   wrapper pins `LITELLM_BASE_URL="https://llm.choukalos.com"` — but the
   public route is Caddy key-gated to chuck's + dylan's keys only
   (Caddyfile `@llm` handle, security Phase 1): verified 2026-11-28, the
   test key got **401 on the public route / 200 on the LAN proxy**
   (`http://192.168.4.54:4000`), while chuck's key got 200 on both. Caller
   keys can only traverse the LAN. If the live wrapper still pins the
   public URL, switch it to `http://192.168.4.54:4000`.

**Repo note:** the live stdin-patched wrapper (≈68 lines) is **ahead of
the pushed repo** — as of `ca618d0` the repo's `scripts/run-with-key.sh`
still carries the argv-only version. The stdin patch (plus the two fixes
above) needs to be committed from the Matrix working tree so the transport
entry point is under version control.

**Cleanup:** test key `t4-test-20261128` revoked via the admin API
(`deleted_keys` confirmed) and absent from `/key/list` + `/spend/keys`;
no key values persisted anywhere (launch-script output is redacted;
admin API responses referenced by hash/name only).

**Re-run:** once the wrapper fixes are live on Matrix, re-run T4 checks
1/2/4 (fresh small-budget test key): expect `Reply OK` session success,
spend on the test key only, and a fast 401 on the revoked-key launch with
no fallback to chuck's key.

# Run: 2026-11-28 (attempt 2) — T4 verification re-run: checks 3 + 4 (proxy-side) re-verified; checks 1/2/4 end-to-end still blocked — Matrix-side manual fix required

Context: follow-up to the T4 run above (attempt 1). The two wrapper fixes
identified there (PATH for `pi` + LAN base URL) live in the **live wrapper on
Matrix** and cannot be applied from Thor: the only Thor→Matrix execution
channel is the restricted `matrix-agent` ssh key, whose forced command is the
very wrapper that is broken (`exec: pi: not found`, exit 127) —
chicken-and-egg. A fix job launched over the transport therefore also exits
127 (verified 2026-11-28, captured on Thor in
`/tmp/t4-evidence/fixjob-run.log`). No other Thor→Matrix execution channel
exists (no shell key; filebridge is a file relay only; media-pipeline /
ComfyUI APIs are keyless and non-shell). The wrapper patch + step-2 cutover
are therefore a **manual task for chuck on Matrix** — turnkey instructions:
Thor `tmp/pi-long-task/2026-11-28T03-56-11-368Z-cc40aca4/matrix-side-fix-instructions.md`
(same pattern as the T1 install instructions).

| # | Check | Result (attempt 2) |
|---|---|---|
| 1 | Trivial job with the test key | ❌ **BLOCKED (Matrix wrapper)** — unchanged: exit 127 `exec: pi: not found`; the fix cannot be delivered over the transport itself (chicken-and-egg, above) |
| 2 | Spend lands on the TEST key, not chuck's | ⚠️ **BLOCKED on check 1** — fresh test key `t4-test2` (hash `3a904dc7…`, max_budget $1.0, 24 h expiry) minted and verified live: **200 on the LAN proxy** (`/v1/models`, 0.042 s) but **401 on the public route** (Caddy family-key gate, 0.099 s) — caller keys must traverse `http://192.168.4.54:4000`; spend still $0.00 (no LLM call yet) |
| 3 | Keyless launch fails with no fallback | ✅ **PASS (re-verified)** — `ssh matrix-agent </dev/null` → exit 2, `run-with-key.sh: no key on stdin (keyless launches are rejected)`; Thor-side intake also rejects (exit 2, `no caller key … T3`) |
| 4 | Revoked/over-budget key fails fast, no fallback | ⚠️ **PARTIAL (proxy-side, re-verified)** — second zero-budget probe key `t4-test2-revoked` (hash `be4cb2de…`) minted → revoked (`deleted_keys` confirmed) → GET `/v1/models` with it → **HTTP 401 in 0.028 s** (`token_not_found_in_db`); absent from `/key/list` (9 keys). Session-level launch still blocked by the wrapper issue |
| 5 | Evidence recorded in both validation logs | ✅ **PASS** — this entry + Thor-side entry in `docs/thor_validation_log.md` (part 16) |

**Step-2 cutover status:** still pending on Matrix — a precondition for check
2 (the stored `auth.json` credential beats the env key, so until the cutover
runs a caller-key session would bill chuck's key). The films-plan gate is
**cleared**: the films long task finished and the plan is CLOSED
(`ca618d0`, 2026-11-27). The cutover is included in the manual task above.

**Captured evidence (redacted; full captures on Thor in `/tmp/t4-evidence/`):**

```
# Check 3 (PASS, re-verified)
$ ssh matrix-agent </dev/null
Pseudo-terminal will not be allocated because stdin is not a terminal.
run-with-key.sh: no key on stdin (keyless launches are rejected)   # exit=2
$ MATRIX_CALLER_KEY= scripts/matrix-agent-launch.sh -- -p "Reply OK" </dev/null
matrix-agent-launch: ERROR: no caller key — set MATRIX_CALLER_KEY or pipe the key on stdin (keyless launches are rejected; T3)   # exit=2

# Check 1 blocker (re-confirmed — a fix job over the transport also 127s)
$ MATRIX_CALLER_KEY=<chuck key> scripts/matrix-agent-launch.sh -- -p "<fix instructions>"
/home/chuck/homelab/scripts/run-with-key.sh: line 68: exec: pi: not found   # exit=127

# Check 2 (proxy-side) — fresh test key t4-test2 (hash 3a904dc7…)
$ GET /v1/models, LAN proxy  http=200 time=0.041530s   (model list incl. matrix-coder)
$ GET /v1/models, public     http=401 time=0.099188s   (Caddy: "Unauthorized")
$ GET /spend/keys (baseline) chuck $1315.85 · t4-test2 $0.00 (max_budget $1.0)

# Check 4 (proxy-side, re-verified) — probe key t4-test2-revoked (hash be4cb2de…)
$ POST /key/generate (max_budget 0.0, 1 h expiry) → token be4cb2de…
$ POST /key/delete {"keys":["be4cb2de…"]} → {"deleted_keys":["be4cb2de…"]}
$ GET /v1/models with the revoked key, LAN proxy → http=401 time=0.028098s
  "Authentication Error, Invalid proxy server token passed … token_not_found_in_db"
$ GET /key/list → 9 keys; probe token absent; t4-test2 present
```

**Cleanup / key state:** probe key `t4-test2-revoked` revoked + confirmed
absent. `t4-test2` (hash `3a904dc7…`) intentionally **left live** (24 h
expiry, max_budget $1.0, LAN-only usable given the Caddy gate) for the
end-to-end re-run once the wrapper fix is live; its value lives only in
Thor's `/tmp/t4-evidence/t4-test2.key` (chmod 600). If the window lapses,
revoke via `POST /key/delete {"keys":["3a904dc7…"]}` (admin API, LAN) or
re-mint. No key values in this log.

**Re-run:** once chuck applies the manual task (wrapper patch + cutover),
re-run T4 checks 1/2/4 end-to-end with `t4-test2` (or a fresh small-budget
key): expect `Reply OK` session success, spend on the test key only (chuck
unchanged vs the $1315.85 baseline), and a fast 401 on the revoked-key
launch with no fallback to chuck's key.

# Run: 2026-11-28 (attempt 3) — T4 re-run: check 3 re-verified PASS; checks 1/2/4 end-to-end still blocked — Matrix-side manual fix not yet applied

Context: follow-up to attempt 2. As of 04:20 UTC the manual task
(Thor `tmp/pi-long-task/2026-11-28T03-56-11-368Z-cc40aca4/matrix-side-fix-instructions.md`)
had **not yet been applied on Matrix** — the live wrapper is byte-identical
to the pre-patch version (same `line 68: exec: pi: not found` failure; the
PATH export would shift the `exec pi` line to 69). Polling continued until
04:26 UTC (4 launch probes, all exit 127 —
`/tmp/t4-evidence/check1-poll1..4.txt`); the wrapper remained unpatched.
Everything verifiable from Thor was re-verified with fresh captures below.

| # | Check | Result (attempt 3) |
|---|---|---|
| 1 | Trivial job with the test key | ❌ **BLOCKED (Matrix wrapper)** — re-run with `t4-test2` **and** with fresh `t4-test3`: both exit 127, `line 68: exec: pi: not found` (wrapper unpatched — line number unchanged) |
| 2 | Spend lands on the TEST key, not chuck's | ⚠️ **BLOCKED on check 1** — no LLM call was made (the wrapper exits before `pi` starts, so our launches make zero proxy API calls); spend snapshots: chuck `$1316.74 → $1316.96` (unrelated activity), `t4-test2` **$0.00 throughout** (max_budget $1.0) |
| 3 | Keyless launch fails with no fallback | ✅ **PASS (re-verified)** — `ssh matrix-agent </dev/null` → exit 2, `run-with-key.sh: no key on stdin (keyless launches are rejected)`; Thor-side intake also rejects (exit 2, `no caller key … T3`) |
| 4 | Revoked/over-budget key fails fast, no fallback | ⚠️ **PARTIAL (proxy-side, re-verified)** — fresh probe `t4-test3` (prefix `sk-AaFX8Gt42…`, $1 budget, 24 h): minted → **200 LAN in 0.021 s** → revoked (`deleted_keys` confirmed) → **HTTP 401 in 0.045 s** (`token_not_found_in_db`); absent from `/key/list` (9 keys) + `/spend/keys`; launch with the revoked key over the wrapper → 127 (fails before the proxy — no fallback, no API call) |
| 5 | Evidence recorded in both validation logs | ✅ **PASS** — this entry + Thor-side entry in `docs/thor_validation_log.md` (part 17) |

**Captured evidence (redacted; full captures on Thor in `/tmp/t4-evidence/`):**

```
# Check 3 (PASS, re-verified)
$ ssh matrix-agent </dev/null
Pseudo-terminal will not be allocated because stdin is not a terminal.
run-with-key.sh: no key on stdin (keyless launches are rejected)   # exit=2
$ MATRIX_CALLER_KEY= scripts/matrix-agent-launch.sh -- -p "Reply OK" </dev/null
matrix-agent-launch: ERROR: no caller key — set MATRIX_CALLER_KEY or pipe the key on stdin (keyless launches are rejected; T3)   # exit=2

# Check 1 blocker (re-confirmed, both test keys)
$ MATRIX_CALLER_KEY=<t4-test2> scripts/matrix-agent-launch.sh -- -p "Reply OK"
/home/chuck/homelab/scripts/run-with-key.sh: line 68: exec: pi: not found   # exit=127
$ MATRIX_CALLER_KEY=<t4-test3> scripts/matrix-agent-launch.sh -- -p "Reply OK"
/home/chuck/homelab/scripts/run-with-key.sh: line 68: exec: pi: not found   # exit=127

# Check 2 (proxy-side spend snapshots, /spend/keys, master key)
baseline (04:19 UTC):  chuck $1316.74 · t4-test2 $0.00 (max_budget $1.0)
post-run   (04:24 UTC): chuck $1316.96 (delta = unrelated activity — our launches
                        made no API calls; the wrapper exits before pi starts)
                       · t4-test2 $0.00 · t4-test3 ABSENT (revoked)

# Check 4 (proxy-side, re-verified) — probe key t4-test3 (prefix sk-AaFX8Gt42…)
$ POST /key/generate (max_budget 1.0, 24 h expiry) → token sk-AaFX8Gt42…
$ GET /v1/models, LAN proxy (http://192.168.4.54:4000) → http=200 time=0.020910s
$ POST /key/delete {"keys":["sk-AaFX8Gt42…"]} → {"deleted_keys":[…]}
$ GET /v1/models with the revoked key (LAN) → http=401 time=0.045214s
  "Authentication Error, Invalid proxy server token passed … token_not_found_in_db"
$ GET /key/list → 9 keys; t4-test3 token ABSENT; t4-test2 (3a904dc7…) present
$ MATRIX_CALLER_KEY=<revoked t4-test3> scripts/matrix-agent-launch.sh -- -p "Reply OK"
/home/chuck/homelab/scripts/run-with-key.sh: line 68: exec: pi: not found   # exit=127 (no proxy call, no fallback)
```

**Cleanup / key state:** `t4-test3` revoked + confirmed absent from
`/key/list` and `/spend/keys`; its value lived only in
`/tmp/t4-evidence/t4-test3.key` (chmod 600, redacted gen response).
`t4-test2` (hash `3a904dc7…`) still live (24 h expiry from attempt 2,
$1.0 budget, $0.00 spend) — intentionally left for the end-to-end re-run
once the wrapper fix is live. No key values in this log.

**Re-run (unchanged):** chuck applies the manual task on Matrix (wrapper
patch: PATH export + LAN base URL; then step-2 cutover via
`scripts/enable-key-passthrough.sh` — gate cleared, films plan CLOSED
`ca618d0`), then re-run T4 checks 1/2/4 end-to-end with `t4-test2`:
expect `Reply OK` session success, spend on `t4-test2` only (chuck
unchanged vs the $1316.96 post-attempt-3 baseline), and a fast 401 on the
revoked-key launch with no fallback to chuck's key.

# Run: 2026-11-28 (final) — T4 end-to-end: checks 1–4 PASS over the fixed restricted SSH transport

Context: follow-up to attempt 3. The manual task was applied on Matrix
(wrapper patch + step-2 cutover; the Matrix-side `~/.pi/agent/models.json`
fix — `litellm-native` baseUrl → `http://192.168.4.54:4000/v1` + `litellm`
provider entry, stale `models-store.json` cleared — was applied earlier the
same day via fix jobs over the transport). This run re-ran T4 checks 1/2/4
**end-to-end** over the fixed transport with fresh small-budget keys
(`t4-test2` had already been revoked in a prior run, so fresh keys were
minted per the run plan). The wrapper was deterministically reconstructed
on Thor (shipped @ `ca618d0` + T1 stdin patch + T4 patch) and **sha256-
verified against the live wrapper over the transport** (see below).

## Results — checks 1–4 all PASS

| # | Check | Result (final) |
|---|---|---|
| 1 | Trivial job with the test key — `MATRIX_CALLER_KEY=<test-key> scripts/matrix-agent-launch.sh -- -p "Reply OK"` | ✅ **PASS** — fresh `t4-test7` (prefix `sk-...CaZA`, $1.0, 24 h) → Matrix pi session replied `OK`, job completed exit 0 (05:19:31Z) |
| 2 | Spend lands on the TEST key, not chuck's | ✅ **PASS** — `t4-test7` spend **$0.0000712** (spend-log row ip **192.168.4.55** = Matrix); chuck's drift $1327.366 → $1327.504 is 100% unrelated thor-side activity (18 rows, all ip 172.18.0.8, `qwen38-27b`); **zero** chuck rows from Matrix (192.168.4.55) or thor host (192.168.4.54) — no fallback |
| 3 | Keyless launch fails with no fallback | ✅ **PASS (re-verified)** — `ssh matrix-agent </dev/null` → exit 2, `run-with-key.sh: no key on stdin (keyless launches are rejected)`; Thor-side intake also rejects (exit 2, `no caller key … T3`) |
| 4 | Revoked key fails fast with proxy 401/403, no fallback | ✅ **PASS (end-to-end)** — throwaway `t4-test8` (prefix `sk-...OT3w`, $0.50, 24 h): live launch → `OK` exit 0 → revoked (`deleted_keys` confirmed) → relaunch → **401 `token_not_found_in_db` in 1.12 s** end-to-end (exit 1); LAN probe with the revoked key → 401 in 0.052 s; spend-log rows for the revoked relaunch are `status=failure`, **$0 spend**; all chuck rows in the window are 172.18.0.8 — chuck's key never touched |
| 5 | Evidence recorded in both validation logs | ✅ **PASS** — this entry + Thor-side entry in `docs/thor_validation_log.md` (part 18) |

## Wrapper state (live, sha256-verified over the transport)

The live wrapper is the shipped version @ `ca618d0` + the T1 stdin patch
(dual-mode argv/stdin envelope) + the T4 patch (PATH export + LAN base URL)
— 69 lines, fix state:

```
 66  export LITELLM_BASE_URL="http://192.168.4.54:4000"
 68  export PATH="$HOME/.pi/agent/bin:$PATH"
 69  exec pi "${pi_args[@]}"
```

**sha256 (reconstructed on Thor, matches the live wrapper exactly):**
`684cc3fe97e0b2ce37f362e5b6cdd9db026b928dff1796f8c8d52fc5c648e171`

Verified live via a one-shot job over the transport (ephemeral `t4-verify`
key, $0.50, revoked + shredded after the run):

```
$ MATRIX_CALLER_KEY=<t4-verify> scripts/matrix-agent-launch.sh -- -p "Run exactly this bash command: sha256sum /home/chuck/homelab/scripts/run-with-key.sh — then reply with ONLY the 64-character hex hash …"
684cc3fe97e0b2ce37f362e5b6cdd9db026b928dff1796f8c8d52fc5c648e171   # exit 0
```

This resolves the attempt-1 repo note: the reconstructed wrapper is now in
the clone (`scripts/run-with-key.sh`, pending commit) and byte-identical to
the live entry point.

## Cutover state (step 2 — COMPLETE, 2026-11-28)

- `~/.pi/agent/auth.json`: litellm key field **absent** (chuck's stored
  credential removed — caller keys are the only auth).
- `~/.pi/agent/models.json`: `litellm-native` apiKey **env-interpolated**
  (`$ENV`), `litellm-native` baseUrl → `http://192.168.4.54:4000/v1`,
  `litellm` provider entry present (`http://192.168.4.54:4000`,
  `allowInsecureHttp`); stale public-route `models-store.json` cleared
  (`.bak` kept).
- `~/.pi/agent/litellm.key` present, chmod 600.

## Captured evidence (redacted; full captures on Thor in `/tmp/t4-evidence/`)

```
# Check 1 (PASS) — t4-test7 (sk-...CaZA, $1.0, 24 h; minted 05:19:23Z)
$ MATRIX_CALLER_KEY=<t4-test7> scripts/matrix-agent-launch.sh -- -p "Reply OK"
matrix-agent-launch: launching agent job on matrix-agent (/home/chuck/homelab/scripts/run-with-key.sh)
matrix-agent-launch: pi args (redacted): -p Reply OK
matrix-agent-launch: key: via stdin only (env/stdin, never argv); not stored; all output redacted
OK
matrix-agent-launch: job completed (exit 0)   # exit=0, 05:19:31Z

# Check 3 (PASS, re-verified)
$ ssh matrix-agent </dev/null
Pseudo-terminal will not be allocated because stdin is not a terminal.
run-with-key.sh: no key on stdin (keyless launches are rejected)   # exit=2
$ MATRIX_CALLER_KEY= scripts/matrix-agent-launch.sh -- -p "Reply OK" </dev/null
matrix-agent-launch: ERROR: no caller key — set MATRIX_CALLER_KEY or pipe the key on stdin (keyless launches are rejected; T3)   # exit=2

# Check 4 (PASS end-to-end) — throwaway t4-test8 (sk-...OT3w, $0.50, 24 h)
$ MATRIX_CALLER_KEY=<t4-test8> scripts/matrix-agent-launch.sh -- -p "Reply OK"
OK   # live launch, exit 0 (05:19:41Z)
$ POST /key/delete {"key_aliases":["t4-test8"]}   # master key, admin API
{"deleted_keys":["t4-test8"]}
$ MATRIX_CALLER_KEY=<revoked t4-test8> scripts/matrix-agent-launch.sh -- -p "Reply OK"
401: {"message":"Authentication Error, Invalid proxy server token passed. …","type":"token_not_found_in_db","param":"key","code":"401"}
matrix-agent-launch: job ended with exit code 1 (output above is redacted)   # exit=1, 1.12 s end-to-end
$ curl -w '%{http_code} %{time_total}s' /v1/chat/completions -H "Authorization: Bearer <revoked t4-test8>"
http=401 time=0.052s   (token_not_found_in_db)
```

## Spend before/after (`/spend/keys` + per-request spend logs, master key)

```
/spend/keys snapshots:
baseline (05:19:23Z):  chuck $1327.366102 · t4-test7 $0.00 · t4-test8 $0.00
post-run (05:21:32Z):  chuck $1327.504167 · t4-test7 $0.0000712 (check 1) · t4-test8 $0.000398 (check 4 + probes)

per-request spend logs, window 05:17–05:23Z (requester IP per row):
  t4-test7: 1 row, $0.0000712, ip 192.168.4.55 (Matrix) — the check 1 job
  t4-test8: 5 rows, $0.000398, ip 192.168.4.55 (Matrix jobs) + 192.168.4.54 (thor LAN probe)
  chuck:    18 rows, $0.507410, ALL ip 172.18.0.8 (thor docker, qwen38-27b) — unrelated activity;
            ZERO rows from 192.168.4.55 (Matrix) or 192.168.4.54 (thor host) → no fallback to chuck's key
```

## Key lifecycle / cleanup

- `t4-test2` (hash `3a904dc7…`): revoked in a prior run, confirmed absent —
  fresh keys minted for this run per the run plan.
- This run: `t4-test7` ($1.0) + `t4-test8` ($0.50) minted via the LAN admin
  API; `t4-verify` ($0.50) minted solely for the live sha256 verification.
- All three **revoked** at the end (`deleted_keys` confirmed each) and
  confirmed **absent from `/key/list`** (8 keys remain) + `/spend/keys`.
- Local key files **shredded** (`shred -u`); no `.key` files remain on Thor.
- No key values in this log (names/prefixes/hashes only; launch output is
  redacted by the launch script; admin API responses referenced by alias only).

**T4 CLOSED:** all four checks pass end-to-end over the fixed restricted SSH
transport; spend is attributed to the caller's test key with chuck's key
unchanged (drift fully attributed to unrelated thor-side activity); keyless
and revoked paths reject correctly with no fallback.
