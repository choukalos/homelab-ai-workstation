#!/usr/bin/env bash
# backup-lib.sh — shared logic for the Lego backup scripts.
# Sourced by backup-routine.sh / backup-models.sh / backup-restore.sh.
#
# Design (see backup_todo.md):
#   - rsync over a CIFS mount of the Lego NAS (fstab, systemd automount)
#   - dated snapshots with --link-dest hardlink rotation (delta cost, full view)
#   - prune by count, post-run dry-run verify, flock against overlap

set -euo pipefail

LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=backup.conf
source "$LIB_DIR/backup.conf"

STATE_DIR="${STATE_DIR:-$HOME/.local/state/backup}"
LOG_FILE="${LOG_FILE:-$STATE_DIR/backup.log}"
mkdir -p "$STATE_DIR"

ts() { date '+%Y-%m-%d %H:%M:%S'; }
log() {
  local line="[$(ts)] $*"
  echo "$line"
  echo "$line" >> "$LOG_FILE"
}
die() {
  log "FATAL: $*"
  exit 1
}

# ── setup preflight ────────────────────────────────────────────────────────
check_setup() {
  [[ -f "$CRED_FILE" ]] || die "credentials file $CRED_FILE missing — create it (username=/password=) and chmod 600"
  if grep -qE '^password=CHANGE_ME' "$CRED_FILE"; then
    die "credentials $CRED_FILE still contain the CHANGE_ME placeholder — set the real Lego password, then run: sudo $LIB_DIR/backup-setup.sh <share-name>"
  fi
  [[ "$LEGO_SHARE" != "CHANGE_ME" ]] || die "LEGO_SHARE in backup.conf is still CHANGE_ME — run: sudo $LIB_DIR/backup-setup.sh <share-name>"
  command -v mount.cifs >/dev/null || die "mount.cifs missing — install cifs-utils"
}

# ── mount handling ─────────────────────────────────────────────────────────
# noauto + x-systemd.automount: a downed NAS can't wedge boot; first access
# can trigger the systemd automount (no sudo needed).
# Lego's macOS SMB server kills IDLE sessions aggressively (observed 18 s –
# 3 min of idle; active I/O keeps them alive) and rate-limits new sessions
# after churn. A dead session on a `hard` mount blocks all I/O forever —
# that wedges the kernel (zombie D-state processes + stuck umount -l), so
# the fstab line uses `soft`: I/O fails with an error instead of hanging;
# the script's verify step catches any partial snapshot and the next run
# resumes (hardlink rotation makes re-runs cheap). A hung cifs mount would
# block `ls` forever anyway — hence the timeout guards. Recovery: lazy
# umount + remount via the scoped NOPASSWD sudoers rule installed by
# backup-setup.sh. Both steps are bounded: umount -l can hang while
# processes hold I/O to a dead superblock (only a reboot clears that), and
# the server's rate-limit can make mount take minutes — but neither may
# hang forever (a forever-hold would wedge the lock and all later runs).
ensure_mounted() {
  if timeout 15 ls "$MOUNT" >/dev/null 2>&1; then
    return 0
  fi
  log "mount of $MOUNT missing or unresponsive — lazy-remounting (sudo)"
  timeout 30 sudo -n umount -l "$MOUNT" 2>/dev/null || true
  sleep 1
  if grep -qE "^[^ ]+ $MOUNT " /proc/mounts 2>/dev/null; then
    die "stale mount of $MOUNT still attached (umount -l timed out) — wedged CIFS state, reboot the host to clear it"
  fi
  if ! timeout 600 sudo -n mount "$MOUNT" >>"$LOG_FILE" 2>&1; then
    die "remount of $MOUNT failed after 10 min (sudo -n mount $MOUNT) — check share/credentials/network, and that the sudoers rule exists (sudo $LIB_DIR/backup-setup.sh $LEGO_SHARE)"
  fi
  timeout 15 ls "$MOUNT" >/dev/null 2>&1 || die "$MOUNT still unresponsive after remount — check the NAS (sudo dmesg | grep -i cifs)"
  log "remounted $MOUNT"
}

