# Matrix TODO

## Promotion: Qwen3.8-27B NVFP4 → main coding setup (2026-08-24)

The `experiment-qwen38-27b-nvfp4` experiment (`unsloth/Qwen3.8-27B-NVFP4`) is promoted to the
primary coding model (`matrix-coder`), replacing Qwen3.6-27B INT4 + MTP.

### Done
- [x] `compose/qwen-coder.yml` — now serves `unsloth/Qwen3.8-27B-NVFP4` as `qwen38-27b` (verified experiment config, benchmark `20260823_160055`)
- [x] `models/profiles/matrix-coder.yaml` — updated to Qwen3.8-27B NVFP4
- [x] `models/profiles/experiment-qwen38-27b-nvfp4.yaml` — marked `promoted`
- [x] Docs updated: `README.md`, `EXPERIMENTS_RESULTS.md`
- [x] Switched to production mode (benchmark `20260823_210603` ran against matrix-coder with the new model)
- [x] Thor: LiteLLM `matrix-coder` entry updated `openai/qwen36-27b` → `openai/qwen38-27b` (`thor.litellm.config.yml`)
- [x] Verified in production: benchmark `20260825_222844` — 123.95 tok/s, 80/80 stress

### Speed fix (2026-08-25) — applied + verified
- [x] Removed `--enforce-eager` from `compose/qwen-coder.yml` (it disabled torch.compile + CUDA graphs; decode was CPU-launch-bound at 58-63 tok/s @ 56-59% GPU util)
- [x] MTP 3 → 2 speculative tokens (3rd token acceptance ~55%; 2 matches the vLLM Qwen3-Next recipe)
- [x] Verified: benchmark `20260825_222844` — **123.95 tok/s** @ 92% GPU util (up from 58-63 tok/s), TTFT 35.5 ms, stress 80/80
- Fallback if CUDA graph capture OOMs: `--compilation-config '{"cudagraph_mode":"PIECEWISE"}'`

Notes:
- Container renamed `qwen36` → `qwen38` (model-manager + all primary-slot compose files updated); served model name is `qwen38-27b`
- `qwen-long` mode still serves Qwen3.6-27B INT4 (240K ctx) — separate compose file (container `qwen38`, serves `qwen38-27b`)

---

## Experiment System (2026-07-03) — COMPLETE ✅

### Experiment profiles created and compose files ready
- [x] `experiment-gemma4-31b` — Gemma 4 31B FP8 via vLLM runtime quantization
- [x] `experiment-qwen3-next-80b-thinking-fp8-mtp` — Qwen3-Next 80B MoE FP8 + MTP
- [x] `experiment-qwen36-27b-w8a16-128k-mtp` — Qwen3.6-27B W8A16, 128K ctx, 3 threads + MTP (Path A)
- [x] `experiment-qwen-long-w8a16-mtp` — Qwen3.6-27B W8A16, 262K ctx, 4 threads + MTP
- [x] `experiment-qwen36-int4-mtp` — Qwen3.6-27B INT4 + MTP (Path B — same model, just MTP added)
- [x] `experiment-qwen38-27b-fp8` — Qwen3.8-27B FP8, 128K ctx, 3 threads + MTP, vision, tool calling
- [x] `experiment-qwen38-27b-nvfp4` — Qwen3.8-27B NVFP4 (NVIDIA 4-bit) — **PROMOTED to matrix-coder 2026-08-24**

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
# Container: gemma4-31b | Model: google/gemma-4-31b-it | ~35-45 GB VRAM
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

### Experiment 6: Qwen3.8-27B FP8 (Vision + Tool Calling + MTP)
```bash
# Start
./scripts/model-manager experiment start --profile experiment-qwen38-27b-fp8
# Container: qwen38-fp8 | Model: Qwen/Qwen3.8-27B-FP8 | ~40-50 GB VRAM
# 128K context, 3 seqs, MTP (3 tokens), FP8 KV, vision + tool calling
# Uses: --reasoning-parser qwen3, --tool-call-parser qwen3_coder

# Benchmark
./scripts/benchmark.sh --profile experiment-qwen38-27b-fp8 --category all --baseline
# Focus on: reasoning quality, tool calling accuracy, vision quality vs Qwen3.6

# Test vision: python3 qwen3.8-experiment/test_image_analysis.py --image /path/to/photo.jpg
# Test tools: python3 qwen3.8-experiment/test_tool_calling.py --backend vllm
```

### Experiment 7: Qwen3.8-27B NVFP4 (Most Efficient + Vision + Tool Calling) — PROMOTED 2026-08-24
```bash
# Start (still available in the experiment slot for re-testing)
./scripts/model-manager experiment start --profile experiment-qwen38-27b-nvfp4
# Container: qwen38 | Model: unsloth/Qwen3.8-27B-NVFP4 | ~58 GB VRAM (measured)
# 196K context, 3 seqs, 75% VRAM, FP8 KV, vision + tool calling
# ⚠️ NVFP4 is Blackwell-only (RTX PRO 5000) — won't work on Ada/Hopper

# Benchmark
./scripts/benchmark.sh --profile experiment-qwen38-27b-nvfp4 --category all --baseline
# Focus on: NVFP4 quality vs FP8, VRAM efficiency, throughput

# Test vision: python3 qwen3.8-experiment/test_image_analysis.py --image /path/to/photo.jpg
# Test tools: python3 qwen3.8-experiment/test_tool_calling.py --backend vllm
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

## Manual Testing

### model-manager script fixes
- [ ] `model-manager experiment start` (no model) — should show usage and NOT generate a compose file
- [ ] `model-manager experiment start --mtp <actual-model-path>` — should generate a compose with `${HF_TOKEN}` (literal variable reference) and `--speculative-config`

### qwen-long optimization
- [ ] Switch to qwen-long mode with new settings and verify it starts and serves:
  - `model-manager mode switch qwen-long`
  - Check: vLLM comes up, `http://localhost:8000/v1/models` responds
  - Check: `docker logs qwen38` shows MTP enabled without errors
  - Test: 2 concurrent requests work (or note if VRAM is exceeded)
  - Compare throughput vs old single-seq baseline using `benchmark.sh`
  - Roll back: `model-manager mode rollback`

## Pre-Git Commit Cleanup

See notes below before committing.