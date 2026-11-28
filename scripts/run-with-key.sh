#!/usr/bin/env bash
# run-with-key.sh — launch a Matrix pi session with a CALLER's LiteLLM key.
#
# Contract: docs/matrix_key_passthrough.md
#
# The caller passes its own LiteLLM virtual key; all LLM usage by the launched
# session (coordinator + pi-long-task worker subagents) is attributed to that
# key at the LiteLLM proxy (the only auth boundary — vLLM has no auth).
#
# Mechanics (verified 2026-11-27, see contract doc §3):
#   - LITELLM_API_KEY env: used by the pi-provider-litellm extension
#     (coordinator) when no stored auth.json credential exists, and by the
#     `litellm-native` provider in ~/.pi/agent/models.json (workers, via
#     $ENV interpolation). Workers are in-process SDK sessions with
#     extensions disabled, so they only see the process env.
#   - LITELLM_BASE_URL env: pinned to the Thor LiteLLM proxy.
#   - NOTE: the `pi --api-key` flag does NOT reach extension-based providers
#     (verified: fake key produced "No API key found", not a 401). Do not rely
#     on it.
#
# Usage (two modes):
#   1. argv (local/interactive — original contract, unchanged):
#        run-with-key.sh <litellm-key> <pi args...>
#        e.g. run-with-key.sh sk-... -p "Run a long task with commits to ..."
#   2. stdin envelope (SSH forced-command mode — thor matrix-agent-launch.sh):
#        the thor authorized_keys line forces this script with NO arguments;
#        the job arrives on stdin:
#          line 1 = caller's LiteLLM virtual key
#          line 2 = pi args as a JSON array, e.g. ["-p","Reply OK"]
#        (T2 hygiene: the key never appears in argv/ps on either host.)
#
# The key never touches disk here; it lives only in this process tree's env
# and travels TLS to the LiteLLM proxy.
set -euo pipefail

if [[ $# -ge 2 ]]; then
  key="$1"
  shift
  pi_args=("$@")
elif [[ $# -eq 1 ]]; then
  echo "usage: run-with-key.sh <litellm-key> <pi args...>" >&2
  exit 2
elif [[ -t 0 ]]; then
  echo "run-with-key.sh: interactive stdin is not accepted — use the thor launcher (key via piped stdin)" >&2
  exit 2
else
  # SSH forced-command mode: key + JSON pi-args on stdin (2 lines)
  IFS= read -r key || { echo "run-with-key.sh: no key on stdin (keyless launches are rejected)" >&2; exit 2; }
  IFS= read -r args_json || args_json='[]'
  if ! python3 -c 'import json, sys
json.loads(sys.argv[1] or "[]")' "$args_json" 2>/dev/null; then
    echo "run-with-key.sh: malformed pi-args JSON on stdin line 2" >&2
    exit 2
  fi
  mapfile -d '' -t pi_args < <(python3 -c 'import json, sys
args = json.loads(sys.argv[1] or "[]")
sys.stdout.write("\0".join(args) + ("\0" if args else ""))' "$args_json")
fi

if [[ -z "$key" ]]; then
  echo "run-with-key.sh: empty key" >&2
  exit 2
fi

export LITELLM_API_KEY="$key"
export LITELLM_BASE_URL="http://192.168.4.54:4000"

export PATH="$HOME/.pi/agent/bin:$PATH"
exec pi "${pi_args[@]}"
