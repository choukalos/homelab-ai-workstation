# backup_todo.md — /home/chuck/data backup (matrix → Lego)

Status: **LIVE 2026-09-27 — both tracks complete; #3 dropped, #4 resolved,
#6 fixed.** Credentials set (`username=backup`), cron installed (Sunday
03:00). First routine snapshot (2026-09-26) taken + verified (MD5 match vs
source). Models backup (~115 GB source) completed 2026-09-27 01:32 (post-run
verify PASS + MD5 spot-checks — #1 resolved). The 03:00 routine cron run of
2026-09-27 had FAILED on two uid-1024/0600 files (media-pipeline reference
workflows) — fixed 2026-09-27 16:46 via a privileged rsync wrapper (root
rsync via scoped sudoers rule; ownership quirks can no longer break a run)
+ chown of the two files; the 2026-09-27 routine snapshot re-ran, completed
and verified (MD5 match on both files). fstab is `soft` + `vers=2.1`.
Scripts in `scripts/backup/` (see its README.md). History: parked 2026-09-21
after directory-layout audit (fix_todo F4); design conversation held
2026-07-15 (thor conversation) — plan below; built 2026-09-26 per this plan;
first live run 2026-09-26.

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

1. **Models track first run** — ✅ RESOLVED 2026-09-27. The models backup
   (~115 GB source) completed 2026-09-27 01:32 via
   `/home/chuck/homelab/scripts/backup/backup-models.sh`. Timeline: the 00:34
   first pass didn't finish (6 of 10 sources started, no done/verify line in
   the log); two dry-runs at 01:10/01:17 after the free-space guard was
   raised 120→220 GB; the 01:17:18 run completed all 10 sources. `backup.log`
   ends with `post-run verify: PASS (0 changed files)` +
   `=== models backup done ===`. Spot-checks (MD5 vs source, both match):
   small file — `comfyui/basedir/models/tts/xtts_cache/.../lang_code2speaker.json`
   (`9d50fc3cf8267befb1fc25c0350f4589`); 3.5 GB file —
   `comfyui/basedir/models/acestep/acestep-5Hz-lm-1.7B.safetensors`
   (`aa9129d5cf9ffd41521e203deef340ea`). Snapshot
   `/mnt/lego/matrix/models/2026-09-27/` holds all 10 manifest dirs (222
   files). Note: `du` shows ~200 GB vs ~115 GB source — expected, not
   bloat: HF-hub `snapshots/*/model.safetensors` are symlinks into `blobs/`
   and `-rtL` materializes them (CIFS can't store symlinks), so the 22 GB
   unsloth weights exist as 5 full copies (110 GB). Context for what nearly
   bit us: repeated idle-session deaths on Lego's SMB server had wedged the
   CIFS kernel state (D-state `cifsd`/`umount`/zombie rsync — unkillable,
   `timeout`/`kill` can't rescue a D-state process); the 2026-09-27 reboot
   cleared it. fstab is `vers=2.1,soft` (no `file_mode` — the server reports
   755 for everything anyway, and a forced `file_mode` breaks `--link-dest`
   hardlinking with `-a`; scripts use `-rt`). Credentials (`username=backup`),
   sudoers rule, cifs module, and cron are all in place.
2. **Checkpoints (6 GB)** — ✅ RESOLVED 2026-09-26: included in
   `model_manifest.txt` (safe side; one line to remove if not wanted).
3. **Encryption at rest** — ❌ DROPPED 2026-09-27 (accepted risk): LAN-only
   homelab NAS with credential-restricted access; plaintext keeps things
   simple. Revisit restic if Lego ever becomes reachable off-LAN.
4. **Inferact NVFP4 duplicate (25 GB)** — ✅ RESOLVED 2026-09-27: deleted
   `data/models/hub/models--Inferact--Qwen3.8-27B-NVFP4` (25 GB; root-owned
   dir, removed via `docker exec qwen38 rm -rf` as root). Safety checks
   before delete: live matrix-coder (container `qwen38`,
   `compose/qwen-coder.yml`) runs `unsloth/Qwen3.8-27B-NVFP4` — confirmed via
   running process args, `docker inspect`, and the vLLM API (`/v1/models`
   → root `unsloth/Qwen3.8-27B-NVFP4`); no process had open fds on the
   Inferact dir; unsloth copy verified intact (22 GB) and vLLM healthy after
   delete. Note added to `models/profiles/experiment-qwen38-27b-nvfp4.yaml`
   (the historical experiment profile referenced the Inferact path; re-running
   that experiment re-downloads from HF).
5. **Static hosts entry** — `lego.local` → 192.168.5.100 resolves via **DNS**
   (not `/etc/hosts` — grep of /etc/hosts is empty). No action needed.
6. **Routine cron run 2026-09-27 03:00 FAILED (rsync code 23)** — ✅ RESOLVED
   2026-09-27 (privileged rsync wrapper + chown; see fix notes below). What the files are: `data/comfyui/basedir/user/default/{templates,workflows}/qwen-image-2512-infographic-720p.json`
   (8 KB each) are one saved ComfyUI workflow — the Qwen-Image-2512
   infographic mode (GGUF Q3_K_M + Lightning 8-step + 4x upscale → 1080p).
   The 2026-08-28 legacy cleanup **intentionally kept** this file as a
   media-pipeline reference (`docs/matrix_comfyui_media_api.md` changelog),
   so it's a capability artifact, not throwaway scratch. The pipeline itself
   builds graphs in code (`media-pipeline/workflows.py`), so the JSON is a
   reference, not a runtime dependency — but it's worth backing up.
   Root cause: the ComfyUI container entrypoint runs `sudo su comfy` — the
   live ComfyUI process runs as **uid 1024** (in-container `comfy` user) and
   keeps writing (~236k files under `/home/chuck/data` are uid-1024; most
   are 644/readable, only these two are 0600 → unreadable by chuck/uid 1000
   → rsync code 23). `set -euo pipefail` aborted the run after the `user`
   source, so `config` was never rsynced and post-run verify never ran — the
   2026-09-27 routine snapshot is incomplete (projects/input/output OK, user
   partial, config missing).
   Fix (implemented 2026-09-27, option B + A):
   A. `chown`ed the two files to chuck (via root `media_pipeline` container) —
      unblocked the re-run.
   B. **Privileged rsync wrapper** `lego-backup-rsync` (source of truth:
      `scripts/backup/lego-backup-rsync`, installed root-owned 755 at
      `/usr/local/sbin/lego-backup-rsync`): runs the backup/verify/restore
      rsyncs as root via a scoped NOPASSWD rule in its own sudoers file
      (`/etc/sudoers.d/lego-backup-rsync` — kept separate from the mount
      rule so a bad edit can't take the mount rule down). The wrapper is the
      only enforcement: it validates src/dest roots (backup/verify:
      `/home/chuck/data` → `/mnt/lego`; restore: the reverse), rejects `..`,
      resolves symlinks, and fixes the rsync flags (callers pass no flags).
      `check_setup` probes it with `sudo -n ... ping`. `backup-setup.sh` now
      installs wrapper + rule (idempotent). Result: no future uid/mode
      weirdness (1024, 1025, root, 0600, 000) can break a run — root reads
      everything under DATA_ROOT. Restore also runs as root, so root-owned
      local files (e.g. the HF model hub dirs) can be overwritten on restore.
   Re-ran `./backup-routine.sh` 2026-09-27 16:46: all 5 sources OK, post-run
   verify PASS (0 changed files); the two infographic files are in the
   snapshot with MD5 match (`4e0fd6d5298b38cf943d5996f782bfb5`); per-source
   file counts match the source (projects 4, input 100, output 441, user 20,
   config 0 — config is an empty dir). The 2026-09-27 routine snapshot is
   complete + verified. Note: the 03:00 failure only cost the two 0600 files
   — `config` was never rsynced but is empty, so nothing else was missing.