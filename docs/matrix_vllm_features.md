# vLLM Feature Evaluation

> Created: 2026-07-03, updated 2026-08-25
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

### Speculative Decoding (External Draft Model)

| Property | Value |
|---|---|
| vLLM Flag | `--speculative-config '{"model_name": "...", "num_steps": N}'` |
| Model Support | Requires a separate draft model (smaller) + target model |
| Status | ❌ Not ready — requires second model on GPU |
| Expected Benefit | Draft model predicts tokens, target verifies → faster generation |
| Risk | Adds VRAM for draft model; complexity; draft model quality matters |
| Note | MTP (above) is *internal* speculative decoding. This section is for *external* draft models. |

**Evaluation Plan:**
1. Identify a small draft model (e.g., Gemma4-2B or similar)
2. Calculate combined VRAM: Qwen3.8-27B NVFP4 (~55GB) + draft model
3. If combined VRAM < 65GB, test with `--speculative-config '{"model_name": "...", "num_steps": 5}'`
4. Benchmark throughput and TTFT
5. Verify coding quality doesn't degrade
6. Likely deferred until we have more VRAM headroom

### FP4 / NVFP4 KV Cache

| Property | Value |
|---|---|
| vLLM Flag | `--kv-cache-dtype nvfp4` |
| Hardware Support | Requires Hopper (H100+) or specific Blackwell support |
| Status | ⏳ Watch — vLLM 0.24.0 lists `nvfp4` as a kv-cache-dtype option |
| Expected Benefit | Even smaller KV cache than FP8 → more context or concurrency |
| Risk | Blackwell support may be immature; quality loss possible |

**Evaluation Plan:**
1. Verify RTX PRO 5000 Blackwell supports NVFP4 in vLLM
2. Test with `--kv-cache-dtype nvfp4` on a non-critical session
3. Compare FP8 vs NVFP4 VRAM, throughput, and coding quality
4. Only promote if quality is indistinguishable and VRAM savings > 15%

### TensorRT-LLM

| Property | Value |
|---|---|
| vLLM Integration | `--load-format tensorrt_llm` |
| Status | ❌ Not ready — requires TensorRT-LLM engine serialization |
| Expected Benefit | Higher throughput via optimized kernels |
| Risk | Complex build process; model must be serialized; limited quantization support |

**Evaluation Plan:**
1. Check if vLLM 0.24.0 supports TensorRT-LLM for this model/quantization
2. Attempt to build a TensorRT-LLM engine for unsloth/Qwen3.8-27B-NVFP4
3. If successful, benchmark vs vanilla vLLM
4. Roll back if build fails or quality degrades

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
| *(none yet)* | | |

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

1. **FP4/NVFP4** — Check Blackwell hardware support (NVFP4 weights already in production; KV-cache nvfp4 still untested)
2. **Speculative decoding** — Wait for VRAM headroom or smaller draft model
3. **TensorRT-LLM** — Lowest priority (complex build, uncertain benefit for NVFP4)
