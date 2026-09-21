# fix_todo.md — /home/chuck directory-layout audit fixes (matrix)

Audit date: 2026-09-20. Machine: **matrix** (GPU host, 192.168.4.55).
Policy: `homelab/` = code/config/compose/scripts (git-versioned, GitHub),
`data/` = data to back up, `workspace/` = temporary, no backup.

Decisions received 2026-09-21. Remaining work: **F2 only** (code change, pending).

---

## F1. Delete stale pipeline code copy in workspace  ✅ DONE (2026-09-21)

Deleted `rm -rf /home/chuck/workspace/media_pipeline` (stale server.py/workflows.py/
comfy_client.py/requirements.txt from the Aug 26-28 build, `.venv/`, `__pycache__/`,
and the uninstalled `media-pipeline.service` systemd unit). Nothing referenced it —
the running `media_pipeline` container is built from `homelab/media-pipeline`
(`compose/comfyui.yml` → `build: context: ../media-pipeline`).

## F3. Workspace project dirs — deliverables preserved, dirs deleted  ✅ DONE (2026-09-21)

Chuck had already moved the deliverables (kitchen-strikes-back + neon-thunder finals
were byte-identical to `data/comfyui/run/media_jobs/` copies — verified by md5).
**Caveat found during cleanup:** `final_potato_wars.mp4` had NO copy in `data/` —
its only copy was in the workspace dir. All three finals were therefore preserved to
the backupable zone before deletion:

```
data/media/projects/kitchen-strikes-back/final_titled.mp4
data/media/projects/potato-wars/final_potato_wars.mp4
data/media/projects/neon-thunder/neon_thunder.mp3 (+ .wav source)
```

Then deleted: `workspace/kitchen-strikes-back/`, `workspace/potato_wars/`,
`workspace/neon_thunder/`. Workspace now holds only throwaway test artifacts
(`media_test/`, `upscale_test/`, root test PNGs/JSONs/mp4, `make_template.py`) —
fine per policy.

## F5. Stray file in home dir  ✅ DONE (2026-09-21)

Deleted `~/cuda-keyring_1.1-1_all.deb`.

## F4. Backup for /home/chuck/data  → MOVED to `backup_todo.md` (2026-09-21)

Chuck: backups needed, but NOT blanket model-cache re-download; back up only
selected models (qwen3.8 NVFP4 daily driver + current media pipeline models) and
only occasionally; target = his server **Lego**. Parked as a separate design
conversation — see `backup_todo.md` for the captured decisions + inventory.

---

## F2. media_jobs retention — APPROVED, pending implementation  [REMAINING]

**Approved params (2026-09-21):**
- Retention: **14 days** default, configurable via `.env` (`MEDIA_JOB_RETENTION_DAYS`).
- `uploads/` (13 MB of uploaded inputs): **also cleaned** — chuck said "we should
  probably remove uploads".
- `metrics/` (88 KB `jobs.jsonl` — per-job metering/billing log: ts, job_id, flow,
  user, client, status, duration, work_units, tokens, cost_usd, model, params;
  feeds `/metrics` for Thor's Prometheus scrape): **EXCLUDE from cleanup** — it's the
  audit/billing trail, not job output.

**Implementation plan (code change in `homelab/media-pipeline/`, versioned):**
1. Add a retention background task to `server.py`: every N hours, delete job dirs
   under `media_jobs/` whose mtime is older than `MEDIA_JOB_RETENTION_DAYS` (default
   14). Skip `metrics/`; sweep stale files in `uploads/` by the same age rule.
   Never delete the dir of a job that is `queued`/`running`.
2. One-off manual cleanup: remove `acestep_test/`, `ltxv_test/`, `tts_test/` and job
   dirs older than 30 days (547 dirs / 2.2 GB today).
3. Document in `docs/matrix_media_pipeline_api.md` (retention + env var).
4. Rebuild: `model-manager rebuild media-pipeline`.
5. Note: the Aug 27-28 finals in `media_jobs/` are now >14 days old and will be
   reclaimed by retention — that's fine, they're preserved in
   `data/media/projects/` (F3).

---

## NOT issues (verified OK — no action)

- `homelab/` repo: clean, in sync with `origin/main` (0/0), `.env` gitignored,
  `state/` gitignored. All media pipeline + client code versioned.
- `homelab/data/benchmarks/` (544 KB): small benchmark evidence, intentionally git-tracked.
- `homelab/` root working docs (TODO.md, auth_todo.md, media_pipeline_gaps.md,
  EXPERIMENTS_RESULTS.md, qwen*.log): git-tracked, so they meet the versioning policy.
- `data/comfyui/run/kitchen_strikes_back/` (56 MB): project working data on the data side — fine.
- `workspace/` remaining test artifacts: temporary by nature — fine per policy.
- One-off project scripts (produce_*.py, qc_*.py, make_template.py): temporary
  project scaffolding; fine unless chuck wants them versioned/reusable.