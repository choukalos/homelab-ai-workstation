# TODO

Consolidated open work across the homelab repo. Sources are listed per item.
Last consolidated: 2026-09-27 (experiment cleanup + backup-chain plan).

## Active

### Backup chain: Lego → Athena + Lego data cleanup (2026-09-27)

Execution plan (human-readable, Phase 1/2 = user, Phase 3 = agent): **`nas_backup_plan.md`**.
Detailed reference: `docs/backup_chain_plan.md`.

- [x] **Lego cleanup: delete old Comcast work dirs** — DONE 2026-09-27 (executed in parallel via DSM while this was being planned): `Comcast - Jan 2023` (49.5 GB), `Comcast - March 2021` (25.4 GB), `Comcast - Dec 2023` (1.3 GB), `Comcast - March 2023` all deleted; `Comcast - Sept 2025` (68.9 GB) kept. Verified 19:23 UTC — only Sept 2025 remains, ~80 GB freed. *(nas_backup_plan.md §4, docs/backup_chain_plan.md §2.1)*
- [ ] **Phase 1 (user): Lego cleanup decisions** — `homes/chuck/#recycle` 966 GB (empty bin?), `chuck/backup` 488 GB, `chuck/iMac_Backup_Critical` 76 GB (2016 iMac), `margarethe/Backup` 121 GB (2008–2018), `margarethe/Margarethe Backup May 2020` 5.8 GB. *(nas_backup_plan.md §4)*
- [ ] **Athena access + capacity** — NAS not discoverable on the LAN (no DSM ports found on 192.168.4.0/22 / .5.0/24). Needed: Athena IP/reachability, free capacity (decides mirror/Hyper Backup scope — Athena is smaller than Lego), USB drive sizes, old rsync script. *(nas_backup_plan.md §7)*
- [ ] **Phase 2 (user): Athena setup** — Hyper Backup "File" task pulling Lego's SMB shares as the `backup` user (versioned store); `lego/` mirror share with per-share subfolders (browsable failover copy for Infuse + SSH-as-chuck); cifs-utils + SSH + static IP. *(nas_backup_plan.md §5)*
- [ ] **Phase 3 (agent): scripts** — Lego→Athena rsync mirror sync (daily, `--delete`, verify, logged); USB + FireSafe backup script replacing the old rsync shell script (auto drive detection for varying sizes, monthly + FireSafe modes, verify, safe unmount); failover runbook; monitoring. *(nas_backup_plan.md §6)*

### vLLM / model experiments

- [ ] **Run experiment 7: Qwen3.8-Flash-Next 125B MoE — 4-bit GGUF via llama.cpp** — Qwen4-arch preview (125B main / 6B active + 51B n-gram embedding, 262K ctx). 111 GB Q4_K_XL fits 72 GiB VRAM + 62 GiB RAM; vLLM path is a no-go (FP8 = 173 GiB). Steps:
  1. Pre-download (111 GB, ~4 shards): `huggingface-cli download unsloth/Qwen3.8-Flash-Next-GGUF --include "UD-Q4_K_XL/*" --local-dir /home/chuck/data/models/gguf`
  2. Stop `qwen-coder` daily driver (frees port 8000 + GPU); let ollama idle-release the GPU (keep_alive 5m)
  3. `docker compose -f compose/experiments/qwen38-flash-next-gguf.yml up -d`
  4. Smoke-test via LiteLLM (`matrix-coder`), then benchmark vs the 27B daily driver
  5. Restore `qwen-coder.yml` on port 8000 when done (or promote Flash-Next if it wins)
  Fallback if RAM-tight: `UD-IQ3_XXS` (82 GB, 85.4% retention). *(EXPERIMENTS_RESULTS.md, Experiment 7)*
- [ ] **vLLM deferred features** — keep as candidates, revisit later (see the candidate table + evaluation protocol). *(docs/matrix_vllm_features.md)*

### Media pipeline / MCP follow-ups (from 2026-09-25 verification)

