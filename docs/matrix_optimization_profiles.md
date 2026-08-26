# Qwen Optimization Profiles

> Created: 2026-07-03, updated 2026-08-25
> Purpose: Separate profiles for different workloads instead of one compromise config

## Overview

Each profile is tuned for a specific workload. Since 2026-08-24, **matrix-coder** runs
`unsloth/Qwen3.8-27B-NVFP4` (196K ctx) while **qwen-long** still runs
`Lorbus/Qwen3.6-27b-int4-AutoRound` (240K ctx); the profiles differ in model, vLLM launch
args, VRAM budget, and concurrency settings.

| Profile | Context | VRAM | Concurrency | Use Case |
|---|---|---|---|---|
| **matrix-coder** | 196K | ~55 GB (0.75 util) | 3 seqs | Daily coding, skills, harness |
| **qwen-long** | 240K | ~50-55 GB (0.50 util) | 2 seqs | Large codebases, long documents |
| **experiment** | varies | varies | varies | Candidate model swap-out |

## Arg Rationale

### `--max-model-len`
- **matrix-coder** (196K): Qwen3.8-27B's native context. Covers the vast majority of
  coding tasks — an entire medium-sized codebase in context. Sweet spot between
  capability and VRAM.
- **qwen-long** (240K): For situations requiring extreme context (very large repos,
  long documentation). Costs extra VRAM for KV cache; runs at lower gpu-memory-utilization
  (0.50) and 2 seqs for stability.
- **experiment** (128K default): Conservative default for unknown models. Adjust per candidate.

### `--gpu-memory-utilization`
- **matrix-coder** (0.75): ~54 GB reserved (~55-58 GB measured with other services).
  Total GPU: ~72 GB. Leaves ~14-18 GB for Ollama (gemma4 loads on demand, 5m keep-alive).
- **qwen-long** (0.50): Lower utilization because the KV cache for 240K context is
  larger. The actual VRAM used is still ~50-55 GB due to the larger cache.
  **Gemma4 must be unloaded before launching** to avoid OOM.
- **experiment** (0.66): Template default — adjust per candidate.

### `--max-num-seqs`
- **matrix-coder** (3): Supports concurrent requests (pi, harness, skills running simultaneously).
- **qwen-long** (2): Stability at extreme context lengths. Multi-seq at 240K context is
  fragile and wastes VRAM on partial KV caches.
- **experiment** (3): Same as matrix-coder unless the candidate behaves differently.

### `--max-num-batched-tokens`
- **matrix-coder** (8192): Good throughput for typical prompt sizes.
- **qwen-long** (8192, with `--chunked-prefill-size 16384`): Leaves more VRAM for KV cache at 240K context.
- **experiment** (8192): Same as matrix-coder.

### Shared args (all profiles)
- `--kv-cache-dtype fp8`: Cuts KV cache VRAM by ~50% with minimal quality loss.
- `--enable-prefix-caching`: Reuses KV cache for repeated prefixes (great for pi chat sessions).
- `--enable-chunked-prefill`: Prevents long prompts from starving the decoder.
- `--enable-auto-tool-choice`: Required for pi tool-calling.
- `--tool-call-parser qwen3_coder`: Correct parser for Qwen3.8 tool calls (matrix-coder). qwen-long still uses `qwen3_xml` for the Qwen3.6 model.
- `--trust-remote-code`: Needed for the quantized models (AutoRound INT4, NVFP4 modelopt).

## Switching Between Profiles

### From daily (matrix-coder) to qwen-long
```bash
# 1. Pre-check
preflight.sh qwen-long

# 2. Unload gemma4 from Ollama (frees ~17 GB; embeddings stay running)
docker exec ollama ollama unload gemma4:26b

# 3. Stop current vLLM
docker compose -f compose/qwen-coder.yml down

# 4. Start qwen-long
docker compose -f compose/qwen-long.yml up -d

# 5. Verify
curl -sf http://localhost:8000/v1/models && echo OK
```

### From qwen-long back to daily
```bash
# 1. Stop qwen-long
docker compose -f compose/qwen-long.yml down

# 2. Restart daily
docker compose -f compose/qwen-coder.yml up -d

# 3. Restart Ollama with gemma4
docker compose -f compose/gemma4-moe.yml up -d
```

### From daily to experiment
```bash
# 1. Copy and edit the template
cp compose/experiment.yml compose/experiments/MYMODEL.yml
# Edit: change --model to candidate path, adjust args as needed

# 2. Stop current vLLM
docker compose -f compose/qwen-coder.yml down

# 3. Start experiment
docker compose -f compose/experiments/MYMODEL.yml up -d

# 4. When done, restore daily
docker compose -f compose/experiments/MYMODEL.yml down
docker compose -f compose/qwen-coder.yml up -d
rm compose/experiments/MYMODEL.yml  # cleanup
```

## VRAM Budget Summary

| Profile | vLLM | Ollama (gemma4) | Ollama (embeddings) | Total | Fits? |
|---|---|---|---|---|---|
| matrix-coder | ~55 GB (0.75 util) | ~17 GB (on demand) | ~0.3 GB | ~72 GB (peak) | ✅ tight — gemma4 loads on demand, 5m keep-alive |
| qwen-long | ~50-55 GB (0.50 util) | 0 (unloaded) | ~0.3 GB | ~50-55 GB | ✅ (72 GB GPU) |
| experiment | varies | varies | varies | varies | Check preflight |

## Rules

1. **Never run vLLM profiles simultaneously** — they share port 8000 and the same GPU.
2. **Always preflight** before switching: `preflight.sh <profile>`
3. **Benchmark before and after** any arg changes: `benchmark.sh --category all --baseline`
4. **One variable at a time** when tuning — change one arg, benchmark, evaluate.
5. **Quality > numbers** — if tool calling breaks, roll back regardless of throughput gains.
6. **matrix-coder is the default** — always return to it when done with other profiles.
