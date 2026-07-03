# vLLM Feature Evaluation

> Created: 2026-07-03
> vLLM version: 0.21.0
> GPU: NVIDIA RTX PRO 5000 72GB Blackwell
> Model: Lorbus/Qwen3.6-27b-int4-AutoRound

## Current Active Features

These are already in `compose.qwen36.yml` and running in production:

| Feature | Flag | Status | Impact |
|---|---|---|---|
| FP8 KV Cache | `--kv-cache-dtype fp8` | ✅ Active | ~50% less VRAM for KV cache vs FP16 |
| Prefix Caching | `--enable-prefix-caching` | ✅ Active | Reuses KV cache for repeated prefixes (chat sessions) |
| Chunked Prefill | `--enable-chunked-prefill` | ✅ Active | Prevents long prompts from starving the decoder |
| Auto Tool Choice | `--enable-auto-tool-choice` | ✅ Active | Required for pi tool-calling |
| Tool Call Parser | `--tool-call-parser qwen3_xml` | ✅ Active | Correct parser for Qwen3.6 |

## Candidate Features

### MTP (Multi-Token Prediction)

| Property | Value |
|---|---|
| vLLM Flag | `--speculative-config '{"n_predict": 1}'` (auto-detected from model) |
| Model Support | ✅ Confirmed — `mtp_num_hidden_layers: 1` in Qwen3.6-27B config |
| Current Status | ❌ NOT active — `speculative_config=None` in running server |
| Expected Benefit | Generate 1 extra token per forward pass → ~10-20% throughput improvement |
| Risk | Low — model natively supports it; minimal VRAM impact |

**Evaluation Plan:**
1. Enable MTP via `--speculative-config '{"n_predict": 1}'` (model has mtp_num_hidden_layers=1)
2. Test with `compose.experiment-mtp.yml` (copy of qwen36 with MTP flag added)
3. Benchmark TTFT and throughput vs baseline
4. Verify tool calling and coding quality unchanged
5. If gains confirmed, promote to `compose.qwen36.yml`

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
2. Calculate combined VRAM: Qwen3.6-27B-int4 (~49GB) + draft model
3. If combined VRAM < 65GB, test with `--speculative-config '{"model_name": "...", "num_steps": 5}'`
4. Benchmark throughput and TTFT
5. Verify coding quality doesn't degrade
6. Likely deferred until we have more VRAM headroom

### FP4 / NVFP4 KV Cache

| Property | Value |
|---|---|
| vLLM Flag | `--kv-cache-dtype nvfp4` |
| Hardware Support | Requires Hopper (H100+) or specific Blackwell support |
| Status | ⏳ Watch — vLLM 0.21.0 lists `nvfp4` as a kv-cache-dtype option |
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
1. Check if vLLM 0.21.0 supports TensorRT-LLM for this model/quantization
2. Attempt to build a TensorRT-LLM engine for Qwen3.6-27B-int4-AutoRound
3. If successful, benchmark vs vanilla vLLM
4. Roll back if build fails or quality degrades

## Evaluation Protocol

For each feature:

1. **Baseline**: Run `benchmark.sh --category all --update-baseline` on current config
2. **Enable**: Add the feature flag to a temporary compose file (never edit production)
3. **Launch**: `docker compose -f compose.experiment-<feature>.yml up -d`
4. **Benchmark**: Run `benchmark.sh --category all --baseline`
5. **Quality Check**: Run a coding task, tool-calling task, and agent loop
6. **Decide**:
   - ✅ **Promote**: Move flag to `compose.qwen36.yml`
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

## Next Evaluation Priority

1. **MTP** — Check model support (lowest risk, highest potential reward)
2. **FP4/NVFP4** — Check Blackwell hardware support
3. **Speculative decoding** — Wait for VRAM headroom or smaller draft model
4. **TensorRT-LLM** — Lowest priority (complex build, uncertain benefit for int4)
