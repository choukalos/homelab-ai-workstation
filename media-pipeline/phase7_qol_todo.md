# Phase 7 — Media QoL: longer clips, sharper keyframes, 720p drafts + workspace hygiene — TASK

> Created: 2026-11-22 · Status: **COMPLETE** (2026-11-22 — verdicts below;
> pending: Chuck's harness push + mcp_media rebuild on Thor)
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

- [x] QA gate: `qa_wan_phase1.py 720 --gpu 1` with `WAN_QA_LENGTH=129`
      (env-parametrize LENGTH — small edit). Result: peak 14,720 MiB total /
      13,457 delta (PASS, below 81f's 19,577), wall 115.1 min, output
      1280×720 129f @16fps verified.
- [x] Quality: 2 frames vs the 81f baseline clip (montage + vision subagent).
      **FAIL** — softer fur, texture noise, dark-bg banding; pose drift
      (different latent shape).
- [x] Docs: API §4 (frames param: 81 recommended, 129 experimental) + §6
      (129f 720p peak + wall) + changelog; harness: mcp_media
      `media_generate_shot` description + media-film contract.
- [x] VRAM passed, so no cap added — API stays open (4k+1), 129f documented
      as experimental with the QA evidence; 81f remains the recommended max.

## Item 3 — QoL-2: sharper keyframes (Qwen-Image-2512 Q6_K)

Goal: a higher-quant 2512 keyframe option (Q6_K ~15–16 GB) for better I2V
conditioning, env-selectable; promote if QA shows a clear win.

- [x] Find the 2512 Q6_K GGUF on HF — `unsloth/Qwen-Image-2512-GGUF` (same
      publisher/naming as the shipped Q4_0), 17.95 GB; downloaded to
      `/home/chuck/workspace/phase7_models/`, docker cp'd into the container,
      verified via `object_info/UnetLoaderGGUF`.
- [x] workflows.py: `QWEN_IMAGE_2512_MODEL` env (default
      `qwen-image-2512-Q4_0.gguf`) on the t2i UNetLoaderGGUF node
      (+ `QWEN_IMAGE_EDIT_2511_MODEL` for the edit node); pipeline rebuilt.
- [x] QA: same keyframe prompt at 1280×720 via direct ComfyUI (qa_images_phase7.py):
      qwen21 48 s / 16,929 MiB; 2512-Q4 28 s / 20,513; 2512-Q6 22 s / 21,601.
      3-way montage → vision subagent: **qwen21 > Q6_K > Q4_0**.
- [x] Decision: **keep qwen21 default** (MEDIA_IMAGE_MODEL unchanged — the
      current default is already the best keyframe). Q6_K kept on disk as an
      opt-in `QWEN_IMAGE_2512_MODEL` legacy upgrade (marginal over Q4_0).
- [x] Docs: API §4 (images model row) + §6 (2512-Q4/Q6 peaks) + changelog;
      harness mcp_media `media_generate_image` description + media-film
      contract.

## Item 4 — QoL-3: LTXV 720p fast lane

Goal: 1280×720 drafts via `model=ltxv2b` (~8 s/clip), so dry-runs match final
framing.

- [x] QA gate: `/shots model=ltxv2b width=1280 height=720 frames=97 fps=25`
      (direct ComfyUI, nvidia-smi sampling): 18.0 s, peak 18,335 MiB /
      17,186 delta (PASS ≤ 22 GB), output **1280×704** (LTXV snaps height to
      ×32) h264 25fps 97f ≈ 3.88 s.
- [x] Quality: frame vs 768×512 draft (montage + vision subagent) — sharper,
      no new artifacts; usable 720p-class draft.
- [x] Docs: API §4 (ltxv2b row: 768×512 default + 1280×720 option, 704 out)
      + §6 (LTXV 720p peak) + changelog; harness mcp_media description +
      media-film contract.
- [x] No OOM — promoted as a supported option (768×512 stays the default).

## Item 5 — Thor (harness) changes — one commit

Repo: `/home/chuck/workspace/homelab-ai-harness` (fresh clone 2026-11-22).
- [ ] `mcp/servers/media/server.py` + `media_pipeline_client.py` + README:
      `media_generate_shot` (wan14b 129f = experimental, 81 recommended;
      ltxv2b 1280×720 option → 704 out), `media_generate_image` (2512-Q6
      legacy opt-in note).
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

## Verdicts (2026-11-22, QA + vision subagents)

| Item | Result | Decision |
|---|---|---|
| QoL-1 wan 129f @720p | VRAM PASS (peak 14,720 MiB total / 13,457 delta — *lower* than 81f's 19,577; ComfyUI offloads more aggressively on the longer latent), wall 115.1 min (~2× the 81f 56.8 min). **Quality FAIL** (vision, 2 frames): softer fur, texture noise, dark-bg banding vs the 81f baseline; pose drift (different latent shape). | **Rejected as a recommended option.** API stays open (129f is 4k+1-legal, no cap added); documented as *experimental* with the QA evidence; 81f remains the recommended max. |
| QoL-2 2512-Q6_K keyframes | All fit: qwen21 16,929 MiB/48 s, 2512-Q4 20,513/28 s, 2512-Q6 21,601/22 s @1280×720. **Vision ranking: qwen21 > Q6_K > Q4_0** (qwen21 most detail + natural light; Q6_K slightly less waxy than Q4). | **Keep qwen21 as the production keyframe default** (MEDIA_IMAGE_MODEL unchanged). Q6_K staged in diffusion_models + `QWEN_IMAGE_2512_MODEL` env opt-in documented (marginal legacy upgrade over Q4_0). |
| QoL-3 LTXV 720p | PASS: 1280×720 in → **1280×704 out** (LTXV snaps height to ×32), 18 s/clip, peak 18,335 MiB / 17,186 delta. Vision: sharper than the 512p draft, no new artifacts. | **Promoted** — 1280×720 documented as a supported ltxv2b draft option (768×512 remains default). |

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
- **2026-11-22 (03:24 UTC)**: **T1 DONE** (129f 720p QA): success, wall 6906.9 s (115.1 min),
  peak 14,720 MiB total / 13,457 delta (below the 81f 19,577 — ComfyUI offloads more
  on the longer latent), output wan22_p1_1280x720_00005.mp4 (1280×720, 129f @16fps
  verified by ffprobe). Log state/phase7_wan_129f_720p.log.
- **2026-11-22 (03:27 UTC)**: **T2/T3 DONE** (qa_images_phase7.py, log
  state/phase7_t2t3.log): ltxv-720p 18.0 s / 18,335 MiB (→1280×704 out); img-qwen21
  48 s / 16,929; img-2512-q4 28 s / 20,513; img-2512-q6 22 s / 21,601. 512p LTXV ref
  run 12 s. 4 montages built (phase7_vision/), served over LAN HTTP.
- **2026-11-22 (03:31 UTC)**: **T4 DONE** (3 vision subagents, parallel codemode,
  mcp_vision): verdicts in the table above. QoL-1 rejected (quality), QoL-2 keep
  qwen21 + Q6 opt-in, QoL-3 promoted. HTTP server killed.
- **2026-11-22 (03:35 UTC)**: T5 in progress: API docs + plan + harness commit.
- **2026-11-22 (04:00 UTC)**: **T5 DONE — Phase 7 COMPLETE.**
  - API doc: §2 /shots row + model note (fixed a stale "832×480 only" line
    left over from the 720p lift), §4 images/shots rows, §6 VRAM rows
    (2512 Q4/Q6, LTXV 720p, Wan 129f), §7 recipe, changelog entry.
  - add_card_plan.md: §4.3 items 2/7/8 closed with outcomes; Phase 7 boxes
    checked (Thor box pending push).
  - matrix_backup.md: diffusion_models 87 → 104 GB, source total 163 → 181 GB.
  - Harness commit `6fd88ca` (server.py descriptions, client docstrings,
    README rows + history, media-film SKILL.md + pipeline-contract.md) →
    **Chuck pushes + rebuilds the mcp_media container on Thor**.
  - Cleanup: redundant 18 GB workspace Q6_K copy deleted (container keeps
    its own; re-downloadable from HF); container test intermediates removed
    (129f evidence clip wan22_p1_1280x720_00005.mp4 + p7_* QA images kept);
    phase7_vision/ montages kept as evidence (15 MB — the new 30-day cron
    will sweep them). Workstation commit follows.
