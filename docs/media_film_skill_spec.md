# `media-film` orchestration skill — spec

> **Status:** SPEC (2026-11-04) — to be implemented as a pi skill on the
> client (Mac) at `~/.pi/skills/media-film/SKILL.md` (pattern: `publish-file/SKILL.md`).
> Optionally register in the family skills gateway (`mcp_skills`) so `run_skill`
> can invoke it.
> **Audience:** any LLM client orchestrating a multi-shot film through
> `mcp_media` (pi on the Mac, Siri, n8n).
> **Companion docs:** `docs/matrix_media_pipeline_api.md` (pipeline contract,
> incl. `GET /jobs` + §7.1 polling/timeout guidance), `docs/thor_media_mcp_handoff.md`
> (MCP-layer additions: `media_list_jobs`, `client` passthrough).

---

## 1. Why this skill exists (incident context)

2026-11-02, "A Peanut Risky Business" short-film run (20 shots, ~5 h wall
clock): the orchestration (long codemode scripts on the Mac) failed 4 times
while generating 19 I2V clips (Wan 2.2, ~11 min each, serial queue):

| # | Failure | Cause | Consequence |
|---|---------|-------|-------------|
| 1 | 32-min orchestration script died | One `media_job_result` poll hit the 60 s MCP transport timeout; no retry wrapper | 4 just-submitted job IDs lost (in-memory only) |
| 2 | Recovery script died on first poll | Used `setTimeout` (not available in the codemode QuickJS sandbox) | 5 more job IDs lost |
| 3 | Chunk script died after 5.5 min | Called an undefined `persist()` helper (script bug) | 1 job ID lost |
| 4 | Final poll script died after 28 min | Same undefined `persist()` bug | no new loss (jobs were known) |

Recovery cost: ~1 h of log archaeology, 6 duplicate I2V jobs (~1 h GPU time,
orphaned clips retained 14 d on the GPU host), 4 extra resubmit cycles. The
film itself shipped fine.

**Root causes (three independent gaps):**
1. **Transient MCP timeouts are fatal to long scripts** — the litellm-proxy →
   mcp_media hop intermittently hangs past the 60 s tool-call timeout.
2. **The job registry was ephemeral and unlistable** — job IDs lived only in
   the pipeline's in-memory registry and the *caller's* memory. **Fixed on
   matrix 2026-11-04:** `GET /jobs` (durable ledger + live registry, filterable
   by client/time), job-ID logging in `docker logs media_pipeline`, structured
   404 with a hint. `media_list_jobs` MCP tool pending on thor
   (`docs/thor_media_mcp_handoff.md`).
3. **Client-side state is fragile** — codemode `store()` only persists if the
   script *succeeds*; timers don't exist in the sandbox. This is what the skill
   encodes (the server-side fixes cannot protect against a dead client).

## 2. Environment map (verified 2026-11-04)

| Thing | Location / value |
|---|---|
| GPU host (matrix) | `192.168.4.55` (use the hostname), pipeline API `http://matrix:8189` (LAN-only) |
| Pipeline artifacts | `/home/chuck/data/comfyui/run/media_jobs/<job_id>/mp_<job_id>_00001.{mp4,png,wav,…}` — 14-day retention |
| mcp_media container | thor `192.168.4.54`, image `ai-mcp-mcp-mcp_media:latest`, proxies to `matrix:8189`, fetch dir `/home/chuck/data/media/generated/pipeline` |
| Public Caddy routes | `https://siri.choukalos.com/media/pipeline/upload` (X-Api-Key) and `/media/pipeline/dl/<token>` (signed URL). Everything else under `/media/pipeline/*` is LAN-only |
| Queue capacity | 1 running + 5 queued = 6 pending max; submit returns HTTP 503 when full (`retry_after_seconds` hint) |
| Models | `wan14b` (Wan 2.2 14B I2V, 832×480, ~5 s clip, ~10–12 min), `ltxv2b` (LTXV 2B, fast draft), `qwen21` (Qwen-Image-2.1 keyframes/edits), ACE-Step (music), MMAudio (SFX), TTS voice library (`trailer`, `narrator_f`, `deep_m`, …) |
| MCP tool-call timeout | 60 s hard timeout at the litellm-proxy MCP gateway ("MCP request timed out after 60000ms") |
| Vision | mcp_vision was broken (`/workspace/vision` permission denied, 2026-11-02; fix pending on thor). Workaround: `POST https://llm.choukalos.com/v1/chat/completions` with model `matrix-coder` and `image_url` content parts (base64 data URLs work) |
| Job visibility (2026-11-04) | `GET /jobs?client=…&since=…` (durable across pipeline restarts), `GET /health` (`running`/`queued` job IDs), `docker logs media_pipeline` (submit/finish lines), `media_list_jobs` MCP tool (pending on thor) |

