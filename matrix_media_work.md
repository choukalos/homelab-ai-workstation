# Matrix — Media Work Metering (handoff spec, v2.1)

**Status: IMPLEMENTED + CALIBRATED (2026-09-06).** §4–§7 done and verified
live; §5 rates calibrated and in `.env`; §9 has the full log. Remaining open
item: re-sync `thor.litellm.config.yml` from live thor + commit (blocked —
no SSH route matrix→thor; do on thor). Thor-side phases (LiteLLM vLLM scrape,
mcp_media identity, matrix scrape, Grafana) proceed per the Thor spec.

**Audience:** the agent working on Matrix. This file is self-contained: read it,
check §8 (assumptions — most already verified 2026-09-05), implement §4–§6, run
§7, and **flag any §8 assumption that doesn't hold** rather than improvising a
different design.

Goal: attribute GPU work from the media pipeline (ComfyUI diffusion models:
Qwen-Image images, LTXV video, SeedVR2 upscale, ACE-Step music, MMAudio SFX,
XTTS TTS) to the **user** who kicked off each job, expressed as **work units +
approximate $**, so Thor's Grafana can show total work $ = LLM + media, by user.

No Langfuse. No ComfyUI changes. All work happens in the media-pipeline service
(docker container on Matrix, `:8189`). No DCGM changes.

---

## v1 → v2 changes (read this first)

v1 priced media work as *synthetic tokens derived from GPU energy* (DCGM
counter / nvidia-smi energy / power integration). Review on 2026-09-05 found
that **every v1 energy source is broken on this box** (see §8.4 + Appendix A):
the GPU (RTX PRO 5000 72GB Blackwell, driver 595.71.05) does not support the
NVML energy counter (`nvidia-smi energy.consumed` → "not a valid field"), and
DCGM field polling is frozen (power + total-energy counters serve stale values;
a *fresh* DCGM session freezes too — not a stale-container problem).

v2 therefore prices media work from **deterministic work units** (steps ×
megapixels, frames × megapixels, audio seconds) with a small per-kind rate
table, calibrated once against real electricity + GPU amortization. Same
architecture (pipeline-side metering, `user`/`client` on job POSTs, `/metrics`
+ `jobs.jsonl`, Thor scrape → Grafana), same metric names where they survive,
no new metering infrastructure. Energy metering is demoted to an optional
stretch goal (Appendix A).

v2 also fixes v1's pricing defaults. **v2.1 (2026-09-05, Thor confirmed):**
the **live** thor LiteLLM config is ground truth — **$0.75/M input / $4.50/M
output** for `matrix-coder`. The repo + matrix copies of
`thor.litellm.config.yml` (repo commit Aug 26, local copy Aug 23 — identical,
both $1/M) are **stale** — re-sync from thor and commit the live version. So:
`MEDIA_MATRIX_CODER_IN_USD=0.00000075`, `MEDIA_MATRIX_CODER_OUT_USD=0.0000045`.
Design rule: the storyboard rate mirrors the live LiteLLM `matrix-coder`
rate, so LLM $ (Thor) and media $ (Matrix) are on the same nominal scale by
construction. (The work-unit rate table + calibration assumptions are recorded
on the Thor side in METRICS.md.)

---

## 1. Architecture

```
Siri/Shortcuts ──► mcp_media (on Thor) ──► media-pipeline :8189 (Matrix)
                                       │      records user + client + stage
                                       │      + work units + $ per job
                                       ▼
                              /metrics (Prometheus) + jobs.jsonl
                                       │
                       Thor scrape (15s) ──► VictoriaMetrics ──► Grafana
                                       │
        total work $ by user = LLM $ (LiteLLM prometheus callback, Thor)
                              + media $ (this system, Matrix)
```

- The media-pipeline service is the only component that knows user + job +
  stage + params. It does all metering.
