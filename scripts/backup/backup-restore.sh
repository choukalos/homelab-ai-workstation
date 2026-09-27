#!/usr/bin/env bash
# backup-restore.sh — restore a snapshot back to its source paths.
#
# Usage: backup-restore.sh <routine|models> [YYYY-MM-DD] [--apply]
#   (no date)      -> latest snapshot
#   (no --apply)   -> DRY-RUN (default; shows what would change)
#   --apply        -> actually rsync the snapshot back to /home/chuck/data/
#
# Note: restore is a plain rsync WITHOUT --delete — it never removes live
# files that are absent from the snapshot. To restore a single subtree,
# rsync it manually:  rsync -av <snap>/comfyui/basedir/models/tts/ /home/chuck/data/comfyui/basedir/models/tts/

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=backup-lib.sh
source "$SCRIPT_DIR/backup-lib.sh"

TRACK=""
DATE=""
APPLY=0

parse_args() {
  for a in "$@"; do
    case "$a" in
      --apply) APPLY=1 ;;
      routine|models) TRACK="$a" ;;
      20[0-9][0-9]-[0-9][0-9]-[0-9][0-9]) DATE="$a" ;;
      *) die "unknown argument: $a (usage: backup-restore.sh <routine|models> [YYYY-MM-DD] [--apply])" ;;
    esac
  done
  [[ -n "$TRACK" ]] || die "usage: backup-restore.sh <routine|models> [YYYY-MM-DD] [--apply]"
}

main() {
  parse_args "$@"
  acquire_lock "restore-$TRACK"
  check_setup

  local snap_root="$BACKUP_ROOT/$TRACK"
  if [[ -z "$DATE" ]]; then
    DATE="$(latest_snapshot "$snap_root")"
    [[ -n "$DATE" ]] || die "no snapshots found under $snap_root (nothing to restore)"
  fi
  local src="$snap_root/$DATE"
  [[ -d "$src" ]] || die "snapshot not found: $src"

  ensure_mounted
  local mode="DRY-RUN"
  [[ $APPLY -eq 1 ]] && mode="APPLY"
  log "=== restore $TRACK snapshot $DATE -> $DATA_ROOT/ ($mode) ==="

  if [[ $APPLY -eq 0 ]]; then
    local n
    n=$(sudo -n "$RSYNC_PRIV" restore "$src" "$DATA_ROOT" --dry-run | wc -l)
    log "dry-run: $n files would be copied/changed (no --delete: live files are never removed)"
    log "=== restore DRY-RUN done (nothing written) — re-run with --apply to execute ==="
    return 0
  fi

  # -rt (not -a): CIFS reports every file as mode 755, so -a would chmod
  # restored files to 755. New files get default umask perms (644); existing
  # local files are never chmod'd. Additive: no --delete, live files kept.
  # Runs as root via $RSYNC_PRIV so root-owned local files (e.g. the HF model
  # hub dirs) can be overwritten by snapshot contents.
  sudo -n "$RSYNC_PRIV" restore "$src" "$DATA_ROOT"
  log "=== restore done ==="
}

main "$@"