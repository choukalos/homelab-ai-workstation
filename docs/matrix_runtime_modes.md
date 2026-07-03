# Matrix Runtime Modes

> Documented: 2026-07-03
> Current active mode: `daily`

## How Modes Work

Matrix has one 72 GB GPU. Each mode defines which models run, how much VRAM each gets,
and which LiteLLM aliases are valid. Mode switches require stopping/restarting containers.

The `matrix-coder` LiteLLM alias always points at port 8000 — whatever model is running
there is "the main model" to clients.

---

## `daily` — Normal Chat/Coding Use (CURRENT)

**What's running:**

| Service | Container | Model | Port | Backend | VRAM |
|---|---|---|---|---|---|
| Primary model | `qwen36` | Qwen3.6-27b-int4-AutoRound | 8000 | vLLM | ~48.7 GB |
| Light model | `ollama` | gemma4:26b (Q4_K_M) | 11434 | Ollama | ~17 GB (on demand) |
| Embeddings | `ollama` | nomic-embed-text (F16) | 11434 | Ollama | ~274 MB |
| Metrics | `node-exporter`, `dcgm-exporter` | — | 9100, 9400 | — | N/A |

**vLLM args (from compose.qwen36.yml):**
- `--gpu-memory-utilization 0.66` → ~48.7 GB
- `--max-model-len 200000`
- `--max-num-seqs 3`
- `--max-num-batched-tokens 8192`
- `--kv-cache-dtype fp8`
- `--enable-prefix-caching`
- `--enable-chunked-prefill`
- `--enable-auto-tool-choice`
- `--tool-call-parser qwen3_xml`

**Ollama config:**
- `OLLAMA_MAX_LOADED_MODELS=2`
- `OLLAMA_KEEP_ALIVE` = 5m (secondary models release VRAM when idle)
- Models loaded on demand; gemma4:26b loads when first requested

**LiteLLM aliases valid:** `matrix-coder`, `matrix-gemma4-moe`, `embeddings`

**Startup (rebuild after reboot):**
```bash
cd /home/chuck/homelab
docker compose -f compose.metrics.yml up -d
docker compose -f compose.qwen36.yml up -d
docker compose -f compose.ollama.yml up -d
```

**Health checks:**
```bash
curl -s http://localhost:8000/v1/models        # vLLM Qwen
curl -s http://localhost:11434                  # Ollama
curl -s http://localhost:9100/metrics | head -1 # node-exporter
curl -s http://localhost:9400/metrics | head -1 # dcgm-exporter
```

**Expected downtime for switch to another mode:** ~5-10 min (vLLM stop + VRAM release + new container)

---

## `qwen-coder` — Best Coding Performance

**Goal:** Give Qwen as much VRAM as possible for better context window and throughput.

**What's running:**

| Service | Container | Model | Port | Backend | VRAM |
|---|---|---|---|---|---|
| Primary model | `qwen36` | Qwen3.6-27b-int4-AutoRound | 8000 | vLLM | ~55-60 GB (gpu-mem 0.75-0.80) |
| Embeddings | `ollama` | nomic-embed-text | 11434 | Ollama | ~274 MB |

**Changes from daily:**
- Stop Ollama (or keep running for embeddings only, unload gemma4)
- Restart vLLM with `--gpu-memory-utilization 0.75` or higher

**LiteLLM aliases valid:** `matrix-coder`, `embeddings`
**Aliases offline:** `matrix-gemma4-moe` (gemma4 unloaded from Ollama)

**Startup:**
```bash
# Stop Gemma in Ollama
docker exec ollama ollama unload gemma4:26b
# Restart vLLM with higher VRAM
docker compose -f compose.qwen36.yml down
QWEN_GPU_MEM=0.75 docker compose -f compose.qwen36.yml up -d
```

**Health check:** `curl -s http://localhost:8000/v1/models`

**Rollback to daily:** Stop vLLM, restart at 0.66; restart Ollama with gemma4 loaded.

---

## `qwen-long` — Long-Context Work

**Goal:** Maximize context window for large codebases or documents.

**What's running:**

| Service | Container | Model | Port | Backend | VRAM |
|---|---|---|---|---|---|
| Primary model | `qwen36` | Qwen3.6-27b-int4-AutoRound | 8000 | vLLM | ~50-55 GB |
| Embeddings | `ollama` | nomic-embed-text | 11434 | Ollama | ~274 MB |

**Changes from daily:**
- Same model, different vLLM args: `--max-model-len 300000` or higher
- May need `--max-num-seqs 1` and `--max-num-batched-tokens 4096` for stability
- `--gpu-memory-utilization` may need to drop to ~0.60 to fit larger KV cache

**LiteLLM aliases valid:** `matrix-coder`, `embeddings`
**Aliases offline:** `matrix-gemma4-moe`

**Startup:**
```bash
# Need to adjust compose args for long context
# This requires a new compose file or manual override
```

**⚠️ Not yet implemented — requires a new compose variant.** See Phase 3 for profile drafts.

**Rollback to daily:** Restart vLLM with standard `compose.qwen36.yml` args.

