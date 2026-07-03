#!/usr/bin/env bash
# matrix-benchmark — Local benchmark harness for Matrix models
# Usage: benchmark.sh [OPTIONS]
#
# Options:
#   --profile PROFILE    Profile to benchmark (default: matrix-coder)
#   --category CAT       all | latency | throughput | quality | stress | gpu (default: all)
#   --output DIR         Output directory (default: data/benchmarks/results/TIMESTAMP)
#   --baseline           Compare against baseline
#   --update-baseline    Update the baseline
#   --vram-only          Quick VRAM snapshot only
#   --warmup N           Warmup iterations (default: 5)
#   --runs N             Real benchmark iterations (default: 20)
#   --concurrent N       Concurrent requests for stress test (default: 4)
#   --tokens N           Tokens to generate for throughput test (default: 512)
#   --help               Show this help

set -uo pipefail

BASE_DIR="/home/chuck/homelab"
PROFILE_DIR="$BASE_DIR/models/profiles"
BENCHMARK_DIR="$BASE_DIR/data/benchmarks"
BASELINE_DIR="$BENCHMARK_DIR/baseline"
RESULTS_DIR="$BENCHMARK_DIR/results"

# Defaults
PROFILE="matrix-coder"
CATEGORY="all"
OUTPUT_DIR=""
DO_BASELINE=0
DO_UPDATE_BASELINE=0
VRAM_ONLY=0
WARMUP=5
RUNS=20
CONCURRENT=4
TOKENS=512

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m'

section() { echo -e "\n${CYAN}=== $1 ===${NC}"; }
info()    { echo -e "  $1"; }
ok()      { echo -e "  ${GREEN}✓${NC}  $1"; }
warn()    { echo -e "  ${YELLOW}⚠${NC}  $1"; }
fail()    { echo -e "  ${RED}✗${NC}  $1"; }

usage() {
    head -18 "$0" | tail -16
    exit 0
}

# ---- Parse args ----
while [[ $# -gt 0 ]]; do
    case "$1" in
        --profile)         PROFILE="$2"; shift 2 ;;
        --category)        CATEGORY="$2"; shift 2 ;;
        --output)          OUTPUT_DIR="$2"; shift 2 ;;
        --baseline)        DO_BASELINE=1; shift ;;
        --update-baseline) DO_UPDATE_BASELINE=1; DO_BASELINE=1; shift ;;
        --vram-only)       VRAM_ONLY=1; shift ;;
        --warmup)          WARMUP="$2"; shift 2 ;;
        --runs)            RUNS="$2"; shift 2 ;;
        --concurrent)      CONCURRENT="$2"; shift 2 ;;
        --tokens)          TOKENS="$2"; shift 2 ;;
        --help)            usage ;;
        *)                 echo "Unknown option: $1"; exit 2 ;;
    esac
done

# ---- Setup ----
TIMESTAMP=$(date -u '+%Y-%m-%dT%H:%M:%SZ')
TIMESTAMP_DIR=$(date -u '+%Y%m%d_%H%M%S')

if [[ -z "$OUTPUT_DIR" ]]; then
    OUTPUT_DIR="$RESULTS_DIR/$TIMESTAMP_DIR"
fi

mkdir -p "$OUTPUT_DIR"

# Resolve profile
PROFILE_FILE="$PROFILE_DIR/$PROFILE.yaml"
if [[ ! -f "$PROFILE_FILE" ]]; then
    fail "Profile not found: $PROFILE_FILE"
    exit 1
fi

# Parse profile via python3
read -r BACKEND MODEL_PATH COMPOSE <<< $(python3 -c "
import yaml
try:
    with open('$PROFILE_FILE') as f:
        d = yaml.safe_load(f)
    print(d.get('backend',''), d.get('model',{}).get('path',''), d.get('launch',{}).get('compose',''))
except:
    print('','','')
" 2>/dev/null)

# Determine endpoint based on backend
case "$BACKEND" in
    vllm)  ENDPOINT="http://localhost:8000/v1"; MODEL_ID=$(curl -sf http://localhost:8000/v1/models 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin)['data'][0]['id'] if json.load(open('/dev/stdin','rb'))['data'] else '')" 2>/dev/null || echo "unknown") ;;
    ollama) ENDPOINT="http://localhost:11434/api"; MODEL_ID="$MODEL_PATH" ;;
    *)      ENDPOINT="http://localhost:8000/v1"; MODEL_ID="$MODEL_PATH" ;;
