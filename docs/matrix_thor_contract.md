# Matrix — Thor Integration Contract

> Created: 2026-07-03
> Version: 1.0
> Maintainer: Chuck (homelab operator)

**This document defines the interface between Thor (application/orchestration layer)
and Matrix (compute appliance). Thor should call Matrix *only* through the endpoints
defined here. All other access requires explicit operator approval.**

---

## 1. Network Identity

| Property | Value |
|---|---|
| **Hostname** | `matrix` |
| **IP Address** | `192.168.4.55` |
| **Network** | LAN (homelab internal) |
| **DNS Resolution** | Matrix hostname resolvable from Thor via network DNS/hosts |
| **Access Control** | All ports LAN-only; no public exposure |

**How Thor should address Matrix:** Use the hostname `matrix` (e.g., `http://matrix:8000`),
not the IP address. The IP may change.

---

## 2. Published Endpoints

### Production Endpoints (Available in Normal Operation)

| Port | Service | Protocol | Purpose | Always Available? |
|---|---|---|---|---|
| `8000` | vLLM | HTTP/JSON (OpenAI-compatible) | Primary model inference (`matrix-coder`) | ✅ Yes (except `images` mode) |
| `11434` | Ollama | HTTP/JSON | Light model + embeddings (`matrix-gemma4-moe`, `embeddings`) | ✅ Yes (all modes except `qwen-coder`/`qwen-long`) |

### Infrastructure Endpoints (Thor Prometheus Scrape)

| Port | Service | Protocol | Purpose |
|---|---|---|---|
| `9100` | node-exporter | Prometheus text format | System metrics (CPU, RAM, disk, network) |
| `9400` | dcgm-exporter | Prometheus text format | GPU metrics (temp, VRAM, power, utilization) |

**Thor Prometheus should scrape:** `http://matrix:9100/metrics` and `http://matrix:9400/metrics`.
**Grafana on Thor** provides dashboards. Matrix has no local monitoring stack.

### Optional Endpoints (Mode-Dependent)

| Port | Service | Protocol | Available In |
|---|---|---|---|
| `8188` | ComfyUI | HTTP/JSON | `images` mode only (internal, not for Thor) |

---

## 3. Model Aliases (Thor LiteLLM)

Thor's `thor.litellm.config.yml` defines these aliases that route to Matrix:

| Alias | Thor LiteLLM Config | Matrix Endpoint | Valid In Modes |
|---|---|---|---|
| `matrix-coder` | `openai/qwen36-27b` → `http://matrix:8000/v1` | vLLM port 8000 | `daily`, `qwen-coder`, `qwen-long`, `llms`, `experiment` |
| `matrix-gemma4-moe` | `ollama/gemma4:26b` → `http://matrix:11434` | Ollama port 11434 | `daily`, `llms`, `images` |
| `embeddings` | `ollama/nomic-embed-text` → `http://matrix:11434` | Ollama port 11434 | `daily`, `llms`, `images`, `experiment` |

### Critical Rule: The `matrix-coder` Alias Is Stable

The `matrix-coder` alias **always points to `http://matrix:8000`**. It never changes.
When Matrix enters `experiment` mode, a different model runs on port 8000, but the
alias stays the same. Clients never need config changes.

| Mode | `matrix-coder` → | `matrix-gemma4-moe` → | `embeddings` → |
|---|---|---|---|
| `daily` | Qwen3.6-27B (vLLM :8000) ✅ | Gemma4-26B (Ollama :11434) ✅ | nomic-embed-text (Ollama :11434) ✅ |
| `qwen-coder` | Qwen3.6-27B @ higher VRAM ✅ | ❌ OFFLINE (gemma4 unloaded) | ✅ |
| `qwen-long` | Qwen3.6-27B long-context ✅ | ❌ OFFLINE | ✅ |
| `llms` | Qwen3.6-27B ✅ | Gemma4-26B ✅ | ✅ |
| `experiment` | *Candidate model* on :8000 ✅ | Depends on candidate size | Depends on candidate size |
| `images` | ❌ OFFLINE (vLLM stopped) | Gemma4-26B ✅ | ✅ |

---

## 4. Health Checks

