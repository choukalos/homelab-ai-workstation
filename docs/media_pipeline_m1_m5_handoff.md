# media-pipeline handoff: M1–M5 (assemble duration hold, job kill, queue status, status accuracy, progress)

> **STATUS: COMPLETE (2026-11-21).** All five items implemented, acceptance tests
> PASS (`media-pipeline/qa_m1_m5.py`), contract updated
> (`docs/matrix_media_pipeline_api.md`), image rebuilt + restarted.
> **Bug found during acceptance:** the 2026-11-19 image deadlocked the whole
> service on the first real queued-job kill — `_kill_job` called `release_slot()`
> *inside* `with _qcond:`, and `_qcond` wraps the non-reentrant `_qlock` (self-
> deadlock; every `/health`, worker pop, and submission hung). The working tree
> already carried the fix (release moved outside the block); the rebuild that
> shipped M1–M5 deployed it. `faulthandler.register(SIGUSR1, all_threads=True)`
> was added to the server as the hang diagnostic (no ptrace in the container).
>
> **Work host: MATRIX (192.168.4.55).** The pipeline server source lives on matrix
> (under `/home/chuck/data/comfyui/` — verify exact path first). This is a
> self-contained spec: implement M1–M5 in the pipeline server, run the acceptance
> tests, and report back. Thor-side follow-ups (contract docs, MCP tools) happen
> after the changes land and are NOT part of this handoff.
>
> **Origin:** `proposals/2026-11-19-tier2-matrix-media-pipeline-improvements.md`
> (homelab repo) + the 2026-11-17 SW Holiday Spoof production evidence (C14 root
> cause, C13 progress, A4 queue visibility) + `improve_mediapipeline_todo.md`
> (2026-11-02 peanut run).
>
> **Binding context:** the pipeline is unauthenticated by design (LAN trust; public
> auth is the Caddy layer on thor). All changes are **additive** — no existing
> field is removed or renamed; existing clients (thor `mcp_media`, the Mac's
> codemode scripts) must keep working unchanged.

---

## M1 (P1 — the C14 root cause): `shots[].duration` must frame-hold / trim

### Current behavior (verified live 2026-11-19, baseline test)

`POST /assemble` accepts `shots: [{path, in?, out?, duration?}]` but **silently
ignores `duration`** — it concatenates each clip at its natural length.

Baseline evidence (job `bab0cbbed536`, client tag `m1-baseline-test`,
2026-11-19 ~12:50Z):

- inputs: `ead65d5be33b/norm_00.mp4` (6.0 s) + `ead65d5be33b/norm_01.mp4` (6.0 s)
- payload: both shots requested `"duration": 9`
- result: `final.mp4` = **12.0 s** (natural concat), not 18.0 s
- job result carries **no warning, no adjustment record** — silent acceptance

This silent acceptance caused the 2026-11-17 film defect: a 36-shot plan of 226 s
produced a 181.5 s cut (44.5 s short, missing closing VO + end card) after a 4.5 h
upscale render. The production fix-forward used `ffmpeg tpad=stop_mode=clone`
per-clip to hold to planned durations — that exact technique is what M1 should
make first-class.

### Required behavior

For each **object** shot (`{path, ...}`) with a `duration` field:

1. Probe the clip's natural length (the assembler already probes for `in`/`out`).
2. Order of operations: `in`/`out` apply first (trim window within the source),
   then `duration` is applied to the **resulting** clip.
3. With tolerance `T = 0.1 s` (|natural − duration| ≤ T → no-op):
   - `duration > natural` → **frame-hold** the last frame for `duration − natural`
     seconds (`tpad=stop_mode=clone:stop_duration=X` semantics — the
     production-proven fix-forward technique).
   - `duration < natural` → **trim** the clip to `duration` (cut at the end).
   - Neither → pass through unchanged.
4. Plain-string shots (no object form) are unchanged: natural length, no probe.
5. `upscale_each: true`: apply the duration adjustment **before** upscaling
   (upscale the held/trimmed clip in one pass — do not upscale then re-hold).
6. **Report every adjustment** in the job result AND the ledger row:
   ```json
   "duration_adjustments": [
     {"shot": 0, "natural_s": 6.0, "requested_s": 9.0, "applied": "hold", "held_s": 3.0},
     {"shot": 1, "natural_s": 6.0, "requested_s": 9.0, "applied": "hold", "held_s": 3.0}
   ]
   ```
   `applied` ∈ `hold | trim | none`; `held_s` / `trimmed_s` as applicable.
   Omit the field entirely when no shot requested a duration.

No loud failure is needed (hold/trim makes the request meaningful), but the
`duration_adjustments` array is the visibility mechanism — a client that
mis-estimates clip lengths now sees exactly what happened instead of a silent
short cut.

### Backward-compat note

Any client passing `duration` today is already getting a silently wrong result
(natural length), so implementing the field's documented meaning is a fix, not a
breaking change. Say so in the change notes.