esac

# If model ID is unknown, try to get it
if [[ "$MODEL_ID" == "unknown" ]] || [[ -z "$MODEL_ID" ]]; then
    if command -v curl &>/dev/null; then
        MODEL_ID=$(curl -sf --max-time 3 "$ENDPOINT/models" 2>/dev/null | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    print(d.get('data', [{}])[0].get('id', 'unknown'))
except:
    print('unknown')
" 2>/dev/null || echo "unknown")
    fi
fi

echo -e "\n${CYAN}═══════════════════════════════════════${NC}"
echo -e "${CYAN}  Matrix Benchmark${NC}"
echo -e "${CYAN}═══════════════════════════════════════${NC}"
echo -e "  Profile:  $PROFILE"
echo -e "  Model:    $MODEL_PATH"
echo -e "  Endpoint: $ENDPOINT"
echo -e "  Category: $CATEGORY"
echo -e "  Timestamp: $TIMESTAMP"
echo -e "  Output:   $OUTPUT_DIR"
echo -e "${CYAN}───────────────────────────────────────${NC}"

# ---- VRAM ONLY (quick mode) ----
if (( VRAM_ONLY )); then
    section "VRAM Snapshot"
    
    VRAM_USED=$(nvidia-smi --query-gpu=memory.used --format=csv,noheader 2>/dev/null | head -1 | tr -d ' MiB')
    VRAM_TOTAL=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader 2>/dev/null | head -1 | tr -d ' MiB')
    GPU_UTIL=$(nvidia-smi --query-gpu=utilization.gpu --format=csv,noheader 2>/dev/null | head -1 | tr -d ' %')
    
    VRAM_USED_GB=$(awk "BEGIN {printf \"%.1f\", $VRAM_USED / 1024}")
    VRAM_TOTAL_GB=$(awk "BEGIN {printf \"%.1f\", $VRAM_TOTAL / 1024}")
    
    info "VRAM: ${VRAM_USED_GB}GB / ${VRAM_TOTAL_GB}GB"
    info "GPU util: ${GPU_UTIL}%"
    
    # Write JSON
    python3 -c "
import json
data = {
    'timestamp': '$TIMESTAMP',
    'profile': '$PROFILE',
    'model': '$MODEL_PATH',
    'type': 'vram_snapshot',
    'gpu': {
        'vram_used_mb': $VRAM_USED,
        'vram_total_mb': $VRAM_TOTAL,
        'vram_used_gb': $VRAM_USED_GB,
        'vram_total_gb': $VRAM_TOTAL_GB,
        'gpu_util_pct': $GPU_UTIL
    }
}
with open('$OUTPUT_DIR/vram.json', 'w') as f:
    json.dump(data, f, indent=2)
" 2>/dev/null
    
    ok "VRAM snapshot saved to $OUTPUT_DIR/vram.json"
    exit 0
fi

# ---- Check endpoint is up ----
if ! curl -sf --max-time 5 "$ENDPOINT/models" &>/dev/null; then
    fail "Endpoint $ENDPOINT is not responding. Is the model running?"
    exit 1
fi
ok "Endpoint $ENDPOINT is responding"

# ---- Collect GPU baseline ----
section "GPU State (Pre-benchmark)"

VRAM_USED_BEFORE=$(nvidia-smi --query-gpu=memory.used --format=csv,noheader 2>/dev/null | head -1 | tr -d ' MiB')
GPU_UTIL_BEFORE=$(nvidia-smi --query-gpu=utilization.gpu --format=csv,noheader 2>/dev/null | head -1 | tr -d ' %')
info "VRAM used: $(awk "BEGIN {printf \"%.1f\", $VRAM_USED_BEFORE / 1024}")GB"
info "GPU util: ${GPU_UTIL_BEFORE}%"

# ---- Results collection ----
RESULTS_FILE="$OUTPUT_DIR/summary.json"
REPORT_FILE="$OUTPUT_DIR/report.md"

# Initialize result accumulators
declare -A RESULTS

# ============================================================
# LATENCY TEST (TTFT + total time)
# ============================================================
if [[ "$CATEGORY" == "all" ]] || [[ "$CATEGORY" == "latency" ]]; then
    section "Latency (TTFT + Total)"
    info "Warmup: $WARMUP runs, Real: $RUNS runs"
    
    # Use python3 for precise timing
    python3 -c "