require_free_space() { # $1=dir $2=min GB $3=track
  local dir="$1" min_gb="$2" track="$3"
  local avail_kb avail_gb
  avail_kb=$(df -Pk "$dir" | awk 'NR==2 {print $4}')
  avail_gb=$((avail_kb / 1024 / 1024))
  if (( avail_gb < min_gb )); then
    die "insufficient free space for $track: ${avail_gb}GB free, need >= ${min_gb}GB"
  fi
  log "free-space check OK: ${avail_gb}GB free (need >= ${min_gb}GB)"
}

# ── snapshot core ──────────────────────────────────────────────────────────
# Latest dated snapshot strictly older than $2 (empty string if none).
prev_snapshot() { # $1=snap_root $2=today
  local root="$1" today="$2"
  [[ -d "$root" ]] || { echo ""; return 0; }
  ( cd "$root" && ls -1 2>/dev/null | grep -E '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' | sort | awk -v t="$today" '$0 < t' | tail -1 ) || true
}

# Rsync one source into the snapshot, mirroring its path under DATA_ROOT.
# $4 = dry-run (0/1). With --link-dest, unchanged files become hardlinks to
# the previous snapshot (zero extra disk); changed/new files take real space.
# Flags (tuned for this CIFS mount, verified 2026-09-26):
#   -rt (NOT -a): the share can't represent real Unix modes (macOS SMB server
#     reports 755 for everything), so -a would re-transfer every file on every
#     run trying to fix permissions. Permissions are NOT preserved in backups.
#   --modify-window=1: CIFS truncates mtime to 100ns; without this, rsync's
#     nanosecond mtime comparison defeats --link-dest hardlinking.
sync_source() { # $1=src $2=dest_root $3=prev_root_or_empty $4=dry_run
  local src="$1" dest_root="$2" prev="$3" dry="${4:-0}"
  local rel="${src#"$DATA_ROOT"/}"
  local dest="$dest_root/$rel"
  local prevsub=""
  [[ -n "$prev" && -d "$prev/$rel" ]] && prevsub="$prev/$rel"
  local args=(-rt --delete --modify-window=1 --timeout=60)
  [[ -n "$prevsub" ]] && args+=(--link-dest="$prevsub")
  local drylabel=""
  [[ "$dry" == "1" ]] && { args+=(-n); drylabel=" (dry-run)"; }
  mkdir -p "$dest"
  log "rsync $src -> $dest${prevsub:+ (link-dest $prevsub)}$drylabel"
  rsync "${args[@]}" "$src/" "$dest/"
}

prune_snapshots() { # $1=snap_root $2=keep
  local root="$1" keep="$2" old d
  [[ -d "$root" ]] || return 0
  old=$( ( cd "$root" && ls -1 | grep -E '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' | sort | head -n -"$keep" ) )
  if [[ -n "$old" ]]; then
    while IFS= read -r d; do
      log "pruning old snapshot $root/$d"
      rm -rf -- "$root/$d"
    done <<< "$old"
  fi
}

# Post-run verify: a dry-run re-rsync must report 0 files that would change.
# WARN rather than fail: CIFS mtime granularity can produce false positives.
# Same flags as sync_source (-rt --modify-window=1) so the check is consistent.
verify_snapshot() { # $1=dest_root $2...=sources
  local dest_root="$1"; shift
  local src rel n total=0
  for src in "$@"; do
    rel="${src#"$DATA_ROOT"/}"
    n=$(rsync -rtn --delete --modify-window=1 --timeout=60 "$src/" "$dest_root/$rel/" | wc -l)
    total=$((total + n))
  done
  if (( total == 0 )); then
    log "post-run verify: PASS (0 changed files)"
  else
    log "post-run verify: WARN ($total files would still change — re-run or check CIFS mtime granularity)"
  fi
}

# ── locking ────────────────────────────────────────────────────────────────
acquire_lock() { # $1=track
  exec 9>"$STATE_DIR/lock-$1"
  flock -n 9 || die "another $1 backup is already running (lock: $STATE_DIR/lock-$1)"
}