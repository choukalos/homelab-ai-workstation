# Media pipeline upgrade: Qwen-Image-2.1 (2026-09-23)

> Upgrade the image create + edit paths to **Qwen-Image-2.1** (7B single-stream
> DiT, unified T2I + editing, up to 10 reference images). The legacy
> Qwen-Image-2512 / Qwen-Image-Edit-2511 (GGUF + Lightning) paths are **kept as
> a backup** and selectable per request or via one env var.
>
> **Decision (2026-09-23):** default the pipeline to the new path
> (`qwen21`); switch back with `MEDIA_IMAGE_MODEL=legacy` in
> `/home/chuck/homelab/.env` + restart (or per-request `"model": "legacy"`).

## Why (verified 2026-09-23)

- Qwen-Image-2.1 is a **new 7B architecture** (not a quant of the 20B 2512/2511):
  unified create + edit in one model, native RGBA, better typography + portrait
  lighting, **up to 10 reference images** in the edit flow (character/product
  consistency across storyboard shots — the killer feature for the commercial
  pipeline).
- ComfyUI **v0.37.0** (published 2026-09-21) is the day-0 release
  ("feat: Qwen-image 2.1 support (CORE-423)"). Live matrix is **v0.22.0** —
  update required. The `mmartial/comfyui-nvidia-docker:latest` image
  (2026-09-14) is also pre-2.1, so a docker image pull is NOT the fix.
- ComfyUI source is a git checkout on the persistent volume
  (`/home/chuck/data/comfyui/run/ComfyUI`, venv `/comfy/mnt/venv`) → pinned
  `git checkout v0.37.0` + `pip install -r requirements.txt` + restart.
  Rollback = `git checkout v0.22.0` + restart (venv pip changes are additive).
- Weights (Comfy-Org/Qwen-Image-2.1, int8_convrot — sized for our 12 GB budget):
  | file | where | size |
  |---|---|---|
  | `qwen_image_2.1_int8_convrot.safetensors` | `basedir/models/diffusion_models/` | 7.26 GB |
  | `qwen3vl_8b_int8_convrot.safetensors` | `basedir/models/text_encoders/` | 9.35 GB |
  | `qwen_image_2.1_vae_bf16.safetensors` | `basedir/models/vae/` | 0.68 GB |
  | **total** | | **~17.3 GB** (matrix has ~1.2 TB free) |
- VRAM: fits the 12 GB budget at **~1 MP** (our 1280×720 / 1344×768 keyframes).
  Native 2K (2048²) would blow the budget — keep the existing
  "1 MP render → SeedVR2/4xUltrasharp upscale" pattern.
- Speed: no distilled 4-step LoRA at launch (official 25–40 steps). Expect
  ~30–120 s per keyframe vs the legacy 15–40 s. Server clamps qwen21 `steps`
  to [10, 50] (default 25) so stale 4-step callers can't produce garbage.

## Phase 0 — pipeline code (thor, this repo)

