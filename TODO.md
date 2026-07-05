# Matrix TODO

## Experiment System (2026-07-03) — COMPLETE ✅

### Experiment profiles created and compose files ready
- [x] `experiment-gemma4-31b` — Gemma 4 31B FP8 via vLLM runtime quantization
- [x] `experiment-qwen3-next-80b-thinking-fp8-mtp` — Qwen3-Next 80B MoE FP8 + MTP
- [x] `experiment-qwen36-27b-w8a16-128k-mtp` — Qwen3.6-27B W8A16, 128K ctx, 3 threads + MTP (Path A)
- [x] `experiment-qwen-long-w8a16-mtp` — Qwen3.6-27B W8A16, 262K ctx, 4 threads + MTP
- [x] `experiment-qwen36-int4-mtp` — Qwen3.6-27B INT4 + MTP (Path B — same model, just MTP added)

### Removed incompatible experiments
- [x] **Qwen3-Next-80B-A3B-Thinking-NVFP4** — REMOVED. NVFP4 is TensorRT-LLM-only.
- [x] **Nemotron-3-Nano-30B-A3B-BF16** — REMOVED. Hybrid Mamba-2/Transformer not vLLM-compatible.

### CLI commands implemented
- [x] `model-manager experiment list` / `start` / `switch` / `show` / `archive`
- [x] `model-manager mode rollback` — cleans up active experiment state

### vLLM image
- [x] Latest `vllm/vllm-openai:latest` pulled (v0.21.0, CUDA 12.9)

---

## How to Run Each Experiment

### General workflow for every experiment

```bash
# 1. Record a baseline BEFORE switching (do this once from current daily mode)
./scripts/benchmark.sh --profile matrix-coder --category all --update-baseline

# 2. Start the experiment
./scripts/model-manager experiment start --profile <PROFILE_NAME>

# 3. Wait for it to come up (1-5 min for large models)
#    Check: docker logs <container_name>    (look for "vLLM is starting...")
#    Check: curl http://localhost:8000/v1/models  (should list the model)

# 4. Run the benchmark
./scripts/benchmark.sh --profile <PROFILE_NAME> --category all --baseline

# 5. Review results
cat data/benchmarks/results/*/report.md

# 6. If happy, keep it. If not, rollback:
./scripts/model-manager mode rollback
```

### Experiment 1: Gemma 4 31B FP8
```bash
# Start
./scripts/model-manager experiment start --profile experiment-gemma4-31b
# Container: qwen36 | Model: google/gemma-4-31b-it | ~35-45 GB VRAM
# 128K context, 4 seqs, --quantization fp8 (runtime BF16→FP8)

# Benchmark
./scripts/benchmark.sh --profile experiment-gemma4-31b --category all --baseline
# Focus on: general reasoning quality, coding quality vs current INT4 Qwen
```

### Experiment 2: Qwen3-Next 80B MoE FP8 + MTP
```bash
# Start
./scripts/model-manager experiment start --profile experiment-qwen3-next-80b-thinking-fp8-mtp
# Container: qwen3-next-80b | Model: Qwen/Qwen3-Next-80B-A3B-Thinking-FP8 | ~55-62 GB VRAM
# 64K context, 2 seqs, MTP (2 speculative tokens), deepseek_r1 reasoning parser
# ⚠️ Uses vllm/vllm-openai:latest-cu129-ubuntu2404 (nightly) — may need pull

# Benchmark
./scripts/benchmark.sh --profile experiment-qwen3-next-80b-thinking-fp8-mtp --category all --baseline
# Focus on: MMLU/AIME-level reasoning, MoE throughput, thinking mode quality
```

### Experiment 3: Qwen3.6-27B W8A16 (128K, 3 threads + MTP)
```bash
# Start
./scripts/model-manager experiment start --profile experiment-qwen36-27b-w8a16-128k-mtp
# Container: qwen36-perf | Model: 88plug/Qwen3.6-27B-W8A16 | ~40-48 GB VRAM
# 128K context, 3 seqs, MTP, 92% VRAM, W8A16 compressed-tensors
# ⚠️ Requires vLLM v0.21.0+ (v0.21.0-cu129-ubuntu2404)

# Benchmark
./scripts/benchmark.sh --profile experiment-qwen36-27b-w8a16-128k-mtp --category all --baseline
# Focus on: W8A16 quality vs INT4, MTP throughput gains, 3-thread stability
```

### Experiment 4: Qwen3.6-27B INT4 + MTP (Path B — Minimal change)
```bash
# Start
./scripts/model-manager experiment start --profile experiment-qwen36-int4-mtp
# Container: qwen36-mtp | Model: Lorbus/Qwen3.6-27b-int4-AutoRound | ~48-52 GB VRAM
# 200K context, 3 seqs, 66% VRAM — same model, just adds MTP + reasoning-parser

# Benchmark
./scripts/benchmark.sh --profile experiment-qwen36-int4-mtp --category all --baseline
# Focus on: MTP decode speedup on INT4, same quality as current, tool-calling still works
```

