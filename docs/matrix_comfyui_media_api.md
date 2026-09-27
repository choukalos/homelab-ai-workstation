# ComfyUI Media API — Image Creation & Editing (Qwen-Image)

> **Audience:** harness server / media tooling developers integrating image generation + editing.
> **Status:** verified end-to-end 2026-09-23 (Qwen-Image-2.1 default; legacy 2512/2511 path re-verified as fallback).
> **Replaces:** any prior ComfyUI integration (old SDXL/FLUX-era workflows).
> **Ops doc:** see `docs/matrix_images_mode.md` for operator-facing details (mode, VRAM, maintenance).
> **Upgrade runbook:** `media_todo.md` (Qwen-Image-2.1, 2026-09-23) + `scripts/qwen21_upgrade_matrix.sh`.
> **Higher-level orchestrator:** this doc covers the low-level ComfyUI image create/edit API. For full
> media production (storyboard → shots → TTS/music/SFX → upscale → assembly) use the **media-pipeline**
> service on port 8189 (see `docs/matrix_media_pipeline_api.md` for the full API contract,
> `docs/matrix_images_mode.md` §Media pipeline orchestrator, `media-pipeline/`,
> and the remote client in `media-mcp-client/` (pointer to `mcp/servers/media` in the homelab-ai-harness repo). The pipeline defaults to Qwen-Image-2.1 and exposes
> `model` (`qwen21` | `legacy`) + `references` per request.

---

## 1. What you get

| Flow | Model (default since 2026-09-23) | Input | Output | Expected time |
|---|---|---|---|---|
| **Create image** (text → image) | **Qwen-Image-2.1** (7B single-stream DiT, int8_convrot) + Qwen3-VL 8B int8 encoder + 64-ch RGBA VAE | text prompt | ~1 MP PNG (1280×720 class; upscale separately for 1080p) | ~30–120 s (25 steps) |
| **Edit image** (image + instruction) | **same Qwen-Image-2.1 model** (unified T2I + edit) | image + text instruction (+ up to 9 reference images) | edited image at the input image's resolution (canvas follows the edited image) | ~30–120 s (25 steps) |
| Create / edit (legacy) | Qwen-Image-2512 (20B GGUF Q4_0) + 4-step Lightning LoRA / Qwen-Image-Edit-2511 (20B GGUF Q4_0) + 8-step Lightning LoRA | as above | 1920×1080 PNG (create, 720p render → 4x upscale → lanczos) / Kontext resolution (edit) | ~15–40 s / ~45–60 s |

All flows run **concurrently with vLLM** (the main LLM workload). ComfyUI is capped at a ~12 GB VRAM
budget via `--reserve-vram 60`; measured peaks stay under 70.2 GB of 72 GB with vLLM holding ~56 GB
untouched. **No mode switch, no stopping vLLM, no coordination needed.**

Strengths (why this replaces the old tooling):
- **Unified model** — one 7B model for create *and* edit (Qwen-Image-2.1), native RGBA.
- **Reference images** — the edit flow accepts **up to 10 images** (1 edit target + 9 references):
  character/product consistency across storyboard shots (the killer feature for the commercial pipeline).
- **Legible in-image text** — improved typography; quote exact strings in the prompt.
- **Better portrait lighting** vs the 20B 2512/2511 generation.
- **Slower than legacy** — no distilled 4-step LoRA at launch (official 25–40 steps; the media-pipeline
  clamps qwen21 `steps` to [10, 50], default 25). Legacy 4/8-step path remains via `model=legacy`.

## 2. Access

- **Base URL:** `http://localhost:8188` (same host; LAN-only, **no authentication** — do not expose publicly)
- **Liveness:** `GET /system_stats` → JSON with `system.comfyui_version` (currently `0.37.0`, pinned git tag — the day-0 Qwen-Image-2.1 release, 2026-09-21)
- **No auth, no API keys.** If the port is down, ComfyUI is not running — operator must start it (`docker compose -f compose/comfyui.yml up -d`); do not attempt to manage the container from tooling.

### Endpoint summary

| Endpoint | Method | Purpose |
|---|---|---|
| `/system_stats` | GET | liveness / version |
| `/prompt` | POST | queue a workflow (API-format prompt) |
| `/history/{prompt_id}` | GET | poll one prompt's status + outputs |
| `/queue` | GET | queue depth (`queue_running`, `queue_pending`) |
| `/view?filename=…&type=output` | GET | download a produced image (bytes) |
| `/upload/image` | POST (multipart) | upload an image into the input dir (edit flow) |
| `/object_info` / `/object_info/{Node}` | GET | node schemas (for debugging) |

## 3. Core request flow (both flows)

```
1. POST /prompt          {"prompt": { …API prompt JSON… }}
   ← {"prompt_id": "<uuid>", "number": <int>}

2. poll GET /history/{prompt_id}   (every 1–2 s)
   ← {} until done, then {
       "<prompt_id>": {
         "status": {"status_str": "success", "completed": true},
         "outputs": {"<node_id>": {"images": [
             {"filename": "prefix_00001_.png", "subfolder": "", "type": "output"}
         ]}}
       }
     }
   On failure: status.status_str == "error", status.messages contains the error.

3. GET /view?filename=<filename>&type=output&subfolder=<subfolder>
   ← image bytes
```

Output naming: `SaveImage.filename_prefix` → `<prefix>_00001_.png` (counter increments per run with the same prefix). Use a unique prefix per job (e.g. `media_<jobid>`) to avoid ambiguity.

## 4. Flow A — Create image (Qwen-Image-2.1, default)

### 4.1 API prompt (verified graph, 8 nodes)

Placeholders in `⟨angle brackets⟩`. This is the official Comfy-Org Qwen-Image-2.1 T2I template
unwound to API format (as shipped by the media-pipeline, `workflows.py::qwen_image_21_t2i`).
No upscale tail — render at ~1 MP and upscale separately if 1080p is needed (§4.4).

```json
{
  "451": { "class_type": "UNETLoader",
            "inputs": { "unet_name": "qwen_image_2.1_int8_convrot.safetensors", "weight_dtype": "default" } },
  "453": { "class_type": "CLIPLoader",
            "inputs": { "clip_name": "qwen3vl_8b_int8_convrot.safetensors", "type": "qwen_image", "weight_dtype": "default" } },
  "454": { "class_type": "VAELoader",
            "inputs": { "vae_name": "qwen_image_2.1_vae_bf16.safetensors" } },
  "456": { "class_type": "EmptyLatentImage",
            "inputs": { "width": 1280, "height": 720, "batch_size": 1 } },
  "474": { "class_type": "TextEncodeQwenImage21",
            "inputs": { "clip": ["453", 0], "vae": ["454", 0],
                        "prompt": "⟨POSITIVE PROMPT⟩", "negative_prompt": "⟨NEGATIVE PROMPT⟩",
                        "resolution": 1024 } },
  "458": { "class_type": "KSampler",
            "inputs": { "model": ["451", 0], "seed": ⟨INT⟩, "steps": 25, "cfg": 1.0,
                        "sampler_name": "euler", "scheduler": "simple",
                        "positive": ["474", 0], "negative": ["474", 1],
                        "latent_image": ["456", 0], "denoise": 1.0 } },
  "457": { "class_type": "VAEDecode",
            "inputs": { "samples": ["458", 0], "vae": ["454", 0] } },
  "999": { "class_type": "SaveImage",
            "inputs": { "filename_prefix": "⟨PREFIX⟩", "images": ["457", 0] } }
}
```

### 4.2 Variable parts

| Placeholder | Rules |
|---|---|
| `⟨POSITIVE PROMPT⟩` | Natural language. For images with text: **quote the exact strings** and specify placement + typography. Keep each quoted element short (a few words). |
| `⟨NEGATIVE PROMPT⟩` | `TextEncodeQwenImage21` takes the negative as an input; the pipeline passes `""`. Only set it if you have a specific failure mode. |
| `⟨INT⟩` seed | Any int. Lock the seed when iterating on a layout. |
| `⟨PREFIX⟩` | Unique per job, e.g. `media_<jobid>`. |
| `resolution` (474) | **Required** in v0.37.0 (API validation rejects the prompt without it). Resizes reference images to ≈ `resolution`² pixels (multiple of 32, aspect preserved); `0` keeps original sizes. Default `1024` = the node default and the pipeline's value (≈1 MP, the VRAM sweet spot). Irrelevant for t2i (no images) but must still be present. |
| `width`/`height` (node 456) | **~1 MP sweet spot for the 12 GB VRAM budget:** 1280×720 (16:9), 720×1280 (9:16), 1024×1024 (1:1). Native 2K (2048²) does NOT fit the budget — render ~1 MP and upscale. |
| `steps` (node 458) | 25 default (official range 25–40). The media-pipeline clamps to [10, 50] — <10 degrades badly (no distilled LoRA at launch). |

### 4.3 Model/step variants

| Config | unet_name | steps | Notes |
|---|---|---|---|
| **Primary (above)** | `qwen_image_2.1_int8_convrot.safetensors` | 25 | int8_convrot sized for the 12 GB budget; the only 2.1 weight currently on disk |
| Watch item | (bf16/fp8 2.1 weights) | 25–40 | not on disk; int8_convrot is the 12 GB fit |

### 4.4 Upscale for 1080p stills

The 2.1 graph has no upscale tail. If 1080p output is needed: run the create flow at 1280×720,
then chain the legacy upscale tail (nodes 10–12 of §6.1: `UpscaleModelLoader` +
`ImageUpscaleWithModel` + `ImageScale` lanczos) on the result, or use the pipeline's video upscale
(`media_upscale_video`, 4xUltrasharp 'a2' fast / SeedVR2 'b' quality) for shots.

## 5. Flow B — Edit image (Qwen-Image-2.1, default, with references)

### 5.1 Provide the input image(s)

`LoadImage` reads from the ComfyUI input dir. Two options:

**Option 1 — API upload (preferred for tooling):**

```
POST /upload/image    (multipart/form-data)
  image:   <file bytes>
  overwrite: true
← {"name": "<basename>.png", "subfolder": "", "type": "input"}
```

Use the returned `name` as `LoadImage.inputs.image`. (Uploads land in `basedir/input/`.)

**Option 2 — host filesystem:** place the file in `/home/chuck/data/comfyui/basedir/input/` and use its basename.

**References (up to 9 extra images):** upload each the same way and add a `LoadImage` node per
reference, wired into `TextEncodeQwenImage21` as `images.image_2 … images.image_10`.
`images.image_1` is **always the image being edited** (the canvas follows it); references are
identity/consistency inputs only (e.g. a character sheet, or a previous shot's keyframe). The
media-pipeline does this staging for you (its `/images/edit` `references` form field accepts
ComfyUI input/ filenames or media_jobs-relative paths).

### 5.2 API prompt (verified graph, 9 nodes + 1 per reference)

Placeholders in `⟨angle brackets⟩`. This is the official Comfy-Org Qwen-Image-2.1 edit template
unwound to API format (as shipped by the media-pipeline, `workflows.py::qwen_image_21_edit`).
The official template's switch=False default (canvas follows the edited image) means no
ComfySwitchNode/EmptyLatentImage pair. The edit model goes through `QwenImage21Cache`
(prefix-KV caching — benefit realized when consecutive jobs share prompt prefixes).

```json
{
  "451": { "class_type": "UNETLoader",
            "inputs": { "unet_name": "qwen_image_2.1_int8_convrot.safetensors", "weight_dtype": "default" } },
  "453": { "class_type": "CLIPLoader",
            "inputs": { "clip_name": "qwen3vl_8b_int8_convrot.safetensors", "type": "qwen_image", "weight_dtype": "default" } },
  "454": { "class_type": "VAELoader",
            "inputs": { "vae_name": "qwen_image_2.1_vae_bf16.safetensors" } },
  "455": { "class_type": "LoadImage",
            "inputs": { "image": "⟨INPUT IMAGE FILENAME⟩" } },
  "469": { "class_type": "QwenImage21Cache",
            "inputs": { "model": ["451", 0], "device": "auto", "dtype": "default" } },
  "474": { "class_type": "TextEncodeQwenImage21",
            "inputs": { "clip": ["453", 0], "vae": ["454", 0],
                        "prompt": "⟨INSTRUCTION⟩", "negative_prompt": "",
                        "images.image_1": ["455", 0], "resolution": 1024 } },
  "458": { "class_type": "KSampler",
            "inputs": { "model": ["469", 0], "seed": ⟨INT⟩, "steps": 25, "cfg": 1.0,
                        "sampler_name": "euler", "scheduler": "simple",
                        "positive": ["474", 0], "negative": ["474", 1],
                        "latent_image": ["474", 2], "denoise": 1.0 } },
  "457": { "class_type": "VAEDecode",
            "inputs": { "samples": ["458", 0], "vae": ["454", 0] } },
  "999": { "class_type": "SaveImage",
            "inputs": { "filename_prefix": "⟨PREFIX⟩", "images": ["457", 0] } }
}
```

Per reference image *i* (i = 2 … 10), add:

```json
"5⟨i⟩": { "class_type": "LoadImage", "inputs": { "image": "⟨REFERENCE i FILENAME⟩" } }
```

and wire it into node 474 as `"images.image_⟨i⟩": ["5⟨i⟩", 0]`. (The pipeline uses node ids
`52 … 510` to avoid collisions with the 4xx ids.)

### 5.3 Variable parts

| Placeholder | Rules |
|---|---|
| `⟨INPUT IMAGE FILENAME⟩` | `name` returned by `/upload/image` (or a file already in the input dir). The canvas follows this image, **resized to ≈ `resolution`² pixels** (multiple of 32, aspect preserved) — e.g. a 1280×720 input yields a 1376×768 output at `resolution=1024`. All reference images are resized the same way. |
| `⟨INSTRUCTION⟩` | Short, specific, imperative. |
| `⟨REFERENCE i FILENAME⟩` | Up to 9 extra images for identity/consistency (10 total incl. the edit target). |
| `⟨INT⟩` seed | Any int. |
| `⟨PREFIX⟩` | Unique per job. |

Fixed parts (do not change): `negative_prompt: ""`; `denoise: 1.0`; `cfg: 1.0`;
`QwenImage21Cache` `device: "auto"` / `dtype: "default"`; the `latent_image: ["474", 2]`
wiring (the encoded input image IS the canvas latent).

### 5.4 Output size & iteration

- The output comes out at **the input image's resolution** (canvas follows the edited image) —
  unlike the legacy Kontext flow, which resized to a fixed Kontext resolution list.
- **Iteration works:** feed the edited image back in as the next input with a follow-up
  instruction (optionally with the same references for consistency).
- If 1080p output is needed from an edit: run the edit, then the upscale tail (§4.4).

## 6. Legacy flows (Qwen-Image-2512/2511 GGUF + Lightning) — `model=legacy`

> Kept as a fallback (and selectable per request via the media-pipeline's `model=legacy`, or
> globally via `MEDIA_IMAGE_MODEL=legacy`). Faster (4/8 steps) but 20B GGUF, no reference
> images, older typography. Scheduled for removal after 1–2 weeks of green qwen21 QA
> (`media_todo.md` Phase 4).

### 6.1 Flow A (legacy) — Create image (text → 1080p image)

API prompt (verified graph, 13 nodes). Placeholders in `⟨angle brackets⟩`. Everything else is
fixed — do not change node types, sampler, or the upscale tail (the 4x-then-lanczos step is what
makes text crisp at 1080p).

```json
{
  "1":  { "class_type": "UnetLoaderGGUF",
           "inputs": { "unet_name": "qwen-image-2512-Q4_0.gguf" } },
  "2":  { "class_type": "CLIPLoader",
           "inputs": { "clip_name": "qwen_2.5_vl_7b_fp8_scaled.safetensors", "type": "qwen_image" } },
  "3":  { "class_type": "VAELoader",
           "inputs": { "vae_name": "qwen_image_vae.safetensors" } },
  "4":  { "class_type": "EmptySD3LatentImage",
           "inputs": { "width": 1280, "height": 720, "batch_size": 1 } },
  "5":  { "class_type": "LoraLoader",
           "inputs": { "model": ["1", 0], "clip": ["2", 0],
                       "lora_name": "Qwen-Image-2512-Lightning-4steps-V1.0-bf16.safetensors",
                       "strength_model": 1.0, "strength_clip": 1.0 } },
  "6":  { "class_type": "CLIPTextEncode",
           "inputs": { "clip": ["2", 0], "text": "⟨POSITIVE PROMPT⟩" } },
  "7":  { "class_type": "CLIPTextEncode",
           "inputs": { "clip": ["2", 0], "text": "⟨NEGATIVE PROMPT⟩" } },
  "8":  { "class_type": "KSampler",
           "inputs": { "model": ["5", 0], "positive": ["6", 0], "negative": ["7", 0],
                       "latent_image": ["4", 0], "seed": ⟨INT⟩, "steps": 4, "cfg": 1.0,
                       "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0 } },
  "9":  { "class_type": "VAEDecode",
           "inputs": { "samples": ["8", 0], "vae": ["3", 0] } },
  "10": { "class_type": "UpscaleModelLoader",
           "inputs": { "model_name": "4xUltrasharp_4xUltrasharpV10.pt" } },
  "11": { "class_type": "ImageUpscaleWithModel",
           "inputs": { "upscale_model": ["10", 0], "image": ["9", 0] } },
  "12": { "class_type": "ImageScale",
           "inputs": { "image": ["11", 0], "upscale_method": "lanczos",
                       "width": 1920, "height": 1080, "crop": "disabled" } },
  "13": { "class_type": "SaveImage",
           "inputs": { "filename_prefix": "⟨PREFIX⟩", "images": ["12", 0] } }
}
```

Variable parts: `⟨POSITIVE PROMPT⟩` (quote exact strings for in-image text),
`⟨NEGATIVE PROMPT⟩` (standard: `low resolution, low quality, deformed, deformed hands,
oversaturated, waxy, AI look, messy composition, blurry text, distorted text, unreadable text,
extra letters, watermark`), `⟨INT⟩` seed, `⟨PREFIX⟩`, and node 4 `width`/`height` (720p class
render; node 12 targets the same aspect at 1080p class — keep the pairs aspect-matched).

Model/step variants (node 1 + node 5 + KSampler.steps):

| Config | unet_name | lora_name | steps | Notes |
|---|---|---|---|---|
| **Primary (above)** | `qwen-image-2512-Q4_0.gguf` | `Qwen-Image-2512-Lightning-4steps-V1.0-bf16.safetensors` | 4 | Fastest, best measured OCR |
| Slower/higher-detail | `qwen-image-2512-Q4_0.gguf` | `Qwen-Image-2512-Lightning-8steps-V1.0-bf16.safetensors` | 8 | Use the 8-step LoRA **with** 8 steps |
| VRAM fallback | `qwen-image-2512-Q3_K_M.gguf` | `Qwen-Image-2512-Lightning-8steps-V1.0-bf16.safetensors` | 8 | Smaller DiT; use if a Q4_0 run OOMs |

Never mix a 4-step LoRA with 8 steps (or vice versa) — the LoRAs are tuned for their step count.

Upscale model choice (node 10): `4xUltrasharp_4xUltrasharpV10.pt` (text/infographic default) or
`RealESRGAN_x4plus.pth` (photos/general), both from `upscale_models/`.

Prompting for legible text: quote the exact text + placement + typography; keep elements short;
high contrast + large type; if a word keeps garbling, reword, run a final render without the
Lightning LoRA at 50 steps (slower), or fix it with the edit flow.

### 6.2 Flow B (legacy) — Edit image (image + instruction → edited image)

API prompt (verified graph, 16 nodes). Matches the official ComfyUI blueprint
`blueprints/Image Edit (Qwen 2511).json` adapted for GGUF — do not drop the
`FluxKontextMultiReferenceLatentMethod` nodes (required for the GGUF version).

```json
{
  "1":  { "class_type": "UnetLoaderGGUF",
           "inputs": { "unet_name": "qwen-image-edit-2511-Q4_0.gguf" } },
  "2":  { "class_type": "CLIPLoader",
           "inputs": { "clip_name": "qwen_2.5_vl_7b_fp8_scaled.safetensors", "type": "qwen_image" } },
  "3":  { "class_type": "VAELoader",
           "inputs": { "vae_name": "qwen_image_vae.safetensors" } },
  "4":  { "class_type": "LoadImage",
           "inputs": { "image": "⟨INPUT IMAGE FILENAME⟩" } },
  "5":  { "class_type": "LoraLoader",
           "inputs": { "model": ["1", 0], "clip": ["2", 0],
                       "lora_name": "Qwen-Image-Edit-2511-Lightning-8steps-V1.0-bf16.safetensors",
                       "strength_model": 1.0, "strength_clip": 1.0 } },
  "6":  { "class_type": "FluxKontextImageScale",
           "inputs": { "image": ["4", 0] } },
  "7":  { "class_type": "VAEEncode",
           "inputs": { "pixels": ["6", 0], "vae": ["3", 0] } },
  "8":  { "class_type": "TextEncodeQwenImageEditPlus",
           "inputs": { "clip": ["5", 1], "prompt": "⟨INSTRUCTION⟩",
                       "vae": ["3", 0], "image1": ["6", 0] } },
  "9":  { "class_type": "TextEncodeQwenImageEditPlus",
           "inputs": { "clip": ["5", 1], "prompt": "",
                       "vae": ["3", 0], "image1": ["6", 0] } },
  "10": { "class_type": "FluxKontextMultiReferenceLatentMethod",
           "inputs": { "conditioning": ["8", 0], "reference_latents_method": "index_timestep_zero" } },
  "11": { "class_type": "FluxKontextMultiReferenceLatentMethod",
           "inputs": { "conditioning": ["9", 0], "reference_latents_method": "index_timestep_zero" } },
  "12": { "class_type": "ModelSamplingAuraFlow",
           "inputs": { "model": ["5", 0], "shift": 3.1 } },
  "13": { "class_type": "CFGNorm",
           "inputs": { "model": ["12", 0], "strength": 1.0 } },
  "14": { "class_type": "KSampler",
           "inputs": { "model": ["13", 0], "positive": ["10", 0], "negative": ["11", 0],
                       "latent_image": ["7", 0], "seed": ⟨INT⟩, "steps": 8, "cfg": 1.0,
                       "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0 } },
  "15": { "class_type": "VAEDecode",
           "inputs": { "samples": ["14", 0], "vae": ["3", 0] } },
  "16": { "class_type": "SaveImage",
           "inputs": { "filename_prefix": "⟨PREFIX⟩", "images": ["15", 0] } }
}
```

Fixed parts (do not change): node 9's empty negative prompt; `denoise: 1.0`; `shift: 3.1`;
`strength: 1.0`; `reference_latents_method: "index_timestep_zero"`.

Output size: `FluxKontextImageScale` resizes the input to the nearest Kontext resolution by aspect
ratio — **16:9 → 1392×752**, 9:16 → 752×1392, 1:1 → 1024×1024 (list: 672×1568 … 1456×720). The
edited image comes out at that size, not the original size. For 1080p: run the edit, then the
create-flow upscale tail (nodes 10–12 of §6.1). Iteration works (feed the edited image back in).

## 7. Model inventory (on disk)

All under `/home/chuck/data/comfyui/basedir/models/`:

| File | Folder | Size | Role |
|---|---|---|---|
| `qwen_image_2.1_int8_convrot.safetensors` | `diffusion_models/` | 7.26 GB | **Primary DiT — Qwen-Image-2.1** (create + edit, default since 2026-09-23) |
| `qwen3vl_8b_int8_convrot.safetensors` | `text_encoders/` | 9.35 GB | **Qwen-Image-2.1 text encoder** (Qwen3-VL 8B int8) |
| `qwen_image_2.1_vae_bf16.safetensors` | `vae/` | 0.68 GB | **Qwen-Image-2.1 VAE** (64-ch RGBA) |
| `qwen-image-2512-Q4_0.gguf` | `diffusion_models/` | 11.85 GB | Legacy generation DiT (`model=legacy` create) |
| `qwen-image-2512-Q3_K_M.gguf` | `diffusion_models/` | 9.93 GB | Legacy fallback generation DiT (lower quality, lower VRAM) |
| `qwen-image-edit-2511-Q4_0.gguf` | `diffusion_models/` | 11.85 GB | Legacy edit DiT (`model=legacy` edit) |
| `qwen_2.5_vl_7b_fp8_scaled.safetensors` | `text_encoders/` | 9.38 GB | Legacy shared text encoder |
| `qwen_image_vae.safetensors` | `vae/` | 0.25 GB | Legacy shared VAE (keep until legacy removal) |
| `Qwen-Image-2512-Lightning-8steps-V1.0-bf16.safetensors` | `loras/` | 850 MB | Legacy generation speed LoRA (8 steps) |
| `Qwen-Image-2512-Lightning-4steps-V1.0-bf16.safetensors` | `loras/` | 850 MB | Legacy generation speed LoRA (4 steps — fastest) |
| `Qwen-Image-Edit-2511-Lightning-8steps-V1.0-bf16.safetensors` | `loras/` | 850 MB | Legacy edit speed LoRA (8 steps) |
| `4xUltrasharp_4xUltrasharpV10.pt` | `upscale_models/` | 67 MB | Upscaler: text/infographic |
| `RealESRGAN_x4plus.pth` | `upscale_models/` | 67 MB | Upscaler: photos/general |

Model lists refresh automatically when files appear in these folders (verified — no restart needed).
Legacy removal (after 1–2 weeks of green qwen21 QA): delete the 4 GGUF/LoRA/encoder rows + both
Lightning LoRAs (≈31 GB); keep `qwen_image_vae.safetensors` until the `model=legacy` code path is
dropped.

## 8. VRAM budget & performance

| Component | VRAM | Notes |
|---|---|---|
| vLLM (Qwen3.8-27B NVFP4) | ~56.3 GB | Committed baseline; **never touched** by ComfyUI |
| ComfyUI budget | ~12 GB | Enforced by `--reserve-vram 60` (reserves 60 GB for other software) |
| GPU total | 72 GB (73,415 MiB) | Acceptance gate: total peak ≤ ~70 GB |

**Qwen-Image-2.1 (default):** fits the 12 GB budget at **~1 MP** (1280×720 / 1344×768 keyframes)
— int8_convrot DiT (7.26 GB) + int8 encoder (9.35 GB) stream, never resident at the same time
(dynamic VRAM). Native 2K (2048²) does NOT fit the budget — keep the "1 MP render →
SeedVR2/4xUltrasharp upscale" pattern. Expect **~30–120 s per keyframe** at 25 steps (no
distilled LoRA at launch).

**Legacy (measured 2026-08-26):** peaks 9.3–14.2 GB attributable; create 14–20 s (4/8 steps),
edit 46–56 s (8 steps). The encoder (9.4 GB) and DiT (11.9 GB) are never resident at the same
time — ComfyUI offloads the encoder before loading the DiT. Idle ComfyUI holds ~0.7 GB.

- **Hard gate:** total GPU peak ≤ ~70 GB. ComfyUI is capped at ~12 GB by `--reserve-vram 60`
  (soft budget; dynamic VRAM streams weights).
- vLLM holds ~56 GB and is **never displaced** — ComfyUI only uses free VRAM.
- Idle ComfyUI retains ~0.7–4.5 GB (model cache) — normal.

## 9. Reference implementation (Python, stdlib only)

```python
#!/usr/bin/env python3
"""ComfyUI media API client — create + edit. Stdlib only (urllib)."""
import json, time, urllib.request, uuid, io

API = "http://localhost:8188"

def api(path, data=None, raw=False):
    req = urllib.request.Request(API + path)
    if data is not None:
        req.data = json.dumps(data).encode()
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=600) as r:
        b = r.read()
        return b if raw else json.loads(b)

def queue(prompt):
    return api("/prompt", {"prompt": prompt})["prompt_id"]

def wait(pid, poll=2.0, timeout=600):
    t0 = time.time()
    while time.time() - t0 < timeout:
        time.sleep(poll)
        h = api(f"/history/{pid}")
        if pid in h:
            st = h[pid].get("status", {})
            if st.get("completed"):
                return h[pid]
            if st.get("status_str") == "error":
                raise RuntimeError(json.dumps(st.get("messages"))[:2000])
    raise TimeoutError(pid)

def fetch(filename, subfolder=""):
    return api(f"/view?filename={urllib.parse.quote(filename)}"
               f"&type=output&subfolder={subfolder}", raw=True)

def run(prompt):
    """Queue a workflow, wait, return (filename, subfolder, png_bytes)."""
    import urllib.parse
    entry = wait(queue(prompt))
    for out in entry.get("outputs", {}).values():
        for img in out.get("images", []):
            return img["filename"], img.get("subfolder", ""), fetch(img["filename"], img.get("subfolder", ""))
    raise RuntimeError("no image output")

def upload_image(path, overwrite=True):
    """Multipart upload into the ComfyUI input dir. Returns filename."""
    import urllib.parse
    boundary = uuid.uuid4().hex
    body = io.BytesIO()
    def part(name, value):
        body.write(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n".encode())
    part("overwrite", "true" if overwrite else "false")
    data = open(path, "rb").read()
    body.write(f"--{boundary}\r\nContent-Disposition: form-data; name=\"image\"; filename=\"{path.split('/')[-1]}\r\nContent-Type: application/octet-stream\r\n\r\n".encode())
    body.write(data + b"\r\n")
    body.write(f"--{boundary}--\r\n".encode())
    req = urllib.request.Request(API + "/upload/image", data=body.getvalue(),
                                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())["name"]
```

Usage:

```python
png = run(create_prompt(positive="A 16:9 infographic …", seed=42, prefix="media_job1"))
name = upload_image("/path/to/input.png")
png = run(edit_prompt(instruction="Change the title to …", image=name, seed=42, prefix="media_job1e"))
```

(`create_prompt` / `edit_prompt` = the JSON templates in §4.1 / §5.2 with placeholders filled —
Qwen-Image-2.1 by default; §6.1 / §6.2 for the legacy path.)

## 10. Error handling

| Symptom | Meaning / action |
|---|---|
| `POST /prompt` → 400 with `invalid` list | Prompt validation failed — check node class types / input names against `GET /object_info/{Node}`. |
| `status.status_str == "error"` | See `status.messages` — usually a missing model file or CUDA OOM. On OOM: retry (transient) or drop to Q3_K_M (legacy create). |
| "node not found" for `TextEncodeQwenImage21` / `QwenImage21Cache` | ComfyUI is pre-v0.37.0 — run `scripts/qwen21_upgrade_matrix.sh` (or the pipeline is on the qwen21 default while ComfyUI was rolled back — set `MEDIA_IMAGE_MODEL=legacy`). |
| Port 8188 not responding | ComfyUI down — operator action required; return "image service unavailable". |
| Job queued but slow (> 2 min) | Check `GET /queue` (another job ahead) and `docker logs comfyui_backend`. |

## 11. Operational notes for integrators

- **Never** start/stop/restart the ComfyUI container or vLLM from tooling. Submit jobs only.
- **Concurrent jobs:** the queue serializes them; each job adds ~10–14 GB peak VRAM. Don't queue more than ~2 jobs at once while vLLM is busy.
- **Unique filename prefixes** per job — the counter (`_00001_`) only increments per prefix.
- **Seeds:** lock seeds when iterating on a specific image; randomize for fresh generations.
- **ComfyUI is pinned at v0.37.0** (git tag, day-0 Qwen-Image-2.1 release). Rollback =
  `git checkout v0.22.0` + `pip install -r requirements.txt` + restart (then set
  `MEDIA_IMAGE_MODEL=legacy` so qwen21 jobs don't fail with node-not-found).
- **Watch item:** a Qwen-Image-2.1 Lightning/distilled LoRA (LightX2V et al.) — if one lands,
  re-test 4–8 steps and consider dropping the [10, 50] clamp floor (would restore legacy-class
  speed on the new model).

## 12. Changelog

| Date | Change |
|---|---|
| 2026-09-23 | **Qwen-Image-2.1 becomes the default** for create + edit (unified 7B DiT, int8_convrot weights, ComfyUI pinned to v0.37.0, 25 steps, cfg=1.0). Edit flow gains up to 9 reference images (10 total) via `TextEncodeQwenImage21.images.image_N` for cross-shot identity/consistency; edit model goes through `QwenImage21Cache`. Canvas follows the edited image (template switch=False default). Native 2K doesn't fit the 12 GB budget — ~1 MP render + upscale. Legacy 2512/2511 GGUF+Lightning flows kept as §6 (`model=legacy`), scheduled for removal after 1–2 weeks of green QA. Runbook: `media_todo.md`; script: `scripts/qwen21_upgrade_matrix.sh`. |
| 2026-09-23 | **Matrix cutover executed + QA green.** ComfyUI upgraded v0.22.0 → v0.37.0 (`scripts/qwen21_upgrade_matrix.sh`, commit `a212759` fixes: `docker exec -u comfy` for dubious-ownership git, version-compare without `v` prefix, log fallback to /tmp). All 3 qwen21 weights downloaded (17.2 GB). Pipeline fix: `TextEncodeQwenImage21` requires a `resolution` input in v0.37.0 (default 1024) — added to both builders in `workflows.py`. QA: t2i 1280×720 @25 steps ≈16 s warm; edit + 1 reference ≈40 s (wall→white verified, canvas 1376×768); legacy t2i/edit regressions pass. All 43 pipeline workflow classes registered; custom nodes (GGUF, MMAudio, SeedVR2, VHS) intact; TTS/ACE-Step workers use isolated venvs (unaffected); vLLM healthy; GPU idle 68/73 GB (within ~70 GB gate). |
| 2026-08-28 | **Legacy cleanup.** Removed ~55 GB of obsolete models (SD1.5/SDXL/SVD checkpoints, LTXV 0.9.8 fp8, SeedVR2 int8 build, duplicate XTTS dir, junk VAEs/bigvgan discriminator), 6 legacy workflow JSONs (kept `qwen-image-2512-infographic-720p.json` as reference), 4 obsolete custom nodes (animatediff-evolved, UltimateSDUpscale, ollamagemini, VideoConcat), and scratch/venv caches. All in-use models verified intact; pipeline + ComfyUI health re-verified. |
| 2026-08-28 | **Ops note:** the ComfyUI Python venv + uv cache live in `run/` (bind-mounted). Deleting them is safe — the container entrypoint self-heals and rebuilds the venv on restart (verified: full rebuild from network in ~15 min, no jobs lost). **Caveat:** the rebuilt venv has base ComfyUI deps only and resolves numpy 2.5 (breaks numba → comfyui-mmaudio import fails). Run `scripts/comfyui_venv_deps.sh restart` after any venv rebuild to (re)install the 4 custom-node dep sets with `numpy<2.5` pinned (verified 2026-08-28: all 5 custom nodes import, 726+ node classes registered). |
| 2026-08-27 | Image generation runs concurrently with vLLM (no more exclusive `images` mode); media-pipeline added to the `image` compose profile. |