# Thor Handoff — Matrix 2-GPU (Grafana / Prometheus)

> Created 2026-11-14 (add_card_plan.md Phase 2). Matrix now has **two GPUs**:
> GPU 0 = RTX PRO 5000 72 GB (daily LLM, vLLM `qwen38`), GPU 1 = RTX 4000 24 GB
> (media pipeline XOR Ollama copywriter, `device_ids: ['1']`).

## What changed on Matrix

- dcgm-exporter (`:9400`) now exports the full DCGM field set **per GPU index**
  (`gpu="0"` / `gpu="1"` labels): util, `FB_USED`/`FB_FREE` (VRAM), power, temp,
  clocks, XID errors.
- No exporter config change — `NVIDIA_VISIBLE_DEVICES=all` already covers both.

## What Thor needs to do

### 1. Prometheus — nothing to change

The existing `matrix:9400` scrape target (already UP) automatically picked up the
new per-GPU series. Verify in the Prometheus UI:

```
DCGM_FI_DEV_GPU_UTIL{instance=~"matrix.*"}
DCGM_FI_DEV_FB_USED{instance=~"matrix.*"}
```

Both should return two series (`gpu="0"`, `gpu="1"`).

### 2. Grafana — import the 2-GPU dashboard

`docs/thor_2gpu_grafana_dashboard.json` (this repo) — Grafana → Dashboards →
Import → upload the file → select the Prometheus datasource. Panels:

| Panel | Query (per GPU) |
|---|---|
| GPU Utilization % | `DCGM_FI_DEV_GPU_UTIL{instance=~"matrix.*"}` |
| VRAM Used (MiB) | `DCGM_FI_DEV_FB_USED{instance=~"matrix.*"}` |
| Power (W) | `DCGM_FI_DEV_POWER_USAGE{instance=~"matrix.*"}` |
| Temperature (°C) | `DCGM_FI_DEV_GPU_TEMP{instance=~"matrix.*"}` |
| XID Errors (stat) | `DCGM_FI_DEV_XID_ERRORS{instance=~"matrix.*"}` (0 = healthy) |

Legends: GPU 0 = "RTX PRO 5000 — daily LLM", GPU 1 = "RTX 4000 — media/copywriter".

### 3. Existing single-GPU panels — check for ambiguity

Any old panel querying `DCGM_FI_DEV_*` **without** a `gpu` label matcher now
returns two series (both cards). Either add `gpu="0"` to keep the old behavior
(daily LLM) or adopt the new dashboard.

## Not in this handoff

- **No LiteLLM change** — the `qwen38-27b` alias on `:8000` is unchanged (GPU 0).
- The **copywriter alias** (`matrix-copywriter`, gemma4:31b on `:11434`) is a
  Phase 3/4 item — separate handoff when it lands. Note for that time:
  gemma4:31b is a **reasoning model** — short `num_predict`/max-tokens budgets
  return empty content (the text lands in the `thinking` field); the alias needs
  a generous output budget.

## Verification (from Thor)

```bash
curl -s 'http://matrix:9400/metrics' | grep -E '^DCGM_FI_DEV_(GPU_UTIL|FB_USED)'
# expect 4 lines: gpu="0"/gpu="1" × util/FB_USED
```
