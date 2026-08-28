# Matrix Inventory

> Captured 2026-07-03. Do not edit — append only.

## Host

| Field | Value |
|---|---|
| **Hostname** | matrix |
| **LAN IP** | 192.168.4.55 |
| **Docker bridges** | 172.17.0.1, 172.18.0.1 |
| **OS** | Ubuntu (LVM on NVMe) |
| **Disk** | 1.7 TB total, 208 GB used (14%) |
| **RAM** | 62 GiB total, 7.3 GiB used, 54 GiB available |
| **Swap** | 8 GiB (unused) |

## GPU

| Field | Value |
|---|---|
| **Model** | NVIDIA RTX PRO 5000 72GB Blackwell |
| **Driver** | 595.71.05 |
| **CUDA** | 13.2 |
| **VRAM total** | 73,415 MiB |
| **VRAM used** | ~48,760 MiB (vLLM) |
| **VRAM free** | ~24,049 MiB |
| **Temp** | 85°C |
| **Power** | 300W / 300W |
| **GPU util** | 100% |

## Strategic Boundaries

**Matrix is a compute-only appliance.** It has spare CPU, RAM, and disk, but that is
**not** a reason to move services here. The boundaries are:

| What Matrix Owns | What Matrix Does NOT Own |
|---|---|
| vLLM inference (primary) | Reverse proxy / Caddy |
| Ollama (Gemma MoE + embeddings) | LiteLLM |
| Model storage & caching | Qdrant / Redis |
| ComfyUI image generation | Grafana / Prometheus (on Thor) |
| GPU metrics export | Postgres / databases |
| Runtime mode switching | Open WebUI |
| Preflight & benchmarking | User management |
| Model profiles | Public-facing APIs |

- **Thor owns orchestration and applications.** Matrix ports are LAN-only and consumed
  by Thor through LiteLLM. No client should talk to Matrix ports directly.
- **Grafana and Prometheus run on Thor.** Matrix exporters (node-exporter :9100, dcgm-exporter :9400)
  expose Prometheus-format metrics for Thor to scrape.
- **No exceptions** unless Chuck explicitly approves them in `matrix_manual_tasks.md`.
- **Matrix ports must never be publicly exposed.**

---

## Running Containers

| Container | Image | Port(s) | Role | Status |
|---|---|---|---|---|
| `qwen38` | vllm/vllm-openai:latest | 8000 | Primary inference (Qwen3.8-27B NVFP4) | Running |
| `ollama` | ollama/ollama:latest | 11434 | Light inference + embeddings | Running |
| `node-exporter` | prom/node-exporter:latest | 9100 | Metrics (CPU, disk, etc.) | Running |
| `dcgm-exporter` | nvidia/dcgm-exporter:latest | 9400 | GPU metrics | Running |
| `vllm-gemma` | vllm/vllm-openai:latest | 8001 | Gemma vLLM (deprecated — see notes) | **Stopped** |
| `vllm-qwen` | vllm/vllm-openai:latest | 8000 | Old Qwen vLLM container | **Stopped** |
| `comfyui_backend` | mmartial/comfyui-nvidia-docker:latest | 8188 | Image generation | **Stopped** |
| `ollama-model-puller` | curlimages/curl:latest | — | One-off model pull | **Stopped** |

### vLLM-Qwen3.8 (active)

- **Model:** `unsloth/Qwen3.8-27B-NVFP4`
- **Served as:** `qwen38-27b`
- **Image:** `vllm/vllm-openai:latest` (v0.24.0)
- **Compose file:** `compose/qwen-coder.yml`
- **GPU memory util:** 0.75
- **Max model len:** 196,608 tokens
- **Max num seqs:** 3
- **Max batched tokens:** 8192
- **KV cache dtype:** fp8
- **Prefix caching:** enabled
- **Chunked prefill:** enabled
- **Tool call parser:** qwen3_coder
- **Reasoning parser:** qwen3 (`preserve_thinking`, `reasoning_effort=high`)
- **MTP:** 2 speculative tokens (tuned from 3 on 2026-08-25 — 3rd token acceptance ~55%)
- **Auto tool choice:** enabled
- **Vision:** enabled (image + video)
- **HF model cache:** `/home/chuck/data/models`
- **shm_size:** 16 GB

### Ollama (active)

- **Configured models:** `nomic-embed-text:latest` (274 MB), `qwen3.6:27b` (17 GB), `gemma4:26b` (17 GB)
- **Active in LiteLLM:** `gemma4:26b` (as `matrix-gemma4-moe`), `nomic-embed-text` (as `embeddings`)
- **OLLAMA_KEEP_ALIVE:** 5m (aligned)
- **OLLAMA_MAX_LOADED_MODELS:** 2
- **OLLAMA_MAX_QUEUE:** 8
- **Data dir:** `/home/chuck/data/ollama` (34 GB)

## Docker Compose Project

| Project | Status | Config Files |
|---|---|---|
| `homelab` | running(4) | `compose/metrics.yml`, `compose/qwen-coder.yml`, `compose/gemma4-moe.yml` |