Thor should monitor Matrix service availability via these endpoints:

### vLLM Health

```
GET http://matrix:8000/v1/models
Expected: HTTP 200, JSON {"object":"list","data":[...]}
Timeout: 3s
```

### Ollama Health

```
GET http://matrix:11434
Expected: HTTP 200, body "Ollama is running"
Timeout: 3s
```

### Metrics Health

```
GET http://matrix:9100/metrics   (node-exporter)
GET http://matrix:9400/metrics   (dcgm-exporter)
Expected: HTTP 200, Prometheus text format
Timeout: 5s
```

### Health Check Summary for Thor

| Service | Endpoint | Expected Status | Retry |
|---|---|---|---|
| vLLM | `http://matrix:8000/v1/models` | 200 | LiteLLM `num_retries: 2` |
| Ollama | `http://matrix:11434` | 200 | LiteLLM `num_retries: 2` |
| node-exporter | `http://matrix:9100/metrics` | 200 | Prometheus scrape timeout 10s |
| dcgm-exporter | `http://matrix:9400/metrics` | 200 | Prometheus scrape timeout 10s |

---

## 5. Failure Behavior

### vLLM (port 8000) is Down

- **Cause:** Mode switch to `images`, manual stop, or crash
- **Thor impact:** `matrix-coder` alias fails after `num_retries: 2` (~3-6 seconds)
- **Client sees:** HTTP 503/504 from LiteLLM
- **Thor behavior:** No automatic failover. The alias remains configured; errors propagate to the client.
- **Recovery:** Operator restarts vLLM on Matrix; alias recovers automatically within seconds of the container being healthy.

### Ollama (port 11434) is Down

- **Cause:** Crash or manual stop
- **Thor impact:** `matrix-gemma4-moe` and `embeddings` aliases fail after retries
- **Client sees:** HTTP 503/504 from LiteLLM
- **Recovery:** Ollama container has `restart: unless-stopped`; auto-recovers unless explicitly stopped.

### Both vLLM and Ollama Down

- **Cause:** Matrix reboot, power event, or comprehensive mode switch
- **Thor impact:** All Matrix aliases return errors
- **Duration:** ~2-10 minutes depending on mode
- **Thor behavior:** Clients receive errors; no automatic recovery on the Matrix side beyond container auto-restart.

### Metrics Exporters Down

- **Cause:** Container restart, crash
- **Thor impact:** Prometheus scrape failures; gaps in dashboards
- **Auto-recovery:** Both have `restart: unless-stopped`
- **Action:** Operator investigates if not auto-recovering

### Network Partition Between Thor and Matrix

- **Symptom:** All Matrix endpoints unreachable from Thor
- **Thor behavior:** LiteLLM retries exhaust, clients see 503/504
- **Diagnosis:** Check network, ping, routing
- **Recovery:** Fix network; Matrix services recover once connectivity restored

---

## 6. Mode Switching Impact

Mode switches are **operator-initiated on Matrix**. Thor is NOT notified in advance.

| Transition | Duration | Impact on Thor |
|---|---|---|
| `daily` → `images` | ~2-5 min | `matrix-coder` offline during switch, then permanently offline while in `images` |
| `images` → `daily` | ~2-5 min | `matrix-coder` offline during switch, then recovers |
| `daily` → `qwen-coder` | ~2-5 min | `matrix-gemma4-moe` offline during/after; `matrix-coder` briefly down during restart |
| `daily` → `qwen-long` | ~2-5 min | `matrix-gemma4-moe` offline during/after; `matrix-coder` briefly down during restart |
| `daily` → `experiment` | ~2-5 min | `matrix-coder` alias works but points to a different model temporarily |
| `daily` → `llms` | ~0 min | No change (same as daily) |

**Thor should expect brief outages (~30 sec) for any mode switch that involves restarting
a service. Extended outages indicate a longer mode change (e.g., `images` mode).**

---

## 7. Rollback

### From Thor's Perspective

Thor does not initiate rollbacks. The operator on Matrix handles mode switches.
If a mode switch causes problems:

1. **Operator action on Matrix:** Switch back to `daily` (or previous mode)
2. **Effect on Thor:** Services recover within 2-5 minutes of the rollback
3. **Thor sees:** Health checks pass again, aliases respond normally

