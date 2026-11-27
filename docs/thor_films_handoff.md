# Thor handoff — mcp_media film-project tools + storyboard skill

> Created: 2026-11-27 · Status: **BLOCKED on matrix** (`media-pipeline/films_plan_matrix.md`
> batches B1–B5). Each section below lands when its matrix batch is live.
> Source of truth for `mcp_media`: `mcp/servers/media` in
> [choukalos/homelab-ai-harness](https://github.com/choukalos/homelab-ai-harness) — **pull
> current main first; never work from a cached copy.** Deployed container: thor `mcp_media`
> (SSE FastMCP proxy → matrix:8189).
> Context: `gemini-insights.md` + Chuck's 2026-11-27 decisions (NAS-durable film projects,
> interactive storyboard with approve gate before any draft video, lip-sync B4, montage B5).
> Pattern reference: `docs/thor_media_mcp_handoff.md` (T1–T5), `docs/media_film_skill_spec.md`.

## 0. Conventions (unchanged)

- Every job tool: `non_blocking` (default `false`) + `media_job_result(job_id, wait_seconds≤25)`
  polling; forward `user`/`client` identity; result shape `{path, location}`.
- 60 s MCP transport timeout → retry wrapper client-side; 503 queue-full → stop submitting, poll.
- Skills go to the skills gateway (thor) — `media-storyboard` (new) + `media-film` (upgrade).

## 1. mcp_media tools (add to `mcp/servers/media`, per matrix batch)

### B1 (matrix B1 live → these)

| Tool | Pipeline endpoint | Notes |
|---|---|---|
| `media_project_create(slug, title)` | `POST /projects` | sync |
| `media_project_list()` | `GET /projects` | sync |
| `media_project_get(slug)` | `GET /projects/{slug}` | sync — includes approval state + nas_sync |
| `media_project_asset(slug, path, file)` | `POST /projects/{slug}/assets` | sync — register char sheet / env plate / keyframe / voice ref (local file path or upload) |
| `media_project_shotplan(slug, shotplan)` | `POST/GET /projects/{slug}/shotplan` | sync — pass-through; 400s carry field errors |
| `media_project_storyboard_pages(slug, per_page=6)` | `POST /projects/{slug}/storyboard_pages` | job (ffmpeg-only, fast) — returns page paths; client `media_fetch`es them for human review |
| `media_project_approve(slug, what="storyboard", note?)` | `POST /projects/{slug}/approve` | sync — the gate |
| `media_project_sync(slug)` | `POST /projects/{slug}/sync` | sync — force NAS mirror |

### B2

| Tool | Notes |
|---|---|
| `media_generate_shot` **+ `end_keyframe`** | optional file param → FLF2V (wan14b only; 400 on ltxv2b). Update description + README row. |
| `media_animatic(manifest, audio?)` | job — Tier-0 pacing preview (zoompan keyframes + audio) |
| `media_extract_frame(video, frame?/time?/positions?)` | sync-ish job (ffmpeg) — frame chaining + QC |
| `media_assemble` **+ `lut`, `grain`** | optional grade params on the existing tool |

### B3 / B5 / B4

| Tool | Notes |
|---|---|
| `media_qc_montage(video, n=3)` | B3 — first/mid/last (or n) → 1 labeled image for vision QC |
| `media_montage(video, n=9, label=true)` | B5 — generic video → labeled grid |
| `media_lipsync(video, audio, face_box?)` | B4 — only if matrix B4 gate passed; else document as deferred |

## 2. Skills (thor skills gateway)

### 2.1 NEW: `media-storyboard` — interactive pre-production with approve gate

Trigger: user wants to plan/pitch a film or short ("storyboard a film about X").
**Hard rule: no I2V/draft spend before `media_project_approve` lands. This is the
anti-suck-atude gate (potato-wars incident).**

Workflow:
1. **Intake** — premise, characters, style, target duration. Create project
   (`media_project_create`); write `storyboard/script.md` via `media_project_asset`.
2. **Pre-production assets** (before any shot keyframe):
   - per character: turnaround sheet (front/3-4/back, neutral light) + 2×2 emotion grid via
     `media_generate_image` → `media_project_asset` (`bible/characters/<id>/`);
   - per location: 3–4 environment plates (angle-specific empty sets) → `bible/environments/<id>/`;
   - per speaking character: create a locked voice (`media_add_voice` from a reference line) —
     record `voice_id` in the shotplan audio blocks.
