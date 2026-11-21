# Phase 6 — Wan quant experiments + headroom guard + docs — TASK

> Created: 2026-11-21 · Status: **IN PROGRESS** (pi long task)
> Parent: `add_card_plan.md` Phase 6 — items 2–5 (item 1 — 720p gate + 480p cap
> lift — DONE 2026-11-21, commit `abef852`).
> **This file is the compact task doc — self-contained, survives context
> compaction.** Read this top-to-bottom to resume.

## State snapshot (2026-11-21)

- **720p gate PASSED**: peak **19,641 MiB** on the 24 GB GPU 1 card (4.8 GB
  headroom), 57 min wall incl. model load. ComfyUI auto-offloads ~1.8 GB of
  weights to CPU at 720p (why it fits; why ~55 min/clip vs ~10 min at 480p).
- **480p cap lifted**: `WAN_RESOLUTIONS = {(832, 480), (1280, 720)}`
  (workflows.py:127) + `/shots` 400 message (server.py). Pipeline rebuilt +
  verified live.
- **Baseline reference (quality)**: `wan22_p1_1280x720_00001.mp4` (Q5_K_M,
  720p, 2026-11-21) in ComfyUI output — compare candidate quants against it.
- **Current Wan models**: `Wan2.2-I2V-A14B-{HighNoise,LowNoise}-Q5_K_M.gguf`
  (10.79 GB each) in `/home/chuck/data/comfyui/basedir/models/diffusion_models/`
  (container path `/basedir/models/diffusion_models/`). Text encoder
  `umt5_xxl_fp8_e4m3fn_scaled.safetensors` (6.74 GB) in `text_encoders/` —
  runs on **CPU** (`"device": "cpu"` in the CLIPLoader node, workflows.py:170).
- **GPU 1**: 24 GB; ~9.5 GB resident after a wan job; ~1 GB idle.
  **GPU 0**: vLLM daily (bumped 27B, 0.95) — untouched by all Phase 6 work.
- **Disk**: ~830 GB free (downloads are fine).
- **Pipeline image**: `media-pipeline:latest` (rebuilt 2026-11-21 for the cap
  lift). Rebuild: `./scripts/model-manager rebuild media-pipeline`.
- **Cancel a running ComfyUI prompt** (this fork's API — the old
  `/prompt/{id}/cancel` is GONE): `POST http://localhost:8188/api/jobs/{id}/cancel`.
- **Queue state**: `curl -s http://localhost:8188/queue` → `queue_running`
  (not `running` — the old key). Pipeline: `GET :8189/health` → `queue.counts`.

## Item 2 — quant experiments (Q6_K / Q8_0 / FP8 + T5-on-GPU + both-experts-resident)

Goal: pick the best quality/VRAM fit **at 720p on the 24 GB card**.

### 2a. Downloads (long pole — run in background, log to `state/`)

| File | Source (HF) | Size (approx) |
|---|---|---|
| `Wan2.2-I2V-A14B-HighNoise-Q6_K.gguf` | QuantStack (same repo as the Q5_K_M pair) | ~11.5 GB |
| `Wan2.2-I2V-A14B-LowNoise-Q6_K.gguf` | ditto | ~11.5 GB |
| `Wan2.2-I2V-A14B-HighNoise-Q8_0.gguf` | ditto | ~14.5 GB |
| `Wan2.2-I2V-A14B-LowNoise-Q8_0.gguf` | ditto | ~14.5 GB |
| FP8 experts (highnoise + lownoise) | Comfy-Org (fp8 split) | ~13.3 GB each |

- Target dir: `/home/chuck/data/comfyui/basedir/models/diffusion_models/`
  (FP8 safetensors go in the same dir; `UnetLoaderGGUF` / `UNETLoader` both
  scan it).
- Verify repo/file names via the HF API first
  (`https://huggingface.co/api/models/<org>/<repo>` + `/tree/main`) — the
  Q5_K_M pair came from **QuantStack** (wan_shots_todo.md §3), FP8 from
  **Comfy-Org**.
- After each download: `curl -s http://localhost:8188/object_info/UnetLoaderGGUF`
  (GGUFs) / `UNETLoader` (FP8) must list the new file.

### 2b. Make the Wan model files env-overridable (testability, no rebuild per test)

- workflows.py:123-124: `WAN_HIGHNOISE` / `WAN_LOWNOISE` are hardcoded
  filenames. Make them env-overridable:
  `os.environ.get("WAN_HIGHNOISE_MODEL", "Wan2.2-I2V-A14B-HighNoise-Q5_K_M.gguf")`
  (same for low-noise). FP8 experts need a loader switch: the workflow uses
  `UnetLoaderGGUF` (nodes 1, 2) — for FP8 safetensors the node must become
  `UNETLoader`. Add a `WAN_UNET_LOADER` env (default `gguf`) and branch the
  node class_type in `wan22_i2v`.
