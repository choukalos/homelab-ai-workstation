#!/usr/bin/env bash
# daily_candidate_bench — Focused harness for the daily-model decision (add_card_plan.md)
#
# Complements scripts/benchmark.sh (which covers standard latency/throughput/quality/
# stress) with the two things the QNF (Flash-Next) decision specifically needs:
#
#   1. QSA context-scaling probe — measures decode tok/s at increasing context lengths.
#      Flat tok/s as context grows  => llama.cpp implements QSA sparse attention
#      (bounded KV reads). Collapsing tok/s  => dense-256K fallback (KV-read dominated).
#      This is the #1 unknown gating the Flash-Next experiment (Phase 0b/5).
#   2. N x 256K concurrency — N concurrent full-context sessions (the real workload
#      shape: long agent sessions), measuring aggregate + per-session tok/s.
#
# Also captures the PCIe link width (x16 vs x8) as metadata so the x16 baseline
# (Phase 0b, pre-install) and the x8 run (Phase 5, post-install) are directly comparable.
#
# Usage:
#   daily_candidate_bench.sh --label x16-baseline [--endpoint URL] [--model ID]
#                            [--ctx 32768,65536,131072,229376] [--n256k 2] [--delegate]
#
#   --label NAME      Label for the run dir (e.g. x16-baseline, x8). Default: auto.
#   --endpoint URL    OpenAI-compatible base (default http://localhost:8000/v1)
#   --model ID        Model id (default: first from /models)
#   --ctx LIST        Comma-sep context sizes for the QSA probe (default 32768,65536,131072,229376)
#   --n256k N         N concurrent 256K sessions for the concurrency test (default 2)
#   --delegate        Also run scripts/benchmark.sh (standard categories)
#   --help            Show this help
#
# Output: data/benchmarks/daily_candidate/<label>_<timestamp>/  (summary.json + report.md)

set -uo pipefail

BASE_DIR="/home/chuck/homelab"
OUT_ROOT="$BASE_DIR/data/benchmarks/daily_candidate"
ENDPOINT="http://localhost:8000/v1"
MODEL=""
LABEL=""
CTX_LIST="32768,65536,131072,229376"
N256K=2
DELEGATE=0
DECODE_TOKENS=256

GREEN='\033[0;32m'; CYAN='\033[0;36m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
section() { echo -e "\n${CYAN}=== $1 ===${NC}"; }
info()    { echo -e "  $1"; }
ok()      { echo -e "  ${GREEN}✓${NC}  $1"; }
warn()    { echo -e "  ${YELLOW}⚠${NC}  $1"; }
fail()    { echo -e "  ${RED}✗${NC}  $1"; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --label)    LABEL="$2"; shift 2 ;;
    --endpoint) ENDPOINT="$2"; shift 2 ;;
    --model)    MODEL="$2"; shift 2 ;;
    --ctx)      CTX_LIST="$2"; shift 2 ;;
    --n256k)    N256K="$2"; shift 2 ;;
    --delegate) DELEGATE=1; shift ;;
    --help)     head -30 "$0" | tail -28; exit 0 ;;
    *) echo "Unknown option: $1"; exit 2 ;;
  esac
done

TS=$(date -u '+%Y%m%d_%H%M%S')
# Auto-label from the current PCIe link width if not given
if [[ -z "$LABEL" ]]; then
  LINK=$(nvidia-smi --query-gpu=pcie.link.width.current --format=csv,noheader 2>/dev/null | head -1 | tr -d ' ')
  LABEL="link${LINK:-?}"
fi
OUT_DIR="$OUT_ROOT/${LABEL}_${TS}"
mkdir -p "$OUT_DIR"
info "Output: $OUT_DIR"

# ---- Capture PCIe link width (x16 vs x8) as metadata ----
section "PCIe Link State"
PCIE_CSV=$(nvidia-smi --query-gpu=index,name,pcie.link.gen.current,pcie.link.width.current --format=csv 2>/dev/null || echo "n/a")
echo "$PCIE_CSV"
echo "$PCIE_CSV" > "$OUT_DIR/pcie.csv"

# ---- Resolve model id ----
if [[ -z "$MODEL" ]]; then
  MODEL=$(curl -sf --max-time 5 "$ENDPOINT/models" 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin)['data'][0]['id'])" 2>/dev/null || echo "unknown")
