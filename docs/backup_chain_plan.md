# Backup Chain Plan — Lego → Athena (+ USB drives) and Lego data cleanup

> Created: 2026-09-27
> Status: **PLAN — pending Athena access + user decisions**
> **Execution plan (human-readable, Phase 1/2 for the user + Phase 3 summary): [`nas_backup_plan.md`](../nas_backup_plan.md)** — this doc is the detailed reference (inventory, SMB quirks, options analysis).
> Scope: (1) clean up stale data on Lego, (2) add a new backup leg: **Lego → Athena (Synology)**,
> (3) update Athena's existing backups to the 3 external USB drives so they cover the new data,
> (4) align the directory structure of Lego and Athena.

> **Design update 2026-09-27 (user requirements):** Athena must serve as a **failover** for
> Lego (browsable, same directory structure, Infuse + SSH-as-chuck access), Athena has **less
> space** than Lego, and the USB tier is **3 drives of varying sizes, manual ~monthly +
> FireSafe 1–2×/year** (replacing the user's old rsync shell script). Consequence: the
> recommended design is now **two copies on Athena** — a Hyper Backup *versioned store*
> (safety net with history) **plus** an rsync *browsable mirror* (the failover/Infuse copy,
> scoped to fit Athena's capacity) — with the USB tier handled by new scripts (Phase 3).

---

## 1. Current state

### 1.1 The chain today

```
matrix (192.168.4.55) ──rsync snapshots──▶ Lego (192.168.5.100)  [DONE, verified]
thor   (192.168.4.54) ──rsync snapshots──▶ Lego (192.168.5.100)  [DONE]
Lego ──────────────────────────────────▶ Athena (Synology, IP TBD)  [THIS PLAN]
Athena ───────────────────────────────▶ 3× USB drives (external)    [EXISTS — needs update]
```

- matrix→Lego: rsync-over-CIFS snapshots, hardlink rotation, weekly routine + on-demand models
  track, post-run verify. Design: [`matrix_backup.md`](matrix_backup.md).
- Athena is **not currently reachable** from the LAN (name does not resolve; no Synology DSM
  ports found on any live host in 192.168.4.0/22 or .5.0/24). It may be powered off, on a
  different segment, or not yet on the network. **Open question §5.**

### 1.2 Lego inventory (measured 2026-09-27, `du` over CIFS)

Lego is a ~40 TB volume, ~19–21 TB used. Shares and top-level sizes:

| Share | Top-level contents | Size |
|---|---|---|
| `Multimedia` | `video` **15.1 TB**, `photo` 351 GB, `music` 85 GB | **~15.5 TB** |
| `homes/chuck` | `Documents` **1.7 TB** (incl. 145 GB Comcast dirs), `#recycle` **966 GB**, `backup` 488 GB, `iMac_Backup_Critical` 76 GB, `Photos` 4 GB, `Drive` 2.5 GB | **~2.45 TB** |
| `Store` | `Software` 255 GB, `Comics` 251 GB, `RPG` 137 GB, `Arcade` 127 GB, `VMWare-Images` 61 GB, `Books` 17 GB, `RaspberryPi` 30 GB, `Family Vehicles` 22 GB, `Podcasts` 24 GB, misc | **~0.9 TB** |
| `homes/margarethe` | `Backup` 121 GB (2008–2018 Windows PC), `Margarethe Backup May 2020` 5.8 GB | **~127 GB** |
| `backup` | `matrix` 201.5 GB (routine + models snapshots), `thor` 4 GB | **~205 GB** |
| `Container` | `kavita` 7.3 GB, `homebridge` 492 MB | **~8 GB** |
| `Share` | family docs (Financial, Estate Planning, Wedding, Wallpaper, old tax PDFs) | **~450 MB** |
| `Web` | static website (api/assets/css/js) | **~40 KB** |
| `Public` | IHM health/statistics files (active) | **~50 MB** |

### 1.3 Access facts (measured 2026-09-27)

- The `backup` SMB user (credentials in `~/.smbcredentials` on matrix) has **read access to all
  shares**, including `homes/chuck` and `homes/margarethe` — but **no write** (mkdir on
  `homes/chuck` → `NT_STATUS_ACCESS_DENIED`). Cleanup therefore cannot be done by the backup
  user; it needs DSM (File Station / API) with an admin or owner account.
- Lego's SMB server quirks (documented in [`matrix_backup.md`](matrix_backup.md)): SMB 2.1 only,
  aggressive idle-session kills, no real Unix modes, login rate-limiting after churn.
- matrix mounts only the `backup` share (`/mnt/lego`). Mounting other shares on matrix is
  restricted to fstab entries (unprivileged `mount` is policy-restricted).

---

## 2. Part 1 — Data cleanup on Lego

### 2.1 Comcast work directories (the requested cleanup)

Five dated snapshots of the old Comcast work laptop lived under
`homes/chuck/Documents/Work/`. Measured sizes (2026-09-27):

| Directory | Size | Disposition |
|---|---|---|
| `Comcast - Sept 2025` | **68.9 GB** | **KEEP** — most recent snapshot (OneDrive, Code, Onboarding, decks) |
| `Comcast - Jan 2023` | 49.5 GB | ~~DELETE~~ **DELETED 2026-09-27** (in parallel with this plan) |
| `Comcast - March 2021` | 25.4 GB | ~~DELETE~~ **DELETED 2026-09-27** (in parallel with this plan) |
| `Comcast - Dec 2023` | 1.3 GB | ~~DELETE~~ **DELETED 2026-09-27** (in parallel with this plan) |
| `Comcast - March 2023` | — | **deleted 2026-09-27 ~18:31** (observed during this work) |

**Status: DONE (2026-09-27 19:23 UTC verified — only `Comcast - Sept 2025` remains; ~76 GB
freed for the 3 dirs deleted after 18:39, per the volume free-block delta).** The deletions
were executed in parallel with this plan (presumably via DSM File Station); the backup user is
read-only so it could not have been the backup account.

### 2.2 Other cleanup candidates (user decision needed)

| Path | Size | What it is | Suggested |
|---|---|---|---|
| `homes/chuck/#recycle` | **966 GB** | Recycle bin: `CloudStation` (2016), `Documents` | **Empty the bin** (biggest single win on the NAS) |
| `homes/chuck/backup` | 488 GB | Backups of machines: `matt`, `virginia`, `dale`, `web`, `Container`, `DockerMounts` (2026-08) | Keep or delete — user call |
| `homes/chuck/iMac_Backup_Critical` | 76 GB | iMac Pictures/Bin/Music, **2016** | Likely delete — user call |
| `homes/margarethe/Backup` | 121 GB | Windows PC data, **2008–2018** | Likely delete — user call |
| `homes/margarethe/Margarethe Backup May 2020` | 5.8 GB | 2020 backup | Likely delete — user call |

Potential total: **~1.66 TB** freed (76 GB Comcast + 966 GB recycle + old Mac/PC backups).

### 2.3 How cleanup gets executed

The `backup` user is read-only, so deletion needs one of:

1. **DSM File Station** (Lego web UI) — user deletes the dirs manually. Simple, no credentials
   shared. (Recommended for the Comcast dirs.)
2. **DSM FileStation API** with an admin/owner account — I script the deletions (move-to-
   recycle first, verify, then purge). Needs the user to provide (or confirm) the DSM account.

Either way: **delete after the first Athena backup has run**, so the deleted data still has a
copy off-box (belt-and-braces; the Comcast dirs are old work data, but the recycle-bin
contents are unknown until inspected). *(The Comcast dirs were in fact deleted 2026-09-27
before the Athena pull exists — accepted: they are superseded work-laptop snapshots; the
Sept 2025 dir is retained.)*

---

## 3. Part 2 — Lego → Athena backup

### 3.1 Options

| | A. Hyper Backup on Athena (pull) | B. rsync mirror from matrix | C. rsyncd pull (Athena cron) |
|---|---|---|---|
| Mechanism | Athena's Hyper Backup connects to Lego's SMB shares as the `backup` user, backs them up to Athena's local disk | matrix rsyncs from `/mnt/lego` (more shares would need mounting) to an Athena SMB share | rsyncd on both NASes; Athena cron pulls |
| Matches "Athena pulls from Lego with the backup user" | ✅ exactly | ❌ matrix is the puller | ✅ (rsync instead of SMB) |
| New infra on Linux side | none | significant (7 more fstab mounts, wrapper changes) | none |
| Throughput / single-hop | direct Lego→Athena | double-hop through matrix (19 TB × 2 × 1 GbE ≈ 2 days initial, matrix is a SPOF) | direct |
| Restore model | Hyper Backup UI (versioned store) | plain file copy (true mirror) | plain file copy |
| Fits Athena's existing USB chain | ✅ (USB tasks are almost certainly Hyper Backup too) | ⚠️ mirror folder would need adding to USB tasks | ⚠️ same |
| Ongoing ops | DSM task, no scripts to babysit | new scripts + cron + mounts to maintain | rsyncd configs on both NASes |

**Recommendation (updated 2026-09-27): Option A (Hyper Backup pull) *plus* an rsync
browsable mirror on Athena.** The failover requirement (Infuse + SSH + "same structure" if
Lego is down) cannot be served by a Hyper Backup version store — it needs a plain mirrored
tree. So Athena holds (a) the Hyper Backup versioned store (history/safety net) and (b) a
`lego/` mirror share filled by an rsync script (Phase 3), scoped to Athena's smaller
capacity (§3 of `nas_backup_plan.md` has the sizing table). The mirror is the failover
copy; if space is tight the mirror wins it and Hyper Backup covers a subset.

Option B stays on the table for the subset of data that must be a **browsable mirror** (e.g. if
Athena is ever used as a direct restore target for matrix/thor data) — but mirroring all 19 TB
through matrix is not worth it.

### 3.2 Option A design

1. **Lego side (one-time, DSM):**
   - Confirm the `backup` user is enabled for SMB and can read the shares to be backed up
     (verified: all shares readable).
   - (Optional, if DSM allows) pin the backup user to the shares that matter, to keep the
     attack surface small.
2. **Athena side (DSM):**
   - **Hyper Backup → Create → File** (version: "File"), source: *Remote → SMB* →
     `//lego.local` (or IP) with the `backup` user.
   - One task per Lego share (or one task with multiple sources, depending on the DSM version):
     `Multimedia`, `Store`, `homes`, `backup`, `Share`, `Web`, `Container`, `Public`.
   - Destination: a dedicated folder on Athena, e.g. `Athena:/lego-backup/<share>/` (see §4
     for the aligned layout).
   - Schedule: **daily incremental** (Hyper Backup dedups + compresses between runs, so the
     steady-state delta is small); the 15 TB `Multimedia/video` initial sync will take a while
     at 1 GbE (~2 days one-way) — run the first sync overnight/weekend.
   - Retention: keep ≥ 4 weekly + monthly versions (match the matrix→Lego retention philosophy).
3. **Verify (first week):**
   - Spot-check: pick 3–5 files across shares, `sha256sum` on Athena's store (via Hyper Backup
     restore to a temp folder) against the Lego originals.
   - Check the task history for clean runs and the dedup/compression stats.

### 3.3 Updating the USB-drive backups

Athena's existing 3× USB backups (the user's old rsync shell script — "doesn't work very
well") will be **replaced by new scripts (Phase 3, `nas_backup_plan.md` §5)**: automatic
drive detection (the 3 drives are of varying sizes), size-fit check, incremental rsync with
post-run verify, logging, safe unmount; manual trigger ~monthly plus a separate FireSafe
mode (1–2×/year). The offline copies cover Athena's data, which now includes the Lego pull.

- **Cadence:** monthly (manual) + FireSafe 1–2×/year — USB drives are the offline tier.
- **Verify:** after the first extended USB run, list the drive contents and confirm the
  Athena tree is present; spot-check one file hash.

### 3.4 What happens to the chain

```
matrix ─▶ Lego ─▶ Athena ─▶ 3× USB
          (rsync, existing)   (Hyper Backup pull, new)   (Hyper Backup, updated)
```

- Off-site depth: data now exists in 4 places (origin, Lego, Athena, USB×3).
- The matrix→Lego `backup` share (205 GB of snapshots) is itself included in the Lego→Athena
  pull, so the USB tier also carries an off-box copy of the matrix/thor snapshots.

---

## 4. Structure alignment (Lego ↔ Athena)

Lego and Athena currently have different top-level organizations. Proposed canonical layout —
Athena mirrors Lego's share names under one root, so "where is X" is the same question on both
boxes:

```
Athena:
└── lego/                        # the new pull root (one folder per Lego share)
    ├── Multimedia/              # ← Lego share "Multimedia"
    ├── Store/                   # ← Lego share "Store"
    ├── homes/                   # ← Lego share "homes" (chuck + margarethe)
    ├── backup/                  # ← Lego share "backup" (matrix + thor snapshots)
    ├── Share/                   # ← Lego share "Share"
    ├── Web/                     # ← Lego share "Web"
    ├── Container/               # ← Lego share "Container"
    └── Public/                  # ← Lego share "Public"
```

- Hyper Backup's per-share tasks map 1:1 to these folders.
- If Athena has its own data (its own `homes`, media, etc.), it keeps those under its existing
  layout; the `lego/` root is the foreign-data namespace.
- **Needs Athena's current layout to finalize** (open question §5) — e.g. if Athena already
  has a `lego` folder or a different naming scheme, adopt whatever is closest and document it
  here.

---

## 5. Open questions (need user input)

1. **Athena access:** IP/hostname, is it powered on / on which segment? DSM web UI + SSH
   access (which account — admin, or a limited account with Hyper Backup + external-storage
   rights)?
2. **Athena capacity:** total + free space (Athena is *smaller* than Lego — the sizing table
   in `nas_backup_plan.md` §3 decides the mirror/Hyper Backup scope; a full 19 TB mirror
   needs ~20 TB+ free).
3. **Athena USB backups:** the 3 drives' sizes (they vary), the old rsync shell script (paste
   it — so the replacement keeps the layout/behavior), and what the FireSafe drive is (4th
   external drive? separate device?).