- [x] `workflows.py`: `qwen_image_21_t2i()` + `qwen_image_21_edit()` builders
      (API-format graphs unwound from the official Comfy-Org workflow
      templates: `TextEncodeQwenImage21`, `QwenImage21Cache`,
      `UNETLoader`/`CLIPLoader`(type=qwen_image)/`VAELoader`, KSampler
      25 steps / cfg=1 / euler / simple; edit canvas latent = encoded input
      image, i.e. the template's switch=False default)
- [x] `workflows.py`: `resolve_image_model(payload)` + `qwen21_steps(payload)`
      — single source of truth for model selection (per-request `model` >
      `MEDIA_IMAGE_MODEL` env > built-in `qwen21`) and step clamping
- [x] `server.py`: `flow_images` / `flow_images_edit` branch on model;
      legacy path unchanged (Lightning LoRA, 4/8 steps)
- [x] `server.py`: `POST /images/edit` gains `references` form field
      (comma-separated; each entry = filename already in ComfyUI `input/` OR a
      media_jobs-relative path like `media_jobs/<jid>/<file>.png`, staged into
      ComfyUI input/ server-side; max 9 refs + 1 edit target = 10)
- [x] `metering.py`: model label per selected model
      (`qwen-image-2.1` / `qwen-image-2512` / `qwen-image-edit-2511`),
      work units use the effective (clamped) step count
- [x] `scripts/qwen21_upgrade_matrix.sh` — matrix-side prep (Phase 1)
- [x] `update_media_mcp_todo.md` — MCP client follow-up (Thor)
- [x] local unit check of the workflow builders (JSON structure)
- [x] docs: `models/profiles/comfyui.yaml`, `docs/matrix_images_mode.md`,
      `docs/matrix_media_pipeline_api.md` §4 (model table + new params)
      (all updated 2026-09-23; re-verified 2026-09-23: builders + clamp + model
      resolution unit checks pass on thor)

## Phase 1 — matrix prep (run ON matrix, 192.168.4.55) — **DONE 2026-09-23 20:25–20:35**

```bash
cd /home/chuck/homelab && git pull
# one-shot: ComfyUI v0.37.0 (pinned) + venv deps + 17.3 GB weights + verification
bash scripts/qwen21_upgrade_matrix.sh
```

Executed result: ComfyUI v0.22.0 → **v0.37.0** (commit `73c9bad4`), both
qwen21 nodes registered, vLLM healthy, 3 weights verified (7.26 + 9.35 + 0.68 GB).
Script fixes from the run (commit `a212759`): git/venv ops via `docker exec -u
comfy` (default exec user is uid 1025 → "dubious ownership"), version compare
against `${TAG#v}` (ComfyUI reports `0.37.0`, not `v0.37.0`), log fallback to
`/tmp/qwen21_upgrade.log` when the run dir isn't writable by the invoking user.

What the script does (idempotent; re-runnable):
1. Records the current ComfyUI commit (rollback ref) in
   `/home/chuck/data/comfyui/run/qwen21_upgrade.log`
2. `git fetch --tags && git checkout v0.37.0` in `/comfy/mnt/ComfyUI` (as `comfy`)
3. `pip install -r requirements.txt` in `/comfy/mnt/venv` (as `comfy`)
   (v0.37.0 adds comfy-kitchen 0.2.35 / comfy-aimdo 0.5.5)
4. `docker restart comfyui_backend`
5. Verifies: `/system_stats` reports v0.37.0; `/object_info` has
   `TextEncodeQwenImage21` + `QwenImage21Cache`; vLLM on :8000 still healthy
6. Downloads the 3 int8_convrot weights (curl, HF resolve URLs) into basedir
   as the `comfy` user; verifies sizes
7. **Does NOT touch media-pipeline** (rebuild is Phase 2)

⚠️ Custom-node risk: v0.22.0 → v0.37.0 is 15 releases. If VHS/SeedVR2/MMAudio
nodes break, roll back: `docker exec -u comfy comfyui_backend git -C /comfy/mnt/ComfyUI checkout v0.22.0 && docker restart comfyui_backend`
(then `pip install -r requirements.txt` again to restore the old venv state).

## Phase 2 — pipeline cutover (run ON matrix, after Phase 1 is green) — **DONE 2026-09-23**

`model-manager rebuild media-pipeline` (twice — second time after the
`resolution` fix below); `curl -s http://localhost:8189/health` → `ok: true`.

```bash
# .env: MEDIA_IMAGE_MODEL is unset by default -> code default is qwen21 (new path)
# To start on legacy instead, add: MEDIA_IMAGE_MODEL=legacy
model-manager rebuild media-pipeline     # rebuild image from source + recreate
curl -s http://localhost:8189/health     # ok:true
```

**Switch-back hook (any time):** set `MEDIA_IMAGE_MODEL=legacy` in
`/home/chuck/homelab/.env`, `model-manager rebuild media-pipeline` (or
`docker compose -f compose/comfyui.yml --profile image up -d` to just
recreate). Per-request override: `"model": "qwen21" | "legacy"` on
`POST /images` (JSON) and `model` form field on `POST /images/edit`.

## Phase 3 — QA (run ON matrix after cutover) — **DONE 2026-09-23 20:40–20:50**

- [x] t2i default (qwen21): job `503ace730583` → done in **16 s** (warm cache;
      first run after model load is slower); 1280×720 PNG 2.17 MB; content
      verified (warm morning scene, red-dominant 44.5%); `model: qwen21` label
- [x] t2i legacy parity: job `e04948650bc3` (`model: legacy`, 4 steps) → done ~30 s
- [x] edit default (qwen21): job `f905abf87192` (plain, no refs) → done ~40 s,
      `references: 0`
- [x] edit with references: job `68b1b13271c2` (1 ref staged via docker cp from
      media_jobs) → done ~40 s; wall→white verified (mean RGB 97/74/58 →
      210/206/205); output canvas 1376×768 (= edited image resized to ≈1024²,
      expected `resolution=1024` behavior)
- [x] steps clamp: job `f7b2ed50819b` (`steps: 4` on qwen21) → ran at 10
      (metering delta 9.216 = 0.9216 MP × 10 steps, not × 4)
- [x] full regression: all 43 pipeline workflow classes registered in v0.37.0
      (GGUF/MMAudio/SeedVR2/VHS custom nodes intact; LTXV 28 nodes); TTS +
      ACE-Step workers use isolated venvs (`venvs/venv-tts`, `ACE-Step-1.5/.venv`)
      → unaffected by the venv requirements bump
- [x] vLLM sanity: `:8000/health` HTTP 200; GPU idle 68185/73415 MiB (vLLM ~56 GB
      + ComfyUI model cache ~12 GB — within the ~70 GB peak gate)

**Pipeline fix found during QA:** `TextEncodeQwenImage21` requires a
`resolution` input in v0.37.0 (API validation rejects the prompt without it).
Added `resolution=1024` (node default) to both builders in
`media-pipeline/workflows.py`; rebuilt pipeline; validation + all jobs green.

## Phase 4 — follow-ups

- [x] MCP client update (Thor): `media-mcp-client/` updated 2026-09-23 — step
      defaults 4/8 → 25, `model` + `references` exposed on the image tools,
      HANDOFF.md embedded code + API tables synced, client payload construction
      unit-tested. **Deploy:** the live media-mcp server (thor) needs the two
      updated files copied in + restart; pipeline is backwards compatible so
      this can land before or after Phases 1–3.
- [x] docs sweep: `comfyui.yaml` profile, `matrix_images_mode.md`,
      `matrix_media_pipeline_api.md` §4 + §6 (metering model labels),
      `matrix_comfyui_media_api.md` (rewritten 2026-09-23: qwen21 default
      flows §4–5 with verified graphs, legacy demoted to §6, model inventory +
      VRAM + error handling updated)
- [x] **Phases 1–3 matrix run — DONE 2026-09-23 (ran ON matrix, agent session
      with local access):** `git pull` + `bash scripts/qwen21_upgrade_matrix.sh`
      (ComfyUI v0.22.0 → v0.37.0, commit `73c9bad4`, weights 17.2 GB verified,
      vLLM healthy) → `model-manager rebuild media-pipeline` (twice: once after
      the `resolution` fix) → Phase 3 QA all green (see above).
      Script fixes landed as commit `a212759`: `docker exec -u comfy` (dubious
      ownership), version-compare without `v` prefix, log fallback to /tmp.
- [ ] watch for a Qwen-Image-2.1 Lightning/distilled LoRA (LightX2V et al.) —
      if one lands, re-test 4–8 steps and consider dropping the clamp floor
- [ ] **only after** 1–2 weeks of green QA: delete legacy models
      (`qwen_image_2512_q4.gguf`, `qwen_image_edit_2511_q4.gguf`,
      `qwen_2.5_vl_7b_fp8_scaled.safetensors`, both Lightning LoRAs ≈ 31 GB)
      and drop the `model=legacy` code path. Keep the old VAE
      (`qwen_image_vae.safetensors`) — the legacy path needs it until removal.
- [x] metering: `media_work_units_total{kind="mpix_steps"}` +
      `media_cost_usd_total` confirmed on `:8189/metrics` with the new 25-step
      defaults (images: 26.73 = 0.92 MP×25 + 0.92 MP×4 legacy; images_edit:
      61.21 incl. 1.057 MP×25×2 + 0.92 MP×8); job outputs carry `model:` label
      (qwen21/legacy). Grafana dashboard: none references the old label.

## Rollback (fast path)

1. **Pipeline-level (no ComfyUI rollback):** `MEDIA_IMAGE_MODEL=legacy` +
   recreate container. New model files stay on disk (harmless).
2. **ComfyUI-level:** `git checkout v0.22.0` + `pip install -r
   requirements.txt` + `docker restart comfyui_backend`. qwen21 jobs will then
   fail with "node not found" — so do step 1 first.

## Open questions

- Native 2K under a *relaxed* VRAM budget (e.g. vLLM down): out of scope now;
  revisit if the 12 GB cap is ever raised.
- PE prompt-rewriter (Qwen3.5-VL 9B int8, 9.47 GB) — optional; skip until the
  base flow is proven (our storyboard visuals are already detailed).
- `QwenImage21Cache` prefix-caching benefit is only realized when consecutive
  jobs share prompt prefixes — no action needed, it's wired in the edit flow.