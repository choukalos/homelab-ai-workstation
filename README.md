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
| **vLLM** | qwen38 | 8000 | Primary inference (Qwen3.8-27B NVFP4) |
| **Ollama** | ollama | 11434 | Light tasks (Gemma4 26B MoE) + embeddings |
| **node-exporter** | node-exporter | 9100 | System metrics |
| **dcgm-exporter** | dcgm-exporter | 9400 | GPU metrics |
| **ComfyUI** | comfyui_backend | 8188 | Image generation + editing (Qwen-Image; runs concurrently with vLLM) |
| **Media pipeline** | media_pipeline | 8189 | Media orchestrator (storyboard→shots→TTS/music/SFX→upscale→assemble); drives ComfyUI + vLLM, runs in a Docker container managed by model-manager |

All ports are LAN-only. Never exposed publicly.

## Current Mode: `qwen-coder`

> Check live mode with `cat state/current_mode` (source of truth). Last switch: 2026-08-23 experiment → qwen-coder.

- **matrix-coder** — Qwen3.8-27B NVFP4 via vLLM (primary model for chat, coding, tools, agents, vision)
- **embeddings** — Nomic Embed Text via Ollama
- **matrix-gemma4-moe** — offline in this mode (Gemma4 26B MoE loads on demand; available in `daily`/`llms`)
- **media pipeline** — ComfyUI + media-pipeline run concurrently (image/video/music generation)

## Modes

| Mode | vLLM | Ollama | ComfyUI | Use Case |
|---|---|---|---|---|
| `daily` | Qwen3.8-27B NVFP4 | Gemma4 + embeddings | ✅ (concurrent) | Normal use |
| `qwen-coder` | Qwen3.8-27B NVFP4 | embeddings only | ✅ (concurrent) | Max coding performance |
| `qwen-long` | Qwen3.6-27B (240K ctx) | embeddings only | ✅ (concurrent) | Long-context work |
| `llms` | Qwen3.8-27B NVFP4 | Gemma4 + embeddings | ✅ (concurrent) | Multi-model tool experiments |
| `experiment` | Candidate model | embeddings | ✅ (concurrent) | Test new models |

> Image generation is **not a mode** — ComfyUI (Qwen-Image, ~12 GB VRAM cap) runs
> concurrently with vLLM. The **media-pipeline** service (port 8189) orchestrates the full
> media flow on top of ComfyUI + vLLM and is managed as a unit with ComfyUI (see
> [ComfyUI Media API](docs/matrix_comfyui_media_api.md) and `media-pipeline/`).

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
./scripts/model-manager experiment start --profile <PROFILE>

# Start an ad-hoc experiment with a model path
./scripts/model-manager experiment start <MODEL_PATH>

# Switch between experiments (no rollback needed)
./scripts/model-manager experiment switch <EXPERIMENT>

# View experiment profile details
./scripts/model-manager experiment show <PROFILE>

