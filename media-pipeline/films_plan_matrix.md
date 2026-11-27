# Media pipeline — film projects: NAS-durable project store, storyboard approval gate, production tools — MATRIX PLAN

> Created: 2026-11-27 · Status: **IN PROGRESS — B1 DONE (QA 35/35) · B2 DONE (QA 24/24 CPU + FLF2V GPU) · B3 DONE (QA 19/19) · B5 DONE (QA 22/22 + regression 13/13) · B4 Phase-0 GATE PASS (MuseTalk v1.5 peak 9079 MiB, 15.4 GB headroom) → /lipsync next** (long task running 2026-11-27)
>
> **Resume state (keep current):**
> - [x] Setup: backups of server.py/workflows.py/metering.py/Dockerfile/comfyui.yml → `/home/chuck/workspace/films-plan/backup/` (2026-11-27 15:44)
> - [x] Facts verified: `/mnt/lego` mounted (CIFS `//lego.local/backup`, df source check = mount probe);
>       media_pipeline container sees RUN_DIR+basedir but NOT /mnt/lego → **decision: add
>       `/mnt/lego:/mnt/lego` bind to compose/comfyui.yml + rsync to Dockerfile** (DONE);
>       no sudo on matrix → B1 NAS-failure QA test = `MEDIA_NAS_ROOT=/tmp/...` env override +
>       restart (mount probe reports "not mounted" → pending → retry on recovery).
> - [x] FLF2V node verified vs official Comfy-Org `video_wan2_2_14B_flf2v` template
>       (saved scratch copy `/home/chuck/workspace/films-plan/wan_flf2v_template.json`):
>       node = **`WanFirstLastFrameToVideo`** (start_image+end_image; NOT
>       `Wan22ImageToVideoLatent`, which has start only). Base mode = dual expert,
>       ModelSamplingSD3 **shift 8.0**, **cfg 4.0**, 20+20 steps euler/simple split at
>       step 10 (leftover enable on first sampler). umt5 fp8 + wan_2.1_vae as usual.
> - [x] **B1 DONE (2026-11-27 16:50)**: `projects.py` (project CRUD, two-tier shotplan
>       validation, takes.json, rsync NAS mirror + CIFS mount probe + hourly retry),
>       8 new `/projects` routes, storyboard grid pages (ffmpeg drawtext), approve gate.
>       **QA 35/35 PASS** (`qa_films_b1.py`, log `/home/chuck/workspace/films-plan/logs/qa_b1.log`).
>       Key fixes during B1: `import projects` must run AFTER `load_dotenv()` (env config);
>       mount probe ALWAYS runs (even forced sync — unmounted share leaves a local dir);
>       service runs as root but ffmpeg runs as comfy (uid 1024) → project dirs chmod 777;
>       single-cell storyboard rows use `null` filter passthrough (hstack needs ≥2 inputs).
> - [x] **LUTs DONE (subagent, 2026-11-27 16:55)**: `media-pipeline/luts/` = 3× 33³ .cube
>       (cinematic_teal_amber, warm_neutral, cool_noir) + README. **IMPORTANT: ffmpeg 6.1
>       `lut3d` reads INTERLEAVED RGB triples (Iridas), NOT the 3-section R/G/B layout** —
>       subagent verified empirically (identity test maxdiff=1, channel-swap test exact).
>       Delivery: Dockerfile `COPY luts/ /app/luts/` + `stage_luts()` at startup copies to
>       RUN_DIR/luts/ (comfyui_backend sees /comfy/mnt/luts/); `resolve_lut()` helper added.
> - [x] **B2 DONE (2026-11-27 19:03)**: FLF2V `end_keyframe` (wan14b only, official
>       `WanFirstLastFrameToVideo` shift 8.0/cfg 4.0) + `/animatic` (zoompan camera moves +
>       labels, ffmpeg-only) + `/extract_frame` (frame/time/positions/n) + `/assemble`
>       `lut`/`grain` (LUT+grain in the concat re-encode; bundled LUTs staged at startup).
>       **CPU QA 24/24 PASS** (`qa_films_b2.py`, log `logs/qa_b2.log`). **FLF2V GPU QA PASS**
>       (`qa_films_b2_flf2v.py` + `_part2.py`, logs `logs/qa_b2_flf2v*.log`): 480p FLF2V done
>       (peak 23066 MiB total on card), end-pose landing verified (last frame MSE 43.6 to END
>       vs 29244.8 to START; first≈start, last≈end visually), i2v 480p same-seed done
>       (peak 23028 MiB), **720p FLF2V done (peak 23349 MiB) → 720p ADMITTED, not 480p-restricted**.
>       Metering/ledger unchanged (FLF2V row = `flow=shots, model=wan2.2-14b-i2v-gguf-q8,
>       cost>0`). Submit-time validation: bad move / exactly-one-of / bad lut / grain>1 → 400.
>       **Caveat:** Ollama copywriter (gemma4:31b) co-resident on GPU 1 during QA (14 GB) →
>       early CPU offload slowed renders (480p FLF2V 17.8 min vs i2v ~10 min) + contaminated
>       the VRAM baseline (absolute peaks include Ollama's share; ComfyUI's own footprint ≈ i2v).
> - [x] **B3 DONE (2026-11-27 19:40)**: `/qc_montage` (ffmpeg only) — clip QC montage:
>       n even-spaced frames (first/middle/last for n=3) → single hstack PNG with
>       index+timestamp labels (one image → one vision call). New shared helpers
>       `_even_frame_times()` + `_extract_even_frames()` (reused by B5 `/montage`).
>       **QA 19/19 PASS** (`qa_films_b3.py`, log `logs/qa_b3.log`): n=3 dims 1440×270,
>       frames pixel-identical to `/extract_frame` positions, n=5 even spacing, label=false,
>       all 400 paths. Key fix: last-frame `-ss` overshoot (original `dur-0.04` clamp went
>       past the last decodable frame on a 5 s/24 fps clip) → clamp to `dur-0.1`.
> - [x] **B5 DONE (2026-11-27 22:07)**: `/montage` (ffmpeg only, no GPU) — any video +
>       `n` (2..36, default 9 = 3×3) + `label` → **grid PNG** (`cols=ceil(√n)`,
>       `rows=ceil(n/cols)`, last row padded with black cells), n even-spaced frames
>       reusing B3's `_even_frame_times()`/`_extract_even_frames()` (no new extraction
>       code path, no new deps). Submit-time validation: bad n / non-int / missing
>       source → clean 400. **QA 22/22 PASS** (`qa_films_b5.py`, log
>       `/home/chuck/workspace/films-plan/logs/qa_b5.log`, fresh run on the rebuilt
>       container): 60 s clip n=9 → 3×3 1440×810 + 9 evenly spaced timestamps correct
>       vs `/info` (last = dur−0.1), n=4 → 2×2, n=5 → 3×2 + 1 pad cell, label=false,
>       all 4 400 paths. **Regression 13/13** (`regress_b5.py`, log
>       `logs/regress_b5.log`): `/health` ok + `montage` in features + `/trim` +
>       `/freeze` + `/qc_montage` (shared-helper flow) all done. Docs updated
>       (`matrix_media_pipeline_api.md` contract + changelog; `matrix_validation_log.md`
>       evidence). Committed: `media: add /montage generic video montage grid (Films B5)`.
>       (Previous session 2026-11-27 19:15 died on a provider 400 image-budget incident
>       after B3; B5 code + QA were written and first QA run passed, but the batch was
>       not closed out — this run re-verified fresh before committing.)
> - [x] **B4 Phase-0 GATE PASS (2026-11-27 22:54)**: MuseTalk v1.5 (lighter candidate) on GPU 1:
>       peak **9079 MiB** (baseline 344 MiB, wall 73 s, 1 Hz nvidia-smi sampling) vs gate
>       20480 MiB → **15388 MiB (~15.4 GB) headroom ≥ 4 GB → PASS, B4 not deferred**.
>       Representative inference = official demo pair (yongen.mp4 10.7 s @25 fps 704×1216 +
>       yongen.wav 8 s) → 8.0 s 704×1216 h264 + AAC output (ffprobe-verified, audio
>       pass-through intact). Evidence: `/home/chuck/workspace/films-plan/logs/b4_gate_result.json`
>       (+ `b4_gate_run.log`); gate script + venv in scratch `b4/` (not in repo). Ollama
>       gemma4:31b (17 GB on GPU 1) unloaded via `keep_alive=0` API before each clean run
>       (not a container change); one contaminated run (peak 22672 MiB, Ollama co-resident
>       mid-run) discarded — per-run 1 Hz samples logged in the result JSON to detect this.
>       LatentSync (heavier) not measured (optional record only; gate already passed with
>       15.4 GB headroom). **B4 implementation (/lipsync) is next.**
> - [ ] B4: /lipsync implementation (gate passed — see above)
> - [ ] Final summary + docs + commits
> Source: `gemini-insights.md` (AI-filmmaking workflow analysis) + Chuck's decisions 2026-11-27:
> 1. Every film project's references/shots/storyboard stored on the NAS — durable, never removed, kept for all time.
> 2. Interactive storyboard skill with a **review/approve gate before any draft video** (anti-suck-atude; the potato-wars incident).
> 3. Lip-sync = batch 4. Video→montage helper = batch 5.
> Companion (thor side, blocked on this): `docs/thor_films_handoff.md`.
> **This file is the compact task doc — self-contained, survives context compaction. Read top-to-bottom to resume.**

