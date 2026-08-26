# ComfyUI Image Generation Plan — Infographics + 720p→1080p in 12 GB VRAM

**Date:** 2026-08-24
**Goal:** Generate infographics with clear/legible in-image text (plus normal images) in ComfyUI, generating at 720p and upscaling to 1080p, all within a 12 GB VRAM budget while vLLM keeps the rest of the GPU.

---

## 1. Current State (verified 2026-08-24)

| Item | Value |
|---|---|
| GPU | NVIDIA RTX PRO 5000 72 GB Blackwell (sm_120), driver 595.71.05 |
| vLLM (qwen38) | ~58.2 GB VRAM → **~14.6 GB free** (the 12 GB budget leaves ~2.5 GB headroom) |
| ComfyUI | v0.22.0 at `/home/chuck/data/comfyui/run/ComfyUI`, data dir `/home/chuck/data/comfyui/basedir` |
| System RAM | 62 GB total, ~53 GB available (needed for model offloading) |
| Disk | 1.2 TB free (downloads total ~23 GB) |
| Existing models | SD1.5/SDXL-era checkpoints, orangemix VAE, **4x-UltraSharp (already have — the upscaler we want)** |
| Existing custom nodes | Manager, UltimateSDUpscale, VideoHelperSuite, etc. |
| Container | `comfyui_backend` (port 8188), currently `--reserve-vram 8` (i.e. "use up to 64 GB") — **must change** |

## 2. Model Choice (researched 2026-08-24)