### Acceptance (run these after the change; the first is the live baseline above)

1. **Hold:** re-run the baseline — 2 shots, 6.0 s clips, `duration: 9` each →
   `final.mp4` = 18.0 s ± 0.15 s; `duration_adjustments` present, both
   `applied: "hold"`, `held_s: 3.0`.
2. **Trim:** 2 shots, 6.0 s clips, `duration: 4` each → 8.0 s ± 0.15 s; both
   `applied: "trim"`.
3. **No-op:** 6.0 s clip, `duration: 6.05` → `applied: "none"`, output ≈ 6.0 s.
4. **Regression:** plain assemble (string shots, no `duration`) behaves exactly
   as before (natural-length concat).

---

## M2 (P2 — A4): job kill/abort — `DELETE /jobs/{id}`

Superseded/orphaned jobs currently sit in the global FIFO and burn slots (two
orphaned keyframe jobs held 2 of 6 queue slots — ~33% capacity — in the film run).

### Required behavior

- `DELETE /jobs/{id}` (also accept `POST /jobs/{id}/kill` as an alias if DELETE
  is awkward in the framework — but DELETE is the primary contract).
- **Only `queued` jobs can be killed.** Killing must remove the job from the FIFO
  so the slot is actually freed (the next queued job starts).
- `running` → **409** `{"error": "job is running (GPU work in progress); kill is
  queued-only", "status": "running"}`. **Never kill GPU work.**
- Already `killed` → **200** `{"job_id", "killed": false, "status": "killed"}`
  (idempotent no-op, not an error).
- Terminal `done` / `error` / `timeout` → **200** `{"job_id", "killed": false,
  "status": "<status>"}` (nothing to kill).