## 3. What the skill must encode (all learned the hard way)

1. **State file on disk, written immediately after every submit** — not
   codemode `store()` (which is lost on script failure). Schema (proven in the
   peanut-movie run's `state.json`):
   ```json
   {
     "title": "...", "created": "...", "target_duration_s": [90, 150],
     "assets": { "gpu_uploads": { "name": "<media_jobs path>" } },
     "identity": { "<character>": "<canonical identity prompt>" },
     "shots": { "S1": { "beat": "...", "kf_path": "...", "i2v_job": "<id>", "i2v_path": "...", "qc": "..." } },
     "i2v": { "S1": { "job_id": "...", "path": "...", "status": "done" } },
     "i2v_pending": ["S13"],
     "audio": { "music": "...", "vo": "...", "sfx": "..." },
     "assemble_job": "<id>"
   }
   ```
2. **Retry wrapper on every MCP call** (4 attempts):
   ```js
   const mcall = async (name, args, tries = 4) => {
     for (let i = 0; i < tries; i++) {
       try { const r = await tools[name](args); return JSON.parse(r.content[0].text); }
       catch (e) { if (i === tries - 1) throw e; }
     }
     throw new Error("retries exhausted: " + name);
   };
   ```
   (No `setTimeout` in the sandbox — back off by issuing a cheap blocking call,
   e.g. `media_job_result` on a known-done job with `wait_seconds: 25`.)
3. **Timeboxed chunks** — one codemode call per 4–8 job completions (~45–90 min
   max). Persist state after each completion.
4. **Resume protocol** — on any crash:
   (a) read the state file;
   (b) re-poll all known `job_id`s (they stay in the registry while jobs are
       live);
   (c) check `GET /health` (`running`/`queued` lists in-flight IDs) and
       `media_list_jobs`/`GET /jobs?client=<project tag>` (durable ledger —
       finished jobs survive pipeline restarts) to recover any IDs missing
       from the state file;
   (d) for shots with a job but no result, resubmit (orphaned duplicates are
       harmless — 14 d retention);
   (e) continue from `i2v_pending`.
5. **Queue management** — keep ≤5 in-flight submissions (queue = 1 running +
   5 queued); on 503 "queue full", stop submitting and poll instead of
   spinning (honor `retry_after_seconds`).
6. **QC gates** — vision-check every keyframe before spending I2V time
   (keyframe regen is ~1 min vs I2V ~11 min); vision-check 2–3 action frames
   per clip post-I2V; vision-check the final cut at 6–8 timeline points (title
   cards, key beats, end card).
7. **Assembly + publish checklist** — `media_info` on the final cut (duration
   in target range, codec, audio present) → `media_fetch` to
   `/home/chuck/data/media/generated/pipeline/...` (approved publish root) →
   `run_skill publish_file` with `subdirectory: "video"` → verify public URL
   with `curl -sIL`.
8. **Audio timing pattern** — TTS segments generated separately, placed with
   `adelay` + `amix` in ffmpeg, combined track uploaded via the public
   `/media/pipeline/upload` route, single `vo` path with `vo_start: 0`.
   (Assemble overlays/SFX `at`/`start`/`end` are absolute timeline seconds —
   see `docs/matrix_media_pipeline_api.md` §2 `/assemble` timing note.)

## 4. Verification plan

- Dry-run the skill against a 3-shot test film.
- **Kill test:** kill the orchestrating script mid-run; confirm the resume
  protocol (state file + `/health` + `media_list_jobs`) recovers all in-flight
  IDs with no duplicate submits of already-finished shots.

## 5. Related upstream issue (pi codemode — not fixable from the skill)

The codemode runtime has two gaps that amplified the incident (file upstream
against `@earendil-works/pi-coding-agent`; a local patch is not durable):

1. `store()` should persist per-call, not only when the script succeeds — a
   crashed script loses all stored state (the exact failure that cascaded
   here).
2. Provide `sleep(ms)` (or document loudly that timers don't exist) —
   `setTimeout` is undefined in the QuickJS sandbox and produced a
   ReferenceError after the script had already submitted 5 GPU jobs.

Until then: the skill's on-disk state file + retry wrapper are the mitigation.