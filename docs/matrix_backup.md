# Matrix Backup — `/home/chuck/data` → Lego NAS

**Status: LIVE (2026-09-27).** Both tracks running: weekly routine snapshots
(cron Sun 03:00, keep 4) and on-demand model snapshots (keep 2), all
post-run-verified. Operational details (setup, commands, CIFS quirks,
privileged rsync wrapper) live in
[`scripts/backup/README.md`](../scripts/backup/README.md) — this doc is the
design rationale, scope decisions, and incident history.

## Design

### Target: "Lego" NAS

- `lego.local` → **192.168.5.100** (resolves via DNS/mDNS; no static
  `/etc/hosts` entry needed — verified 2026-09-27). SMB 445/139 open; SSH 22
  also open (fallback access).
- Access: **CIFS mount** at `/mnt/lego/` (fstab, `vers=2.1`, `soft`,
  `noauto` + `x-systemd.automount` so a downed NAS can't wedge boot or the
  backup script). Credentials in `~/.smbcredentials` (chmod 600,
  `username=backup`). Per-host subfolder `/mnt/lego/<hostname>/` so the same
  scripts onboard thor / future hosts unchanged.
- On-NAS layout: `<share>/matrix/{routine,models}/YYYY-MM-DD/`.

### Tool: rsync over the CIFS mount (not restic/borg)

Rationale: routine data is <1 GB, model data is ~110 GB of large files that
change rarely. rsync gives exactly the "only what changed since last week"
behavior we want; restore is a plain rsync back; no new daemon or repo format
to maintain. (Revisit restic if data ever leaves the LAN — see Decisions.)

### Snapshot scheme: hardlink rotation (`rsync --link-dest`)

Each run rsyncs into a new dated snapshot dir with `--link-dest` pointing at
the previous snapshot: unchanged files become hardlinks (zero extra disk),
changed/new files take real space. This gives **both** "N weeks of history"
**and** "only weekly changes consume space" — a full view per snapshot at
delta cost. Deletion of old snapshots is plain `rm -rf` (hardlink-safe).

### Privileged rsync

All backup/verify/restore rsyncs run **as root** through the
`lego-backup-rsync` wrapper (scoped NOPASSWD sudoers rule, root-owned
wrapper, path-validated). Reason: `/home/chuck/data` contains files owned by
other uids with restrictive modes — the ComfyUI container runs as uid 1024
(in-container `comfy` user) and saves some files 0600, unreadable by chuck.
On 2026-09-27 two such files broke the routine cron run (rsync code 23). A
root rsync reads everything, so ownership/mode quirks can never break a run
again. Full security model: `scripts/backup/README.md` → "Privileged rsync".

## Tracks

### Track 1 — routine data, automated weekly (cron Sun 03:00, keep 4)

| Path | Size | Why |
|---|---|---|
| `data/media/projects/` | ~86 MB | finished deliverables — the real keepers |
| `data/comfyui/basedir/{input,output,user,config}` | ~730 MB | inputs/outputs/settings/workflows/DB (`config` is currently an empty dir) |

Excluded (decisions): `data/comfyui/run/media_jobs/` (14-day retention,
re-runnable), `data/logs/` (empty), the homelab repo (already on GitHub),
everything under model caches (Track 2). Cost: ~0.8 GB first run, then only
the weekly delta (MB-scale).

### Track 2 — selected production models, on-demand (keep 2)

Manifest-driven: `scripts/backup/model_manifest.txt` lists the production
model paths; the script rsyncs exactly those, same `--link-dest` rotation.

| Path | Size | Role |
|---|---|---|
| `data/models/hub/models--unsloth--Qwen3.8-27B-NVFP4` | 22 GB | **daily driver** — `compose/qwen-coder.yml` runs `--model unsloth/Qwen3.8-27B-NVFP4` |
| `data/comfyui/basedir/models/diffusion_models` | 39 GB | Qwen-Image 2.1 int8 + GGUF (image gen/edit) |
| `data/comfyui/basedir/models/text_encoders` | 23 GB | Qwen3-VL-8B, T5-XXL (Qwen-Image encoders) |
| `data/comfyui/basedir/models/acestep` | 9.4 GB | music (ACE-Step) |
| `data/comfyui/basedir/models/checkpoints` | 6 GB | misc checkpoints — **included** (safe-side, 2026-09-26) |
| `data/comfyui/basedir/models/mmaudio` | 5.3 GB | SFX (MMAudio) |
| `data/comfyui/basedir/models/SEEDVR2` | 3.7 GB | upscale pipeline B |
| `data/comfyui/basedir/models/vae` | 2.5 GB | VAEs |
| `data/comfyui/basedir/models/loras` | 2.4 GB | LoRAs |
| `data/comfyui/basedir/models/tts` | 1.8 GB | voice-over (trailer voice, XTTS) |

