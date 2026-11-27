# Matrix key pass-through contract

**Created:** 2026-11-27 · **Status:** STEP 1 ready (wrapper + docs); STEP 2 cutover
scripted, pending run (see §5 — must wait for the active long task to finish)

## 1. Purpose

Matrix agent work (pi sessions, incl. pi-long-task worker subagents) consumes LLM
capacity through the **Thor LiteLLM proxy** (`https://llm.choukalos.com/v1`) — the
only auth boundary in the stack (vLLM/Ollama/media-pipeline on Matrix are LAN-only
with no auth; see `docs/matrix_thor_contract.md` §10).

Before this change, all Matrix agent work billed to **chuck's key hard-copied in
`~/.pi/agent/auth.json`** (and, since 2026-11-27, a literal copy in
`~/.pi/agent/models.json` for pi-long-task workers). Caller-launched jobs had no
way to be attributed to the caller.

**Contract:** a caller that hands work to Matrix passes **its own LiteLLM virtual
key** at launch time. Every LLM request from that session — coordinator and
worker subagents — carries the caller's key. Jobs launched without a key fail
loudly (`No API key found`) instead of silently billing chuck's key.

## 2. Flow

```
Caller (client / thor agent / cron)
   │  job + caller's LiteLLM virtual key
   ▼
scripts/run-with-key.sh <key> <pi args...>
   │  exports LITELLM_API_KEY + LITELLM_BASE_URL, execs pi
   ▼
Coordinator pi session (pi-provider-litellm extension)
   │  key source after step 2: LITELLM_API_KEY env (no stored credential)
   │  spawns pi-long-task WORKERS = in-process SDK sessions (extensions OFF)
   ▼
Workers (litellm-native provider, ~/.pi/agent/models.json)
   │  key source: "apiKey": "$LITELLM_API_KEY" (env interpolation, per request)
   ▼
LiteLLM proxy (thor) — validates key, budget/rate per key, routes to matrix:8000
```

The key never touches disk on Matrix; it exists only in the launched process
tree's environment and travels TLS to the proxy.

## 3. Verified mechanics (2026-11-27, sandbox tests with `PI_CODING_AGENT_DIR`)

| Mechanism | Result |
|---|---|
| `pi --api-key <fake>` with extension provider `litellm`, no stored cred | ❌ `No API key found` (not a 401) — **the `--api-key` flag does NOT reach extension-based providers; do not rely on it** |
| `LITELLM_API_KEY` env, extension provider, no stored cred | ✅ model responded (coordinator path) |
| Stored `auth.json` credential vs env | stored credential **wins** (extension code: `if (stored) {...} else {env chain}`) — so step 2 must remove the stored key for pass-through to take effect |
| Worker isolated session (extensions disabled) with `models.json` `litellm-native` provider | ✅ `WORKER-OK` (2026-11-27) — workers resolve the key **only** from `models.json` + process env; `apiKey: "$LITELLM_API_KEY"` interpolation is the pass-through hook |
| Base URL | extension needs `LITELLM_BASE_URL` env once the stored credential (which carried it) is scrubbed; `run-with-key.sh` exports it |

## 4. Entry point contract

```bash
/home/chuck/homelab/scripts/run-with-key.sh <litellm-key> <pi args...>
# e.g.
run-with-key.sh sk-... -p "Run a long task with commits to ..."
```

- Caller's key must be a **LiteLLM virtual key** (per-caller budget/rate/spend at
  the proxy). The proxy, not Matrix, enforces policy.
- The wrapper exports `LITELLM_API_KEY` + `LITELLM_BASE_URL` and `exec`s `pi` —
  the env is inherited by all in-process worker subagents.
- Interactive use (chuck, at the terminal):
  `LITELLM_API_KEY=$(cat ~/.pi/agent/litellm.key) pi` — key file installed by the
  step-2 cutover (chmod 600).

## 5. Step 2 cutover (remove the hard copy — forces pass-through)

`scripts/enable-key-passthrough.sh` (idempotent, safety-gated):

1. **Gate:** aborts if a `pi -p` session is running — the active films long task
   (2026-11-27) uses the current config: its coordinator reads `auth.json` per
   request, its workers use the `models.json` literal. **Run the cutover only
   after it finishes.**
2. Backs up `models.json` + `auth.json` (`.bak-<stamp>`).
3. `models.json`: `litellm-native.apiKey` literal → `"$LITELLM_API_KEY"`.
4. `auth.json`: drops the `key` field from the `litellm` entry (keeps `type` +
   `env`, so the base URL survives the scrub).
5. Installs `~/.pi/agent/litellm.key` (chuck's key, chmod 600) for interactive use.
6. Verifies: no literal key remains in live config; env-key auth works for both
   the coordinator path (extension) and the worker path (`models.json`).

**Rollback:** restore the two `.bak-<stamp>` files; delete `litellm.key`.

## 6. Thor / media-pipeline contract impact

**No changes to the media-pipeline API contract** (`docs/matrix_media_pipeline_api.md`,
`docs/matrix_thor_contract.md`):

- `media-pipeline` (`matrix:8189`) makes **no LLM calls** — verified 2026-11-27:
  zero litellm/api-key/authorization references in `server.py`/`workflows.py`
  (cost accounting is ledger-based metering via `MEDIA_PRICE_*` rates, not LLM
  spend). GPU work needs no key.
- `mcp_media` (thor) and the `media-film` skill call the pipeline API only;
  their LLM orchestration runs in the local agent session, which already carries
  its own LiteLLM key.
- The key contract lives **only** in the agent-job launch path (§4). The
  `matrix_thor_contract.md` §10 auth table is unchanged: LiteLLM proxy remains
  the sole auth boundary; Matrix endpoints stay keyless.

## 7. Known limits / follow-ups

- Pass-through is per **launched session** (process env). It is not per-request
  or per-tool-call; a session cannot switch keys mid-run.
- If a caller's key lacks budget/quota at the proxy, the session fails fast —
  by design (attribution over convenience).
- `~/.pi/agent/litellm.key` is still a hard copy of chuck's key on disk — that is
  the *interactive* credential, deliberately kept so chuck's own terminal use
  doesn't depend on the caller pipeline. Caller jobs never see it (scripts run
  non-interactive; the key is not in global env).
- Optional hardening: a LiteLLM-side guard (custom callback) that counts image
  parts per request for the matrix-coder alias — see
  `docs/thor_litellm_image_budget_handoff.md` §5.
