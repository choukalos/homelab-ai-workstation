# Matrix — Media Work Metering (handoff spec)

**Audience:** the agent working on Matrix. This file is self-contained: read it,
verify the assumptions in §8 against the real code (`choukalos/homelab-ai-workstation`,
`media-pipeline/`), implement §4–§6, run §7, and **flag any §8 assumption that
doesn't hold** rather than improvising a different design.

Goal: attribute GPU work from the media pipeline (ComfyUI diffusion models:
Flux images, LTXV video, SeedVR2 upscale, ACE-Step music, MMAudio SFX) to the
**user** who kicked off each job, expressed as **approximate tokens + $**, so
Thor's Grafana can show total work $ = LLM + media, by user.

No Langfuse. No ComfyUI changes. All work happens in the media-pipeline
service (docker container on Matrix, `:8189`) + one DCGM config addition.

---

## 1. Architecture

```
User (chuck/dylan)
  → LiteLLM proxy (Thor:4000)  ← knows the user's API key
  → mcp_media (Thor, MCP)      ← forwards caller key in Authorization header
  → media-pipeline (Matrix:8189)  ← NEW: stores `user` on the job, meters it
       ├─ ComfyUI (:8188)        diffusion work (images/video/upscale)
       ├─ vLLM (:8000)           storyboard LLM (real tokens, usage in response)
       └─ TTS / ACE-Step / MMAudio workers (audio; no tokens, has duration)
  → /metrics (NEW, :8189) ── scraped by Thor VictoriaMetrics (15s)
  → jobs.jsonl (NEW)          per-job detail log
```

Why meter in the pipeline, not ComfyUI or DCGM:
- The pipeline is the **only component that knows job_id + user + stage + params**.
- It runs **1 concurrent job + 5 queued** (verified: `/health` →
  `max_concurrent: 1`) → during a job's window, ~all GPU activity belongs to
  that job. Energy attribution is clean.
- ComfyUI has no native Prometheus metrics and no user/job concept.

## 2. Definitions (the money math)

Diffusion models don't have tokens. We define **synthetic tokens** from GPU
energy so everything can be summed on one scale and priced.

| Symbol | Meaning | Value |
|---|---|---|
| `E_job` | GPU energy consumed during a job (Joules) | measured (§4) |
| `E_PT` | Joules-per-token constant (calibrated, §4.3) | env `MEDIA_JOULES_PER_TOKEN`, placeholder `5.0` until calibrated |
| `T_syn` | synthetic tokens for a job | `E_job / E_PT` |
| `P_media` | $ per synthetic token | env `MEDIA_TOKEN_PRICE_USD`, default `0.00000225` (= ½ × matrix-coder output rate $4.50/M) |
| `C_job` | job cost | `T_syn × P_media` |