- [x] **MCP tool calls abort at ~30 s while pipeline jobs keep running** — **FIXED (thor `mcp_media` tool, 2026-09-25): submit-and-poll** — every job tool takes `non_blocking` (returns `{job_id}` in ~1 s; `false` = legacy blocking behavior) and a new `media_job_result(job_id, wait_seconds≤25)` polls under the 30 s client abort, so no tool call can exceed it. Retested live 2026-09-25: image job `14802a5b1eee` (qwen21, 25 steps) submitted non-blocking and polled to `done` over 2 polls (~50 s total) with no client abort. *(docs/matrix_validation_log.md, Run 2026-09-25, Findings)*
- [x] **`media_pull` rejects `media_jobs/<job_id>/<file>` paths (404)** — **FIXED (thor `mcp_media` tool, 2026-09-25):** `media_pull` now accepts `media_jobs/<job_id>/<file>` paths (verified: signed URL minted for `media_jobs/8fb134a2ea1b/mp_8fb134a2ea1b_00001_.png`). *(docs/matrix_validation_log.md, Run 2026-09-25, Findings)*
- [ ] **Pipeline is a single-worker queue** — jobs run strictly serially (a CPU ffmpeg freeze queued behind a GPU image edit). Fine for now; revisit if throughput matters (per-flow worker pools). *(docs/matrix_validation_log.md, Run 2026-09-25, Findings)*

### Modes / switching

- [ ] **Verify `qwen-long` mode switch end-to-end** — compose + profile exist but the switch has not been verified in production. *(docs/matrix_runtime_modes.md)*
- [ ] **Embeddings decision review** — re-evaluate "keep embeddings on Matrix" before Phase 15 production deployment (decision + revisit conditions documented). *(docs/matrix_embeddings_decision.md)*

### Housekeeping

- [x] **Clean up stale containers** — `vllm-gemma`, `vllm-qwen`, `ollama-model-puller` no longer exist (removed earlier); `comfyui_backend` is a live service. The 7 remaining stopped experiment containers were removed 2026-09-27 (experiment cleanup). *(docs/matrix_manual_tasks.md, resolved 2026-08-28; containers resolved 2026-09-27)*
- [x] **Set `HF_TOKEN` in `.env`** — verified set (2026-08-28). *(docs/matrix_manual_tasks.md, resolved 2026-08-28)*

## Dropped

- ~~Rerun experiments 4 & 5~~ (2026-08-28) — Qwen3.6 W8A16 128K and Qwen-long W8A16 262K superseded by Qwen3.8 (current NVFP4 daily driver + Qwen3.8-Flash-Next candidate). *(EXPERIMENTS_RESULTS.md, Next Steps)*
- ~~Run experiment 6: Nemotron-3-Puzzle-75B NVFP4~~ (2026-09-27) — old model; Experiment 7 (Qwen3.8-Flash-Next 125B) is a far better next candidate. *(EXPERIMENTS_RESULTS.md, Experiment 6)*
- ~~Qwen3-Next-80B FP8 experiment~~ (2026-09-27) — old model, never run; Flash-Next supersedes it. *(EXPERIMENTS_RESULTS.md, Experiment 3)*
- ~~Consider W8A16 + MTP as a quality upgrade~~ (2026-09-27) — the W8A16 experiments were Qwen3.6 (dropped 2026-08-28); MTP is already in production on the NVFP4 daily driver (2 tokens, tuned 2026-08-25); no Qwen3.8 8-bit candidate identified — revisit only if 4-bit quality issues surface. *(EXPERIMENTS_RESULTS.md, Next Steps)*

## Done (this consolidation)

