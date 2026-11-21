# vLLM Feature Evaluation

> Created: 2026-07-03, updated 2026-08-25, **updated 2026-11-21 (all three deferred
candidates rejected — not interesting; candidate list is now empty)**
> vLLM version: 0.24.0
> GPU: NVIDIA RTX PRO 5000 72GB Blackwell
> Model: unsloth/Qwen3.8-27B-NVFP4 (since 2026-08-24; previously Lorbus/Qwen3.6-27b-int4-AutoRound)

## Current Active Features

These are already in `compose/qwen-coder.yml` and running in production:

| Feature | Flag | Status | Impact |
|---|---|---|---|
| FP8 KV Cache | `--kv-cache-dtype fp8` | ✅ Active | ~50% less VRAM for KV cache vs FP16 |
| Prefix Caching | `--enable-prefix-caching` | ✅ Active | Reuses KV cache for repeated prefixes (chat sessions) |
| Chunked Prefill | `--enable-chunked-prefill` | ✅ Active | Prevents long prompts from starving the decoder |
| Auto Tool Choice | `--enable-auto-tool-choice` | ✅ Active | Required for pi tool-calling |
| Tool Call Parser | `--tool-call-parser qwen3_coder` | ✅ Active | Correct parser for Qwen3.8 (since 2026-08-24) |
| Reasoning Parser | `--reasoning-parser qwen3` | ✅ Active | Parses thinking blocks (`preserve_thinking`, `reasoning_effort=high`) |
| MTP Speculative Decoding | `--speculative-config '{"method":"mtp","num_speculative_tokens":2}'` | ✅ Active | Internal draft tokens; 2 tokens since 2026-08-25 (tuned down from 3 after acceptance-rate check) |
| Vision (image + video) | `--limit-mm-per-prompt '{"image": 5, "video": 2}'` | ✅ Active | Native Qwen3.8 multimodal inputs (since 2026-08-24) |

## Candidate Features

### MTP (Multi-Token Prediction) — ✅ PROMOTED

| Property | Value |
|---|---|
| vLLM Flag | `--speculative-config '{"method":"mtp","num_speculative_tokens":2}'` |
| Model Support | ✅ Confirmed — `mtp_num_hidden_layers: 1` in Qwen3.6-27B and Qwen3.8-27B configs |
| Current Status | ✅ Active in production since 2026-08-14 (INT4+MTP daily driver); carried over to Qwen3.8 NVFP4 at the 2026-08-24 promotion |
| History | Promoted 2026-08-14 (137 tok/s with INT4, baseline `20260814_233938`). Added at the 2026-08-24 promotion with 3 tokens; tuned to 2 on 2026-08-25 after the 3rd token showed ~55% acceptance (vLLM warns multi-forward on the same MTP layer lowers acceptance). Post-fix: 123.95 tok/s (benchmark `20260825_222844`) |
| Risk | Low — model natively supports it; minimal VRAM impact |

### Speculative Decoding (External Draft Model) — ❌ REJECTED 2026-11-21

`--speculative-config '{"model_name": "...", "num_steps": N}'` — a separate small
draft model predicts tokens, the target verifies. **Rejected (user decision
2026-11-21: not interesting).** MTP (above) already provides *internal*
speculative decoding (2 tokens, active in production), and the Phase 4 0.95
config leaves only ~3 GB of GPU 0 headroom — no room for a draft model.

### FP4 / NVFP4 KV Cache — ❌ REJECTED 2026-11-21

`--kv-cache-dtype nvfp4` — smaller KV cache than the active FP8. **Rejected
(user decision 2026-11-21: not interesting).** The active FP8 KV cache is
sufficient at the Phase 4 config (256K ctx, KV pool 42.47 GiB = 1.2M tokens);
Blackwell support was unverified and quality risk was open.

### TensorRT-LLM — ❌ REJECTED 2026-11-21

`--load-format tensorrt_llm` — higher throughput via optimized kernels.
**Rejected (user decision 2026-11-21: not interesting).** Complex engine
serialization for uncertain benefit on an already-NVFP4 model.

## Evaluation Protocol

For each feature:

1. **Baseline**: Run `benchmark.sh --category all --update-baseline` on current config
2. **Enable**: Add the feature flag to a temporary compose file (never edit production)
3. **Launch**: `docker compose -f compose/experiments/<feature>.yml up -d`
4. **Benchmark**: Run `benchmark.sh --category all --baseline`
5. **Quality Check**: Run a coding task, tool-calling task, and agent loop
6. **Decide**:
   - ✅ **Promote**: Move flag to `compose/qwen-coder.yml`
   - ⏸️ **Defer**: Keep as candidate, revisit later
   - ❌ **Reject**: Document why in rejection log

### Rejection Log

| Feature | Date | Reason |
|---|---|---|
| Speculative Decoding (External Draft Model) | 2026-11-21 | Not interesting (user decision). MTP internal speculative decoding is active; no VRAM headroom for a draft model (0.95 config) |
| FP4 / NVFP4 KV Cache (`--kv-cache-dtype nvfp4`) | 2026-11-21 | Not interesting (user decision). FP8 KV cache sufficient at the Phase 4 256K/0.95 config |
| TensorRT-LLM (`--load-format tensorrt_llm`) | 2026-11-21 | Not interesting (user decision). Complex serialization, uncertain benefit for the NVFP4 model |

### Promotion Log

| Feature | Date | Baseline → After | Notes |
|---|---|---|---|
| FP8 KV Cache | 2026-07-03 | — | Inherited from initial config |
| Prefix Caching | 2026-07-03 | — | Inherited from initial config |
| Chunked Prefill | 2026-07-03 | — | Inherited from initial config |
| MTP (2 tokens) | 2026-08-14 | — | 137 tok/s with INT4+MTP (baseline `20260814_233938`); 3→2 tokens on 2026-08-25 (acceptance rate) |
| Tool call parser `qwen3_coder` | 2026-08-24 | — | Supersedes `qwen3_xml` with the Qwen3.8 promotion |
| CUDA graphs (removed `--enforce-eager`) | 2026-08-25 | 58-63 → 123.95 tok/s | Speed fix for Qwen3.8 NVFP4 — enforce-eager had disabled torch.compile + CUDA graphs (benchmark `20260825_222844`) |

## Next Evaluation Priority

**None — the candidate list is empty** (all three deferred features rejected
2026-11-21 as not interesting). If a new vLLM feature becomes interesting, use
the Evaluation Protocol above.
