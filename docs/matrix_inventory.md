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
| `qwen36` | vllm/vllm-openai:latest | 8000 | Primary inference (Qwen3.6-27B) | Running |
| `ollama` | ollama/ollama:latest | 11434 | Light inference + embeddings | Running |
| `node-exporter` | prom/node-exporter:latest | 9100 | Metrics (CPU, disk, etc.) | Running |
| `dcgm-exporter` | nvidia/dcgm-exporter:latest | 9400 | GPU metrics | Running |
| `vllm-gemma` | vllm/vllm-openai:latest | 8001 | Gemma vLLM (deprecated — see notes) | **Stopped** |
| `vllm-qwen` | vllm/vllm-openai:latest | 8000 | Old Qwen vLLM container | **Stopped** |
| `comfyui_backend` | mmartial/comfyui-nvidia-docker:latest | 8188 | Image generation | **Stopped** |
| `ollama-model-puller` | curlimages/curl:latest | — | One-off model pull | **Stopped** |

### vLLM-Qwen (active)

- **Model:** `Lorbus/Qwen3.6-27b-int4-AutoRound`
- **Served as:** `qwen36-27b`
- **Image:** `vllm/vllm-openai:v0.21.0`
- **Compose file:** `compose.qwen36.yml`
- **GPU memory util:** 0.66 (set via compose; overridden to 0.66 in container)
- **Max model len:** 200,000 tokens
- **Max num seqs:** 3
- **Max batched tokens:** 8192
- **KV cache dtype:** fp8
- **Prefix caching:** enabled
- **Chunked prefill:** enabled
- **Tool call parser:** qwen3_xml
- **Auto tool choice:** enabled
- **HF model cache:** `/home/chuck/data/models` (34 GB)
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
| `homelab` | running(4) | `compose.metrics.yml`, `compose.qwen36.yml`, `compose.ollama.yml` |

### Compose Files Present

| File | Service(s) | Role |
|---|---|---|
| `compose.qwen36.yml` | `qwen36` | vLLM Qwen3.6-27B |
| `compose.ollama.yml` | `ollama` | Ollama (Gemma4 MoE + embeddings) |
| `compose.metrics.yml` | `node-exporter`, `dcgm-exporter` | Monitoring |
| `compose.comfyui.yml` | `comfyui` (profile: `image`) | ComfyUI / FLUX |

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
| `HF_TOKEN` | *(empty)* |
| `QWEN_VLLM_MODEL` | `Lorbus/Qwen3.6-27b-int4-AutoRound` |
| `QWEN_GPU_MEM` | `0.56` (but compose overrides to `0.66`) |

## Model Storage

| Path | Size | Contents |
|---|---|---|
| `/home/chuck/data/models/hub/` | 34 GB | HuggingFace cache (vLLM model weights) |
| `/home/chuck/data/models/xet/` | 1.2 MB | HF Xet cache |
| `/home/chuck/data/ollama/` | 34 GB | Ollama models (3 models: 17+17+0.3 GB) |
| `/home/chuck/data/comfyui/` | *(exists but not measured)* | ComfyUI workspace |

## Discrepancies Found

1. ~~`switch.sh` references non-existent compose files~~ — **RESOLVED**: `switch.sh` deleted in Phase 1, replaced by model manager design.
2. **vLLM container runs at gpu-memory-utilization 0.66** but `.env` has `QWEN_GPU_MEM=0.56`. The compose file hardcodes 0.66, so the env var is ignored for the main setting.
3. ~~`OLLAMA_KEEP_ALIVE` differs between compose and runtime~~ — **RESOLVED**: aligned to `5m` across compose and profiles.
4. **Two old stopped vLLM containers** (`vllm-gemma`, `vllm-qwen`) from prior modes. Stale containers take up disk.
5. ~~Grafana/Prometheus not running on Matrix~~ — **RESOLVED**: Grafana/Prometheus run on Thor. Matrix exporters are scraped by Thor.
6. ~~`switch.sh` mode names don't match the plan~~ — **RESOLVED**: `switch.sh` deleted; model manager uses plan mode names.
