# Thor handoff — mcp_media improvements (post 2026-11-02 incident)

> **For:** the Thor agent (owner of the `mcp_media` MCP server, `192.168.4.54`)
> **From:** Matrix (GPU host) — pipeline-side changes are **already shipped** (2026-11-04)
> **Source of truth:** `mcp/servers/media` in `choukalos/homelab-ai-harness`
> (`server.py` = 22 FastMCP tools, `media_pipeline_client.py` = thin stdlib HTTP
> client). Deployed container: `ai-mcp-mcp-mcp_media:latest`.
> **Companion docs:** `docs/matrix_media_pipeline_api.md` (pipeline contract,
> incl. new `GET /jobs` + §7.1 client guidance), `docs/media_film_skill_spec.md`
> (client-side orchestration skill — Mac side).

---

## 1. Context (why this exists)

During the 2026-11-02 "A Peanut Risky Business" film run (20 shots, ~5 h), the
client-side orchestration crashed 4 times and lost 9 job IDs that were
unrecoverable: job IDs lived only in the pipeline's in-memory registry and in
the dead script's memory, and **no log line anywhere contained a job ID**.
Cost: ~1 h GPU (duplicate I2V jobs) + ~1 h log archaeology.

**Matrix already shipped (2026-11-04, no action needed):**
- `GET http://192.168.4.55:8189/jobs` — durable job listing (finished jobs from
  the `jobs.jsonl` ledger **survive pipeline restarts** + live in-flight jobs).
  Filters: `status` (comma-separated), `client`, `user`, `flow`, `since`
  (ISO-8601), `limit` (1..200). Full contract: API doc §2.
- Job IDs logged to `docker logs media_pipeline` (`job submitted: <id>
  flow=… client=… user=…` / `job finished: <id> status=…`).
- `GET /jobs/{id}` unknown/expired → structured 404 with a hint.
- `user`/`client` identity already accepted on **every** submit endpoint and
  recorded in the ledger — the MCP layer just needs to expose/pass it (T2).

**Remaining on thor:** T1–T5 below. Suggested order: T1 → T2 → T3 → T4 → T5
(~2 h total). All changes are additive; the pipeline is backward compatible,
so the old client keeps working throughout.

---

## 2. Tasks

### T1 — Log job IDs to the mcp_media container log *(~15 min, cheapest win)*

**Problem:** container-log archaeology failed because submit responses were
never logged.

**Change:** in the mcp_media server, log one line per submit and per final
result:

```python
# after a submit returns a job_id (all job tools):
logger.info("SUBMIT kind=%s tag=%s job_id=%s", kind, client_tag or "-", job_id)
# when job_result returns a terminal status:
logger.info("DONE job_id=%s status=%s path=%s", job_id, status, path or "-")
```

`kind` = the MCP tool name (`generate_shot`, `edit_image`, …); `client_tag` =
the `client` value forwarded to the pipeline (T2). Gives `docker logs
ai-mcp-mcp-mcp_media` a recovery path even before T2 lands.

**Verify:** submit a job via the MCP tool; confirm both lines in container logs.

### T2 — `media_list_jobs` tool + `client` passthrough *(~1 h, highest value)*

**Problem:** even with pipeline logging, a multi-project client can't tell
which jobs are its own, and there's no way to list jobs at all.

**Change (two parts):**

1. **`media_pipeline_client.py`** — add the pipeline's new endpoint:

```python
def list_jobs(self, status=None, client=None, user=None, flow=None,
              since=None, limit=50):
    q = {k: v for k, v in {"status": status, "client": client, "user": user,
                           "flow": flow, "since": since, "limit": limit}
         if v is not None}
    return self._get("/jobs", params=q)   # adapt to the client's existing helper
```

2. **`server.py`** — new FastMCP tool:

```python
@mcp.tool()
def media_list_jobs(status: str = None, client: str = None, user: str = None,
                    flow: str = None, since: str = None, limit: int = 50) -> str:
    """List media-pipeline jobs, newest first (durable — survives pipeline
    restarts). Use this to RECOVER lost job_ids: match by client tag and/or
    time (e.g. since='2026-11-02T00:00:00Z'). Filters: status
    (queued,running,done,error,timeout or 'all', comma-separated), client,
    user, flow, since (ISO-8601), limit (1..200, default 50). Each job:
    job_id, flow, status, client, user, created, done, model, duration_s,
    cost_usd, error, output, outputs (run-dir-relative files; null when the
    job dir was retention-swept after 14 days)."""
    ...
```

