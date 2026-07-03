# Matrix Monitoring & Health

> Created: 2026-07-03
> Scope: Health endpoints, metrics, and status reporting for the Matrix appliance

## Overview

Matrix exposes health endpoints and metrics through four categories:

| Tier | Audience | Content | Access |
|---|---|---|---|
| **Infrastructure** (node-exporter, dcgm-exporter) | Grafana / Prometheus / Admin | System + GPU metrics | Port-scraped by collector |
| **Service Health** (vLLM, Ollama, ComfyUI) | Model Manager / Preflight / CLI | Up/down + model status | HTTP endpoints |
| **Operational Status** (mode, profiles, VRAM) | Operator / CLI | What's running, what mode | File-based + CLI |
| **External Contract** (Thor, portal) | Thor LiteLLM / downstream clients | Service availability | HTTP health checks |

---

## Infrastructure Metrics (Admin)

These exporters feed Grafana/Prometheus. They are **not** exposed externally and are
not part of the Thor integration contract.

### node-exporter

| Property | Value |
|---|---|
| Container | `node-exporter` |
| Image | `prom/node-exporter:latest` |
| Port | `9100` |
| Compose | `compose.metrics.yml` |
| Health check | `curl -s http://localhost:9100/metrics` |

**Key metrics available:**
- `node_memory_MemTotal_bytes`, `node_memory_MemAvailable_bytes` — system RAM
- `node_filesystem_avail_bytes` — disk free
- `node_cpu_seconds_total` — CPU usage
- `node_network_receive_bytes_total` — network I/O

### dcgm-exporter

| Property | Value |
|---|---|
| Container | `dcgm-exporter` |
| Image | `nvidia/dcgm-exporter:latest` |
| Port | `9400` |
| Compose | `compose.metrics.yml` |
| Health check | `curl -s http://localhost:9400/metrics` |

**Key metrics available:**
- `DCGM_FI_DEV_GPU_TEMP` — GPU temperature (°C)
- `DCGM_FI_DEV_MEMORY_USED` — VRAM used (MB)
- `DCGM_FI_DEV_POWER_USAGE` — power draw (W)
- `DCGM_FI_DEV_SM_CLOCK`, `DCGM_FI_DEV_MEM_CLOCK` — clock frequencies
- `DCGM_FI_DEV_FB_FREE` — framebuffer free (MB)
- `DCGM_FI_DEV_ENC_UTIL`, `DCGM_FI_DEV_DEC_UTIL` — encode/decode utilization

### Exporter Data Consumer

**Grafana and Prometheus run on Thor, not Matrix.** The exporters on Matrix expose
Prometheus-format metrics for Thor's Prometheus to scrape. Grafana on Thor provides
dashboards. Matrix remains a compute-only appliance — no monitoring stack installed locally.

**Thor Prometheus should scrape:**
- `http://matrix:9100/metrics` (node-exporter)
- `http://matrix:9400/metrics` (dcgm-exporter)

---

## Service Health Endpoints

These are checked by `preflight.sh` and the model manager during mode switches.

### vLLM (Qwen3.6-27B)

| Property | Value |
|---|---|
| Container | `qwen36` |
| Port | `8000` |
| Health endpoint | `GET /v1/models` |
| Compose | `compose.qwen36.yml` |

**Health check:**
```bash
curl -sf --max-time 3 http://localhost:8000/v1/models
```

**Response format:**
```json
{
  "object": "list",
  "data": [
    { "id": "qwen36-27b", "object": "model", "root": "Lorbus/Qwen3.6-27b-int4-AutoRound" }
  ]
}
```

**Non-standard vLLM metrics (when LiteLLM prometheus callback is active on Thor):**
- `litellm_llm_duration_ms` — per-request latency
- `litellm_total_requests` — request count by model

vLLM itself does NOT expose a `/metrics` endpoint in the `vllm/vllm-openai:latest`
image. Latency/request metrics flow through LiteLLM on Thor.

### Ollama

