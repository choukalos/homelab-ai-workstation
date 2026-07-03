# Qwen Optimization Profiles

> Created: 2026-07-03
> Purpose: Separate profiles for different workloads instead of one compromise config

## Overview

Each profile is tuned for a specific workload. They share the same base model
(`Lorbus/Qwen3.6-27b-int4-AutoRound`) but differ in vLLM launch args, VRAM budget,
and concurrency settings.

| Profile | Context | VRAM | Concurrency | Use Case |
|---|---|---|---|---|
| **matrix-coder** | 200K | ~49 GB | 3 seqs | Daily coding, skills, harness |
| **qwen-long** | 300K | ~55 GB | 1 seq | Large codebases, long documents |
| **experiment** | varies | varies | varies | Candidate model swap-out |

## Arg Rationale

### `--max-model-len`
- **matrix-coder** (200K): Covers the vast majority of coding tasks. 200K tokens is
  enough for an entire medium-sized codebase in context. This is the sweet spot between
  capability and VRAM.
- **qwen-long** (300K): For situations requiring extreme context (very large repos,
  long documentation). Costs ~6-7 GB extra VRAM for KV cache. Single-seq only.
- **experiment** (128K default): Conservative default for unknown models. Adjust per candidate.

### `--gpu-memory-utilization`
- **matrix-coder** (0.66): Leaves ~23 GB free for Ollama (gemma4 + embeddings).
  Total GPU: ~72 GB. vLLM: ~49 GB used, ~23 GB free for Ollama.
- **qwen-long** (0.60): Lower utilization because the KV cache for 300K context is
  larger. The actual VRAM used is still ~50-55 GB due to the larger cache.
  **Gemma4 must be unloaded before launching** to avoid OOM.
- **experiment** (0.66): Same as matrix-coder unless the candidate needs more.

### `--max-num-seqs`
- **matrix-coder** (3): Supports concurrent requests (pi, harness, skills running simultaneously).
- **qwen-long** (1): Stability at extreme context lengths. Multi-seq at 300K context is
  fragile and wastes VRAM on partial KV caches.
- **experiment** (3): Same as matrix-coder unless the candidate behaves differently.

### `--max-num-batched-tokens`
- **matrix-coder** (8192): Good throughput for typical prompt sizes.
- **qwen-long** (4096): Smaller to leave more VRAM for KV cache at 300K context.
- **experiment** (8192): Same as matrix-coder.

### Shared args (all profiles)
- `--kv-cache-dtype fp8`: Cuts KV cache VRAM by ~50% with minimal quality loss.
- `--enable-prefix-caching`: Reuses KV cache for repeated prefixes (great for pi chat sessions).
- `--enable-chunked-prefill`: Prevents long prompts from starving the decoder.
- `--enable-auto-tool-choice`: Required for pi tool-calling.
- `--tool-call-parser qwen3_xml`: Correct parser for Qwen3.6 tool calls.
- `--trust-remote-code`: Needed for the AutoRound quantized model.

## Switching Between Profiles

### From daily (matrix-coder) to qwen-long
```bash
# 1. Pre-check
preflight.sh qwen-long

# 2. Stop Ollama (frees ~17 GB for Gemma4)
docker compose -f compose.ollama.yml down

# 3. Stop current vLLM
docker compose -f compose.qwen36.yml down

# 4. Start qwen-long
docker compose -f compose.qwen-long.yml up -d

# 5. Verify
curl -sf http://localhost:8000/v1/models && echo OK
```

### From qwen-long back to daily
```bash
# 1. Stop qwen-long
docker compose -f compose.qwen-long.yml down

# 2. Restart daily
docker compose -f compose.qwen36.yml up -d

# 3. Restart Ollama
docker compose -f compose.ollama.yml up -d
```

### From daily to experiment
```bash
# 1. Copy and edit the template
cp compose.experiment.yml compose.experiment-MYMODEL.yml
# Edit: change --model to candidate path, adjust args as needed

# 2. Stop current vLLM
docker compose -f compose.qwen36.yml down

# 3. Start experiment
docker compose -f compose.experiment-MYMODEL.yml up -d

# 4. When done, restore daily
docker compose -f compose.experiment-MYMODEL.yml down
docker compose -f compose.qwen36.yml up -d
rm compose.experiment-MYMODEL.yml  # cleanup
```

## VRAM Budget Summary

| Profile | vLLM | Ollama (gemma4) | Ollama (embeddings) | Total | Fits? |
|---|---|---|---|---|---|
| matrix-coder | ~49 GB | ~17 GB | ~0.3 GB | ~66.3 GB | ✅ (72 GB GPU) |
| qwen-long | ~55 GB | 0 (stopped) | 0 (stopped) | ~55 GB | ✅ (72 GB GPU) |
| experiment | varies | varies | varies | varies | Check preflight |

## Rules

1. **Never run vLLM profiles simultaneously** — they share port 8000 and the same GPU.
2. **Always preflight** before switching: `preflight.sh <profile>`
3. **Benchmark before and after** any arg changes: `benchmark.sh --category all --baseline`
4. **One variable at a time** when tuning — change one arg, benchmark, evaluate.
5. **Quality > numbers** — if tool calling breaks, roll back regardless of throughput gains.
6. **matrix-coder is the default** — always return to it when done with other profiles.
