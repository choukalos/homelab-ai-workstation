# NAS Backup Plan — Lego → Athena → USB drives

> Created: 2026-09-27 · Updated: 2026-09-27 — **Hyper Backup dropped** (user decision): plain
> rsync mirror only, no versioned store.
> Status: **waiting on Athena power-on** (192.168.5.110) + USB drive size confirmation
> **You do Phase 1 and Phase 2** (short checklists). **I do Phase 3** (scripts, §6).
> Detailed reference (inventory, SMB quirks, options history): `docs/backup_chain_plan.md`

## 1. What we're building

Athena (your Synology, 192.168.5.110) holds a **plain mirror** of the files on Lego —
browsable as normal files, same directory structure, usable for failover / Infuse / SSH.
The USB drives hold **copies of that mirror** (the offline tier).

```
Lego ──▶ Athena:/lego/  (rsync mirror, daily, pulled by Athena as the `backup` user)
            │
            └──▶ USB drives  (rsync copy of the mirror, manual ~monthly + FireSafe 1–2×/yr)
```

**What "mirror" means (the one trade-off, stated once):** what's on Lego is on Athena,
file-for-file — including deletions. A file deleted on Lego disappears from Athena (and from
the USB drives on the next run). There is no "restore last week's version" — that was the
Hyper Backup idea, dropped 2026-09-27. If you ever want versioning back, it's a separate
addition; the mirror design doesn't get in the way of it.

Goals (priority order):

1. **Failover** — if Lego goes down, Athena stands in: same structure, plain files.
2. **Infuse** — Apple TV Infuse points at Athena and browses the media the same way.
3. **SSH** — log in as `chuck` on Athena and use the files.
4. **Offline copies** — USB drives (monthly) + FireSafe (1–2×/year) carry the mirror.
5. **Space watch** — if Athena drops to **10% free**, the scripts flag it loudly (time to
   update drives if you want to keep this strategy).

**Key constraint:** Athena has ~4–5 TB free (reported 2026-09-27) vs ~19 TB on Lego. So the
mirror covers everything **except the 15.1 TB video library** (TV/movies = lowest priority,
per your call) — the video goes to the USB drives instead.

---

## 2. Sizing (measured on Lego, 2026-09-27)

