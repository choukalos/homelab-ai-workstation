#!/usr/bin/env bash
# matrix-preflight — Pre-flight validation for mode switches
# Usage: matrix-preflight <MODE|PROFILE>
#
# MODE:  daily | qwen-coder | qwen-long | llms | experiment | images
# PROFILE: matrix-coder | matrix-gemma4-moe | embeddings | qwen-long | experiment | comfyui
#
# Exit 0 = all PASS, Exit 1 = FAIL(s), Exit 2 = usage error

set -uo pipefail

BASE_DIR="/home/chuck/homelab"
PROFILE_DIR="$BASE_DIR/models/profiles"
ENV_FILE="$BASE_DIR/.env"
STATE_DIR="$BASE_DIR/state"
LOG_FILE="$BASE_DIR/docs/state/preflight_log.md"

PASS=0
WARN=0
FAIL=0

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

pass()  { echo -e "  ${GREEN}PASS${NC}  $1"; ((PASS++)) || true; }
warn()  { echo -e "  ${YELLOW}WARN${NC}  $1"; ((WARN++)) || true; }
fail()  { echo -e "  ${RED}FAIL${NC}  $1"; ((FAIL++)) || true; }
section() { echo -e "\n--- $1 ---"; }

usage() {
    echo "Usage: $0 <MODE|PROFILE>"
    echo ""
    echo "Modes:    daily  qwen-coder  qwen-long  llms  experiment  images"
    echo "Profiles: matrix-coder  matrix-gemma4-moe  embeddings  qwen-long  experiment  comfyui"
    exit 2
}

if [[ $# -lt 1 ]]; then
    usage
fi

TARGET="$1"
echo "=== Matrix Preflight: $TARGET ==="
echo "Date: $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
echo ""

# Load .env
if [[ -f "$ENV_FILE" ]]; then
    set -a
    source "$ENV_FILE"
    set +a
    pass ".env loaded"
else
    fail ".env not found at $ENV_FILE"
fi

# ============================================================
# 1. GPU CHECKS
# ============================================================
section "GPU"

if command -v nvidia-smi &>/dev/null; then
    pass "nvidia-smi available"
else
    fail "nvidia-smi not found — GPU driver may be missing"
fi

if nvidia-smi &>/dev/null; then
    GPU_NAME=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1)
    DRIVER=$(nvidia-smi --query-gpu=driver_version --format=csv,noheader 2>/dev/null | head -1)
    CUDA_VER=$(nvidia-smi --query-gpu=cuda_version --format=csv,noheader 2>/dev/null | head -1 || echo "unknown")
    TOTAL_VRAM=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader 2>/dev/null | head -1 | tr -d ' MiB')
    USED_VRAM=$(nvidia-smi --query-gpu=memory.used --format=csv,noheader 2>/dev/null | head -1 | tr -d ' MiB')
    FREE_VRAM=$(nvidia-smi --query-gpu=memory.free --format=csv,noheader 2>/dev/null | head -1 | tr -d ' MiB')

    echo "  GPU: $GPU_NAME"
    echo "  Driver: $DRIVER, CUDA: $CUDA_VER"
    echo "  VRAM: ${USED_VRAM}MiB / ${TOTAL_VRAM}MiB (${FREE_VRAM}MiB free)"
    pass "GPU online: $GPU_NAME"
else
    fail "nvidia-smi returned error — check GPU health"
    GPU_NAME="unknown"; TOTAL_VRAM=0; USED_VRAM=0; FREE_VRAM=0
fi

# ============================================================
# 2. DOCKER CHECKS
# ============================================================
section "Docker"

if docker info &>/dev/null; then
    pass "Docker daemon running"
else
    fail "Docker daemon not reachable"
fi

if docker network ls --format '{{.Name}}' | grep -q "homelab_default"; then
    pass "Docker network 'homelab_default' exists"
else
    fail "Docker network 'homelab_default' missing"
fi

# ============================================================
# 3. PROFILE RESOLUTION
# ============================================================
section "Profiles"

# Map modes to profiles
declare -A MODE_PROFILES
MODE_PROFILES[daily]="matrix-coder matrix-gemma4-moe embeddings"
MODE_PROFILES[qwen-coder]="matrix-coder embeddings"
MODE_PROFILES[qwen-long]="qwen-long embeddings"
MODE_PROFILES[llms]="matrix-coder matrix-gemma4-moe embeddings"
MODE_PROFILES[experiment]="experiment embeddings"
MODE_PROFILES[images]="comfyui matrix-gemma4-moe embeddings"

# Determine if TARGET is a mode or a profile
if [[ -n "${MODE_PROFILES[$TARGET]+x}" ]]; then
    # It's a mode
    echo "  Mode: $TARGET"
    PROFILES="${MODE_PROFILES[$TARGET]}"
    pass "Mode '$TARGET' resolves to: $PROFILES"
