#!/usr/bin/env bash
# enable-key-passthrough.sh — STEP 2 cutover: remove chuck's hard-copied
# LiteLLM key from Matrix pi config so caller-launched jobs MUST pass a key.
#
# Contract: docs/matrix_key_passthrough.md
# Companion: scripts/run-with-key.sh (the caller entry point)
#
# What it does:
#   1. Safety gate: abort if a `pi -p` session is running (its coordinator
#      reads auth.json per request; its pi-long-task workers use the current
#      models.json key).
#   2. Back up ~/.pi/agent/models.json and ~/.pi/agent/auth.json.
#   3. models.json: litellm-native apiKey literal -> "$LITELLM_API_KEY"
#      (env interpolation; workers then have NO fallback to chuck's key).
#   4. auth.json: drop the `key` field from the litellm entry (keep type +
#      env, so the base URL survives; the extension then falls through to the
#      LITELLM_API_KEY env chain).
#   5. Install ~/.pi/agent/litellm.key (chuck's key, chmod 600) for
#      interactive use: `LITELLM_API_KEY=$(cat ~/.pi/agent/litellm.key) pi`
#   6. Verify: no literal key remains in config; env-key auth works for both
#      the coordinator (extension) and the worker path (models.json).
#
# Idempotent: safe to re-run; skips steps already applied.
set -euo pipefail

AGENT_DIR="$HOME/.pi/agent"
MODELS="$AGENT_DIR/models.json"
AUTH="$AGENT_DIR/auth.json"
KEYFILE="$AGENT_DIR/litellm.key"
BASE_URL="https://llm.choukalos.com"
STAMP="$(date +%Y%m%d-%H%M%S)"

die() { echo "FATAL: $*" >&2; exit 1; }

# --- 1. safety gate ---------------------------------------------------------
if pgrep -f "pi -p" >/dev/null 2>&1; then
  pgrep -af "pi -p" >&2
  die "a pi -p session is running — wait for it to finish (its coordinator/workers depend on the current key config), then re-run."
fi

[[ -f "$MODELS" ]] || die "missing $MODELS"
[[ -f "$AUTH" ]] || die "missing $AUTH"

# --- 2. backups -------------------------------------------------------------
cp -a "$MODELS" "$MODELS.bak-$STAMP"
cp -a "$AUTH" "$AUTH.bak-$STAMP"
echo "backups: $MODELS.bak-$STAMP, $AUTH.bak-$STAMP"

# --- 3. models.json: literal -> $LITELLM_API_KEY ----------------------------
if grep -q '"apiKey": "\$LITELLM_API_KEY"' "$MODELS"; then
  echo "models.json already on \$LITELLM_API_KEY — skip"
else
  python3 - "$MODELS" <<'EOF'
import json, sys
p = sys.argv[1]
d = json.load(open(p))
prov = d["providers"]["litellm-native"]
if prov.get("apiKey", "").startswith("$"):
    print("models.json apiKey already env-interpolated — skip")
else:
    prov["apiKey"] = "$LITELLM_API_KEY"
    json.dump(d, open(p, "w"), indent=2)
    print("models.json litellm-native apiKey -> $LITELLM_API_KEY")
EOF
  chmod 600 "$MODELS"
fi

# --- 4. auth.json: drop the stored key --------------------------------------
if python3 -c "
import json,sys
e=json.load(open('$AUTH')).get('litellm',{})
sys.exit(0 if e.get('key') else 1)"; then
  python3 - "$AUTH" <<'EOF'
import json, sys
p = sys.argv[1]
d = json.load(open(p))
e = d["litellm"]
e.pop("key", None)
json.dump(d, open(p, "w"), indent=2)
print("auth.json litellm key removed (type+env kept)")
EOF
  chmod 600 "$AUTH"
else
  echo "auth.json litellm key already absent — skip"
fi

# --- 5. interactive key file -------------------------------------------------
if [[ ! -f "$KEYFILE" ]]; then
  # recover chuck's key from the freshest backup (it is gone from live config now)
  KEY="$(python3 -c "
import json,glob,os
best=''
for p in sorted(glob.glob('$AUTH.bak-*')):
    k=json.load(open(p)).get('litellm',{}).get('key','')
    if k: best=k
print(best)")"
  [[ -n "$KEY" ]] || die "could not recover chuck's key from auth.json backups"
  printf '%s' "$KEY" > "$KEYFILE"
  chmod 600 "$KEYFILE"
  echo "installed $KEYFILE (chmod 600)"
else
  echo "key file already present — skip"
fi

# --- 6. verification ---------------------------------------------------------
echo "--- verify: no literal key in live config"
LIVE_KEY="$(cat "$KEYFILE")"
for f in "$MODELS" "$AUTH"; do
  if grep -qF "$LIVE_KEY" "$f"; then
    die "literal key still present in $f"
  fi
done
echo "ok: no literal key in models.json / auth.json"

echo "--- verify: coordinator path (extension, env key, no stored credential)"
SBX="$(mktemp -d)"
cp "$AGENT_DIR/settings.json" "$AGENT_DIR/models-store.json" "$SBX/"
out="$(cd /tmp && PI_CODING_AGENT_DIR="$SBX" LITELLM_API_KEY="$LIVE_KEY" LITELLM_BASE_URL="$BASE_URL" \
  timeout 120 pi -p --model litellm/matrix-coder "Reply with exactly: OK" 2>&1 | grep -vE '^Warning' | tail -2)"
rm -rf "$SBX"
echo "$out"
echo "$out" | grep -q "OK" || die "coordinator env-key verification failed"

echo "--- verify: worker path (models.json litellm-native, env key)"
out2="$(cd /tmp && LITELLM_API_KEY="$LIVE_KEY" timeout 120 pi -p --model litellm-native/matrix-coder "Reply with exactly: OK" 2>&1 | grep -vE '^Warning' | tail -2)"
echo "$out2"
echo "$out2" | grep -q "OK" || die "worker-path env-key verification failed"

echo
echo "CUTOVER COMPLETE."
echo "  - caller jobs:   scripts/run-with-key.sh <caller-key> <pi args...>"
echo "  - interactive:   LITELLM_API_KEY=\$(cat ~/.pi/agent/litellm.key) pi"
echo "  - jobs launched without a key now fail loudly (No API key found) instead of billing chuck's key."
