# ComfyUI Media API — Image Creation & Editing (Qwen-Image)

> **Audience:** harness server / media tooling developers integrating image generation + editing.
> **Status:** verified end-to-end 2026-08-26 (all flows tested on this box, VRAM + OCR checked).
> **Replaces:** any prior ComfyUI integration (old SDXL/FLUX-era workflows).
> **Ops doc:** see `docs/matrix_images_mode.md` for operator-facing details (mode, VRAM, maintenance).

---

## 1. What you get

| Flow | Model | Input | Output | Verified time |
|---|---|---|---|---|
| **Create image** (text → image) | Qwen-Image-2512 (20B, GGUF Q4_0) + 4-step Lightning LoRA | text prompt | 1920×1080 PNG (720p render → 4x upscale → lanczos) | ~15–40 s |
| **Edit image** (image + instruction) | Qwen-Image-Edit-2511 (20B, GGUF Q4_0) + 8-step Lightning LoRA | image + text instruction | edited image at Kontext resolution (16:9 → 1392×752) | ~45–60 s |

Both flows run **concurrently with vLLM** (the main LLM workload). ComfyUI is capped at a ~12 GB VRAM budget via `--reserve-vram 60`; measured peaks stay under 70.2 GB of 72 GB with vLLM holding ~56 GB untouched. **No mode switch, no stopping vLLM, no coordination needed.**

Strengths (why this replaces the old tooling):
- **Legible in-image text** — quote exact strings in the prompt; verified by OCR (all quoted strings detected, no garbling).
- **True instruction-based editing** — "change the title to …", "make the background navy", "make all text white" — with a stable iteration loop.
- Fast: 4 steps (create) / 8 steps (edit) with the Lightning LoRAs (no 30–50 step sampling).

## 2. Access

- **Base URL:** `http://localhost:8188` (same host; LAN-only, **no authentication** — do not expose publicly)
- **Liveness:** `GET /system_stats` → JSON with `system.comfyui_version` (currently `0.22.0`)
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

## 4. Flow A — Create image (text → 1080p image)

### 4.1 API prompt (verified graph, 13 nodes)

Placeholders in `⟨angle brackets⟩`. Everything else is fixed — do not change node types,
sampler, or the upscale tail (the 4x-then-lanczos step is what makes text crisp at 1080p).

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

### 4.2 Variable parts

| Placeholder | Rules |
|---|---|
| `⟨POSITIVE PROMPT⟩` | Natural language. For images with text: **quote the exact strings** and specify placement + typography, e.g. `Visible text: "RENEWABLE ENERGY 2026", placed at the top in large bold sans-serif white lettering. …` Keep each quoted element short (a few words). |
| `⟨NEGATIVE PROMPT⟩` | Use the standard one: `low resolution, low quality, deformed, deformed hands, oversaturated, waxy, AI look, messy composition, blurry text, distorted text, unreadable text, extra letters, watermark` |
| `⟨INT⟩` seed | Any int. Lock the seed when iterating on a layout (only the text/prompt changes). |
| `⟨PREFIX⟩` | Unique per job, e.g. `media_<jobid>`. |
| `width`/`height` (node 4) | Render at 720p class: 1280×720 (16:9), 720×1280 (9:16), 1024×1024 (1:1). The upscale tail (nodes 12) targets the same aspect at 1080p class: 1920×1080, 1080×1920, 1440×1440. Keep both pairs aspect-matched. |

### 4.3 Model/step variants (node 1 + node 5 + KSampler.steps)

| Config | unet_name | lora_name | steps | Notes |
|---|---|---|---|---|
| **Primary (above)** | `qwen-image-2512-Q4_0.gguf` | `Qwen-Image-2512-Lightning-4steps-V1.0-bf16.safetensors` | 4 | Fastest, best measured OCR |
| Slower/higher-detail | `qwen-image-2512-Q4_0.gguf` | `Qwen-Image-2512-Lightning-8steps-V1.0-bf16.safetensors` | 8 | Use the 8-step LoRA **with** 8 steps |
| VRAM fallback | `qwen-image-2512-Q3_K_M.gguf` | `Qwen-Image-2512-Lightning-8steps-V1.0-bf16.safetensors` | 8 | Smaller DiT; use if a Q4_0 run OOMs |

