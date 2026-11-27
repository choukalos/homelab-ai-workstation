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
# Usage:
#   run-with-key.sh <litellm-key> <pi args...>
#
# Examples:
#   run-with-key.sh sk-... -p "Run a long task with commits to ..."
#   run-with-key.sh sk-... --model litellm/matrix-coder -p "..."
#
# The key never touches disk here; it lives only in this process tree's env
# and travels TLS to the LiteLLM proxy.
set -euo pipefail

if [[ $# -lt 2 ]]; then
  echo "usage: run-with-key.sh <litellm-key> <pi args...>" >&2
  exit 2
fi
key="$1"
shift

if [[ -z "$key" ]]; then
  echo "run-with-key.sh: empty key" >&2
  exit 2
fi

export LITELLM_API_KEY="$key"
export LITELLM_BASE_URL="https://llm.choukalos.com"

exec pi "$@"