fi
info "Endpoint: $ENDPOINT   Model: $MODEL"

if ! curl -sf --max-time 5 "$ENDPOINT/models" &>/dev/null; then
  fail "Endpoint $ENDPOINT not responding. Is the candidate running on :8000?"
  exit 1
fi
ok "Endpoint responding"

# ============================================================
# QSA CONTEXT-SCALING PROBE
# ============================================================
section "QSA Context-Scaling Probe (decode tok/s vs context length)"
info "Contexts: $CTX_LIST   decode tokens/run: $DECODE_TOKENS"

python3 - "$ENDPOINT" "$MODEL" "$CTX_LIST" "$DECODE_TOKENS" "$OUT_DIR" <<'PYEOF'
import sys, json, time, statistics, requests

endpoint, model, ctx_list, decode_tokens, out_dir = sys.argv[1:6]
ctx_sizes = [int(c) for c in ctx_list.split(",")]
chat = f"{endpoint}/chat/completions"

# Build a deterministic filler of ~N tokens (~4 chars/token). Repeating a varied
# sentence keeps it tokenizable and avoids degenerate repetition penalties.
def filler(tokens):
    base = "The quick brown fox jumps over the lazy dog near the riverbank at dawn. "
    per = len(base.split())  # ~13 words ~ 16 tokens
    reps = max(1, int(tokens / per))
    return (base * reps)[: int(tokens * 4)]

results = []
for ctx in ctx_sizes:
    prompt = filler(ctx) + "\n\nIn one short sentence, summarize the mood of the text above."
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": decode_tokens,
        "temperature": 0.0,
        "stream": True,
    }
    ttft_ms = None
    tps = None
    t0 = time.perf_counter()
    first = None
    n_tokens = 0
    try:
        with requests.post(chat, json=payload, timeout=900, stream=True) as r:
            buf = ""
            for chunk in r.iter_content(chunk_size=512):
                if not chunk:
                    continue
                if first is None:
                    first = time.perf_counter()
                    ttft_ms = (first - t0) * 1000
                buf += chunk.decode("utf-8", "replace")
                n_tokens = len(buf.split())  # rough token count
        total = time.perf_counter() - t0
        if first is not None and n_tokens > 0:
            decode_s = total - (first - t0)
            if decode_s > 0:
                tps = n_tokens / decode_s
    except Exception as e:
        print(f"  ctx={ctx}: FAILED {e}", file=sys.stderr)

    results.append({
        "ctx_tokens": ctx,
        "ttft_ms": round(ttft_ms, 1) if ttft_ms else None,
        "decode_tps": round(tps, 2) if tps else None,
        "out_tokens_approx": n_tokens,
    })
    print(f"  ctx={ctx:>7,}  TTFT={ttft_ms and round(ttft_ms)/1000:.1f}s  decode={tps and round(tps,1)} tok/s")

with open(f"{out_dir}/qsa_probe.json", "w") as f:
    json.dump(results, f, indent=2)

# QSA signal: compare tok/s at the smallest vs largest context.
good = [r for r in results if r["decode_tps"]]
if len(good) >= 2:
    first_tps = good[0]["decode_tps"]
    last_tps = good[-1]["decode_tps"]
    ratio = last_tps / first_tps if first_tps else 0
    print()
    if ratio > 0.7:
        print(f"  SIGNAL: tok/s holds {ratio*100:.0f}% from {good[0]['ctx_tokens']:,} to {good[-1]['ctx_tokens']:,} ctx")
        print("  => consistent with QSA sparse attention (bounded KV reads). GOOD sign.")
    else:
        print(f"  SIGNAL: tok/s drops to {ratio*100:.0f}% from {good[0]['ctx_tokens']:,} to {good[-1]['ctx_tokens']:,} ctx")
        print("  => consistent with DENSE attention fallback (KV-read dominated). Concern.")
PYEOF

# ============================================================
# N x 256K CONCURRENCY
# ============================================================
section "N x 256K Concurrency (N=$N256K full-context sessions)"

python3 - "$ENDPOINT" "$MODEL" "$N256K" "$DECODE_TOKENS" "$OUT_DIR" <<'PYEOF'
import sys, json, time, threading, statistics, requests