3. **`client` passthrough** — every job tool (`media_generate_shot`,
   `media_generate_image`, `media_edit_image`, `media_generate_music`,
   `media_generate_sfx`, `media_text_to_speech`, `media_upscale_video`,
   `media_assemble`, `media_trim`, `media_freeze`, `media_caption`,
   `media_storyboard`, `media_transcribe_song`) should accept an optional
   `client: str = None` and forward it to the pipeline's `client` field
   (JSON body or form field — the pipeline already accepts it on all
   endpoints). Convention: `<project>/<shot>` (e.g. `peanut-movie/S1`).
   Keep forwarding the existing `user` identity as-is (metering depends on
   it).

**Verify:** submit a job with `client="qa_list_jobs"`; call
`media_list_jobs(client="qa_list_jobs")` → the job appears with status/outputs.
Restart the pipeline container (`docker restart media_pipeline` on matrix —
ask the operator) → the job still appears (ledger-backed).

### T3 — Document the 60 s MCP timeout in tool descriptions *(~30 min)*

**Problem:** transient `MCP request timed out after 60000ms` errors killed
long orchestration scripts that had no retry wrapper.

**Change:** in the tool descriptions (and the server README), add:

> Polls block up to `wait_seconds` (max 25). The MCP gateway has a **60 s hard
> timeout** — transient "MCP request timed out" errors are expected on long
> orchestrations; **retry the call** (the job is unaffected). Submits return
> 503 + `retry_after_seconds` when the queue is full (1 running + 5 queued) —
> stop submitting and poll. Wrap all calls in retry logic (≥3 attempts).

Put the same text in `media_job_result`'s description (the tool most likely
to time out).

### T4 — `media_fetch` basename collision *(~30 min)*

**Problem:** fetching 5 files named `vo.wav` into one subdirectory silently
overwrote each other (all 5 "succeeded" to the same path).

**Change:** preserve the source basename; on collision, suffix
(`vo_2.wav`, `vo_3.wav`, …) or fail loudly — never silently overwrite.
(Alternative: a `dest_name` param.) Log the final destination path.

**Verify:** fetch the same source file twice → two distinct paths (or a
clean error); the first file is intact.

### T5 — mcp_vision permission bug *(~30 min)*

**Problem:** `vision_analyze_image` fails with `/workspace/vision` permission
denied (observed 2026-11-02).

**Change:** inspect the mcp_vision container's volume mounts / workspace
permissions on thor (the `/workspace/vision` dir is not writable by the
container user — likely an ownership mismatch after a host update). Fix the
mount/ownership; verify with both a URL and a local-path image. Until fixed,
the documented workaround: `POST https://llm.choukalos.com/v1/chat/completions`
with model `matrix-coder` and `image_url` content parts (base64 data URLs work)
— keep that workaround documented in the skill (`docs/media_film_skill_spec.md`)
and in mcp_vision's tool description.

---

## 3. Verification checklist (after deploy)

```bash
# via the MCP tools, or directly against the pipeline:
curl -s 'http://192.168.4.55:8189/jobs?limit=5'
```

- [ ] deploy: updated `media_pipeline_client.py` + `server.py` into the live
      media-mcp server dir + rebuild/restart the container
- [ ] T1: `SUBMIT` + `DONE` lines in `docker logs ai-mcp-mcp-mcp_media`
- [ ] T2: `media_list_jobs` returns recent jobs; `client` filter works;
      `client="…"` on a submit is recorded and filterable
- [ ] T2: `media_list_jobs` still returns a job after a pipeline restart
      (ledger durability)
- [ ] T3: timeout/retry text present in `media_job_result` description
- [ ] T4: duplicate-basename fetch → suffixed path or clean error
- [ ] T5: `vision_analyze_image` works on a URL + a local path
- [ ] regression: `media_generate_image` / `media_edit_image` /
      `media_generate_shot` still work with the old call signatures (no
      `client` given)

## 4. Rollback

All changes are additive. Rollback = restore the previous
`server.py`/`media_pipeline_client.py` from git (`mcp/servers/media`) +
rebuild/restart the container. The pipeline side needs nothing — `GET /jobs`
is a plain read-only endpoint and the old client ignores it.