import requests, json, time, sys, statistics

endpoint = '$ENDPOINT/completions'
payload = {
    'model': '$MODEL_ID',
    'prompt': 'Explain briefly what a deadlock is.',
    'max_tokens': 1,
    'temperature': 0.0
}

# Warmup
for i in range($WARMUP):
    try:
        requests.post(endpoint, json=payload, timeout=60)
    except:
        pass

# Real runs
ttfts = []
total_times = []
for i in range($RUNS):
    t0 = time.perf_counter()
    try:
        r = requests.post(endpoint, json=payload, timeout=120, stream=True)
        first_chunk = False
        for chunk in r.iter_content(chunk_size=1):
            if not first_chunk:
                ttft = (time.perf_counter() - t0) * 1000
                ttfts.append(ttft)
                first_chunk = True
        total = (time.perf_counter() - t0) * 1000
        total_times.append(total)
    except Exception as e:
        print(f'  Run {i} failed: {e}', file=sys.stderr)

if not ttfts:
    print('ERROR: No successful latency runs')
    sys.exit(1)

results = {
    'ttft_ms_mean': round(statistics.mean(ttfts), 2),
    'ttft_ms_median': round(statistics.median(ttfts), 2),
    'ttft_ms_p95': round(sorted(ttfts)[int(len(ttfts)*0.95)], 2),
    'ttft_ms_min': round(min(ttfts), 2),
    'ttft_ms_max': round(max(ttfts), 2),
    'total_ms_mean': round(statistics.mean(total_times), 2),
    'total_ms_median': round(statistics.median(total_times), 2),
    'total_runs': len(ttfts)
}

print(json.dumps(results, indent=2))
" 2>&1 > "$OUTPUT_DIR/latency.json"

    if [[ -f "$OUTPUT_DIR/latency.json" ]]; then
        TTFT_MEAN=$(python3 -c "import json; d=json.load(open('$OUTPUT_DIR/latency.json')); print(d.get('ttft_ms_mean','?'))" 2>/dev/null)
        TTFT_P95=$(python3 -c "import json; d=json.load(open('$OUTPUT_DIR/latency.json')); print(d.get('ttft_ms_p95','?'))" 2>/dev/null)
        info "TTFT mean: ${TTFT_MEAN}ms, p95: ${TTFT_P95}ms"
        ok "Latency test complete ($RUNS runs)"
    else
        fail "Latency test produced no output"
    fi
fi

# ============================================================
# THROUGHPUT TEST (tokens/sec)
# ============================================================
if [[ "$CATEGORY" == "all" ]] || [[ "$CATEGORY" == "throughput" ]]; then
    section "Throughput (tokens/sec)"
    info "Generating $TOKENS tokens x $RUNS runs"
    
    python3 -c "
import requests, json, time, sys, statistics

endpoint = '$ENDPOINT/completions'
# Prompt that will generate a decent amount of text
payload = {
    'model': '$MODEL_ID',
    'prompt': 'Write a detailed explanation of how transformer attention mechanisms work, covering self-attention, multi-head attention, and positional encoding. Be thorough and include mathematical formulations.',
    'max_tokens': $TOKENS,
    'temperature': 0.7,
    'stream': True
}

# Warmup
for i in range(3):
    try:
        requests.post(endpoint, json=payload, timeout=120)
    except:
        pass

# Real runs
tps_list = []
for i in range($RUNS):
    t0 = time.perf_counter()
    token_count = 0
    try:
        with requests.post(endpoint, json=payload, timeout=300, stream=True) as r:
            buffer = ''
            for chunk in r.iter_content(chunk_size=1024):
                buffer += chunk.decode('utf-8', errors='replace')
                # Count tokens roughly by whitespace-separated words (good approximation for English)
                parts = buffer.split()
                token_count = len(parts)
        elapsed = time.perf_counter() - t0
        if elapsed > 0:
            tps = token_count / elapsed
            tps_list.append(tps)
    except Exception as e:
        print(f'  Run {i} failed: {e}', file=sys.stderr)

if not tps_list:
    print('ERROR: No successful throughput runs')
    sys.exit(1)

results = {
    'tokens_per_sec_mean': round(statistics.mean(tps_list), 2),
    'tokens_per_sec_median': round(statistics.median(tps_list), 2),
    'tokens_per_sec_min': round(min(tps_list), 2),
    'tokens_per_sec_max': round(max(tps_list), 2),
    'target_tokens': $TOKENS,
    'total_runs': len(tps_list)
}

