# Matrix Runtime Modes

> Documented: 2026-07-03, updated 2026-08-25 (Qwen3.8-27B NVFP4 promotion + speed fix; primary container renamed `qwen36` → `qwen38`)

## How Modes Work

Matrix has one 72 GB GPU. Each mode defines which models run, how much VRAM each gets,
and which LiteLLM aliases are valid. Mode switches require stopping/restarting containers.

The `matrix-coder` LiteLLM alias always points at port 8000 — whatever model is running
there is "the main model" to clients.

---

## `daily` — Normal Chat/Coding Use

**What's running:**

| Service | Container | Model | Port | Backend | VRAM |
|---|---|---|---|---|---|
| Primary model | `qwen38` | unsloth/Qwen3.8-27B-NVFP4 | 8000 | vLLM | ~54 GB (0.75 util) |
| Light model | `ollama` | gemma4:26b (Q4_K_M) | 11434 | Ollama | ~17 GB (on demand) |
| Embeddings | `ollama` | nomic-embed-text (F16) | 11434 | Ollama | ~274 MB |
| Metrics | `node-exporter`, `dcgm-exporter` | — | 9100, 9400 | — | N/A |

**vLLM args (from compose/qwen-coder.yml):**
- `--gpu-memory-utilization 0.75` → ~54 GB reserved (~58 GB measured with other services running)
- `--max-model-len 196608`
- `--max-num-seqs 3`
- `--max-num-batched-tokens 8192`
- `--kv-cache-dtype fp8`
- `--enable-prefix-caching`
- `--enable-chunked-prefill`
- `--enable-auto-tool-choice`
- `--tool-call-parser qwen3_coder`
- `--reasoning-parser qwen3`
- `--speculative-config '{"method":"mtp","num_speculative_tokens":2}'`
- `--default-chat-template-kwargs '{"preserve_thinking": true, "reasoning_effort": "high"}'`
- `--override-generation-config '{"temperature": 0.7, "top_p": 0.95}'`

**Ollama config:**
- `OLLAMA_MAX_LOADED_MODELS=2`
- `OLLAMA_KEEP_ALIVE` = 5m (secondary models release VRAM when idle)
- Models loaded on demand; gemma4:26b loads when first requested

**LiteLLM aliases valid:** `matrix-coder`, `matrix-gemma4-moe`, `embeddings`

**Startup (rebuild after reboot):**
```bash
cd /home/chuck/homelab
docker compose -f compose/metrics.yml up -d
docker compose -f compose/qwen-coder.yml up -d
docker compose -f compose/gemma4-moe.yml up -d
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
| Primary model | `qwen38` | unsloth/Qwen3.8-27B-NVFP4 | 8000 | vLLM | ~54 GB (gpu-mem 0.75) |
| Embeddings | `ollama` | nomic-embed-text | 11434 | Ollama | ~274 MB |

**Changes from daily:**
- Same model and vLLM args as daily (`compose/qwen-coder.yml`)
- Ollama runs embeddings only (gemma4 unloaded)

**LiteLLM aliases valid:** `matrix-coder`, `embeddings`
**Aliases offline:** `matrix-gemma4-moe` (gemma4 unloaded from Ollama)

**Startup:**
```bash
# Stop Gemma in Ollama
docker exec ollama ollama unload gemma4:26b
# vLLM: standard production config
docker compose -f compose/qwen-coder.yml down
docker compose -f compose/qwen-coder.yml up -d
```

**Health check:** `curl -s http://localhost:8000/v1/models`

**Rollback to daily:** Restart Ollama with gemma4 loaded (vLLM unchanged).

---

## `qwen-long` — Long-Context Work

**Goal:** Maximize context window for large codebases or documents.

**What's running:**

| Service | Container | Model | Port | Backend | VRAM |
|---|---|---|---|---|---|
| Primary model | `qwen38` | Lorbus/Qwen3.6-27b-int4-AutoRound | 8000 | vLLM | ~50-55 GB |
| Embeddings | `ollama` | nomic-embed-text | 11434 | Ollama | ~274 MB |

