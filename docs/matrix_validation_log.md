# Matrix Validation Log

> Run: 2026-07-03T02:02 UTC

## Read-Only Checks

| Check | Result | Notes |
|---|---|---|
| `hostname -I` | ✅ 192.168.4.55 | LAN address reachable |
| `nvidia-smi` | ✅ RTX PRO 5000 72GB | Driver 595.71.05, CUDA 13.2 |
| `docker ps` | ✅ 4 running containers | qwen36, ollama, node-exporter, dcgm-exporter |
| `docker compose ls` | ✅ `homelab` running(4) | compose.qwen36.yml, compose.ollama.yml, compose.metrics.yml |
| `docker network ls` | ✅ `homelab_default` bridge | Network exists for inter-container comms |
| `docker volume ls` | ✅ None | All state is bind-mounted (good for auditability) |
| `df -h` | ✅ 14% used on root | 1.7 TB NVMe, plenty of space |
| `free -h` | ✅ 54 GiB available | 62 GiB total, plenty of RAM |

## Service Health

| Service | Endpoint | Status | Notes |
|---|---|---|---|
| vLLM Qwen3.6 | http://localhost:8000/v1/models | ✅ Running | Serves `qwen36-27b` |
| Ollama | http://localhost:11434/v1/models | ✅ Running | 3 models loaded: nomic-embed-text, qwen3.6:27b, gemma4:26b |
| node-exporter | http://localhost:9100/metrics | ✅ Running | Prometheus node metrics (scraped by Thor) |
| dcgm-exporter | http://localhost:9400/metrics | ✅ Running | GPU metrics (scraped by Thor) |

**Note:** Grafana and Prometheus run on Thor, not Matrix. Matrix exporters are scraped remotely.

## Discrepancies

| # | Finding | Severity | Status |
|---|---|---|---|
| 1 | ~~`switch.sh` references non-existent compose files~~ | ~~**High**~~ | **RESOLVED** — switch.sh deleted in Phase 1 |
| 2 | `.env` `QWEN_GPU_MEM=0.56` but compose hardcodes `0.66` | Low — compose wins | Documented |
| 3 | ~~Ollama compose `KEEP_ALIVE=-1m` vs container `5m`~~ | ~~Medium~~ | **RESOLVED** — aligned to `5m` |
| 4 | Two stopped vLLM containers consuming disk | Low | Documented |
| 5 | ~~Grafana/Prometheus not running on Matrix~~ | ~~Medium~~ | **RESOLVED** — Grafana/Prometheus on Thor |
| 6 | ~~switch.sh mode names don't match plan~~ | ~~Medium~~ | **RESOLVED** — switch.sh deleted; model manager uses plan names |

## Next Validation Run

Re-run after any production changes in Phase 15.
