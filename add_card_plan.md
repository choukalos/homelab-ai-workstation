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

The 5000 is currently linked at **PCIe 5.0 x16** (verified 2026-11-13:
`pcie.link.width.current = 16`). **Per the mobo manual, installing the 2nd card
drops BOTH cards to x8 (~31.5 GB/s vs ~63 GB/s)** — confirmed constraint, not an
open question.

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

## 6. My phases (agent — card in since 2026-11-14; Phases 1–3 done)

> **Ordering (decided 2026-11-13):** card in → **metrics** → **modes/ops** (both get
> us back to parity) → **ramp up the daily** → **QNF experiment** (swap 27B → QNF as
> daily) → **flex the media pipeline**. Phase 0b is **on hold until ~Friday evening**
> (Chuck's availability) and is the prerequisite for the QNF experiment's x8 delta.

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

### Phase 0b — x16 Flash-Next baseline (pre-install, ON HOLD until ~Friday evening)
> The 5000 is **x16 today and x8 after install**. A pre-install Flash-Next run is the
> **only** chance to capture a clean x16 baseline, so Phase 5 can attribute any
> throughput drop to PCIe x8 with confidence instead of estimating.
- [ ] Stop the daily driver (`qwen-coder`), run Flash-Next at x16, benchmark
      (tok/s, TTFT, **QSA sparse-attention behavior**, quality probe), restore driver.
- [ ] Record the x16 baseline into `EXPERIMENTS_RESULTS.md` for the Phase 5 delta.

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
- [ ] Thor Prometheus/Grafana 2-GPU panels: daily-on-GPU0 / media-on-GPU1 split visible.
      **Exporter side PASS** — Thor Prometheus target `matrix:9400` UP; `DCGM_FI_DEV_GPU_UTIL`
      returns series for `gpu="0"` and `gpu="1"`. **Grafana panels pending (Thor-side,
      Chuck)** — Grafana :3000 not reachable from Matrix; panel spec + suggested
      queries in `docs/matrix_inventory.md` (2026-11-14 snapshot).
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

### Phase 4 — ramp up the daily (GPU 0)
- [ ] Bumped 27B vLLM (mem-util 0.95→0.98, seqs 4–6, ctx 262144) as the working
      `daily` (**x8-immune** — all-in-VRAM); verify `matrix-coder` (the agent) is
      healthy on it.
- [ ] **Gate: agent healthy on the bumped 27B** — the safe daily the QNF experiment
      compares against.

### Phase 5 — QNF experiment (swap 27B → QNF as daily)
> Depends on the Phase 0b x16 baseline (on hold until ~Friday evening).
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
2. **Copywriter model**: Gemma 4 31B (dense, ~19 GB Q4, higher quality ceiling) vs
   26B MoE (17 GB, faster)? And what context budget do you want (8K / 16K / 32K)?
3. **Exact 4000 SKU** (for the PSU/connector check in §5.1)?
4. ~~**Which card ends up on x8**~~ — **settled 2026-11-13: both drop to x8** (mobo manual). No impact on media/copywriter/vLLM; Flash-Next risk row in §8 carries the consequence
5. **Keep `qwen3.6:27b` in Ollama** (17 GB disk) or drop it once the copywriter moves
   to GPU 1?

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
