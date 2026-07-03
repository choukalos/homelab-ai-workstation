# Compose Layout — Draft

> Status: **DRAFT ONLY** — do not use until explicitly promoted in Phase 15.
> Original compose files in `../` remain the active production config.

## Directory Layout

```
compose/
  draft/
    qwen-coder.yml      # vLLM Qwen3.6-27B (primary — daily, qwen-coder, experiment)
    qwen-long.yml       # vLLM Qwen3.6-27B long-context (300K, 1 seq)
    experiment.yml      # vLLM template for candidate models (copy & edit)
    gemma4-moe.yml      # Ollama (gemma4:26b + nomic-embed-text)
    embeddings.yml      # Ollama embeddings-only reference (shared instance with gemma4-moe)
    comfyui.yml         # ComfyUI / FLUX (images mode)
    metrics.yml         # node-exporter + dcgm-exporter
```

## Mapping from Current Files

| Current file | Draft file | Changes |
|---|---|---|
| `compose.qwen36.yml` | `qwen-coder.yml` | Renamed for clarity; removed stale port comment |
| `compose.qwen-long.yml` | `qwen-long.yml` | Moved to draft dir; no functional changes |
| `compose.experiment.yml` | `experiment.yml` | Moved to draft dir; no functional changes |
| `compose.experiment-mtp.yml` | *(not in draft)* | Feature experiments stay in `scripts/` |
| `compose.ollama.yml` | `gemma4-moe.yml` | Renamed to match profile name; KEEP_ALIVE aligned to 5m |
| `compose.ollama.yml` | `embeddings.yml` | Reference-only (same Ollama instance) |
| `compose.comfyui.yml` | `comfyui.yml` | **Unchanged — matches legit production `compose.comfyui.yml`** |
| `compose.metrics.yml` | `metrics.yml` | Moved to draft dir; no functional changes |

## Why This Layout?

1. **Naming matches profiles** — each compose file name matches its `models/profiles/*.yaml` counterpart
2. **One file per profile** — `gemma4-moe.yml` clearly serves the Gemma4 MoE profile; `embeddings.yml` exists as a reference for the embeddings profile (they share the Ollama container)
3. **Experiment as a template** — `experiment.yml` has `MODEL_PATH_HERE` placeholder, making it obvious it needs editing
4. **No ambiguity** — `qwen-coder.yml` is clearly the primary; `qwen-long.yml` is the variant

## Usage in the Draft Layout

### daily mode
```bash
docker compose -f compose/draft/metrics.yml up -d
docker compose -f compose/draft/qwen-coder.yml up -d
docker compose -f compose/draft/gemma4-moe.yml up -d
```

### qwen-coder mode
```bash
docker compose -f compose/draft/metrics.yml up -d
docker compose -f compose/draft/qwen-coder.yml up -d
# Unload gemma4 from Ollama, keep it for embeddings only
docker exec ollama ollama unload gemma4:26b
```

### qwen-long mode
```bash
docker compose -f compose/draft/metrics.yml up -d
docker compose -f compose/draft/qwen-long.yml up -d
# Unload gemma4 from Ollama
docker exec ollama ollama unload gemma4:26b
```

### images mode
```bash
docker compose -f compose/draft/metrics.yml up -d
docker compose -f compose/draft/comfyui.yml --profile image up -d
docker compose -f compose/draft/gemma4-moe.yml up -d
```

### experiment mode
```bash
cp compose/draft/experiment.yml compose/experiment-mytest.yml
# Edit the copy: change --model, adjust args
docker compose -f compose/draft/metrics.yml up -d
docker compose -f compose/experiment-mytest.yml up -d
```

## Migration Plan (Phase 15)

When the operator decides to adopt this layout:

1. Verify all draft files parse correctly: `docker compose -f compose/draft/*.yml config`
2. Stop all running containers
3. Move current files to `compose/legacy/` (archive, don't delete)
4. Move `compose/draft/` → `compose/`
5. Update `models/profiles/*.yaml` to reference new paths
6. Update `scripts/preflight.sh` to check `compose/` instead of root
7. Start daily mode from new layout
8. Verify health: `scripts/matrix_health.sh`
9. Keep `compose/legacy/` for 1 week as rollback, then remove

## Rules (from Phase 13)

- ✅ Draft only — do not replace active compose files
- ✅ Do not run `docker compose up` against these files
- ✅ Do not delete old compose files
- ✅ Path changes require updating `preflight.sh` and `models/profiles/*.yaml`