- Set the env on the `media-pipeline` service in `compose/comfyui.yml`
  (commented out by default = Q5_K_M GGUF).
- Rebuild once after this change; then switching quants = env change +
  `docker compose ... up -d` (no rebuild).

### 2c. Per-candidate test protocol (each ~1 h at 720p)

0. **Pre-flight: GPU 1 must be clean** — `nvidia-smi -i 1` should show < ~1 GB.
   The Ollama copywriter (KEEP_ALIVE=5m) can be resident for up to 5 min
   after the last copy request: `docker exec ollama ollama ps` + `ollama stop
   gemma4:31b` if needed. (Bite 2026-11-21: first Q6_K run started with the
   copywriter resident — 22 GB baseline → canceled + restarted.)
1. Set env to the candidate pair (or edit constants as fallback).
2. `./scripts/model-manager rebuild media-pipeline` (only if code changed) /
   `docker compose -f compose/comfyui.yml --profile image up -d` (env change).
3. `nohup python3 media-pipeline/qa_wan_phase1.py 720 --gpu 1 > state/phase6_<cand>_720p.log 2>&1 &`
   (gate: peak VRAM + wall time + output file).
4. Quality: extract 2–3 frames from the new mp4 + the Q5_K_M baseline mp4,
   compare (sharpness, motion coherence, artifacts). `ffmpeg -i x.mp4 -vf
   select=eq(n\,20) frame.png`.
5. Record: peak MiB, wall_s, quality verdict → progress log below.
6. Candidate OOMs or peak > ~22 GB → reject (need headroom for the
   copywriter XOR), record why.

### 2d. T5-on-GPU sub-experiment

- workflows.py:170: CLIPLoader `"device": "cpu"` → `"cuda"` (env-gated:
  `WAN_T5_DEVICE`, default `cpu`).
- umt5 fp8 = 6.74 GB → 720p peak 19.6 + 6.7 ≈ 26 GB = **OOM on 24 GB**
  (expected reject at 720p). At 480p (peak 14.4 + 6.7 ≈ 21 GB) it may fit —
  test only if a 720p winner needs the speed (T5-on-GPU cuts the ~4 min
  text-encode step per clip).
- Record the math + result either way.

### 2e. Both-experts-resident sub-experiment

- Current: ComfyUI swaps experts at the schedule midpoint (peak ≈ 1 expert +
  VAE + activations). Both resident = +10.8 GB (Q5) / +13.3 GB (FP8) →
  720p: 19.6 + 10.8 ≈ 30 GB = **OOM (expected reject)**; 480p: 14.4 + 10.8 ≈
  25 GB = OOM. Verify empirically only if cheap; otherwise record the math as
  the rejection.

### 2f. Decision + promotion

- Winner = best quality at 720p with peak ≤ ~22 GB. If no higher quant fits
  720p, **Q5_K_M stays** (document the ceiling — the 24 GB card can't hold a
  14B dual-expert at Q8/FP8 + 720p activations; revisit with a bigger card or
  480p-only higher quant).
- Promote: default env in compose (or constants), metering label (Item 4),
  docs (API §4/§6, mcp_media desc + README, media-film SKILL/contract in the
  harness repo), plan checkbox.

## Item 3 — headroom guard for the Ollama-resident case (plan §4.1.4)

- Current: `_ensure_vram_headroom` (server.py, ~line 510) +
  `VRAM_HEADROOM_GIB = {"music": 8.0, "tts": 2.0, "transcribe": 4.0}`
  (line 468): when free VRAM < the flow's requirement → wait for an idle
  ComfyUI queue → `docker restart comfyui_backend` (~40 s) to drop its
  resident model.
- **Add (per plan §4.1.4)**: before the ComfyUI restart, if the Ollama
  copywriter is resident on GPU 1, try the cheaper fix first:
  `docker exec ollama ollama stop gemma4:31b` (instant; weights stay on disk,
  next copy request reloads in ~10–30 s). New order:
  1. check free VRAM (already there);
  2. if low → check `docker exec ollama ollama ps` for a resident copywriter
     (gemma4:31b; make the model name env-configurable, default
     `gemma4:31b`) → if resident, `ollama stop` it;
  3. re-check free VRAM;
  4. if still low → the existing ComfyUI-restart path.
- Test: load gemma4:31b (`curl -X POST :11434/api/generate` with a small
  prompt), confirm it's resident (ollama ps + nvidia-smi), submit a `music`
  job, verify the guard logged the `ollama stop` (not a ComfyUI restart) and
  the job succeeded. Then confirm the copywriter reloads on the next
  generate.