3. **Two-tier shotplan** — Scene → Beats → Micro-shots, schema exactly as
   `media-pipeline/films_plan_matrix.md` §2.4. Craft rules (enforce while writing):
   - one physical beat per action shot, ≤49 frames @16 fps (≈3 s);
   - 180° rule: `screen_direction_lock` per scene, injected verbatim into every keyframe prompt;
   - shot mix for action ≈10% wide / 50% medium / 40% macro inserts;
   - `keyframe_prompt` = static composition, ZERO motion verbs (lens, lighting, placement);
   - `motion_prompt` = camera + physics only, ZERO appearance words, mouth closed for dialogue;
   - `risk_level` on every shot (high = fight impacts, two-character interaction, lunges).
4. **Keyframe pass** — per shot: `media_edit_image` with character sheet(s) + env plate +
   composition prompt (multi-reference). QC each keyframe (vision) before next; re-seed ≤3×,
   then flag to user.
5. **Pages + review loop** — `media_project_storyboard_pages` → `media_fetch` pages → present
   to user. **Explicit accept/revise loop**: user reviews pages (and the shotplan JSON),
   requests per-shot changes → regenerate only changed keyframes → re-render pages. Repeat
   until the user says accept.
6. **Approve + handoff** — `media_project_approve(slug, "storyboard")`; `media_project_sync`
   (NAS copy is permanent). Tell the user the project is ready for production →
   `media-film` picks it up from the project store.

### 2.2 UPGRADE: `media-film` — production from an approved project

Keep everything in `docs/media_film_skill_spec.md` (state file, retry wrapper, timeboxed
chunks, resume protocol, queue mgmt, QC gates, publish checklist) and add:

1. **Project-first** — start from `media_project_get(slug)` (durable store, not just the local
   state file); local state file still written after every submit (both, always).
2. **Gate check** — refuse to render drafts/heroes if `approval.storyboard` is null (point the
   user at `media-storyboard`).
3. **Phase-batched submission** (matches the 1-job-serial GPU queue):
   all keyframe refreshes → **animatic gate** (`media_animatic` + user watch/review) →
   drafts (high-risk shots only; macro inserts + slow dialogue skip straight to hero) →
   heroes (FLF2V when `use_end_frame_anchor`; frame-chaining via `media_extract_frame`
   last-clean-frame → next `start` when `chain_from_prev_shot`).
4. **Audio-first dialogue timing** — TTS before video for dialogue shots;
   `duration_frames = ceil((ffprobe_duration + 0.35) × 16)`, snapped to 4k+1.
5. **Takes, never overwrite** — every finished clip registered in `takes.json`
   (job_id, seed, model, path); `active` pointer; re-roll = new take; swap = flip pointer.
6. **QC retry budget** — vision-check keyframes pre-I2V (cheap) and 2–3 frames per clip
   post-I2V (`media_qc_montage`); spatial/anatomy fail → re-seed ≤3× → flag to user.
7. **Editorial finish** — trim ~8–12 dead frames head/tail via assemble `in`/`out`; SFX
   pre-lap −4…−6 frames (J-cut) via timestamped SFX; final assemble with `lut` + `grain`;
   `media_qc_montage` on the final cut at 6–8 timeline points before publish.
8. **Mouths closed** — dialogue motion prompts never ask the video model to talk;
   `lip_sync_required` shots get `media_lipsync` post-render (B4, when live).

## 3. Verification (per batch, on thor)

1. `git pull` main → apply tools → rebuild/restart `mcp_media` → tool-list schema check.
2. Smoke per new tool (non_blocking submit → poll → result shape) against the live matrix
   endpoints; 400 paths (bad slug, ltxv2b + end_keyframe, invalid shotplan).
3. Skill smoke: run `media-storyboard` end-to-end on a 4-shot test project through the
   approve gate; confirm `media_project_get` shows approved + NAS sync ok; hand off to
   `media-film` for a 2-shot draft (ltxv2b) → animatic → hero → assemble with grade.
4. Update `mcp/servers/media/README.md` tool table + this file's status; push (Chuck pushes).
