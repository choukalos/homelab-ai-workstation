#!/usr/bin/env bash
# backup-routine.sh — Track 1: routine data, weekly (cron, Sunday 03:00).
#
# Scope (per backup_todo.md):
#   data/media/projects                          — finished deliverables
#   data/comfyui/basedir/{input,output,user,config}
# Excluded by construction: media_jobs (14-day retention, re-runnable),
# data/logs (empty), homelab repo (on GitHub), model caches (Track 2).
#
# Snapshot: $BACKUP_ROOT/routine/YYYY-MM-DD/ (hardlink rotation, keep 4).
# Usage: backup-routine.sh [--dry-run]

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=backup-lib.sh
source "$SCRIPT_DIR/backup-lib.sh"

TRACK="routine"
SNAP_ROOT="$BACKUP_ROOT/$TRACK"
DRY_RUN=0
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=1

SOURCES=(
  "$DATA_ROOT/media/projects"
  "$DATA_ROOT/comfyui/basedir/input"
  "$DATA_ROOT/comfyui/basedir/output"
  "$DATA_ROOT/comfyui/basedir/user"
  "$DATA_ROOT/comfyui/basedir/config"
)

main() {
  local today prev dest s
  acquire_lock "$TRACK"
  check_setup
  for s in "${SOURCES[@]}"; do
    [[ -d "$s" ]] || die "source missing: $s"
  done
  ensure_mounted
  require_free_space "$BACKUP_ROOT" "$ROUTINE_MIN_FREE_GB" "$TRACK"

  today=$(date +%F)
  prev="$(prev_snapshot "$SNAP_ROOT" "$today")"
  dest="$SNAP_ROOT/$today"
  local drylabel=""
  [[ $DRY_RUN -eq 1 ]] && drylabel=" (DRY-RUN)"
  log "=== $TRACK backup start (${#SOURCES[@]} sources, snapshot $dest, link-dest ${prev:-none}$drylabel) ==="

  for s in "${SOURCES[@]}"; do
    sync_source "$s" "$dest" "$prev" "$DRY_RUN"
  done

  if [[ $DRY_RUN -eq 1 ]]; then
    log "=== $TRACK DRY-RUN done (nothing written) ==="
    return 0
  fi

  prune_snapshots "$SNAP_ROOT" "$ROUTINE_KEEP"
  verify_snapshot "$dest" "${SOURCES[@]}"
  log "=== $TRACK backup done ==="
}

main "$@"