| Property | Value |
|---|---|
| Container | `ollama` |
| Port | `11434` |
| Health endpoint | `GET /` |
| Model list | `GET /api/tags` |
| Compose | `compose.ollama.yml` |

**Health check:**
```bash
curl -sf --max-time 3 http://localhost:11434
```
Expected response: `Ollama is running`

**Loaded models:**
```bash
curl -sf http://localhost:11434/api/tags | python3 -c "
import sys, json
data = json.load(sys.stdin)
for m in data.get('models', []):
    print(f\"  {m['name']} — {m['size'] / 1024 / 1024:.0f} MB, {m['details']['parameter_size']} params\")
"
```

**Current models:**
| Model | Size | Params | Quantization |
|---|---|---|---|
| `nomic-embed-text:latest` | 274 MB | 137M | F16 |
| `qwen3.6:27b` | 17 GB | 27B | Q4_K_M |
| `gemma4:26b` | 17 GB | 26B | Q4_K_M |

**Note:** Ollama has no `/metrics` endpoint. GPU memory used by Ollama models
is tracked via `nvidia-smi` / dcgm-exporter.

### ComfyUI

| Property | Value |
|---|---|
| Container | `comfyui_backend` |
| Port | `8188` |
| Health check | `GET /` (HTTP 200 on any response) |
| Compose | `compose.comfyui.yml` (profile: `image`) |

**Health check:**
```bash
curl -sf --max-time 5 http://localhost:8188 > /dev/null
```

ComfyUI has no standardized health endpoint. Port responsiveness is the best indicator.

---

## Operational Status (CLI)

### Active Mode

**File:** `state/current_mode`

Plain text file containing the active mode name (`daily`, `qwen-coder`, `qwen-long`,
`llms`, `experiment`, `images`). Set by the model manager during mode switches.

```bash
cat /home/chuck/homelab/state/current_mode
# → daily
```

### Active Profiles

**File:** `state/active_profiles`

Space-separated list of profile names currently running.

```bash
cat /home/chuck/homelab/state/active_profiles
# → matrix-coder matrix-gemma4-moe embeddings
```

### Switch Log

**File:** `state/switch_log.md`

Append-only log of mode transitions (from model manager):

```markdown
| Timestamp | From | To | Result | Duration |
|---|---|---|---|---|
| 2026-07-03T00:34:15 | daily | daily | success | 0s (restart) |
```

### Rollback Target

**File:** `state/last_mode`

The previous mode, written *before* a switch begins (guarantees valid rollback target).

---

## GPU Status

### Quick Check

```bash
nvidia-smi --query-gpu=name,temperature.gpu,memory.used,memory.total,utilization.gpu,power.draw --format=csv,noheader
```

**Current typical output (daily mode):**
```
NVIDIA RTX PRO 5000 72GB Blackwell, 84, 48840 MiB, 73415 MiB, 100 %, 300.63 W
```

### VRAM Budget by Mode

| Mode | vLLM | Ollama | ComfyUI | Total | Headroom |
|---|---|---|---|---|---|
| `daily` | ~48.7 GB | ~17 GB + 0.3 GB | — | ~66 GB | ~6 GB |
| `qwen-coder` | ~55-60 GB | ~0.3 GB | — | ~55-60 GB | ~12-17 GB |
| `qwen-long` | ~50-55 GB | ~0.3 GB | — | ~50-55 GB | ~17-22 GB |
| `experiment` | varies | varies | — | varies | varies |
| `images` | — | ~17 GB + 0.3 GB | ~30-40 GB | ~48-58 GB | ~14-24 GB |

### VRAM Warning Thresholds

| Metric | Warning | Critical |
|---|---|---|
| VRAM headroom | < 4 GB | < 1 GB |
| GPU temperature | > 85°C | > 95°C |
| Power draw | > 280 W | > 300 W (limit) |

---

## Sanitized Status for Portal / Remote Check

When exposing Matrix status to external consumers (Thor portal, status page),
use this sanitized view — no admin ports or sensitive paths:

