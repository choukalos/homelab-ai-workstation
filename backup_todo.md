# backup_todo.md — /home/chuck/data backup (matrix)

Status: **parked — needs a dedicated design conversation before building.**
(2026-09-21: split out of the directory-layout audit; see `fix_todo.md` F4.)

## The problem

`/home/chuck/data/` on matrix is 331 GB with the stated intent that it "should be
backed up", but there is currently **no backup mechanism** (no restic/borg/rclone,
no cron, no backup docs).

## Decisions / notes from chuck (2026-09-21)

1. **We need a backup.**
2. **Do NOT back up by re-downloading model caches.** No blanket "models are
   re-downloadable, skip them" policy — instead:
3. **Back up SELECTED models only:**
   - `qwen3.8 NVFP4` — the daily driver (vLLM primary model)
   - the **current media pipeline models** (ComfyUI-based: image/upscale/TTS/music/SFX)
4. **Selected-model backups should be occasional** (e.g. after a model set changes /
   monthly), not part of every regular backup run.
5. **Backup target: chuck's server "Lego".**

## Inventory snapshot (2026-09-21, for the design conversation)

### Regular data (non-model) — what a routine backup should cover (TBD)

| Path | Size | Notes |
|---|---|---|
| `data/media/projects/` | ~50 MB | finished media deliverables (kitchen-strikes-back, potato-wars, neon-thunder) — the real keepers |
| `data/comfyui/run/media_jobs/` | 2.2 GB | pipeline job outputs; subject to 14-day retention (fix_todo F2) — probably NOT a backup target |
| `data/comfyui/basedir/{input,output,user,config}` | ~700 MB | ComfyUI inputs/outputs/settings |
| `data/logs/` | 0 | empty |
| homelab repo itself | 4.1 GB | already on GitHub — not a backup concern |

### Selected models (occasional backup) — candidates from inventory

| Path | Size | Role |
|---|---|---|
| `data/models/hub/models--unsloth--Qwen3.8-27B-NVFP4` | 22 GB | **daily driver** (matrix-coder profile) |
| `data/models/hub/models--Inferact--Qwen3.8-27B-NVFP4` | 25 GB | duplicate NVFP4 (which one is live? verify before choosing) |
| `data/comfyui/basedir/models/diffusion_models` | 32 GB | Qwen-Image etc. (media pipeline image gen) |
| `data/comfyui/basedir/models/SEEDVR2` | 3.7 GB | media pipeline upscale (pipeline B) |
| `data/comfyui/basedir/models/acestep` | 9.4 GB | media pipeline music (ACE-Step) |
| `data/comfyui/basedir/models/mmaudio` | 5.3 GB | media pipeline SFX (MMAudio) |
| `data/comfyui/basedir/models/tts` | 1.8 GB | media pipeline voice-over (trailer voice) |
| `data/comfyui/basedir/models/text_encoders` | 14 GB | Qwen-Image text encoders |
| `data/comfyui/basedir/models/loras` | 2.4 GB | LoRAs |
| `data/comfyui/basedir/models/vae` | 1.8 GB | VAEs |
| `data/comfyui/basedir/models/checkpoints` | 6 GB | misc checkpoints |

(Explicitly OUT per decision #2: the rest of `data/models/hub/` — gemma-4-31b 59G,
Qwen3.8-FP8 29G, experiment candidates — and `data/ollama/` 34G, `data/huggingface/` 18G,
`data/comfyui/basedir/models/upscale_models` 128M etc. — re-downloadable, not backed up.)

## Open questions for the design conversation

- **Lego details:** OS, reachable how (LAN? VPN?), path/capacity, access method
  (SSH? NFS/SMB mount? S3-compatible object store?).
- **Tool:** restic vs borg vs rclone (Lego-side storage type drives this).
- **Routine backup scope:** exactly which non-model paths, how often (daily? weekly?),
  retention on the Lego side.
- **Selected-model backup trigger:** on-demand script vs calendar (monthly)?
  Which of the two NVFP4 copies is the live one?
- **Encryption** (at rest on Lego) and **verification** (restore test plan).
- **Sizing:** routine data is small (<2 GB) — the occasional model backup is ~70-90 GB.