**Changes from daily:**
- Different model: `Lorbus/Qwen3.6-27b-int4-AutoRound` (240K ctx) vs daily's Qwen3.8-27B NVFP4
- `--max-model-len 240000`, `--max-num-seqs 2`, `--gpu-memory-utilization 0.50`, `--chunked-prefill-size 16384`
- Serves as `qwen38-27b` — the alias follows port 8000 (see design principle above), so `matrix-coder` routes to the long-context model while this mode is active

**LiteLLM aliases valid:** `matrix-coder`, `embeddings`
**Aliases offline:** `matrix-gemma4-moe`

**Startup:**
```bash
docker compose -f compose/qwen-coder.yml down
docker compose -f compose/qwen-long.yml up -d
```

**⚠️ Compose + profile exist** (`compose/qwen-long.yml`, `models/profiles/qwen-long.yaml`) **but the mode switch has not been verified end-to-end yet** (see TODO).

**Rollback to daily:** Restart vLLM with standard `compose/qwen-coder.yml` args.

---

## `llms` — Multi-Model Tool Experiments

**Goal:** Run Qwen + Gemma simultaneously for multi-model experiments.

**What's running:**

| Service | Container | Model | Port | Backend | VRAM |
|---|---|---|---|---|---|
| Primary model | `qwen38` | unsloth/Qwen3.8-27B-NVFP4 | 8000 | vLLM | ~54 GB |
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
| Experiment | `qwen38` (same container, different model) | *[candidate]* | 8000 | vLLM | varies |
| Light/embed | `ollama` | depends on candidate size | 11434 | Ollama | varies |

**Key principle:** The LiteLLM alias `matrix-coder` points at port 8000. Whatever model
serves port 8000 IS `matrix-coder` to clients. No config changes needed.

**Startup:**
```bash
# Stop current vLLM
docker compose -f compose/qwen-coder.yml down
# Start vLLM with different model (manual command or new compose)
docker run --rm -d --name qwen38 \
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
docker compose -f compose/qwen-coder.yml down
docker compose -f compose/qwen-coder.yml up -d
```

---

## Image generation (ComfyUI) — not a mode

> Updated 2026-08-26: image generation no longer requires stopping vLLM.
> ComfyUI (Qwen-Image) runs **concurrently with any mode** at a ~12 GB VRAM
> budget (`--reserve-vram 60`). `matrix-coder` stays online.

**Start / stop:**
```bash
docker compose -f compose/comfyui.yml --profile image up -d    # start
curl -s http://localhost:8188/system_stats | head -c 100       # verify
docker compose -f compose/comfyui.yml --profile image down     # stop
```

**VRAM:** ~12 GB cap; 9.3–14.4 GB measured peaks; total-GPU peak ≤ ~71.2 GB.
Idle cost ~0.7 GB — safe to leave running.

**Details:** [ComfyUI Media API](matrix_comfyui_media_api.md) (tooling contract) ·
[ComfyUI Ops](matrix_images_mode.md) (operations)

---

## Mode Switch Matrix

| Target mode | Stop containers | Start containers | Aliases affected |
|---|---|---|---|
| `daily` | (none — this is current) | (none) | all valid |
| `qwen-coder` | Ollama gemma4 (unload) | vLLM @ higher gpu-mem | gemma4-moe offline |
| `qwen-long` | vLLM, Ollama gemma4 | vLLM @ long-ctx args | gemma4-moe offline |
| `llms` | (same as daily) | (same as daily) | all valid |
| `experiment` | vLLM | vLLM with new model | coder points to experiment |

(Image generation is not a mode — see the section above.)

---

## VRAM Budget (72 GB GPU)

| Mode | vLLM | Ollama | Total | Headroom |
|---|---|---|---|---|
| `daily` | ~54 GB (0.75 util) | ~17 GB (gemma4) + 274 MB | ~71 GB (peak) | tight — gemma4 loads on demand, 5m keep-alive |
| `qwen-coder` | ~54 GB (0.75 util) | ~274 MB (embed only) | ~54 GB | ~18 GB |
| `qwen-long` | ~50-55 GB | ~274 MB | ~50-55 GB | ~17-22 GB |
| `experiment` | varies | varies | varies | varies |
