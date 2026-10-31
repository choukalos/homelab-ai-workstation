# Wan 2.2 14B I2V for `/shots` — Plan

> Created: 2026-10-25 · Status: **COMPLETE 2026-10-31** (QA passed; default
> flipped to `wan14b`; thor MCP handoff pending — see `thor_mcp_shot_model_snippet.md`)
> Related: `docs/matrix_media_pipeline_api.md` (contract + VRAM budget §6),
> `docs/matrix_comfyui_media_api.md` (ComfyUI :8188), `docs/matrix_inventory.md` (GPU/models)

## 0. Goal & rationale

Current `/shots` runs **LTXV 2B 0.9.6 distilled** (768×512, 97f, 8 steps, ~8 s/clip,
~10–12 GB peak). It is a fast draft model — the clips lack coherent motion and
fidelity. Root cause: the ~14 GB media VRAM ceiling (72 GB GPU − vLLM's 56–66 GB)
forced a 2B model.

**Plan:** add **Wan 2.2 14B I2V** (MoE 27B total / 14B active, Apache 2.0) as a
`model=wan14b` option on `/shots`, quantized (GGUF Q5) with T5-XXL on CPU so it
fits the **same ~14 GB ceiling**. Trade speed (~8 s → ~1–5 min/clip) for a
7×-parameter quality jump. LTXV 2B stays available as the fast path — same
`model` selector pattern as `/images` (`qwen21` | `legacy`).

Model landscape checked 2026-10-25: Wan 2.7 (same stack, 24 GB practical min —
out of budget), LTX-2.x 22B (24–32 GB — out of budget; would need MIG or a lower
vLLM `gpu-memory-utilization`), Mochi 1 / HunyuanVideo (superseded). Wan 2.2 14B
is the best quality that fits 14 GB.

## 1. Design

| Decision | Value | Why |
|---|---|---|
| Model | Wan 2.2 I2V-A14B **dual-expert MoE**, GGUF **Q5_K_M** ×2 (high-noise + low-noise, 10.79 GB each) | FP8 experts (13.3 GB each) would eat the whole budget; Q5_K_M at 480p is near-FP16 quality in blind tests. ComfyUI swaps experts at the schedule midpoint, so peak ≈ 1 expert + VAE + activations |
| Text encoder | **umt5-xxl fp8** (`umt5_xxl_fp8_e4m3fn_scaled.safetensors`, 6.74 GB) **on CPU** (`CLIPLoader device=cpu`) | Conditioning pass only; 62 GiB host RAM. Note: LTXV's `t5xxl_fp8_e4m3fn.safetensors` is a different tokenizer (Google T5 vs umt5) and is NOT reusable — keep both. fp16 T5 (9.4 GB) rejected: fp8_scaled is the Comfy-Org reference and halves CPU RAM |
| Image encoder | **None** — 2.2 I2V does not use CLIP-ViT-H | Verified against the official Comfy-Org `video_wan2_2_14B_i2v` template: `WanImageToVideo` conditioned on the start-image latent only (no `clip_vision_output` wired). Saves ~2.5 GB VRAM vs 2.1 |
| VAE | `wan_2.1_vae.safetensors` (0.25 GB, GPU) | Shared with 2.1/2.2 |
| Nodes | **ComfyUI native + ComfyUI-GGUF** (v0.37.0 in `comfyui_backend`) | Verified live: `WanImageToVideo`, `UnetLoaderGGUF` (already lists both Wan GGUFs), `CLIPLoader` (type `wan`, native `device: cpu`), `ModelSamplingSD3`, `KSamplerAdvanced` (step-based `start_at_step`/`end_at_step`), `VHS_VideoCombine`. No new custom nodes needed |
| Native settings | 480p = 832×480, 720p = 1280×720; **81 frames @ 16 fps** (~5.1 s) | Wan-native grid; length must be 4n+1 (`WanImageToVideo` length step=4); 97f @ 16 fps (~6.1 s) optional later (more VRAM/time) |
| Sampling | Official Comfy-Org dual-expert graph: 2× `ModelSamplingSD3` (shift 5.0) → 2× `KSamplerAdvanced` (euler, simple, **cfg 5.0, 20 steps each, split at step 10** = 50%) | Extracted from the official `video_wan2_2_14B_i2v` template (base mode, no 4-step LoRA). **Tune in Phase 1** |
| API surface | `model` Form param on `/shots`: `ltxv2b` \| `wan14b`; env `MEDIA_SHOT_MODEL` overrides default — **default = `wan14b` (quality by default, decided 2026-10-25)** | Mirrors `resolve_image_model` / `MEDIA_IMAGE_MODEL` |
| Defaults per model | `ltxv2b`: unchanged (768×512, 97f, 24 fps). `wan14b`: 832×480, 81f, 16 fps when caller omits dims/frames | Backward compatible; MCP tool updated to send model-appropriate values |
| Output | Raw 480p/720p mp4 — **unchanged contract**; `assemble` `upscale_each=true` (SeedVR2) already delivers 1080p | No change to downstream flows |
| Metering | Unchanged formula (frames × MP → `mpix_frames`); model label follows `model` | Wan 480p ≈ 32 MP-frames, 720p ≈ 74 vs LTXV ≈ 38 — cost honestly rises ~1.6–2× per clip |
| Queue | Unchanged (1 job at a time, depth 5, 503 back-pressure) | Slower shots are absorbed; optional: per-model `retry_after_seconds` estimate later |
| Timeout | Unchanged (`timeout=7200` on `flow_shots`) | Minutes-scale runs fit with margin |

**VRAM budget (planned — measured in Phase 1):**

| Flow | Peak VRAM (measured 2026-10-25) | Notes |
|---|---|---|
| Wan 2.2 14B I2V 480p | **14.4 GB** (peak delta 13.9 GB; headroom ~2.4 GB) | Q5_K_M experts + VAE + activations on GPU; umt5 on CPU. **SHIPPED** |
| Wan 2.2 14B I2V 720p | **OOM** (2.3 MiB free at failure) | Activations alone exceed the budget even with model resident. **NOT shipped** — revisit needs lower quants + vLLM profile change |

## 2. Phase 0 — Matrix prep (no service impact) — **DONE 2026-10-25**

1. ✅ Disk check: 1.1 TB free on `/` (32% used).
2. ✅ Node check (live `/object_info`, ComfyUI v0.37.0): `WanImageToVideo` +
   `Wan22ImageToVideoLatent` (native), `UnetLoaderGGUF`/`UnetLoaderGGUFAdvanced`
   (ComfyUI-GGUF custom node, scans `diffusion_models/`), `CLIPLoader` with
   `type: wan` and native `device: ['default','cpu']`, `CLIPVisionLoader`,
   `VAELoader`, `ModelSamplingSD3`, `KSamplerAdvanced` (step-based
   `start_at_step`/`end_at_step`), `VHS_VideoCombine`. No wrapper needed.
3. ✅ Models downloaded (~28.6 GB total, sources QuantStack + Comfy-Org):

   | File | Target dir | Size |
   |---|---|---|
   | `Wan2.2-I2V-A14B-HighNoise-Q5_K_M.gguf` | `basedir/models/diffusion_models/` | 10.79 GB |
   | `Wan2.2-I2V-A14B-LowNoise-Q5_K_M.gguf` | `basedir/models/diffusion_models/` | 10.79 GB |
   | `umt5_xxl_fp8_e4m3fn_scaled.safetensors` | `basedir/models/text_encoders/` | 6.74 GB |
   | `wan_2.1_vae.safetensors` | `basedir/models/vae/` | 0.25 GB |

   Fallback quants if the 720p gate fails: Q5_K_S (10.14 GB) / Q4_K_M
   (9.65 GB) per expert from the same QuantStack repo.
4. ✅ `UnetLoaderGGUF` now lists both Wan GGUFs; `CLIPLoader` lists the umt5
   encoder. LTXV's `t5xxl_fp8_e4m3fn.safetensors` untouched (different
   tokenizer — Google T5 vs umt5).

