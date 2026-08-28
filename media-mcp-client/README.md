# media-mcp-client — remote-side files for the media-pipeline service

These are the **two files to copy to the other machine** (the one running the
media-mcp server) so it can leverage the GPU-host media-pipeline service:

| File | Purpose |
|---|---|
| `media_pipeline_client.py` | Thin HTTP client (stdlib-only, **zero deps**) for the pipeline API. Blocking calls, job polling, file fetch. |
| `mcp_tools.py` | MCP tool definitions (FastMCP) that wrap the client — one tool per flow. |

## What goes where
- **GPU host** (this box): the `media-pipeline/` service (port 8189). See `../media-pipeline/`.
- **Remote machine**: these two files, dropped into the existing media-mcp server.

## Setup on the remote machine
```bash
# 1. Copy the two files into the media-mcp server's directory
cp media_pipeline_client.py mcp_tools.py /path/to/media-mcp/

# 2. Point it at the GPU host
export MEDIA_PIPELINE_URL=http://<gpu-host>:8189

# 3. (optional) If the remote box has NO shared filesystem with the GPU host,
#    set this so results are downloaded locally and local paths are returned:
export MEDIA_LOCAL_DIR=/tmp/media_mcp_out

# 4. Ensure the MCP framework is available (mcp_tools.py uses FastMCP)
pip install "mcp[cli]"        # if not already present

# 5. Register mcp_tools with your MCP server (see note below)
```

### Integrating into your existing media-mcp server
`mcp_tools.py` uses **FastMCP** (`from mcp.server.fastmcp import FastMCP`). If your
media-mcp server uses the same framework, just import its tools:
```python
from mcp_tools import mcp as media_mcp
# mount/serve media_mcp alongside your other tools
```
If your server uses a different MCP framework, copy the `@mcp.tool()` function
bodies into your framework's decorators — the logic is identical. Each tool is a
thin wrapper around a `media_pipeline_client.MediaPipelineClient` method.

## The tools
| MCP tool | Pipeline flow | Returns |
|---|---|---|
| `media_storyboard` | LLM shot list | `{"shots":[{id,visual,vo}]}` |
| `media_generate_image` | Qwen-Image T2I | image path |
| `media_edit_image` | Qwen-Image-Edit | image path |
| `media_generate_shot` | LTXV I2V | video path |
| `media_text_to_speech` | XTTS-v2 | wav path |
| `media_generate_music` | ACE-Step | wav path |
| `media_sfx` | MMAudio | audio path |
| `media_upscale_video` | SeedVR2 / 4xUltrasharp | video path |
| `media_assemble` | ffmpeg concat+mix | final mp4 path |

- Inputs that are **local paths** (keyframe for `media_generate_shot`, image for
  `media_edit_image`, video for `media_sfx`/`media_upscale_video`) are **uploaded**
  to the pipeline automatically.
- Outputs are **GPU-host paths** by default. If `MEDIA_LOCAL_DIR` is set, results
  are downloaded there and **local paths** are returned.

## Notes
- All media jobs run through a **bounded FIFO queue** on the GPU host: at most `MAX_CONCURRENT_JOBS`
  (default 1, set in the GPU host's `.env`) run at once; the rest wait with `status=queued` (visible
  via `/health` + `queue_position`). GPU flows additionally serialize on a GPU lock.
- For 1080p-quality commercials, run `media_assemble` with `upscale_each=true` (SeedVR2 per shot) and
  `text_overlays` for titles (see the quality plan in the media-pipeline working doc).
- `media_pipeline_client.py` has **no third-party dependencies** (urllib only), so
  it works on any Python 3.10+ box with network access to the GPU host.