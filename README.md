# Matrix — AI Compute Appliance

> Matrix is a dedicated GPU inference appliance. Thor is the stable platform layer. Matrix is the model engine.

## Architecture

```
Thor clients & tools
  → Thor LiteLLM (stable API surface)
    → Matrix model profiles
      → vLLM / Ollama / ComfyUI runtimes
        → RTX PRO 5000 72 GB GPU
```

## Runtime

| Component | Service | Port | Role |
|---|---|---|---|
| **vLLM** | qwen36 | 8000 | Primary inference (Qwen3.6-27B) |
| **Ollama** | ollama | 11434 | Light tasks (Gemma4 26B MoE) + embeddings |
| **node-exporter** | node-exporter | 9100 | System metrics |
| **dcgm-exporter** | dcgm-exporter | 9400 | GPU metrics |
| **ComfyUI** | comfyui_backend | 8188 | Image generation (stopped by default) |

All ports are LAN-only. Never exposed publicly.

## Current Mode: `daily`

- **matrix-coder** — Qwen3.6-27B via vLLM (primary model for chat, coding, tools, agents)
- **matrix-gemma4-moe** — Gemma4 26B MoE via Ollama (light/fast tasks)
- **embeddings** — Nomic Embed Text via Ollama

## Modes

| Mode | vLLM | Ollama | ComfyUI | Use Case |
|---|---|---|---|---|
| `daily` | Qwen3.6-27B | Gemma4 + embeddings | — | Normal use |
| `qwen-coder` | Qwen3.6-27B | embeddings only | — | Max coding performance |
| `qwen-long` | Qwen3.6-27B (240K ctx) | embeddings only | — | Long-context work |
| `experiment` | Candidate model | embeddings | — | Test new models |
| `images` | — | Gemma4 + embeddings | ComfyUI/FLUX | Image generation |

### Switching Modes

```bash
# Check current mode
./scripts/model-manager status

# Switch modes (interactive)
./scripts/model-manager mode switch <MODE>

# Switch non-interactively
./scripts/model-manager mode switch --yes <MODE>

# Roll back to previous mode
./scripts/model-manager mode rollback

# --- Experiments ---
# List available experiment profiles
./scripts/model-manager experiment list

# Start a named experiment (uses pre-configured profile)
./scripts/model-manager experiment start --profile experiment-gemma4-31b

# Start an ad-hoc experiment with a model path
./scripts/model-manager experiment start <MODEL_PATH>

# Switch between experiments (no rollback needed)
./scripts/model-manager experiment switch experiment-nemotron-3-nano-30b

# View experiment profile details
./scripts/model-manager experiment show experiment-gemma4-31b

# View experiment history log
./scripts/model-manager experiment archive
```

### Experiment Profiles

Named experiments are defined by a YAML profile and a Docker Compose file:

| Profile | Model | VRAM | Notes |
|---|---|---|---|
| `experiment-gemma4-31b` | google/gemma-4-31b-it | ~35-45 GB | Dense 31B, FP8 runtime quantization, good for general tasks |
| `experiment-qwen3-next-80b-thinking-fp8-mtp` | Qwen/Qwen3-Next-80B-A3B-Thinking-FP8 | ~55-62 GB | 80B MoE (3B active), FP8 + MTP, thinking mode, nightly vLLM |
| `experiment-qwen36-27b-w8a16-128k-mtp` | 88plug/Qwen3.6-27B-W8A16 | ~40-48 GB | W8A16 INT8, 128K ctx, 3 threads, MTP — best daily-coder candidate (Path A) |
| `experiment-qwen36-int4-mtp` | Lorbus/Qwen3.6-27b-int4-AutoRound | ~48-52 GB | Same model as daily + MTP — minimal-risk throughput upgrade (Path B) |
| `experiment-qwen-long-w8a16-mtp` | 88plug/Qwen3.6-27B-W8A16 | ~35-42 GB | W8A16 INT8, 262K ctx, 4 threads, MTP — max long context |

> **Note:** The `--profile` flag lets you jump between experiments without rolling back to production. Use `mode rollback` to return to the previous production mode.

> **Removed profiles:** Nemotron-3-Nano (Mamba-2/Transformer not vLLM-compatible) and Qwen3-Next NVFP4 (TensorRT-LLM-only format) were removed during compatibility research. See TODO.md for details.

## Directory Layout

```
home/
  compose/              # Docker Compose configs (canonical)
    qwen-coder.yml      # vLLM Qwen3.6-27B (primary)
    qwen-long.yml       # vLLM Qwen3.6-27B (long-context, optimized)
    gemma4-moe.yml      # Ollama (gemma4 + embeddings)
    experiment.yml      # vLLM template (copy & edit)
    comfyui.yml         # ComfyUI / FLUX
    metrics.yml         # node-exporter + dcgm-exporter
    experiments/        # Named experiment compose files
      gemma4-31b.yml
      nemotron-3-nano-30b.yml
      qwen3-next-80b-nvfp4.yml
    legacy/             # Archived pre-migration files
  models/profiles/      # Declarative model profiles
    experiment-*.yaml   # Experiment profile definitions
  scripts/              # Operational tools
    model-manager       # Mode switching & state management
    preflight.sh        # Pre-launch validation
    benchmark.sh        # Performance benchmarks
    matrix_health.sh    # Quick health checks
    vllm_feature_eval.sh # vLLM feature testing
  docs/                 # Design docs & reference materials
  state/                # Runtime state (gitignored)
    experiment_archive.md  # Experiment start/end history
  .env                  # Environment vars (gitignored)
```

## Key Principles

- **Thor LiteLLM is the stable API** — clients never call Matrix ports directly
- **One primary model** — `matrix-coder` is the main model; no variants
- **Experiment slot is transparent** — swapping the vLLM model replaces `matrix-coder` without config changes
- **Matrix is compute-only** — no orchestration, reverse proxy, or app hosting
- **Modes are manual** — operator decides when to switch; no auto-switching
- **Metrics never stop** — node-exporter and dcgm-exporter are always running

## Quick Reference

| Task | Command |
|---|---|
| Check all services | `./scripts/model-manager health` |
| List profiles | `./scripts/model-manager list` |
| Validate a profile | `./scripts/model-manager profile validate <NAME>` |
| Run preflight | `./scripts/model-manager preflight <MODE>` |
| Run benchmarks | `./scripts/model-manager benchmark <PROFILE>` |
| Show profile details | `./scripts/model-manager profile show <NAME>` |
| List experiments | `./scripts/model-manager experiment list` |
| Start experiment | `./scripts/model-manager experiment start --profile <NAME>` |
| Switch experiments | `./scripts/model-manager experiment switch <NAME>` |
| Experiment history | `./scripts/model-manager experiment archive` |
| Health check (JSON) | `./scripts/matrix_health.sh --json` |

## Docs

Detailed design docs are in `docs/`:

- [Runtime Modes](docs/matrix_runtime_modes.md) — Per-mode operational guides
- [Model Manager](docs/matrix_model_manager.md) — CLI design & mode switch flow
- [Thor Contract](docs/matrix_thor_contract.md) — Integration contract between Thor and Matrix
- [Optimization Profiles](docs/matrix_optimization_profiles.md) — Arg rationale & VRAM budget
- [vLLM Features](docs/matrix_vllm_features.md) — Feature status & eval plans
- [Images Mode](docs/matrix_images_mode.md) — ComfyUI operational guide
- [Monitoring](docs/matrix_monitoring_health.md) — Health endpoints & metrics
- [Benchmark Plan](docs/matrix_benchmark_plan.md) — Performance testing approach
