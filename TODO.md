# Matrix TODO

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
