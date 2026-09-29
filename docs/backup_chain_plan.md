# Backup Chain Plan — Lego → Athena (+ USB drives) and Lego data cleanup

> Created: 2026-09-27
> Status: **PLAN — pending Athena access + user decisions**
> **Execution plan (human-readable, Phase 1/2 for the user + Phase 3 summary): [`nas_backup_plan.md`](../nas_backup_plan.md)** — this doc is the detailed reference (inventory, SMB quirks, options analysis).
> Scope: (1) clean up stale data on Lego, (2) add a new backup leg: **Lego → Athena (Synology)**,
> (3) update Athena's existing backups to the 3 external USB drives so they cover the new data,
> (4) align the directory structure of Lego and Athena.

> **Design update 2026-09-27 (user requirements):** Athena must serve as a **failover** for
> Lego (browsable, same directory structure, Infuse + SSH-as-chuck access), Athena has **less
> space** than Lego (~4–5 TB free vs ~19 TB used on Lego), and the USB tier is **3 drives of
> varying sizes (~12 / 8 / 6 TB), manual ~monthly + FireSafe 1–2×/year** (replacing the
> user's old rsync shell script — whose layout is being replaced by the Lego-mirroring
> layout).
>
> **Final decision 2026-09-27 (user):** **Hyper Backup dropped** — no versioned store. The
> design is a **plain rsync mirror** on Athena (`lego/` share, file-for-file, deletions
> included) + **rsync copies of the mirror to the USB drives**. Mirror scope: everything
> except `Multimedia/video` (15.1 TB — doesn't fit; lowest priority) and `chuck/#recycle`
> (966 GB trash). Video goes to the USB drives (12 TB movies / 8 TB 4K split). 10%
> free-space flag on Athena. See `nas_backup_plan.md`.

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

**Final decision (2026-09-27, user): plain rsync mirror only — Hyper Backup dropped.**
The failover requirement (Infuse + SSH + "same structure") needs a plain mirrored tree, and
the user wants mirror semantics ("the files on Lego are on Athena, mirrored") without a
versioned store. Implementation: **Athena pulls** (cifs mount of the in-scope Lego shares as
the `backup` user + rsync `--delete` into `lego/<share>/`, daily cron, verify, logged —
Phase 3 scripts). Scope: everything except `Multimedia/video` (15.1 TB — doesn't fit in
~4–5 TB; lowest priority) and `chuck/#recycle` (trash). Trade-off accepted: no versioning —
deletions on Lego propagate to Athena/USB on the next sync.

Option B (rsync through matrix) and Option C (rsyncd) remain rejected: the pull happens
*on Athena* (single hop, matches the requested pull model, no matrix dependency).

### 3.2 Mirror design (final)

1. **Lego side (one-time):** the existing `backup` SMB user (verified: read access to all
   shares). No changes needed.
2. **Athena side:**
   - `lego/` shared folder with one subfolder per in-scope Lego share, same names:
     `Multimedia, Store, homes, backup, Container, Share, Web, Public` (no `video` inside
     `Multimedia` — excluded by scope).
   - `cifs-utils` package; the mirror script mounts `//192.168.5.100/<share>` per share
     (SMB 2.1, `soft`, same flags that work from matrix — see §1.3 quirks).
   - rsync per share: `-rt --delete --modify-window=1 --timeout=60` (+ `--exclude` for
     `#recycle`, `@Recently-Snapshot`, `@Recycle`), incremental, daily cron.
   - **10% free-space flag**: before/during a run, if the Athena volume is <10% free the
     script logs a prominent warning and the health check surfaces it (drive-upgrade signal).
3. **Verify:** post-run file counts + hash spot-checks per share (same discipline as the
   matrix backup scripts); the mirror is only "good" after a verified run.

### 3.3 Updating the USB-drive backups

The user's old rsync shell script ("pretty nasty") is being **replaced wholesale (Phase 3,
`nas_backup_plan.md` §6)** — including its layout: the drives will now **mirror Lego's
structure** (user decision 2026-09-27). Drive map (sizes to confirm when powered up):

| Drive | Scope |
|---|---|
| 12 TB | `Multimedia/video` — movies |
| 8 TB | `Multimedia/video` — 4K movies (existing practice: 4K on a separate drive) |
| ~6 TB | everything else (photo, music, homes, Store, backup, Container, Share, Web, Public ≈ 4 TB) |

