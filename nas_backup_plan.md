# NAS Backup Plan — Lego → Athena → USB drives

> Created: 2026-09-27 · Status: **waiting on Athena access + capacity numbers** (§7)
> **You do Phase 1 and Phase 2** (checklists below). **I do Phase 3** (scripts, §6).
> Detailed reference (full inventory, SMB quirks, options analysis): `docs/backup_chain_plan.md`

## 1. What we're building

Athena (your Synology) becomes a second copy of the Lego NAS, plus the offline tier:

```
matrix / thor ──▶ Lego ──▶ Athena ──▶ 3× USB drives + FireSafe
 (done)           (source)  (Phase 1-3)   (Phase 3, replaces your old rsync script)
```

Goals, in priority order:

1. **Failover** — if Lego goes down, Athena can stand in: same directory structure,
   browsable as plain files (not a backup-archive format).
2. **Infuse** — Apple TV Infuse can point at Athena and browse the media the same way.
3. **SSH access** — you can log in as `chuck` on Athena and use the files.
4. **Offline copies** — the 3 USB drives (manual, ~monthly) and the FireSafe drive
   (1–2×/year) get a copy of what's on Athena.
5. **Versioning** — Hyper Backup on Athena keeps dated versions of the pulled data
   (restore any older version via the DSM UI).

**Key constraint:** Athena has less space than Lego (~19 TB used on Lego). So we choose
*what* gets mirrored vs. versioned vs. USB'd — §3 shows what fits at each size.

---

## 2. How the pieces fit

| Piece | Where | What it is | Who sets it up |
|---|---|---|---|
| **Hyper Backup task** (pulls Lego via SMB as the `backup` user) | Athena, local disk | *Versioned* copy: dated versions, dedup+compression, restore via DSM UI. Not directly browsable. | **You** (Phase 2a) |
| **Mirror share** `lego/` | Athena, local disk | *Browsable* copy: plain files, same folder names as Lego's shares. This is the failover/Infuse/SSH copy. Filled by an rsync script. | **You** create the share + folders (Phase 2b); **I** write the fill script (Phase 3) |
| **USB + FireSafe backup** | 3 USB drives + FireSafe | *Offline* copy of Athena's data, dated, verified. Replaces your old rsync shell script. | **I** (Phase 3) |

Why both Hyper Backup *and* a mirror? Hyper Backup's output is a versioned store — great for
"restore last week's file", but you can't point Infuse at it or fail over to it. The mirror is
the live stand-in; Hyper Backup is the safety net with history. If Athena's space can't hold
both, §3 shows the trade-off — the mirror (failover) wins the space, Hyper Backup can cover a
smaller subset.

---

## 3. Sizing (measured on Lego, 2026-09-27)

| Lego share | Size | Failover value | Notes |
|---|---|---|---|
| `Multimedia/video` | **15.1 TB** | high (Infuse) | the whole problem child |
| `Multimedia/photo` | 351 GB | high | |
| `Multimedia/music` | 85 GB | medium | |
| `homes` | ~2.6 TB | high | chuck 2.45 TB (Docs 1.7 TB, `#recycle` 966 GB, backup 488 GB, iMac 76 GB) + margarethe 127 GB |
| `Store` | ~0.9 TB | medium | comics/books/arcade/RPG (Kavita-style content) |
| `backup` | 205 GB | **high** | matrix + thor snapshots — restore path for the AI box |
| `Container` | 8 GB | medium | kavita, homebridge data |
| `Share`, `Web`, `Public` | <1 GB | low | |
| **Total** | **~19 TB** | | |

What fits on Athena (mirror = raw size; Hyper Backup ≈ raw size for the first full pass,
media compresses poorly):

