# Add Card Plan — RTX 4000 Blackwell (24 GB) → Matrix as GPU 1

> **Status: PLANNING** — card not yet installed. Plan only; nothing has been changed.
> Created 2026-11-13. Owners: **Chuck** (hardware actions, §5) · **agent** (phases, §6).
> Companion docs: `docs/matrix_inventory.md`, `EXPERIMENTS_RESULTS.md` (Exp 7/8),
> `media-pipeline/wan_shots_todo.md`, `docs/matrix_media_pipeline_api.md` §6.

## 1. Goal

Add a 24 GB RTX 4000 (Blackwell) to the 2nd PCIe slot so that:

- **GPU 0 (RTX PRO 5000, 72 GB)** → dedicated to the **daily LLM** (new candidate model
  or the current Qwen3.8-27B with more headroom). Serves `matrix-coder` on :8000 —
  that's what the agent stack (pi/codex via LiteLLM) and the main LLM workload run on.
- **GPU 1 (RTX 4000, 24 GB)** → dedicated to the **media pipeline** (full 24 GB) **and**
  an **Ollama copywriter** (Gemma 4 31B-class), with the two able to **swap in/out** of
  the card.
- `model-manager` (the mode-switch entry point — the plan's `homelab.sh`
  reference is this script, no separate wrapper) keeps working: experiments still
  run, a **`legacy-daily`** mode preserves today's single-card setup as the rollback
  target, and a new **`daily`** mode is the 2-GPU steady state.

## 2. Current state (verified 2026-11-13)

### VRAM ledger — single 72 GB card