**Sanity target:** for the *same GPU energy*, media work should cost ≈ ½ of
what matrix-coder tokens cost. If calibration (§4.3) shows the implied ratio
is far from 0.5, adjust `P_media` (it's a knob; document the change).

**Storyboard (LLM) stage is special:** it calls vLLM directly (not via
LiteLLM), so its cost is metered here too — with **real** tokens from the
vLLM response `usage` field, priced at matrix-coder rates
(input `0.00000075`, output `0.0000045` $/token). `token_kind="real"`.
All other stages: `token_kind="synthetic"`.

**Work units** (secondary, for efficiency analysis — not for $):
- image stages: `steps × (width×height)/1e6` → `mpix_steps`
- video stages (shot/upscale): `frames × (width×height)/1e6` → `mpix_frames`
- audio stages (tts/music/sfx): output `duration` seconds → `audio_seconds`

## 3. Interfaces

### 3.1 Job POST (client → pipeline)

Add **optional** fields to every job POST (`/storyboard /images
/images/edit /shots /tts /music /sfx /upscale /assemble`):

```json
{ "user": "chuck", "client": "pi", ...existing fields... }
```

- `user`: string, low cardinality (`chuck`, `dylan`, …). If absent → `"unknown"`.
- `client`: optional string (calling app: `pi`, `webui`, `siri`, `script`).
  If absent → `"mcp"`.
- Backward compatible: old clients without these fields keep working.

(The Thor-side mcp_media change that populates these is in
`thor_media_work.md` — Matrix just needs to accept + persist them.)

### 3.2 `/metrics` (NEW endpoint on :8189, Prometheus text format)

Use `prometheus_client`. Exact names/labels (Thor dashboards will use these):

| Metric | Type | Labels |
|---|---|---|
| `media_jobs_total` | counter | `user, client, stage, status` (`status`: `done\|error\|timeout`) |
| `media_job_duration_seconds` | histogram | `user, stage` — buckets `1 5 15 30 60 120 300 600 1800 3600 7200` |
| `media_gpu_energy_joules_total` | counter | `user, stage` |
| `media_tokens_total` | counter | `user, stage, kind` (`kind`: `prompt\|completion\|cached\|synthetic`) |
| `media_cost_usd_total` | counter | `user, stage` |
| `media_work_units_total` | counter | `user, stage, kind` (`kind`: `mpix_steps\|mpix_frames\|audio_seconds`) |
| `media_queue_depth` | gauge | — |
| `media_jobs_active` | gauge | — |
| `media_up` | gauge | — (1) |

Stage values: `storyboard image image_edit shot tts music sfx upscale assemble`.
Cardinality is tiny (≤3 users × ≤4 clients × 9 stages × 3 statuses) — safe.

### 3.3 Per-job log (NEW): `jobs.jsonl`

Append one JSON line per finished job (dir: pipeline's job/data dir, e.g.
`/home/chuck/data/comfyui/run/media_jobs/metrics/jobs.jsonl` — pick the
pipeline's actual data dir):

```json
{"job_id":"<id>","user":"chuck","client":"pi","stage":"shot","model":"ltxv",
 "status":"done","started_at":"2026-07-09T12:00:00Z","finished_at":"...Z",
 "queue_wait_s":12.3,"duration_s":245.1,"energy_j":81234.5,
 "tokens":16247.0,"token_kind":"synthetic",
 "cost_usd":0.0366,"work_units":48.6,"work_unit_kind":"mpix_frames",
 "params":{"frames":97,"width":768,"height":512,"steps":null},"error":null}
```

For storyboard: `tokens` = real total (prompt+completion), `token_kind="real"`,
plus `tokens_prompt`/`tokens_completion` fields.

## 4. Metering

### 4.1 Energy source (pick the first that works; flag which you used)

1. **DCGM cumulative counter (preferred):** add `DCGM_FI_DEV_ENERGY_CONSUMPTION`
   to the dcgm-exporter config (Matrix `:9400`; currently exposes
   `DCGM_FI_DEV_POWER_USAGE` only — verified from Thor). Cumulative mJ since
   driver load. Pipeline reads `http://127.0.0.1:9400/metrics` at job start
   and end, parses the value, `E_job = (end − start)/1000` J.
2. **nvidia-smi** (if the pipeline container has GPU access):
   `nvidia-smi --query-gpu=energy.consumed --format=csv,noheader,nounits`
   (mJ) at job start/end, same delta math.
3. **Fallback — power integration:** sample `DCGM_FI_DEV_POWER_USAGE` (W,
   already exposed) at 1 Hz during the job; `E_job ≈ Σ W × 1s`. Coarser,
   but works with zero config changes.

### 4.2 Job-window metering

- On job **start** (when it leaves the queue and begins executing — not when
  it's enqueued): read energy counter → `E0`. Record `queue_wait_s`.
- On job **end** (done/error/timeout): read `E1` → `E_job = max(0, E1−E0)/1000`.
- Compute `T_syn`, `C_job`, work units from job params (§2).
- Increment the Prometheus metrics (§3.2) and append the JSONL line (§3.3).
- Failure mid-job: still record (status=`error`, partial energy).

### 4.3 Calibration of `E_PT` (do once, record the result)

1. With the energy source working, note the counter at t0.
2. Generate a known volume of matrix-coder output tokens via vLLM directly
   (e.g. a ~20–50k-token completion; avoid concurrent load).
3. `E_PT = ΔE(J) / tokens`. Store in env `MEDIA_JOULES_PER_TOKEN` (compose
   file) and note it in this file's §9 log.
4. Sanity: a 60s, ~500W job ≈ 30,000 J ≈ 6,000 synthetic tokens @ $2.25/M ≈
   $0.014 — same order as a few thousand matrix-coder output tokens. If your
   measured `E_PT` makes typical media jobs cost >3× or <⅓ of that, re-check
   the energy source before touching `P_media`.

## 5. Config

| Env | Default | Meaning |
|---|---|---|
| `MEDIA_JOULES_PER_TOKEN` | `5.0` (placeholder) | `E_PT` calibration constant |
| `MEDIA_TOKEN_PRICE_USD` | `0.00000225` | $ per synthetic token (½ × matrix-coder output rate) |
| `MEDIA_MATRIX_CODER_IN_USD` | `0.00000075` | storyboard prompt-token price |
| `MEDIA_MATRIX_CODER_OUT_USD` | `0.0000045` | storyboard completion-token price |
| `MEDIA_METRICS_ENABLED` | `true` | kill-switch for the /metrics endpoint |

## 6. Out of scope (do NOT do)

- No Langfuse, no ComfyUI extensions, no changes to ComfyUI/vLLM workers.
- No per-job_id Prometheus labels (high cardinality) — JSONL covers detail.
- No dollar conversion for LLM traffic routed via Thor's LiteLLM (already
  metered there; avoid double-counting — see §9 note).

## 7. Verification

1. `curl -s http://127.0.0.1:8189/metrics | grep media_` → all series present.
2. Run one `/tts` job (fast, no GPU diffusion): `media_jobs_total{stage="tts",status="done"}` +1,
   energy > 0, duration recorded, JSONL line written.
3. Run one `/images` job: `media_tokens_total{stage="image",kind="synthetic"}` +,
   `media_cost_usd_total{stage="image"}` +, work units = steps×MP.
4. Run one `/storyboard` job: `media_tokens_total{stage="storyboard",kind="prompt"}`
   and `kind="completion"` match vLLM's reported usage.
5. Kill a job mid-run (or force an error): status=`error` line present, partial
   energy recorded, no crash.
6. From Thor: `curl -s 'http://192.168.4.54:9090/api/v1/query?query=media_jobs_total'`
   (after the Thor-side scrape job is added — coordinate via thor_media_work.md).
7. Two consecutive jobs: energy deltas are independent (no bleed), queue_wait
   recorded on the second.

## 8. Verify these assumptions — FLAG if any is false

1. Pipeline is a single long-running service (FastAPI or similar) with a job
   queue, 1 concurrent + 5 queued, jobs persisted under
   `/home/chuck/data/comfyui/run/media_jobs/`.
2. Job objects already carry stage + params (steps/width/height/frames/fps/
   duration) and a job_id; a completion hook exists (or is trivially addable)
   where metrics/JSONL can be written.
3. The storyboard path calls vLLM's OpenAI-compatible API and the response
   includes `usage` (prompt/completion tokens).
4. The pipeline container can reach `127.0.0.1:9400` (dcgm-exporter) — or
   has `nvidia-smi` with GPU visibility (§4.1 options 1–2).
5. One pipeline job = one logical unit of work; even if it triggers several
   ComfyUI executions (e.g. multi-pass upscale), the job window still covers
   all of them.
6. Nothing else on Matrix writes to the JSONL path; the pipeline owns it.
7. `prometheus_client` is available (or can be added to the container image
   without breaking the build).

## 9. Implementation log (fill in as you go)

- [ ] Energy source used: ______ (DCGM counter / nvidia-smi / power integration)
- [ ] `E_PT` calibrated: ______ J/token (date: ______)
- [ ] `P_media` adjusted from default? ______ (reason: ______)
- [ ] Assumptions flagged: ______
- [ ] Metric endpoint live + verified: ______