## 0. Current-state facts (verified 2026-11-27)

- Service: `media_pipeline` container (FastAPI, :8189), code `media-pipeline/server.py` + `workflows.py`.
  `RUN_DIR=/home/chuck/data/comfyui/run`, `JOB_DIR=RUN_DIR/media_jobs` (14-d retention sweep —
  sweep iterates **JOB_DIR only**, so a sibling `RUN_DIR/media_projects` dir is safe from it).
- Existing flows: storyboard (single-tier LLM shot list), images (qwen21 default / legacy 4-step),
  images_edit (≤9 refs), shots (`ltxv2b` draft | `wan14b` hero, 480p/720p, 4k+1 frames, 16 fps),
  tts (voice lib + ref-wav cloning), transcribe, music, sfx, upscale (SeedVR2), trim, freeze,
  caption, assemble (per-shot in/out/duration, timestamped SFX, stills+duration, loudnorm,
  upscale_each).
- ComfyUI (`comfyui_backend`, :8188) has `Wan22ImageToVideoLatent` (FLF2V node) — confirmed in
  `wan_shots_todo.md` Phase 0. No new custom nodes needed for B2.
- NAS: `/mnt/lego` = `//lego.local/backup` (39 TB, CIFS autofs, **verified writable 2026-11-27**,
  holds `matrix/` + `thor/` snapshots). Athena (Synology 192.168.5.110) shares are NOT writable
  with current creds — **decision: use `/mnt/lego/media-projects/`**; env-overridable root so the
  target can move later (e.g. to an Athena share once Chuck grants access).