print(json.dumps(results, indent=2))
" 2>&1 > "$OUTPUT_DIR/throughput.json"

    if [[ -f "$OUTPUT_DIR/throughput.json" ]]; then
        TPS=$(python3 -c "import json; d=json.load(open('$OUTPUT_DIR/throughput.json')); print(d.get('tokens_per_sec_mean','?'))" 2>/dev/null)
        info "Throughput: ${TPS} tokens/sec (mean)"
        ok "Throughput test complete"
    else
        fail "Throughput test produced no output"
    fi
fi

# ============================================================
# QUALITY TESTS (outputs for manual review)
# ============================================================
if [[ "$CATEGORY" == "all" ]] || [[ "$CATEGORY" == "quality" ]]; then
    section "Quality Tests (for manual review)"
    
    ENDPOINT_COMPLETIONS="$ENDPOINT/completions"
    
    # Code generation
    python3 -c "
import requests, json

endpoint = '$ENDPOINT_COMPLETIONS'
payload = {
    'model': '$MODEL_ID',
    'prompt': 'Write a bash script that parses docker container logs from stdin, identifies ERROR and FATAL messages, extracts the timestamp and container name if present, and outputs a summary CSV with columns: timestamp, container, severity, message. Handle multi-line log formats gracefully.',
    'max_tokens': 2048,
    'temperature': 0.3
}
try:
    r = requests.post(endpoint, json=payload, timeout=120)
    resp = r.json()
    output = resp.get('choices', [{}])[0].get('text', 'ERROR: No response')
    with open('$OUTPUT_DIR/quality_code_gen.md', 'w') as f:
        f.write('# Quality Test: Code Generation\n')
        f.write('## Prompt\nWrite a bash script that parses docker container logs...\n\n')
        f.write('## Output\n\n\`\`\`bash\n' + output.strip() + '\n\`\`\`\n')
    print('  OK: quality_code_gen.md')
except Exception as e:
    print(f'  FAIL: {e}', file=open('$OUTPUT_DIR/quality_code_gen.md', 'w'))
" 2>/dev/null
    
    ok "Code generation test written"
    
    # Refactoring
    python3 -c "
import requests, json

endpoint = '$ENDPOINT_COMPLETIONS'
payload = {
    'model': '$MODEL_ID',
    'prompt': '''Refactor this Python class to use dependency injection instead of hardcoded dependencies:

class EmailService:
    def __init__(self):
        self.smtp_host = \"smtp.example.com\"
        self.smtp_port = 587
        self.db = PostgreSQLConnection(\"localhost\", 5432)
        self.cache = RedisClient(\"localhost\", 6379)
    
    def send_notification(self, user_id, template):
        user = self.db.query(\"SELECT * FROM users WHERE id = \" + str(user_id))
        if self.cache.exists(f\"template:{template}\"):
            content = self.cache.get(f\"template:{template}\")
        else:
            content = self.db.query(f\"SELECT content FROM templates WHERE name = '{template}'\")
            self.cache.set(f\"template:{template}\", content, 3600)
        # send email logic...
        print(f\"Sent to {user['email']}\")''',
    'max_tokens': 2048,
    'temperature': 0.3
}
try:
    r = requests.post(endpoint, json=payload, timeout=120)
    resp = r.json()
    output = resp.get('choices', [{}])[0].get('text', 'ERROR: No response')
    with open('$OUTPUT_DIR/quality_refactoring.md', 'w') as f:
        f.write('# Quality Test: Refactoring\n')
        f.write('## Prompt\nRefactor Python class to use dependency injection...\n\n')
        f.write('## Output\n\n\`\`\`python\n' + output.strip() + '\n\`\`\`\n')
    print('  OK: quality_refactoring.md')
except Exception as e:
    print(f'  FAIL: {e}', file=open('$OUTPUT_DIR/quality_refactoring.md', 'w'))
" 2>/dev/null
    
    ok "Refactoring test written"
    
    # Debugging
    python3 -c "
import requests, json