- `mcp_media` (Thor) forwards the caller's identity as `user` + `client` on
  each job POST (see §3.1). The pipeline does not need the LiteLLM API key.
- Storyboard's vLLM tokens are real tokens, read from the vLLM API response
  `usage` field, priced at the real `matrix-coder` nominal rate.
- Diffusion stages have no LLM tokens; they are priced from work units.

---

## 2. Definitions

### 2.1 Work units (per stage)

| Stage | Work unit | kind label | Source of values |
|---|---|---|---|
| `storyboard` | — (real LLM tokens) | — | vLLM `usage` (prompt/completion) |
| `images` | `steps × (w×h)/1e6` | `mpix_steps` | job payload (`steps`, `width`, `height`) |
| `images/edit` | `steps × (out_w×out_h)/1e6` | `mpix_steps` | payload `steps`; **output image dims** (payload has no w/h — read the output PNG) |
| `shots` | `frames × (w×h)/1e6` | `mpix_frames` | job payload (`frames`, `width`, `height`) |
| `upscale` | `frames_out × (out_w×out_h)/1e6` | `mpix_frames` | **output video** via `ffprobe` (duration_s × payload `fps`) × output MP (payload `resolution` scale) |
| `tts` | output audio seconds | `audio_seconds` | **output wav** via `ffprobe` (payload has no duration) |
| `music` | output audio seconds | `audio_seconds` | **output wav** via `ffprobe` (requested `duration` may differ from actual) |
| `sfx` | output audio seconds | `audio_seconds` | **output wav** via `ffprobe` |
| `assemble` | 0 (CPU/ffmpeg only) — **unless** `upscale_each=true`: then `Σ per-shot frames×MP` | `mpix_frames` | per upscaled shot, `ffprobe` (assemble's SeedVR2 work is the GPU work) |

`ffprobe` is available in the `comfyui_backend` container (verified 2026-09-05,
ffprobe 6.1.1); the pipeline already `docker exec`s into it for ffmpeg.

### 2.2 Pricing

| Quantity | Env var | Default | Notes |
|---|---|---|---|
| $ / mpix_step | `MEDIA_PRICE_MPPIX_STEP_USD` | `0.000053` | **calibrated 2026-09-06** (§5/§9) |
| $ / mpix_frame | `MEDIA_PRICE_MPPIX_FRAME_USD` | `0.0000058` | **calibrated 2026-09-06** (§5/§9) |
| $ / audio second | `MEDIA_PRICE_AUDIO_SEC_USD` | `0.000031` | **calibrated 2026-09-06** (§5/§9) |
| $ / matrix-coder input token | `MEDIA_MATRIX_CODER_IN_USD` | `0.00000075` | live thor LiteLLM rate (Thor-confirmed 2026-09-05; repo/matrix copies are stale — re-sync) |
| $ / matrix-coder output token | `MEDIA_MATRIX_CODER_OUT_USD` | `0.0000045` | same |
| electricity $/kWh | `MEDIA_ELEC_COST_USD_PER_KWH` | `0.15` | calibration only (§5) |
| GPU purchase cost | `MEDIA_GPU_COST_USD` | `4000` | calibration only (§5) |
| GPU lifetime hours | `MEDIA_GPU_LIFETIME_HOURS` | `43800` | = 5 years; calibration only |

Rates are **full-cost rates** (electricity + hardware amortization), so
"total work $" is a true cost proxy for ROI tracking. The three `MEDIA_PRICE_*`
values are the 2026-09-06 calibration results (see §5/§9); re-run §5 if the
hardware, power cap, or GPU price assumptions change.

Cost per job:

```
storyboard:  cost = prompt_tokens × IN + completion_tokens × OUT
other GPU:   cost = work_units × MEDIA_PRICE_<kind>
assemble (no upscale): cost = 0
```

Sanity targets at the calibrated rates (verified live in §7): a 4-step
1280×720 image (≈3.7 mpix_steps) ≈ **$0.0002**; a 97-frame 768×512 shot
(≈38 mpix_frames) ≈ **$0.0002**; 30 s of music ≈ **$0.0009**; a storyboard
with 208 prompt + 761 completion tokens ≈ **$0.0036**. Amortization dominates
(≈$0.0913/h GPU); electricity is negligible at these job sizes. If a stage
lands >10× off its target, the rate (not the math) is wrong.

---

## 3. Interfaces

### 3.1 Job POSTs accept `user` and `client`

Every job endpoint accepts optional `user` (string, e.g. `"chuck"`, `"siri"`,
`"test"`) and `client` (string, e.g. `"mcp_media"`, `"siri"`, `"manual"`).
The pipeline stores them on the job record and on every metering output.
If absent: `user="unknown"`, `client="unknown"`.

**Endpoint shape matters (verified 2026-09-05):**

| Endpoint | Body type | `user`/`client` as |
|---|---|---|
| `POST /storyboard` | JSON | JSON fields |
| `POST /images` | JSON | JSON fields |
| `POST /images/edit` | multipart form | **Form fields** |
| `POST /shots` | multipart form | **Form fields** |
| `POST /tts` | JSON | JSON fields |
| `POST /music` | JSON | JSON fields |
| `POST /sfx` | multipart form | **Form fields** |
| `POST /upscale` | multipart form (file + form) | **Form fields** |
| `POST /assemble` | JSON | JSON fields |

### 3.2 `GET /metrics` (Prometheus text format)

New endpoint on the existing service (`:8189`). All job metrics carry
`user` (the metering point of this whole system) and `stage`.

| Metric | Type | Labels | Meaning |
|---|---|---|---|
| `media_jobs_total` | counter | `user,client,stage,status` | jobs finished; `status ∈ {done, error, timeout}` |
| `media_job_duration_seconds` | histogram | `user,stage` | queue-pop → completion (execution, not queue wait) |
| `media_tokens_total` | counter | `user,stage,kind` | **real LLM tokens, storyboard only**; `kind ∈ {prompt, completion}` |
| `media_cost_usd_total` | counter | `user,stage` | $ attributed to the job (§2.2) |
| `media_work_units_total` | counter | `user,stage,kind` | `kind ∈ {mpix_steps, mpix_frames, audio_seconds}` |
| `media_queue_depth` | gauge | — | current queue length |
| `media_jobs_active` | gauge | — | currently running (1) |
| `media_up` | gauge | — | 1 when the service is up |

Cardinality is bounded: users are a handful, stages are 9, status 3, kind ≤ 3.
No `job_id` label (unbounded). Histogram buckets:
`1 5 15 30 60 120 300 600 1800 3600 7200`.

v1's `media_gpu_energy_joules_total` is **removed** (energy demoted to
Appendix A).

