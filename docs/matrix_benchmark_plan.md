# Matrix Benchmark Plan

> Created: 2026-07-03
> Status: Design + script draft

## Purpose

Optimize Qwen settings without guessing. Every profile change must be benchmarked
against a **baseline** before and after. Raw numbers are secondary to coding quality
and tool/agent performance.

## Benchmark Categories

| Category | What We Measure | Tool / Method |
|---|---|---|
| **Code generation** | Correctness, completeness of generated code | Local prompts + manual review |
| **Refactoring** | Ability to transform existing code correctly | Local prompts + manual review |
| **Debugging** | Ability to find and fix bugs | Local prompts + manual review |
| **Tool calling** | Correct function selection, arg formatting | vLLM chat completions with tool defs |
| **Long-context retrieval** | Needle-in-haystack accuracy at various context lengths | Synthetic prompts (1K–128K tokens) |
| **Agent loops** | Multi-step reasoning success rate | LiteLLM harness with 5-step tasks |
| **First token latency** | Time to first token (TTFT) | vLLM `/v1/completions` + timing |
| **Tokens/sec** | Throughput (prefill + decode) | vLLM `/v1/completions` + timing |
| **VRAM steady state** | VRAM after warmup | `nvidia-smi` snapshot |
| **GPU utilization** | % SM active during decode | `nvidia-smi` / dcgm-exporter |
| **CPU utilization** | CPU load during inference | `top` / node-exporter |
| **Concurrent requests** | Throughput under N parallel requests | vLLM `/v1/completions` xN |

## Architecture

```
/home/chuck/homelab/
├── scripts/
│   └── benchmark.sh          # Local benchmark runner (bash + python3)
├── docs/
│   └── matrix_benchmark_plan.md   # This file
├── data/benchmarks/
│   ├── baseline/               # Baseline results (JSON + markdown)
│   └── results/                # Result snapshots per run
│       └── 2026-07-03_12-00/   # Timestamped directories
└── models/profiles/            # Profiles to benchmark
```

## Benchmark Script: `scripts/benchmark.sh`

```text
benchmark.sh [--profile PROFILE] [--category CAT] [--output DIR]
```

Arguments:
- `--profile`: Profile to benchmark (default: current active profile)
- `--category`: One of: `all`, `latency`, `throughput`, `quality`, `stress`, `gpu`
- `--output`: Output directory (default: `data/benchmarks/results/TIMESTAMP`)
- `--baseline`: Compare against baseline and print diff
- `--vram-only`: Quick VRAM snapshot only (fast)

## Benchmark Workflow

```
1.  Record baseline (first run with current settings)
    └── benchmark.sh --profile matrix-coder --category all --baseline

2.  Change ONE variable (e.g., max_seq_len, gpu_memory_utilization)

3.  Run benchmark again
    └── benchmark.sh --profile matrix-coder --category all

4.  Compare
    └── benchmark.sh --profile matrix-coder --category all --baseline

5.  Evaluate:
    - Did coding quality improve/stay the same?
    - Did latency/throughput improve?
    - Did VRAM fit?
    - Roll back if quality regresses
```

## Quality Benchmarks (Manual Review)

Quality benchmarks are **NOT automated**. They produce outputs that Chuck reviews:

| Test | Prompt | Success Criteria |
|---|---|---|
| Code gen | "Write a bash script that parses docker logs and finds errors" | Script runs, handles edge cases |
| Refactoring | "Refactor this Python class to use dependency injection" | Correct patterns, no bugs |
| Debugging | Given buggy code + error output, find root cause | Correct diagnosis |
| Tool calling | Given tool defs + user request, select correct tool | Right tool, right args |
| Long context | 100K context with needle at various positions | Retrieves needle correctly |
| Agent loop | 5-step task: search → read → analyze → write → review | Completes all steps without hallucination |

The script writes these outputs to the result directory with filenames like
`quality_code_gen.md`, `quality_tool_calling.md`, etc.

## Performance Benchmarks (Automated)

### Latency
```bash
# TTFT + total time for 5 warmup + 20 real runs
curl -s -w "%{time_starttransfer} %{time_total}" \
  -H "Content-Type: application/json" \
  -d '{"model":"qwen36-27b","prompt":"Hello","max_tokens":1}' \
  http://localhost:8000/v1/completions
```

### Throughput
```bash
# Generate a fixed 1024-token response, measure tokens/sec
curl -s -d '{"model":"qwen36-27b","prompt":"Explain quantum computing in detail...","max_tokens":1024}' \
  http://localhost:8000/v1/completions
# Parse response tokens / total time
```

### VRAM steady state
```bash
# After warmup, snapshot VRAM
nvidia-smi --query-gpu=memory.used,memory.total,utilization.gpu --format=csv
```

### Concurrent requests
```bash
# Fire N parallel requests, measure total throughput
for i in $(seq 1 $N); do
  curl -s ... &
done
# Count total tokens / total wall-clock time
```

### GPU utilization during benchmark
```bash
# Poll dcgm-exporter or nvidia-smi during test
watch -n 1 nvidia-smi
```

## Output Format

Each benchmark run writes a JSON summary and a human-readable markdown report:

**`data/benchmarks/results/2026-07-03_12-00/summary.json`:**
```json
{
  "timestamp": "2026-07-03T12:00:00Z",
  "profile": "matrix-coder",
  "model": "Lorbus/Qwen3.6-27b-int4-AutoRound",
  "vllm_args": { "gpu_memory_utilization": 0.56, "max_model_len": 32768 },
  "latency": { "ttft_ms": 450, "ttft_p95_ms": 520 },
  "throughput": { "tokens_per_sec": 18.5 },
  "gpu": { "vram_used_gb": 48.7, "gpu_util_pct": 65 },
  "quality": { "code_gen": "pending_review", "tool_calling": "pending_review" }
}
```

**`data/benchmarks/results/2026-07-03_12-00/report.md`:**
Human-readable markdown with tables, pass/fail indicators, and delta vs baseline.

## Baseline Protocol

1. The first run with `--baseline` creates `data/benchmarks/baseline/`
2. Subsequent runs with `--baseline` compare against that file
3. To update baseline: `benchmark.sh --baseline --update`
4. Baseline is locked until explicitly updated

## Rules

1. **Record baseline before changing settings.** Never change settings without a benchmark first.
2. **Change one variable at a time.** One param, one benchmark run.
3. **Roll back if coding quality regresses.** Speed is irrelevant if the code is wrong.
4. **Raw benchmark numbers are secondary to coding quality and tool/agent performance.**
5. **Quality tests require manual review.** The script produces outputs; Chuck judges them.

## Future Enhancements (Phase 14+)

- Integrate with existing LiteLLM harness for agent loop tests
- Add automated quality scoring (e.g., eval-based)
- Track benchmark history over time (line charts in Grafana on Thor)
