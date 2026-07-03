# Images Mode — ComfyUI/FLUX Image Generation

> Created: 2026-07-03
> Compose: `compose.comfyui.yml` (profile: `image`)
> Profile: `models/profiles/comfyui.yaml`

## Overview

Images mode runs ComfyUI for image generation while keeping a light LLM (Gemma4)
available for chat and tool-calling. vLLM (Qwen3.6-27B) is **stopped** —
ComfyUI + Qwen won't fit on the 72 GB GPU simultaneously.

**Rule: Never assume Qwen (`matrix-coder`) is available during image mode.**

---

## What Runs in Images Mode

| Service | Container | Port | Backend | VRAM |
|---|---|---|---|---|
| ComfyUI | `comfyui_backend` | 8188 | ComfyUI + FLUX or other | ~30-40 GB |
| Gemma4 MoE | `ollama` | 11434 | Ollama | ~17 GB (on demand) |
| Embeddings | `ollama` | 11434 | Ollama | ~274 MB |
| Metrics | `node-exporter`, `dcgm-exporter` | 9100, 9400 | — | N/A |

**Total VRAM: ~48-58 GB of 72 GB → ~14-24 GB headroom**

### LiteLLM Aliases

| Alias | Status |
|---|---|
| `matrix-coder` | ❌ **OFFLINE** (vLLM stopped) |
| `matrix-gemma4-moe` | ✅ Available (Gemma4 on Ollama) |
| `embeddings` | ✅ Available |

Thor's LiteLLM proxy will return an error for `matrix-coder` since port 8000 is
down. Clients routed there should expect a connection failure and fall back to
`matrix-gemma4-moe` if available.

---

## How to Enter Images Mode

### Step-by-step

```bash
cd /home/chuck/homelab

# 1. Run preflight
bash scripts/preflight.sh images

# 2. Stop vLLM (releases ~48 GB VRAM)
docker compose -f compose.qwen36.yml down

# 3. Start ComfyUI
docker compose -f compose.comfyui.yml --profile image up -d

# 4. Verify Ollama is still running (it should be — don't touch it)
curl -s http://localhost:11434 | head -1

# 5. Verify ComfyUI
curl -s http://localhost:8188 | head -c 20
echo ""
```

### Expected timing

| Step | Time |
|---|---|
| vLLM stop | ~30 sec |
| ComfyUI start + model load | ~2-5 min (depends on which model is loaded) |
| **Total downtime for `matrix-coder`** | **~2-5 min** (already stopped after step 2) |

### What happens to active clients

- Any client calling `matrix-coder` gets a connection error immediately after vLLM stops
- `matrix-gemma4-moe` and `embeddings` remain available throughout
- No client-side config change needed — the error is transient during the switch

---

## How to Exit Images Mode (Return to Daily)

```bash
cd /home/chuck/homelab

# 1. Stop ComfyUI
docker compose -f compose.comfyui.yml --profile image down

# 2. Start vLLM
docker compose -f compose.qwen36.yml up -d

# 3. Verify
curl -s http://localhost:8000/v1/models | head -1
```

### Expected timing

| Step | Time |
|---|---|
| ComfyUI stop + VRAM release | ~30 sec |
| vLLM start + model load | ~2-5 min |
| **Total downtime** | **~2-5 min** |

---

## VRAM Budget Detail

| Component | Min | Max | Notes |
|---|---|---|---|
| ComfyUI (FLUX or similar) | ~30 GB | ~40 GB | Depends on model loaded and resolution |
| Gemma4-26B (Ollama) | ~17 GB | ~17 GB | Q4_K_M quantization |
| nomic-embed-text (Ollama) | ~0.3 GB | ~0.3 GB | Tiny |
| **Total** | **~47 GB** | **~58 GB** | |
| GPU total | — | 72 GB | |
| **Headroom** | **~14 GB** | **~25 GB** | |

### VRAM Risks

- If ComfyUI loads a very large model (e.g., 70B+ checkpoint), it may exhaust VRAM
- Gemma4 loads on demand. If both Gemma4 and a large ComfyUI model load
  simultaneously, Ollama may be evicted. This is acceptable — Gemma4 can reload.
- **Rule:** Don't run ComfyUI at maximum resolution + large model + Gemma4 all at once.

---

## ComfyUI Configuration

### Compose file: `compose.comfyui.yml`

| Setting | Value | Purpose |
|---|---|---|
| Image | `mmartial/comfyui-nvidia-docker:latest` | GPU-accelerated ComfyUI |
| Container name | `comfyui_backend` | Stable name for management |
| Profile | `image` | Requires `--profile image` to start |
| Port | `8188:8188` | ComfyUI web UI |
| shm_size | `4gb` | Shared memory for CUDA |
| `COMFYUI_FLAGS` | `--listen 0.0.0.0 --port 8188 --fp16-vae --reserve-vram 8` | FP16 VAE + 8 GB VRAM reservation |
| `CUDA_MALLOC_ASYNC` | `1` | Async CUDA memory allocator for better fragmentation |
| `SECURITY_LEVEL` | `weak` | Allow broader file access (internal only) |
| Volumes | `run:/comfy/mnt`, `basedir:/basedir` | Persistent data and workspace |
| Restart | `unless-stopped` | Auto-restart on crash (NOT on reboot — deliberate) |