### Manual Recovery Steps (for Operator on Matrix)

```bash
cd /home/chuck/homelab

# Emergency: restore daily mode regardless of current state
docker compose -f compose.qwen36.yml down    # Stop whatever is on port 8000
docker compose -f compose.comfyui.yml --profile image down  # Stop ComfyUI if running
docker compose -f compose.metrics.yml up -d
docker compose -f compose.qwen36.yml up -d
docker compose -f compose.ollama.yml up -d

# Verify
curl -s http://localhost:8000/v1/models       # vLLM
curl -s http://localhost:11434                 # Ollama
```

---

## 8. What Thor Should NEVER Do

| Action | Reason |
|---|---|
| **Stop or restart Matrix containers directly** | Bypasses mode tracking; may leave system in inconsistent state |
| **Write to `/home/chuck/homelab/state/`** | State files are managed by the Matrix model manager/operator |
| **Modify `compose.*.yml` files on Matrix** | Compose files define the appliance configuration — only the operator changes them |
| **Edit `thor.litellm.config.yml` to change Matrix ports** | Ports are fixed by contract; changing them breaks the model manager |
| **Assume Matrix models are available for direct inference** | All inference should go through Thor's LiteLLM proxy, not direct to Matrix ports |
| **Deploy application services on Matrix** | Matrix is a compute-only appliance. No apps, DBs, or proxies belong here |
| **Change `matrix-coder` alias mapping** | The alias is stable by design; changing it breaks experiment mode and the model manager |

### What Thor CAN Do

| Action | Notes |
|---|---|
| Read Matrix endpoints for inference | Via LiteLLM aliases (`matrix-coder`, `matrix-gemma4-moe`, `embeddings`) |
| Scrape metrics from ports 9100 and 9400 | Prometheus scraping for dashboards |
| Health-check ports 8000 and 11434 | To detect availability changes |
| Log Matrix service outages | For alerting and incident tracking |

---

## 9. API Contracts

### vLLM (OpenAI-Compatible, port 8000)

```
POST http://matrix:8000/v1/chat/completions
Content-Type: application/json
Authorization: Bearer unused  (Matrix vLLM ignores API keys)

{
  "model": "qwen36-27b",
  "messages": [{"role": "user", "content": "..."}],
  "max_tokens": 4096,
  "temperature": 0.7
}
```

```
GET http://matrix:8000/v1/models
→ {"object": "list", "data": [{"id": "qwen36-27b", ...}]}
```

### Ollama (port 11434)

Thor LiteLLM handles the Ollama protocol translation. Thor should never call Ollama
directly — use the LiteLLM aliases.

---

## 10. Authentication & Security

| Endpoint | Auth Required? | Notes |
|---|---|---|
| vLLM :8000 | No (internal LAN only) | `api_key: "unused"` in LiteLLM config |
| Ollama :11434 | No (internal LAN only) | `api_key: "unused"` in LiteLLM config |
| node-exporter :9100 | No (internal LAN only) | No auth on node-exporter |
| dcgm-exporter :9400 | No (internal LAN only) | No auth on dcgm-exporter |
| ComfyUI :8188 | No (internal LAN only) | `SECURITY_LEVEL=weak` — never expose publicly |

**Network-level security:** All Matrix ports are bound to `0.0.0.0` but are only
reachable on the homelab LAN. No ports are exposed to the internet.

---

## 11. Cross-References

| Document | Content |
|---|---|
| `docs/matrix_runtime_modes.md` | Mode definitions and alias availability per mode |
| `docs/matrix_optimization_profiles.md` | vLLM arg rationale and VRAM budgets |
| `docs/matrix_images_mode.md` | ComfyUI/image mode operational details |
| `docs/matrix_monitoring_health.md` | Health endpoints and metrics |
| `docs/matrix_model_manager.md` | Model manager CLI (operator tool, not Thor-facing) |
| `thor.litellm.config.yml` | Thor's LiteLLM proxy config (deployed on Thor) |

---

## 12. Version History

| Version | Date | Change |
|---|---|---|
| 1.0 | 2026-07-03 | Initial contract document |
