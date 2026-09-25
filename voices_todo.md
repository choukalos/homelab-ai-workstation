# voices_todo — voice library for the media pipeline (matrix) + MCP exposure (thor)

Created 2026-09-24 after verifying the live pipeline (see §Background).

> **STATUS (2026-09-25): Part 1 (matrix) DONE & verified.** Part 2 (thor MCP)
> pending — standalone handoff: `voices_thor_handoff.md` (repo root).
> Order of work: **Part 1 (matrix) first** — the pipeline changes are backwards
> compatible, so existing videos/VO keep working throughout. **Part 2 (thor)**
> deploys after matrix Phases 1–4 are green.

## Part 1 verification log (2026-09-25)

- Deploy: `model-manager rebuild media-pipeline` (2 deploys — one mid-test bugfix:
  `flow_voices_add` was passing the host path to in-container ffmpeg; fixed with
  `to_container_any`).
- Manifest auto-seeded on first boot: `trailer` + `default` (protected).
- `GET /voices` → 4 voices with descriptions. ✅
- Unknown voice `POST /tts` → clean **400** listing available voices. ✅
- Backwards compat: `POST /tts` with no voice → trailer (job `3e9471fc128e`). ✅
- TTS per voice + F0 sanity check (autocorrelation, stdlib):
  `trailer` 123 Hz, `default` ~193 Hz (stock), `deep_m` 154 Hz, `narrator_f` 212 Hz,
  path-based ref (narrator_f) 210 Hz. ✅ (deep_m v1 at ×0.95 measured 172 Hz —
  indistinguishable from stock; re-registered at ×0.85 + lowpass 5 kHz → 154 Hz.)
- `POST /voices` dogfood: `narrator_f` (job `626828423999`) + `deep_m` v2
  (job `8daef1ef24b8`) — QC, 16 kHz mono normalize, sample generation all live.
  Re-register upsert kept `added` dates. ✅
- `DELETE /voices/tmp_test_v` → 200 + files removed; protected `trailer` → 400;
  missing → 404. ✅
- E2E video (job `1d3faa490cdc`): 2 static shots + `narrator_f` VO, 10 s 1080p mp4
  (`/home/chuck/data/comfyui/run/media_jobs/1d3faa490cdc/final.mp4`). ✅
- Retention: `_retention_sweep()` iterates `JOB_DIR` (`media_jobs/`) only — voices
  dir under `basedir/` untouched (code inspection). ✅
- Docs updated: `docs/matrix_media_pipeline_api.md` (endpoints + notes + changelog),
  `media-mcp-client/HANDOFF.md` (contract + reference code, new tools marked ⏳
  pending), `media-mcp-client/README.md`, top-level README index.
- Reference artifacts (48 h signed URLs, minted 2026-09-25): E2E video +
  `narrator_f`/`deep_m` samples (see session log; re-mint via `POST /dl_token`).

## Background — verified current state (2026-09-24)

- **TTS engine: XTTS-v2 (Coqui), zero-shot voice cloning.** Every "voice" is just a
  3–10 s reference clip. Model: `/basedir/models/tts/XTTS-v2/` (container) =
  `/home/chuck/data/comfyui/basedir/models/tts/XTTS-v2/` (host).
- **Voice dir:** `/basedir/models/tts/voices/` (container) =
  `/home/chuck/data/comfyui/basedir/models/tts/voices/` (host). Contents today:
  - `en_sample.wav` — XTTS stock sample (**male**) → the `default` voice
  - `trailer_raw.wav` / `trailer_ref.wav` — pitch-shifted (×0.88) + reverb/lowpass
    clone of `en_sample.wav` (**male**, dramatic) → the `trailer` voice (the default)
  - → **both built-in voices are male**, which is why every video VO sounds male.
- **Worker:** `tts_worker.py` — repo `media-pipeline/workers/tts_worker.py`, deployed
  by copy to `/home/chuck/data/comfyui/run/media_workers/` (container
  `/comfy/mnt/media_workers/`, no rebuild needed for worker changes). Runs via
  `docker exec` into `comfyui_backend` with venv `/comfy/mnt/venvs/venv-tts/bin/python`.
  CLI: `--text TEXT [--voice {trailer,default}] [--reference-audio PATH] --out PATH`;
  `language="en"` hardcoded.