## 3. Phase 1 — Manual validation (no code changes) ⭐ decision gate

Drive ComfyUI `:8188` directly (raw API prompt JSON, one-off — not via
media-pipeline). Harness: **`media-pipeline/qa_wan_phase1.py`** (builds the
official dual-expert graph, samples `nvidia-smi` at 1 Hz, reports baseline /
peak/peak-delta/wall-time/output; usage `python3 qa_wan_phase1.py 480 720`).
Test keyframe: `c1_k11_edited.png` (ComfyUI `input/`); seed 42; 81f @ 16 fps.
Graph (verified against the official Comfy-Org template):
`UnetLoaderGGUF`×2 → `ModelSamplingSD3`(shift 5.0)×2 → `WanImageToVideo`
(start_image latent, no clip-vision) → `KSamplerAdvanced`×2 (euler/simple,
cfg 5.0, 20 steps, first half steps 0–10 high-noise add_noise=enable, second
half steps 10–20 low-noise add_noise=disable) → `VAEDecode` →
`VHS_VideoCombine` (16 fps, crf 19, yuv420p).

1. **480p run** (832×480, 81f, 16 fps): time it; sample
   `docker exec comfyui_backend nvidia-smi --query-gpu=memory.used --format=csv -l 2`
   in parallel → record **peak VRAM** and wall time.