| Tenant | VRAM | Notes |
|---|---|---|
| vLLM `qwen38` (daily driver) | ~55–56.8 GB | `unsloth/Qwen3.8-27B-NVFP4`, :8000, alias `qwen38-27b` = LiteLLM `matrix-coder` (the agent). `--max-model-len 196608`, `--max-num-seqs 3`, `--gpu-memory-utilization 0.75`, MTP 2, ~124 tok/s |
| Media pipeline (ComfyUI) | ~14 GB gate | 72 GB − vLLM. Wan 2.2 I2V A14B **GGUF Q5_K_M** dual-expert (480p only — 720p OOM'd by 2.3 MiB), Qwen-Image-2512 Q4_0, LTXV 2B, SeedVR2 3B fp8, MMAudio, ACE-Step, XTTS, transcribe (4 GiB gate). umt5-xxl fp8 text encoder on CPU. Headroom guard in `server.py` auto-restarts ComfyUI before music/tts |
| Ollama | 17 GB on demand | `gemma4:26b` MoE (copywriter, `matrix-gemma4-moe`), `qwen3.6:27b` (legacy), `nomic-embed-text`. `OLLAMA_KEEP_ALIVE=5m`, `MAX_LOADED_MODELS=2` |

**The ~14 GB slot is shared**: Ollama copywriter **XOR** media pipeline. That's the
constraint the new card removes.

### Mode machinery

- `scripts/model-manager` (bash): modes `daily / qwen-coder / qwen-long / llms /
  experiment / images` → profiles in `models/profiles/*.yaml`; state in `state/`
  (`current_mode`, `switch_log.md`); `experiment start` runs candidates on :8000 with
  the daily driver stopped.
- `compose/experiments/`: **Qwen3.8-Flash-Next experiment is set up, not yet run** —
  `qwen38-flash-next-gguf.yml` (Exp 8: `ukisai/Swift-1.5-Qwen3.8-Flash-Next` **IQ4_XS**
  97.7 GB GGUF, llama.cpp, 262K ctx, `--parallel 2`, k8v4 KV, serves as `qwen38-27b`
  on :8000) + `qwen38-flash-next-gsq-rco.yml` (Exp 8b: **IQ3_S GSQ-RCO** 83.7 GB
  fallback). Pre-download ~98.6 GB.
- 2026-10-31 budget analysis (in the Exp 8 header): with the media pipe sharing GPU 0,
  IQ4_XS is capped at **2 × 256K** sessions (RAM at the wall, 62/62 GB); **without the
  media pipe, 4 × 256K fits VRAM** (near-zero headroom → drop `--mmproj` or use 196608
  ctx for margin).
- `homelab.sh`: **resolved 2026-11-21 — it is `scripts/model-manager`** (Chuck:
  no separate wrapper script exists; the mode-switch entry point is the
  model-manager script itself). External call shape:
  `model-manager mode switch --yes <mode>` (verified in the Phase 3 drill).
- Disk: **896 GB free** (1.7 TB vol). RAM: 62 GiB. Driver 595.91.07 (already
  Blackwell-capable — the 5000 runs on it).
- GPU pinning: `comfyui.yml` / `gemma4-moe.yml` use `count: 1` (no `device_ids`) —
  today that's GPU 0 by default; post-install they need `device_ids: ['1']`.

## 3. Target architecture

| GPU | Tenant | VRAM |
|---|---|---|
| **GPU 0 — RTX PRO 5000 (72 GB)** | New daily model (Flash-Next llama.cpp **or** bumped Qwen3.8-27B vLLM). Serves `qwen38-27b` on :8000 → `matrix-coder` alias unchanged → **no Thor/LiteLLM changes** | ~57–70 GB |
| **GPU 1 — RTX 4000 (24 GB)** | Media pipeline (dedicated) **XOR-swappable with** Ollama copywriter (Gemma 4 31B / 26B MoE). Both pinned via `device_ids: ['1']` | ~19–24 GB peak |

### PCIe note (2-GPU config: x8, not x16)

The 5000 was linked at **PCIe 5.0 x16** pre-install (verified 2026-11-13:
`pcie.link.width.current = 16`). **Per the mobo manual, installing the 2nd card
drops BOTH cards to x8 (~31.5 GB/s vs ~63 GB/s)** — confirmed constraint, not an
open question. **Post-install verified 2026-11-21:** both cards negotiate **Gen5 x8
under load** (`pcie.link.gen.current=5, width=8` while GPU-busy). Idle, the cards
drop the link to **Gen1** (ASPM power management — the "1x speed" reading is the
idle *speed*, not the width; width is x8, the slot maximum). Nothing to fix:
x8 is the slot's wired maximum, and Gen5 x8 under load is the full negotiated
link. (Monitoring quirk, 2026-11-21: after a short load burst, `utilization.gpu`
can stick at 100 % while idle — power draw (≈5 W) shows the card is actually
idle; clears on context unload / reboot. Minor data-quality caveat for the
Grafana util panel.)

| Workload | Impact at x8 |
|---|---|
| GPU 1 media pipeline | **None** — dedicated 24 GB means everything resident; no host↔device streaming mid-inference. Model load is NVMe-bound (~0.4 s for 10 GB). Expert swap at the Wan midpoint loads from NVMe (disk-bound, not PCIe-bound); with Q5_K_M both experts (21.6 GB + VAE) even fit resident → swap is GPU-internal |
| GPU 1 Ollama copywriter | **None** — 31B Q4 (~19 GB) fully resident; PCIe only at load/unload |
| GPU 0 vLLM daily (27B) | **None** — weights + KV in VRAM; PCIe only at startup |
| GPU 0 Flash-Next (llama.cpp) | **The one real sensitivity** — ~34 GB weights RAM-resident (mmap); active experts + PLE rows cross PCIe per token. Two mitigating factors: (a) the 72 GB card holds ~63.5 GB on-GPU → only **~35% RAM-resident vs the anchor's ~75%** (24 GB GPU, 70 tok/s) — we push ~2× less traffic per token over the link; (b) the PLE table is designed for async row prefetch, which hides most latency when the per-token fetch is sparse. Halving the link (x16→x8) still *could* cost decode throughput if prefetch can't hide it — **the Phase 2 first benchmark measures this directly** (compare vs the 70 tok/s anchor, normalized for RAM-resident %). Unaffected fallback = bumped 27B vLLM (all-in-VRAM, x8-immune) |

Post-install sanity check (no decision impact): `nvidia-smi --query-gpu=index,pcie.link.gen.current,pcie.link.width.current` — expect x8 on both.

Steady-state properties:

- Agent + main LLM: no contention (GPU 0 is 100% theirs).
- Media jobs: no vLLM contention, no headroom-guard restarts for music/TTS, 24 GB
  budget (see §4.3).
- Experiments: full 72 GB (media no longer on GPU 0) — Flash-Next 4 × 256K becomes
  feasible.
- Rollback: `legacy-daily` mode = today's exact ledger (§2).

## 4. The three questions

### 4.1 model-manager + modes (legacy-daily / daily / experiment)

Design (implemented in Phase 4, no changes yet):

1. **`legacy-daily`** (new mode name for *today's* setup — the rollback target):
   - GPU 0: vLLM `qwen38` @ 0.75 (~56 GB) + 14 GB slot (Ollama XOR media, as now).
   - = profiles `matrix-coder` + `matrix-gemma4-moe` + `embeddings` + media on GPU 0.
2. **`daily`** (new steady state):
   - GPU 0: promoted daily model (Phase 2 winner) on :8000.
   - GPU 1: `media-gpu1` (comfyui_backend + media_pipeline, `device_ids: ['1']`) +
     `ollama-copywriter-gpu1` (`device_ids: ['1']`).
3. **`experiment`** (unchanged contract): candidate on :8000, daily driver stopped.
   Win: experiments get the **full 72 GB** (media moved to GPU 1) — this unblocks
   Flash-Next 4 × 256K.
4. **GPU 1 swap (media ⇄ copywriter)**: both pinned to GPU 1; they only conflict when
   the Wan model is resident (~10 GB post-shot) *and* the copywriter is loaded
   (17–19 GB). Resolution, in order:
   - Ollama `KEEP_ALIVE=5m` idle-unload (free, already configured);
   - extend the existing **VRAM headroom guard pattern** (from `server.py`) to the
     Ollama side: media pre-flight checks free VRAM; if the copywriter is resident and
     headroom < flow requirement → `ollama stop <copywriter>` (instant; weights stay
     on disk, next copy request reloads in ~10–30 s);
   - optional explicit `model-manager gpu1 swap {media|copywriter}` for manual swaps.
5. **model-manager** (the mode-switch entry point — the plan's `homelab.sh`
   reference; resolved 2026-11-21, §7.1): external callers keep working by
   calling `model-manager mode switch --yes {legacy-daily|daily|experiment}`.
   Call shape verified in the Phase 3 drill.

### 4.2 New daily model

Two candidates, both testable via the existing experiment machinery:

**(a) Qwen3.8-Flash-Next (Exp 8/8b — already set up, never run)**
- 125B MoE (6B active/token) + 51B PLE + 4B MTP, 262K native ctx, GDN+QSA hybrid
  attention (KV cheap: only 12 QSA layers carry per-token KV).
- Swift 1.5 IQ4_XS (97.7 GB) via llama.cpp; IQ3_S GSQ-RCO (83.7 GB) fallback.
- Benchmark vs current 27B (Qwen official): DeepSWE 58.7 vs 42.2, GPQA 91.7 vs 89.2,
  LiveCodeBench 91.9 vs 90.3 — strong upgrade if the 4-bit quant holds.
- **The new card is what unlocks it**: with media off GPU 0, 4 × 256K fits VRAM
  (today: 2 × 256K ceiling). RAM fits on 62 GiB (~50 GB total).
- Risks: llama.cpp's QSA sparse-attention support unverified (first benchmark tells —
  KV-read fallback would collapse the numbers); prefill serializes under concurrency
  (TTFT degrades with N); ~98.6 GB pre-download.

**(b) Qwen3.8-27B NVFP4 bumped (safe lane — same model, more headroom)**
- Media leaves GPU 0 → `--gpu-memory-utilization 0.75 → 0.95–0.98` (≈1 GB free at
  0.986; start 0.95, step up if stable — vLLM needs activation headroom).
- `--max-num-seqs 3 → 4–6` (the "concurrent threads" bump); `--max-model-len 196608 →
  262144` (256K). KV cache grows ~2.5× → far more concurrent 200K+ sessions.
- Zero model risk; instant; the rollback baseline is trivially the old args.

**Plan**: run (b) first as the safe `daily` (immediate win, no model change), then run
(a) as the experiment against it; benchmark both (throughput, TTFT, quality probes,
stress); promote the winner. `legacy-daily` stays as the rollback either way.

### 4.3 What a full 24 GB buys the media pipeline

Current 14 GB gate → 24 GB dedicated. Ranked by impact:

1. **Wan 2.2 at 720p (1280×720)** — the headline unlock. 720p OOM'd the 14 GB gate by
   2.3 MiB (needs ~16–17 GB) → fits 24 GB with margin. Native 720p shots instead of
   480p + SeedVR2 upscale. Re-run `qa_wan_phase1.py 720` on GPU 1 to confirm.
2. **Longer clips**: 97 f (~6.1 s) → 129 f (~8.1 s) (frames must be 4n+1) at 720p.
3. **Better quants**: Q5_K_M → **Q6_K** (~12.3 GB/expert) or **Q8_0/FP8** (~13.3–14.5
   GB/expert). Peak = 1 expert resident (ComfyUI swaps at the step-10 midpoint) + VAE
   + activations ≈ 19–20 GB < 24 GB → near-FP16 quality (Q5 was chosen *only* because
   FP8 "would eat the whole 14 GB budget").
4. **umt5-xxl text encoder on GPU** (fp8 ~5.6 GB, today on CPU) — faster encoding,
   frees CPU; still fits alongside a Q5/Q6 expert.
5. **Both experts resident** (Q5: 10.8 × 2 + VAE 0.25 ≈ 21.9 GB < 24) — removes the
   expert-swap churn at the schedule midpoint (modest speed win, simpler behavior).
6. **No more contention or guards**: ACE-Step (6 GB) + XTTS (2 GB) can run *alongside*
   a resident Wan model (10.2 + 6 + 2 ≈ 18.2 GB < 24) → music/TTS in parallel with
   video gen → faster end-to-end assembly; the comfyui auto-restart guard becomes a
   no-op (kept, adapted for the Ollama-resident case from §4.1.4).
7. **Sharper keyframes**: Qwen-Image-2512 Q4_0 → Q6/fp8 (~15–16 GB) or higher-res
   output → better Wan conditioning (keyframe quality is the I2V ceiling).
8. **LTXV fast lane at 720p** for quick drafts (today 768×512).
9. **Speed caveat**: the 4000 has less memory bandwidth than the 5000, so single-clip
   wall-time is comparable or slightly slower — but zero contention + the quality
   unlocks (1–3) are the real win.

Disk note: media models already live in `/home/chuck/data/comfyui` (GPU-agnostic) —
**no re-downloads**; only the new quants (~+15–30 GB).

**Copywriter on 24 GB**: Gemma 4 31B @ Q4 ≈ 18–19 GB → fits with ~4–5 GB for context
(8–16K ctx comfortable, 32K tight with q8 KV); the current 26B MoE (17 GB) fits with
more headroom and is faster. Either works; see §7.2.

## 5. Your actions (hardware — in order)

1. **Confirm the SKU**: exact model (RTX PRO 4000 Blackwell 24 GB?) → power connector
   type + TDP. The 5000 already draws 300 W/300 W — check PSU headroom for
   300 W + the 4000's draw under simultaneous load.
2. **Check the slot**: 2nd PCIe slot present, x16 or x8 (either is fine for this
   workload), enabled in BIOS, physical clearance/airflow.
3. **Install**: power off, seat the card, connect power, boot.
4. **Verify**: `nvidia-smi -L` shows 2 GPUs; `nvidia-smi` shows GPU 1 with 24 GB;
   no Xid errors in `dmesg`. Driver 595.91.07 is already Blackwell-capable — **no
   driver change expected**; if the card misbehaves, a driver bump is your call
   (Matrix boundary: no changes without your approval).
5. **Ping me** → I start Phase 1.

## 6. My phases (agent — card in since 2026-11-14; Phases 1–4 done)

> **Ordering (decided 2026-11-13):** card in → **metrics** → **modes/ops** (both get
> us back to parity) → **ramp up the daily** → **QNF experiment** (swap 27B → QNF as
> daily) → **flex the media pipeline**. Phase 0b's x16 baseline was **already
captured 2026-11-14** (the Experiment 8 rerun, pre-install — see Phase 0b below);
> the remaining 0b question (QSA at long context) folds into the Phase 5 x8 run.

### Phase 0a — prep (pre-install, safe, no approval needed, no disruption)
- [x] This plan (done) + answers locked in (§4).
- [x] Pre-download Flash-Next GGUF (IQ4_XS ~98.6 GB + mmproj) — **already on disk**
      (Oct 31); byte-verified vs remote 2026-11-13 (3 shards + mmproj exact match).
- [x] Pre-pull copywriter `gemma4:31b` (20 GB) to Ollama — pulled 2026-11-13
      (`state/ollama_pull_gemma4_31b.log`); 26B MoE kept as the fast option.
- [x] Drafted (as files, not applied): `compose/comfyui-gpu1.yml`,
      `compose/ollama-gpu1.yml`, `compose/qwen-coder-bumped.yml`, profiles
      `matrix-coder-flashnext.yaml` / `media-gpu1.yaml` / `ollama-copywriter-gpu1.yaml`,
      `models/modes-2gpu-draft.md`. Validated: compose dry-run (w/ `--profile image`),
      YAML parse, `device_ids: ['1']` pin confirmed in rendered config.
- [x] Benchmark harness `scripts/daily_candidate_bench.sh` (QSA context-scaling probe
      + N×256K concurrency + PCIe link-width capture); syntax-checked.

**Status: COMPLETE (2026-11-13).** No running service changed. Optional follow-up:
pre-pull the IQ3_S GSQ-RCO fallback (83.7 GB) if desired (else on-demand in Phase 5).

### Phase 0b — x16 Flash-Next baseline — **RESOLVED 2026-11-21 (baseline already captured)**
> The 5000 was **x16 pre-install and x8 after**. A pre-install Flash-Next run was the
> **only** chance to capture a clean x16 baseline, so Phase 5 can attribute any
> throughput drop to PCIe x8 with confidence instead of estimating.

**Chuck's recollection confirmed 2026-11-21:** the x16 baseline **is the Experiment 8
valid rerun (2026-11-14 00:04–00:18 UTC)** — it ran *before* the 4000 was added.
Evidence (journalctl boot history):
- Boot `554aac23` (11-13 13:56 → 11-14 01:29, the experiment's boot): kernel
  enumerated only `02:00.0` (the 5000); `03:00.0` (the 4000) **absent**; boot
  script reported a single GPU. 5000 verified x16 on 11-13 (plan v0.2) — same boot.
- Reboot 11-14 01:30 (boot `dff75b4e`): boot script still reported only GPU 0 — the
  4000 was added **after** that boot (hot-plug; the slots are `HotPlug+ Surprise+`),
  before the 04:10 Phase 1 pinning. No reboot in between (boot ran 7 days).

**The x16 baseline numbers (Experiment 8, `EXPERIMENTS_RESULTS.md`):**
Flash-Next IQ4_XS @ x16, single-card system — **291.4 tok/s** (512 tok × 20 runs,
275.7–298.4), TTFT 47.4/55.0 ms, stress 22.0 req/s @ 4 concurrent (80/80 ok),
quality 4/5 solid. → Phase 5's x8 delta is measured against **291.4 tok/s**.

- [x] Stop the daily driver, run Flash-Next at x16, benchmark (tok/s, TTFT,
      quality probe), restore driver. — **done 2026-11-14 00:04–00:18 UTC** (the
      Experiment 8 rerun; switch_log entries 00:04:27 / 00:18:04).
- [x] Record the x16 baseline into `EXPERIMENTS_RESULTS.md` for the Phase 5 delta.
      — **labeled 2026-11-21** (Experiment 8 section now carries the Phase 0b
      baseline designation + the boot-journal evidence).
- [ ] **QSA sparse-attention behavior at long context (2×256K)** — the remaining
      0b question. Link-width-independent (software question), so it folds into the
      **Phase 5 x8 run** instead of needing another pre-install window. Same for the
      fixed long-context quality rerun (harness `reasoning_content` fix).

**Learn (the high-value stuff):** (1) does llama.cpp implement QSA sparse attention
(a dense-256K fallback collapses the numbers — find out *before* install); (2) the
x16 baseline; (3) Flash-Next's real quality vs the 27B.
**Cost:** stops the daily driver → **agent offline ~1–2 h**. Chuck schedules the window.

**Gate:** if QSA is broken or quality disappoints → pivot to the bumped 27B (or another
candidate) *before* install. If good → proceed with confidence.

### Phase 1 — card in, media + copywriter onto GPU 1 — **DONE 2026-11-14**
- [x] Verify 2-GPU visibility (nvidia-smi, docker `--gpus`, `device_ids: ['1']`).
      Both GPUs visible; in-container checks: `comfyui_backend` + `ollama` see only
      the 4000, `qwen38` only the 5000. GPU 1 idles at PCIe Gen1 (ASPM), Gen5 x8 max.
- [x] Pin media stack to GPU 1 (comfyui_backend + media_pipeline), run
      `qa_wan_phase1.py 480` on GPU 1 → must match GPU 0 results.
      **PASS** — success, peak 15,961 MiB (7.5 GB headroom), output specs identical
      to the GPU 0 clip; wall 1055 s vs 615 s (1.7×, 4000 bandwidth — expected).
      Compose promoted: `compose/comfyui.yml` = GPU 1 variant; GPU 0 variant archived
      at `compose/legacy/compose.comfyui-gpu0.yml` (legacy-daily, Phase 3).
- [x] Pin Ollama to GPU 1, verify copywriter + `nomic-embed-text` (embeddings must
      keep working for RAG).
      **PASS** — `compose/gemma4-moe.yml` = GPU 1 variant (legacy: `compose/legacy/
      compose.gemma4-moe-gpu0.yml`). gemma4:31b: 46 s load, 22,021 MiB @ 32K ctx,
      ~30 tok/s, reasoning model (short budgets → text lands in `thinking`).
      nomic-embed-text: 768-dim vectors OK.
- [x] Confirm GPU 0 steady state unchanged while media runs on GPU 1 (no cross-talk).
      vLLM 56,158 MiB steady through the 17.6-min GPU 1 run; ~92 tok/s probe; :8000
      healthy. `qa_wan_phase1.py` gained `--gpu N` (per-GPU sampling, default 1).

### Phase 2 — metrics / observability (right after Phase 1 — parity)
- [x] dcgm-exporter multi-GPU check (it was frozen on the old driver — re-verify);
      both GPUs export per-index.
      **PASS** — full DCGM field set (util, FB_USED/FREE, power, temp, clocks, XID)
      exported per `gpu` index for both cards; live values.
- [x] Thor Prometheus/Grafana 2-GPU panels: daily-on-GPU0 / media-on-GPU1 split visible.
      **DONE** — exporter side PASS (Thor Prometheus target `matrix:9400` UP;
      `DCGM_FI_DEV_GPU_UTIL` returns series for `gpu="0"` and `gpu="1"`); **Grafana
      2-GPU panels imported on Thor 2026-11-21 (Chuck)** per the handoff in
      `docs/thor_2gpu_grafana_handoff.md` + dashboard JSON.
- [x] **Gate: monitoring parity** — we can see both GPUs before any model change, so
      every later phase is observable. (Exporter + Prometheus verified; Grafana
      panels are a convenience layer, not a blocker.)

### Phase 3 — modes + ops (right after metrics — parity) — **DONE 2026-11-21**
- [x] model-manager: add `legacy-daily` + new `daily` modes, `gpu1 swap` command,
      profile validation for GPU 1 pinning. (Plus: `gpu1 status`, preflight made
      2-GPU aware — both-GPUs-visible + per-GPU VRAM budget w/ GPU 1 XOR semantics.
      Pre-existing `--timeout` compose-flag bug fixed — it was silently no-op'ing
      every ComfyUI stop; caught by the drill.)
- [x] Locate + integrate `homelab.sh` (§7.1). (**Resolved 2026-11-21: it is
      `scripts/model-manager` itself — no separate wrapper script (Chuck).**
      Call shape `model-manager mode switch --yes
      {legacy-daily|daily|experiment}` verified working for all three modes.)
- [x] Rollback drill: `legacy-daily` switch + back, timed, logged (`state/phase3_drill_20261121.log`).
      vLLM `qwen38` untouched throughout (container `State.StartedAt` identical
      pre/post; :8000 served `qwen38-27b` the whole time). Media stack down ~2 min
      during the drill (expected — legacy-daily has no media tenant), restored via
      `model-manager media up` (10 s). `gpu1 swap` cycle verified both directions.
- [x] Update `docs/matrix_inventory.md` (append-only snapshot) + `EXPERIMENTS_RESULTS.md`.
- [x] **Gate: ops parity** — mode switching + rollback work before any model change.

### Phase 4 — ramp up the daily (GPU 0) — **DONE 2026-11-21**
- [x] Bumped 27B vLLM as the working `daily` (**x8-immune** — all-in-VRAM):
      mem-util 0.75 → **0.95**, max-num-seqs 3 → **5** (user direction:
      concurrent seqs > context length), max-model-len 196608 → **262144**
      (256K — "okay" per user; 192K was the good baseline). The 0.75 config is
      the PARTIAL config that left ~14 GB on GPU 0 for the small media pipeline
      (queued, pre-2-GPU) — archived as rollback:
      `compose/legacy/compose.qwen-coder-075-14gb-media.yml`. The
      `qwen-coder-bumped.yml` draft was promoted to the canonical
      `compose/qwen-coder.yml` (profile `matrix-coder.yaml` updated to match).
- [x] **Gate: agent healthy on the bumped 27B — PASSED 2026-11-21.**
      Startup clean (CUDA graphs captured, no OOM); KV pool **42.47 GiB =
      1,218,373 tokens** (4.65× at full 256K; 20–60× at typical 30–60K agent
      contexts — the pool, not max-num-seqs, is the real concurrency limit).
      Tool call PASS (0.6 s); same-methodology benchmark (completions,
      streaming, 512 tok × 10): **136.74 tok/s mean (130.25–141.78) vs the
      123.95 baseline** (benchmark 20260825_222844; results
      `data/benchmarks/results/20261121_165352`) — +10.3%. Idle verified
      (16.8 W / 56 °C, no preemption warnings, prefix cache ~88%). The
      0.97/seqs-6 step was evaluated and **held**: +~4% KV for free headroom
      dropping ~2 GB → ~0.6 GB (below the ~1 GB safety target); revisit only
      if 5 concurrent sessions queue. Agent-down window: qwen38 recreated
      16:47 UTC (the container serving the agent — expected disruption, ~4 min
      to healthy).

### Phase 5 — QNF experiment (swap 27B → QNF as daily)
> Depends on the Phase 0b x16 baseline — **captured 2026-11-14 (291.4 tok/s @ x16,
> see Phase 0b)**; the QSA/long-context question folds into this run.
- [ ] Flash-Next experiment (IQ4_XS; IQ3_S fallback) at 2 × 256K → 4 × 256K **at x8**;
      **compare directly to the Phase 0b x16 baseline** → the x8 delta is measured,
      not estimated. Full benchmark vs the bumped 27B.
- [ ] Decision: promote QNF to `daily` (profile + mode + LiteLLM alias unchanged) or
      keep the bumped 27B. Archive the loser to `state/experiment_archive.md`.
- [ ] **Gate: daily model settled** — before flexing the media pipeline, so media work
      happens against a stable daily.

### Phase 6 — flex the media pipeline (GPU 1 quality unlocks)
- [ ] Re-run the 720p gate on 24 GB (`qa_wan_phase1.py 720`); if green, lift the
      480p-only API gate (new `WAN_RESOLUTIONS` + metering note).
- [ ] Try Q6_K / Q8_0 / FP8 Wan experts + T5-on-GPU + both-experts-resident; pick the
      best quality/VRAM fit at 720p.
- [ ] Adapt the VRAM headroom guard for the Ollama-resident case (§4.1.4).
- [ ] Update `docs/matrix_media_pipeline_api.md` §6 (VRAM budget), metering labels,
      MCP tool docs (720p option).
- [ ] Backup notes: new model downloads (Q6/Q8/FP8 quants) covered by the existing
      backup chain?

## 7. Open questions (need your answers)

1. ~~**Where does `homelab.sh` live?**~~ — **settled 2026-11-21 (Chuck): it is
   `scripts/model-manager`** — no separate wrapper script exists; the mode-switch
   entry point is the model-manager script itself. Call shape
   `model-manager mode switch --yes <mode>` verified in the Phase 3 drill.
2. ~~**Copywriter model**: Gemma 4 31B vs 26B MoE? Context budget?~~ — **settled
   2026-11-21 (Chuck): 31B confirmed; context flexed 32K → 64K** (measured: 64K ≈
   22.1 GB total on the 24 GB card, ~2.3 GB headroom — Gemma 4's KV is tiny,
   ~4 MiB/1K tokens; 128K verified loadable as the ceiling). 26B MoE kept as the
   fast option.
3. ~~**Exact 4000 SKU** (for the PSU/connector check in §5.1)?~~ — **settled
   2026-11-21 (Chuck): RTX PRO 4000 Blackwell 24 GB, on the 600 W power cable.**
   Card TDP 145 W (measured power limit) — ample margin. PCIe: Gen5 x8 under load
   (slot max), Gen1 at idle (ASPM) — see the §3 PCIe note.
4. ~~**Which card ends up on x8**~~ — **settled 2026-11-13: both drop to x8** (mobo manual). No impact on media/copywriter/vLLM; Flash-Next risk row in §8 carries the consequence
5. ~~**Keep `qwen3.6:27b` in Ollama** (17 GB disk) or drop it once the copywriter
   moves to GPU 1?~~ — **settled 2026-11-21 (Chuck): dropped.** `ollama rm
   qwen3.6:27b` done (16 GB disk freed; no compose/profile/model-manager references
   — docs-only). Ollama now: gemma4:31b (20 GB), gemma4:26b (17 GB),
   nomic-embed-text (274 MB).

## 8. Risks & rollback

| Risk | Mitigation |
|---|---|
| Flash-Next QSA fallback in llama.cpp (KV-read collapse) | First benchmark gates it; fallback = IQ3_S (8b) or stay on bumped 27B; `legacy-daily` is the full rollback |
| Flash-Next throughput loss at PCIe x8 (RAM-resident MoE weights; both cards x8 per mobo manual) | 72 GB card holds ~63.5 GB on-GPU → only ~35% RAM-resident (anchor: ~75%, 70 tok/s); first benchmark measures x8 directly vs anchor; mitigations: more GPU-resident layers (lower ctx/parallel), IQ3_S tier, or keep bumped 27B as daily (x8-immune) |
| 24 GB too tight for a quality combo (720p + Q8 + T5-on-GPU simultaneously) | Per-flow VRAM table (as in `wan_shots_todo.md` Phase 1); pick the best fit that passes QA |
| Ollama + media VRAM conflict on GPU 1 | keep_alive idle-unload + extended headroom guard (`ollama stop`) + explicit `gpu1 swap` |
| Driver/PCIe issues on the new card | Your call per Matrix boundaries; `legacy-daily` works with the card idle (or removed) |
| Agent (matrix-coder) disruption during Phase 4/5 (daily model changes) | Bumped 27B first (same model, Phase 4); QNF only after it passes (Phase 5); alias `qwen38-27b` on :8000 unchanged throughout → no Thor changes |

**Rollback (any time):** `model-manager mode switch legacy-daily` → today's exact
single-card ledger (§2). Nothing in this plan deletes models or state.

## 9. Change log

- 2026-11-13: v0.1 — plan drafted (card not installed; no changes made). Grounded in
  live state: nvidia-smi, `compose/*`, `models/profiles/*`, `state/*`,
  `EXPERIMENTS_RESULTS.md` Exp 7/8, `docs/matrix_media_pipeline_api.md` §6.
- 2026-11-13: v0.2 — PCIe x8 assessment added (5000 currently x16, verified;
  x8 = no impact on media/copywriter/vLLM, watch-item for Flash-Next).
- 2026-11-13: v0.3 — both cards confirmed x8 (mobo manual): PCIe note updated
  (settled constraint, mitigating factors for Flash-Next), open question 4 closed,
  risk row sharpened.
- 2026-11-13: v0.4 — Phase 0 split into **0a** (safe pre-install prep) + **0b**
  (optional x16 Flash-Next baseline, needs an agent-down window). 0b rationale:
  the 5000 is x16 today / x8 post-install, so a pre-install run is the only clean
  x16 baseline and the early QSA probe.
- 2026-11-13: v0.5 — **reordered phases** per Chuck: card in (1) → metrics (2) →
  modes/ops (3) → ramp daily (4) → QNF experiment (5) → flex media (6). Metrics +
  modes/ops moved up to restore parity first; QNF experiment runs before the media
  flex so media work targets a stable daily. **0b on hold until ~Friday evening.**
  0a approved to start now.
- 2026-11-13: v0.6 — **Phase 0a COMPLETE**: Flash-Next IQ4_XS byte-verified (already
  on disk), `gemma4:31b` (20 GB) pulled, GPU1 compose variants + 3 profiles +
  `qwen-coder-bumped.yml` + `modes-2gpu-draft.md` + `daily_candidate_bench.sh`
  drafted and validated (compose dry-run w/ `--profile image`, YAML parse,
  `device_ids: ['1']` pin confirmed). No running service changed.
- 2026-11-14: v0.7 — **Phase 1 + Phase 2 complete.** Card installed (RTX PRO 4000
  Blackwell 24 GB = GPU 1, both cards x8 as predicted). Media stack + Ollama pinned
  to GPU 1 via `device_ids: ['1']` (compose promoted; GPU 0 variants archived in
  `compose/legacy/` for legacy-daily). QA 480 on GPU 1 PASS (peak 15.9 GB, output
  parity with GPU 0; wall 1055 s vs 615 s). gemma4:31b + nomic-embed-text verified
  on GPU 1. vLLM steady state on GPU 0 unchanged. dcgm-exporter per-GPU export
  re-verified (driver 595.91.07); Thor Prometheus shows both GPUs. Grafana 2-GPU
  panels = Thor-side handoff (spec in inventory). `qa_wan_phase1.py` gained `--gpu`.
  Docs: `docs/matrix_inventory.md` 2026-11-14 snapshot, `wan_shots_todo.md` GPU 1
  parity section, `state/switch_log.md`.
- 2026-11-21: v0.8 — **Phase 3 complete (ops-parity gate passed).** `model-manager`
  gained `legacy-daily` + redefined 2-GPU `daily` modes, `gpu1 {status|swap
  <media|copywriter>}`, and `device_ids` pin validation in `profile validate`
  (FAIL on a mis-pin). `cmd_mode_switch` treats the `media-pipeline` backend as the
  ComfyUI family (so `daily` keeps the running media stack). `preflight.sh` is
  2-GPU aware: both-GPUs-visible check for `gpu: 1` modes + per-GPU VRAM budget
  (GPU 0 additive; GPU 1 XOR tenants checked individually vs the 24 GB card).
  Pre-existing bug fixed: `docker compose … --profile image --timeout 30 down`
  (flag position) silently no-op'd every ComfyUI stop under Compose v5 — caught by
  the drill. Rollback drill (qwen-coder → legacy-daily → qwen-coder, timed + logged,
  `state/phase3_drill_20261121.log`): vLLM `qwen38` container untouched (StartedAt
  identical), :8000 served `qwen38-27b` throughout, media down ~2 min then restored
  in 10 s; `gpu1 swap` verified both directions. `homelab.sh` locate still open
  (§7.1 — not on matrix); its call shape is verified working. Docs:
  `modes-2gpu-draft.md` → APPLIED, inventory 2026-11-21 snapshot.
- 2026-11-21: v0.9 — **open question §7.1 settled (Chuck): `homelab.sh` is
  `scripts/model-manager`** — no separate wrapper script exists; the mode-switch
  entry point is the model-manager script itself. All plan/doc references updated
  (goal §1, current state §2, §4.1, Phase 3 item, §7.1); the script header now
  states the external call shape (`model-manager mode switch --yes <MODE>`) and
  lists the `gpu1` command (was missing from the header).
- 2026-11-21: v1.0 — **Phase 0b resolved + open questions 2/3/5 settled (Chuck).**
  (a) **The x16 Flash-Next baseline was already captured**: the Experiment 8 valid
  rerun (2026-11-14 00:04–00:18 UTC) ran *before* the 4000 was added — proven by the
  boot journal (the experiment's boot enumerated only the 5000; the 4000 appeared
  after the 01:30 reboot via hot-plug, before the 04:10 Phase 1 pinning). Baseline:
  291.4 tok/s @ x16, TTFT 47.4/55.0 ms, stress 22.0 req/s, quality 4/5. The QSA
  long-context question (link-width-independent) folds into the Phase 5 x8 run.
  (b) **Copywriter: 31B confirmed, context flexed 32K → 64K** — measured 22,021/22,157
  MiB @ 32K/64K on the 24 GB card (Gemma 4 KV ≈ 4 MiB/1K tokens); 128K verified
  loadable (22,125 MiB). Profile + compose updated. (c) **4000 SKU: RTX PRO 4000
  Blackwell 24 GB on the 600 W cable** (TDP 145 W — ample margin). PCIe answer:
  Gen5 x8 under load (slot max), Gen1 at idle = ASPM (the "1x" reading is idle
  speed, not width); noted the util-sticks-at-100 %-while-idle monitoring quirk.
  (d) **`qwen3.6:27b` dropped from Ollama** (16 GB disk freed; docs-only refs).
  (e) **Grafana 2-GPU panels imported on Thor (Chuck)** — Phase 2 fully closed.
- 2026-11-21: v1.1 — **Phase 4 complete (gate passed).** Bumped 27B daily live:
  mem-util 0.75 → 0.95, max-num-seqs 3 → 5 (user: concurrent seqs > context),
  max-model-len 196608 → 262144 (256K). The 0.75 config — the PARTIAL config
  that left ~14 GB on GPU 0 for the small queued media pipeline (pre-2-GPU)
  — archived as `compose/legacy/compose.qwen-coder-075-14gb-media.yml` (rollback
  path if the bumped daily misbehaves or media needs GPU 0 again). Draft
  `qwen-coder-bumped.yml` promoted to canonical `compose/qwen-coder.yml` +
  `matrix-coder.yaml` profile. Gate: startup clean (KV pool 42.47 GiB =
  1,218,373 tokens; 4.65× at full 256K), tool call PASS, benchmark 136.74 tok/s
  mean vs 123.95 baseline (+10.3%, `data/benchmarks/results/20261121_165352`),
  idle 16.8 W / 56 °C, no preemptions. 0.97/seqs-6 step evaluated + held
  (headroom ~2 GB → ~0.6 GB, below the ~1 GB target). Agent-down window used:
  qwen38 recreated 16:47 UTC, ~4 min to healthy. Phase 5 (QNF) is next — it
  compares against this config.
- 2026-11-21: **Phase 4 closed out — rollback path verified + baseline
  updated.** `matrix-coder-partial` profile (0.75/seqs3/192K, same model +
  served name) wired into `legacy-daily` (model-manager + preflight) —
  one-command rollback, transparent to clients. Phase 4 drill
  (`state/phase4_drill_20261121.log`): qwen-coder → legacy-daily 187 s
  (partial verified: max_model_len 196608, GPU 0 56.2 GB, gemma4:26b loaded),
  → qwen-coder 147 s (bumped verified: 262144, GPU 0 70.8 GB), media stack
  restored ~15 s. Baseline promoted (`benchmark.sh --update-baseline`,
  `data/benchmarks/baseline/summary.json`): TTFT 28.8 ms (was 35.5), 136.47
  tok/s (was 123.95), stress 4-concurrent 36.7 req/s (was 23.9 — **+54 %**).
  `EXPERIMENTS_RESULTS.md` + `docs/matrix_runtime_modes.md` refreshed for the
  2-GPU era.