### 3.3 `jobs.jsonl` (durable per-job record)

One JSON object per completed job, appended to
`/home/chuck/data/comfyui/run/media_jobs/metrics/jobs.jsonl` (new path; pick
the pipeline's actual data dir if it differs). Written after the job finishes
(or fails) — failures are recorded too. ~1 line per job; no rotation needed at
current volume (~300 jobs/week), but cap/truncate the file at 10 MB (keep the
newest) to bound growth.

```json
{"ts":"2026-09-05T18:40:12Z","job_id":"20260905-184001-abc123","flow":"images",
 "user":"chuck","client":"mcp_media","status":"done",
 "queue_wait_s":1.2,"duration_s":14.3,
 "work_units":3.69,"work_unit_kind":"mpix_steps",
 "tokens":null,"tokens_prompt":null,"tokens_completion":null,
 "cost_usd":0.00369,
 "model":"qwen-image-2512",
 "params":{"steps":4,"width":1280,"height":720,"seed":42}}
```

Field rules:
- `tokens*` are non-null **only** for storyboard (real vLLM usage).
- `work_units`/`work_unit_kind` are non-null only for GPU diffusion stages
  (null for storyboard; 0/null for plain assemble).
- `cost_usd` per §2.2 (0 for plain assemble).
- `model` is derived from the stage (no model field exists on job records):
  `storyboard→qwen38-27b`, `images→qwen-image-2512`,
  `images_edit→qwen-image-edit-2511`, `shots→ltxv-2b-0.9.6-distilled`,
  `upscale→seedvr2-3b|4xultrasharp` (by pipeline), `tts→xtts-v2`,
  `music→ace-step-1.5`, `sfx→mmaudio-large-44k-v2`, `assemble→ffmpeg`.
- `params` = the job's original payload minus uploaded-file bytes.

---

## 4. Implementation (media-pipeline service)

Where things go, in `media-pipeline/` (FastAPI, `server.py`):

1. **Job intake** — every job-creating route captures `user`/`client`
   (§3.1: JSON fields or Form fields per the table) into the job record.
   `new_job()` already stores the full payload; add the two fields.
2. **Completion hook** — the single choke point is `_worker_loop`:
   `set_status(jid, "done"/"error")` + the `finally` block, after
   `FLOW_MAP[flow](job_id, payload)` returns. This is where metering happens.
   Job records already carry `created`/`started`/`finished` timestamps →
   `queue_wait_s = started − created`, `duration_s = finished − started`.
3. **Work-unit extraction** — per §2.1. For stages that need output
   measurement (`images/edit`, `upscale`, `tts`, `music`, `sfx`, `assemble`
   with `upscale_each`), run `ffprobe` (or read PNG/JPEG headers) on the
   output artifact in the job dir. Keep it best-effort: if measurement fails,
   record `work_units: null` with a `metering_error` field, still record the
   job, still increment `media_jobs_total`.
4. **Storyboard tokens** — `flow_storyboard` currently reads only
   `choices[0].message.content` from the vLLM response. Also capture
   `usage.prompt_tokens` / `usage.completion_tokens` (verified present in the
   live response; `usage.prompt_tokens_details` is `null` on vLLM 0.24.0, so
   no cached-token split — do not attempt it).
5. **Status mapping** — the service has only `done`/`error` today. Map
   subprocess `TimeoutExpired` → `status="timeout"` (all other exceptions →
   `error`). There is no cancel endpoint; do not invent one.
6. **Cost + metric emission** — compute `cost_usd` (§2.2), increment
   `media_jobs_total`, `media_job_duration_seconds`, `media_tokens_total`
   (storyboard), `media_work_units_total`, `media_cost_usd_total`; append the
   `jobs.jsonl` line. All of it inside the completion hook, after status is
   final, so a metering bug can never break job completion (wrap in
   try/except, log on failure).
7. **Gauges** — `media_queue_depth`, `media_jobs_active`, `media_up` from the
   existing queue state.
8. **Dependency** — add `prometheus_client` to `requirements.txt` (not
   currently installed; pure Python, trivial). Rebuild the image:
   `model-manager rebuild media-pipeline` (recreates the container).
9. **Config** — the `MEDIA_*` envs from §2.2 go in the `media-pipeline`
   service's `environment:` block in `compose/comfyui.yml` (profile `image`)
   **or** in `/home/chuck/homelab/.env` (mounted ro; `server.py` already
   `load_dotenv()`s it — that's where `MAX_CONCURRENT_JOBS`/`MAX_QUEUE_DEPTH`
   live). Either is fine; pick one and note it in §9. `MEDIA_METRICS_ENABLED`
   (default `true`) is the kill switch: when `false`, no `/metrics`, no
   JSONL, zero behavior change.

---

## 5. Calibration (one-time, ~1 h) — **DONE 2026-09-06, results in §9**

Purpose: set the three `MEDIA_PRICE_*` rates so per-job $ ≈ real cost
(electricity + amortization). Run one reference job per work-unit kind:

1. Pick a reference job per kind (record its params): e.g. `/images`
   4-step 1280×720; `/shots` 97-frame 768×512; `/music` 30 s.
2. Run it. While it runs, sample GPU power at 1 Hz:
   `docker exec comfyui_backend nvidia-smi --query-gpu=power.draw
   --format=csv,noheader,nounits` (verified live and accurate on this box —
   the *only* working power source; see Appendix A).
3. Compute:
   - `electricity_$ = avg_W × duration_s / 3.6e6 × MEDIA_ELEC_COST_USD_PER_KWH`
   - `amortization_$ = (MEDIA_GPU_COST_USD / MEDIA_GPU_LIFETIME_HOURS) × duration_s / 3600`
   - `rate = (electricity_$ + amortization_$) / work_units`
4. Set the `MEDIA_PRICE_*` envs to the rounded rates; record the reference
   jobs, measured powers, and resulting rates in §9.
5. Sanity-check against §2.2 targets. At a 300 W cap and $0.15/kWh, a 1-min
   full-load job costs ≈ $0.00075 electricity + ≈ $0.0026 amortization (at
   $4k/5 yr) — so per-job $ in the $0.001–$0.10 range is correct, and a
   rate that produces $1+ per image is wrong.

Note: vLLM is resident on the GPU (~80–110 W idle, spikes to the 300 W cap on
requests). Subtract the pre-job idle baseline from the sampled power before
integrating, or the short-job rates will be biased high.

---

## 6. Out of scope (explicit)

- **Langfuse** — not used, not being set up. (v1's optional "phase 2" is
  dropped, not deferred.)
- **ComfyUI changes** — none. No custom nodes, no workflow template edits.
  (Prompt-token counting in ComfyUI is the wrong metric for this goal: it
  measures prompt text length, not GPU work; it can't meter prompt-less stages
  — upscale/SFX/TTS — and provides no user/$/per-job story.)
- **Energy-based synthetic tokens** — demoted to Appendix A (stretch).
- **Job-level labels on LLM metrics** — LLM $ stays at model granularity on
  Thor (LiteLLM prometheus callback); job-level media $ comes from here.
- **Double-counting** — storyboard is metered HERE (real tokens from the vLLM
  response). It does not go through Thor's LiteLLM, so LLM $ (Thor) + media $
  (Matrix) = total, no overlap.

---

## 7. Verification

1. **Unit:** `prometheus_client` installed; `GET /metrics` returns the §3.2
   families; `GET /health` unchanged.
2. **Storyboard:** `POST /storyboard {"prompt":"test","user":"test",
   "client":"manual"}` → `media_tokens_total{user="test",stage="storyboard"}`
   increments by the response's real `usage` counts; `media_cost_usd_total`
   increments by `pt×IN + ct×OUT`.
3. **Image:** `POST /images {"prompt":"a red cube","width":512,"height":512,
   "steps":2,"user":"test","client":"manual"}` → `media_work_units_total{
   stage="images",kind="mpix_steps"}` ≈ 2×0.26 = 0.52; cost ≈ 0.00052 at
   defaults.
4. **Audio:** `POST /tts {"text":"hello world","user":"test","client":"manual"}`
   → `media_work_units_total{stage="tts",kind="audio_seconds"}` ≈ the output
   wav duration (ffprobe).
5. **Error path:** `POST /upscale` (multipart: file + form field
   `pipeline=bogus`, plus `user=test`, `client=manual`) →
   `media_jobs_total{status="error"}` increments and a JSONL line exists with
   `status:"error"`. (No cancel endpoint exists — do not test "kill mid-run".)
6. **JSONL:** lines appear for every job above; fields per §3.3.
7. **Kill switch:** `MEDIA_METRICS_ENABLED=false` + rebuild → no `/metrics`,
   no JSONL writes, jobs behave identically.
8. **Grafana (Thor side):** `sum by (user) (increase(media_cost_usd_total[7d]))`
   and the LLM+media total query return sensible numbers.
9. **Sanity:** per-job $ within §2.2 targets (within 10×).

---

## 8. Assumptions (verified 2026-09-05 unless noted)

Verify before implementing; flag (don't redesign) anything false.

1. **TRUE.** The pipeline is a single FastAPI service, one worker thread
   (`MAX_CONCURRENT_JOBS=1`, `MAX_QUEUE_DEPTH=5`, live `/health`:
   `max_concurrent=1, max_queue_depth=5, max_pending=6, jobs=288`). Artifacts
   under `/home/chuck/data/comfyui/run/media_jobs/`. Job *state* is in-memory
   (`JOBS` dict, resets on container restart) — the JSONL is the durable
   record.
2. **TRUE.** Job objects carry `flow` + full `payload` (steps/width/height/
   frames/fps/duration per stage) + `job_id`; the completion choke point is
   `_worker_loop`'s `try/except/finally` around `FLOW_MAP[flow](...)`.
   Nuances handled in §4: `tts` has no duration param, `images/edit` has no
   w/h, `music`/`sfx` output duration can differ from the request → measure
   outputs (§2.1).
3. **TRUE.** Storyboard calls vLLM `:8000/v1/chat/completions` (model
   `qwen38-27b`) and the response includes `usage`
   (`prompt_tokens`/`completion_tokens`/`total_tokens`) — verified with live
   calls. `usage.prompt_tokens_details` is `null` (vLLM 0.24.0) → no cached
   split. Current code reads only `choices[0].message.content` → capturing
   `usage` is a code change.
4. **FALSE in v1, resolved in v2 (no dependency).** v1 assumed a working
   GPU energy counter. Reality: `nvidia-smi energy.consumed` → "not a valid
   field" (NVML energy counter unsupported on this GPU/driver); DCGM
   `POWER_USAGE` and `TOTAL_ENERGY_CONSUMPTION` are **frozen** (identical
   values across repeated scrapes while the GPU drew 17–308 W; a *fresh*
   dcgm-exporter session freezes too). v2 does not depend on any energy
   source. The only working power source is 1 Hz `nvidia-smi power.draw` via
   `docker exec comfyui_backend` (verified live: 246.48 → 96.09 W in 2 s) —
   used only for §5 calibration and Appendix A.
5. **TRUE (at current config).** One job = one unit of work: all flows run
   synchronously in the worker thread; `flow_assemble` with `upscale_each`
   does N SeedVR2 runs + ffmpeg inside the job window. Caveat: only true at
   `MAX_CONCURRENT_JOBS=1` (with >1, `GPU_LOCK` makes job windows overlap).
6. **TRUE.** `media_jobs/metrics/jobs.jsonl` is a new path; nothing else
   writes there. Pipeline container runs as root → can write the 1024-owned
   run dir.
7. **TRUE.** `prometheus_client` is not in `requirements.txt` today; trivial
   to add (pure Python, `python:3.12-slim` base). Requires
   `model-manager rebuild media-pipeline`.
8. **TRUE.** `mcp_media` (Thor) can add `user`/`client` to job POSTs; the
   pipeline does not need the LiteLLM API key (v1's "forward the Authorization
   header" is unnecessary — identity travels in the body/form).
9. **TRUE.** `ffprobe` 6.1.1 is available in `comfyui_backend` (verified);
   the pipeline already `docker exec`s into it for ffmpeg.
10. **TRUE.** Thor's query API `http://192.168.4.54:9090/api/v1/query` is
    live (verified from Matrix). `:8428` is closed — Thor's docs should be
    consistent about which system (Prometheus vs VictoriaMetrics) is in use.
11. **TRUE (pricing resolved 2026-09-05).** Thor's LiteLLM has the
    `prometheus` callback enabled; the **live** `matrix-coder` rate is
    **$0.75/M in / $4.50/M out** (Thor-confirmed). The repo/matrix config
    copies ($1/M both, Aug 23/26) are stale — re-sync + commit the live
    version. §2.2 defaults match the live rate.

---

## 9. Implementation log (fill in as you go)

**Matrix implementation — 2026-09-06 (this session):**

- [x] `prometheus_client` added to `requirements.txt`; image rebuilt via
      `model-manager rebuild media-pipeline` (script at
      `scripts/model-manager`, not on PATH)
- [x] `user`/`client` captured on all 9 job routes (5 JSON + **4** Form
      endpoints — `/upscale` is also multipart, spec §3.1 table corrected)
- [x] Completion hook emits metrics + JSONL (`metering.record_job` in the
      worker's `finally`, wrapped so metering can't break jobs);
      `subprocess.TimeoutExpired` + `TimeoutError` → `status="timeout"`
- [x] Work-unit extraction per §2.1 (incl. ffprobe/output-dim measurement —
      new module `media-pipeline/metering.py`; ffprobe via
      `docker exec comfyui_backend`)
- [x] Storyboard `usage` captured; priced at $0.75/M in / $4.50/M out (live
      rate) — verified: 208 prompt + 761 completion → $0.0035805
- [x] `MEDIA_MATRIX_CODER_*_USD` = 0.00000075 / 0.0000045 set in config
- [ ] `thor.litellm.config.yml` re-synced from thor (live) + committed to the
      repo (current repo/matrix copies are stale — $1/M) — **blocked: no SSH
      route matrix→thor; Chuck to do on thor**
- [x] Env config location: `/home/chuck/homelab/.env` (loaded by
      `load_dotenv()` in `server.py`; `import metering` placed AFTER the
      `load_dotenv()` call so the `MEDIA_*` vars are visible; `.env` edits
      take effect on `docker restart`, code changes need a rebuild)
- [x] `MEDIA_METRICS_ENABLED=false` path verified: `/metrics` → 404, no JSONL
      line, job completes normally; restored to `true`
- [x] §7 verification run (2026-09-06, all passed):
  - `/health` ok; `/metrics` Prometheus text (gauges `media_queue_depth`,
    `media_jobs_active`, `media_up=1`; counters appear after first job)
  - storyboard (user=chuck, client=pi-test): `media_tokens_total` 208/761,
    cost $0.0035805, JSONL line with `model=qwen38-27b`
  - images 4×1280×720: 3.6864 `mpix_steps`, cost $0.0036864
  - images/edit (Form fields): output measured 1392×752 → 8.3743 `mpix_steps`
    (edit model outputs its own resolution — output-based metering is correct)
  - tts: 4.6803 `audio_seconds` (ffprobe of vo.wav), cost $0.00234
  - error path (upscale `pipeline=bogus` via Form): `status=error`, JSONL line,
    `cost_usd=0.0`, `work_units=null`
  - JSONL: one line per job, all §3.3 fields, `params` excludes user/client
- [x] §5 calibration run (2026-09-06; 1 Hz `nvidia-smi power.draw` via
      `docker exec comfyui_backend`, baseline-subtracted; idle baseline
      ≈ 76–96 W with vLLM resident). Reference jobs, measured rates, `.env`
      updated + `docker restart` + live cost re-verified:

  | unit | reference job | dur | peak | energy above base | measured rate | rate set |
  |---|---|---|---|---|---|---|
  | `mpix_steps` | images 4×1280×720 (3.6864 u) | 6 s | 300 W (cap) | 952 J | $0.00005285/u | **0.000053** |
  | `mpix_frames` | shots 97f 768×512 (38.142 u) | 8 s | 249 W | 395 J | $0.00000580/u | **0.0000058** |
  | `audio_seconds` | music 30 s (30 u) | 36 s | 157 W | 409 J | $0.00003069/s | **0.000031** |

      Notes: (a) amortization dominates (≈$0.0913/h GPU); electricity is
      negligible at these job sizes. (b) First images attempt was contaminated
      (a concurrent job from another session lifted the "idle" baseline to
      282 W) — re-ran after a clean-idle gate (10 consecutive <120 W samples).
      (c) LTXV-distilled is very fast on this card (97 frames in 8 s), so the
      shots rate is low. (d) Post-calibration sanity: image ≈ $0.0002,
      shot ≈ $0.0002, 30 s music ≈ $0.0009, storyboard (208+761 tok) ≈
      $0.0036 — LLM cost now dominates media cost, as intended.
      (e) `upscale`/`sfx` reuse the `mpix_frames`/`audio_seconds` rates
      (no separate reference jobs).
      (f) Rate change verified live: image job cost $0.000195 = 3.6864 ×
      0.000053 (JSONL + `media_cost_usd_total`).
- [x] `jobs.jsonl` 10 MB cap behavior verified (offline logic test: rotation
      keeps newest, file bounded; live file is small so no live rotation yet)

---

## Appendix A — Energy metering (stretch goal, NOT part of v2)

If real consumption tracking is wanted later:

- **Only working source:** 1 Hz `nvidia-smi power.draw` via
  `docker exec comfyui_backend` (the pipeline container has no GPU devices
  and no `nvidia-smi`; `comfyui_backend` does). Integrate over the job window
  (rectangular rule at 1 Hz is fine).
- **Baseline:** subtract pre-job idle power (~80–110 W with vLLM resident)
  per sample: `max(0, p − p_base)`.
- **DCGM is currently broken** on this driver/GPU (595.71.05 + DCGM 4.x
  `latest` image): power and total-energy counters freeze after the first
  sample, in both the production exporter and fresh sessions. Do not build on
  DCGM until a version pin or driver update is verified to fix it.
- **Ops note:** the production dcgm-exporter freeze means Thor's existing
  GPU power/util dashboards are showing stale values right now. Restart does
  not help (fresh sessions freeze too). **In pursuit (Thor-confirmed
  2026-09-05):** pin an older `dcgm-exporter` image (DCGM 3.x) + restart.
  Verification after: `DCGM_FI_DEV_POWER_USAGE{instance="matrix"}` must
  change between idle and a load test, and `DCGM_FI_DEV_GPU_UTIL` must track
  it. If the pin doesn't unfreeze it, report back — the `nvidia-smi`
  fallback via `comfyui_backend` (above) is the working power source.

## Appendix B — Review findings (2026-09-05, v1)

- DCGM counters frozen (production + fresh session); `nvidia-smi
  energy.consumed` unsupported; `nvidia-smi power.draw` live (→ v2 §2/§5).
- v1 pricing: **resolved 2026-09-05** — live thor LiteLLM rate is $0.75/M in
  / $4.50/M out (Thor-confirmed); the repo/matrix config copies ($1/M both,
  Aug 23/26) are stale. `MEDIA_MATRIX_CODER_*_USD` set accordingly (→ §2.2).
- v1 §4.3 sanity example "60 s at ~500 W" impossible: GPU is capped at 300 W
  (measured max ~308 W) (→ v2 §5 note).
- v1 §3.2 `kind="cached"` unreachable (vLLM `prompt_tokens_details=null`)
  (→ removed).
- v1 §3.1 JSON example didn't apply to the 3 multipart endpoints (→ v2 §3.1
  table).
- v1 §7.5 "kill a job mid-run" not possible (no cancel endpoint) (→ v2 §7.5).
- GPU: single NVIDIA RTX PRO 5000 72GB Blackwell, driver 595.71.05, 300 W cap.
- Pipeline: host networking, docker.sock rw, `compose/comfyui.yml` profile
  `image`, `.env` mounted ro and `load_dotenv()`ed; only LLM call site is
  `flow_storyboard` → vLLM `:8000`.