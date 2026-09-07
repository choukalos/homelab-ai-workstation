# Media Pipeline Auth — TODO (matrix side)

Created 2026-09-07 from `media_pipeline_gaps.md` (M8 + T3 auth requirements).

> **⚠️ Needs to be updated from the thor side first.** Auth work was done on
> thor (Caddy / LiteLLM layer); before anything on matrix changes, reconcile this
> file against what thor actually implements (Caddyfile routes, LiteLLM key env
> vars, `media.choukalos.com` DNS record).

## What the plan says (`media_pipeline_gaps.md`)

- **M8:** `POST /upload` on matrix `:8189` with "X-Api-Key auth (same pattern as
  existing job endpoints)" — **this pattern does not exist on matrix** (see below).
- **M8:** `POST /dl_token` mints HMAC-SHA256 signed pull URLs
  (`token = HMAC-SHA256(MEDIA_DL_SECRET, "<path>|<expiry_epoch>")`, base64url,
  path-bound, TTL default 24h / max 168h); `GET /dl/<token>` is unauthenticated
  (the token IS the credential), streams with Range support, 404 (not 403) on
  bad/expired tokens; minted tokens logged (path + expiry), never the token.
- **T3:** public route `media.choukalos.com` on thor Caddy — `/upload` requires
  `X-Api-Key` matching `$LITELLM_KEY_CHUCK` or `$LITELLM_KEY_DYLAN` (mirrors the
  existing `@siri` pattern), `request_body 500MB`; `/dl/*` has NO API key;
  `/dl_token` is not Caddy-authenticated in the T3 snippet.

## What actually exists on matrix (verified 2026-09-07)

- **No auth anywhere on `:8189`.** The entire media-pipeline API is
  unauthenticated; the canonical contract (`docs/matrix_media_pipeline_api.md`)
  says: "LAN-only, no auth — never expose publicly". There is no X-Api-Key
  pattern on the job endpoints for M8 to mirror.
- The only credential mechanism in play is the planned HMAC-signed `/dl` token
  (M8) — independent of any API key.
- Public exposure of `:8189` is blocked by the network (LAN-only bind intent);
  the only public surface will be the Caddy route on thor (T3).

## Decision (2026-09-07, client)

Stick with the current auth approach for the media pipeline: **matrix `:8189`
stays unauthenticated (LAN-only trust); public auth is Caddy-layer only (thor,
T3).** The plan's M8 text "X-Api-Key auth (same pattern as existing job
endpoints)" is dropped — updated in the plan.

## TODO

1. **[thor first]** Reconcile this file with the thor-side auth work:
   `caddy/Caddyfile` (repo `choukalos/homelab-ai-harness`), LiteLLM key env
   vars, `media.choukalos.com` DNS record. Update the "what the plan says"
   section above to match what thor actually implements.
2. Decide whether `/dl_token` should be Caddy-authenticated on the public route
   (today's T3 snippet leaves it open — anyone who can reach matrix `:8189`
   directly can mint tokens; acceptable on the LAN, but confirm intent).
3. If matrix-side key verification is ever wanted (beyond Caddy): a new
   shared-secret mechanism (env in `/home/chuck/homelab/.env`) applied to
   `/upload` (and possibly `/dl_token`). Not part of the current plan.