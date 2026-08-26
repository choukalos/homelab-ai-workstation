# Qwen3.8-27B Experiment

> **Status (2026-08-25):** `experiment-qwen38-27b-nvfp4` (unsloth/Qwen3.8-27B-NVFP4) was
> **promoted to `matrix-coder` on 2026-08-24** and is now the production daily driver
> (196K ctx, 75% VRAM). The 2026-08-25 speed fix (removed `--enforce-eager`, MTP 3 → 2)
> was verified at **123.95 tok/s** (benchmark `20260825_222844`).
> See `EXPERIMENTS_RESULTS.md` for the full round.

## Experiments Setup

Two experiment profiles created under the homelab standard structure:

| Profile | Model | VRAM | Context | Notes |
|---------|-------|------|---------|-------|
| `experiment-qwen38-27b-fp8` | Qwen/Qwen3.8-27B-FP8 | 40-50 GB | 128K | Official FP8, balanced |
| `experiment-qwen38-27b-nvfp4` | Inferact/Qwen3.8-27B-NVFP4 | 28-38 GB | 128K | NVIDIA 4-bit, most efficient |

### To Start an Experiment

```bash
# Start FP8 experiment
./scripts/model-manager experiment start --profile experiment-qwen38-27b-fp8

# Start NVFP4 experiment
./scripts/model-manager experiment start --profile experiment-qwen38-27b-nvfp4

# Switch between experiments
./scripts/model-manager experiment switch experiment-qwen38-27b-fp8
```

## Model Overview

| Property | Value |
|----------|-------|
| **Params** | 27B dense |
| **Architecture** | Hybrid GDN: 48/64 layers linear (Gated DeltaNet) + 16/64 full attention |
| **Vision** | Native image + video |
| **Context** | 262K native, extensible to 1M with YaRN |
| **MTP** | Multi-Token Prediction (2 speculative tokens via vLLM in production; 3 in early experiments) |
| **Tool Calling** | XML-style format, auto-parsed by `qwen3_coder` parser |
| **Thinking** | `reasoning_effort`: xhigh/medium/low, `preserve_thinking` |
| **License** | Apache 2.0 |
| **HF** | https://huggingface.co/Qwen/Qwen3.8-27B |

## vLLM Serving Settings (from model card)

### Thinking Mode (default)
- `temperature=1.0`, `top_p=0.95`, `top_k=20`, `min_p=0.0`
- `presence_penalty=0.0`, `repetition_penalty=1.0`
- `reasoning_effort=xhigh` (default), `preserve_thinking=true` (default)

### Instruct/Non-Thinking Mode
- `temperature=0.7`, `top_p=0.80`, `top_k=20`, `min_p=0.0`
- `presence_penalty=1.5`, `repetition_penalty=1.0`

## Key vLLM Flags

| Flag | Purpose |
|------|---------|
| `--reasoning-parser qwen3` | Parse thinking/reasoning tags |
| `--tool-call-parser qwen3_coder` | Parse XML-style tool calls |
| `--enable-auto-tool-choice` | Let model refuse tools when not needed |
| `--speculative-config '{"method":"mtp","num_speculative_tokens":2}'` | MTP decode speedup (tuned 3 → 2 on 2026-08-25) |
| `--kv-cache-dtype fp8` | Halves KV memory vs BF16 |
| `--enable-prefix-caching` | Reuse KV for shared prefixes |
| `--default-chat-template-kwargs` | Set enable_thinking, preserve_thinking |

## Test Scripts

```bash
# Tool calling test (requires vLLM running on port 8000)
python test_tool_calling.py --backend vllm --query "What's the weather in San Francisco?"

# Image analysis test
python test_image_analysis.py --backend vllm --image /path/to/photo.jpg
```

## VRAM Budget (72GB RTX PRO 5000)

### FP8
- Weights: ~28 GB
- KV cache (3 x 128K @ FP8): ~3.6 GB
- Activations + overhead: ~6-7 GB
- **Total: ~40-50 GB** (comfortable headroom)

### NVFP4
- Weights: ~14 GB
- KV cache (3 x 128K @ FP8): ~3.6 GB
- Activations + overhead: ~5-6 GB
- **Total: ~28-38 GB** (plenty of headroom)

## Notes

- Requires vLLM 0.17.0+ for Qwen3.8 hybrid GDN + NVFP4 support
- NVFP4 is Blackwell-only (RTX PRO 5000) — won't work on Ada/Hopper
- `MXFP4` does NOT work on NVIDIA — use `NVFP4` (Inferact modelopt format)
- Vision requires `transformers >= 5.8.0`
- For context beyond 262K: enable YaRN via `--hf-overrides`
- Rollback: `./scripts/model-manager mode rollback`