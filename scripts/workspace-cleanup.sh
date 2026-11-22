#!/usr/bin/env bash
# workspace-cleanup.sh — age-based cleanup for /home/chuck/workspace
#
# Convention (2026-11-22): all scratch/test artifacts live under
# /home/chuck/workspace/<task>/ (never /home/chuck/tmp or ad-hoc $HOME dirs).
# This job removes FILES older than MAX_AGE_DAYS (default 30) and the empty
# directories left behind, so the workspace can't accumulate unbounded
# test output. Run daily by cron (04:30); safe to run manually.
#
# Usage:
#   workspace-cleanup.sh            # delete files older than 30 days
#   workspace-cleanup.sh --dry-run  # report only, delete nothing
#   workspace-cleanup.sh --days 14  # custom threshold
#
# Log: ~/.local/state/workspace-cleanup/cleanup.log (one summary line per run)
set -euo pipefail

WORKSPACE="${WORKSPACE_DIR:-/home/chuck/workspace}"
MAX_AGE_DAYS=30
DRY_RUN=0

while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift ;;
    --days) MAX_AGE_DAYS="$2"; shift 2 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

case "$MAX_AGE_DAYS" in (*[!0-9]*|"") echo "--days must be a positive integer" >&2; exit 2 ;; esac

LOG_DIR="$HOME/.local/state/workspace-cleanup"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/cleanup.log"

if [ ! -d "$WORKSPACE" ]; then
  echo "$(date -Is) SKIP: $WORKSPACE does not exist" >> "$LOG"
  exit 0
fi

# Files strictly older than the threshold (mtime), then empty dirs bottom-up.
# -mindepth 1 never touches the workspace root itself.
if [ "$DRY_RUN" -eq 1 ]; then
  files=$(find "$WORKSPACE" -mindepth 1 -type f -mtime +"$MAX_AGE_DAYS" | wc -l)
  size=$(find "$WORKSPACE" -mindepth 1 -type f -mtime +"$MAX_AGE_DAYS" -printf '%s\n' 2>/dev/null | awk '{s+=$1} END {printf "%.1f", s/1e6}')
  echo "$(date -Is) DRY-RUN: would remove $files files (~${size} MB) older than ${MAX_AGE_DAYS}d under $WORKSPACE" | tee -a "$LOG"
  find "$WORKSPACE" -mindepth 1 -type f -mtime +"$MAX_AGE_DAYS" -printf '%TY-%Tm-%Td  %8s  %p\n' 2>/dev/null | sort >> "$LOG"
  exit 0
fi

before=$(find "$WORKSPACE" -mindepth 1 -type f | wc -l)
find "$WORKSPACE" -mindepth 1 -type f -mtime +"$MAX_AGE_DAYS" -delete
find "$WORKSPACE" -mindepth 1 -type d -empty -delete
after=$(find "$WORKSPACE" -mindepth 1 -type f | wc -l)
removed=$((before - after))
echo "$(date -Is) OK: removed $removed files older than ${MAX_AGE_DAYS}d ($before -> $after files under $WORKSPACE)" >> "$LOG"
