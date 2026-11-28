# Thor handoff — agent-job key pass-through (caller key → Matrix pi)

> **For:** the Thor agent (owner of the LiteLLM proxy + client-facing services,
> `192.168.4.54`)
> **From:** Matrix (GPU host) — 2026-11-27
> **Status:** **COMPLETE (2026-11-28)** — Matrix step 1 + step 2 cutover done;
> Thor T1–T4 done (T4 verified end-to-end 2026-11-28, checks 1–4 PASS).
> **Contract of record:** `docs/matrix_key_passthrough.md` (Matrix repo — pull
> current main). Cross-ref: `docs/matrix_thor_contract.md` v1.7 (§10 "T1
> transport LIVE" note).

---

## 1. Context (why this exists)

Client work that runs as **agent jobs on Matrix** (pi sessions, incl.
pi-long-task worker subagents) consumes LLM capacity through the Thor LiteLLM
proxy — the only auth boundary (Matrix endpoints are keyless LAN-only).

Before this change, all Matrix agent work billed to **chuck's key hard-copied in
Matrix's `~/.pi/agent/auth.json`**. The contract now: **the caller passes its own
LiteLLM virtual key with the job; the Matrix session (coordinator + worker
subagents) runs under that key.** Jobs launched without a key fail loudly
(`No API key found`) instead of silently billing chuck's key.

## 2. What's ready on Matrix (do not rebuild)

- **`scripts/run-with-key.sh <litellm-key> <pi args...>`** — the entry point.
  Exports `LITELLM_API_KEY` + `LITELLM_BASE_URL` and `exec`s `pi`. The env is
  inherited by all in-process worker subagents (pi-long-task workers resolve the
  key from `~/.pi/agent/models.json` `$ENV` interpolation — extensions are
  disabled in workers, verified 2026-11-27).
- **Verified mechanics** (contract doc §3): `pi --api-key` does NOT reach
  extension providers — the env var is the only reliable hook. Stored
  `auth.json` credentials beat the env var, so the step-2 scrub is what makes
  pass-through mandatory.
- **Step-2 cutover:** `scripts/enable-key-passthrough.sh` (removes chuck's
  hard copy; idempotent; aborts while a `pi -p` session runs). **DONE
  2026-11-28** — ran after the films long task finished: stored `auth.json`
  credential removed, `models.json` env-interpolated, `~/.pi/agent/litellm.key`
  installed (chmod 600). Keyless launches now fail with `No API key found`
  (no fallback) — T4 check 3.
- **No media-pipeline change:** `matrix:8189` makes no LLM calls (GPU work is
  ledger-metered) — `mcp_media` and the film skills are unaffected.

## 3. Thor-side work

### T1 — Transport (Thor → Matrix job launch) — **DONE 2026-11-28 (recommended option chosen)**

**Chosen transport: the recommended restricted Thor→Matrix SSH keypair** —
dedicated ed25519 key (`~/.ssh/id_ed25519_matrix`, ssh alias `matrix-agent`);
the Matrix `authorized_keys` line carries `from="192.168.4.54"` and is
command-restricted to `/home/chuck/homelab/scripts/run-with-key.sh`
(no-pty/no-forwarding, no shell). Jobs ride ssh **stdin** as a 2-line envelope
(line 1 = caller's LiteLLM virtual key, line 2 = pi args as a JSON array);
launch via Thor `scripts/matrix-agent-launch.sh`. Live wrapper sha256
`684cc3fe…` verified over the transport — matches the committed
`scripts/run-with-key.sh` (commit `861b299`). Recorded in
`docs/matrix_thor_contract.md` §10 (v1.7 "T1 transport LIVE" note).

(Original options, for the record — there was no Thor→Matrix SSH at the time;
Matrix is reachable at `192.168.4.55`, hostname `matrix`):

- **Recommended:** install a restricted Thor→Matrix SSH key (dedicated user or
  command-restricted `authorized_keys` line limited to
  `/home/chuck/homelab/scripts/run-with-key.sh`), then launch:

  ```bash
  ssh chuck@matrix '/home/chuck/homelab/scripts/run-with-key.sh' \
    <<< "$CALLER_KEY"   # key via stdin — see T2
  ```

  (wrap so the key rides stdin/env, never argv — see T2)
- **Alternative:** reuse an existing Thor→Matrix relay (e.g. the filebridge
  pattern, `docs/thor_manual_tasks.md`) if one is extended to carry a
  job+key envelope.

~~Record the chosen transport in `docs/matrix_thor_contract.md` (new § or §10
note) once live.~~ — **done 2026-11-28** (contract §10 note, v1.7).

### T2 — Key hygiene (hard rules, both sides)

1. **Never in argv:** the key must not appear in a command line (visible in
   `ps` on both hosts, in shell history, in job logs). Pass via stdin or env:
   `ssh ... 'read -r k; exec /home/chuck/homelab/scripts/run-with-key.sh "$k" ...' <<< "$KEY"`.
2. **Never logged/persisted:** no key in MCP tool args, run logs, kanban cards,
   or session transcripts. Redact `sk-…` patterns in any log pipeline that sees
   job payloads.
3. **Ephemeral:** the key lives only in the launched process tree's env on
   Matrix and in the caller's transient job state on Thor.
4. **Caller's key only:** Thor must not substitute its own or chuck's key when
   the caller supplied one. (Fallback to chuck's key is a Matrix-side,
   pre-step-2 behavior only — see §2.)

### T3 — Caller side

- Clients submit agent jobs with a **LiteLLM virtual key** (per-caller
  budget/rate/spend enforced at the proxy — that is the policy point; Matrix
  enforces nothing).
- Validate key shape at intake (`sk-…`, non-empty); reject otherwise. Do not
  store it beyond the job's lifetime.

### T4 — Verification (after Matrix step 2 runs) — **DONE 2026-11-28 (checks 1–4 PASS)**

Final run end-to-end over the fixed restricted SSH transport:
`docs/matrix_validation_log.md` (Run 2026-11-28 final — trivial job on a fresh
small-budget test key, spend attributed to the test key with per-request IP
attribution, keyless launch rejected, revoked key 401 in 1.12 s end-to-end)
+ Thor `docs/thor_validation_log.md` (part 18).

1. Launch a trivial job (`run-with-key.sh <test-key> -p "Reply OK"`) with a
   **test virtual key** that has a small budget.
2. Confirm success + that spend lands on the test key in LiteLLM
   (key usage/budget view), not chuck's.
3. Confirm a **keyless** launch fails with `No API key found` (no fallback).
4. Confirm a **revoked/over-budget** key fails fast with the proxy's 401/403.
5. Record evidence in `docs/matrix_validation_log.md` (Matrix repo) + the Thor
   validation log.

## 4. Timing

- T1–T3 can proceed now (transport + hygiene are independent of the Matrix
  cutover).
- T4 requires Matrix step 2 to have run (otherwise keyless launches still fall
  back to chuck's key and check 3 will "pass" for the wrong reason).
  **Satisfied: step 2 ran 2026-11-28; T4 verified the same day (checks 1–4
  PASS).**
- ~~Matrix will announce step-2 completion in the films-plan closeout~~ — the
  films plan closed 2026-11-27; the cutover ran 2026-11-28 (see §2).

## 5. Out of scope

- Per-request or mid-session key switching (contract is per launched session).
- Any auth on Matrix endpoints (stays keyless LAN-only; LiteLLM proxy remains
  the sole boundary).
- Raising/relaxing the Qwen 27B 5-image limit (`docs/thor_litellm_image_budget_handoff.md`).