- Unknown id → **404** with a hint to `GET /jobs`.
- **Client scoping (nominal — the pipeline is unauthenticated by design):**
  optional `?client=<tag>` query param. If the job has a `client` tag AND the
  param is present AND they don't match → **403**. Param omitted, or job has no
  tag → allowed. (The thor MCP wrapper — tier3 — will pass the caller's tag.)
- Killed jobs get `status: "killed"` and a **ledger row** (jobs.jsonl) so
  `GET /jobs?status=killed` finds them after a restart.
- Success response: `{"job_id", "killed": true, "status": "killed",
  "queue_position_freed": <int>}`.

### Acceptance

1. Fill the queue (submit a slow job + a second job that queues behind it).
   `DELETE` the queued job → `killed: true`; the next job starts (slot freed).
2. `DELETE` the running job → 409 with the queued-only error.
3. `DELETE` the killed job again → 200 `killed: false, status: "killed"`.
4. `DELETE` an unknown id → 404 + hint.
5. `DELETE /jobs/{id}?client=wrong-tag` on a tagged job → 403; matching tag → 200.
6. Restart the pipeline → `GET /jobs?status=killed` still lists the killed job.

---

## M3 (P2 — A4): queue status/summary

Clients currently have to guess when slots free ("1 running + 5 queued" is
documented but there's no cheap global view).

### Required behavior

Extend `GET /health` (keep every existing field — clients parse them) with:

1. `running` / `queued`: when non-empty these MUST be lists of job IDs
   (verify the current behavior lists IDs, not counts — the film run suggests
   they do, but make it explicit in the contract).
2. New `queue` object:
   ```json
   "queue": {
     "counts": {"running": 1, "queued": 5},
     "queued_jobs": [
       {"job_id": "...", "flow": "shots", "client": "sw-holiday-spoof/S12",
        "created": "2026-11-19T12:00:00Z", "waited_s": 342.5},
       ...
     ],
     "oldest_queued": {"job_id": "...", "flow": "shots", "client": "...",
                       "created": "...", "waited_s": 342.5}
   }
   ```
   `queued_jobs` in **FIFO order** (oldest first — that's the wait order);
   `oldest_queued` = first element, or `null` when the queue is empty.
   `waited_s` = now − created, for the oldest job at request time.
3. New `features` list so clients can feature-detect:
   `"features": ["assemble-duration-hold", "job-kill", "queue-summary",
   "job-progress"]` (add each as it lands; M1–M5 all land → all four).
4. Consistency invariant: `pending == counts.running + counts.queued`
   (assert it in a test; `pending` today is a count — keep it).

Estimated wait: do NOT build a predictor — `waited_s` on the oldest job plus the
known per-flow wall-clocks (documented in the contract) is enough; clients can
estimate.

### Acceptance

1. Idle queue: `queue.counts = {running: 0, queued: 0}`, `oldest_queued: null`,
   `queued_jobs: []`.
2. One job queued: its ID, flow, client tag, and a plausible `waited_s` appear;
   `pending == 1`.
3. Existing `/health` fields unchanged (`ok`, `gpu_locked`, `jobs`,
   `max_concurrent`, `max_queue_depth`, `max_pending`, `pending`, `running`,
   `queued`, `queue_depth`, `retention_*`).

---

## M4 (P2 — A4): fix `status=running` under-reporting in `GET /jobs`

During the film run, `media_list_jobs(status=running)` showed 1–2 of 6 in-flight;
per-ID `GET /jobs/{id}` polling was the only authoritative check.

### Required behavior

Make the status taxonomy **accurate** (verify the root cause in the source —
likely the in-memory registry only flips to `running` when the GPU worker
actually starts, or ledger rows are completion-only and in-flight jobs are
merged in with stale statuses):

1. A job holding the execution slot (GPU/worker work in progress) MUST read
   `status: "running"` in both `GET /jobs?status=running` and `GET /jobs/{id}`.
2. A job accepted but waiting for a slot MUST read `status: "queued"`.
3. `GET /jobs?status=running` returns exactly the jobs in state 1;
   `?status=queued` exactly the jobs in state 2. (Clients can already pass
   comma-separated lists — `status=queued,running` for "in-flight" — so the fix
   is accurate statuses, not a new pseudo-status.)
4. Cross-check with `/health`: the ID in `health.running` must be the same ID
   `GET /jobs?status=running` returns; `health.queued` IDs == `?status=queued`
   IDs. (This is the M3 invariant from the client's side.)

### Acceptance

1. Submit 6 small jobs (queue fills: 1 running + 5 queued).
   `GET /jobs?status=running` → exactly 1 job, ID == `health.running[0]`.
   `GET /jobs?status=queued` → exactly 5, IDs == `health.queued`.
2. As jobs complete, the running ID rolls to the next queued job and the
   counts stay consistent (poll through all 6 completions).

---

## M5 (P2 — C13): job progress field for long assembles

A 36-shot `upscale_each` assemble runs 3+ h (observed ~12.5 min/shot under
contention). Today the only stall signal is counting `upNN` artifacts in the
ledger's `outputs` array (that's how the film run detected 20/36 at ~253 min).
Make progress first-class.

### Required behavior

1. `GET /jobs/{id}` for an **in-flight assemble** returns:
   ```json
   "progress": {"stage": "upscale", "completed": 20, "total": 36,
                "label": "upscale shot 20/36"}
   ```
   - Update `completed` as each per-shot artifact finishes (the assembler
     already writes `norm_NN` / `upNN` files in a per-shot loop — hook the
     counter into that loop; no new worker needed).
   - Stages for assemble: `normalize` (per-shot norm_NN, when
     `loudnorm`/normalization runs) → `upscale` (upNN, when `upscale_each`) →
     `concat` → `finalize` (final.mp4 / title / overlays). For a no-upscale
     assemble: `concat` → `finalize` (fast stages — a coarse
     `{"stage": "assemble", "completed": i, "total": N, "label": "shot i/N"}`
     over the per-shot loop is acceptable there).
2. The **final ledger row** includes the terminal progress
   (`{"stage": "done", "completed": N, "total": N}`) so the durable record shows
   it completed all shots.
3. In-flight progress is in-memory (it dies with the job — fine; the ledger row
   is the durable record). Do NOT append per-shot progress lines to jobs.jsonl
   (keep the ledger one-row-per-job).
4. Non-assemble flows: `progress` is optional; single-stage jobs may omit it.

### Acceptance

1. Submit a multi-shot assemble (≥ 6 shots, `upscale_each: true` if a cheap
   upscale path is available, else plain). While in-flight, poll `GET
   /jobs/{id}` and observe `progress.completed` strictly increasing toward
   `total`; `label` reflects the stage.
2. On completion, the ledger row (`GET /jobs?client=<tag>` or the jobs.jsonl
   file) carries the terminal progress with `completed == total`.

---

## Shared requirements

- **Additive only.** No existing field removed/renamed; `GET /jobs` rows gain
  fields, old rows stay as-is. `duration_adjustments` / `progress` / `queue` /
  `features` are new.
- **Ledger:** new rows may carry the new fields; the 14-day retention sweeper is
  unchanged.
- **No new public surface.** Everything stays on `:8189` LAN (public routes are
  the thor Caddy layer).
- **Keep the running version** (image/config) until the new one passes all
  acceptance tests — rollback is restoring it (per the proposal).
- **Change notes:** when done, write a short `CHANGELOG` entry (or equivalent)
  in the pipeline source tree: date, M1–M5, the M1 backward-compat note, and
  the acceptance-test results (job IDs + measured durations).

## Thor-side follow-ups (NOT this handoff — for the record)

- `agents-skills/media-film/references/pipeline-contract.md` +
  `docs/media-mcp-handoff.md` §4: document the new semantics after the changes
  land (homelab repo, `tier2-media-film-production-lessons` companion).
- `tier3-media-queue-mcp-tools`: `media_kill_job` + `media_queue_status` MCP
  tools wrapping M2/M3 (client-scoped via the `?client=` param).
- C13 companion: contract docs for assemble wall-clock expectations (range-based:
  upscale_each observed ~12.5 min/shot under contention, ~5 min idle) and the
  draft-assemble pattern.