### Compose Files Present

| File | Service(s) | Role |
|---|---|---|
| `compose/qwen-coder.yml` | `qwen38` | vLLM Qwen3.8-27B NVFP4 (primary) |
| `compose/qwen-long.yml` | `qwen38` | vLLM Qwen3.6-27B INT4 (long-context mode, 240K ctx) |
| `compose/gemma4-moe.yml` | `ollama` | Ollama (Gemma4 MoE + embeddings) |
| `compose/metrics.yml` | `node-exporter`, `dcgm-exporter` | Monitoring |
| `compose/comfyui.yml` | `comfyui` (profile: `image`) | ComfyUI (Qwen-Image create + edit) |
| `compose/experiment.yml` | — | vLLM template for candidate models (copy & edit) |

## Docker Networks

| Network | Driver | Scope |
|---|---|---|
| `bridge` | bridge | local |
| `homelab_default` | bridge | local |
| `host` | host | local |
| `none` | null | local |

## Docker Volumes

None (all state is bind-mounted from host).

## Metrics / Observability

| Exporter | Port | Status | Consumer |
|---|---|---|---|
| node-exporter | 9100 | Running | Thor Prometheus |
| dcgm-exporter | 9400 | Running | Thor Prometheus |

**Grafana and Prometheus run on Thor.** Matrix only exposes the exporter endpoints
for Thor to scrape. No monitoring stack installed locally.

## LiteLLM Config (on Thor)

| Alias | Model | Backend | Host:Port |
|---|---|---|---|
| `matrix-coder` | Qwen3.6-27B | vLLM | matrix:8000 |
| `matrix-gemma4-moe` | Gemma4 26B MoE | Ollama | matrix:11434 |
| `embeddings` | Nomic Embed Text | Ollama | matrix:11434 |
| `studio-gemma4-4b` | Gemma-4b | LMStudio | macstudio:1234 |

## Switch Script

| Field | Value |
|---|---|
| **Status** | **DELETED** — removed in Phase 1, replaced by model manager design (Phase 4) |

## Env File

| Variable | Value |
|---|---|
| `HF_TOKEN` | *(set — used by vLLM to pull gated models)* |
| `QWEN_VLLM_MODEL` | `Lorbus/Qwen3.6-27b-int4-AutoRound` (legacy — compose hardcodes the model path) |
| `QWEN_GPU_MEM` | `0.56` (legacy — compose hardcodes `0.75`) |

## Model Storage

| Path | Size | Contents |
|---|---|---|
| `/home/chuck/data/models/hub/` | 34 GB | HuggingFace cache (vLLM model weights) |
| `/home/chuck/data/models/xet/` | 1.2 MB | HF Xet cache |
| `/home/chuck/data/ollama/` | 34 GB | Ollama models (3 models: 17+17+0.3 GB) |
| `/home/chuck/data/comfyui/` | *(exists but not measured)* | ComfyUI workspace |

## Discrepancies Found

1. ~~`switch.sh` references non-existent compose files~~ — **RESOLVED**: `switch.sh` deleted in Phase 1, replaced by model manager design.
2. **vLLM container runs at gpu-memory-utilization 0.75** but `.env` has `QWEN_GPU_MEM=0.56`. The compose file hardcodes 0.75, so the env var is ignored (legacy value).
3. ~~`OLLAMA_KEEP_ALIVE` differs between compose and runtime~~ — **RESOLVED**: aligned to `5m` across compose and profiles.
4. **Two old stopped vLLM containers** (`vllm-gemma`, `vllm-qwen`) from prior modes. Stale containers take up disk.
5. ~~Grafana/Prometheus not running on Matrix~~ — **RESOLVED**: Grafana/Prometheus run on Thor. Matrix exporters are scraped by Thor.
6. ~~`switch.sh` mode names don't match the plan~~ — **RESOLVED**: `switch.sh` deleted; model manager uses plan mode names.

---

## Snapshot 2026-08-28 (supersedes the 2026-07-03 snapshot for current state)

> Captured 2026-08-28 after the Qwen3.8 promotion (2026-08-24), the media-pipeline
> launch (2026-08-26/27), and the ComfyUI legacy-model cleanup (2026-08-28).

## Host

| Field | Value |
|---|---|
| **Hostname** | matrix |
| **LAN IP** | 192.168.4.55 |
| **OS** | Ubuntu (LVM on NVMe) |
| **Disk** | 1.7 TB total, 540 GB used (34%) |
| **RAM** | 62 GiB total, ~7.4 GiB used, 54 GiB available |

## GPU

| Field | Value |
|---|---|
| **Model** | NVIDIA RTX PRO 5000 72GB Blackwell |
| **Driver** | 595.71.05 |
| **VRAM total** | 73,415 MiB |
| **VRAM used (steady, qwen-coder mode)** | ~56 GB (vLLM ~55 GB + ComfyUI idle ~0.7 GB) |

## Running Containers