endpoint, model, n, decode_tokens, out_dir = sys.argv[1:6]
ctx = 262144  # full context
chat = f"{endpoint}/chat/completions"

def filler(tokens):
    base = "The quick brown fox jumps over the lazy dog near the riverbank at dawn. "
    per = 16
    reps = max(1, int(tokens / per))
    return (base * reps)[: int(tokens * 4)]

prompt = filler(ctx - 512) + "\n\nIn one short sentence, summarize the mood."
payload = {
    "model": model,
    "messages": [{"role": "user", "content": prompt}],
    "max_tokens": decode_tokens,
    "temperature": 0.0,
    "stream": True,
}

results = {"per_session": [], "errors": 0}
lock = threading.Lock()

def run_session(idx):
    t0 = time.perf_counter()
    first = None
    n_tok = 0
    try:
        with requests.post(chat, json=payload, timeout=1800, stream=True) as r:
            buf = ""
            for chunk in r.iter_content(chunk_size=512):
                if not chunk:
                    continue
                if first is None:
                    first = time.perf_counter()
                buf += chunk.decode("utf-8", "replace")
                n_tok = len(buf.split())
        total = time.perf_counter() - t0
        decode_s = total - (first - t0) if first else total
        tps = (n_tok / decode_s) if (decode_s > 0 and n_tok) else None
        with lock:
            results["per_session"].append({
                "session": idx,
                "ttft_ms": round((first - t0) * 1000, 1) if first else None,
                "decode_tps": round(tps, 2) if tps else None,
                "out_tokens_approx": n_tok,
            })
    except Exception as e:
        with lock:
            results["errors"] += 1
        print(f"  session {idx}: FAILED {e}", file=sys.stderr)

t_start = time.perf_counter()
threads = [threading.Thread(target=run_session, args=(i,)) for i in range(n)]
for t in threads: t.start()
for t in threads: t.join(timeout=2000)
wall = time.perf_counter() - t_start

ok_sessions = [s for s in results["per_session"] if s.get("decode_tps")]
summary = {
    "n_sessions": n,
    "ctx_tokens": ctx,
    "wall_s": round(wall, 1),
    "errors": results["errors"],
    "per_session_tps_mean": round(statistics.mean([s["decode_tps"] for s in ok_sessions]), 2) if ok_sessions else None,
    "per_session_tps_min": round(min([s["decode_tps"] for s in ok_sessions]), 2) if ok_sessions else None,
    "aggregate_tps": round(sum([s["out_tokens_approx"] for s in ok_sessions]) / wall, 1) if ok_sessions and wall else None,
    "per_session": results["per_session"],
}
with open(f"{out_dir}/n256k.json", "w") as f:
    json.dump(summary, f, indent=2)
print(f"  wall={wall:.0f}s  errors={results['errors']}  per-session mean={summary['per_session_tps_mean']} tok/s  aggregate={summary['aggregate_tps']} tok/s")

PYEOF

# ============================================================
# SUMMARY
# ============================================================
section "Summary"
python3 - "$OUT_DIR" "$LABEL" "$ENDPOINT" "$MODEL" <<'PYEOF'
import sys, json, os
out_dir, label, endpoint, model = sys.argv[1:5]
summary = {"label": label, "endpoint": endpoint, "model": model}
for fn in ["qsa_probe.json", "n256k.json"]:
    p = os.path.join(out_dir, fn)
    if os.path.exists(p):
        summary[fn.replace(".json","")] = json.load(open(p))
try:
    summary["pcie"] = open(os.path.join(out_dir,"pcie.csv")).read().strip()
except: pass
json.dump(summary, open(os.path.join(out_dir,"summary.json"),"w"), indent=2)
print("  summary.json written")
PYEOF

if (( DELEGATE )); then
  section "Delegating to scripts/benchmark.sh (standard categories)"
  bash "$BASE_DIR/scripts/benchmark.sh" --output "$OUT_DIR" || warn "delegate benchmark.sh returned non-zero"
fi

echo -e "\n${CYAN}═══════════════════════════════════════${NC}"
ok "Daily-candidate bench complete"
info "Results: $OUT_DIR/"
info "Compare x16 vs x8: diff the two summary.json (QSA probe + N x 256K)."
echo -e "${CYAN}═══════════════════════════════════════${NC}"