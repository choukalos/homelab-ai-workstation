# Phase 7 — Media QoL: longer clips, sharper keyframes, 720p drafts + workspace hygiene — TASK

> Created: 2026-11-22 · Status: **IN PROGRESS** (pi long task)
> Parent: `add_card_plan.md` §4.3 items 2/7/8 (QoL unlocks from the 24 GB card)
> + Chuck's 2026-11-22 housekeeping rules.
> **This file is the compact task doc — self-contained, survives context
> compaction.** Read this top-to-bottom to resume.

## Conventions (new, 2026-11-22, Chuck)

- **Scratch lives in `/home/chuck/workspace/<task>/` — never `/home/chuck/tmp`
  or ad-hoc dirs under `$HOME`.** `/home/chuck/tmp` was removed 2026-11-22.
- **Auto-cleanup**: `scripts/workspace-cleanup.sh` (daily cron 04:30) removes
  files older than 30 days under `/home/chuck/workspace` + the empty dirs
  left behind. Log: `~/.local/state/workspace-cleanup/`.
- **Harness repo clone lives at `/home/chuck/workspace/homelab-ai-harness`**
  (was `/home/chuck/tmp/homelab-ai-harness`, removed). Pull before editing;
  Chuck pushes.
- GPU 1 work stays serial (pipeline `GPU_LOCK`); measure peaks with
  nvidia-smi sampling (pattern: `qa_wan_phase1.py`).
- Vision QA via the subagent pattern (2026-11-22): side-by-side montages
  (hstack via `comfyui_backend` ffmpeg) served over LAN HTTP (throwaway
  `python3 -m http.server`, kill after) → 3 parallel codemode scripts calling
  `mcp__litellm__mcp_vision_vision_analyze_image` (URL source — mcp_vision is
  on Thor; local paths fail). Never load >5 images into the main context.

## State snapshot (2026-11-22)

- Live Wan quant: **Q8_0** (Phase 6, promoted 2026-11-22, `b73d5bb`).
  720p peak 19,577 MiB / ~57 min per 81f clip (Q5_K_M fallback kept).
- `/shots` code facts (workflows.py): wan14b frames must be 4k+1 — **no upper
  bound** (129f already API-legal); LTXV `ltxv_i2v` has **no resolution
  assert** (1280×720 already API-legal). So QoL-1/3 = QA gates + docs, not
  code. QoL-2 needs a download + env override.
- Keyframes: `/images` default `model=qwen21` (Qwen-Image 2.1 int8, 7.3 GB);
  `model=legacy` = Qwen-Image-2512 **Q4_0** GGUF (11.9 GB) + 4-step Lightning
  LoRA; `qwen-image-edit-2511-Q4_0.gguf` for edits. Filenames hardcoded in
  workflows.py:27/47 → make env-overridable (Item 3).
- Disk: ~820 GB free. GPU 1 idle ~1 GB.

## Item 1 — workspace hygiene (DONE 2026-11-22)

- [x] `scripts/workspace-cleanup.sh`: `find /home/chuck/workspace -mindepth 1
      -type f -mtime +30 -delete` then `-type d -empty -delete` (bottom-up);
      dry-run flag; refuses to run if the workspace root is missing; logs a
      summary line.
- [x] cron: `30 4 * * *` daily (off-peak, after the 03:00 Sunday backup).
- [x] Tested: dry-run + forced-delete of a seeded 31-day-old file.

## Item 2 — QoL-1: longer Wan clips (129f, ~8.1 s)

Goal: support 129-frame (4n+1) wan14b clips at 720p (and 480p), documented as
a first-class option (~1.6× the per-clip time vs 81f).

- [ ] QA gate: `qa_wan_phase1.py 720 --gpu 1` with `WAN_QA_LENGTH=129`
      (env-parametrize LENGTH — small edit). Gate: peak ≤ ~22 GB (19.6 GB at
      81f; 129f adds ~59% latent/VAE-decode activations — measure), wall
      recorded, output `wan22_p1_1280x720_0000X.mp4` 129f @16fps ≈ 8.06 s.
- [ ] Quality: 2 frames vs the 81f baseline clip (montage + vision subagent).
- [ ] Docs: API §4 (frames param: 81 default, 129 supported, 4k+1) + §6
      (129f 720p peak + wall) + changelog; harness: mcp_media
      `media_generate_shot` description + media-film contract (clip length
      option).
- [ ] If peak > ~22 GB: record + cap frames at 81 for 720p (clean 400 above
      the cap) and keep 129f for 480p only.

## Item 3 — QoL-2: sharper keyframes (Qwen-Image-2512 Q6_K)

Goal: a higher-quant 2512 keyframe option (Q6_K ~15–16 GB) for better I2V
conditioning, env-selectable; promote if QA shows a clear win.

- [ ] Find the 2512 Q6_K GGUF on HF (same repo as Q4_0/Q3_K_M — check
      `docs/matrix_comfyui_media_api.md` §6 / the Qwen-Image-2512 GGUF
      publisher; verify via HF API + `object_info/UnetLoaderGGUF` after cp).
      Download to `/home/chuck/workspace/phase7_models/` (NEW convention —
      not /home/chuck/tmp), docker cp into the container (comfy-owned dir —
      `docker cp` works; chown not needed, ComfyUI reads as comfy).
