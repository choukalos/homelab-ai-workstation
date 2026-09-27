# backup_todo.md — /home/chuck/data backup (matrix → Lego)

Status: **LIVE 2026-09-26 — routine track complete; models track running
(2026-09-27).** Credentials set (`username=backup`), cron installed (Sunday
03:00), first routine snapshot taken + verified (MD5 match vs source). The
models backup (~115 GB) started 2026-09-27 00:34 after the reboot that
cleared the wedged CIFS state (D-state `cifsd`/`umount` — unkillable —
caused by repeated idle-session deaths on Lego's SMB server); expect
~15–30 min (continuous I/O keeps the session alive). Remaining: confirm the
run's post-run verify passed and spot-check one file (open item #1). fstab
is `soft` + `vers=2.1`. Scripts in `scripts/backup/` (see its README.md).
History: parked 2026-09-21 after directory-layout audit (fix_todo F4); design
conversation held 2026-07-15 (thor conversation) — plan below; built
2026-09-26 per this plan; first live run 2026-09-26.

## The problem

`/home/chuck/data/` on matrix is ~346 GB with the stated intent that it "should be
backed up", but there is currently **no backup mechanism** (no restic/borg/rclone,
no cron, no backup docs, no `.smbcredentials`).

## Confirmed design

### Target: "Lego" NAS

- `lego.local` → **192.168.5.100** (resolves via mDNS today; add a static
  `/etc/hosts` entry for robustness). SMB ports 445/139 verified open
  (2026-07-15). SSH 22 also open (fallback access).
- Access: **CIFS mount** with credentials in `/home/chuck/.smbcredentials`
  (chmod 600). Mount point: **`/mnt/lego/`** (fstab, `vers=2.1`, `soft`,
  `noauto` + `x-systemd.automount` so a downed NAS can't wedge the backup
  script or boot). Per-host subfolder: `/mnt/lego/<hostname>/` so the same
  scripts onboard thor / future hosts unchanged.
- Backup/restore layout on Lego:
  ```
  <share>/homelab/
    routine/YYYY-MM-DD/     # weekly snapshots
    models/YYYY-MM-DD/      # occasional snapshots
  ```

### Tool: rsync over the CIFS mount (not restic/borg)

Rationale: routine data is <1 GB, model data is ~110 GB of large files that
change rarely. rsync gives exactly the "only what changed since last week"
behavior we want, restore is a plain rsync back, and there's no new daemon or
repo format to maintain. (Revisit restic if data ever leaves the LAN.)

### Snapshot scheme: hardlink rotation (`rsync --link-dest`)

Each run rsyncs into a new dated snapshot dir with `--link-dest` pointing at
the previous snapshot: unchanged files become hardlinks (zero extra disk),
changed/new files take real space. This gives **both** "2 weeks of history"
**and** "only weekly changes consume space" — a full view per week at delta
cost. Deletion of old snapshots is plain `rm -rf` (hardlink-safe).

### Track 1 — routine data, automated weekly (cron, e.g. Sunday 03:00)

Scope (what a routine run covers):

| Path | Size | Why |
|---|---|---|
| `data/media/projects/` | ~86 MB | finished deliverables — the real keepers |
| `data/comfyui/basedir/{input,output,user,config}` | ~730 MB | inputs/outputs/settings/workflows/DB |

Excluded (decisions #2/#4): `data/comfyui/run/media_jobs/` (14-day retention,
re-runnable), `data/logs/` (empty), homelab repo (already on GitHub),
everything under model caches (Track 2).

- Retention: **keep 4 weekly snapshots** (~2 weeks of history); prune older.
- Cost: ~0.8 GB first run, then only the weekly delta (MB-scale).

### Track 2 — selected production models, occasional (on-demand script + optional monthly cron)

Manifest-driven: `scripts/backup/model_manifest.txt` lists the production
model paths; the script rsyncs exactly those, same `--link-dest` rotation.

Confirmed live set (2026-07-15):

