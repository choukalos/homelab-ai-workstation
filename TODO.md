# TODO

Consolidated open work across the homelab repo. Sources are listed per item.
Last consolidated: 2026-08-28.

## Active

### vLLM / model experiments

- [ ] **Rerun experiments 3, 4, 5** (Qwen3-Next-80B FP8, Qwen3.6 W8A16 128K, Qwen-long W8A16 262K) — configs fixed after the vLLM 0.24.0 CLI change, ready to launch. *(EXPERIMENTS_RESULTS.md, Next Steps)*
- [ ] **Run experiment 6: Nemotron-3-Puzzle-75B-A9B NVFP4** — config + profile ready. ⚠️ Pull latest `vllm/vllm-openai:latest` first (NVFP4 Marlin fallback needs v0.22.1+). *(EXPERIMENTS_RESULTS.md, Next Steps)*
- [ ] **Consider W8A16 + MTP** as a potential quality upgrade over the current NVFP4 daily driver. *(EXPERIMENTS_RESULTS.md, Next Steps)*
- [ ] **vLLM deferred features** — keep as candidates, revisit later (see the candidate table + evaluation protocol). *(docs/matrix_vllm_features.md)*

### Modes / switching

- [ ] **Verify `qwen-long` mode switch end-to-end** — compose + profile exist but the switch has not been verified in production. *(docs/matrix_runtime_modes.md)*
- [ ] **Embeddings decision review** — re-evaluate "keep embeddings on Matrix" before Phase 15 production deployment (decision + revisit conditions documented). *(docs/matrix_embeddings_decision.md)*

### Housekeeping

- [x] **Clean up stale containers** — `vllm-gemma`, `vllm-qwen`, `ollama-model-puller` no longer exist (removed earlier); `comfyui_backend` is a live service. Remaining 7 stopped experiment containers are optional cleanup, left in place for the pending experiment reruns. *(docs/matrix_manual_tasks.md, resolved 2026-08-28)*
- [ ] **Set `HF_TOKEN` in `.env`** — currently empty; latent issue, only matters when a future gated model needs authenticated download. *(docs/matrix_manual_tasks.md)*

## Done (this consolidation)

- [x] **Experiment system: manual testing** — the system has been exercised end-to-end in production: MTP experiment (2026-07-05), Qwen3.8-27B NVFP4 candidate round (2026-08-14 → 2026-08-24), promotion to `matrix-coder`, MTP 3→2 tuning + speed fix (2026-08-25, 123.95 tok/s). *(TODO.md, originally "Manual Testing")*
- [x] **Pre-git-commit cleanup** — `.gitignore` covers `__pycache__/` and `*.pyc`; zero tracked pyc files. *(TODO.md, originally "Pre-Git Commit Cleanup")*
- [x] **ComfyUI legacy model/workflow cleanup (2026-08-28)** — removed ~55 GB of obsolete models (SD1.5/SDXL/SVD checkpoints, old LTXV/SeedVR2 builds, dup XTTS dir), 6 legacy workflow JSONs, 4 obsolete custom nodes, and scratch/venv caches. All 25 pipeline models verified intact; pipeline + ComfyUI healthy. See `docs/matrix_comfyui_media_api.md` changelog.
- [x] **Fresh inventory snapshot (2026-08-28)** — appended to `docs/matrix_inventory.md` (append-only): current containers, Qwen3.8 vLLM config, media stack, model storage sizes, post-cleanup notes.