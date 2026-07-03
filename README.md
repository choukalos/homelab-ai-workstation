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

# Start a candidate model for testing
./scripts/model-manager experiment start <MODEL_PATH>

# Test with Multi-Token Prediction
./scripts/model-manager experiment start --mtp <MODEL_PATH>
```

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
    legacy/             # Archived pre-migration files
  models/profiles/      # Declarative model profiles
  scripts/              # Operational tools
    model-manager       # Mode switching & state management
    preflight.sh        # Pre-launch validation
    benchmark.sh        # Performance benchmarks
    matrix_health.sh    # Quick health checks
    vllm_feature_eval.sh # vLLM feature testing
  docs/                 # Design docs & reference materials
  state/                # Runtime state (gitignored)
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