| Path | Size | Role |
|---|---|---|
| `data/models/hub/models--unsloth--Qwen3.8-27B-NVFP4` | 22 GB | **daily driver** — verified live: `compose/qwen-coder.yml` runs `--model unsloth/Qwen3.8-27B-NVFP4` |
| `data/comfyui/basedir/models/diffusion_models` | 39 GB | Qwen-Image 2.1 int8 + GGUF (image gen/edit) |
| `data/comfyui/basedir/models/text_encoders` | 23 GB | Qwen3-VL-8B, T5-XXL (Qwen-Image encoders) |
| `data/comfyui/basedir/models/acestep` | 9.4 GB | music (ACE-Step) |
| `data/comfyui/basedir/models/checkpoints` | 6 GB | misc checkpoints — **included** (resolved 2026-09-26) |
| `data/comfyui/basedir/models/mmaudio` | 5.3 GB | SFX (MMAudio) |
| `data/comfyui/basedir/models/SEEDVR2` | 3.7 GB | upscale pipeline B |
| `data/comfyui/basedir/models/vae` | 2.5 GB | VAEs |
| `data/comfyui/basedir/models/loras` | 2.4 GB | LoRAs |
| `data/comfyui/basedir/models/tts` | 1.8 GB | voice-over (trailer voice, XTTS) |

≈ **109 GB** (115 GB if checkpoints included). Retention: keep last 2 model
snapshots (rollback to the previous model set). Cost: ~110 GB once, then only
model-set deltas. (Built 2026-09-26: checkpoints included → ~115 GB.)

Explicitly OUT (re-downloadable, per decision #2): `models--Inferact--Qwen3.8-27B-NVFP4`
(25 GB — duplicate of the live unsloth copy; deletion is a separate decision),
rest of `data/models/hub/` (gemma-4-31b, Qwen3.8-FP8, experiment candidates),
`data/ollama/` (34 GB), `data/huggingface/` (18 GB), `upscale_models` (128 MB),
`animatediff_*`, `controlnet`, etc.

### Scripts (in `scripts/backup/`)

- `backup-routine.sh` — mount-check → rsync routine scope → `--link-dest`
  snapshot → prune to 4 → post-run verify (re-rsync dry-run must be 0 files).
- `backup-models.sh` — same flow, manifest-driven, prune to 2.
- `backup-restore.sh <routine|models> [YYYY-MM-DD]` — rsync a chosen snapshot
  back to source paths; `--dry-run` by default, `--apply` to execute.
- `model_manifest.txt` — the production-model list (edit when the set changes).
- cron: weekly routine (Sun 03:00); model run on-demand (monthly max).

### Verification

- Every run: rsync exit code + dry-run diff count logged to
  `~/.local/state/backup/backup.log` (+ `cron.log` for the cron run).
- Monthly: restore drill — pull one small file from a snapshot, sha256-compare
  against source.

## Open items

1. **Models track first run** — 🔄 IN PROGRESS 2026-09-27. The routine track
   is live (first snapshot 2026-09-26, verified). The models backup (~115 GB)
   started 2026-09-27 00:34 via `/home/chuck/homelab/scripts/backup/backup-models.sh`
   (continuous I/O keeps the session alive). **Remaining:** once it finishes,
   confirm `~/.local/state/backup/backup.log` ends with the post-run verify
   OK (0 changed files) and spot-check one model file's MD5 against source
   (same drill as the routine track). Context for what nearly bit us:
   repeated idle-session deaths on Lego's SMB server had wedged the CIFS
   kernel state (D-state `cifsd`/`umount`/zombie rsync — unkillable,
   `timeout`/`kill` can't rescue a D-state process); the 2026-09-27 reboot
   cleared it. fstab is `vers=2.1,soft` (no `file_mode` — the server reports
   755 for everything anyway, and a forced `file_mode` breaks `--link-dest`
   hardlinking with `-a`; scripts use `-rt`). Credentials (`username=backup`),
   sudoers rule, cifs module, and cron are all in place.
2. **Checkpoints (6 GB)** — ✅ RESOLVED 2026-09-26: included in
   `model_manifest.txt` (safe side; one line to remove if not wanted).
3. **Encryption at rest** — plaintext on a LAN-only NAS, per plan. Revisit
   restic if Lego ever becomes reachable off-LAN.
4. **Inferact NVFP4 duplicate (25 GB)** — delete locally (separate decision,
   not part of this backup). Unchanged.
5. **Static hosts entry** — `lego.local` → 192.168.5.100 resolves via **DNS**
   (not `/etc/hosts` — grep of /etc/hosts is empty). No action needed.