- Conventions (2026-11-22 house rules): scratch in `/home/chuck/workspace/<task>/`, GPU work serial
  (`GPU_LOCK`), VRAM peaks measured with nvidia-smi sampling (pattern `qa_wan_phase1.py`), vision
  QC via montage + `mcp_vision` subagent pattern, docs updated in `docs/matrix_media_pipeline_api.md`
  (contract + changelog) and `docs/matrix_validation_log.md` (evidence).

## 1. Batches (implement in order; B4 is gated)

| Batch | What | GPU? |
|---|---|---|
| B1 | Project store + NAS mirror + storyboard pages | no |
| B2 | FLF2V `end_keyframe`, `/animatic`, `/extract_frame`, `/assemble` LUT+grain | FLF2V only |
| B3 | QC montage (clip first/mid/last → 1 image) | no |
| B4 | Lip-sync (LatentSync/MuseTalk) — **VRAM-gated, last** | yes |
| B5 | Video → montage (generic, n labeled frames) | no |

---

## 2. B1 — Project store + NAS durability + storyboard pages

### 2.1 Design

Project store = the "project bible": one dir per film, local source of truth, mirrored to NAS.

```
RUN_DIR/media_projects/<slug>/          # env MEDIA_PROJECTS_ROOT, default this
  project.json        # {slug,title,status,created,updated,approval:{storyboard:ts|null,by},nas_sync:{last,ok}}
  bible/
    characters/<char_id>/turnaround.png, emotion_grid.png, identity.json
    environments/<env_id>/plate_<angle>.png, env.json
    voices/                       # optional copies; canonical voices live in the TTS voice library
  storyboard/
    script.md
    shotplan.json                 # two-tier: scene -> beats -> micro_shots (schema in §2.4)
    keyframes/<shot_id>.png
    pages/page_01.png ...
  production/
    clips/<shot_id>_<take>.mp4    # takes never overwritten
    audio/, final/
  takes.json                      # {shot_id: [{job_id,seed,prompt,model,path,active}]}
```