# View experiment history log
./scripts/model-manager experiment archive
```

### Experiment Profiles

Named experiments are defined by a YAML profile and a Docker Compose file:

| Profile | Model | VRAM | Notes |
|---|---|---|---|
| *(none — 2026-09-27 cleanup)* | — | — | Round-1 (2026-07-05) and Qwen3.8-round profiles removed; results archived in [EXPERIMENTS_RESULTS.md](EXPERIMENTS_RESULTS.md). Sole remaining candidate: **Experiment 7 — Qwen3.8-Flash-Next 125B MoE**, `compose/experiments/qwen38-flash-next-gguf.yml` (llama.cpp, 4-bit GGUF; strongest daily-driver candidate on paper) |

> **Note:** The `--profile` flag lets you jump between experiments without rolling back to production. Use `mode rollback` to return to the previous production mode.

> **Removed profiles:** Nemotron-3-Nano (Mamba-2/Transformer not vLLM-compatible) and Qwen3-Next NVFP4 (TensorRT-LLM-only format) were removed during compatibility research. **2026-09-27:** all remaining round-1/Qwen3.8-round experiment profiles + compose files removed (results in EXPERIMENTS_RESULTS.md); experiments 3 & 6 dropped (old models — Flash-Next is the next candidate), 4 & 5 superseded by Qwen3.8.

## Directory Layout

```
home/
  compose/              # Docker Compose configs (canonical)
    qwen-coder.yml      # vLLM Qwen3.8-27B NVFP4 (primary)
    qwen-long.yml       # vLLM Qwen3.6-27B (long-context, optimized)
    gemma4-moe.yml      # Ollama (gemma4 + embeddings)
    experiment.yml      # vLLM template (copy & edit)
    comfyui.yml         # ComfyUI (Qwen-Image create + edit) + media-pipeline orchestrator
    metrics.yml         # node-exporter + dcgm-exporter
    experiments/        # Named experiment compose files (2026-09-27: only Experiment 7 remains)
      qwen38-flash-next-gguf.yml
    legacy/             # Archived pre-migration files
  models/profiles/      # Declarative model profiles
    experiment-*.yaml   # Experiment profile definitions
  scripts/              # Operational tools
    model-manager       # Mode switching & state management
    preflight.sh        # Pre-launch validation
    benchmark.sh        # Performance benchmarks
    matrix_health.sh    # Quick health checks
    vllm_feature_eval.sh # vLLM feature testing
    comfyui_venv_deps.sh # Reinstall custom-node deps after a ComfyUI venv rebuild (numpy<2.5 pin)
    backup/              # Lego NAS backup: rsync-over-CIFS snapshots (docs/matrix_backup.md + backup/README.md)
  docs/                 # Design docs & reference materials
  media-pipeline/       # Media orchestrator service (Docker build context: server.py, workflows.py, Dockerfile)
  media-mcp-client/     # Remote MCP client (thin HTTP client + FastMCP tools for the media pipeline)
  qwen3.8-experiment/   # Qwen3.8 validation scripts (image analysis, tool-calling tests)
  data/benchmarks/      # Benchmark baseline + result snapshots
  state/                # Runtime state (gitignored)
    experiment_archive.md  # Experiment start/end history
  EXPERIMENTS_RESULTS.md # Experiment round results & promotion history
  TODO.md               # Consolidated open work
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
| Rebuild media-pipeline | `./scripts/model-manager rebuild media-pipeline` |

## Docs

Detailed design docs are in `docs/`:

- [Runtime Modes](docs/matrix_runtime_modes.md) — Per-mode operational guides
- [Model Manager](docs/matrix_model_manager.md) — CLI design & mode switch flow
- [Thor Contract](docs/matrix_thor_contract.md) — Integration contract between Thor and Matrix
- [Optimization Profiles](docs/matrix_optimization_profiles.md) — Arg rationale & VRAM budget
- [vLLM Features](docs/matrix_vllm_features.md) — Feature status & eval plans
- [ComfyUI (Images)](docs/matrix_images_mode.md) — ComfyUI operational guide
- [ComfyUI Media API](docs/matrix_comfyui_media_api.md) — media pipeline tooling contract (images, video, TTS, music, SFX, upscale, assemble)
- [Media-Pipeline API](docs/matrix_media_pipeline_api.md) — port 8189 contract: endpoints, job model, queue, VRAM budget, metering (incl. TTS voice library, 2026-09-25)
- [MCP Media Client](media-mcp-client/HANDOFF.md) — thor MCP tooling over the pipeline: contract, reference code, voice-library tools (`media_list_voices` / `media_add_voice` / `media_delete_voice`), Qwen-Image-2.1 defaults (`media-mcp-client/README.md`)
- [Monitoring](docs/matrix_monitoring_health.md) — Health endpoints & metrics
- [Benchmark Plan](docs/matrix_benchmark_plan.md) — Performance testing approach
- [Embeddings Decision](docs/matrix_embeddings_decision.md) — Matrix vs. Thor placement decision
- [Inventory](docs/matrix_inventory.md) — Append-only system snapshots
- [Backup](docs/matrix_backup.md) — `/home/chuck/data` → Lego NAS: design, scope decisions, incident history (operations: `scripts/backup/README.md`)
- [Manual Tasks](docs/matrix_manual_tasks.md) — Operator-approval tasks (resolved + open)
- [Validation Log](docs/matrix_validation_log.md) — Post-change validation evidence
- [TODO](TODO.md) — Consolidated open work
- [Experiment Results](EXPERIMENTS_RESULTS.md) — Experiment rounds, fixes, promotions