4. **Cleanup decisions (§2.2):** confirm keep/delete for `chuck/backup` (488 GB),
   `iMac_Backup_Critical` (76 GB), `margarethe/Backup` (121 GB),
   `Margarethe Backup May 2020` (5.8 GB); confirm emptying `chuck/#recycle` (966 GB).
5. **Cleanup execution:** do the Comcast deletions via File Station yourself, or provide a DSM
   account so I can script it (move-to-recycle → verify → purge)?
6. **Scope:** include all 8 Lego shares in the Athena pull, or a subset (e.g. skip `Public`)?

---

## 6. Execution phases

| Phase | Work | Depends on |
|---|---|---|
| 0 | Answer §5 questions; finalize share list + layout | user |
| 1 | **First Athena pull (pre-cleanup):** create the Hyper Backup task(s), run the initial sync (overnight/weekend for the 15 TB video share) | Phase 0 |
| 2 | Verify the pull (spot-check hashes, task history) | Phase 1 |
| 3 | **Cleanup on Lego** (Comcast dirs — done 2026-09-27 in parallel; approved candidates pending) — the remaining deletions happen once the off-box copy is verified | Phase 2 |
| 4 | Update the USB-drive tasks to include `lego/`; first extended USB run; verify | Phase 3 |
| 5 | Steady state: daily Athena pull (incremental), weekly USB; add to monitoring; document the final layout here and in the top-level README | Phase 4 |

**Ordering note:** the Athena pull runs *before* the cleanup on purpose — deletions happen
only once the off-box copy is verified.