- [x] **`media-mcp-client/` resync (2026-09-27)** — resolved by referencing the official repo instead of keeping a snapshot: the authoritative `mcp_media` implementation is [`mcp/servers/media`](https://github.com/choukalos/homelab-ai-harness/tree/main/mcp/servers/media) in `github.com/choukalos/homelab-ai-harness` (all 2026-09-25 updates checked in + pushed: tool renames, `media_job_result`/`media_put`/`media_fetch`, `non_blocking`, `{path, location}` shapes — 22 tools). The stale pre-2026-09-25 snapshot (`mcp_tools.py`, `media_pipeline_client.py`, `HANDOFF.md`) was deleted; `media-mcp-client/` is now a pointer doc. Contract: GitHub README + `docs/matrix_media_pipeline_api.md`. *(TODO.md, 2026-09-25 verification)*

- [x] **Experiment cleanup (2026-09-27)** — dropped experiments 3 (Qwen3-Next-80B FP8) & 6 (Nemotron-3-Puzzle-75B NVFP4) as old models; Experiment 7 (Flash-Next 125B) is the next candidate. Removed: 9 experiment compose files + 9 profiles (round-1 + Qwen3.8-round), 7 stopped experiment containers, ~88 GB re-downloadable weights (gemma-4-31b-it 59G, Qwen3.8-27B-FP8 29G, Qwen3-Next-80B 16M, 88plug W8A16 refs). Kept: Lorbus INT4 18G (live `qwen-long` mode), cyankiwi gemma-4-26B AWQ 17G (live Ollama), unsloth NVFP4 22G (daily driver). Results archived in EXPERIMENTS_RESULTS.md.
- [x] **Media MCP auth + `media_pull` path fix (thor, 2026-09-25)** — auth for the `mcp_media` tool updated (key = user, key passed to the pipeline); finished files exposed publicly via `siri.choukalos.com` / `choukalos.com/files`. `media_pull` now accepts `media_jobs/<job_id>/<file>` paths (verified: signed URL minted). `auth_todo.md` deleted post-completion. The 30 s MCP tool-call abort was fixed the same day (submit-and-poll on the `mcp_media` tool side — see Active).
- [x] **Media MCP client + TTS voice library (matrix + thor, 2026-09-25)** — Qwen-Image-2.1 client defaults + `model`/`references` pass-through, voice library (`trailer`/`default`/`narrator_f`/`deep_m`), and the three voice MCP tools (`media_list_voices` / `media_add_voice` / `media_delete_voice`) deployed on the MCP host and verified end-to-end (15 checks: gen/edit/legacy/image-refs, TTS by name + by path + unknown-voice 400, add/delete round-trip, freeze/assemble smoke, pull/info). Evidence: `docs/matrix_validation_log.md` (Run 2026-09-25); contract: `docs/matrix_media_pipeline_api.md` + the `mcp_media` GitHub README (MCP side — `mcp/servers/media` in the homelab-ai-harness repo; the local `HANDOFF.md` snapshot was removed 2026-09-27). Working docs `update_media_mcp_todo.md`, `voices_todo.md`, `voices_thor_handoff.md` deleted post-completion.
- [x] **Experiment system: manual testing** — the system has been exercised end-to-end in production: MTP experiment (2026-07-05), Qwen3.8-27B NVFP4 candidate round (2026-08-14 → 2026-08-24), promotion to `matrix-coder`, MTP 3→2 tuning + speed fix (2026-08-25, 123.95 tok/s). *(TODO.md, originally "Manual Testing")*
- [x] **Pre-git-commit cleanup** — `.gitignore` covers `__pycache__/` and `*.pyc`; zero tracked pyc files. *(TODO.md, originally "Pre-Git Commit Cleanup")*
- [x] **ComfyUI legacy model/workflow cleanup (2026-08-28)** — removed ~55 GB of obsolete models (SD1.5/SDXL/SVD checkpoints, old LTXV/SeedVR2 builds, dup XTTS dir), 6 legacy workflow JSONs, 4 obsolete custom nodes, and scratch/venv caches. All 25 pipeline models verified intact; pipeline + ComfyUI healthy. See `docs/matrix_comfyui_media_api.md` changelog.
- [x] **Fresh inventory snapshot (2026-08-28)** — appended to `docs/matrix_inventory.md` (append-only): current containers, Qwen3.8 vLLM config, media stack, model storage sizes, post-cleanup notes.