- **Single writer**: all project mutations go through the service endpoints (no ad-hoc writes),
  so the NAS mirror stays consistent. `takes.json` is updated by the service when a shot job
  finishes (hook in `set_status`/flow completion for shots registered to a project via
  `payload["project"]` — optional field on `/shots`; absent = legacy behavior, zero change).
- **NAS mirror (append-only)**: after every project mutation, `rsync -a --ignore-existing`
  (never `--delete`; NAS copy is permanent — deletions on Matrix do NOT propagate) to
  `/mnt/lego/media_projects/<slug>/`. Mount check first (`/mnt/lego` autofs noauto — stat a
  sentinel or `mountpoint -q`); if unmounted, set `nas_sync.pending=true` and retry on next
  mutation + hourly cron in the retention loop. NAS failure must never fail a job.
- **Retention**: `media_projects` is outside `JOB_DIR` → untouched by the sweep. State this in
  the API doc.

### 2.2 Endpoints (all new, additive)

| Endpoint | Purpose |
|---|---|
| `POST /projects` `{slug,title}` | create (slug: `[a-z0-9_]+`; 409 if exists) |
| `GET /projects` | list slugs + status + updated |
| `GET /projects/{slug}` | manifest + tree summary + takes + nas_sync state |
| `POST /projects/{slug}/assets` multipart `path` + file | register asset (char sheet / env plate / keyframe / voice ref); path must be under the project dir; updates `project.json` index |
| `POST /projects/{slug}/shotplan` `{shotplan}` | save/replace two-tier shotplan JSON (validate schema §2.4; 400 with field errors) |
| `GET /projects/{slug}/shotplan` | fetch |
| `POST /projects/{slug}/storyboard_pages` `{per_page?=6, page_size?="640x360"}` | render labeled grid pages from shotplan + existing keyframes (missing keyframes → placeholder cell "no keyframe"); ffmpeg-only (scale + drawtext caption + hstack/vstack tile); no GPU |
| `POST /projects/{slug}/approve` `{what:"storyboard", note?}` | set approval gate (status → `storyboard_approved`); idempotent |
| `POST /projects/{slug}/sync` | force NAS rsync now; returns synced file count + ok |

### 2.3 Storyboard page rendering (ffmpeg-only, no Pillow)

Per cell: keyframe scaled to `page_size`, `drawtext` caption bar:
`<shot_id> · <shot_type> · <duration_frames>f · <animatic_move> · risk:<risk_level>` (white on
black@0.6 box, bottom). Grid: `per_page` cells (default 6 = 3×2), `hstack`/`vstack`, page footer
`<title> — page N/M`. Output `storyboard/pages/page_NN.png`. Deterministic ordering = shotplan
order. QA: render a 7-shot fake shotplan → 2 pages; verify with vision (montage subagent pattern).

### 2.4 Shotplan schema (two-tier — the anti-suck-atude contract)

```json
{
  "scene_id": "scene_01", "screen_direction_lock": "Hero: screen-left facing right | Villain: screen-right facing left",
  "environment_plate_id": "env_kitchen_night", "color_grade_lut": "luts/cinematic_teal_amber.cube",
  "beats": [{
    "beat_id": "beat_01", "narrative_goal": "...",
    "micro_shots": [{
      "shot_id": "s01_b01_sh01", "shot_category": "dialogue_drama|action_impact|macro_insert|establishing",
      "risk_level": "low|high", "shot_type": "wide|medium|medium_close_up|extreme_close_up",
      "character_refs": ["char_id", ...], "expression_anchor": "...",
      "duration_frames": 49, "chain_from_prev_shot": false, "use_end_frame_anchor": false,
      "keyframe_prompt": "static composition only — ZERO motion verbs",
      "end_keyframe_prompt": "optional (when use_end_frame_anchor)",
      "motion_prompt": "camera + physics only — ZERO appearance words, mouth closed for dialogue",
      "animatic_move": "push_in|pull_out|pan_left|pan_right|shake|static",
      "audio": {"voice_id": "...", "dialogue_text": "...", "lip_sync_required": false}
    }]
  }]
}
```

