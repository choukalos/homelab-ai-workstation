# Media Pipeline Gap-Fill Plan

Two workstreams, in order: **Part 1 (matrix, GPU host 192.168.4.55) must complete before Part 2 (thor, homelab 192.168.4.54).** Each tool needs changes in both parts; the matrix side is the implementation, the thor side is the MCP wrapper + public routing.

> **Status (2026-09-07):** ✅ **PART 1 (M1–M9) COMPLETE** — all matrix endpoints implemented, image rebuilt, QA 38/38 passing (`media-pipeline/qa_part1.py`), client updated (17 MCP tools), docs updated (contract + README + HANDOFF + `media_jobs/PIPELINE_CHANGES.md`). Part 2 (thor) not started — see THOR HANDOFF. Step 0 recon complete (results in Part 1). Client decisions: metering = new flows register at 0 GPU work units (model `ffmpeg`); ffmpeg = in-container via the existing `run_ffmpeg` pattern (6.1.1 in `comfyui_backend`, not on host); M1 fixture = stand-in MOV; M4 still without `duration` = 0s (unchanged); M3 = textfile-based drawtext; P2 batch upscale = skip (redundant with `upscale_each`); queue sharing = accepted (simple architecture, VRAM-constrained). **Auth:** matrix keeps the current no-auth approach — see `auth_todo.md` (needs to be updated from the thor side first).

## Context

The **mcp_media** MCP tools (exposed on thor behind the LiteLLM MCP gateway at `https://llm.choukalos.com/mcp/`) are a **light wrapper** — the actual pipeline runs on matrix. Job artifacts live in:

```
/home/chuck/data/comfyui/run/media_jobs/<job_id>/mp_<job_id>_00001.mp4   (or .png / .wav)
```