```bash
#!/usr/bin/env bash
# matrix_status.sh — Sanitized status for portal consumption
# Output: JSON suitable for external status pages

cat /home/chuck/homelab/state/current_mode 2>/dev/null || echo "unknown"
```

**Portal-friendly JSON (future model-manager `status` command):**

```json
{
  "appliance": "Matrix",
  "mode": "daily",
  "gpu": {
    "name": "NVIDIA RTX PRO 5000 72GB Blackwell",
    "temperature_c": 84,
    "vram_used_gb": 48.7,
    "vram_total_gb": 72.0,
    "utilization_pct": 100
  },
  "services": {
    "matrix-coder": { "status": "up", "port": 8000, "model": "qwen36-27b" },
    "matrix-gemma4-moe": { "status": "up", "port": 11434, "model": "gemma4:26b" },
    "embeddings": { "status": "up", "port": 11434, "model": "nomic-embed-text" }
  },
  "disk": {
    "available_tb": 1.4,
    "total_tb": 1.7
  },
  "last_switch": "2026-07-03T00:34:15Z"
}
```

---

## Health Check Script

A consolidated health check is available via the preflight script and can be
summarized for quick CLI use:

```bash
# Full preflight for current mode
bash scripts/preflight.sh $(cat state/current_mode 2>/dev/null || echo daily)

# Quick health summary (future model-manager health command)
bash scripts/matrix_health.sh
```

### Planned `matrix_health.sh` Output

```
Matrix Health — 2026-07-03T14:30:00
Mode: daily

  vLLM  : UP   (qwen36-27b, port 8000)
  Ollama: UP   (gemma4:26b, nomic-embed-text, port 11434)
  ComfyUI: DOWN (not running — expected in daily mode)
  node-exporter: UP (port 9100)
  dcgm-exporter: UP (port 9400)

  GPU: 48.7 GB / 72.0 GB (67%), 84°C, 300W
  Disk: 1.4 TB free / 1.7 TB

  Status: HEALTHY
```

---

## Grafana Dashboard Plan (On Thor)

These panels should be created on Thor's Grafana instance, scraping Matrix exporters
via Thor's Prometheus:

| Panel | Source | Metric |
|---|---|---|
| VRAM Usage | dcgm-exporter | `DCGM_FI_DEV_MEMORY_USED` |
| GPU Temperature | dcgm-exporter | `DCGM_FI_DEV_GPU_TEMP` |
| Power Draw | dcgm-exporter | `DCGM_FI_DEV_POWER_USAGE` |
| GPU Utilization | nvidia-smi / dcgm | `DCGM_FI_DEV_GPU_UTIL` |
| System RAM | node-exporter | `node_memory_MemAvailable_bytes` |
| Disk Free | node-exporter | `node_filesystem_avail_bytes` |
| Active Mode | File-based | `state/current_mode` |
| LiteLLM Latency | LiteLLM prometheus callback (Thor) | `litellm_llm_duration_ms` |

---

## Monitoring Architecture

| Component | Location | Rationale |
|---|---|---|
| **Exporters** (node-exporter, dcgm-exporter) | Matrix | Must run close to the hardware |
| **Prometheus** | Thor | Centralized scraping of Matrix exporters |
| **Grafana** | Thor | Dashboards alongside application monitoring |
| **LiteLLM metrics** | Thor | Callback is in `thor.litellm.config.yml` |
| **Service health** | Matrix | HTTP endpoints on each service |

---

## Files & References

| File | Purpose |
|---|---|
| `compose.metrics.yml` | node-exporter + dcgm-exporter compose |
| `scripts/preflight.sh` | Health checks during mode validation |
| `state/current_mode` | Active mode name |
| `state/active_profiles` | Running profile list |
| `state/last_mode` | Rollback target |
| `state/switch_log.md` | Mode switch history |
| `docs/matrix_manual_tasks.md` | Resolved tasks (Grafana/Prometheus on Thor, KEEP_ALIVE=5m) |
| `thor.litellm.config.yml` | LiteLLM prometheus callback config (on Thor) |