endpoint = '$ENDPOINT_COMPLETIONS'
payload = {
    'model': '$MODEL_ID',
    'prompt': '''Find and fix the bugs in this code. Explain each bug:

import os
from collections import defaultdict

def process_files(directory):
    results = {}
    for file in os.listdir(directory):
        path = directory + '/' + file
        if os.path.isdir(path):
            continue
        with open(path) as f:
            data = f.read()
        ext = os.path.splitext(file)[1]
        results[ext] = results.get(ext, []) + [len(data)]
    return results

# Error output:
# TypeError: can only concatenate list (\"[]\") to str (\"'\\x00'\")''',
    'max_tokens': 1536,
    'temperature': 0.1
}
try:
    r = requests.post(endpoint, json=payload, timeout=120)
    resp = r.json()
    output = resp.get('choices', [{}])[0].get('text', 'ERROR: No response')
    with open('$OUTPUT_DIR/quality_debugging.md', 'w') as f:
        f.write('# Quality Test: Debugging\n')
        f.write('## Prompt\nFind and fix bugs in Python file processing code...\n\n')
        f.write('## Output\n\n' + output.strip() + '\n')
    print('  OK: quality_debugging.md')
except Exception as e:
    print(f'  FAIL: {e}', file=open('$OUTPUT_DIR/quality_debugging.md', 'w'))
" 2>/dev/null
    
    ok "Debugging test written"
    
    # Tool calling
    python3 -c "
import requests, json

endpoint = '$ENDPOINT/chat/completions'
payload = {
    'model': '$MODEL_ID',
    'messages': [
        {'role': 'system', 'content': 'You are a helpful assistant with access to tools.'},
        {'role': 'user', 'content': 'What is the weather in San Francisco right now?'}
    ],
    'tools': [
        {
            'type': 'function',
            'function': {
                'name': 'get_weather',
                'description': 'Get current weather for a city',
                'parameters': {
                    'type': 'object',
                    'properties': {
                        'city': {'type': 'string', 'description': 'City name'},
                        'units': {'type': 'string', 'enum': ['celsius', 'fahrenheit'], 'description': 'Temperature units'}
                    },
                    'required': ['city']
                }
            }
        },
        {
            'type': 'function',
            'function': {
                'name': 'search_wiki',
                'description': 'Search Wikipedia for information',
                'parameters': {
                    'type': 'object',
                    'properties': {
                        'query': {'type': 'string', 'description': 'Search query'}
                    },
                    'required': ['query']
                }
            }
        }
    ],
    'max_tokens': 512,
    'temperature': 0.1
}
try:
    r = requests.post(endpoint, json=payload, timeout=120)
    resp = r.json()
    output = resp.get('choices', [{}])[0].get('message', {})
    with open('$OUTPUT_DIR/quality_tool_calling.md', 'w') as f:
        f.write('# Quality Test: Tool Calling\n')
        f.write('## Prompt\nWhat is the weather in San Francisco right now?\n\n')
        f.write('## Output\n\n' + json.dumps(output, indent=2) + '\n')
    print('  OK: quality_tool_calling.md')
except Exception as e:
    print(f'  FAIL: {e}', file=open('$OUTPUT_DIR/quality_tool_calling.md', 'w'))
" 2>/dev/null
    
    ok "Tool calling test written"
    
    # Long-context needle-in-haystack
    python3 -c "
import requests, json, random, string

endpoint = '$ENDPOINT/chat/completions'

