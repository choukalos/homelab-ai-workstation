#!/usr/bin/env bash
# qwen21_upgrade_matrix.sh — matrix-side prep for the Qwen-Image-2.1 upgrade.
#
# RUN ON MATRIX (192.168.4.55), after `cd /home/chuck/homelab && git pull`:
#   bash scripts/qwen21_upgrade_matrix.sh
#
# What it does (idempotent, re-runnable; logs to /home/chuck/data/comfyui/run/qwen21_upgrade.log):
#   1. records the current ComfyUI commit (rollback ref)
#   2. pins ComfyUI to v0.37.0 (the day-0 Qwen-Image-2.1 release) via git
#   3. installs core requirements into the persistent venv (numpy<2.5 pinned,
#      same constraint as scripts/comfyui_venv_deps.sh — numba breaks on numpy 2.5)
#   4. restarts comfyui_backend and waits for :8188
#   5. verifies: ComfyUI version, new nodes present, vLLM still healthy
#   6. downloads the 3 int8_convrot weights (~17.3 GB) as the comfy user
#
# Does NOT touch media-pipeline (Phase 2: `model-manager rebuild media-pipeline`).
#
# ROLLBACK (if custom nodes break):
#   docker exec -u comfy comfyui_backend git -C /comfy/mnt/ComfyUI checkout <commit-from-log>
#   bash scripts/comfyui_venv_deps.sh restart
#   (qwen21 jobs will then fail with node-not-found; set MEDIA_IMAGE_MODEL=legacy
#    in .env so the pipeline defaults to the legacy path.)
set -euo pipefail

CONTAINER="${CONTAINER:-comfyui_backend}"
TAG="v0.37.0"
LOG="/home/chuck/data/comfyui/run/qwen21_upgrade.log"
HF_BASE="https://huggingface.co/Comfy-Org/Qwen-Image-2.1/resolve/main"
VENV_PY="/comfy/mnt/venv/bin/python"
COMFY_SRC="/comfy/mnt/ComfyUI"

# (container path, expected bytes)
WEIGHTS=(
  "/basedir/models/diffusion_models/qwen_image_2.1_int8_convrot.safetensors 7256783064"
  "/basedir/models/text_encoders/qwen3vl_8b_int8_convrot.safetensors 9350798360"
  "/basedir/models/vae/qwen_image_2.1_vae_bf16.safetensors 675509688"
)

log() { echo "[$(date -u +%H:%M:%S)] $*" | tee -a "$LOG"; }

log "=== qwen21 upgrade start (target ComfyUI ${TAG}) ==="

# --- 1. record rollback ref -------------------------------------------------
CUR_COMMIT=$(docker exec "$CONTAINER" git -C "$COMFY_SRC" rev-parse HEAD)
CUR_DESCRIBE=$(docker exec "$CONTAINER" git -C "$COMFY_SRC" describe --tags --always 2>/dev/null || echo unknown)
log "current ComfyUI: ${CUR_DESCRIBE} (${CUR_COMMIT})"

# --- 2. pin ComfyUI to v0.37.0 ----------------------------------------------
if [ "$(docker exec "$CONTAINER" git -C "$COMFY_SRC" rev-parse --short HEAD)" = "$(docker exec "$CONTAINER" git -C "$COMFY_SRC" rev-parse --short "refs/tags/${TAG}" 2>/dev/null || true)" ]; then
  log "ComfyUI already at ${TAG} — skipping checkout"
else
  log "fetching tags + checking out ${TAG}"
  docker exec "$CONTAINER" git -C "$COMFY_SRC" fetch --tags origin
  docker exec "$CONTAINER" git -C "$COMFY_SRC" checkout "${TAG}"
  log "checked out: $(docker exec "$CONTAINER" git -C "$COMFY_SRC" rev-parse HEAD)"
fi

# --- 3. venv deps (core requirements, numpy<2.5 pinned) ----------------------
log "pip install -r requirements.txt (numpy<2.5 constraint)"
docker exec "$CONTAINER" sh -c "
  echo 'numpy<2.5' > /tmp/comfy_constraints.txt
  ${VENV_PY} -m pip install -q -c /tmp/comfy_constraints.txt -r ${COMFY_SRC}/requirements.txt
  ${VENV_PY} -c 'import comfy_kitchen' 2>/dev/null && echo 'comfy_kitchen import OK' || echo 'comfy_kitchen not importable (check pip output)'
"

# --- 4. restart + wait --------------------------------------------------------
log "restarting ${CONTAINER}"
docker restart "$CONTAINER"
for i in $(seq 1 90); do
  if curl -sf -m 3 http://localhost:8188/system_stats > /dev/null 2>&1; then
    log "ComfyUI is up on :8188 (after ${i} polls)"
    break
  fi
  [ "$i" = 90 ] && { log "ERROR: ComfyUI did not come up"; exit 1; }
  sleep 5
done

# --- 5. verify ----------------------------------------------------------------
VER=$(curl -s http://localhost:8188/system_stats | python3 -c "import json,sys; print(json.load(sys.stdin)['system']['comfyui_version'])")
log "ComfyUI version: ${VER}"
[ "${VER}" = "${TAG}" ] || { log "ERROR: expected ${TAG}, got ${VER}"; exit 1; }

for NODE in TextEncodeQwenImage21 QwenImage21Cache; do
  if [ -n "$(curl -s http://localhost:8188/object_info/${NODE} | tr -d '[:space:]')" ]; then
    log "node ${NODE}: present"
  else
    log "ERROR: node ${NODE} missing"; exit 1
  fi
done

if curl -sf -m 5 http://localhost:8000/health > /dev/null 2>&1; then
  log "vLLM :8000 healthy"
else
  log "WARNING: vLLM :8000 not responding (check the 'matrix' container — ComfyUI restart should not affect it)"
fi

# --- 6. weights ---------------------------------------------------------------
for entry in "${WEIGHTS[@]}"; do
  dest=$(echo "$entry" | awk '{print $1}')
  want=$(echo "$entry" | awk '{print $2}')
  name=$(basename "$dest")
  url="${HF_BASE}/${dest#/basedir/models/}"
  if [ -f "/home/chuck/data/comfyui/basedir/models/$(basename "$(dirname "$dest")")/${name}" ] \
     && [ "$(stat -c%s "/home/chuck/data/comfyui/basedir/models/$(basename "$(dirname "$dest")")/${name}" 2>/dev/null || echo 0)" = "$want" ]; then
    log "weight already present: ${name}"
    continue
  fi
  log "downloading ${name} ($(numfmt --to=iec "$want" 2>/dev/null || echo "$want" bytes)) ..."
  docker exec -u comfy "$CONTAINER" sh -c "
    mkdir -p \$(dirname ${dest})
    curl -fSL --retry 3 -o ${dest}.part '${url}'
    mv ${dest}.part ${dest}
    chown comfy:comfy ${dest}
  "
  got=$(stat -c%s "/home/chuck/data/comfyui/basedir/models/$(basename "$(dirname "$dest")")/${name}")
  [ "$got" = "$want" ] || { log "ERROR: ${name} size ${got} != expected ${want}"; exit 1; }
  log "weight OK: ${name}"
done

log "=== qwen21 upgrade complete ==="
log "NEXT: model-manager rebuild media-pipeline   (then QA per media_todo.md Phase 3)"