---

## `llms` — Multi-Model Tool Experiments

**Goal:** Run Qwen + Gemma simultaneously for multi-model experiments.

**What's running:**

| Service | Container | Model | Port | Backend | VRAM |
|---|---|---|---|---|---|
| Primary model | `qwen36` | Qwen3.6-27b-int4-AutoRound | 8000 | vLLM | ~48 GB |
| Light model | `ollama` | gemma4:26b | 11434 | Ollama | ~17 GB |
| Embeddings | `ollama` | nomic-embed-text | 11434 | Ollama | ~274 MB |

**Note:** This is essentially the same as `daily` mode. The distinction is semantic:
in `llms` mode you're actively using both models together.

**LiteLLM aliases valid:** `matrix-coder`, `matrix-gemma4-moe`, `embeddings`

**This is the current production state.**

---

## `experiment` — Candidate Model Swap-Out

**Goal:** Temporarily replace Qwen on port 8000 with a different model for testing.

**What's running:**

| Service | Container | Model | Port | Backend | VRAM |
|---|---|---|---|---|---|
| Experiment | `qwen36` (same container, different model) | *[candidate]* | 8000 | vLLM | varies |
| Light/embed | `ollama` | depends on candidate size | 11434 | Ollama | varies |

**Key principle:** The LiteLLM alias `matrix-coder` points at port 8000. Whatever model
serves port 8000 IS `matrix-coder` to clients. No config changes needed.

**Startup:**
```bash
# Stop current vLLM
docker compose -f compose.qwen36.yml down
# Start vLLM with different model (manual command or new compose)
docker run --rm -d --name qwen36 \
  --gpus all --shm-size 16g -p 8000:8000 \
  -v /home/chuck/data/models:/data/models \
  -e HF_HOME=/data/models \
  vllm/vllm-openai:latest \
  --model <candidate-model> --host 0.0.0.0 --port 8000 ...
```

**⚠️ Operator responsibility:** Track which model is actually live. When done,
restore the production Qwen model immediately.

**Rollback to daily:**
```bash
docker compose -f compose.qwen36.yml down
docker compose -f compose.qwen36.yml up -d
```

---

## `images` — ComfyUI/FLUX Image Generation

**Goal:** Run image generation while keeping a light LLM available for chat/tools.

**What's running:**

| Service | Container | Model | Port | Backend | VRAM |
|---|---|---|---|---|---|
| ComfyUI | `comfyui_backend` | FLUX or other | 8188 | ComfyUI | ~30-40 GB |
| Light model | `ollama` | gemma4:26b | 11434 | Ollama | ~17 GB |
| Embeddings | `ollama` | nomic-embed-text | 11434 | Ollama | ~274 MB |

**vLLM (Qwen) is stopped** — ComfyUI + Qwen won't fit on 72 GB. Ollama stays up
with Gemma4 for a working LLM fallback during image work.

**LiteLLM aliases valid:** `matrix-gemma4-moe`, `embeddings`
**Aliases offline:** `matrix-coder` (vLLM stopped)

**Startup:**
```bash
docker compose -f compose.qwen36.yml down
docker compose -f compose.comfyui.yml --profile image up -d
# Ollama stays running — no need to touch it
```

**Health check:** `curl -s http://localhost:8188` and `curl -s http://localhost:11434`

**Rollback to daily:**
```bash
docker compose -f compose.comfyui.yml --profile image down
docker compose -f compose.qwen36.yml up -d
```

**⚠️ Expected downtime:** 5-10 min to switch in or out of image mode.
**⚠️ Do not expose port 8188 publicly.**
**⚠️ `matrix-coder` is offline during image mode — clients get Gemma instead.

---

## Mode Switch Matrix

| Target mode | Stop containers | Start containers | Aliases affected |
|---|---|---|---|
| `daily` | (none — this is current) | (none) | all valid |
| `qwen-coder` | Ollama gemma4 (unload) | vLLM @ higher gpu-mem | gemma4-moe offline |
| `qwen-long` | vLLM, Ollama gemma4 | vLLM @ long-ctx args | gemma4-moe offline |
| `llms` | (same as daily) | (same as daily) | all valid |
| `experiment` | vLLM | vLLM with new model | coder points to experiment |
| `images` | vLLM | ComfyUI + Ollama (gemma4) | coder offline; gemma4-moe + embeddings valid |

---

## VRAM Budget (72 GB GPU)

| Mode | vLLM | Ollama | Total | Headroom |
|---|---|---|---|---|
| `daily` | ~48.7 GB | ~17 GB (gemma4) + 274 MB | ~66 GB | ~6 GB |
| `qwen-coder` | ~55-60 GB | ~274 MB (embed only) | ~55-60 GB | ~12-17 GB |
| `qwen-long` | ~50-55 GB | ~274 MB | ~50-55 GB | ~17-22 GB |
| `experiment` | varies | varies | varies | varies |
| `images` | *(stopped)* | ~30-40 GB (ComfyUI) + ~17 GB (gemma4) + 274 MB | ~48-58 GB | ~14-24 GB |