# Generate filler text (~90K tokens worth of context, compressed)
# We'll use a concise format to stay within practical limits
filler = ' '.join([f'The capital of {country} is {cap}.' 
    for country, cap in {
        'France': 'Paris', 'Germany': 'Berlin', 'Italy': 'Rome', 'Spain': 'Madrid',
        'Portugal': 'Lisbon', 'Greece': 'Athens', 'Poland': 'Warsaw', 'Sweden': 'Stockholm',
        'Norway': 'Oslo', 'Finland': 'Helsinki', 'Denmark': 'Copenhagen', 'Iceland': 'Reykjavik',
        'Switzerland': 'Bern', 'Austria': 'Vienna', 'Czech Republic': 'Prague', 'Hungary': 'Budapest',
        'Romania': 'Bucharest', 'Bulgaria': 'Sofia', 'Croatia': 'Zagreb', 'Slovenia': 'Ljubljana',
        'Japan': 'Tokyo', 'China': 'Beijing', 'Korea': 'Seoul', 'Thailand': 'Bangkok',
        'Vietnam': 'Hanoi', 'Philippines': 'Manila', 'Indonesia': 'Jakarta', 'Singapore': 'Singapore',
        'Malaysia': 'Kuala Lumpur', 'India': 'New Delhi', 'Pakistan': 'Islamabad', 'Bangladesh': 'Dhaka',
        'Sri Lanka': 'Colombo', 'Nepal': 'Kathmandu', 'Myanmar': 'Naypyidaw', 'Cambodia': 'Phnom Penh',
        'Laos': 'Vientiane', 'Mongolia': 'Ulaanbaatar', 'Russia': 'Moscow', 'Turkey': 'Ankara',
        'Iran': 'Tehran', 'Iraq': 'Baghdad', 'Saudi Arabia': 'Riyadh', 'UAE': 'Abu Dhabi',
        'Egypt': 'Cairo', 'Morocco': 'Rabat', 'Algeria': 'Algiers', 'Tunisia': 'Tunis',
        'South Africa': 'Pretoria', 'Nigeria': 'Abuja', 'Kenya': 'Nairobi', 'Ethiopia': 'Addis Ababa',
        'Brazil': 'Brasilia', 'Argentina': 'Buenos Aires', 'Chile': 'Santiago', 'Peru': 'Lima',
        'Colombia': 'Bogota', 'Venezuela': 'Caracas', 'Mexico': 'Mexico City', 'Canada': 'Ottawa',
        'USA': 'Washington DC', 'Australia': 'Canberra', 'New Zealand': 'Wellington'
    }.items()]) * 200  # Repeat to bulk up context

needle = 'The secret code is ' + ''.join(random.choices('ABCDEFGHJKLMNPQRSTUVWXYZ23456789', k=8))
# Insert needle at random position
filler_list = filler.split('. ')
insert_pos = len(filler_list) // 2
filler_list.insert(insert_pos, needle)
context = '. '.join(filler_list)

payload = {
    'model': '$MODEL_ID',
    'messages': [
        {'role': 'system', 'content': 'Answer the question based ONLY on the context provided.'},
        {'role': 'user', 'content': context + f'\n\nQuestion: What is the secret code?'}
    ],
    'max_tokens': 64,
    'temperature': 0.0
}
try:
    r = requests.post(endpoint, json=payload, timeout=300)
    resp = r.json()
    output = resp.get('choices', [{}])[0].get('message', {}).get('content', 'ERROR')
    with open('$OUTPUT_DIR/quality_long_context.md', 'w') as f:
        f.write('# Quality Test: Long-Context Needle-in-Haystack\n')
        f.write(f'## Context length: ~{len(context)} chars\n')
        f.write(f'## Needle: {needle}\n\n')
        f.write(f'## Model response: {output.strip()}\n')
    print(f'  OK: quality_long_context.md (context: {len(context)} chars)')
except Exception as e:
    print(f'  FAIL: {e}', file=open('$OUTPUT_DIR/quality_long_context.md', 'w'))
" 2>/dev/null
    
    ok "Long-context test written"
    
    info "Review quality outputs in $OUTPUT_DIR/quality_*.md"
fi

# ============================================================
# STRESS TEST (concurrent requests)
# ============================================================
if [[ "$CATEGORY" == "all" ]] || [[ "$CATEGORY" == "stress" ]]; then
    section "Stress (Concurrent Requests)"
    info "Firing $CONCURRENT concurrent requests x $RUNS rounds"
    
    python3 -c "
import requests, json, time, sys, threading, statistics

endpoint = '$ENDPOINT/completions'
payload = {
    'model': '$MODEL_ID',
    'prompt': 'Briefly explain what recursion is.',
    'max_tokens': 50,
    'temperature': 0.0
}

results = {'success': 0, 'fail': 0, 'times': []}
lock = threading.Lock()

def do_request(idx):
    t0 = time.perf_counter()
    try:
        r = requests.post(endpoint, json=payload, timeout=60)
        elapsed = time.perf_counter() - t0
        with lock:
            results['success'] += 1
            results['times'].append(elapsed)
    except Exception as e:
        with lock:
            results['fail'] += 1

for round_idx in range($RUNS):
    threads = []
    for i in range($CONCURRENT):
        t = threading.Thread(target=do_request, args=(round_idx * $CONCURRENT + i,))
        threads.append(t)
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)

if not results['times']:
    print('ERROR: No successful stress runs')
    sys.exit(1)

avg_time = statistics.mean(results['times'])
total_tps = results['success'] / (avg_time * $CONCURRENT)

