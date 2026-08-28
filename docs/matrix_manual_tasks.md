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

## RESOLVED: Clean up stale containers

**Status:** **RESOLVED 2026-08-28** — `vllm-gemma`, `vllm-qwen`, and `ollama-model-puller` no longer exist (removed earlier). `comfyui_backend` is a **live service** (ComfyUI + media-pipeline run concurrently with vLLM) and must not be removed.

**Remaining (optional):** 7 stopped experiment containers (`qwen36`, `qwen38-fp8`, `qwen38-nvfp4`, `qwen36-long`, `qwen3-next-80b`, `qwen36-perf`, `qwen36-mtp`) hold minor disk. Safe to `docker rm` — configs live in `compose/experiments/`. Left in place in case experiments are rerun (TODO: rerun 3/4/5, run 6).

**Validation:** `docker ps -a` shows only running containers + stopped experiment leftovers.

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

**Status:** **RESOLVED** — `switch.sh` was deleted in Phase 1. The model manager (Phase 4) uses the plan mode names: `daily`, `qwen-coder`, `qwen-long`, `llms`, `experiment`. (The `images` mode was retired 2026-08-27 — image generation now runs concurrently with all modes.)

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