elif [[ -f "$PROFILE_DIR/$TARGET.yaml" ]]; then
    # It's a single profile
    PROFILES="$TARGET"
    echo "  Profile: $TARGET"
else
    fail "Unknown mode/profile: '$TARGET'"
    echo ""
    echo "Summary: PASS=$PASS WARN=$WARN FAIL=$FAIL"
    exit 1
fi

# ============================================================
# 4. DISK CHECK
# ============================================================
section "Disk"

AVAIL_DISK_KB=$(df -k "$BASE_DIR" | tail -1 | awk '{print $4}')
AVAIL_DISK_GB=$((AVAIL_DISK_KB / 1024 / 1024))

if (( AVAIL_DISK_GB >= 10 )); then
    pass "Disk: ${AVAIL_DISK_GB}GB available (>= 10GB threshold)"
elif (( AVAIL_DISK_GB >= 5 )); then
    warn "Disk: ${AVAIL_DISK_GB}GB available (low — model pulls may fail)"
else
    fail "Disk: ${AVAIL_DISK_GB}GB available (< 5GB — critical)"
fi

# ============================================================
# 5. PER-PROFILE VALIDATION
# ============================================================
section "Profile Validation"

TOTAL_EXPECTED_VRAM=0

for profile in $PROFILES; do
    PROFILE_FILE="$PROFILE_DIR/$profile.yaml"
    echo ""
    echo "  >> $profile"

    if [[ ! -f "$PROFILE_FILE" ]]; then
        fail "  $profile: profile file missing ($PROFILE_FILE)"
        continue
    fi

    # Parse key fields from YAML using python3
    # Parse profile via python3, one field per line to avoid space-in-value issues
    PARSED=$(python3 -c "
import yaml, sys, re
try:
    with open('$PROFILE_FILE') as f:
        d = yaml.safe_load(f)
    backend = d.get('backend', 'unknown')
    model = d.get('model', {}).get('path', 'unknown')
    compose = d.get('launch', {}).get('compose', 'unknown')
    vram_str = str(d.get('model', {}).get('expected_vram', '0'))
    # Parse '48.7 GB', '274 MB', '17 GB', '30-40 GB', '50-55 GB', etc.
    m = re.search(r'([\d.]+)(?:-([\d.]+))?\s*(MB|GB)', vram_str, re.IGNORECASE)
    if m:
        val = float(m.group(2)) if m.group(2) else float(m.group(1))  # Use upper bound for ranges
        unit = m.group(3).upper()
        vram_gb = val if unit == 'GB' else val / 1024.0
    else:
        # Fallback: just grab the first number and assume GB
        m2 = re.search(r'([\d.]+)', vram_str)
        vram_gb = float(m2.group(1)) if m2 else 0.0
    print(f'{backend}')
    print(f'{model}')
    print(f'{compose}')
    print(f'yes')
    print(f'{vram_gb:.1f}')
except Exception as e:
    print(f'unknown')
    print(f'unknown')
    print(f'unknown')
    print(f'no')
    print(f'0.0')
" 2>/dev/null)
    BACKEND=$(echo "$PARSED" | sed -n '1p')
    MODEL_PATH=$(echo "$PARSED" | sed -n '2p')
    COMPOSE=$(echo "$PARSED" | sed -n '3p')
    FILE_EXISTS=$(echo "$PARSED" | sed -n '4p')
    VRAM_GB=$(echo "$PARSED" | sed -n '5p')
    
    # Fallback if python3 failed
    BACKEND=${BACKEND:-unknown}
    MODEL_PATH=${MODEL_PATH:-unknown}
    COMPOSE=${COMPOSE:-unknown}
    VRAM_GB=${VRAM_GB:-0.0}

    echo "    Backend: $BACKEND"
    echo "    Model: $MODEL_PATH"
    echo "    Compose: $COMPOSE"

    # Check compose file exists
    case "$COMPOSE" in
        manual|unknown)
            warn "  $profile: compose is '$COMPOSE' — will need manual launch"
            ;;
        *)
            # Resolve compose file paths
            COMPOSE_PATH="$BASE_DIR/$COMPOSE"
            if [[ -f "$COMPOSE_PATH" ]]; then
                pass "  $profile: compose file exists"
            else
                fail "  $profile: compose file missing ($COMPOSE_PATH)"
            fi
            ;;
    esac

    # Check model files exist
    case "$BACKEND" in
        vllm)
            # HF cache uses models--{user}--{model} format
            HF_CACHE_DIR="/home/chuck/data/models/hub"
            # Try direct path first, then HF cache format
            MODEL_PATH_DIRECT="$HF_CACHE_DIR/$MODEL_PATH"
            # Convert model path to HF cache format: user/model -> models--user--model
            MODEL_USER=$(echo "$MODEL_PATH" | cut -d'/' -f1)
            MODEL_NAME=$(echo "$MODEL_PATH" | cut -d'/' -f2-)
            MODEL_PATH_HF="$HF_CACHE_DIR/models--${MODEL_USER}--${MODEL_NAME}"

            if [[ -d "$MODEL_PATH_DIRECT" ]]; then
                MODEL_SIZE=$(du -sh "$MODEL_PATH_DIRECT" 2>/dev/null | cut -f1)
                echo "    HF cache: $MODEL_SIZE (direct path)"
                pass "  $profile: model cached"
            elif [[ -d "$MODEL_PATH_HF" ]]; then
                MODEL_SIZE=$(du -sh "$MODEL_PATH_HF" 2>/dev/null | cut -f1)
                echo "    HF cache: $MODEL_SIZE (HF format)"
                pass "  $profile: model cached"
            else
                warn "  $profile: model not in HF cache — will need to download on first launch"
            fi
            ;;
        ollama)
            # Check if model is in Ollama library
            if docker ps --format '{{.Names}}' | grep -q ollama; then
                if docker exec ollama ollama list 2>/dev/null | grep -q "$MODEL_PATH" || \
                   docker exec ollama ollama list 2>/dev/null | grep -q "${MODEL_PATH%%:*}"; then
                    pass "  $profile: model in Ollama library"
                else
                    warn "  $profile: model not in Ollama library — will need to pull"
                fi
            else
                warn "  $profile: Ollama container not running — cannot check model"
            fi
            ;;
        comfyui)
            # ComfyUI models are in /home/chuck/data/comfyui
            if [[ -d "/home/chuck/data/comfyui" ]]; then
                pass "  $profile: ComfyUI data dir exists"
            else
                warn "  $profile: ComfyUI data dir missing"
            fi
            ;;
    esac

    # Check port availability
    case "$BACKEND" in
        vllm)
            PORT=8000
            ;;
        ollama)
            PORT=11434
            ;;
        comfyui)
            PORT=8188
            ;;
        *)
            PORT=0
            ;;
    esac

    if (( PORT != 0 )); then
        # Check if port is in use by a DIFFERENT container than expected
        LISTENING=$(ss -tlnp 2>/dev/null | grep ":$PORT " || true)
        if [[ -n "$LISTENING" ]]; then
            echo "    Port $PORT: in use ($LISTENING)"
            pass "  $profile: port $PORT available (in use by existing instance)"
        else
            # Port not in use — could be bad (model stopped) or good (ready to start)
            warn "  $profile: port $PORT not listening"
        fi
    fi

    # Accumulate VRAM (use awk for float math)
    echo "    VRAM: $MODEL_PATH expected ~${VRAM_GB}GB"
    TOTAL_EXPECTED_VRAM=$(awk "BEGIN {printf \"%d\", $TOTAL_EXPECTED_VRAM + $VRAM_GB}")