### Data paths

```
/home/chuck/data/comfyui/
  run/          # ComfyUI runtime (models, outputs, venv, git checkout)
  basedir/      # User workspace for projects
```

### Security

- **Do NOT expose port 8188 publicly.** ComfyUI has no authentication by default.
- `SECURITY_LEVEL=weak` is intentional (internal network only) — allows file access
  for workspace operations.
- Access should be limited to LAN or via Thor reverse proxy.

---

## How Thor / Open WebUI Should Behave

### Thor LiteLLM

Thor's `thor.litellm.config.yml` has:

```yaml
- model_name: matrix-coder
    litellm_params:
      api_base: http://matrix:8000/v1
      num_retries: 2
```

During images mode, `http://matrix:8000` is unreachable. After 2 retries,
LiteLLM will raise an error. Clients receive a 503/504.

**Recommended client behavior:**
- Catch the error, display "Primary model offline"
- Offer `matrix-gemma4-moe` as fallback
- No automatic failover — this is a manual mode switch

### Open WebUI

If Open WebUI runs on Thor and uses `matrix-coder`:
- The chat interface will show an error for `matrix-coder`
- Users can manually switch to `matrix-gemma4-moe` in the UI
- Once vLLM restarts, `matrix-coder` is available again automatically

---

## Requesting Image Generation from Skills / Tools

### Current state

No skill currently integrates ComfyUI. The `presentation_build` skill
(mentioned in the project plan) may want to trigger image generation.

### How a skill would call ComfyUI (future)

ComfyUI exposes a REST API on port 8188:

```bash
# 1. Submit a workflow
curl -X POST http://matrix:8188/prompt \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": { ...workflow JSON... },
    "front": false
  }'

# 2. Poll for status
curl http://matrix:8188/history/<prompt_id>

# 3. Download output
curl http://matrix:8188/output/<filename>
```

### Future integration notes

- A skill should **never** start/stop ComfyUI or vLLM directly. It should only
  submit jobs to a running ComfyUI instance.
- If ComfyUI is not running (port 8188 not responding), the skill should
  return an error: "Image generation unavailable. Operator must switch to images mode."
- The skill should not attempt auto-switching modes. That's an operator decision.
- Workflow templates can live in `scripts/comfyui-workflows/` for reusable prompts.

---

## Preflight Validation

Run before switching:

```bash
bash scripts/preflight.sh images
```

This validates:
- ComfyUI compose file exists
- ComfyUI data directory exists
- Ollama is healthy (gemma4 + embeddings)
- VRAM budget fits
- Port 8188 status (warns if not listening — expected when switching in)

Typical output: `22 PASS, 3 WARN` (ComfyUI not running yet, HF_TOKEN empty, port not listening)

---

## Common Issues

### ComfyUI fails to start

```bash
# Check logs
docker logs comfyui_backend --tail 50

# Common causes:
# - Model files missing in /home/chuck/data/comfyui/run/
# - VRAM insufficient (check nvidia-smi)
# - Port 8188 in use (check docker ps)
```

### Gemma4 disappears during ComfyUI work

Ollama may unload Gemma4 if VRAM pressure is high. It will reload on next request
with a warm-up penalty (~30-60 sec). This is normal and acceptable.

### ComfyUI model downloads

ComfyUI downloads models on first run. Ensure `HF_TOKEN` is set in `.env` if
the model requires auth. Check `/home/chuck/data/comfyui/run/` for downloaded files.

---

## Compose File Reference

```yaml
# compose.comfyui.yml
services:
  comfyui:
    image: mmartial/comfyui-nvidia-docker:latest
    container_name: comfyui_backend
    profiles: ["image"]
    ports:
      - "8188:8188"
    shm_size: "4gb"
    environment:
      - COMFYUI_FLAGS=--listen 0.0.0.0 --port 8188 --fp16-vae --reserve-vram 8
      - CUDA_MALLOC_ASYNC=1
      - SECURITY_LEVEL=weak
      - USE_SOCAT=false
      - USE_UV=true
      - BASE_DIRECTORY=/basedir
    volumes:
      - /home/chuck/data/comfyui/run:/comfy/mnt
      - /home/chuck/data/comfyui/basedir:/basedir
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]
    restart: unless-stopped
```

---

## Switch Decision Flow

```
Is image generation needed?
  └─ Yes
      └─ Are you okay with matrix-coder being offline?
          └─ Yes → Enter images mode (stop vLLM, start ComfyUI)
          └─ No  → Use daily mode; skip image work or use Thor/Mac for images

Is image generation done?
  └─ Yes → Exit images mode (stop ComfyUI, start vLLM)
```

**Never run in images mode longer than necessary.** It degrades the primary
coding/chat experience by taking down the main model.
