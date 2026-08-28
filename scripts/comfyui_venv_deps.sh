#!/usr/bin/env bash
# comfyui_venv_deps.sh — (re)install custom-node Python deps into the ComfyUI venv.
#
# WHY THIS EXISTS
# The comfyui_backend entrypoint self-heals: if /comfy/mnt/venv is missing it recreates
# it with base ComfyUI deps ONLY. Custom-node deps (numba, librosa, gguf, imageio-ffmpeg,
# ...) are NOT installed, so comfyui-mmaudio / ComfyUI-GGUF / SeedVR2 / VideoHelperSuite
# fail to import on the next start.
#
# ALSO: the fresh venv resolves numpy 2.5.x, which breaks numba ("Numba needs NumPy 2.4
# or less"). We pin numpy<2.5 via a constraint file on every install.
#
# USAGE: ./scripts/comfyui_venv_deps.sh [restart]
#   (default: install deps only; pass "restart" to also restart comfyui_backend)
set -euo pipefail

CONTAINER="${CONTAINER:-comfyui_backend}"
BASEDIR="/basedir/custom_nodes"
VENV_PY="/comfy/mnt/venv/bin/python"
NODES=(comfyui-mmaudio ComfyUI-GGUF ComfyUI-SeedVR2_VideoUpscaler ComfyUI-VideoHelperSuite)

echo "== Installing custom-node deps (numpy<2.5 pinned) into ${CONTAINER}"
docker exec "$CONTAINER" sh -c "
  echo 'numpy<2.5' > /tmp/comfy_constraints.txt
  V='${VENV_PY}'
  for n in ${NODES[*]}; do
    echo \"-- \$n\"
    \$V -m pip install -q -c /tmp/comfy_constraints.txt -r ${BASEDIR}/\$n/requirements.txt
  done
  \$V -c 'import numpy, numba; print(\"numpy\", numpy.__version__, \"numba\", numba.__version__)'
"

if [ "${1:-}" = "restart" ]; then
  echo "== Restarting ${CONTAINER}"
  docker restart "$CONTAINER"
  echo "== Waiting for ComfyUI on :8188 ..."
  for i in $(seq 1 60); do
    if curl -sf -m 3 http://localhost:8188/system_stats > /dev/null 2>&1; then
      echo "ComfyUI is up."
      break
    fi
    sleep 5
  done
fi