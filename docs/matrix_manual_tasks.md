# Matrix Manual Tasks

> Tasks that require Chuck's approval before execution. Generated during Phase 0.

**Resolved decisions (as of 2026-07-03):**
- **switch.sh**: Deleted in Phase 1, replaced by model manager design
- **Grafana/Prometheus**: Run on Thor (not Matrix). Matrix exporters scraped by Thor.
- **OLLAMA_KEEP_ALIVE**: Set to `5m` across all compose files and profiles.

---

## RESOLVED: Fix switch.sh compose file references

**Reason:** `switch.sh` references `compose.vllm.yml` and `compose.gemma-vllm.yml` which don't exist. The actual compose files are `compose.qwen36.yml`, `compose.ollama.yml`, and `compose.comfyui.yml`. The script is completely non-functional.

**Status:** **DELETED** — `switch.sh` was removed in Phase 1. Replaced by the model manager design (Phase 4). No further action needed.

---

## MANUAL TASK FOR CHUCK: Clean up stale containers

**Reason:** `vllm-gemma` (exited 13 days ago), `vllm-qwen` (exited 12 days ago), `comfyui_backend` (exited 2 weeks ago), and `ollama-model-puller` (never started) are stopped containers consuming disk space.

**Command:**
```bash
docker rm vllm-gemma vllm-qwen comfyui_backend ollama-model-puller
```

**Expected impact:** Frees minor disk space. No impact on running services.

**Rollback:** None — containers are already stopped and have no live state.

**Validation:** `docker ps -a` shows only running containers + any new ones.

---

## RESOLVED: Decide on Grafana/Prometheus

**Status:** **DECIDED: Grafana and Prometheus run on Thor, not Matrix.**

Matrix exporters (node-exporter :9100, dcgm-exporter :9400) expose Prometheus-format metrics for Thor's Prometheus to scrape. Grafana on Thor provides the dashboards.

Matrix remains a compute-only appliance. No monitoring stack installed locally.

**Validation:** Thor's Prometheus should scrape `http://matrix:9100/metrics` and `http://matrix:9400/metrics`.

---

## RESOLVED: Reconcile Ollama KEEP_ALIVE setting

**Status:** **DECIDED: `OLLAMA_KEEP_ALIVE=5m`.** Ollama hosts secondary models that should release VRAM when idle.

All Ollama compose files and profiles updated to `5m`. No more drift between compose and runtime.

---

## RESOLVED: Decide on `switch.sh` mode names vs. plan modes

**Status:** **RESOLVED** — `switch.sh` was deleted in Phase 1. The model manager (Phase 4) uses the plan mode names: `daily`, `qwen-coder`, `qwen-long`, `llms`, `experiment`, `images`.

---

## MANUAL TASK FOR CHUCK: HF_TOKEN is empty in .env

**Reason:** `.env` has `HF_TOKEN=` with no value. The current Qwen3.6 model is already cached, so vLLM works. But if any future model needs auth to download, it will fail silently.

**Command:**
```bash
# Add actual HF token to .env if needed for future model downloads
```

**Expected impact:** Enables authenticated model downloads from HuggingFace.

**Rollback:** Remove the token from .env.

**Validation:** N/A — latent issue, only matters when downloading new models.