**Architecture (known facts — verify in Step 0, don't re-discover):**
- The pipeline service on matrix listens on **`:8189`** (thor's wrapper calls `MEDIA_PIPELINE_URL`, default `http://<gpu-host>:8189`).
- The thor-side wrapper source is public: repo **`choukalos/homelab-ai-harness`**, dir **`mcp/servers/media/`** — `server.py` (MCP tool registration) + `media_pipeline_client.py` (thin stdlib HTTP client: POST JSON → `job_id`, poll status, `GET /files/{name}` relative to the job dir to fetch results to a thor-local path).
- The wrapper's visible input roots: homelab `/home/chuck/data/media/generated/...` and matrix `media_jobs/` ONLY (ComfyUI `input/` and `output/` are NOT visible to it).
- ComfyUI runs on matrix at `:8188` (RTX PRO 5000 72GB, intentionally VRAM-constrained as a sidecar to the main LLM — do NOT add concurrency/queueing machinery around GPU jobs).
- `media_assemble` concatenates shots (must be media_jobs paths) + mixes one VO + one music + one SFX track → final mp4.
- Public site: Caddy (thor:80) routes `choukalos.com` → `portal:8080` (repo `portal/server.py`, compose `homelab-portal`); the `/files/` drop zone serves `/home/chuck/data/media/public/`.
- **No DNS wildcard on choukalos.com** (verified 2026-09-07): `media.choukalos.com` is NXDOMAIN; existing subdomains (siri, llm, api, invest, plausible) are individual CF records.

**How a new tool lands (both parts):**
1. **matrix:** new endpoint on the `:8189` pipeline service (job-based, like the existing ones).
2. **thor:** new method in `mcp/servers/media/media_pipeline_client.py` + new MCP tool registration in `mcp/servers/media/server.py`.
3. **thor (only for client-facing tools T3):** public Caddy route.

The pipeline was used end-to-end to produce a 58s mockumentary ("Peanut: The Documentary"). The gaps below were all hit in that build, with hard evidence. Close them without breaking existing tools.

---

# PART 1 — MATRIX (do this first)

> **✅ COMPLETE (2026-09-07).** M1–M9 implemented, image rebuilt, container recreated,
> QA 38/38 (`media-pipeline/qa_part1.py`), client smoke-tested end-to-end
> (all 9 new methods + extended `assemble`). Change log:
> `media_jobs/PIPELINE_CHANGES.md`. Notable fixes found during QA: `/assemble` audio
> truncation (apad), `MEDIA_DL_SECRET` config-ordering, `/upload_local` basedir
> confinement (exfil vector), `_probe_summary` audio-only crash.

## Step 0 — Recon (report before implementing)

1. Locate the pipeline service source (the `:8189` server): `docker ps`, `systemctl list-units`, `ss -tlnp | grep 8189`, `/home/chuck/data/comfyui/`, `/opt`, `/srv`. Confirm the job API surface matches what `media_pipeline_client.py` in `choukalos/homelab-ai-harness` expects.
2. Map how jobs are dispatched to ComfyUI (workflow templates, `/upload/image` usage, how results are pulled from ComfyUI `output/` into `media_jobs/`).
3. Confirm `ffmpeg`/`ffprobe` versions on this host.
4. Identify ComfyUI's real `output/` dir (the `:8188` history reports a `/basedir/output/` placeholder; check process cwd, docker mounts, or `/system_stats`).
5. Report: pipeline location + API surface, dispatch mechanism, ffmpeg version, ComfyUI output path. Then proceed to M1.

**Step 0 results (verified on matrix, 2026-09-07):**
1. **Pipeline source:** `/home/chuck/homelab/media-pipeline/server.py` (FastAPI + uvicorn; container `media_pipeline`, image `media-pipeline:latest`, host network + docker.sock; binds `0.0.0.0:8189`). API surface matches `media_pipeline_client.py` (local copy: `media-mcp-client/`): `POST /storyboard /images /images/edit /shots /tts /music /sfx /upscale /assemble`, `GET /jobs/{id} /files/{name} /health /metrics`. Canonical contract: `docs/matrix_media_pipeline_api.md`.
2. **Dispatch:** `comfy_client.py` + `workflows.py` (JSON templates, `mp_<jid>` filename prefix). Job dir created via `docker exec -u comfy comfyui_backend mkdir` (uid 1024, then chmod 777). Uploads → `media_jobs/<jid>/input/` → `docker cp` into ComfyUI `/basedir/input/`; ComfyUI outputs pulled host-side from `basedir/output/` into the job dir. TTS/ACE-Step workers run via `docker exec` inside `comfyui_backend`. **ffmpeg runs in-container** via the existing `run_ffmpeg` helper (`docker exec -u comfy comfyui_backend ffmpeg`) — **ffmpeg/ffprobe 6.1.1 exist only inside `comfyui_backend`, not on the host**.
3. **ComfyUI real output dir:** `/home/chuck/data/comfyui/basedir/output/` (container `/basedir/output`).
4. **Existing machinery not in the plan's "known facts":** bounded FIFO job queue (`MAX_CONCURRENT_JOBS=1`, `MAX_QUEUE_DEPTH=5`, 503 + retry_after) — new CPU jobs share it (accepted); **metering** (`metering.py`: Prometheus `/metrics` + `jobs.jsonl`, user/client identity) — new flows register at 0 GPU work units (model label `ffmpeg`, like `assemble`); `/assemble` already has `text_overlays` (drawtext, textfile-based; DejaVu fonts confirmed in container) and `upscale_each` (per-shot SeedVR2). **No auth anywhere on :8189** (contract: "LAN-only, no auth — never expose publicly").
5. **Acceptance fixtures:** Peanut final cut = `media_jobs/11b290ce2acf/final.mp4` (58.67s, 30MB). `Peanut_runs_in_leaves.MOV` is not on matrix (client-side file) — M1 uses a stand-in MOV of similar length. `media_jobs/qa_tests/` does not exist yet.
6. **Thor repo confirmed public:** `choukalos/homelab-ai-harness` — `mcp/servers/media/{server.py,media_pipeline_client.py}`, `portal/server.py`, `caddy/Caddyfile` all exist. Part 2 facts (portal line numbers, Caddy, CF DNS) must be verified on thor.

## M1. `media_trim` — cut a clip to a time range

**Gap evidence:** a 17.5s source MOV had to be dropped from the cut entirely — no tool can trim. `media_assemble` uses shots at full native duration.

```
POST /trim   { source, start, end | duration, reencode=true, output_name? }  → job_id
```
**Impl:** `ffmpeg -ss <start> -i in -t <dur> -c:v libx264 -crf 18 -preset medium -c:a aac out` (`-ss` after `-i` for frame accuracy). Output → media_jobs, standard naming.
**Acceptance:** trim a stand-in MOV of similar length (~17.5s; the original `Peanut_runs_in_leaves.MOV` is a client-side file, not on matrix) to 0–4.0s → M5 reports 3.95–4.05s; clip plays cleanly.

## M2. `media_freeze` — still image or video frame → static N-second clip (NO generative model)

**Gap evidence:** freeze-frame beats had to be rendered through LTXV image-to-video, which warped faces and destroyed overlaid text. A freeze frame must be pixel-static.

```
POST /freeze  { source (image|video), frame?=0, duration=2.0, width=1280, height=720, fps=24 }  → job_id
```
**Impl:** video → extract frame N first, then `ffmpeg -loop 1 -t <dur> -i img -vf "scale=<w>:<h>:force_original_aspect_ratio=decrease,pad=<w>:<h>:(ow-iw)/2:(oh-ih)/2,fps=<fps>" -c:v libx264 -pix_fmt yuv420p -r <fps> out`.
**Acceptance:** freeze a photo for 2.0s → 48 frames, all pixel-identical (ffprobe frame count + frame-hash spot check); accepted by `media_assemble`.

## M3. `media_caption` — burn text into a clip (ffmpeg drawtext)

**Gap evidence:** LTXV is unreliable for burned-in text — dropped the caption at strength 1.0, "completely illegible" at 1.0, garbled at 0.9 (7 failed seeds on "I don't make the rules."; "HE'S GOT THE CHOMP" survived 1 of 8 tries and once rendered as "GUMBO" in the cut). Captions had to be faked with generated text cards.

```
POST /caption  { source, text, start?=0, end?=clip_end, position?="bottom", font_size?≈5% of height, font?, color?="white", outline?=3, background_bar?=false }  → job_id
```
**Impl:** textfile-based drawtext (reuse the existing `_apply_text_overlays` pattern — write the text to a job-dir file, `textfile=...`; no inline colon/apostrophe escaping): `ffmpeg -vf "drawtext=fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf:textfile=<tf>:fontsize=<fs>:fontcolor=white:borderw=3:bordercolor=black:x=(w-text_w)/2:y=h-text_h-40:enable='between(t,<start>,<end>)'"` (DejaVu fonts confirmed in `comfyui_backend`). `background_bar` = `drawbox` (black, `t=fill@0.55`) across the bottom third first (Modern Family style).
**Acceptance:** burn "I DON'T MAKE THE RULES." onto a 2s clip → exact text in every frame (extract 3 frames, OCR/vision-read), no warping.

## M4. `media_assemble` extensions (backward compatible)

**Gap evidence:** no per-shot duration/trim; a still image in `shots` displays for **0 seconds** (tested: [still + 4s video] → 4s total); single `sfx` path mixed across the whole video — no timestamp placement (chomp at 0:19, scooter at 0:44 — impossible today).

**Additive spec — existing string-array `shots` and single `sfx` must keep working:**
- `shots` entries may be objects: `{ path, in?, out?, duration? }` (trim/hold a shot; `duration` on a still image renders it via M2 logic instead of 0s)
- `sfx` may be a list: `[{ path, at }]` for timestamped placement; single path keeps current behavior
- optional `vo_start?` (offset VO from t=0) and `loudnorm?` (EBU R128 on final mix)
- a still image in `shots` **without** `duration` keeps today's behavior (1-frame ≈ 0s) — strict backward compat; `duration` is explicit-only

**Acceptance:** assemble `[still (duration 3.0), trimmed shot (in 1.0, out 3.5), captioned clip]` + two timestamped SFX → duration = sum ±0.1s; SFX at their timestamps; old-style calls produce identical results.

## M5. `media_info` — ffprobe metadata

**Gap evidence:** no way to measure duration/codec/resolution through the pipeline (VO-vs-cut fit was a guess; duration checks via vision QA).

```
GET /info?path=...   → { duration_s, width, height, fps, video_codec, audio_codecs[], size_bytes, bitrate_bps }
```
**Impl:** `ffprobe -v quiet -print_format json -show_format -show_streams`.
**Acceptance:** `duration_s` ≈ 58.6 (±0.1) for the Peanut final cut (`media_jobs/11b290ce2acf/final.mp4`, measured 58.67s).

## M6. `media_upload` — bridge arbitrary local files (on matrix) into media_jobs

**Gap evidence:** ComfyUI `output/` is invisible to the pipeline — a still→video freeze rendered via ComfyUI nodes succeeded but was unusable: `FileNotFoundError: shot not found: /home/chuck/data/comfyui/output/peanut_freeze1_00001.mp4` (real ComfyUI output dir is `/home/chuck/data/comfyui/basedir/output/` — the cited path was missing `basedir/`). `VHS_LoadVideoPath` also rejects host paths outside ComfyUI's roots, so ComfyUI can't read media_jobs either.

```
POST /upload_local  { source (any readable path on this host), subdirectory? }  → { path }
```
**Acceptance:** upload a file from ComfyUI `output/` (e.g. `peanut_freeze1_00001.mp4`) → path accepted by `media_assemble`. Unblocks arbitrary ComfyUI workflows as pipeline inputs.

## M7. `media_download` — ingest from a URL into media_jobs

**Gap evidence:** the pipeline cannot fetch external URLs (tested against a LAN HTTP server: `Keyframe not found (local or on GPU host)`). Source media had to be hand-copied.

```
POST /download  { url, subdirectory?, filename? }  → { path }
```
**Acceptance:** download a 4MB mp4 from `http://192.168.4.56:8765/` → path accepted by `media_trim`.

## M8. Client file transfer — push files IN, pull results OUT (the new piece)

**Gap evidence:** today a client that is NOT on the homelab cannot (a) hand source media to the pipeline except by hosting it somewhere the matrix box can URL-fetch, and (b) retrieve a finished video except by publishing it to the public `/files/` drop zone (which goes to the website — not everyone wants that). `media_fetch` only copies to a thor-local path.

**Design: three endpoints on `:8189`, path-confined to media_jobs, HMAC-signed pull URLs.**

```
POST /upload            # multipart, NO auth on matrix (consistent with the rest of :8189 —
                        # LAN-only trust; public auth is Caddy-layer only, see T3 + auth_todo.md)
                        # body: file + optional subdirectory
                        # → { path }  (stored under media_jobs/uploads/<ts>_<name> — not a job)
                        # cap: 500 MB (env-overridable); reject oversize with 413

POST /dl_token          # { path, ttl_hours? (default 24, max 168) }
                        # → { token, url_path: "/dl/<token>", expires_at }
                        # token = HMAC-SHA256(secret, "<path>|<expiry_epoch>"), base64url
                        # secret from env (MEDIA_DL_SECRET); token binds the EXACT path —
                        # /dl serves only that file, nothing else, no traversal possible

GET /dl/<token>         # NO API key required — the token IS the credential
                        # verify signature + expiry → stream the file (Content-Length,
                        # Content-Type, Range support for video seeking)
                        # expired/invalid → 404 (do not distinguish; don't leak paths)
```

**Safety requirements:**
- `/dl` and `/upload` resolve targets with realpath and must stay inside media_jobs (same pattern as the portal's `safe_join`).
- `/dl_token` may only be minted for paths inside media_jobs.
- Log minted tokens (path + expiry), never the token itself.
- `/upload`, `/dl_token`, `/dl` are **synchronous, not jobs** (no queue slot, no metering); everything else stays path-confined to media_jobs.

**Acceptance (runnable from any machine on the internet):**
1. `curl -X POST -F file=@clip.mp4 http://192.168.4.55:8189/upload` → `{ path }` (no auth on matrix).
2. `curl -X POST http://192.168.4.55:8189/dl_token -d '{"path": "<that path>"}'` → token.
3. `curl -OJ <matrix>:8189/dl/<token>` from a machine NOT on the LAN → byte-identical file (sha256 match), video seeks (Range 206 works).
4. Token with a 1h TTL: request after expiry → 404.
5. Token for path A cannot fetch path B.

**P2 niceties (after the core works):** vision-QA wrapper (run mcp_vision on a media_jobs path, return report inline). ~~Batch upscale~~ — skipped (redundant: `/assemble` already has `upscale_each` per-shot SeedVR2).

## M9. Docs — update everything that documents what was built

**Gap evidence:** the canonical contract (`docs/matrix_media_pipeline_api.md`, "verified live 2026-09-06 (all 9 job flows + metering)"), the client docs (`media-mcp-client/README.md`, `HANDOFF.md` — "9 MCP tools"), and the metering contract (§5) will all be stale after M1–M8.

**Update:**
1. `docs/matrix_media_pipeline_api.md` — the canonical contract: new endpoints (§2), extended `/assemble` fields, metering note for the new flows (§5: 0 GPU work units, model label `ffmpeg`), changelog entry (§9).
2. `media-mcp-client/README.md` + `HANDOFF.md` — new client methods + MCP tools; keep in sync with the thor repo's `mcp/servers/media/` (same two-file layout).
3. `PIPELINE_CHANGES.md` changelog (see Definition of done) — Part 1 + Part 2 evidence.

**Acceptance:** contract doc lists all endpoints (old + new) with request/response examples; no stale "9 flows" / "9 tools" counts remain in the docs.

**Done (2026-09-07).** All docs updated and cross-checked against the live server:
1. `docs/matrix_media_pipeline_api.md` — canonical contract (12 job flows + 6 sync endpoints, §2 table, §4 models, §5 metering, §7 recipe post-tools, §9 changelog).
2. `media-mcp-client/README.md` + `HANDOFF.md` — 17 tools; embedded code blocks regenerated from source.
3. `media_jobs/PIPELINE_CHANGES.md` — change log.
4. `docs/matrix_inventory.md` — 2026-09-07 addendum (append-only doc; supersedes the old role cell).
5. `docs/matrix_validation_log.md` — 2026-09-07 run (38/38 + 7 bugs found/fixed).
6. `docs/matrix_thor_contract.md` — v1.3 + :8189 auth-table note (no auth change; signed tokens).
7. `docs/matrix_images_mode.md` — orchestrator section note (CPU flows + sync endpoints).

Param-name audit done while updating: server uses `source` for trim/freeze/caption (not `video`),
caption uses `font_size` (client was silently sending `size` — fixed), freeze `frame` is a frame
index (not a second offset). All client methods re-verified end-to-end after the fixes.

---

# PART 2 — THOR (only after Part 1 is verified)

## T1. Cloudflare cache fix for re-published files

**Why:** re-publishing to the public drop zone is invisible behind Cloudflare for up to 4h. Measured: origin (portal) sends `Cache-Control: max-age=3600` and serves fresh bytes immediately; CF edge holds the bare URL ~4h (`cf-cache-status: HIT`; changed query string = `MISS`).

**Where:** repo **`choukalos/homelab-ai-harness`**, file **`portal/server.py`** (zero-dep stdlib static server; `portal` container, compose `homelab-portal`, image `portal:local`, port 8080; Caddy routes `choukalos.com` → `portal:8080`).

**Changes (3 small edits, all in `portal/server.py`):**
1. Line ~52: `FILES_CACHE_MAX_AGE = 3600` → `FILES_CACHE_MAX_AGE = 60`.
2. Line ~473 (`/files/` file route): `extra={"Cache-Control": f"max-age={FILES_CACHE_MAX_AGE}"}` → `f"max-age={FILES_CACHE_MAX_AGE}, must-revalidate"`.
3. `_send_file()` (line ~337): add `If-Modified-Since` → **304** support. It currently streams the full file on every request (handles `Range`, no conditional requests). Compare `If-Modified-Since` against the same mtime used for the `Last-Modified` header it already sends; unmodified → `304`, no body. **Required** — without it, every CF revalidation (now every 60s) re-downloads the full file from origin (30MB video ≈ 30MB/min/edge node wasted). With 304s, revalidation costs bytes.

**Deploy:** rebuild `portal:local` + restart the `portal` container (container is `read_only`, code baked into the image — no config-only path).

**Acceptance:**
- `curl -sI -H "Host: choukalos.com" http://192.168.4.54/files/video/peanut_doc_final.mp4` → `Cache-Control: max-age=60, must-revalidate`.
- Same + `If-Modified-Since: <future>` → `304`.
- Overwrite the public file with a different-sized test copy, wait 70s, `curl -sI https://choukalos.com/files/video/peanut_doc_final.mp4` (bare URL) → new `content-length` within ~2 min. Restore the real file.

## T2. Update the media MCP server for ALL new tools

Repo `choukalos/homelab-ai-harness`, dir `mcp/servers/media/`. For each Part 1 endpoint, add (a) a client method in `media_pipeline_client.py` and (b) an MCP tool in `server.py` (same naming style as existing `mcp_media-media_*` tools):

| MCP tool | client method | matrix endpoint |
|---|---|---|
| `media_trim` | `trim(...)` | `POST /trim` |
| `media_freeze` | `freeze(...)` | `POST /freeze` |
| `media_caption` | `caption(...)` | `POST /caption` |
| `media_info` | `info(path)` | `GET /info` |
| `media_upload` | `upload_local(...)` | `POST /upload_local` |
| `media_download` | `download(...)` | `POST /download` |
| `media_put` | `put(local_file)` | `POST /upload` (multipart — client file → media_jobs) |
| `media_pull` | `pull(path, ttl_hours)` | `POST /dl_token` → returns `{ url, expires_at }` (and optionally downloads to a thor-local path for homelab clients, like existing `fetch()`) |

Details:
- **`media_put`** is the client-side "send a file to the pipeline" tool: input is a file path **on thor** (the MCP server process runs on thor and can only read thor-local files — an off-LAN MCP client cannot pass a local file through the gateway; those clients use the raw `https://media.choukalos.com/upload` route from T3 instead). Method streams it to `POST /upload` (multipart). Output: the media_jobs path to use in subsequent tools.
- **`media_pull`** is the client-side "get my video back" tool: input is a media_jobs path (or job_id), output is the **signed public URL** (`https://media.choukalos.com/dl/<token>`, see T3) + expiry. A client anywhere can then `curl` it — no homelab access, no publishing to the website.
- **`media_assemble`** tool schema: extend for M4 (object shots, sfx list, `vo_start`, `loudnorm`) — keep the old schema working.
- Every tool gets a one-line description in the registration, matching the existing style.
- Rebuild/redeploy the `mcp/servers/media` container on thor (check its compose file for the deploy path).

**Acceptance (end-to-end, from the LiteLLM MCP gateway as an unknown client):**
1. From a machine NOT on the LAN, call `media_put` (via the MCP gateway) with a 4MB test mp4 staged on thor → media_jobs path.
2. `media_trim` it to 2s → `media_info` reports ≈2.0s.
3. `media_caption` "TEST CAPTION" → vision-read a frame, text exact.
4. Run a small `media_assemble` (2 shots, one timestamped SFX) → `media_pull` the result → `curl` the signed URL from the off-LAN machine → sha256 matches the media_jobs file.
5. All pre-existing tools (storyboard, generate_image, generate_shot, tts, music, sfx, upscale, assemble, fetch) still work unchanged.

## T3. Public client-facing route (upload + signed download)

**Why:** M8's endpoints live on matrix `:8189`, which is LAN-only. A client "not even in the homelab" needs a public, authenticated path — without publishing anything to the website.

**DNS:** add a `media.choukalos.com` A record in the Cloudflare zone (proxied, like the other subdomains — there is no wildcard). Fallback if a new subdomain is undesirable: mount the same routes under `siri.choukalos.com/media/pipeline/*` (that host already exists and already uses `X-Api-Key` auth).

**Caddy** (repo `choukalos/homelab-ai-harness`, `caddy/Caddyfile`), mirroring the existing `@siri` auth pattern:
```
@media host media.choukalos.com
handle @media {
    handle /upload {
        request_body 500MB
        @noAuth expression {http.request.header.X-Api-Key} != '{$LITELLM_KEY_CHUCK}' && {http.request.header.X-Api-Key} != '{$LITELLM_KEY_DYLAN}'
        respond @noAuth "Unauthorized" 401
        reverse_proxy http://192.168.4.55:8189
    }
    handle /dl/* {
        reverse_proxy http://192.168.4.55:8189   # no API key — the signed token is the credential
    }
    respond "Not Found" 404
}
```
Notes: confirm matrix `:8189` is reachable from thor over the LAN (same subnet; verify with `curl http://192.168.4.55:8189/health` from thor before deploying). `request_body` must be set on the upload route or Caddy's 10MB default will clip video uploads. Reload Caddy after editing (validate first: `caddy validate`). **Matrix `:8189` stays unauthenticated** (current approach — see `auth_todo.md`); Caddy is the only auth layer for the public route. `/dl_token` is not Caddy-authenticated (LAN-only minting; see `auth_todo.md`).

**Acceptance:** from a machine NOT on the homelab LAN: `curl -F file=@clip.mp4 -H "X-Api-Key: ..." https://media.choukalos.com/upload` → `{ path }`; then `https://media.choukalos.com/dl/<token>` → byte-identical file.

## T4. Docs + end-to-end verification

1. Update `mcp/servers/media/README.md`: new tool list, the client push/pull flow (upload → job → `media_pull` → signed URL → download), key/URL caveats (TTLs, 500MB cap).
2. Full end-to-end from an off-network client: upload a source photo → `media_generate_shot` → `media_caption` → `media_assemble` → `media_pull` → verify sha256 of the pulled final matches the matrix-side file.
3. Record results in the changelog (T1–T4 evidence).

---

# THOR HANDOFF — self-contained (share this section with thor)

Everything thor needs, with the matrix-side endpoint contracts inlined. Part 1 (matrix) must be verified first; the contracts below are what matrix implements on `:8189`.

## Context (thor's side)

- Repo: `choukalos/homelab-ai-harness`. Media MCP server: `mcp/servers/media/` (`server.py` = MCP tool registration, `media_pipeline_client.py` = thin stdlib HTTP client; `MEDIA_PIPELINE_URL` → `http://192.168.4.55:8189`).
- Public site: Caddy (thor:80) → `portal:8080` (`portal/server.py`); `/files/` drop zone serves `/home/chuck/data/media/public/`.
- No DNS wildcard on choukalos.com — `media.choukalos.com` needs its own CF A record (proxied).

## 1. New matrix endpoints (contracts, all on `:8189`)

Job-based endpoints return `{"job_id"}`; poll `GET /jobs/{id}` until `status=done`, then read `output`. Synchronous endpoints return directly. All paths are matrix host paths under `/home/chuck/data/comfyui/run/media_jobs/`.

| Endpoint | Kind | Request | Response (done) |
|---|---|---|---|
| `POST /trim` | job | `{source, start=0.0, end \| duration}` (exactly one of `end`/`duration`; always re-encodes, libx264 crf 18) + optional `fps?, width?, height?` | `{video}` |
| `POST /freeze` | job | `{source (image\|video), frame?=0 (frame index), duration?=2.0, width?=1280, height?=720, fps?=24}` | `{video}` |
| `POST /caption` | job | `{source, text, start?=0, end?=clip_end, position?="bottom", font_size?, font?=DejaVuSans-Bold.ttf, color?="white", outline?=3}` (multiline via textfile) | `{video}` |
| `GET /info?path=` | sync | path (matrix, exists) | `{duration_s, width, height, fps, video_codec, audio_codecs[], size_bytes, bitrate_bps}` |
| `POST /upload_local` | sync | `{source (MUST be under the ComfyUI basedir — 400 otherwise), subdirectory?}` | `{path}` (media_jobs) |
| `POST /download` | sync | `{url, subdirectory?, filename?}` | `{path}` (media_jobs) |
| `POST /upload` | sync, multipart | file + optional `subdirectory` field; **no auth on matrix** | `{path}` (`media_jobs/uploads/`); 500MB cap (413) |
| `POST /dl_token` | sync | `{path (must be inside media_jobs), ttl_hours?=24, max 168}` | `{token, url_path: "/dl/<token>", expires_at}` |
| `GET /dl/<token>` | sync | token = HMAC-SHA256(secret, `"<path>\|<expiry_epoch>"`), base64url; **no API key** | file stream (Content-Length, Content-Type, Range); 404 on bad/expired |

Notes:
- Job outputs land in the job dir with the standard `mp_<job_id>_00001.<ext>` naming. `/info`, `/upload_local`, `/download`, `/upload`, `/dl_token`, `/dl` are sync and are **not metered jobs**.
- `POST /assemble` extensions (backward compatible — old calls unchanged): `shots` entries may be objects `{path, in?, out?, duration?}` (still image + `duration` → static clip via freeze logic; still without `duration` stays ≈0s); `sfx` may be a list `[{path, at}]` for timestamped placement; optional `vo_start?` (VO offset from t=0) and `loudnorm?` (EBU R128 on final mix).
- `/upload`, `/dl`, `/dl_token` are path-confined to media_jobs (realpath); 404 (not 403) for bad/expired tokens; tokens never logged.

## 2. MCP tools + client methods (repo `mcp/servers/media/`)

For each: (a) client method in `media_pipeline_client.py`, (b) MCP tool registration in `server.py` (existing naming style, one-line description).

| MCP tool | client method | matrix endpoint |
|---|---|---|
| `media_trim` | `trim(source, start, end=None, duration=None, ...)` | `POST /trim` |
| `media_freeze` | `freeze(source, frame=0, duration=2.0, width=1280, height=720, fps=24)` | `POST /freeze` |
| `media_caption` | `caption(source, text, start=0.0, end=None, position="bottom", ...)` | `POST /caption` |
| `media_info` | `info(path)` | `GET /info` |
| `media_upload` | `upload_local(source, subdirectory=None)` | `POST /upload_local` |
| `media_download` | `download(url, subdirectory=None, filename=None)` | `POST /download` |
| `media_put` | `put(local_file, subdirectory=None)` — multipart, **thor-local path only** (MCP server runs on thor; off-LAN clients use the raw T3 route) | `POST /upload` |
| `media_pull` | `pull(path, ttl_hours=24, local_dir=None)` → `{url, expires_at}` (+ optional thor-local copy, like `fetch()`) | `POST /dl_token` |

- `media_assemble` tool schema: extend for the `/assemble` extensions above (object shots, sfx list, `vo_start`, `loudnorm`) — keep the old schema working.
- Rebuild/redeploy the `mcp/servers/media` container on thor (check its compose file for the deploy path).

## 3. T1 — Cloudflare cache fix (`portal/server.py`)

1. `FILES_CACHE_MAX_AGE = 3600` → `60`.
2. `/files/` file route: `Cache-Control: max-age=<N>` → `max-age=<N>, must-revalidate`.
3. `_send_file()`: add `If-Modified-Since` → **304** support (compare against the same mtime used for `Last-Modified`; unmodified → 304, no body). Required — without it every CF revalidation re-downloads the full file from origin.

Deploy: rebuild `portal:local` + restart the `portal` container (code is baked into the image). Acceptance: `curl -sI -H "Host: choukalos.com" http://192.168.4.54/files/...` → `max-age=60, must-revalidate`; future `If-Modified-Since` → 304; re-published file visible at the bare URL within ~2 min.

## 4. T3 — public route (Caddy + DNS)

DNS: `media.choukalos.com` A record in the CF zone (proxied). Caddy (`caddy/Caddyfile`), mirroring the existing `@siri` auth pattern:

```
@media host media.choukalos.com
handle @media {
    handle /upload {
        request_body 500MB
        @noAuth expression {http.request.header.X-Api-Key} != '{$LITELLM_KEY_CHUCK}' && {http.request.header.X-Api-Key} != '{$LITELLM_KEY_DYLAN}'
        respond @noAuth "Unauthorized" 401
        reverse_proxy http://192.168.4.55:8189
    }
    handle /dl/* {
        reverse_proxy http://192.168.4.55:8189   # no API key — the signed token is the credential
    }
    respond "Not Found" 404
}
```

Matrix `:8189` stays unauthenticated (current approach — `auth_todo.md`); Caddy is the only auth layer. `request_body 500MB` or Caddy's 10MB default clips video uploads. `caddy validate` + reload.

## 5. Acceptance (end-to-end, from an off-LAN client via the MCP gateway)

1. `media_put` a 4MB test mp4 (staged on thor) → media_jobs path.
2. `media_trim` it to 2s → `media_info` reports ≈2.0s.
3. `media_caption` "TEST CAPTION" → vision-read a frame, text exact.
4. Small `media_assemble` (2 shots, one timestamped SFX) → `media_pull` the result → `curl` the signed URL from the off-LAN machine → sha256 matches the media_jobs file.
5. All pre-existing tools (storyboard, generate_image, generate_shot, tts, music, sfx, upscale, assemble, fetch) still work unchanged.
6. T3: `curl -F file=@clip.mp4 -H "X-Api-Key: ..." https://media.choukalos.com/upload` → `{ path }`; then `https://media.choukalos.com/dl/<token>` → byte-identical file.

## 6. T4 — docs

Update `mcp/servers/media/README.md`: new tool list, the client push/pull flow (upload → job → `media_pull` → signed URL → download), key/URL caveats (TTLs, 500MB cap). Record T1–T4 results in the changelog.

---

## Constraints (all of them)

- **Do not break existing tools or their schemas** — every change is backward compatible (string-array `shots`, single `sfx`, existing tool names/args keep working).
- **Keep the media_jobs path + naming convention** (`mp_<job_id>_00001.<ext>` under `/home/chuck/data/comfyui/run/media_jobs/<job_id>/`).
- **VRAM is intentionally constrained** (sidecar to the main LLM) — no job-queueing, concurrency limits, or retry machinery.
- **ffmpeg defaults:** libx264 `-crf 18 -preset medium`, `-pix_fmt yuv420p`, audio AAC 192k (matches current assemble quality).
- **Security:** `/upload`, `/dl`, `/dl_token` path-confined to media_jobs via realpath; tokens HMAC-signed, path-bound, time-limited, never logged; `/dl` returns 404 (not 403) for bad/expired tokens.
- **Auth:** matrix `:8189` keeps the current no-auth approach (LAN-only trust); public auth = Caddy layer (T3) only. See `auth_todo.md` (needs to be updated from the thor side first).
- **Metering:** new job flows (trim/freeze/caption/info/upload_local/download) register with `metering.py` at 0 GPU work units (model label `ffmpeg`, like `assemble`); `/upload`, `/dl_token`, `/dl` are not jobs (no metering). New CPU jobs share the existing single-slot queue — no fast lane.
- **ffmpeg:** in-container only (existing `run_ffmpeg` pattern; 6.1.1 in `comfyui_backend`) — no host install, no ffmpeg in the media-pipeline image.
- **Test each tool against its acceptance criteria before declaring it done.** Keep test artifacts under `/home/chuck/data/comfyui/run/media_jobs/qa_tests/`.

## Definition of done

- **Part 1 (matrix):** Step 0 recon report; M1–M8 implemented on `:8189` and passing acceptance (M8 verified from an off-LAN machine); M9 docs updated.
- **Part 2 (thor):** T1 cache fix live (all 3 acceptance checks); T2 wrapper updated for every new tool and the T2 end-to-end acceptance passes from an unknown client via the MCP gateway; T3 public route live; T4 docs written.
- Changelog at `/home/chuck/data/comfyui/run/media_jobs/PIPELINE_CHANGES.md`.