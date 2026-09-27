# Lego backup (matrix → 192.168.5.100)

rsync-over-CIFS snapshots of `/home/chuck/data` onto the Lego NAS, per the
design in [`../../backup_todo.md`](../../backup_todo.md).

## Layout

The `backup` share on Lego (192.168.5.100) holds one subfolder **per host**
(named after the hostname, derived automatically — the same scripts work
unchanged on matrix, thor, or any future host):

```
Lego share "backup"
├── matrix/                  # this host (hostname-derived)
│   ├── routine/YYYY-MM-DD/  # Track 1 — weekly, keep 4
│   └── models/YYYY-MM-DD/   # Track 2 — on-demand, keep 2
├── thor/                    # (when thor is onboarded)
└── ...
```

Each snapshot mirrors the source path under `/home/chuck/data/`
(e.g. `routine/2026-09-27/comfyui/basedir/models/tts/`), so restore is a
plain rsync back. Snapshots use `rsync --link-dest` hardlink rotation:
unchanged files are hardlinks to the previous snapshot (zero extra disk),
only the delta consumes space. Pruning is a plain `rm -rf` (hardlink-safe).

## Files

| File | Purpose |
|---|---|
| `backup.conf` | config: share, paths, retention, free-space guards |
| `backup-lib.sh` | shared logic (logging, mount, snapshot, prune, verify, lock) |
| `backup-routine.sh` | Track 1: `media/projects` + comfyui `input/output/user/config` |
| `backup-models.sh` | Track 2: manifest-driven production model set (~110 GB) |
| `backup-restore.sh` | restore a snapshot (dry-run by default, `--apply` to execute) |
| `backup-setup.sh` | **one-time root setup**: fstab + mount + capacity check |
| `model_manifest.txt` | the production model list (edit when the set changes) |

## First-time setup (once, as root)

1. Put the real Lego password in `~/.smbcredentials` (template already there,
   `username=backup`; keep `chmod 600`).
2. `sudo /home/chuck/homelab/scripts/backup/backup-setup.sh <share-name>`
   (writes the share into `backup.conf`, installs the fstab entry, installs a
   scoped sudoers rule, mounts, verifies capacity ≥ ~150 GB, write-tests).
3. `./backup-routine.sh --dry-run` then `./backup-routine.sh` (first snapshot).
4. `./backup-models.sh --dry-run` then `./backup-models.sh` (first model set,
   ~110 GB one-time transfer).

The fstab entry is `noauto` + `x-systemd.automount`: the mount is triggered
on first access by the backup script (systemd does the root mount); a downed
NAS can never wedge boot or other services.

The setup script also installs a scoped sudoers rule
(`/etc/sudoers.d/lego-backup`) so the backup user can `mount`/`umount -l`
`/mnt/lego` without a password — needed for the self-heal below.

## Operations

```bash
./backup-routine.sh                 # weekly (cron Sun 03:00); manual OK
./backup-models.sh                  # on-demand, monthly max
./backup-restore.sh routine         # dry-run: newest routine snapshot
./backup-restore.sh models 2026-09-27 --apply   # real restore
```

- Logs: `~/.local/state/backup/backup.log` (every run), `cron.log` (cron).
- Every run logs: free-space check, per-source rsync, prune, and a
  **post-run verify** (dry-run re-rsync must report 0 changed files; a WARN
  means re-run or check CIFS mtime granularity).
- Monthly restore drill (per the plan): pick one small file from a snapshot,
  `sha256sum` it against the live source.
- Locking: `flock` prevents overlapping runs of the same track.
- Restore is **additive** (no `--delete`): it never removes live files that
  are absent from the snapshot.

## CIFS / macOS SMB quirks (measured 2026-09-26)

Lego is a macOS box; its SMB server has four quirks that shaped the design:

1. **SMB 2.1 only.** SMB3 negotiation → `EOPNOTSUPP`, SMB 2.0.2 → `EINVAL`.
   The fstab entry pins `vers=2.1`.
2. **Idle sessions are killed quickly** (observed: dead within ~18 s–3 min of
   idle; the client's `has not responded in 180 seconds` dmesg message is
   just its detection threshold, not the server's timeout). Continuous I/O
   keeps the session alive (verified with a 5-min sustained-write test), so
   a running rsync is safe — but any stale mount must be remounted first.
   **A dead session on a `hard` mount is fatal**: all I/O blocks forever,
   processes pile up in unkillable D-state (zombie rsync/containers + stuck
   `umount -l` + stuck `cifsd` kernel threads), and `timeout`/`kill` cannot
   rescue a D-state process — only a reboot clears it. This is why the fstab
   entry uses `soft`: I/O fails with an error instead of hanging, the
   verify step catches any partial snapshot, and the next run resumes
   (hardlink rotation makes re-runs cheap).
3. **No real Unix modes.** The server reports mode 755 for every file, so
   original permissions cannot be preserved through the share.
4. **The server rate-limits logins after session churn.** Rapid
   umount/mount cycles (or many dead sessions) make new `mount`s block for
   minutes (transient `NT_STATUS_LOGON_FAILURE` with correct credentials).
   It clears on its own in a few minutes — the scripts bound the remount
   with a 10-min timeout rather than hanging forever.

Mitigations baked into the scripts:

- `ensure_mounted` (backup-lib.sh) probes the mount with `timeout 15 ls`;
  on a hung/missing mount it does `timeout 30 sudo -n umount -l` +
  `timeout 600 sudo -n mount` (the scoped sudoers rule), checks
  `/proc/mounts` after the umount (a stale attachment means wedged CIFS
  state → reboot), and re-probes. Same bounded self-heal runs in
  `backup-setup.sh`. **Both steps are timeout-bounded** because `umount -l`
  can hang while processes hold I/O to a dead superblock (reboot clears it)
  and the server's rate-limit can make `mount` take minutes.
- fstab uses `soft` (not `hard`) — see quirk 2. A `soft` mount can abort a
  backup mid-run on a transient server hiccup; that is safe because the
  post-run verify re-diffs the snapshot and fails the run (no silent
  partial snapshots), and the next run resumes from the previous snapshot.
- rsync uses `-rt` (NOT `-a`): with `-a`, rsync would try to fix the
  (unfixable) permission mismatch and re-transfer every file on every run;
  it also defeats `--link-dest` hardlinking. **Permissions are not
  preserved in backups** (restored files land as 644/755).
- rsync uses `--modify-window=1`: CIFS truncates mtime to 100 ns; without
  this, rsync's nanosecond mtime comparison defeats `--link-dest` hardlinks
  (verified: with the flag, unchanged files share inodes across snapshots).
- **Operational rule**: do mount + work in one continuous invocation. The
  session only survives continuous I/O; any gap of ~1 min risks a dead
  session. Run the backup scripts (which self-heal) rather than ad-hoc I/O.
  If the kernel wedges (D-state `cifsd`/`umount` in `ps`), reboot the host.

## Known edge cases / decisions

- **rsync quick check is mtime+size.** A file modified within the same second
  as the previous snapshot with identical size would be treated as unchanged.
  Negligible for weekly runs; if it ever matters, add `--checksum` to
  `sync_source` (full-read cost on 110 GB).
- **checkpoints (6 GB) are INCLUDED** in the model manifest (open item #2,
  resolved safe-side 2026-09-26). Remove its manifest line to exclude.
- **Plaintext on a LAN-only NAS** (open item #3): if Lego ever becomes
  reachable off-LAN, switch Track 2 to restic.
- The Inferact NVFP4 duplicate (25 GB) is intentionally NOT backed up
  (re-downloadable; deletion is a separate decision — open item #4).