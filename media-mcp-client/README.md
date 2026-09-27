# media-mcp-client — pointer to the `mcp_media` MCP server

> **2026-09-27:** the handoff snapshot that used to live here (`mcp_tools.py`,
> `media_pipeline_client.py`, `HANDOFF.md` — the pre-2026-09-25 baseline) was
> removed. It had drifted behind the deployed tool set, and a second copy is
> exactly what causes that drift. The authoritative `mcp_media`
> implementation is fully checked into git:
>
> **Source of truth: [`mcp/servers/media`](https://github.com/choukalos/homelab-ai-harness/tree/main/mcp/servers/media) in [choukalos/homelab-ai-harness](https://github.com/choukalos/homelab-ai-harness)**
> — `server.py` (22 FastMCP tools), `media_pipeline_client.py` (thin
> stdlib-only HTTP client), e2e tests, and the full contract in that
> directory's README. The deployed thor `mcp_media` container is built from
> that repo.

## What it is

`mcp_media` is a FastMCP server (SSE) on thor (the MCP host) that wraps the
**media-pipeline** service on this GPU host (Matrix, port 8189): it POSTs
jobs, polls, and downloads results. All GPU work (ComfyUI, VLLM,
TTS/music/SFX workers) runs on Matrix; the MCP container only submits and
fetches. It forwards the caller's `user`/`client` identity headers for
metering.

## Contract (read the GitHub README)

Full tool table, endpoints, and history:
[`mcp/servers/media/README.md`](https://github.com/choukalos/homelab-ai-harness/blob/main/mcp/servers/media/README.md).

Key points (2026-09-25 state, verified):

- **22 tools** — 13 job flows (`media_storyboard`, `media_generate_image`
  [Qwen-Image-2.1 default; `model`: `qwen21`|`legacy`], `media_edit_image`
  [+`references`, up to 9], `media_generate_shot`, `media_text_to_speech`
  [voice = library name or reference wav], `media_generate_music`,
  `media_sfx`, `media_upscale_video`, `media_assemble`, `media_trim`,
  `media_freeze`, `media_caption`, `media_add_voice`), 8 sync
  (`media_info`, `media_upload`, `media_download`, `media_put`,
  `media_pull`, `media_fetch`, `media_list_voices`, `media_delete_voice`),
  1 poll (`media_job_result`).
- **Submit-and-poll:** every job tool takes `non_blocking` (default
  `false` = legacy blocking). `true` → `{job_id, status}` in ~1 s, then
  poll `media_job_result(job_id, wait_seconds≤25)` — keeps every tool call
  under the ~30 s pi/LiteLLM tool-call abort.
- **Result shape:** `{path, location}` for media jobs; finished files are
  retained 14 d and can be re-pulled via `media_pull` (signed public URL,
  `https://siri.choukalos.com/media/pipeline/dl/<token>`).
- **TTS voice library:** `trailer` (default), `default`, `narrator_f`,
  `deep_m`; `trailer`/`default` protected from deletion.

## Matrix-side references (this repo)

- Pipeline HTTP API contract: [`docs/matrix_media_pipeline_api.md`](../docs/matrix_media_pipeline_api.md)
- Verification evidence: [`docs/matrix_validation_log.md`](../docs/matrix_validation_log.md)
- Service code: [`media-pipeline/`](../media-pipeline/)