Never mix a 4-step LoRA with 8 steps (or vice versa) — the LoRAs are tuned for their step count.

### 4.4 Upscale model choice (node 10)

| Use case | `model_name` |
|---|---|
| Infographics / text / illustration | `4xUltrasharp_4xUltrasharpV10.pt` (default above — sharpens strokes, great for text) |
| Photos / general | `RealESRGAN_x4plus.pth` (general-purpose) |

Both are loaded from `upscale_models/` via `UpscaleModelLoader` — no other change needed.

### 4.5 Prompting for legible text (the part that matters)

1. Quote the exact text, specify placement and typography (see example above).
2. Keep each text element short; long paragraphs break. For dense content, generate the layout with clean areas and add final type in an editor (hybrid), or follow up with the **edit flow** (§5).
3. Ask for high contrast + large type ("large bold", "high contrast", "clean white background").
4. If a specific word keeps garbling: reword, or run a final render without the Lightning LoRA at 50 steps (slower), or fix that element with the edit flow.

## 5. Flow B — Edit image (image + instruction → edited image)

### 5.1 Provide the input image

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

### 5.2 API prompt (verified graph, 16 nodes)

Placeholders in `⟨angle brackets⟩`. This graph matches the official ComfyUI blueprint
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

### 5.3 Variable parts

| Placeholder | Rules |
|---|---|
| `⟨INPUT IMAGE FILENAME⟩` | `name` returned by `/upload/image` (or a file already in the input dir). |
| `⟨INSTRUCTION⟩` | Short, specific, imperative. Verified examples: `Change the year in the title from 2026 to 2027 so the title reads "RENEWABLE ENERGY 2027". Change the background color to dark navy blue. Keep all other text, icons and layout exactly the same.` / `Change all text to bright white color so it is clearly readable on the dark navy background. Keep the layout, icons, colors and wording exactly the same.` |
| `⟨INT⟩` seed | Any int. |
| `⟨PREFIX⟩` | Unique per job. |

Fixed parts (do not change): node 9's empty negative prompt; `denoise: 1.0`;
`shift: 3.1`; `strength: 1.0`; `reference_latents_method: "index_timestep_zero"`.

### 5.4 Output size & iteration

- `FluxKontextImageScale` resizes the input to the nearest Kontext resolution by aspect ratio — **16:9 → 1392×752**, 9:16 → 752×1392, 1:1 → 1024×1024 (list: 672×1568 … 1456×720). The edited image comes out at that size, not the original size.
- If 1080p output is needed from an edit: run the edit, then the create-flow upscale tail (nodes 10–12 of §4.1) on the result.
- **Iteration works:** feed the edited image back in as the next input with a follow-up instruction. Verified: pass 1 (recolor background) left dark text on dark bg; pass 2 ("make all text white") fixed it. OCR confidence rose 0.34–0.86 → 0.58–0.99.

## 6. Model inventory (on disk, verified)

All under `/home/chuck/data/comfyui/basedir/models/`:

| File | Folder | Size | Role |
|---|---|---|---|
| `qwen-image-2512-Q4_0.gguf` | `diffusion_models/` | 11.85 GB | **Primary generation DiT** (create flow) |
| `qwen-image-2512-Q3_K_M.gguf` | `diffusion_models/` | 9.93 GB | Fallback generation DiT (lower quality, lower VRAM) |
| `qwen-image-edit-2511-Q4_0.gguf` | `diffusion_models/` | 11.85 GB | **Edit DiT** (edit flow) |
| `qwen_2.5_vl_7b_fp8_scaled.safetensors` | `text_encoders/` | 9.38 GB | Shared text encoder (both flows) |
| `qwen_image_vae.safetensors` | `vae/` | 0.25 GB | Shared VAE (both flows) |
| `Qwen-Image-2512-Lightning-8steps-V1.0-bf16.safetensors` | `loras/` | 850 MB | Generation speed LoRA (8 steps) |
| `Qwen-Image-2512-Lightning-4steps-V1.0-bf16.safetensors` | `loras/` | 850 MB | Generation speed LoRA (4 steps — fastest) |
| `Qwen-Image-Edit-2511-Lightning-8steps-V1.0-bf16.safetensors` | `loras/` | 850 MB | Edit speed LoRA (8 steps) |
| `4xUltrasharp_4xUltrasharpV10.pt` | `upscale_models/` | 67 MB | Upscaler: text/infographic |
| `RealESRGAN_x4plus.pth` | `upscale_models/` | 67 MB | Upscaler: photos/general |

