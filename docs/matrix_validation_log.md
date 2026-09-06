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