| Athena free space | Mirror (browsable failover) | Hyper Backup (versioned) |
|---|---|---|
| ≥ 24 TB | everything (19 TB) | everything |
| ~10 TB | everything **except video** (~3.9 TB) | a subset (e.g. homes + backup + Store + photo) |
| ~5 TB | homes + backup + Multimedia/photo+music (~3.2 TB) | homes + backup only (~2.8 TB) |
| less | pick the shares that matter most — **your call, §7** | same |

*(Video is the one thing that's genuinely irreplaceable if it's personal content — if Athena
can't hold it, consider whether the USB drives should carry it instead.)*

---

## 4. Phase 1 — You: clean up Lego (save space + shrink the backup)

Done already (2026-09-27, verified): the old Comcast work dirs — Jan 2023, March 2021,
Dec 2023, March 2023 deleted; **`Comcast - Sept 2025` (68.9 GB) kept**. ~80 GB freed.

Remaining tasks. Each one is also *less data to back up to Athena*.

### 1.1 Empty chuck's recycle bin — **966 GB** (biggest win)

- DSM → **File Station** → `homes/chuck` → enable *show hidden files* (view menu) →
  `#recycle` → select all → delete (permanently).
- Contents: `CloudStation` (2016), `Documents`. Nothing recent.
- ⚠️ This is a permanent delete — if you're unsure, skip it and just exclude it from the
  Athena backup (I can scope the backup to skip `#recycle`).

### 1.2 Decide on the old machine backups — **~690 GB total**

| Path | Size | What it is | Suggestion |
|---|---|---|---|
| `homes/chuck/backup` | 488 GB | Backups of `matt`, `virginia`, `dale`, `web`, `Container`, `DockerMounts` (Aug 2026) | keep if those machines are still in use, else delete |
| `homes/chuck/iMac_Backup_Critical` | 76 GB | iMac Pictures/Bin/Music from **2016** | likely delete |
| `homes/margarethe/Backup` | 121 GB | Windows PC data from **2008–2018** | likely delete |
| `homes/margarethe/Margarethe Backup May 2020` | 5.8 GB | 2020 backup | likely delete |

Same mechanics as 1.1 (File Station → delete). Tell me which you keep — anything kept gets
included in the Athena backup scope; anything deleted just disappears.

### 1.3 (Optional) Let me script it

If you'd rather not click through File Station: give me a Lego DSM account (or confirm an
existing one) and I'll do it via the API with move-to-recycle → verify → purge, so nothing
gets deleted without a check.

**Phase 1 is done when:** you've made the keep/delete calls (or told me to skip the rest —
that's a valid outcome too; the Athena backup can just exclude the junk).

---

## 5. Phase 2 — You: set up Athena

### 2a. Hyper Backup task (the versioned copy)

1. **DSM → Package Center** → make sure **Hyper Backup** is installed (usually is).
2. **Hyper Backup → Create → File** (the "File" template — backs up files/folders; the
   "Hyper" template is for DSM system settings, not what we want).
