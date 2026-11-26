# TODO

Consolidated open work across the homelab repo. Sources are listed per item.
Last consolidated: 2026-11-21 (M1–M5 follow-ups closed; T1–T5 + media-film skill marked live; Experiment 8 consolidated into add_card_plan Phase 5; benchmark plan status corrected to IMPLEMENTED; embeddings decision reviewed + closed).

## Active

### Tab tool: pitch/vibrato + lyrics capture (2026-11-08, DONE)

Thor flagged two remaining items on the transcribe/tab pipeline. Both done:

- **Pitch/vibrato detection** — already shipped (v1.4.1, commit `4d2658c`):
  per-note `pitch_contour` (5-point semitone-vs-nominal) on every note in
  `notes.json` → `analysis.json`, plus the tab.md pitch/vibrato note. Verified
  on Back In Black: 1,117/1,117 notes carry contours (max |dev| 2.33 st).
- **Lyrics capture** — root cause: Silero VAD (faster-whisper's `vad_filter`)
  is trained on clean speech and keeps only ~2% of a dense-rock vocal stem
  (Back In Black: 3 garbled lines from a 4-min song). Fix: two-pass ASR in
  `workers/transcribe_worker.py` — pass A (stem + VAD) as before; when VAD
  keeps <25% of a >60 s track, pass B runs on the full mix with VAD off; the
  pass with more words wins (deterministic). New `analysis.json` fields:
  `lyrics_source` (`stem_vad`|`mix_vad`|`mix_no_vad`), `lyrics_vad_kept_frac`,
  `lyrics_passes`. Verified: Back In Black now 62 clean lines (was 3), Riptide
  unchanged (stem_vad, 53 lines, no fallback). Also fixed worker stdout being
  dropped on success in `server.py run_worker` (stage/coverage lines now in
  the pipeline log). Spec updated: `docs/transcribe_pipeline_spec.md`.

### Backup chain: Lego → Athena + Lego data cleanup (2026-09-27)

Execution plan (human-readable, Phase 1/2 = user, Phase 3 = agent): **`nas_backup_plan.md`**.
Detailed reference: `docs/backup_chain_plan.md`.

- [x] **Lego cleanup: delete old Comcast work dirs** — DONE 2026-09-27 (executed in parallel via DSM while this was being planned): `Comcast - Jan 2023` (49.5 GB), `Comcast - March 2021` (25.4 GB), `Comcast - Dec 2023` (1.3 GB), `Comcast - March 2023` all deleted; `Comcast - Sept 2025` (68.9 GB) kept. Verified 19:23 UTC — only Sept 2025 remains, ~80 GB freed. *(nas_backup_plan.md §4, docs/backup_chain_plan.md §2.1)*
- [ ] **Phase 1 (user): Lego cleanup decisions** — `homes/chuck/#recycle` 966 GB (excluded from the mirror regardless; delete if you like), `chuck/backup` 488 GB, `chuck/iMac_Backup_Critical` 76 GB (2016 iMac), `margarethe/Backup` 121 GB (2008–2018), `margarethe/Margarethe Backup May 2020` 5.8 GB. Every GB deleted shrinks the mirror. *(nas_backup_plan.md §4)*
- [ ] **Athena power-on + drive details** — IP 192.168.5.110 (powered off 2026-09-27); ~4–5 TB free; USB drives ~12/8/6 TB (confirm sizes + which is which; is FireSafe a 4th drive?); how 4K movies are organized in `Multimedia/video` (12 TB vs 8 TB split rule); is Athena on 24/7? *(nas_backup_plan.md §7)*
- [ ] **Phase 2 (user): Athena setup (10 min)** — `lego/` mirror share + 8 per-share subfolders (no `video` — 15.1 TB excluded from Athena; goes to USB), cifs-utils, SSH + `chuck` user, static IP, Infuse library. No Hyper Backup (dropped 2026-09-27 — plain mirror only). *(nas_backup_plan.md §5)*
- [ ] **Phase 3 (agent): scripts** — Lego→Athena rsync mirror (Athena pulls via cifs as `backup` user, daily, `--delete`, excludes `#recycle`/snapshots, verify, logged, **10% free-space flag**); USB copy script replacing the old rsync script (drive map: 12 TB ← video, 8 TB ← 4K, 6 TB ← everything else; layout mirrors Lego's structure; auto drive detection, monthly + FireSafe modes, verify, safe unmount); failover runbook; health check. *(nas_backup_plan.md §6)*

### vLLM / model experiments

- [x] **Experiment 8 follow-up: Flash-Next long-context verification + promotion decision** — **CONSOLIDATED into `add_card_plan.md` Phase 5 (2026-11-21)**: the x16 baseline is captured (291.4 tok/s, 2026-11-14 rerun — boot-journal proven; first 2026-11-13 run invalid — CPU-only image), and the pending items (long-context quality rerun with `reasoning_content` + larger `max_tokens`; 2×256K → 4×256K concurrent decode at x8 to verify QSA sparse attention; promotion decision vs the bumped 27B) are now Phase 5's gate sequence. *(add_card_plan.md Phase 0b/5, EXPERIMENTS_RESULTS.md Experiment 8)*
- [x] **vLLM deferred features** — **CLOSED 2026-11-21 (all 3 rejected — not interesting):** external draft-model speculative decoding, NVFP4 KV cache, and TensorRT-LLM are all dropped (user decision). Recorded in the rejection log; the candidate list is now empty. *(docs/matrix_vllm_features.md — Rejection Log + Next Evaluation Priority)*

### Media pipeline / MCP follow-ups (from 2026-09-25 verification)

- [x] **MCP tool calls abort at ~30 s while pipeline jobs keep running** — **FIXED (thor `mcp_media` tool, 2026-09-25): submit-and-poll** — every job tool takes `non_blocking` (returns `{job_id}` in ~1 s; `false` = legacy blocking behavior) and a new `media_job_result(job_id, wait_seconds≤25)` polls under the 30 s client abort, so no tool call can exceed it. Retested live 2026-09-25: image job `14802a5b1eee` (qwen21, 25 steps) submitted non-blocking and polled to `done` over 2 polls (~50 s total) with no client abort. *(docs/matrix_validation_log.md, Run 2026-09-25, Findings)*
- [x] **`media_pull` rejects `media_jobs/<job_id>/<file>` paths (404)** — **FIXED (thor `mcp_media` tool, 2026-09-25):** `media_pull` now accepts `media_jobs/<job_id>/<file>` paths (verified: signed URL minted for `media_jobs/8fb134a2ea1b/mp_8fb134a2ea1b_00001_.png`). *(docs/matrix_validation_log.md, Run 2026-09-25, Findings)*
- [x] **Pipeline is a single-worker queue** — **DROPPED 2026-11-21 (decision: keep single-worker).** Jobs run strictly serially (`MAX_CONCURRENT_JOBS=1`); a CPU ffmpeg freeze queues behind a GPU job. User confirmed this is fine — no per-flow worker pools. *(docs/matrix_validation_log.md, Run 2026-09-25, Findings — the finding stands as documented behavior)*

### Media pipeline / MCP follow-ups (2026-11-02 incident)

Context: 2026-11-02 film run — 4 client-side orchestration crashes lost 9 job
IDs (unrecoverable: in-memory registry only, no log lines, no listing). All
sides fixed: Matrix 2026-11-04, thor T1–T5 live (verified 2026-11-21), Mac
skill implemented (harness `agents-skills/media-film/`). Incident context +
environment map: `docs/media_film_skill_spec.md` §1–2.

- [x] **Matrix: lost job IDs unrecoverable** — **FIXED 2026-11-04 (matrix `media-pipeline`):** `GET /jobs` (durable `jobs.jsonl` ledger + live registry; filters `status`/`client`/`user`/`flow`/`since`/`limit`), job IDs logged to `docker logs media_pipeline` (submit + finish), structured 404 on unknown job. Verified live incl. ledger durability across a container restart. Evidence: `docs/matrix_validation_log.md` (Run 2026-11-04); contract: `docs/matrix_media_pipeline_api.md` §2 + §7.1. SQLite registry evaluated + deferred (ledger covers it at current volume).
- [x] **Thor: mcp_media additions (T1–T5)** — **LIVE (verified 2026-11-21)**: SUBMIT/DONE log lines, `media_list_jobs` tool + `client` passthrough, 60 s MCP timeout in tool descriptions, `media_fetch` basename-collision fix, mcp_vision permission fix — all in the deployed `mcp_media` server (harness repo commit `23a2e79` records "thor_media_mcp_handoff T1-T5 live"; deployed tool list confirms `media_list_jobs` + submit-and-poll). Spec: `docs/thor_media_mcp_handoff.md`. M1–M5 additions (`media_kill_job`, `media_queue_status`) shipped 2026-11-21 (harness `7f2d0d3`, 24/24 live acceptance).
- [x] **Mac: `media-film` orchestration skill** — **IMPLEMENTED** in the harness repo (`agents-skills/media-film/`: SKILL.md + references + templates + tools) — state file after every submit, retried MCP calls, timeboxed chunks, resume protocol on the durable ledger, QC gates, publish checklist. Extended 2026-11-19 with the SW Holiday Spoof production lessons (C1–C14, A2, D1) and the M1–M5 contract (2026-11-21). Spec: `docs/media_film_skill_spec.md` (superseded by the skill itself).

### Modes / switching

- [x] **Expose `gemma4:31b` (GPU 1 copywriter) in Thor LiteLLM as `matrix-copywriter`** — **LIVE (2026-11-26)**: `ollama/gemma4:31b` → `http://matrix:11434` added to `thor.litellm.config.yml` with `model_group_fallbacks: matrix-copywriter → [matrix-coder]`; 7-point verification checklist passed (serves / GPU-1 load / idle-unload / fallback-while-media-held / evict-on-media / manual swap). Contract updated to v1.5 (`docs/matrix_thor_contract.md` §3); profile's "Thor entry" note closed. Plan: `docs/thor_copywriter_litellm_plan.md` (CLOSED). Closes the Phase 3 open item on `ollama-copywriter-gpu1`.

- [x] **Verify `qwen-long` mode switch end-to-end** — **DROPPED 2026-11-21 (mode retired, never verified):** the bumped 27B daily serves 256K natively (Phase 4), covering the long-context use case on the retired model. `compose/qwen-long.yml` + `models/profiles/qwen-long.yaml` deleted; mode removed from `model-manager` / `preflight.sh` / `matrix_health.sh`; operational docs updated (historical records untouched). The 18 GB `Lorbus/Qwen3.6-27b-int4-AutoRound` weights were removed 2026-11-22 (re-downloadable; root-owned HF cache, deleted via the `qwen38` container). *(docs/matrix_runtime_modes.md — retirement note)*
- [x] **Embeddings decision review** — **REVIEWED 2026-11-21, decision stands**: the "before Phase 15" trigger was stale (Phase 15 shipped months ago) and no revisit condition met (still nomic-embed-text 274 MB; <100 MB VRAM on GPU 1; no Ollama bottleneck). Disambiguation recorded: the decision covers the RAG **embedding** model only — the daily driver **LLM** has been multimodal (native image + video) since 2026-08-24, a different component. *(docs/matrix_embeddings_decision.md, Review 2026-11-21)*

### Housekeeping

- [x] **Clean up stale containers** — `vllm-gemma`, `vllm-qwen`, `ollama-model-puller` no longer exist (removed earlier); `comfyui_backend` is a live service. The 7 remaining stopped experiment containers were removed 2026-09-27 (experiment cleanup). *(docs/matrix_manual_tasks.md, resolved 2026-08-28; containers resolved 2026-09-27)*
- [x] **Set `HF_TOKEN` in `.env`** — verified set (2026-08-28). *(docs/matrix_manual_tasks.md, resolved 2026-08-28)*

## Dropped

- ~~Experiment 9: Inkling-2-Mini (Thinking Machines)~~ (2026-11-27) — closed without running: the model is running on the M1 Mac Studio as the backup model (~30 tok/s, ~64k context ≈ 2×32k threads), so the GPU experiment is no longer needed. Compose file + profile deleted; 19 GB weights removed from `/home/chuck/data/models/inkling2-mini` (re-downloadable from HF). Research table kept in EXPERIMENTS_RESULTS.md (Experiment 9).
- ~~Rerun experiments 4 & 5~~ (2026-08-28) — Qwen3.6 W8A16 128K and Qwen-long W8A16 262K superseded by Qwen3.8 (current NVFP4 daily driver + Qwen3.8-Flash-Next candidate). *(EXPERIMENTS_RESULTS.md, Next Steps)*
- ~~Run experiment 6: Nemotron-3-Puzzle-75B NVFP4~~ (2026-09-27) — old model; Experiment 7 (Qwen3.8-Flash-Next 125B) is a far better next candidate. *(EXPERIMENTS_RESULTS.md, Experiment 6)*
- ~~Qwen3-Next-80B FP8 experiment~~ (2026-09-27) — old model, never run; Flash-Next supersedes it. *(EXPERIMENTS_RESULTS.md, Experiment 3)*
- ~~Consider W8A16 + MTP as a quality upgrade~~ (2026-09-27) — the W8A16 experiments were Qwen3.6 (dropped 2026-08-28); MTP is already in production on the NVFP4 daily driver (2 tokens, tuned 2026-08-25); no Qwen3.8 8-bit candidate identified — revisit only if 4-bit quality issues surface. *(EXPERIMENTS_RESULTS.md, Next Steps)*

## Done (this consolidation)

- [x] **Media pipeline job visibility (matrix, 2026-11-04)** — post-incident hardening for the 2026-11-02 film run (9 lost job IDs, ~1 h GPU + ~1 h recovery): `GET /jobs` (durable `jobs.jsonl` ledger + live registry, filterable, survives pipeline restarts), job-ID logging to the container log, structured 404 with hint. `improve_mediapipeline_todo.md` removed — consolidated into `docs/matrix_media_pipeline_api.md` (contract + §7.1 client guidance), `docs/thor_media_mcp_handoff.md` (thor T1–T5 spec), `docs/media_film_skill_spec.md` (Mac skill spec). Evidence: `docs/matrix_validation_log.md` (Run 2026-11-04).

- [x] **`media-mcp-client/` resync (2026-09-27)** — resolved by referencing the official repo instead of keeping a snapshot: the authoritative `mcp_media` implementation is [`mcp/servers/media`](https://github.com/choukalos/homelab-ai-harness/tree/main/mcp/servers/media) in `github.com/choukalos/homelab-ai-harness` (all 2026-09-25 updates checked in + pushed: tool renames, `media_job_result`/`media_put`/`media_fetch`, `non_blocking`, `{path, location}` shapes — 22 tools). The stale pre-2026-09-25 snapshot (`mcp_tools.py`, `media_pipeline_client.py`, `HANDOFF.md`) was deleted; `media-mcp-client/` is now a pointer doc. Contract: GitHub README + `docs/matrix_media_pipeline_api.md`. *(TODO.md, 2026-09-25 verification)*

- [x] **Experiment cleanup (2026-09-27)** — dropped experiments 3 (Qwen3-Next-80B FP8) & 6 (Nemotron-3-Puzzle-75B NVFP4) as old models; Experiment 7 (Flash-Next 125B) is the next candidate. Removed: 9 experiment compose files + 9 profiles (round-1 + Qwen3.8-round), 7 stopped experiment containers, ~88 GB re-downloadable weights (gemma-4-31b-it 59G, Qwen3.8-27B-FP8 29G, Qwen3-Next-80B 16M, 88plug W8A16 refs). Kept: Lorbus INT4 18G (live `qwen-long` mode), cyankiwi gemma-4-26B AWQ 17G (live Ollama), unsloth NVFP4 22G (daily driver). Results archived in EXPERIMENTS_RESULTS.md.
- [x] **Media MCP auth + `media_pull` path fix (thor, 2026-09-25)** — auth for the `mcp_media` tool updated (key = user, key passed to the pipeline); finished files exposed publicly via `siri.choukalos.com` / `choukalos.com/files`. `media_pull` now accepts `media_jobs/<job_id>/<file>` paths (verified: signed URL minted). `auth_todo.md` deleted post-completion. The 30 s MCP tool-call abort was fixed the same day (submit-and-poll on the `mcp_media` tool side — see Active).
- [x] **Media MCP client + TTS voice library (matrix + thor, 2026-09-25)** — Qwen-Image-2.1 client defaults + `model`/`references` pass-through, voice library (`trailer`/`default`/`narrator_f`/`deep_m`), and the three voice MCP tools (`media_list_voices` / `media_add_voice` / `media_delete_voice`) deployed on the MCP host and verified end-to-end (15 checks: gen/edit/legacy/image-refs, TTS by name + by path + unknown-voice 400, add/delete round-trip, freeze/assemble smoke, pull/info). Evidence: `docs/matrix_validation_log.md` (Run 2026-09-25); contract: `docs/matrix_media_pipeline_api.md` + the `mcp_media` GitHub README (MCP side — `mcp/servers/media` in the homelab-ai-harness repo; the local `HANDOFF.md` snapshot was removed 2026-09-27). Working docs `update_media_mcp_todo.md`, `voices_todo.md`, `voices_thor_handoff.md` deleted post-completion.
- [x] **Experiment system: manual testing** — the system has been exercised end-to-end in production: MTP experiment (2026-07-05), Qwen3.8-27B NVFP4 candidate round (2026-08-14 → 2026-08-24), promotion to `matrix-coder`, MTP 3→2 tuning + speed fix (2026-08-25, 123.95 tok/s). *(TODO.md, originally "Manual Testing")*
- [x] **Pre-git-commit cleanup** — `.gitignore` covers `__pycache__/` and `*.pyc`; zero tracked pyc files. *(TODO.md, originally "Pre-Git Commit Cleanup")*
- [x] **ComfyUI legacy model/workflow cleanup (2026-08-28)** — removed ~55 GB of obsolete models (SD1.5/SDXL/SVD checkpoints, old LTXV/SeedVR2 builds, dup XTTS dir), 6 legacy workflow JSONs, 4 obsolete custom nodes, and scratch/venv caches. All 25 pipeline models verified intact; pipeline + ComfyUI healthy. See `docs/matrix_comfyui_media_api.md` changelog.
- [x] **Fresh inventory snapshot (2026-08-28)** — appended to `docs/matrix_inventory.md` (append-only): current containers, Qwen3.8 vLLM config, media stack, model storage sizes, post-cleanup notes.