summary = {
    'concurrent': $CONCURRENT,
    'rounds': $RUNS,
    'total_requests': $RUNS * $CONCURRENT,
    'success': results['success'],
    'fail': results['fail'],
    'avg_time_per_request_s': round(avg_time, 3),
    'total_throughput_req_per_s': round(results['success'] / (avg_time * $CONCURRENT), 2)
}

print(json.dumps(summary, indent=2))
" 2>&1 > "$OUTPUT_DIR/stress.json"

    if [[ -f "$OUTPUT_DIR/stress.json" ]]; then
        OK_COUNT=$(python3 -c "import json; d=json.load(open('$OUTPUT_DIR/stress.json')); print(d.get('success','?'))" 2>/dev/null)
        FAIL_COUNT=$(python3 -c "import json; d=json.load(open('$OUTPUT_DIR/stress.json')); print(d.get('fail','?'))" 2>/dev/null)
        info "Concurrent: $CONCURRENT, Success: $OK_COUNT, Fail: $FAIL_COUNT"
        ok "Stress test complete"
    else
        fail "Stress test produced no output"
    fi
fi

# ============================================================
# GPU POST-STATE
# ============================================================
section "GPU State (Post-benchmark)"

VRAM_USED_AFTER=$(nvidia-smi --query-gpu=memory.used --format=csv,noheader 2>/dev/null | head -1 | tr -d ' MiB')
GPU_UTIL_AFTER=$(nvidia-smi --query-gpu=utilization.gpu --format=csv,noheader 2>/dev/null | head -1 | tr -d ' %')
info "VRAM used: $(awk "BEGIN {printf \"%.1f\", $VRAM_USED_AFTER / 1024}")GB"
info "GPU util: ${GPU_UTIL_AFTER}%"

# ============================================================
# COMPILE SUMMARY
# ============================================================
section "Compiling Results"

# Build summary JSON
python3 << PYEOF
import json, os, glob

output_dir = "$OUTPUT_DIR"
summary = {
    "timestamp": "$TIMESTAMP",
    "profile": "$PROFILE",
    "model": "$MODEL_PATH",
    "endpoint": "$ENDPOINT",
    "category": "$CATEGORY",
    "params": {
        "warmup": $WARMUP,
        "runs": $RUNS,
        "concurrent": $CONCURRENT,
        "tokens": $TOKENS
    },
    "gpu": {
        "vram_used_before_mb": $VRAM_USED_BEFORE,
        "vram_used_after_mb": $VRAM_USED_AFTER,
        "gpu_util_before_pct": $GPU_UTIL_BEFORE,
        "gpu_util_after_pct": $GPU_UTIL_AFTER
    }
}

# Load individual results
for fname in ['latency.json', 'throughput.json', 'stress.json', 'vram.json']:
    fpath = os.path.join(output_dir, fname)
    if os.path.exists(fpath):
        try:
            with open(fpath) as f:
                summary[fname.replace('.json', '')] = json.load(f)
        except:
            pass

with open(os.path.join(output_dir, 'summary.json'), 'w') as f:
    json.dump(summary, f, indent=2)

# Generate report
report = []
report.append(f"# Matrix Benchmark Report")
report.append(f"**Profile:** {summary['profile']}")
report.append(f"**Model:** {summary['model']}")
report.append(f"**Timestamp:** {summary['timestamp']}")
report.append(f"**Category:** {summary['category']}")
report.append("")

if 'latency' in summary:
    l = summary['latency']
    report.append("## Latency")
    report.append(f"- TTFT mean: {l.get('ttft_ms_mean', '?')}ms")
    report.append(f"- TTFT p95: {l.get('ttft_ms_p95', '?')}ms")
    report.append(f"- TTFT min/max: {l.get('ttft_ms_min', '?')} / {l.get('ttft_ms_max', '?')}ms")
    report.append("")

if 'throughput' in summary:
    t = summary['throughput']
    report.append("## Throughput")
    report.append(f"- Tokens/sec mean: {t.get('tokens_per_sec_mean', '?')}")
    report.append(f"- Tokens/sec min/max: {t.get('tokens_per_sec_min', '?')} / {t.get('tokens_per_sec_max', '?')}")
    report.append("")