Script behavior: auto-detect attached drives (size + label — no hardcoded mountpoints),
free-space check, incremental rsync, post-run verify, logging, safe unmount; manual trigger
~monthly plus a separate FireSafe mode (1–2×/year).

- **Cadence:** monthly (manual) + FireSafe 1–2×/year — USB drives are the offline tier.
- **Verify:** after the first run, list the drive contents and confirm the Lego-mirrored tree
  is present; spot-check one file hash.

### 3.4 What happens to the chain

```
matrix ─▶ Lego ─▶ Athena ─▶ 3× USB + FireSafe
          (rsync, existing)   (rsync mirror, new)   (rsync copy of the mirror, new)
```

- Data now exists in 4 places (origin, Lego, Athena, USB×3) — with mirror semantics
  (deletions propagate; no versioning — the accepted trade-off of dropping Hyper Backup).
- The matrix→Lego `backup` share (205 GB of snapshots) is included in the mirror, so the USB
  tier also carries an off-box copy of the matrix/thor snapshots.

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

- The mirror's per-share folders map 1:1 to these (the rsync script fills them).
- If Athena has its own data (its own `homes`, media, etc.), it keeps those under its existing
  layout; the `lego/` root is the foreign-data namespace.
- The USB drives use the **same structure** (per-drive scope subsets, §3.3) — so "where is X"
  is the same question on Lego, Athena, and the drives.

---

## 5. Open questions (need user input)

Answered 2026-09-27: Athena IP **192.168.5.110** (powered off at the time); free capacity
**~4–5 TB**; USB drives **~12 / 8 / ~6 TB** (exact sizes to confirm); TV/movies = lowest
priority; USB layout = mirror Lego's structure (old script's layout abandoned); 10%
free-space flag requested; Hyper Backup dropped.

Still open:

1. **Power on Athena** — so reachability, DSM version, and the current layout can be
   verified; SSH access for the Phase 3 scripts.
2. **USB drive sizes + identity** — confirm 12 / 8 / ~6 TB and which is which (labels?);
   is FireSafe a 4th drive or one of the three? How are 4K movies organized inside
   `Multimedia/video` (subfolder? naming?) — needed for the 12 TB vs 8 TB split rule.
3. **Cleanup decisions (§2.2):** keep/delete for `chuck/backup` (488 GB),
   `iMac_Backup_Critical` (76 GB), `margarethe/Backup` (121 GB),
   `Margarethe Backup May 2020` (5.8 GB); empty `chuck/#recycle` (966 GB — excluded from the
   mirror regardless)?
4. **Cleanup execution:** File Station by hand, or a DSM account so I can script it
   (move-to-recycle → verify → purge)? *(The Comcast deletions were done 2026-09-27 in
   parallel.)*
5. **Is Athena on 24/7?** — the daily mirror cron assumes yes; otherwise the sync moves to a
   manual/weekly trigger or runs from matrix.
6. **Scope edge cases:** include `Web`/`Public` in the mirror (tiny, default yes)?

---

## 6. Execution phases

| Phase | Work | Depends on |
|---|---|---|
| 0 | Power on Athena; answer §5; confirm drive sizes + 4K split rule | user |
| 1 | **Cleanup on Lego** (Comcast dirs — done 2026-09-27 in parallel; §2.2 decisions pending) — shrinks the mirror | Phase 0 |
| 2 | **Athena setup** (user, 10 min): `lego/` share + 8 subfolders, cifs-utils, SSH, `chuck` user, static IP, Infuse library | Phase 0 |
| 3 | **Mirror fill** (agent): mirror sync script, initial fill (~3–4 TB overnight), verify | Phases 1–2 |
| 4 | **USB copy** (agent): drive-map script, first monthly run + verify | Phase 3 |
| 5 | Steady state: daily mirror (incremental), monthly USB + FireSafe; health check with the 10% flag; failover runbook; document final layout here + README | Phase 4 |

**Ordering note:** the mirror fill runs *after* the cleanup on purpose — deletions happen
first so the mirror never has to un-copy them (rsync `--delete` would handle it, but there's
no reason to copy 1.6 TB of trash and delete it a day later).