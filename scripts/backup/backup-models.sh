#!/usr/bin/env bash
# backup-models.sh — Track 2: production model set, occasional (on-demand).
#
# Manifest-driven: rsyncs exactly the paths in model_manifest.txt
# (~110 GB). Same --link-dest rotation as Track 1; keep 2 snapshots
# (current + previous model set for rollback).
#
# Usage: backup-models.sh [--dry-run]
#   --dry-run: rsync no-op (still verifies setup, mount, and free space).

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=backup-lib.sh
source "$SCRIPT_DIR/backup-lib.sh"

TRACK="models"
SNAP_ROOT="$BACKUP_ROOT/$TRACK"
MANIFEST_FILE="$SCRIPT_DIR/model_manifest.txt"

DRY_RUN=0
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=1

load_manifest() {
  [[ -f "$MANIFEST_FILE" ]] || die "manifest missing: $MANIFEST_FILE"
  local line
  SOURCES=()
  while IFS= read -r line || [[ -n "$line" ]]; do
    # strip whitespace; skip blanks + comments
    line="${line#"${line%%[![:space:]]*}"}"
    line="${line%"${line##*[![:space:]]}"}"
    [[ -z "$line" || "$line" == \#* ]] && continue
    SOURCES+=("$line")
  done < "$MANIFEST_FILE"
  (( ${#SOURCES[@]} > 0 )) || die "manifest is empty: $MANIFEST_FILE"
  for s in "${SOURCES[@]}"; do
    [[ "$s" == "$DATA_ROOT"/* ]] || die "manifest path not under $DATA_ROOT: $s"
    [[ -d "$s" ]] || die "manifest source missing: $s"
  done
}

main() {
  local today prev dest
  acquire_lock "$TRACK"
  check_setup
  load_manifest
  ensure_mounted
  require_free_space "$BACKUP_ROOT" "$MODELS_MIN_FREE_GB" "$TRACK"

  today=$(date +%F)
  prev="$(prev_snapshot "$SNAP_ROOT" "$today")"
  dest="$SNAP_ROOT/$today"
  local drylabel=""
  [[ $DRY_RUN -eq 1 ]] && drylabel=" (DRY-RUN)"
  log "=== $TRACK backup start (${#SOURCES[@]} manifest paths, snapshot $dest, link-dest ${prev:-none}$drylabel) ==="

  for s in "${SOURCES[@]}"; do
    sync_source "$s" "$dest" "$prev" "$DRY_RUN"
  done

  if [[ $DRY_RUN -eq 1 ]]; then
    log "=== $TRACK DRY-RUN done (nothing written) ==="
    return 0
  fi

  prune_snapshots "$SNAP_ROOT" "$MODELS_KEEP"
  verify_snapshot "$dest" "${SOURCES[@]}"
  log "=== $TRACK backup done ==="
}

main "$@"