### The landscape
- **Qwen-Image-2.0** (7B, released 2026-02-10) is the *ideal* model for this use case: professional typography rendering, 1k-token infographic instructions (PPTs/posters/comics), native 2K, unified gen+edit, #1 on AI Arena. **BUT the weights are NOT public yet** — API-only (HuggingFace `Qwen/Qwen-Image-2.0` returns 401 as of Aug 2026; community prediction markets put open release in H2 2026/H1 2027). → **Watch item, not actionable.**
- **Qwen-Image-2512** (20B MMDiT, Apache 2.0, 2025-12-31) is the best *available* open-weight model for text rendering (EN/ZH typography, posters, infographics). FP8 build is 20.4 GB (won't fit 12 GB) → use **GGUF Q4_0 (11.85 GB)**, the community-validated floor for good results on 12 GB cards.
- **Z-Image Turbo** (6B, 8 steps, Apache 2.0): fast fallback with decent text; full repo is ~33 GB bf16, so only worth it if a good GGUF/fp8 quant appears.
- **FLUX.2 [dev] 32B**: quality leader but non-commercial license + ~32 GB — ruled out. FLUX.2 [klein] 4B (Apache 2.0, ~8 GB) is a viable backup if Qwen-Image proves too slow.
- **Qwen-Image-Edit-2511** (20B, Apache 2.0): the best local *editor* — Q4_0 GGUF (11.9 GB) also fits 12 GB. Phase 2 for fixing/iterating infographics.

### Decision
**Primary: Qwen-Image-2512 GGUF Q3_K_M (9.93 GB) + Lightning 8-step LoRA**, upscaled with the existing 4x-UltraSharp.
(Q3_K_M chosen over Q4_0 (11.85 GB) on 2026-08-24 to guarantee the 12 GB fit with comfortable headroom; Q4_0 remains the upgrade path if text quality needs the extra precision.)

## 3. VRAM Budget (12 GB cap)

| Component | Size | Notes |
|---|---|---|
| Qwen-Image-2512 Q3_K_M GGUF | 9.93 GB | Weights; ~2 GB headroom under the 12 GB cap; dynamic-VRAM streams any excess |
| Qwen2.5-VL 7B FP8 text encoder | 9.38 GB | Runs *before* the DiT and is released after encode — does not stack with the DiT; needs system RAM headroom (we have 53 GB) |
| qwen_image_vae | 0.25 GB | |
| Lightning 8-step LoRA | 0.85 GB | 8 steps instead of 50 → ~6x faster |
| 4x-UltraSharp (upscale pass) | ~0.07 GB + activations | ~2–4 GB peak when upscaling 1280x720→2560x1440 |
| **Peak during sampling** | **~11–12 GB** | 720p activations are modest; tight but the documented 12 GB configuration |
| **Peak during upscale** | **~3–4 GB** | Generation model offloaded by then |

Enforcement: set ComfyUI `--reserve-vram 60` (72 − 12) so it can never exceed ~12 GB and can't OOM the GPU out from under vLLM.

## 4. Downloads (exact URLs + destinations)

Base: `BASE=/home/chuck/data/comfyui/basedir/models`

### 4.1 Generation model (Q3_K_M chosen; Q4_0 as later upgrade)
| File | Size | URL |
|---|---|---|
| `qwen-image-2512-Q3_K_M.gguf` → `$BASE/diffusion_models/` | 9.93 GB | https://huggingface.co/unsloth/Qwen-Image-2512-GGUF/resolve/main/qwen-image-2512-Q3_K_M.gguf |
| (optional, later) `qwen-image-2512-Q4_0.gguf` → `$BASE/diffusion_models/` | 11.85 GB | https://huggingface.co/unsloth/Qwen-Image-2512-GGUF/resolve/main/qwen-image-2512-Q4_0.gguf |

### 4.2 Text encoder + VAE (shared by all Qwen-Image variants)
| File | Size | URL |
|---|---|---|
| `qwen_2.5_vl_7b_fp8_scaled.safetensors` → `$BASE/text_encoders/` | 9.38 GB | https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI/resolve/main/split_files/text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors |
| `qwen_image_vae.safetensors` → `$BASE/vae/` | 0.25 GB | https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI/resolve/main/split_files/vae/qwen_image_vae.safetensors |

### 4.3 Speed LoRA
| File | Size | URL |
|---|---|---|
| `Qwen-Image-2512-Lightning-8steps-V1.0-bf16.safetensors` → `$BASE/loras/` | 0.85 GB | https://huggingface.co/lightx2v/Qwen-Image-2512-Lightning/resolve/main/Qwen-Image-2512-Lightning-8steps-V1.0-bf16.safetensors |
| (optional) 4-step variant → `$BASE/loras/` | 0.85 GB | https://huggingface.co/lightx2v/Qwen-Image-2512-Lightning/resolve/main/Qwen-Image-2512-Lightning-4steps-V1.0-bf16.safetensors |

### 4.4 Upscalers
| File | Size | URL |
|---|---|---|
| 4x-UltraSharp | 67 MB | **already installed** at `$BASE/upscale_models/4xUltrasharp_4xUltrasharpV10.pt` |
| `RealESRGAN_x4plus.pth` → `$BASE/upscale_models/` (optional, for photo-style images) | 67 MB | https://huggingface.co/amd/realesrgan-x4plus/resolve/main/RealESRGAN_x4plus.pth (mirror — the original `xinntao/Real-ESRGAN` repo now requires auth) |

### 4.5 Custom node (GGUF loader)
```bash
git clone https://github.com/city96/ComfyUI-GGUF /home/chuck/data/comfyui/basedir/custom_nodes/ComfyUI-GGUF
```
(Or install "ComfyUI-GGUF" via ComfyUI-Manager.) Provides the **Unet Loader (GGUF)** node — filed under the "bootleg" category in the node browser; that's the right one.

## 5. Container / Launch Changes

`compose/comfyui.yml` currently runs `COMFYUI_FLAGS=--listen 0.0.0.0 --port 8188 --fp16-vae --reserve-vram 8` (that flag means "reserve 8 GB for others, use the rest" — wrong for a 12 GB budget).

Change to:
```
COMFYUI_FLAGS=--listen 0.0.0.0 --port 8188 --fp16-vae --reserve-vram 60
```
- `--reserve-vram 60` → ComfyUI caps itself at ~12 GB (72 − 60).
- Dynamic VRAM is on by default in v0.22.0 (needed here; it streams weights in/out of VRAM). If fine-detail quality ever looks degraded on the last steps, that's the known dynamic-VRAM tradeoff — mitigate with `--fp16-intermediates` (halves intermediate memory, minimal quality impact).
- Restart: `docker compose -f compose/comfyui.yml up -d comfyui_backend` (or however the container is currently started).

## 6. Workflows

### 6.1 Infographic (text-heavy) — 720p → 1080p
Node graph (all native except the GGUF loader):
1. **Unet Loader (GGUF)** → `qwen-image-2512-Q3_K_M.gguf`
2. **Load CLIP** → `qwen_2.5_vl_7b_fp8_scaled.safetensors` (Qwen-Image clip vision loader)
3. **Load VAE** → `qwen_image_vae.safetensors`
4. **EmptySD3LatentImage** → **1280 x 720** (Qwen-Image uses SD3-type latents; 16:9)
5. **LoRA Loader** → Lightning 8-step, strength 1.0
6. **KSampler** → steps **8**, cfg **1.0**, sampler `euler` (or `res_multistep`), denoise 1.0
   - (Without the LoRA: 50 steps, true_cfg 4.0 — the reference config, ~6x slower)
7. **VAE Decode** → 1280×720 image
8. **Load Upscale Model** → `4xUltrasharp_4xUltrasharpV10.pt`
9. **Upscale Image (Using Model)** → scale **2.0** → 2560×1440
10. **ImageScale** (lanczos) → **1920 x 1080** (the downscale is deliberate: 2x supersampling makes text edges much crisper than a direct 1.5x upscale)
11. **Save Image**

Why 2x-then-downscale: pure ESRGAN upscaling preserves and sharpens existing strokes (great for text) without a diffusion re-draw that would *rewrite* the text. The extra resolution from supersampling is what makes 1080p text legible.

### 6.2 Normal images (no text)
Same graph, steps 1–7. For photos use RealESRGAN_x4plus instead of 4x-UltraSharp (UltraSharp is tuned for art/illustration/infographics; Real-ESRGAN is the general-purpose pick).

### 6.3 Phase 2 (optional): iteration with Qwen-Image-Edit-2511
When a generated infographic needs fixes (move a label, recolor a panel, add a row):
- `unsloth/Qwen-Image-Edit-2511-GGUF` Q4_0 (11.9 GB) → `$BASE/diffusion_models/`
  https://huggingface.co/unsloth/Qwen-Image-Edit-2511-GGUF/resolve/main/qwen-image-edit-2511-Q4_0.gguf
- Same encoder/VAE as above; feed the generated image + a text instruction ("change the title to ...", "make the chart bars blue"). 4–8 steps with the Lightning edit LoRA.

## 7. Prompting for Legible Text (the part that actually matters)

1. **Quote the exact text, specify placement and typography.** Qwen-Image reads natural language:
   `A clean 16:9 infographic about renewable energy in 2026. Visible text: "RENEWABLE ENERGY 2026", placed at the top in large bold sans-serif white lettering. Three panels with icons. Visible text: "SOLAR 42%", "WIND 31%", "HYDRO 27%", each under its icon in medium bold dark-gray text. Flat vector style, light background, high contrast.`
2. **Keep each text element short** (a few words). Long paragraphs are where text rendering breaks; for dense content, generate the layout with placeholder/clean areas and add final type in an editor (hybrid approach — the standard production workflow).
3. **High contrast, large type** in the prompt ("large bold", "high contrast", "clean white background") correlates with legible output.
4. **Negative prompt** (English translation of the official Qwen-Image negative):
   `low resolution, low quality, deformed, deformed hands, oversaturated, waxy, AI look, messy composition, blurry text, distorted text, unreadable text, extra letters, watermark`
5. **Seed discipline:** lock the seed when iterating on text — the layout stays stable and only the text changes.
6. If a specific word keeps garbling: reword the prompt, increase steps (drop the Lightning LoRA, use 50 steps for the final render), or fix that one element in an editor / with the Edit model.

## 8. Test / Acceptance Plan

1. `nvidia-smi` before start: confirm vLLM ~58 GB, free ≥ 14 GB.
2. Generate the infographic prompt from §7.1 at 1280×720 (8 steps, Lightning). Watch `nvidia-smi` during sampling — **peak must stay ≤ ~12 GB** (i.e. total GPU ≤ ~70 GB).
   - (Q3_K_M is the safe-fit choice; if it *still* OOMs — unlikely — drop generation to 1024×576 or use the Q2_K quant.)
3. Upscale 2x → downscale to 1920×1080. Zoom to 100%: **every quoted text string must be fully legible** (no garbled letters).
4. Timing check: 8 steps at 720p should be well under a minute on this GPU; if it's minutes, check the container isn't thrashing (dynamic VRAM + 12 GB cap is tight — Q3_K_M will be faster too).
5. Normal-image test: one photo-style prompt, RealESRGAN pass, verify no color shifts.
6. Save the working workflow as a ComfyUI template (Workflow → Save as Template) named `qwen-image-2512-infographic-720p`.

## 9. Watch Items

- **Qwen-Image-2.0 open weights** — the single best fit for infographics (7B → fits 12 GB with room, native 2K so the upscale step becomes optional, 1k-token typography instructions). Re-check monthly: `curl -s https://huggingface.co/api/models/Qwen/Qwen-Image-2.0` (HTTP 200 = released). When out: download fp8 or GGUF Q4, drop into the same workflow, retire 2512.
- **Qwen-Image-Edit-2511 Q4_0** (Phase 2 above) once the gen pipeline is proven.
- **Z-Image Turbo GGUF/fp8** if a good quant appears and speed matters more than text quality.
- ComfyUI template browser: if ComfyUI ships an official Qwen-Image-2512 template, it can replace the hand-built graph (same nodes, same settings).

## 10. Download Checklist (one-shot)

```bash
BASE=/home/chuck/data/comfyui/basedir/models
mkdir -p $BASE/diffusion_models $BASE/text_encoders $BASE/vae $BASE/loras $BASE/upscale_models

# 1. Generation model (9.93 GB)
wget -O $BASE/diffusion_models/qwen-image-2512-Q3_K_M.gguf \
  "https://huggingface.co/unsloth/Qwen-Image-2512-GGUF/resolve/main/qwen-image-2512-Q3_K_M.gguf"

# 2. Text encoder (9.38 GB)
wget -O $BASE/text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors \
  "https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI/resolve/main/split_files/text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors"

# 3. VAE (254 MB)
wget -O $BASE/vae/qwen_image_vae.safetensors \
  "https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI/resolve/main/split_files/vae/qwen_image_vae.safetensors"

# 4. Lightning 8-step LoRA (850 MB)
wget -O $BASE/loras/Qwen-Image-2512-Lightning-8steps-V1.0-bf16.safetensors \
  "https://huggingface.co/lightx2v/Qwen-Image-2512-Lightning/resolve/main/Qwen-Image-2512-Lightning-8steps-V1.0-bf16.safetensors"

# 5. GGUF loader custom node
git clone https://github.com/city96/ComfyUI-GGUF /home/chuck/data/comfyui/basedir/custom_nodes/ComfyUI-GGUF

# 6. Optional: photo upscaler (67 MB) — amd mirror (xinntao repo now requires auth)
wget -O $BASE/upscale_models/RealESRGAN_x4plus.pth \
  "https://huggingface.co/amd/realesrgan-x4plus/resolve/main/RealESRGAN_x4plus.pth"
```
Total: ~22.4 GB. Then: edit `compose/comfyui.yml` (`--reserve-vram 60`), restart the container, refresh the node list in the UI, build the workflow from §6.1.