Validation (400 with exact field list): required fields present, `duration_frames` 4k+1 for
wan14b-bound shots (≤129), `shot_category`/`risk_level`/`animatic_move` in enum, action_impact
shots ≤49f, keyframe_prompt must not contain motion-verb blacklist (warn, not fail — stored as
`schema_warnings`), unique shot_ids.

### 2.5 B1 QA

- `qa_films_b1.py`: create project → upload assets → save shotplan (valid + 3 invalid variants
  → 400s) → generate 6 test keyframes (solid colors via ffmpeg, no GPU) → storyboard_pages →
  2 pages exist, correct cell count (vision-check one page) → approve → GET shows approved →
  sync → files present on `/mnt/lego/media_projects/<slug>/` → unmount test: `umount /mnt/lego`
  (or stop autofs) → mutation succeeds + `nas_sync.pending` → remount → next sync completes.
- Regression: existing flows untouched (`/health`, one `/images` job).

---

## 3. B2 — Production tools

### 3.1 `/shots` + `end_keyframe` (FLF2V, wan14b only)

- New optional multipart file `end_keyframe`. When present: workflow uses
  `Wan22ImageToVideoLatent` (start + end latents) instead of `WanImageToVideo`. 400 if
  `model=ltxv2b`. Verify exact node wiring against the Comfy-Org `video_wan2_2_14B_i2v`
  first-last-frame template before coding (docs/matrix_comfyui_media_api.md).
- **VRAM gate first** (pattern `qa_wan_phase1.py`): 480p start+end peak vs start-only peak;
  if 720p OOMs, restrict FLF2V to 480p and document.
- QA: same start frame, same seed, with/without end frame → both render; end-frame clip visibly
  lands on the end pose (vision check); metering/ledger unchanged.

### 3.2 `/animatic` (Tier 0 pacing gate — ffmpeg only)

`POST /animatic`: manifest `[{image, duration, move}]` + optional `audio` path → per-shot
`zoompan` (pre-scale `scale=3840:-1` to avoid jitter; moves: push_in/pull_out/pan_left/pan_right/
shake/static per the gemini-insights filter expressions), `drawtext` shot number + duration,
concat, mux audio (`-shortest`). Output mp4. No GPU. This is the "watch the whole scene in 20 s
before burning 11 min/clip" tool. QA: 3-shot manifest with test images + a TTS wav →
`/info` duration ≈ sum(durations), moves visually distinct (vision).

### 3.3 `/extract_frame` (ffmpeg only)