| Lego share | Size | On Athena mirror? | Notes |
|---|---|---|---|
| `Multimedia/video` | **15.1 TB** | ❌ **no** (doesn't fit) | → USB drives instead (§3) |
| `Multimedia/photo` | 351 GB | ✅ | |
| `Multimedia/music` | 85 GB | ✅ | |
| `homes` | ~2.6 TB | ✅ **minus `chuck/#recycle`** (966 GB of trash — not worth mirroring) | chuck 2.45 TB, margarethe 127 GB |
| `Store` | ~0.9 TB | ✅ | comics/books/arcade/RPG |
| `backup` | 205 GB | ✅ | matrix + thor snapshots — restore path for the AI box |
| `Container` | 8 GB | ✅ | kavita, homebridge |
| `Share`, `Web`, `Public` | <1 GB | ✅ | |

Mirror size:

| Phase 1 outcome | Mirror size | Fits in Athena (4–5 TB free)? |
|---|---|---|
| Keep everything (only `#recycle` excluded) | **~4.0 TB** | tight at 4 TB, OK at 5 TB |
| Delete the old machine backups (~690 GB) | **~2.3–3.3 TB** | comfortable |

So Phase 1 directly buys mirror headroom — every GB you delete on Lego is a GB Athena
doesn't have to hold.

---

## 3. USB drive plan

Drives (to confirm when powered up): **12 TB, 8 TB, ~6 TB**. The 15.1 TB video library
doesn't fit on one drive — your existing practice (4K movies on a separate drive) becomes the
drive map:

| Drive | Scope | Layout |
|---|---|---|
| **12 TB** | `Multimedia/video` — movies | mirrors Lego: `Multimedia/video/...` |
| **8 TB** | `Multimedia/video` — 4K movies | mirrors Lego: `Multimedia/video/...` (4K subset) |
| **~6 TB** | everything else (photo, music, homes, Store, backup, Container, Share, Web, Public ≈ 4 TB) | mirrors Lego: `Multimedia/photo/...`, `homes/...`, `Store/...`, … |

- **Layout = mirror of Lego's structure** (your call, 2026-09-27) — the old script's layout
  is being abandoned; the old script itself is being replaced wholesale.
- **Cadence:** manual, ~monthly; FireSafe 1–2×/year (separate mode in the script).
- The 4K split rule needs one fact: how 4K movies are organized inside
  `Multimedia/video` (a subfolder? naming convention?) — §7.

---

## 4. Phase 1 — You: clean up Lego (shrinks the mirror)

Done (2026-09-27, verified): old Comcast work dirs deleted; `Comcast - Sept 2025` (68.9 GB)
kept. ~80 GB freed.

Remaining — DSM **File Station** → delete (or tell me which to keep; or give me a DSM
account and I'll script it with verify-before-purge):

| Task | Size | Contents | Suggestion |
|---|---|---|---|
| Empty `homes/chuck/#recycle` | **966 GB** | `CloudStation` (2016), `Documents` | **skip from the mirror regardless** (it's a trash bin); delete if you like (show hidden files in File Station to see it) |
| `homes/chuck/backup` | 488 GB | backups of `matt`, `virginia`, `dale`, `web`, `Container`, `DockerMounts` (Aug 2026) | keep if those machines are still in use |
| `homes/chuck/iMac_Backup_Critical` | 76 GB | iMac Pictures/Bin/Music from **2016** | likely delete |
| `homes/margarethe/Backup` | 121 GB | Windows PC data **2008–2018** | likely delete |
| `homes/margarethe/Margarethe Backup May 2020` | 5.8 GB | 2020 backup | likely delete |

**Phase 1 is done when:** you've made the keep/delete calls (or told me to skip the rest —
valid; the mirror just gets a bit bigger).

---

## 5. Phase 2 — You: set up Athena (short now — no Hyper Backup)

1. **Power on Athena** (192.168.5.110). Confirm it has a **static IP / DHCP reservation**
   so the address never changes.
2. **Create the mirror share:** Storage Manager → Shared Folder → **`lego`**.
   Permissions: read for `chuck`/Infuse, **write for root** (the sync script runs as root).
   Inside it, 8 top-level folders matching Lego's share names:
   ```
   lego/Multimedia   lego/Store   lego/homes   lego/backup
   lego/Share        lego/Web     lego/Container   lego/Public
   ```
   *(I can create the folders over SSH once you give me access — 8 folders, 2 minutes.)*
3. **SSH + user:** Control Panel → Terminal & SNMP → enable SSH; make sure a `chuck` user
   exists with read access to `lego`.
4. **Package Center → install `cifs-utils`** (so Athena can mount Lego's shares over SMB).
5. **Infuse:** add an SMB library on Athena → `lego` share → `Multimedia/...` (once the
   mirror has content; video will be empty on Athena by design — Infuse's video library can
   keep pointing at the USB drives or Lego as before).

That's it. Everything else is Phase 3.

---

## 6. Phase 3 — What I do (summary)

Once Athena is on and Phase 1–2 are done:

1. **Mirror sync script (Lego → Athena, runs on Athena, daily cron)**
   - Mounts the in-scope Lego shares (SMB 2.1, `backup` user — same server quirks as the
     matrix backup: session kills, no real permissions; the script is built for them:
     timeouts, retries, lock file).
   - rsyncs each share into `lego/<share>/` with `--delete` → always an exact mirror
     (your Phase 1 deletions propagate automatically).
   - Excludes: `#recycle`, `@Recently-Snapshot`, `@Recycle`.
   - **10% free-space flag:** checks Athena's volume before/after; if free < 10% it logs a
     loud warning and the health check reports it (your "time to update drives" signal).
   - Post-run verification (per-share file counts + hash spot-checks); fails loudly instead
     of leaving a half-synced mirror.
2. **USB copy script (Athena → drives, replaces your old rsync script)**
   - Auto-detects attached drives (size + label — no hardcoded mountpoints; the drives are
     different sizes).
   - Per-drive scope map from §3 (12 TB ← video, 8 TB ← 4K subset, 6 TB ← everything
     else), layout mirroring Lego's structure.
   - Free-space check before starting, incremental rsync (re-runs are fast), post-run
     verify, logging, clean unmount.
   - Two modes: **monthly** (one command, picks the attached drives) and **FireSafe**
     (1–2×/year).
3. **Failover runbook** — one page: point Infuse/SMB/SSH at Athena if Lego dies; restore
   matrix/thor data from `lego/backup/` snapshots; get Lego's data back if *Athena* dies
   (USB drives + re-point).
4. **Health check** — one command: mirror age (last successful sync), last USB run, Athena
   free-space % (with the 10% flag), Lego reachability.

---

## 7. Open questions (the remaining blockers)

1. **Power on Athena** (192.168.5.110) — so I can verify reachability, DSM version, and
   either do §5 with you or over SSH.
2. **USB drive sizes + identity** — confirm 12 / 8 / ~6 TB and which is which (labels?);
   is FireSafe a **4th** drive or one of the three?
3. **4K movie organization** — how are 4K movies marked inside `Multimedia/video`
   (subfolder? naming?) — needed for the 12 TB vs 8 TB split rule.
4. **Phase 1 keep/delete calls** (§4) — decides the mirror's final size.
5. **Is Athena on 24/7?** — the mirror cron assumes yes (if it's usually off, we move the
   sync to a manual/weekly trigger or run it from matrix instead).
6. *(Optional)* Lego DSM account — for scripted Phase 1 cleanup.

**Order:** power on Athena → answer §7 → Phase 1 (or scope call) → Phase 2 (10 minutes) →
I do Phase 3 (mirror fill first — the big one — then USB script, runbook, health check).