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

## Phase 1 — matrix prep (run ON matrix, 192.168.4.55)

```bash
cd /home/chuck/homelab && git pull
# one-shot: ComfyUI v0.37.0 (pinned) + venv deps + 17.3 GB weights + verification
bash scripts/qwen21_upgrade_matrix.sh
```

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

## Phase 2 — pipeline cutover (run ON matrix, after Phase 1 is green)

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

## Phase 3 — QA (run ON matrix after cutover)

- [ ] t2i default (qwen21): `curl -s -X POST localhost:8189/images -H 'Content-Type: application/json' -d '{"prompt":"a red bicycle leaning on a brick wall, soft morning light","width":1280,"height":720,"seed":42}'`
      → job done; image legible; **time it** (expect ~30–120 s);
      `docker stats` / dcgm peak during run (expect < 12 GB ComfyUI-side)
- [ ] t2i legacy parity: same prompt with `"model":"legacy"` → still works
- [ ] edit default (qwen21): upload a keyframe + instruction
      (`curl -F file=@k.png -F prompt="make it night" localhost:8189/images/edit`)
      → works; time it
- [ ] edit with references: `references=media_jobs/<jid_from_step_1>/<png>`
      (single ref), then 3 refs → canvas follows the edit target, refs influence
      identity; verify with vision QA
- [ ] steps clamp: `steps: 4` on qwen21 → runs at 10 (job log line), metering
      work units = 10 × MP
- [ ] full regression: `python3 media-pipeline/qa_part1.py` (38 checks —
      shots/TTS/music/SFX/upscale/assemble/trim/freeze/caption unaffected)
- [ ] vLLM sanity: matrix-coder chat still fine (vram untouched)

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
- [ ] **Phases 1–3 matrix run (PENDING — no SSH from thor; run on matrix or
      via matrix agent):** `git pull` + `bash scripts/qwen21_upgrade_matrix.sh`
      (Phase 1) → `model-manager rebuild media-pipeline` + health (Phase 2) →
      Phase 3 QA checklist below. ComfyUI still at v0.22.0 as of 2026-09-23.
- [ ] watch for a Qwen-Image-2.1 Lightning/distilled LoRA (LightX2V et al.) —
      if one lands, re-test 4–8 steps and consider dropping the clamp floor
- [ ] **only after** 1–2 weeks of green QA: delete legacy models
      (`qwen_image_2512_q4.gguf`, `qwen_image_edit_2511_q4.gguf`,
      `qwen_2.5_vl_7b_fp8_scaled.safetensors`, both Lightning LoRAs ≈ 31 GB)
      and drop the `model=legacy` code path. Keep the old VAE
      (`qwen_image_vae.safetensors`) — the legacy path needs it until removal.
- [ ] metering: confirm `COST_TOTAL`/`WORK_UNITS_TOTAL` series show the new
      model label; Grafana dashboard if any references the old label

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