- [ ] workflows.py: `QWEN_IMAGE_2512_MODEL` env (default
      `qwen-image-2512-Q4_0.gguf`) on the t2i + edit UNetLoaderGGUF nodes.
      (Edit model stays Q4_0 — no Q6 edit model in scope.)
- [ ] QA: same keyframe prompt (the Pomeranian style prompt or a storyboard
      keyframe) at 1280×720 via `/images`: `model=qwen21` (current default),
      `model=legacy` (Q4_0), `model=legacy` + Q6 env. nvidia-smi peak for the
      Q6 run (gate ≤ ~22 GB). 3-way montage (qwen21 | 2512-Q4 | 2512-Q6) →
      vision subagent: sharpness/detail/text-rendering/photorealism.
- [ ] Decision: clear win + fits → promote (compose env
      `MEDIA_IMAGE_MODEL=legacy` + `QWEN_IMAGE_2512_MODEL=...Q6_K.gguf`) —
      NOTE: this changes the production keyframe model from qwen21; record
      the before/after evidence. Otherwise keep qwen21 default and document
      2512-Q6 as the `model=legacy` high-quality option.
- [ ] Docs: API §4 (images model row) + §6 (2512-Q6 peak) + changelog;
      harness mcp_media `media_generate_image` description + media-film
      contract (keyframe model options).

## Item 4 — QoL-3: LTXV 720p fast lane

Goal: 1280×720 drafts via `model=ltxv2b` (~8 s/clip), so dry-runs match final
framing.

- [ ] QA gate: `/shots model=ltxv2b width=1280 height=720 frames=97 fps=25`
      (nvidia-smi peak during the run; gate ≤ ~22 GB — LTXV is ~10–12 GB at
      768×512, 720p is ~2.5× the pixels — measure). Wall + output spec
      (1280×720 h264 25fps 97f ≈ 3.88 s).
- [ ] Quality: 2 frames vs 768×512 draft (montage + vision subagent) —
      expectation: same draft quality, bigger canvas (no regression check
      needed beyond "no artifacts/OOM").
- [ ] Docs: API §4 (ltxv2b row: 768×512 default + 1280×720 option) + §6
      (LTXV 720p peak) + changelog; harness mcp_media description +
      media-film contract.
- [ ] If OOM/too slow: record + keep 768×512 (item closed as rejected).

## Item 5 — Thor (harness) changes — one commit

Repo: `/home/chuck/workspace/homelab-ai-harness` (fresh clone 2026-11-22).
- [ ] `mcp/servers/media/server.py` + `media_pipeline_client.py` + README:
      `media_generate_shot` (wan14b 129f option; ltxv2b 720p option),
      `media_generate_image` (2512-Q6 keyframe option if Item 3 promotes or
      documents it).
- [ ] `agents-skills/media-film/` SKILL.md + pipeline-contract.md: clip
      length + draft resolution + keyframe model rows.
- [ ] Commit → **tell Chuck to push**.

## Execution order (GPU 1 serial — longest first)

1. T0 (now): hygiene + clone + Q6_K download (background, ~15 GB).
2. T1: wan 129f 720p QA (~90 min — start first, nohup, log `state/`).
3. T2: ltxv 720p QA (minutes) — after T1.
4. T3: 2512-Q6 image QA (minutes) — after download + T2.
5. T4: vision subagent compares (montages + 3 parallel codemode scripts).
6. T5: decisions + promotions + docs + harness commit (Item 5) + progress
   log. Append to the progress log after every milestone.

## Progress log

- **2026-11-22 (01:30 UTC)**: task doc created; code survey done (facts in
  State snapshot). Item 1 (hygiene) in progress.
- **2026-11-22 (01:35 UTC)**: **Item 1 DONE**: `scripts/workspace-cleanup.sh`
  (30d file age + empty-dir sweep, dry-run + --days flags, logged to
  ~/.local/state/workspace-cleanup/) + cron `30 4 * * *`. First live run
  removed 31 stale files (~50 MB, Aug–Oct test artifacts) — vision/phase6
  montages (today) untouched. Harness re-cloned to
  `/home/chuck/workspace/homelab-ai-harness` (891cad0).
- **2026-11-22 (01:40 UTC)**: Q6_K download started (17.95 GB,
  `unsloth/Qwen-Image-2512-GGUF` — same publisher/naming as the shipped Q4_0;
  → `/home/chuck/workspace/phase7_models/`). workflows.py: `QWEN_IMAGE_2512_MODEL`
  + `QWEN_IMAGE_EDIT_2511_MODEL` env overrides (defaults = Q4_0 pair);
  pipeline rebuild running (state/phase7_rebuild.log). qa_wan_phase1.py:
  `WAN_QA_LENGTH` env (default 81).
- **2026-11-22 (01:42 UTC)**: **T1 running**: wan 129f 720p QA (Q8_0, prompt
  d86aa323, pid 87118, log state/phase7_wan_129f_720p.log; ~90 min).
  LTXV 720p + 2512-Q6 QA wait for the GPU (serial).
