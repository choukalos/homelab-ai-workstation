# Matrix Model Manager — Design Doc

> Created: 2026-07-03
> Status: Design only. No implementation yet.

## What It Is

A shell script at `/home/chuck/homelab/scripts/model-manager` that reads declarative
profile YAMLs and orchestrates safe mode switches, health checks, and rollbacks.

**It is NOT an automation daemon.** Every mode switch requires an explicit operator
command. The manager's job is to make that switch *safe*, *logged*, and *repeatable*.

## Command Interface

```text
model-manager status                    # Current active mode + running profiles
model-manager list                      # All available profiles (active + planned)
model-manager mode current              # Show current mode
model-manager mode switch <MODE>        # Switch to a mode (interactive confirmation)
model-manager mode rollback             # Rollback to last known good mode
model-manager profile show <PROFILE>    # Show a profile's full spec
model-manager profile validate <P>      # Validate a profile's compose/args/env
model-manager health                    # Check all running services
model-manager preflight <MODE>          # Run preflight checks without switching
model-manager benchmark <PROFILE>       # Run benchmark harness against a profile
```

## Architecture

```
/home/chuck/homelab/
├── scripts/
│   └── model-manager          # Main CLI (bash)
├── models/
│   └── profiles/              # Profile YAMLs (read by manager)
│       ├── matrix-coder.yaml
│       ├── matrix-gemma4-moe.yaml
│       ├── embeddings.yaml
│       ├── qwen-long.yaml
│       ├── experiment.yaml
│       └── comfyui.yaml
├── docs/
│   └── matrix_runtime_modes.md  # Mode definitions (read by manager)
├── .env                         # Shared env vars
└── state/                       # Runtime state (written by manager)
    ├── current_mode             # Current active mode name
    ├── last_mode                # Previous mode for rollback
    └── switch_log.md            # Append-only switch history
```

## State File Protocol

The manager maintains a `state/` directory under `/home/chuck/homelab/`:

| File | Content | Purpose |
|---|---|---|
| `state/current_mode` | Mode name (e.g. `daily`) | What mode is active now |
| `state/last_mode` | Mode name (e.g. `qwen-coder`) | For `rollback` |
| `state/switch_log.md` | Append-only log | Every switch: timestamp, from, to, result |
| `state/active_profiles` | YAML list of running profiles | Current running state |

On first run, the manager initializes these from the live system state.

## Mode Switch Flow

```
model-manager mode switch <MODE>
  │
  ├── 1. Read target mode definition from docs/matrix_runtime_modes.md
  │     (or derive from profile compatible_modes)
  │
  ├── 2. Run preflight checks (Phase 5):
  │     ├── GPU available?
  │     ├── Sufficient free VRAM?
  │     ├── Required model files present?
  │     ├── Target ports free?
  │     ├── Compose files valid?
  │     └── Docker network exists?
  │
  ├── 3. Calculate delta:
  │     ├── Profiles to STOP (not in target mode)
  │     ├── Profiles to START (in target mode, not running)
  │     └── Profiles to RESTART (same profile, different args)
  │
  ├── 4. Display plan to operator:
  │     ├── "Will stop: matrix-coder"
  │     ├── "Will start: qwen-long"
  │     ├── "Will restart: ollama (no change)"
  │     ├── "Aliases affected: gemma4-moe offline"
  │     └── "Expected downtime: ~5-10 min"
  │
  ├── 5. Operator confirmation (interactive):
  │     "Proceed? [y/N] "
  │
  ├── 6. Execute switch (ordered):
  │     ├── Stop containers (vLLM first, then others)
  │     ├── Unload Ollama models if needed
  │     ├── Start/restart containers
  │     └── Wait for health checks
  │
  ├── 7. Health check all active profiles:
  │     ├── vLLM: GET /v1/models
  │     ├── Ollama: GET /
  │     └── ComfyUI: GET /
  │
  ├── 8. Update state files:
  │     ├── Write current_mode
  │     ├── Write last_mode
  │     └── Append to switch_log.md
  │
  └── 9. Report:
        ├── "Switched to <MODE>"
        ├── "Active profiles: ..."
        ├── "Aliases: ..."
        └── "Downtime: X seconds"
```

## Rollback

```
model-manager mode rollback
  │
  ├── 1. Read last_mode from state/last_mode
  ├── 2. Run preflight for last_mode
  ├── 3. Execute mode switch to last_mode
  └── 4. Update state
```

## Safety Rules

1. **No silent switches.** Every mode change requires explicit operator confirmation.
2. **State is always consistent.** The manager writes `last_mode` *before* starting the switch,
   so even a failed switch leaves a valid rollback target.
3. **No LiteLLM changes.** The manager never touches `thor.litellm.config.yml`. Port 8000
   is always `matrix-coder` to clients.
4. **vLLM is the critical path.** vLLM is always stopped *before* starting ComfyUI or a new
   vLLM instance. The manager enforces this ordering.
5. **Metrics never stop.** node-exporter and dcgm-exporter are always running. The manager
   never stops them.
6. **Ollama stays up unless the mode explicitly stops it.** Even in `qwen-coder` and `qwen-long`,
   Ollama keeps running for embeddings; the manager just unloads gemma4.

## Profile Validation

`model-manager profile validate <PROFILE>` checks:

| Check | For vLLM profiles | For Ollama profiles |
|---|---|---|
| Compose file exists | ✅ | ✅ |
| Compose file is valid YAML | ✅ | ✅ |
| Container image available locally | ✅ | ✅ |
| Model files on disk | ✅ | ✅ (ollama list) |
| Port not in use by another profile | ✅ | ✅ |
| Expected VRAM fits available GPU | ✅ | ✅ |
| Compatible modes listed | ✅ | ✅ |
| Health endpoint defined | ✅ | ✅ |
| Env vars from `.env` available | ✅ | ✅ |

Output: `PASS` / `FAIL` with per-check results.

## Experiment Mode Special Handling

`experiment` is not a static mode — it's a slot. The manager supports:

```text
model-manager experiment start <MODEL_PATH> [--gpu-mem 0.70] [--max-len 128000] ...
```

This:
1. Stops the current vLLM container
2. Starts a new vLLM container on port 8000 with the specified model
3. Sets `current_mode=experiment`
4. Logs which model is live
5. `model-manager mode rollback` restores the original Qwen

## Metrics Policy

The manager never stops node-exporter or dcgm-exporter. They are considered
infrastructure, not profiles. They are documented but not managed by the tool.

## Implementation Notes

- Written in bash with `yq` for YAML parsing (or `python3` + `yaml` if needed)
- No external dependencies beyond `docker`, `curl`, `yq`/`python3`
- All state is in `/home/chuck/homelab/state/`
- No daemons, no systemd, no cron — pure CLI tool
- Logs to `state/switch_log.md` (append-only)

## Future (Phase 14+)

Once the manager is proven reliable:
- Auto-detect `daily` mode on boot and restore it
- Integrate with Thor's LiteLLM for graceful "I'm switching" notifications
- Add a `watch` mode that monitors GPU health and alerts