Model lists refresh automatically when files appear in these folders (verified — no restart needed).

## 7. VRAM budget & performance (measured 2026-08-26)

| Component | VRAM | Notes |
|---|---|---|
| vLLM (Qwen3.8-27B NVFP4) | ~56.3 GB | Committed baseline; **never touched** by ComfyUI (measured: identical before/after runs) |
| ComfyUI budget | ~12 GB | Enforced by `--reserve-vram 60` (reserves 60 GB for other software) |
| ComfyUI measured peaks | 9.3–14.2 GB attributable | Generation: 71.2 GB total peak; edit: 70.2 GB total peak |
| GPU total | 72 GB (73,415 MiB) | Acceptance gate: total peak ≤ ~70 GB — all runs passed |

The encoder (9.4 GB) and DiT (11.9 GB) are **never resident at the same time** — ComfyUI offloads the encoder before loading the DiT (dynamic VRAM). Idle ComfyUI holds ~0.7 GB.

Measured runs:

| Run | Time | Peak GPU (of 73,415 MiB) | vLLM |
|---|---|---|---|
| Create: infographic 720p→1080p, **Q4_0 + 4-step (primary)** | **14 s** | 71,125 MiB (69.4 GiB) | untouched |
| Create: infographic 720p→1080p, Q3_K_M + 8-step (fallback) | 20 s | 71,189 MiB (69.5 GiB) | untouched |
| Create: photo + RealESRGAN, Q3_K_M + 8-step | 18 s | (same envelope) | untouched |
| Edit: 16:9 infographic, Q4_0 + 8-step | 46 s | 70,175 MiB | untouched |
| Edit: iteration pass, Q4_0 + 8-step | 56 s | 70,212 MiB | untouched |

Q4_0+4-step vs Q3_K_M+8-step OCR (same prompt/seed): all quoted strings detected in both; Q4_0+4step scored equal-or-better on 6 of 7 (HYDRO 1.00 vs 0.85, 42% 0.97 vs 0.81) and is ~30% faster → **Q4_0 + 4-step is the primary create config.**

- **Hard gate:** total GPU peak ≤ ~70 GB. ComfyUI is capped at ~12 GB by `--reserve-vram 60` (soft budget; dynamic VRAM streams weights).
- vLLM holds ~56 GB and is **never displaced** — ComfyUI only uses free VRAM.
- Expect ~20–60 s per job. If a job takes minutes, something is wrong (check `docker logs comfyui_backend`).
- Idle ComfyUI retains ~0.7–4.5 GB (model cache) — normal.

## 8. Reference implementation (Python, stdlib only)

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
    body.write(f"--{boundary}\r\nContent-Disposition: form-data; name=\"image\"; filename=\"{path.split('/')[-1]}\"\r\nContent-Type: application/octet-stream\r\n\r\n".encode())
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

(`create_prompt` / `edit_prompt` = the JSON templates in §4.1 / §5.2 with placeholders filled.)

## 9. Error handling

| Symptom | Meaning / action |
|---|---|
| `POST /prompt` → 400 with `invalid` list | Prompt validation failed — check node class types / input names against `GET /object_info/{Node}`. |
| `status.status_str == "error"` | See `status.messages` — usually a missing model file or CUDA OOM. On OOM: retry (transient) or drop to Q3_K_M for create. |
| Port 8188 not responding | ComfyUI down — operator action required; return "image service unavailable". |
| Job queued but slow (> 2 min) | Check `GET /queue` (another job ahead) and `docker logs comfyui_backend`. |

## 10. Operational notes for integrators

- **Never** start/stop/restart the ComfyUI container or vLLM from tooling. Submit jobs only.
- **Concurrent jobs:** the queue serializes them; each job adds ~10–14 GB peak VRAM. Don't queue more than ~2 jobs at once while vLLM is busy.
- **Unique filename prefixes** per job — the counter (`_00001_`) only increments per prefix.
- **Seeds:** lock seeds when iterating on a specific image; randomize for fresh generations.
- **Watch item:** Qwen-Image-2.0 (better typography, native 2K) is API-only as of 2026-08; when open weights release, the same graph works with the new model file (node 1 swap).