`POST /extract_frame`: video + `frame` (int) OR `time` (s) OR `positions=["first","middle","last"]`
(+`n` for even spacing) → PNG(s). Returns path(s). Used for frame-chaining (last clean frame →
next shot's start) and QC. QA: extract frame 0/40/80 from a known clip → pixel-diff vs
ffmpeg-direct extraction (must be identical).

### 3.4 `/assemble` + `lut` / `grain`

Optional params: `lut` (path to a `.cube` under RUN_DIR or project dir) + `grain` (0.0–1.0).
Applied to the final concat: `lut3d=<cube>` + `noise=alls=<v>:allf=t` (subtle, temporal).
Ship 3 preset LUTs in `media-pipeline/luts/` (cinematic_teal_amber, warm_neutral, cool_noir —
generate small 33×33 .cubes programmatically; document as starting points, not masters).
Backward compatible (params absent = today's behavior). QA: assemble with/without grade →
`/info` identical duration; graded clip shows LUT shift (vision).

---

## 4. B3 — QC montage (clip QC)

`POST /qc_montage`: video → first/middle/last (or `n` even-spaced) frames → single hstack PNG
with index labels. One image → one vision call (fits the existing montage subagent pattern).
Shares the frame-extraction helper with B5. QA: 5 s clip → 3-frame montage, frames match
`/extract_frame` output.

## 5. B4 — Lip-sync (GATED — do last)

- **Phase 0 (gate)**: pick MuseTalk (lighter) vs LatentSync (better, heavier). Download, load on
  the dedicated 24 GB card (GPU 1, serial `GPU_LOCK`), measure peak VRAM with nvidia-smi
  sampling. **Gate: peak ≤ ~20 GB** (keep ≥4 GB headroom, same standard as the 720p Wan gate).
  If it fails → write the measurement, mark B4 DEFERRED in this file, ship everything else.
  **RESULT (2026-11-27 22:54): PASS — MuseTalk v1.5 peak 9079 MiB on GPU 1 (24467 MiB card;
  baseline 344 MiB, wall 73 s, 1 Hz sampling) = 15388 MiB (~15.4 GB) headroom ≫ 4 GB gate.**
  Representative inference: official demo pair (yongen.mp4 10.7 s @25 fps 704×1216 + yongen.wav
  8 s) → 8.0 s 704×1216 h264 + AAC stereo output (ffprobe-verified, audio pass-through intact).
  Setup (scratch, NOT repo): `b4/MuseTalk` clone + weights, `b4/venv` (torch 2.8.0+cu128 —
  sm_120 Blackwell needs cu128; mmcv 2.2.0 with a venv-local ext-loader stub because its
  prebuilt `_ext.so` predates the torch ABI — the stubbed compiled ops are never invoked on
  the RTMPose-topdown runtime path; `TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD=1` for the torch ≥2.6
  `weights_only` default; ffmpeg = imageio-ffmpeg static binary symlinked to `b4/bin/ffmpeg`).
  Ollama gemma4:31b (17 GB, GPU 1) unloaded via `keep_alive=0` API before each clean run —
  not a container change; one contaminated run (peak 22672 MiB, Ollama loaded mid-run) was
  discarded, per-run 1 Hz samples logged in `b4_gate_result.json` to detect co-resident loads.
  LatentSync not measured (optional record only; gate passed with 15.4 GB headroom).
  **B4 is NOT deferred → /lipsync ships next.** Evidence:
  `/home/chuck/workspace/films-plan/logs/b4_gate_result.json`, `b4_gate_run.log`.
- Flow `/lipsync`: video + wav (+`face_box` optional) → clip with synced lower face. Only for
  shots with `lip_sync_required`. QA: TTS line + talking-head test clip → vision-check lip
  motion alignment at 3 timestamps; audio pass-through intact (`/info`).
- Motion-prompt rule (skill side, thor): mouths stay closed in dialogue renders; lip-sync pass
  animates the lower face after.

## 6. B5 — Video → montage (generic)

`POST /montage`: any video (local or uploaded) + `n` (default 9, 3×3 grid) + `label` (bool,
timestamps) → grid PNG, frames evenly spaced. Fun tool + long-render review. Reuses the B3
helper. QA: 60 s test video → 9 labeled frames, timestamps correct vs `/info` duration.

---

## 7. Safety & conventions (all batches)

- Back up `server.py`/`workflows.py` to `/home/chuck/workspace/films-plan/` before first edit.
- API strictly additive — every existing flow's behavior unchanged (regression: `/health` + one
  job per existing flow after each batch).
- Do NOT touch `comfyui_backend`, `qwen38` (vLLM), `ollama` containers. `media_pipeline`
  rebuild/restart only; verify `/health` after every restart.
- GPU work serial; measure peaks; no new Python deps for B1–B3/B5 (ffmpeg-only). B4 may add deps
  inside its own worker container.
- After each batch: QA script + log in `state/` or `qa_films_bN.py`, changelog entry in
  `docs/matrix_media_pipeline_api.md`, evidence in `docs/matrix_validation_log.md`, update the
  Status header here. Commit per batch (`media: <imperative subject>`).
- Scratch: `/home/chuck/workspace/films-plan/`.

## 8. Definition of done

B1–B3 + B5 shipped & QA'd, B4 either shipped or DEFERRED with measurement, docs updated,
`/mnt/lego/media_projects/` mirror verified append-only, regression clean. Thor can then pick up
`docs/thor_films_handoff.md`.
