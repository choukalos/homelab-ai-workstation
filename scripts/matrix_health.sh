#!/usr/bin/env bash
# matrix_health.sh — Quick health status for CLI / portal
# Usage: ./scripts/matrix_health.sh [--json]
#
# Outputs a concise health summary of all Matrix services.
# Use --json for machine-readable output (portal integration).

set -euo pipefail

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE_DIR="$BASE_DIR/state"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

json_mode=false
if [[ "${1:-}" == "--json" ]]; then
    json_mode=true
fi

# Helper functions
check_http() {
    local url="$1" timeout="${2:-3}"
    curl -sf --max-time "$timeout" "$url" &>/dev/null
}

# Gather data
mode=$(cat "$STATE_DIR/current_mode" 2>/dev/null || echo "unknown")
profiles=$(cat "$STATE_DIR/active_profiles" 2>/dev/null || echo "")

# Service status
vllm_status="down"
vllm_models=""
if check_http "http://localhost:8000/v1/models"; then
    vllm_status="up"
    vllm_models=$(curl -sf --max-time 3 http://localhost:8000/v1/models 2>/dev/null | python3 -c "
import sys, json
data = json.load(sys.stdin)
models = [m['id'] for m in data.get('data', [])]
print(','.join(models) if models else 'none')
" 2>/dev/null || echo "unknown")
fi

ollama_status="down"
ollama_models=""
if check_http "http://localhost:11434"; then
    ollama_status="up"
    ollama_models=$(curl -sf --max-time 3 http://localhost:11434/api/tags 2>/dev/null | python3 -c "
import sys, json
data = json.load(sys.stdin)
models = [m['name'] for m in data.get('models', [])]
print(','.join(models) if models else 'none')
" 2>/dev/null || echo "none")
fi

comfyui_status="down"
if check_http "http://localhost:8188" 5; then
    comfyui_status="up"
fi

node_status="down"
if check_http "http://localhost:9100/metrics"; then
    node_status="up"
fi

dcgm_status="down"
if check_http "http://localhost:9400/metrics"; then
    dcgm_status="up"
fi

# GPU data
gpu_data=$(nvidia-smi --query-gpu=name,temperature.gpu,memory.used,memory.total,utilization.gpu,power.draw --format=csv,noheader,nounits 2>/dev/null || echo "N/A,,0,0,0,0")
gpu_name=$(echo "$gpu_data" | cut -d',' -f1)
gpu_temp=$(echo "$gpu_data" | cut -d',' -f2)
gpu_mem_used=$(echo "$gpu_data" | cut -d',' -f3)
gpu_mem_total=$(echo "$gpu_data" | cut -d',' -f4)
gpu_util=$(echo "$gpu_data" | cut -d',' -f5)
gpu_power=$(echo "$gpu_data" | cut -d',' -f6)

# Disk
disk_data=$(df -BG / 2>/dev/null | tail -1)
disk_avail=$(echo "$disk_data" | awk '{print $4}' | tr -d 'G')
disk_total=$(echo "$disk_data" | awk '{print $2}' | tr -d 'G')

# Status color helpers
color() {
    local status="$1"
    case "$status" in
        up) echo -e "${GREEN}UP${NC}" ;;
        down) echo -e "${RED}DOWN${NC}" ;;
        *) echo -e "${YELLOW}$status${NC}" ;;
    esac
}

# Determine overall health
overall="HEALTHY"
# Check if critical services for the current mode are down
if [[ "$mode" == "daily" || "$mode" == "qwen-coder" || "$mode" == "qwen-long" || "$mode" == "experiment" ]]; then
    [[ "$vllm_status" == "down" ]] && overall="DEGRADED"
fi
if [[ "$vllm_status" == "down" && "$ollama_status" == "down" && "$comfyui_status" == "down" ]]; then
    overall="CRITICAL"
fi

# Temp warning
if [[ "$gpu_temp" =~ ^[0-9]+$ ]] && (( gpu_temp > 85 )); then
    overall="WARNING"
fi

# VRAM headroom warning
if [[ "$gpu_mem_total" =~ ^[0-9]+$ ]] && [[ "$gpu_mem_used" =~ ^[0-9]+$ ]]; then
    headroom=$(( gpu_mem_total - gpu_mem_used ))
    if (( headroom < 1024 )); then
        overall="WARNING"
    fi
fi

if [[ "$json_mode" == true ]]; then
    cat <<EOF
{
  "appliance": "Matrix",
  "mode": "$mode",
  "profiles": "$(echo $profiles | tr ' ' ',')",
  "timestamp": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "gpu": {
    "name": "$gpu_name",
    "temperature_c": ${gpu_temp:-0},
    "vram_used_mb": ${gpu_mem_used:-0},
    "vram_total_mb": ${gpu_mem_total:-0},
    "utilization_pct": ${gpu_util:-0},
    "power_w": ${gpu_power:-0}
  },
  "services": {
    "vllm": { "status": "$vllm_status", "port": 8000, "models": "$vllm_models" },
    "ollama": { "status": "$ollama_status", "port": 11434, "models": "$ollama_models" },
    "comfyui": { "status": "$comfyui_status", "port": 8188 },
    "node-exporter": { "status": "$node_status", "port": 9100 },
    "dcgm-exporter": { "status": "$dcgm_status", "port": 9400 }
  },
  "disk": {
    "available_gb": ${disk_avail:-0},
    "total_gb": ${disk_total:-0}
  },
  "overall": "$overall"
}
EOF
else
    echo -e "${CYAN}Matrix Health — $(date -u +%Y-%m-%dT%H:%M:%SZ)${NC}"
    echo -e "Mode: ${CYAN}$mode${NC}"
    echo ""
    echo -e "  vLLM  : $(color $vllm_status)"
    [[ -n "$vllm_models" && "$vllm_models" != "none" ]] && echo -e "          ($vllm_models, port 8000)"
    echo -e "  Ollama: $(color $ollama_status)"
    [[ -n "$ollama_models" && "$ollama_models" != "none" ]] && echo -e "          ($ollama_models, port 11434)"
    echo -e "  ComfyUI: $(color $comfyui_status) (port 8188)"
    echo -e "  node-exporter: $(color $node_status) (port 9100)"
    echo -e "  dcgm-exporter: $(color $dcgm_status) (port 9400)"
    echo ""
    echo -e "  GPU: ${gpu_mem_used} MB / ${gpu_mem_total} MB ($gpu_util%), ${gpu_temp}°C, ${gpu_power}W"
    echo -e "  Disk: ${disk_avail} GB free / ${disk_total} GB"
    echo ""
    case "$overall" in
        HEALTHY)  echo -e "  Status: ${GREEN}HEALTHY${NC}" ;;
        DEGRADED) echo -e "  Status: ${YELLOW}DEGRADED${NC}" ;;
        WARNING)  echo -e "  Status: ${YELLOW}WARNING${NC}" ;;
        CRITICAL) echo -e "  Status: ${RED}CRITICAL${NC}" ;;
    esac
fi
