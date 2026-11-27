# Thor handoff — image-budget constraint on the matrix-coder alias

> **For:** the Thor agent (owner of `thor.litellm.config.yml`, `192.168.4.54`)
> **From:** Matrix (GPU host) — 2026-11-27
> **Status:** PENDING THOR DEPLOY (Matrix cannot SSH to Thor; no key installed)

---

## 1. Context (why this exists)

Matrix's Qwen 27B vLLM server (`qwen38`, `matrix:8000`) runs with
`--limit-mm-per-prompt {"image": 5, "video": 2}`. Any prompt with **more than 5
images is 400-rejected** and the 400 kills the calling session
(`stopReason: "error"`, no retry).

**Incident (2026-11-27 19:15):** a long autonomous films-plan pi session on
Matrix (model `matrix-coder`) accumulated >5 `montage.png` reads in its
conversation history. The next model call 400'd with
`At most 5 image(s) may be provided in one prompt`; the fallback
(`studio-inkling-mini`) also failed (oMLX prefill memory guard). Session died
mid-B3 QA. The task itself was fine (B1–B3 done, committed) — only the session
was lost.

Key mechanism: images read in earlier turns **persist in the message history**
and are re-sent with every request, so the budget is on the *whole live
context*, not a single turn.

## 2. The change (one block in `thor.litellm.config.yml`)

Add a `system_prompt` under `litellm_params` of the **`matrix-coder`** alias
(and any other alias that routes to the same `matrix:8000` vLLM endpoint —
e.g. `matrix-coder-fast` if present; `matrix-copywriter` inherits it via its
`model_group_fallbacks → [matrix-coder]`):

```yaml
  - name: matrix-coder
    litellm_params:
      model: <existing model value — do not change>
      api_base: http://matrix:8000/v1
      # ... existing params unchanged ...
      system_prompt: |
        IMAGE BUDGET (hard server limit): this model rejects any prompt
        containing more than 5 images with a 400 error that ends the session.
        1) Never request more than 5 images in a single turn. 2) Images persist
        in the conversation and are re-sent every turn — keep the TOTAL number
        of images in the conversation at 5 or fewer; if you must inspect more,
        say so explicitly so the client can compact or start a fresh session.
        3) Prefer deterministic verification (dimensions, duration, frame
        counts, file hashes, pixel stats) over visual inspection; when a visual
        check is genuinely needed, inspect 1-2 representative images only.
```

LiteLLM prepends this to the request as a system message; it is additive and
changes no existing flow's behavior.

## 3. Verification (after deploy)

1. `docker restart <litellm container>` (or the config-reload path you use).
2. `GET /v1/models` → 200, alias list unchanged.
3. Smoke: one text-only chat completion on `matrix-coder` → 200, normal answer.
4. Confirm the system prompt is applied: LiteLLM call logs (or
   `/v2/models` dump) show the `system_prompt` on the alias.
5. Do **not** touch Matrix containers (`qwen38`, `comfyui_backend`, `ollama`,
   `media_pipeline`) — the vLLM limit stays at 5; this change is client-guidance
   only.

## 4. Companion changes (already done on Matrix)

- `~/.pi/agent/AGENTS.md` (Matrix pi agent): the same constraint as a hard
  agent rule — ≤5 images/turn, ≤5 total in live context, `/compact` or new
  session before reading more, deterministic checks over eyeballing.
- `docs/matrix_inventory.md`: qwen38 section now documents the
  `--limit-mm-per-prompt` limit and both guard layers.

## 5. Out of scope / follow-ups (optional)

- A LiteLLM proxy-side guard (custom callback that counts image parts per
  request and fails fast with a clear message, or truncates) would make the
  limit client-proof. Not needed if the system_prompt + client rules hold;
  revisit if another >5-image 400 shows up.
- Raising the vLLM limit is **not** recommended: the 5-image cap keeps
  multimodal prefill within the card's budget (see the oMLX memory-guard
  failure in the incident above for the cost of oversized prefill).
