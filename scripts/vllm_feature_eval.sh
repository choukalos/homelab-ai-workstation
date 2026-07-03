#!/usr/bin/env bash
# vllm_feature_eval.sh — Evaluate vLLM features one at a time
# Usage: ./vllm_feature_eval.sh <feature> [--apply]
#
# Features:
#   mtp          Enable MTP (multi-token prediction) via speculative config
#   nvfp4        Test NVFP4 KV cache (Blackwell)
#   speculative  Test external draft model speculative decoding
#
# Without --apply: dry run (shows what would change)
# With --apply:    actually restarts vLLM with the feature, benchmarks, then rolls back

set -euo pipefail

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_BASE="$BASE_DIR"
BENCHMARK="$BASE_DIR/scripts/benchmark.sh"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

feature=""
apply=false

usage() {
    echo "Usage: $0 <feature> [--apply]"
    echo ""
    echo "Features:"
    echo "  mtp          Enable MTP (multi-token prediction) via speculative config"
    echo "  nvfp4        Test NVFP4 KV cache (Blackwell)"
    echo "  speculative  Test external draft model speculative decoding"
    echo ""
    echo "Options:"
    echo "  --apply      Actually restart vLLM (otherwise dry run)"
    exit 1
}

# Parse args
if [[ $# -lt 1 ]]; then
    usage
fi

feature="$1"
shift
while [[ $# -gt 0 ]]; do
    case "$1" in
        --apply) apply=true ;;
        *) usage ;;
    esac
    shift
done

# Feature definitions
case "$feature" in
    mtp)
        COMPOSE_FILE="compose.experiment-mtp.yml"
        DESCRIPTION="MTP (Multi-Token Prediction) — speculative decoding with n_predict=1"
        FLAGS="--speculative-config '{\"n_predict\": 1}'"
        ROLLBACK_COMPOSE="compose.qwen36.yml"
        ;;
    nvfp4)
        COMPOSE_FILE="compose.experiment-nvfp4.yml"
        DESCRIPTION="NVFP4 KV Cache — lower precision KV cache for Blackwell"
        FLAGS="--kv-cache-dtype nvfp4"
        ROLLBACK_COMPOSE="compose.qwen36.yml"
        ;;
    speculative)
        echo -e "${RED}External speculative decoding requires a draft model.${NC}"
        echo -e "${YELLOW}Not yet implemented. Use MTP instead (built into Qwen3.6).${NC}"
        exit 1
        ;;
    *)
        echo -e "${RED}Unknown feature: $feature${NC}"
        usage
        ;;
esac

echo -e "${CYAN}=== vLLM Feature Evaluation: $feature ===${NC}"
echo -e "Feature: ${GREEN}$DESCRIPTION${NC}"
echo -e "Compose: $COMPOSE_FILE"
echo -e "Rollback: $ROLLBACK_COMPOSE"
echo ""

# Check compose file exists
if [[ ! -f "$COMPOSE_BASE/$COMPOSE_FILE" ]]; then
    echo -e "${RED}Compose file not found: $COMPOSE_BASE/$COMPOSE_FILE${NC}"
    echo -e "${YELLOW}Create it first based on compose.qwen36.yml with the feature flags added.${NC}"
    exit 1
fi

if [[ "$apply" == false ]]; then
    echo -e "${YELLOW}=== DRY RUN ===${NC}"
    echo ""
    echo "This would:"
    echo "  1. Run baseline benchmark"
    echo "  2. Stop current vLLM (compose.qwen36.yml)"
    echo "  3. Start $COMPOSE_FILE"
    echo "  4. Run benchmark with --baseline"
    echo "  5. Compare results"
    echo "  6. Roll back to compose.qwen36.yml"
    echo ""
    echo "Run with --apply to actually do it."
    exit 0
fi

echo -e "${CYAN}=== APPLYING ===${NC}"
echo ""

# Step 1: Baseline
echo -e "${CYAN}Step 1: Running baseline benchmark...${NC}"
bash "$BENCHMARK" --category latency --category throughput --update-baseline 2>&1 | tail -5
echo ""

# Step 2: Stop current vLLM
echo -e "${CYAN}Step 2: Stopping current vLLM...${NC}"
docker compose -f "$COMPOSE_BASE/$ROLLBACK_COMPOSE" down 2>&1 | tail -3
sleep 2
echo ""

# Step 3: Start experiment
echo -e "${CYAN}Step 3: Starting $feature experiment...${NC}"
docker compose -f "$COMPOSE_BASE/$COMPOSE_FILE" up -d 2>&1 | tail -3
sleep 5

# Health check
echo -e "${CYAN}Step 3b: Health check...${NC}"
if curl -sf http://localhost:8000/v1/models > /dev/null 2>&1; then
    echo -e "${GREEN}  vLLM is healthy${NC}"
else
    echo -e "${RED}  vLLM health check failed! Rolling back...${NC}"
    docker compose -f "$COMPOSE_BASE/$COMPOSE_FILE" down 2>&1 | tail -1
    docker compose -f "$COMPOSE_BASE/$ROLLBACK_COMPOSE" up -d 2>&1 | tail -1
    exit 1
fi
echo ""

# Step 4: Benchmark with feature
echo -e "${CYAN}Step 4: Running benchmark with $feature...${NC}"
bash "$BENCHMARK" --category latency --category throughput --baseline 2>&1 | tail -10
echo ""

# Step 5: Compare
echo -e "${CYAN}Step 5: Comparing results...${NC}"
echo -e "Check the benchmark output above for delta percentages."
echo ""

# Step 6: Rollback
echo -e "${CYAN}Step 6: Rolling back to production...${NC}"
docker compose -f "$COMPOSE_BASE/$COMPOSE_FILE" down 2>&1 | tail -1
sleep 2
docker compose -f "$COMPOSE_BASE/$ROLLBACK_COMPOSE" up -d 2>&1 | tail -1
sleep 5

# Health check after rollback
if curl -sf http://localhost:8000/v1/models > /dev/null 2>&1; then
    echo -e "${GREEN}  Production vLLM restored successfully${NC}"
else
    echo -e "${RED}  WARNING: Production vLLM health check failed!${NC}"
fi
echo ""

echo -e "${CYAN}=== DONE ===${NC}"
echo ""
echo "Summary:"
echo "  - Baseline and feature benchmarks are in data/benchmarks/"
echo "  - Review the delta to decide whether to promote"
echo "  - To promote: add the flags to compose.qwen36.yml"
echo "  - To reject: document in docs/matrix_vllm_features.md rejection log"