≈ **115 GB source** (~200 GB on-NAS: HF-hub `snapshots/*/model.safetensors`
are symlinks into `blobs/` and rsync `-L` materializes them because CIFS
can't store symlinks — so the 22 GB unsloth weights exist as 5 full copies.
Expected, not bloat.) Retention: keep last 2 model snapshots (rollback to the
previous model set).

**Explicitly OUT** (re-downloadable): the rest of `data/models/hub/`
(experiment candidates: gemma-4-31b 59 GB, Qwen3.8-FP8 29 GB, Lorbus INT4
18 GB, cyankiwi gemma-4-26B AWQ 17 GB — all re-pullable from HF),
`data/ollama/` (~34 GB), `data/huggingface/` (~18 GB), `upscale_models`,
`animatediff_*`, `controlnet`, etc. The Inferact NVFP4 duplicate (25 GB) was
deleted 2026-09-27 (see Decisions).

## Verification

- Every run: rsync exit code + dry-run diff count logged to
  `~/.local/state/backup/backup.log` (+ `cron.log` for the cron run).
- Post-run verify: a dry-run re-rsync must report **0 files that would
  change** (WARN otherwise — CIFS mtime granularity can false-positive;
  re-run is cheap thanks to hardlink rotation).
- Monthly: restore drill — pull one small file from a snapshot,
  sha256-compare against source. (Done 2026-09-26 and 2026-09-27: MD5 match
  on routine + models spot-checks, incl. a 3.5 GB safetensors file.)

## Decisions

| # | Decision | When |
|---|---|---|
| checkpoints (6 GB) | **INCLUDED** in the model manifest (safe-side; one manifest line to remove if not wanted) | 2026-09-26 |
| encryption at rest | **DROPPED (accepted risk)**: LAN-only homelab NAS with credential-restricted access; plaintext keeps things simple. Revisit restic if Lego ever becomes reachable off-LAN | 2026-09-27 |
| Inferact NVFP4 duplicate (25 GB) | **DELETED** — re-downloadable copy of the live unsloth model. Safety checks before delete: live model confirmed as the unsloth copy three ways (process args, `docker inspect`, vLLM API root field), no open fds, unsloth copy verified intact + vLLM healthy after. Note added to `models/profiles/experiment-qwen38-27b-nvfp4.yaml` | 2026-09-27 |
| uid-1024 0600 files breaking the routine run | **Privileged rsync wrapper** (root rsync via scoped sudoers rule) + chown of the two files. The two files are the Qwen-Image-2512 infographic workflow — a media-pipeline reference intentionally kept during the 2026-08-28 legacy cleanup (`docs/matrix_comfyui_media_api.md`), so worth backing up | 2026-09-27 |
| static `/etc/hosts` entry | Not needed — `lego.local` resolves via DNS | 2026-09-27 |

## History / incidents

- **2026-07-15** — design conversation (thor conversation): rsync-over-CIFS,
  hardlink rotation, two tracks.
- **2026-09-21** — parked after the directory-layout audit (fix_todo F4).
- **2026-09-26** — built per the plan; first routine snapshot taken +
  verified (MD5 match vs source); credentials, sudoers, cifs module, cron
  installed.
- **2026-09-27 (pre-dawn)** — repeated idle-session deaths on Lego's SMB
  server had wedged the CIFS kernel state (D-state `cifsd`/`umount`/zombie
  rsync — unkillable; only a reboot clears it). Reboot cleared it. First
  models run: 00:34 pass didn't finish (6/10 sources); free-space guard
  raised 120→220 GB; 01:17 run completed all 10 sources at 01:32, post-run
  verify PASS + MD5 spot-checks.
- **2026-09-27 03:00** — routine cron run FAILED (rsync code 23): two
  uid-1024/0600 files unreadable by chuck aborted the run after the `user`
  source. Fixed 16:46 (privileged rsync wrapper + chown); re-run completed
  + verified (post-run verify PASS, MD5 match on both files, per-source file
  counts match). The failure only cost those two files — `config` was never
  rsynced but is an empty dir.