done

# ============================================================
# 6. VRAM BUDGET
# ============================================================
section "VRAM Budget"

# Convert to GB for comparison
TOTAL_VRAM_GB=$((TOTAL_VRAM / 1024))
USED_VRAM_GB=$((USED_VRAM / 1024))
FREE_VRAM_GB=$((FREE_VRAM / 1024))

echo "  GPU total: ${TOTAL_VRAM_GB}GB"
echo "  Currently used: ${USED_VRAM_GB}GB"
echo "  Currently free: ${FREE_VRAM_GB}GB"
echo "  Expected for '$TARGET': ~${TOTAL_EXPECTED_VRAM}GB"

# The check: total expected VRAM must fit on the GPU
# (not just the free VRAM, because we'll stop existing models first)
if (( TOTAL_EXPECTED_VRAM <= TOTAL_VRAM_GB )); then
    pass "VRAM budget: ${TOTAL_EXPECTED_VRAM}GB expected fits within ${TOTAL_VRAM_GB}GB total GPU"
    # Additional check: is there enough headroom for OS/host?
    HEADROOM=$((TOTAL_VRAM_GB - TOTAL_EXPECTED_VRAM))
    if (( HEADROOM < 5 )); then
        warn "VRAM budget: tight headroom (${HEADROOM}GB free) — minimal room for error"
    fi
elif (( TOTAL_EXPECTED_VRAM <= TOTAL_VRAM_GB + 10 )); then
    warn "VRAM budget: ${TOTAL_EXPECTED_VRAM}GB expected is close to limit — may need tuning"