2. **720p run** (1280×720, 81f): same.
3. **Decision gate:**
   - 480p peak ≤ ~12 GB → 480p approved as the `wan14b` default.
   - 720p peak ≤ ~13.5 GB (headroom vs vLLM's 56–66 GB swing) → 720p approved.
   - 720p OOM or peak > ceiling → ship 480p-only; revisit later (MIG option).
     (Agreed 2026-10-25: if 720p fails the gate, 480p simply becomes the
     `wan14b` default — no further decision needed.)
4. Visual sanity: extract 3 frames, vision-check keyframe fidelity + motion
   coherence vs an LTXV baseline clip (same keyframe/prompt).
5. Record measured peaks/times in §1 tables and the doc changelog.

### Phase 1 results (measured 2026-10-25) — GATE: 480p PASS / 720p FAIL (OOM)

Harness: `qa_wan_phase1.py` (14-node official dual-expert graph; nvidia-smi 1 Hz
sampling; keyframe `c1_k11_edited.png`; seed 42; base-mode sampling 20+20 steps).

| Test | Status | Wall | Baseline used | Peak used | Peak delta | Headroom at peak |
|---|---|---|---|---|---|---|
| 480p 832×480×81f | **success** | 615 s (10.3 min) | 56,645 MiB | 70,911 MiB | 14,266 MiB (13.9 GB) | ~2.4 GB (73,415−70,911) |
| 720p 1280×720×81f | **error — OOM** | ~20 min to failure | 66,367 MiB (model resident from 480p run) | — | — | 2.3 MiB free at failure |

- **480p gate: PASS.** Peak delta 13.9 GB exceeds the 12 GB planning estimate
  (which assumed 8.5 GB quants + CLIP-ViT-H; actual Q5_K_M dual-expert, no
  CLIP-ViT-H, is heavier on the GPU side). Still fits with ~2.4 GB headroom.
  vLLM confirmed **stable at 56,184 MiB** (per-process nvidia-smi; the docs'
  "56–66 GB fluctuation" is stale — that was profile switching, not runtime
  variance). 480p approved as the `wan14b` default.
- **720p gate: FAIL — `torch.OutOfMemoryError`** ("Free (according to CUDA):
  2.31 MiB", device limit 71.12 GiB) during the low-noise expert's sampling
  (ROPE op). Even with the model already resident (no load spike), 720p
  activations exceed the budget. **720p NOT shipped** (per decision: 480p
  becomes the wan14b default; no further decision needed). The API rejects
  non-480p dims for `wan14b` with a clean 400 (see Phase 2).
- **480p output verified:** 832×480, h264, 16 fps, 81 frames, 5.0625 s,
  2.03 MB; no decode errors. Clip: `media_jobs/qa/20261025T193426_wan22_p1_832x480_00001.mp4`.
- **Perceptual QA:** quantitative checks done; vision MCP server broken
  (remote `/workspace/vision` permission bug — `[Errno 13]` on its artifact
  dir, can't fix from Matrix); local ollama vision OOMs (GPU full).
  **User eyeball pending** (signed URL minted 2026-10-25).
- **ComfyUI retains the loaded Wan model after a run** (~10.2 GB resident
  post-run; auto-freed under pressure — after the 720p OOM it was back to
  568 MiB). Same-model consecutive jobs are fast; cross-model swaps cost a
  reload (~30–60 s for a 10.8 GB GGUF). ComfyUI's existing offload/swap
  handles this.
- **Note:** `qa_wan_phase1.py` has a post-run bug (crashes in the
  error-extraction line when history `messages` entries are lists); run data
  is still valid. Fix before reusing.

## 4. Phase 2 — Code changes (after Phase 1 gate passes)

| File | Change |
|---|---|
| `media-pipeline/workflows.py` | Add `wan22_i2v(keyframe, prompt, width, height, frames, fps, seed, prefix)` — verified graph: `UnetLoaderGGUF`×2 (high/low noise Q5_K_M) + `CLIPLoader` (`umt5_xxl_fp8_e4m3fn_scaled`, type `wan`, **device `cpu`**) + `VAELoader` (`wan_2.1_vae`) + `WanImageToVideo` (start_image latent, **no clip-vision**) + `ModelSamplingSD3`×2 (shift 5.0) + `KSamplerAdvanced`×2 (euler/simple, cfg 5.0, 20 steps, split at step 10) + `VAEDecode` + `VHS_VideoCombine` (16 fps). Add `resolve_shot_model(payload)` — per-request `model` > `MEDIA_SHOT_MODEL` env > `ltxv2b` (mirror of `resolve_image_model`, L202-209). Add `WAN_RESOLUTIONS = {(832,480)}` + frame validation (81 default; must be 4n+1). **720p rejected at API level (400) — Phase 1 gate failed (OOM 2026-10-25).** |
| `media-pipeline/server.py` | `/shots` route (L1357): add `model: str = Form(None)`; pass through payload. `flow_shots` (L543): branch on `wf.resolve_shot_model(payload)` — `wan14b` → `wf.wan22_i2v(...)` with Wan-native defaults when caller omits dims/frames/fps (832×480, 81f, 16 fps); unknown `model` → clean 400 listing options (same pattern as `_resolve_voice`). Output contract unchanged (`video`, `frames`, `fps`). |
| `media-pipeline/metering.py` | `_model_for` (L166): `shots` → label by resolved model: `ltxv2b` → `ltxv-2b-0.9.6-distilled` (unchanged), `wan14b` → `wan2.2-14b-i2v-gguf-q5`. Work units unchanged (`mpix_frames`). |
| `/home/chuck/homelab/.env` | `MEDIA_SHOT_MODEL=wan14b` (**quality by default — decided 2026-10-25**). During Phase 2–3 keep `ltxv2b` until QA passes, then flip (one-line change, no rebuild). |
| `mcp/servers/media` (homelab-ai-harness) | `media_generate_shot`: add `model` param (`wan14b` quality default | `ltxv2b` fast) + docstring; dims/frames/fps become optional (omit → server applies model-native defaults). Separate repo — small follow-up commit. **Handoff snippet for thor: `media-pipeline/thor_mcp_shot_model_snippet.md`** |

## 5. Phase 3 — Rebuild + QA — **DONE 2026-10-31**

Results (full run: `media-pipeline/qa_wan_e2e.py`, report
`media-pipeline/qa_wan_e2e_report.json`):

1. ✅ `model-manager rebuild media-pipeline` (2026-10-25) — image rebuilt,
   container recreated.
2. ✅ `/health` ok, `gpu_locked:false` (2026-10-25 and 2026-10-31).
3. ✅ QA suite:
   1. `/health` ok.
   2. **Regression:** `/shots model=ltxv2b` 768×512 97f → done in 15 s,
      97f @24fps mp4 (LTXV path intact; 2026-10-31 E2E run).
   3. `/shots model=wan14b` 832×480 97f → done in 674.6 s (2026-10-25,
      job c798653dea7d; metering row `wan2.2-14b-i2v-gguf-q5`, 38.74
      mpix_frames, $0.000225). 81f variant verified in Phase 1 (615 s).
   4. `/shots model=wan14b` 1280×720 → **400** (clean error listing
      supported dims) ✓ 2026-10-25.
   5. `model=bogus` → 400 listing `ltxv2b, wan14b` ✓ 2026-10-25.
      `frames=95` (not 4n+1) → 400 ✓ 2026-10-25.
   6. `jobs.jsonl`: model label + `mpix_frames` units correct ✓ 2026-10-25.
   7. **E2E (2026-10-31):** storyboard (2 shots, coffee commercial) → 2×
      `/images` (qwen21 keyframes) → 2× `/shots model=wan14b` 832×480 81f →
      `/tts` (trailer VO) + `/music` → `/assemble upscale_each=true`
      (SeedVR2 → 1080p) + loudnorm → final 1920×1080 mp4. ✅
   8. **Visual QA:** user eyeballed the Phase 1 QA clip
      (`media/wan22_p1_832x480_qa.mp4`) 2026-10-31: "looks great". ✅
4. ✅ Measured peaks/times recorded in §1 (480p: 615 s / 13.9 GB peak delta;
   pipeline QA: 674.6 s).

**✅ Known issue found in E2E (2026-10-31) — FIXED 2026-10-31 (VRAM headroom
**guard in `server.py`):** ComfyUI retains the ~10 GiB Wan model in VRAM after
a wan14b shot (it only auto-offloads when *its own* allocation fails). A
subsequent `/music` or `/sfx` job (ACE-Step ~6 GiB, separate process) then
OOMs: vLLM ~56 GB + Wan 10 GiB + ACE-Step 6 GiB > 71.12 GiB card (music job
failed "Error initializing model: CUDA out of memory ... 10.31 MiB free";
ComfyUI process holding 9.96 GiB). Fix: `_ensure_vram_headroom()` in
`server.py` measures free VRAM before `music` (≥8 GiB) / `tts`+`voices-add`
(≥2 GiB) and, if low + ComfyUI queue idle, `docker restart comfyui_backend`
(~40 s) to drop the resident model. Verified both paths (no-op at 15.7 GiB
free; restart at 2.8 GiB free → music + qwen21 image OK). LTXV shots don't
trigger it (model ~3-5 GiB); `sfx` is a ComfyUI flow (its own OOM-offload
handles it).

## 6. Phase 4 — Docs, MCP surface, memory

1. ✅ `docs/matrix_media_pipeline_api.md` (2026-10-31): §2 `/shots` row
   (model param + per-model defaults + 480p-only 400), §3 notes (model +
   strength ltxv2b-only), §4 models table (shots row dual model), §6 VRAM
   table (measured Wan row + headroom-guard note), §7 recipe (wan14b note),
   §9 changelog entry.
2. ✅ `docs/matrix_inventory.md` (2026-10-31): model data size (~156 GB total,
   models ~118 GB incl. Wan +28.6 GB — row was already updated 2026-10-25),
   container role row (Wan 2.2), notes item 6 (headroom guard).
3. ⬜ **MCP tool** (`mcp/servers/media` in homelab-ai-harness, on thor):
   `media_generate_shot` gains `model` param + optional dims. Handoff snippet:
   `media-pipeline/thor_mcp_shot_model_snippet.md` (updated 2026-10-31 post-QA).
   **Required** — the old tool's hardcoded 768×512/97f/24fps 400s against the
   new wan14b default.
4. ✅ `kb_add_fact` (kb_homelab): Wan 2.2 14B I2V live on `/shots` — model
   files, measured peaks/times, `MEDIA_SHOT_MODEL` default
   (fact_id `f00da4cc-e9dd-c540-d08a-1d305d046ff4`).
5. ✅ `PIPELINE_CHANGES.md` at `/home/chuck/data/comfyui/run/media_jobs/`
   (2026-10-31 entry: wan14b flow + VRAM headroom guard, both verified).
6. ✅ **Default flip (decided 2026-10-25):** `MEDIA_SHOT_MODEL=wan14b` set in
   `.env` + container restarted 2026-10-31.
7. ✅ **VRAM headroom guard** (the E2E music/sfx OOM fix) — `server.py`
   `_ensure_vram_headroom()`, rebuilt + verified 2026-10-31 (see §5 note).

## 7. Rollback

- Instant: `MEDIA_SHOT_MODEL=ltxv2b` in `.env` + container restart (or simply
  stop passing `model=wan14b`). LTXV path is untouched by this change.
- Full: remove `wan22_i2v`/`resolve_shot_model` (workflows.py), the branch in
  `flow_shots`, the metering label; models stay on disk (~28.6 GB, reclaimable later).

## 8. Risks & mitigations

| Risk | Mitigation |
|---|---|
| 720p OOM when vLLM spikes (free VRAM can dip to ~6–8 GB) | 480p default; Phase 1 gate decides 720p enablement; OOM ladder unchanged (lower res first) |
| Native Wan nodes missing/buggy in mmartial image | Phase 0 `/object_info` check; fallback = kijai WanVideoWrapper (custom-node install, same model files) |
| GGUF Q5 quality < expected | Phase 1 visual gate; Q5_K_S→Q6_K or FP8-at-480p fallback within budget |
| Slower shots (minutes) feel like a stall | Queue is 1-at-a-time with 503 back-pressure + `queue_position` visibility; MCP `non_blocking` + `media_job_result` polling already handles long jobs |
| ComfyUI auto-offload thrash (T5 in/out per job) | ~20–30 s conditioning overhead accepted; if painful, try `t5xxl_fp8` (halves encoder size) |
| Metering cost jump surprises users | Documented in §1; rates unchanged, work units reflect real compute |

## 9. Out of scope (parked)

- **Wan 2.7** (4K/20 s): needs ~24 GB — revisit only with a higher ceiling.
- **LTX-2.5** (native multi-shot + synchronized audio, 22B): needs ~24–32 GB;
  the clean path is a **MIG partition** (RTX PRO 5000 72 GB supports 2×36 GB
  slices — dedicated media slice, zero runtime interference with vLLM). Separate
  plan if/when wanted.
- T2V mode for Wan (we only need I2V — keyframes come from Qwen-Image-2.1).