3. **Name:** `Lego-pull`.
4. **Source:** *Remote* → **SMB**:
   - Server: `lego.local` (or `192.168.5.100`), port 445
   - User: `backup`, password: *(the one in matrix's `~/.smbcredentials`)*
   - Select the shares to pull (per §3 scope — e.g. all 8, or the subset you chose).
5. **Destination:** *Local* → pick the volume with the most space, folder e.g. `lego-backup`.
6. **Schedule:** daily, e.g. 02:00. The *first* run is a long full sync (19 TB ≈ 2 days at
   ~100 MB/s over 1 GbE) — start it on a weekend and let it run.
7. **Retention:** e.g. keep 1/day for 7 days, 1/week for 4 weeks, 1/month for 12 months
   (match whatever you like — the point is having several restore points).
8. Leave **encryption off** (same accepted-risk call as matrix→Lego: LAN-only, credential
   access). It's a toggle in the task if you change your mind later.
9. **Run it** (Start Now) and watch the first night in the task history.

### 2b. The mirror share (the failover / Infuse copy)

1. **Storage Manager → Shared Folder → Create**: name it **`lego`** (or `lego-mirror`).
   Permissions: read for the users/Infuse, **write for root** (the rsync script runs as root).
2. Create the top-level folders inside it — **one per Lego share, same names**:
   ```
   lego/Multimedia   lego/Store   lego/homes   lego/backup
   lego/Share        lego/Web     lego/Container   lego/Public
   ```
   (I can create these over SSH once you give me access — or do it in File Station, 8
   folders, 2 minutes.)
3. **Infuse:** add an SMB library on Athena pointing at the `lego` share →
   `Multimedia/video/...` — same browse path as Lego.
4. **SSH as chuck:** Control Panel → User & Permissions → make sure a `chuck` user exists
   (admin or regular with read on `lego`); Control Panel → Terminal & SNMP → enable SSH.
5. **Package Center → install `cifs-utils`** (needed so the mirror script can mount Lego's
   shares on Athena).
6. Give Athena a **static IP** (or DHCP reservation) so Lego's address never changes.

### 2c. Verify (10 minutes, once)

- In DSM **Hyper Backup → `Lego-pull` → Restore**: restore 3–5 small files from different
  shares to a temp folder; confirm they open and match the originals.
- Once I've run the mirror script (Phase 3): browse `lego/` in File Station, confirm the
  tree matches Lego, and point Infuse at it.

---

## 6. Phase 3 — What I do (summary)

Once Athena is reachable and Phase 1–2 are done, I build and run:

1. **Mirror sync script (Lego → Athena, runs on Athena)**
   - Mounts the in-scope Lego shares (SMB, `backup` user), rsyncs each into `lego/<share>/`
     with `--delete` so the mirror is always an exact copy (including your Phase 1 deletions).
   - Scheduled (daily) via Athena's cron; incremental, so after the first fill it's fast.
   - Same safety discipline as the matrix backup scripts: locked, logged, path-validated,
     post-run verification (file counts + hash spot-checks), and it reports failure
     loudly instead of half-syncing.
2. **USB + FireSafe backup script (Athena → drives, replaces your old rsync script)**
   - Detects which drive(s) are attached (they're different sizes — no hardcoded
     mountpoints or sizes), checks free space before starting, rsyncs Athena's data with a
     stable layout so re-runs are incremental, verifies after, logs, and unmounts cleanly.
   - Two modes: **monthly** (you run one command, it picks the right drive) and
     **FireSafe** (1–2×/year, its own flag/target).
   - I'll want a copy of your old script first, so the new one keeps the layout/behavior
     you already expect (just paste it when convenient).
3. **Failover runbook** — a one-page doc: how to point Infuse/SMB/SSH at Athena if Lego dies,
   how to restore matrix/thor data from the `backup/` snapshots, and how to get Lego's data
   back if *Athena* dies (USB/FireSafe + Hyper Backup restore).
4. **Monitoring** — a health check (mirror age, last USB run, Hyper Backup status) you can
   run any time; optionally hooked into something you already watch.

---

## 7. Open questions (the blockers)

1. **Athena's IP / reachability** — it wasn't on the LAN when I scanned (2026-09-27).
   Powered on? Which subnet?
2. **Athena's free capacity** — the number that decides §3 scope (mirror subset,
   Hyper Backup subset).
3. **USB drive sizes** — the 3 drives + FireSafe: what are their capacities? (so the
   script can match data to drive)
4. **Failover scope** — which shares *must* be on Athena if Lego dies? (My default:
   everything except video if space is tight; video to USB instead?)
5. **Your old USB rsync script** — paste it so I preserve its layout/behavior.
6. **Lego DSM account** — for Phase 1 scripting (optional; File Station works too).
7. **Athena DSM version** (7.x?) — so the menu names in Phase 2 match exactly.

**Order of operations:** answer §7 → you do Phase 1 (or tell me the scope) → you do Phase 2
→ I do Phase 3 (mirror fill first, then USB script, then runbook + monitoring).