- **Server:** `flow_tts` (server.py:536) forwards **only** `--voice` + `--out`.
  `--reference-audio` is never forwarded, and the worker's argparse rejects any voice
  other than `{trailer,default}` (verified via job `b046ded80c64` error). So the
  documented contract — HANDOFF.md §4: "`voice` for `/tts`: `trailer` … **or a path to
  a custom reference wav**" — is **broken**. (The MCP tool description also advertises
  this.)
- **No voice discovery** — no endpoint lists available voices.
- **Retention sweeper** only touches `media_jobs/` (job dirs + uploads) — the voices
  dir under `basedir/` is safe from the 14-day sweep (verify in Phase 4).
- **Deploy (matrix):** `server.py` changes → `model-manager rebuild media-pipeline`
  (`compose/comfyui.yml`, profile `image`). Worker changes → `cp` into
  `/home/chuck/data/comfyui/run/media_workers/`.
- **MCP client (thor):** canonical source in this repo `media-mcp-client/`
  (`media_pipeline_client.py` + `mcp_tools.py`), copied into the media-mcp server dir
  on thor; container `ai-mcp-mcp_media:latest` (`mcp_media`), env
  `MEDIA_PIPELINE_URL=http://192.168.4.55:8189`.
- **Music (ACE-Step):** vocal gender is prompt-driven — no code change needed; just
  specify the vocalist in the prompt (e.g. "female lead vocals"). Doc-only.
- ComfyUI on matrix also has **ElevenLabs + Fish-Audio TTS nodes** (with voice
  libraries/cloning) — unused by the pipeline. Future option, not this plan.

**Design decision:** keep the worker dumb (unchanged). All voice-library logic lives
in `server.py`: a JSON manifest next to the reference clips, name→reference resolution
in `flow_tts`, plus three small endpoints. XTTS-v2 needs no new model download.

---

# Part 1 — matrix: expand the media pipeline voice library

## Phase 1 — Voice library + manifest ✅ (2026-09-25)

- [ ] **Manifest file:** `/home/chuck/data/comfyui/basedir/models/tts/voices/manifest.json`
  (server container already mounts `basedir` at `/home/chuck/data/comfyui/basedir`).
  Schema:
  ```json
  {
    "voices": {
      "<name>": {
        "file": "<name>_ref.wav",          // in the voices dir
        "description": "short human description (agent-facing)",
        "gender": "female|male|unknown",
        "style": "narrator|trailer|casual|...",
        "added": "2026-09-24T00:00:00Z",
        "sample": "<name>_sample.wav"      // optional; generated at registration
      }
    }
  }
  ```
  - Names: lowercase slug `[a-z0-9_]{2,32}`.
  - Seed with the two existing voices: `trailer` (file `trailer_ref.wav`, gender male,
    style trailer) and `default` (file `en_sample.wav`, gender male, style default).
  - `trailer`/`default` are **protected**: cannot be deleted or overwritten.
- [ ] **Manifest helpers in `server.py`:** `load_voices_manifest()`,
  `save_voices_manifest()` (atomic write: tmp + rename), `resolve_voice(voice) ->
  (ref_path | None, error)`:
  1. `voice in {"trailer","default"}` → legacy pass-through (worker handles it,
     incl. first-run `trailer_ref.wav` generation)
  2. `voice` is a manifest name → its reference path
  3. `voice` contains `/` or ends in `.wav` → treat as a GPU-host path to a reference
     wav (must exist) — **fixes the broken contract**
  4. else → error listing available names

## Phase 2 — Server API (server.py) ✅ (2026-09-25)

- [ ] **Fix `flow_tts` (server.py:536):** use `resolve_voice()`:
  - case 1 → `--voice <name>` (unchanged behavior)
  - cases 2/3 → `--reference-audio <path>` (worker flag already exists)
  - case 4 → raise before enqueue → HTTP 400 with the available-voice list
  - Also accept explicit `{"text", "reference_audio": "<path>"}` in the payload
    (belt-and-braces; takes precedence over `voice`).