if 'stress' in summary:
    s = summary['stress']
    report.append("## Stress")
    report.append(f"- Concurrent: {s.get('concurrent', '?')}")
    report.append(f"- Success: {s.get('success', '?')}, Fail: {s.get('fail', '?')}")
    report.append(f"- Avg time/request: {s.get('avg_time_per_request_s', '?')}s")
    report.append("")

report.append("## GPU")
g = summary['gpu']
report.append(f"- VRAM before: {g['vram_used_before_mb']}MB")
report.append(f"- VRAM after: {g['vram_used_after_mb']}MB")
report.append(f"- GPU util before/after: {g['gpu_util_before_pct']}% / {g['gpu_util_after_pct']}%")
report.append("")

report.append("## Quality Files (manual review required)")
quality_files = sorted(glob.glob(os.path.join(output_dir, 'quality_*.md')))
for qf in quality_files:
    report.append(f"- {os.path.basename(qf)}")
report.append("")

with open(os.path.join(output_dir, 'report.md'), 'w') as f:
    f.write('\n'.join(report))

print("  Summary + report written")
PYEOF

ok "Results saved to $OUTPUT_DIR/"

# ---- BASELINE COMPARISON ----
if (( DO_BASELINE )); then
    section "Baseline Comparison"
    
    BASELINE_FILE="$BASELINE_DIR/summary.json"
    
    # If --update-baseline, always create/update the baseline first
    if (( DO_UPDATE_BASELINE )); then
        mkdir -p "$BASELINE_DIR"
        cp "$OUTPUT_DIR/summary.json" "$BASELINE_FILE"
        ok "Baseline updated from $OUTPUT_DIR/summary.json"
    fi
    
    # If no baseline exists after the above, warn and skip comparison
    if [[ ! -f "$BASELINE_FILE" ]]; then
        warn "No baseline found at $BASELINE_FILE"
        info "Run with --update-baseline to create one"
    else
        # Compare key metrics
        python3 << PYEOF
import json

try:
    with open("$BASELINE_FILE") as f:
        baseline = json.load(f)
    with open("$OUTPUT_DIR/summary.json") as f:
        current = json.load(f)
    
    print("  Baseline: " + baseline.get('timestamp', '?'))
    print("  Current:  " + current.get('timestamp', '?'))
    print("")
    
    # Latency comparison
    bl_lat = baseline.get('latency', {})
    cur_lat = current.get('latency', {})
    if bl_lat and cur_lat:
        bl_ttft = bl_lat.get('ttft_ms_mean', 0)
        cur_ttft = cur_lat.get('ttft_ms_mean', 0)
        if bl_ttft:
            delta = ((cur_ttft - bl_ttft) / bl_ttft) * 100
            sign = '+' if delta > 0 else ''
            print(f"  TTFT: {bl_ttft}ms -> {cur_ttft}ms ({sign}{delta:.1f}%)")
    
    # Throughput comparison
    bl_tp = baseline.get('throughput', {})
    cur_tp = current.get('throughput', {})
    if bl_tp and cur_tp:
        bl_tps = bl_tp.get('tokens_per_sec_mean', 0)
        cur_tps = cur_tp.get('tokens_per_sec_mean', 0)
        if bl_tps:
            delta = ((cur_tps - bl_tps) / bl_tps) * 100
            sign = '+' if delta > 0 else ''
            print(f"  TPS: {bl_tps} -> {cur_tps} ({sign}{delta:.1f}%)")
    
    # VRAM comparison
    bl_gpu = baseline.get('gpu', {})
    cur_gpu = current.get('gpu', {})
    if bl_gpu and cur_gpu:
        bl_vram = bl_gpu.get('vram_used_after_mb', 0)
        cur_vram = cur_gpu.get('vram_used_after_mb', 0)
        if bl_vram:
            delta = ((cur_vram - bl_vram) / bl_vram) * 100
            sign = '+' if delta > 0 else ''
            print(f"  VRAM: {bl_vram}MB -> {cur_vram}MB ({sign}{delta:.1f}%)")
    
    print("")
    print("  ⚠ Quality tests require manual review of quality_*.md files")
except Exception as e:
    print(f"  Error comparing: {e}")
PYEOF
    fi
fi

echo -e "\n${CYAN}═══════════════════════════════════════${NC}"
ok "Benchmark complete!"
info "Results: $OUTPUT_DIR/"
info "Review:  $REPORT_FILE"
echo -e "${CYAN}═══════════════════════════════════════${NC}"