### Experiment 5: Qwen3.6-27B W8A16 (262K long context + MTP)
```bash
# Start
./scripts/model-manager experiment start --profile experiment-qwen-long-w8a16-mtp
# Container: qwen36-long | Model: 88plug/Qwen3.6-27B-W8A16 | ~35-42 GB VRAM
# 262K context, 4 seqs, MTP, 92% VRAM, W8A16 compressed-tensors
# ⚠️ Requires vLLM v0.21.0+ (v0.21.0-cu129-ubuntu2404)

# Benchmark
./scripts/benchmark.sh --profile experiment-qwen-long-w8a16-mtp --category all --baseline
# Focus on: long-context retrieval (needle-in-haystack), MTP at extreme lengths
```

---

## Running a Full Benchmark

### Quick VRAM-only check (fast)
```bash
./scripts/benchmark.sh --vram-only
```

### Full benchmark suite
```bash
# With baseline comparison:
./scripts/benchmark.sh --category all --baseline

# Specific categories:
./scripts/benchmark.sh --category latency    # TTFT
./scripts/benchmark.sh --category throughput  # tokens/sec
./scripts/benchmark.sh --category quality     # coding/tool-calling prompts (manual review)
./scripts/benchmark.sh --category stress      # concurrent request handling
./scripts/benchmark.sh --category gpu         # VRAM + utilization
```

### After reviewing quality benchmarks
The quality outputs are in `data/benchmarks/results/*/quality_*.md` — review these manually.
Coding quality > raw throughput. Roll back if quality degrades.

---

## Upgrading vLLM for Daily Coder

### Current state
- **Image**: `vllm/vllm-openai:latest` (v0.21.0, CUDA 12.9) ✅
- **Model**: `Lorbus/Qwen3.6-27b-int4-AutoRound` (INT4 quantization)
- **Container**: `qwen36`

### Path A — INT4 → W8A16 (recommended, better quality)

The W8A16 model (`88plug/Qwen3.6-27B-W8A16`) is **~99% MMLU recovery vs BF16**, much better
than INT4. Test it via the experiment profile, then promote:

```bash
# 1. Test the W8A16 experiment first
./scripts/model-manager experiment start --profile experiment-qwen36-27b-w8a16-128k-mtp

# 2. Benchmark it against baseline
./scripts/benchmark.sh --category all --baseline

# 3. Review quality outputs manually
cat data/benchmarks/results/latest/quality_*.md

# 4. If quality is equal or better → promote to daily:
#    - Copy compose/experiments/qwen36-27b-w8a16-128k-mtp.yml → compose/qwen-coder.yml
#    - Update models/profiles/matrix-coder.yaml with new model path + args
#    - Or: model-manager mode rollback (to go back to INT4)
```

### Path B — Keep INT4, add MTP (minimal change)

Add MTP speculative decoding to the existing INT4 model. Same VRAM, same model, just faster:

```bash
# 1. Test MTP on current model
./scripts/model-manager experiment start --profile experiment-qwen36-int4-mtp

# 2. Benchmark it against baseline
./scripts/benchmark.sh --category all --baseline

# 3. Review quality outputs manually
cat data/benchmarks/results/latest/quality_*.md

# 4. If MTP works on INT4 → promote to daily:
#    - Edit compose/qwen-coder.yml, add these 3 flags to the command:
#      --reasoning-parser qwen3
#      --speculative-config '{"method":"qwen3_next_mtp","num_speculative_tokens":2}'
#      --default-chat-template-kwargs '{"preserve_thinking":true}'
#    - Remove --enable-auto-tool-choice and --tool-call-parser qwen3_xml (redundant)
#    - Or: model-manager mode rollback (to go back to current config)
```

### Head-to-head comparison

| | Path A (W8A16) | Path B (INT4 + MTP) |
|---|---|---|
| Quality | ~99% MMLU vs BF16 | Same as current (INT4) |
| Throughput | MTP + W8A16 decode speed | MTP decode speed only |
| VRAM | ~40-48 GB | ~48-52 GB (same as now) |
| Context | 128K | 200K (same as now) |
| Risk | Medium (new model + new quant) | Low (same model, 3 new flags) |
| Recommendation | Best overall upgrade | Quick win if W8A16 has issues |

### vLLM version considerations
- **Latest tag** → v0.21.0 — supports compressed-tensors W8A16 ✅
- **v0.21.0-cu129-ubuntu2404** — pinned version used by W8A16 experiments
- **For experiments with MTP** (qwen3_next_mtp): needs v0.21.0+ ✅
- **To upgrade**: `docker pull vllm/vllm-openai:latest` then restart the container



---

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