else
    fail "VRAM budget: ${TOTAL_EXPECTED_VRAM}GB expected exceeds GPU total (${TOTAL_VRAM_GB}GB)"
fi

# ============================================================
# 7. ENV VARS
# ============================================================
section "Environment"

if [[ -n "${HF_TOKEN:-}" ]]; then
    pass "HF_TOKEN set"
else
    warn "HF_TOKEN is empty — model downloads requiring auth will fail"
fi

if [[ -n "${QWEN_VLLM_MODEL:-}" ]]; then
    pass "QWEN_VLLM_MODEL set to $QWEN_VLLM_MODEL"
else
    warn "QWEN_VLLM_MODEL not set"
fi

# ============================================================
# 8. NETWORK
# ============================================================
section "Network"

HOSTNAME=$(hostname)
echo "  Hostname: $HOSTNAME"

# Check if Thor can reach Matrix (try Thor's LiteLLM config reference)
LITELLM_CONFIG="$BASE_DIR/thor.litellm.config.yml"
if [[ -f "$LITELLM_CONFIG" ]]; then
    MATRIX_HOST=$(grep -oP 'matrix(?::\d+)?' "$LITELLM_CONFIG" 2>/dev/null | head -1 || true)
    if [[ -n "$MATRIX_HOST" ]]; then
        echo "  Thor config references: $MATRIX_HOST"
        pass "LiteLLM config present and references matrix"
    else
        warn "LiteLLM config found but no matrix reference detected"
    fi
else
    warn "thor.litellm.config.yml not found — cannot validate Thor connectivity"
fi

# ============================================================
# 9. HEALTH ENDPOINTS (if applicable)
# ============================================================
section "Health Checks (current state)"

# vLLM
if curl -sf --max-time 3 http://localhost:8000/v1/models &>/dev/null; then
    VLLM_MODELS=$(curl -sf --max-time 3 http://localhost:8000/v1/models 2>/dev/null | python3 -c "import sys,json; [print('  -', m['id']) for m in json.load(sys.stdin).get('data',[])]" 2>/dev/null || echo "  - (unknown)")
    echo "  vLLM (:8000) UP:$VLLM_MODELS"
    pass "vLLM health endpoint responding"
else
    echo "  vLLM (:8000) DOWN"
    if [[ "$PROFILES" == *"matrix-coder"* ]] || [[ "$PROFILES" == *"qwen-long"* ]] || [[ "$PROFILES" == *"experiment"* ]]; then
        warn "vLLM not responding but target mode needs it — will need to start"
    fi
fi

# Ollama
if curl -sf --max-time 3 http://localhost:11434 &>/dev/null; then
    echo "  Ollama (:11434) UP"
    pass "Ollama health endpoint responding"
else
    echo "  Ollama (:11434) DOWN"
    if [[ "$PROFILES" == *"matrix-gemma4-moe"* ]] || [[ "$PROFILES" == *"embeddings"* ]]; then
        warn "Ollama not responding but target mode needs it — will need to start"
    fi
fi

# ComfyUI
if curl -sf --max-time 3 http://localhost:8188 &>/dev/null; then
    echo "  ComfyUI (:8188) UP"
    pass "ComfyUI health endpoint responding"
else
    echo "  ComfyUI (:8188) DOWN"
    if [[ "$PROFILES" == *"comfyui"* ]]; then
        warn "ComfyUI not responding but target mode needs it — will need to start"
    fi
fi

# ============================================================
# 10. METRICS
# ============================================================
section "Metrics"

if curl -sf --max-time 3 http://localhost:9100/metrics &>/dev/null; then
    pass "node-exporter (:9100) responding"
else
    warn "node-exporter not responding"
fi

if curl -sf --max-time 3 http://localhost:9400/metrics &>/dev/null; then
    pass "dcgm-exporter (:9400) responding"
else
    warn "dcgm-exporter not responding"
fi

# ============================================================
# SUMMARY
# ============================================================
echo ""
section "SUMMARY"
echo "  Target: $TARGET"
echo "  Profiles: $PROFILES"
echo -e "  ${GREEN}PASS=${PASS}${NC}  ${YELLOW}WARN=${WARN}${NC}  ${RED}FAIL=${FAIL}${NC}"
echo ""

if (( FAIL > 0 )); then
    echo -e "  ${RED}PREFLIGHT FAILED${NC} — $FAIL check(s) failed. Fix before switching."
    exit 1
elif (( WARN > 3 )); then
    echo -e "  ${YELLOW}PREFLIGHT PASSED WITH WARNINGS${NC} — proceed with caution."
    exit 0
else
    echo -e "  ${GREEN}PREFLIGHT PASSED${NC} — mode switch should be safe."
    exit 0
fi
