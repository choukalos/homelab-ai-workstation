#!/usr/bin/env bash
# pi.sh — launch an interactive Matrix pi session with chuck's key.
#
# Installed as an alias in ~/.bashrc: alias pi='/home/chuck/homelab/scripts/pi.sh'
# so plain `pi` in the terminal goes through this wrapper. The alias only
# expands in interactive shells; the `exec pi` below runs non-interactive and
# resolves the real binary via PATH — no recursion.
#
# Companion to scripts/run-with-key.sh (caller-launched jobs). This is the
# interactive path from docs/matrix_key_passthrough.md §4: it exports chuck's
# key from the cutover-installed ~/.pi/agent/litellm.key, so you are never
# prompted for a key and never re-save it into auth.json. That matters: a
# stored auth.json credential WINS over the env for the coordinator path
# (contract doc §3), so a prompt-and-re-save silently reverts the pass-through
# cutover for caller jobs.
#
# Usage:
#   pi-interactive.sh [pi args...]
#   pi-interactive.sh --model litellm/matrix-coder
#   pi-interactive.sh -c        # continue last session
#
# If LITELLM_API_KEY is already exported in your shell, it is honored as-is
# (useful for testing another virtual key); the key file is only the fallback.
# LITELLM_BASE_URL defaults to the Thor proxy but can be overridden the same way.
set -euo pipefail

KEYFILE="$HOME/.pi/agent/litellm.key"
BASE_URL="https://llm.choukalos.com"

if [[ -z "${LITELLM_API_KEY:-}" ]]; then
  if [[ ! -f "$KEYFILE" || ! -s "$KEYFILE" ]]; then
    echo "pi.sh: no LITELLM_API_KEY in env and $KEYFILE is missing or empty." >&2
    echo "  fix: re-run scripts/enable-key-passthrough.sh (reinstalls the key file" >&2
    echo "       from the newest auth.json backup), or export LITELLM_API_KEY yourself." >&2
    exit 1
  fi
  export LITELLM_API_KEY="$(cat "$KEYFILE")"
fi
export LITELLM_BASE_URL="${LITELLM_BASE_URL:-$BASE_URL}"

exec pi "$@"