## Item 4 — metering labels + API doc §6

- `media-pipeline/metering.py:183`: `return "wan2.2-14b-i2v-gguf-q5"` —
  hardcoded. Make it follow the active quant (env-driven, e.g.
  `WAN_METERING_LABEL`, default `wan2.2-14b-i2v-gguf-q5`; set to
  `-q6`/`-q8`/`-fp8` for the winner).
- `docs/matrix_media_pipeline_api.md` §6 VRAM budget table: add/update the
  Wan rows for the final config (720p peak for the winner; the 2026-11-21
  19.6 GB Q5_K_M row stays as the baseline).
- (The 720p-option half of this item was done in commit `abef852`.)

## Item 5 — backup chain note

- `docs/matrix_backup.md` §"Explicitly OUT" list: add the new Wan quant
  downloads (Q6_K/Q8_0/FP8 experts, ~20–50 GB) as re-downloadable OUT-of-
  manifest items (same treatment as the Lorbus INT4 / cyankiwi AWQ notes).

## Conventions

- **Commits**: workstation `/home/chuck/homelab` (branch `main`); harness
  `/home/chuck/tmp/homelab-ai-harness` (separate commit for mcp_media +
  media-film doc changes). User pushes both.
- **No disruption to the daily**: everything here is GPU 1 / media-pipeline
  only. GPU 0 vLLM never restarts.
- **QA logs**: `state/phase6_*.log` (gitignored — keep the RESULT json lines
  in the progress log below).
- **Append to the progress log after every milestone** (download done, test
  result, code change, commit).

## Progress log

- **2026-11-21**: task doc created. Item 1 already done (720p gate PASSED
  19,641 MiB peak; 480p cap lifted; live-verified; commits `abef852`
  workstation + `d47fc1f` harness).
- **2026-11-21 (21:00 UTC)**: 2b + 2d + 4 code done (rebuild + restart OK,
  health green, defaults unchanged):
  - workflows.py: `WAN_HIGHNOISE_MODEL` / `WAN_LOWNOISE_MODEL` env overrides
    (defaults = Q5_K_M GGUF), `WAN_UNET_LOADER` (gguf|unet — FP8 safetensors
    use UNETLoader), `WAN_T5_DEVICE` (cpu|cuda) on the CLIPLoader node.
  - metering.py: wan14b label follows `WAN_METERING_LABEL` env (default
    `wan2.2-14b-i2v-gguf-q5`).
  - server.py (Item 3): `_ensure_vram_headroom` now stops the Ollama
    copywriter first (`docker exec ollama ollama stop gemma4:31b`, model
    via `OLLAMA_COPYWRITER_MODEL` env) before the ComfyUI-restart path;
    stale pre-2-GPU comment (vLLM 56 GiB on the same card) fixed.
  - compose/comfyui.yml: commented env block for the quant experiments.
- **2026-11-21 (20:49 UTC)**: 2a downloads started in background
  (Q6_K ×2 12 GB, Q8_0 ×2 15.41 GB, FP8 ×2 ~13.3 GB from Comfy-Org/Wan-2.2
  `split_files/diffusion_models/wan2.2_i2v_{high,low}_noise_14B_fp8_scaled.safetensors`;
  ~81 GB total, ~28 MB/s ≈ 50 min). **Note**: the models dir is owned by
  comfy uid 1024 — downloads go to `/home/chuck/tmp/phase6_models/` then
  `docker cp` into the container (script: `/tmp/phase6_downloads.sh`, log:
  `state/phase6_downloads.log`).
- **2026-11-21 (21:05 UTC)**: qa_wan_phase1.py parameterized with the same
  env knobs (WAN_HIGHNOISE_MODEL / WAN_LOWNOISE_MODEL / WAN_UNET_LOADER /
  WAN_T5_DEVICE / WAN_QUANT_TAG) — quant VRAM gates run without touching the
  pipeline container.
- **2026-11-21 (21:05 UTC)**: **Item 3 VERIFIED end-to-end**: freed GPU 1
  (ComfyUI restart dropped the 9.5 GB Wan model) → loaded gemma4:31b
  (22,021 MiB resident, ~1.9 GiB free) → music job `240a7671faea` (15 s
  track): guard logged `VRAM headroom 1.9 GiB < 8 GiB — stopping Ollama
  copywriter (gemma4:31b) to free GPU 1`, job done in 46 s, ComfyUI never
  restarted. Copywriter reload on next generate: **9.8 s** (weights in page
  cache — beats the 10–30 s estimate).
- **2026-11-21 (21:05 UTC)**: Item 5 done — docs/matrix_backup.md: quant
  candidates land in the manifest's diffusion_models path (covered
  automatically; losers deleted post-decision, staging dir not backed up).
