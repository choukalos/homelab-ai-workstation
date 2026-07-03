# Matrix TODO

## Experiment System (2026-07-03)

### New experiment profiles created
- [x] `experiment-gemma4-31b` — Gemma 4 31B Q4 via vLLM
- [x] `experiment-nemotron-3-nano-30b` — Nemotron 3 Nano 30B (Hybrid Mamba-2/MoE)
- [x] `experiment-qwen3-next-80b-nvfp4` — Qwen3-Next 80B NVFP4

### New CLI commands added
- [x] `model-manager experiment list` — list named experiments
- [x] `model-manager experiment start --profile <NAME>` — start named experiment
- [x] `model-manager experiment switch <NAME>` — switch between experiments
- [x] `model-manager experiment show <NAME>` — show profile details
- [x] `model-manager experiment archive` — view experiment history
- [x] `model-manager mode rollback` — now cleans up active experiment state

### Experiment compatibility research
- [ ] **Qwen3-Next-80B-A3B-Thinking-NVFP4** — NVFP4 is TensorRT-LLM-only. Research:
  - Can we get a vLLM-compatible quant (AWQ/GPTQ/FP8) of this model from HuggingFace?
  - Look for: `Qwen/Qwen3-Next-80B-A3B-Thinking-FP8` or community AWQ variants
  - If TensorRT-LLM is the only path, assess whether a 72GB GPU can run it with TP=1
- [ ] **Nemotron-3-Nano-30B-A3B-BF16** — Hybrid Mamba-2/Transformer not vLLM-compatible. Research:
  - Check latest vLLM (v0.9+) for Mamba-2 support status
  - Is there a pure-Transformer variant of Nemotron-3-Nano?
  - Can we run this via NeMo's inference stack instead of vLLM?
  - Look for community GGUF/AWQ quantized versions that Ollama or llama.cpp could handle

### Things to test when ready
- [ ] Start Gemma 4 31B: `model-manager experiment start --profile experiment-gemma4-31b`
- [ ] Check experiment archive: `model-manager experiment archive`
- [ ] Test rollback: `model-manager mode rollback`

## Manual Testing

### model-manager script fixes
- [ ] `model-manager experiment start` (no model) — should show usage and NOT generate a compose file
- [ ] `model-manager experiment start --mtp <actual-model-path>` — should generate a compose with `${HF_TOKEN}` (literal variable reference) and `--speculative-config`

### qwen-long optimization
- [ ] Switch to qwen-long mode with new settings and verify it starts and serves:
  - `model-manager mode switch qwen-long`
  - Check: vLLM comes up, `http://localhost:8000/v1/models` responds
  - Check: `docker logs qwen36` shows MTP enabled without errors
  - Test: 2 concurrent requests work (or note if VRAM is exceeded)
  - Compare throughput vs old single-seq baseline using `benchmark.sh`
  - Roll back: `model-manager mode rollback`

## Pre-Git Commit Cleanup

See notes below before committing.