- [ ] **`GET /voices`** (sync): return the manifest (name, description, gender, style,
  added, has_sample). No auth change — same LAN-only posture as the rest of the API.
- [ ] **`POST /voices`** (job flow, GPU work for the sample):
  - Input: `{name, description?, gender?, style?, source, sample_text?}` where
    `source` = a `media_jobs/` path to a reference wav **or** a multipart upload
    (reuse the existing `/upload` pattern; 500 MB cap is fine).
  - Job steps: QC the reference (duration **3–15 s** — XTTS wants ≥6 s, warn on
    3–6 s; single speaker is a doc note, not checkable automatically) → ffmpeg
    normalize to 16 kHz mono wav → copy to `voices/<name>_ref.wav` → upsert manifest
    entry → generate `voices/<name>_sample.wav` by running the worker with the new
    reference (default sample text: a neutral 2-sentence line) →
    `output: {voice, ref, sample}`.
  - Re-registering an existing name replaces its ref/sample (protected names 400).
- [ ] **`DELETE /voices/{name}`** (sync): reject protected names; delete
  `<name>_ref.wav`, `<name>_sample.wav`, manifest entry.
- [ ] **Docs:** update HANDOFF.md §4 API table (`/tts` voice semantics + the three new
  endpoints) and the §8 quality tips with the ACE-Step note: *"vocal gender in
  generated music is prompt-driven — write 'female lead vocals' / 'male baritone' etc.
  into the prompt; unspecified often comes out male."*

## Phase 3 — Seed the portfolio (dogfood `POST /voices`) ✅ (2026-09-25)

Goal: a small, genuinely useful starter set. All references are 6–10 s clips.

- [ ] **`narrator_f`** (female narrator) — first pass **synthetic, zero external
  deps**: clone `en_sample.wav` with the same trick as the trailer voice but inverted
  (pitch **up** ~1.25–1.3× via `asetrate`, light EQ to soften) → register via
  `POST /voices`. (Quality is "fine for VO", not broadcast; acceptable for a first
  female voice.)
- [ ] **`deep_m`** (second male, distinct from trailer): pitch ×0.95, no reverb.
- [ ] **Optional / later:** a real (human) female reference clip — clean 6–10 s
  single-speaker sample (public-domain audio, or a family voice sample with consent)
  replacing the synthetic `narrator_f` ref. Real references clone far better.
- [ ] Record each voice's description/gender/style in the manifest (agent-facing:
  e.g. `narrator_f`: "bright female documentary narrator").

## Phase 4 — Deploy + test (matrix) ✅ (2026-09-25)

- [ ] Deploy: `model-manager rebuild media-pipeline` (worker is unchanged, so no
  worker copy needed unless Phase 2 touched it).
- [ ] `GET /voices` → shows trailer, default, narrator_f, deep_m.
- [ ] `POST /tts` per voice with a test line → fetch each `vo.wav` via
  `GET /files/<jid>/vo.wav`; **listen** to each: narrator_f clearly female, deep_m
  male-but-not-trailer, trailer unchanged.
- [ ] Objective sanity check (optional): estimate F0 of each sample (e.g. a quick
  Python pitch estimate or `ffmpeg` + aubio) — female ref should sit ~100 Hz+ above
  the male refs.
- [ ] **Backwards compat:** `POST /tts {"text":...}` with no voice → still trailer;
  old job payloads still work.
- [ ] **E2E:** small 2-shot video with `narrator_f` VO + music via `/assemble`;
  watch/listen.
- [ ] **Retention check:** confirm `_retention_sweep()` (server.py:270) only sweeps
  `media_jobs/` — the voices dir under `basedir/` must be untouched (it is, by code
  inspection; note it in the test log).
- [ ] Update the `kb_homelab` fact (id `a7955657-2f72-04b9-af65-890a985200de`) with the
  new voice inventory.

---

# Part 2 — thor: expose the voice portfolio in the mcp_media MCP tools

> Hand to the **Thor agent** after Part 1 Phase 4 is green.

## Phase 5 — Client code (repo `media-mcp-client/`) ⏳ (thor)