| Container | Image | Port(s) | Role | Status |
|---|---|---|---|---|
| `qwen38` | vllm/vllm-openai:latest | 8000 | Primary inference (Qwen3.8-27B NVFP4) | Running |
| `ollama` | ollama/ollama:latest | 11434 | Light inference + embeddings | Running |
| `comfyui_backend` | mmartial/comfyui-nvidia-docker:latest | 8188 | Image/video generation (Qwen-Image, LTXV, SeedVR2, MMAudio, ACE-Step, XTTS) | Running |
| `media_pipeline` | media-pipeline:latest | 8189 | Media orchestrator API (storyboard→shots→assemble) | Running |
| `node-exporter` | prom/node-exporter:latest | 9100 | Metrics | Running |
| `dcgm-exporter` | nvidia/dcgm-exporter:latest | 9400 | GPU metrics | Running |

### Stopped (experiment leftovers — optional cleanup)

`qwen36`, `qwen38-fp8`, `qwen38-nvfp4`, `qwen36-long`, `qwen3-next-80b`, `qwen36-perf`, `qwen36-mtp`

## vLLM (active)

- **Model:** `unsloth/Qwen3.8-27B-NVFP4`, served as `qwen38-27b`
- **Image:** `vllm/vllm-openai:latest`
- **Compose:** `compose/qwen-coder.yml`
- **Context:** 196,608 tokens · **GPU mem util:** 0.75 · **Max seqs:** 3
- **KV cache:** fp8 · **Prefix caching:** on · **Chunked prefill:** on
- **MTP:** 2 speculative tokens (tuned from 3 on 2026-08-25)
- **Tool parser:** `qwen3_coder` · **Reasoning parser:** `qwen3` (`preserve_thinking`)
- **Vision:** enabled (image + video) · **Throughput:** ~124 tok/s (benchmark `20260825_222844`)

## Ollama (active)

- **Configured models:** `gemma4:26b` (18 GB), `qwen3.6:27b` (17.4 GB), `nomic-embed-text:latest` (0.3 GB)
- **Active in LiteLLM:** `gemma4:26b` (as `matrix-gemma4-moe`, loaded on demand), `nomic-embed-text` (as `embeddings`)
- **Data dir:** `/home/chuck/data/ollama` (34 GB)

## Media Stack (new since 2026-07-03)

| Component | Location | Role |
|---|---|---|
| `media-pipeline` service | `media-pipeline/` (repo) | FastAPI orchestrator, port 8189; builds ComfyUI workflows programmatically (no JSON workflow files) |
| `comfyui_backend` | `compose/comfyui.yml` (profile `image`) | ComfyUI + custom nodes (GGUF, SeedVR2, MMAudio, VideoHelperSuite, Manager) |
| `media-mcp-client` | `media-mcp-client/` (repo) | Thin stdlib HTTP client + FastMCP tools for remote machines |
| Model data | `/home/chuck/data/comfyui/basedir/models/` | ~77 GB after 2026-08-28 cleanup (was ~110 GB); total comfyui workspace 109 GB (was 152 GB) |

**Cleanup 2026-08-28:** removed ~55 GB of obsolete models (SD1.5/SDXL/SVD checkpoints,
old LTXV 0.9.8 + SeedVR2 int8 builds, duplicate XTTS dir, junk VAEs), 6 legacy workflow
JSONs (1 kept as reference), 4 obsolete custom nodes, scratch/venv caches. All 25
pipeline models verified intact. See `docs/matrix_comfyui_media_api.md` changelog.

## Docker Compose Project

| Project | Status | Config Files |
|---|---|---|
| `compose` | running(6) | `compose/comfyui.yml`, `compose/qwen-coder.yml`, `compose/gemma4-moe.yml`, `compose/metrics.yml` |

## Model Storage

| Path | Size | Contents |
|---|---|---|
| `/home/chuck/data/models/hub/` | 168 GB | HuggingFace cache (vLLM model weights incl. Qwen3.8 NVFP4 + experiment candidates) |
| `/home/chuck/data/ollama/` | 34 GB | Ollama models (gemma4 + qwen3.6 + embeddings) |
| `/home/chuck/data/comfyui/` | ~109 GB | ComfyUI workspace (models ~77 GB + run/ venvs + media_jobs) |

## Discrepancies / Notes (2026-08-28)

1. **`.env` `HF_TOKEN` is empty** — latent; only matters for future gated downloads (see `docs/matrix_manual_tasks.md`).
2. **`.env` legacy vars** `QWEN_VLLM_MODEL` / `QWEN_VLLM_GPU_MEM` are ignored (compose hardcodes values).
3. **ComfyUI venv/uv_cache live in `run/` (bind-mounted)** — the container entrypoint self-heals and rebuilds the venv on restart (verified 2026-08-28: restart repopulated venv + uv_cache in ~15 min). **After any venv rebuild**, run `scripts/comfyui_venv_deps.sh restart` — the fresh venv lacks custom-node deps (numba/librosa/gguf/imageio-ffmpeg) and resolves numpy 2.5, which breaks numba (comfyui-mmaudio import failure).
4. **`state/current_mode` = `qwen-coder`** (since 2026-08-23 experiment → qwen-coder switch).