- [ ] **`media_pipeline_client.py`:**
  - `list_voices()` → `GET /voices` (sync)
  - `text_to_speech(text, voice="trailer", reference_audio=None)` → `POST /tts`
    (add optional `reference_audio` field)
  - `add_voice(name, description, source, gender=None, style=None, sample_text=None)`
    → `POST /voices` (multipart when `source` is a local file; JSON + path when it's a
    `media_jobs` path)
  - `delete_voice(name)` → `DELETE /voices/{name}`
- [ ] **`mcp_tools.py`:**
  - New tool **`media_list_voices()`** — "List the TTS voice portfolio (name,
    description, gender, style). Call before generating VO to pick a voice."
  - New tool **`media_add_voice(name, description, reference, gender?, style?)`** —
    register a new voice from a reference clip; `reference` may be a local path under
    the staging dir `/home/chuck/workspace/media` (auto-upload, existing pattern) or a
    `media_jobs` path.
  - New tool **`media_delete_voice(name)`** (low priority; skip if it bloats the
    toolset).
  - **`media_text_to_speech` docstring update:** "voice = a name from
    `media_list_voices` (e.g. trailer, default, narrator_f) or a GPU-host path to a
    reference wav (3–15 s single-speaker clip). Default 'trailer' (male movie-trailer
    voice)."
- [ ] **Audition flow (no new endpoint needed):** `media_list_voices` →
  `media_text_to_speech(<short line>, voice=<name>)` → `media_pull` signed URL for the
  user to listen. Note this flow in the tool descriptions.
- [ ] **Docs:** sync HANDOFF.md (tool table, API contract, §8 quality tips) and
  README tool list.

## Phase 6 — Deploy to thor ⏳ (thor)

- [ ] **Reconcile first:** the live `mcp_media` container may be ahead of this repo's
  copy (it has extra tools like `media_put`/`media_pull`/`media_fetch` — diff the
  running server's `mcp_tools.py` against `media-mcp-client/mcp_tools.py` before
  touching it; merge, don't clobber).
- [ ] Copy the updated `media_pipeline_client.py` + `mcp_tools.py` into the media-mcp
  server dir on thor.
- [ ] Rebuild `ai-mcp-mcp_media:latest` + restart the `mcp_media` container.
- [ ] Confirm pi sees the new tools (`ListToolsRequest` / tool list in a fresh
  session).

## Phase 7 — Verification checklist (MCP level, from thor) ⏳ (thor)

- [ ] `media_list_voices` returns trailer, default, narrator_f, deep_m with
  descriptions.
- [ ] `media_text_to_speech("...", voice="narrator_f")` → wav; `media_pull` → listen:
  female.
- [ ] `media_text_to_speech` with `voice=<path-to-ref-wav>` works (the previously
  broken contract).
- [ ] Default call (no voice) → still the male trailer voice (unchanged).
- [ ] `media_add_voice` end-to-end: stage a 6 s test clip in
  `/home/chuck/workspace/media`, register as `test_v`, generate a sample, delete it.
- [ ] Update the `kb_homelab` fact + this file's status block; commit both repos'
  changes (matrix: `media-pipeline/`, thor: `media-mcp-client/`).

---

## Cross-cutting notes

- **Backwards compatibility:** `trailer`/`default` names, defaults, and job payloads
  are unchanged; the worker CLI is unchanged. Nothing breaks in the meantime.
- **Retention:** voices live under `basedir/models/tts/voices/` — outside the
  14-day `media_jobs` sweep.
- **Metering:** TTS jobs are already metered per user/client (`metering.py`); the new
  `voices` job type is trivial — meter it as `tts` or leave unmetered (decide in
  Phase 2).
- **Security:** `/voices*` follows the existing LAN-only, no-auth posture of the
  pipeline API; public delivery stays via signed `/dl` tokens only.
- **ACE-Step (songs):** no code change — prompt guidance only (see Phase 2 docs item).

## Out of scope (future ideas)

- **ElevenLabs / Fish-Audio backend** (ComfyUI nodes already on matrix) for higher
  quality / bigger voice catalog — needs API keys + licensing review.
- **Multilingual VO:** XTTS-v2 does 17 languages; worker hardcodes `language="en"` —
  add `--language` passthrough + `lang` in the payload when needed.
- Voice picker UI (Open WebUI / portal), per-voice cost notes, voice "favorites".