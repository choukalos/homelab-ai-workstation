"""mcp_tools — MCP tool definitions that wrap the media-pipeline service.

Drop this into the REMOTE machine's media-mcp server. The pipeline client
forwards caller identity (`user`/`client`) on every job POST for metering
attribution on the GPU host (docs/matrix_media_pipeline_api.md §5): set
`MEDIA_USER` / `MEDIA_CLIENT` env vars (or rely on the OS username) on the
media-mcp server; per-request overrides go through `MediaPipelineClient(user=, client=)`. It exposes one MCP tool
per pipeline flow. Each tool BLOCKS until the GPU-host job finishes and returns
the result (a host path, or inlined content for small assets).

Requires the `mcp` package (FastMCP). If your media-mcp server uses a different
MCP framework, copy the @mcp.tool() bodies into your framework's decorators —
the logic is identical.

Set MEDIA_PIPELINE_URL to the GPU host before starting the server:
    export MEDIA_PIPELINE_URL=http://<gpu-host>:8189
"""
from __future__ import annotations
import base64
import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from media_pipeline_client import MediaPipelineClient

mcp = FastMCP("media")
pipe = MediaPipelineClient()

# If the remote box has no shared filesystem with the GPU host, set this to a
# local dir and the tools will download results there (and return local paths).
LOCAL_FETCH_DIR = os.environ.get("MEDIA_LOCAL_DIR", "")


def _localize(host_path: str, tag: str = "") -> str:
    """Return a path the caller can use. If LOCAL_FETCH_DIR is set, download
    the file locally and return that; otherwise return the GPU-host path."""
    if not LOCAL_FETCH_DIR:
        return host_path
    dest = os.path.join(LOCAL_FETCH_DIR, f"{tag}_{os.path.basename(host_path)}")
    return pipe.fetch(host_path, dest)


@mcp.tool()
def media_storyboard(brief: str, n_shots: int = 5, aspect: str = "16:9") -> dict:
    """Generate a cinematic shot list (JSON) for a commercial from a brief.
    Returns {"shots": [{"id","visual","vo"}]}."""
    return pipe.storyboard(brief, n_shots=n_shots, aspect=aspect)


@mcp.tool()
def media_generate_image(prompt: str, width: int = 1280, height: int = 720,
                         seed: int = 42, steps: int = 4) -> str:
    """Generate an image (keyframe) from a text prompt. Returns a path."""
    return _localize(pipe.generate_image(prompt, width, height, seed, steps), "img")


@mcp.tool()
def media_edit_image(image: str, prompt: str, seed: int = 42, steps: int = 8) -> str:
    """Edit an image (e.g. compose a consistent keyframe). `image` is a local
    path; it is uploaded to the pipeline. Returns a path."""
    return _localize(pipe.edit_image(image, prompt, seed, steps), "img")


@mcp.tool()
def media_generate_shot(keyframe: str, prompt: str, width: int = 768, height: int = 512,
                        frames: int = 97, fps: float = 24.0, seed: int = 42,
                        strength: float = 0.7) -> str:
    """Animate a keyframe into a ~4s video clip (LTXV I2V). `keyframe` is a
    local image path. `prompt` should describe VISUAL STYLE (not fast motion)
    to minimize warble. `strength` = how strongly the keyframe anchors the clip
    (lower = less warble; 0.7 is the tuned default). Returns a video path."""
    return _localize(pipe.generate_shot(keyframe, prompt, width, height, frames,
                                        fps, seed, strength), "shot")


@mcp.tool()
def media_text_to_speech(text: str, voice: str = "trailer") -> str:
    """Generate voice-over speech (movie-trailer voice by default). Returns a wav path."""
    return _localize(pipe.text_to_speech(text, voice), "vo")


@mcp.tool()
def media_generate_music(prompt: str, lyrics: str = "", duration: int = 30,
                         seed: int = 42) -> str:
    """Generate music or a song (ACE-Step). `lyrics` optional. Returns a wav path."""
    return _localize(pipe.generate_music(prompt, lyrics, duration, seed), "music")


@mcp.tool()
def media_sfx(video: str, description: str = "", duration: float = 8.0) -> str:
    """Generate an SFX bed synced to a video clip (MMAudio). `video` is a local
    path. Returns an audio path."""
    return _localize(pipe.sfx(video, description, duration), "sfx")


@mcp.tool()
def media_upscale_video(video: str, pipeline: str = "b", resolution: int = 1080,
                        noise_scale: float = 0.0, seed: int = 42) -> str:
    """Upscale a video to 1080p. pipeline: 'b' = SeedVR2 (quality, ~5min),
    'a2' = 4xUltrasharp (fast, ~1min). Returns a video path."""
    return _localize(pipe.upscale(video, pipeline, resolution, noise_scale, seed), "upscaled")


@mcp.tool()
def media_assemble(shots: list, vo: str = "", music: str = "", sfx: str = "",
                   width: int = 1920, height: int = 1080, fps: int = 24,
                   vo_volume: float = 1.0, music_volume: float = 0.35,
                   sfx_volume: float = 0.9, vo_start: float = 0.0,
                   loudnorm: bool = False) -> str:
    """Concat video shots and mix VO + music + SFX into a final mp4. `shots` is a
    list of video paths (use B-upscaled shots for 1080p quality); items may also
    be objects {"path", "in", "out", "duration"} (still images need duration).
    `sfx` may be a single path or a list of {"path", "at"} for timestamped SFX.
    `vo_start` delays the VO (silence before it). `loudnorm` = EBU R128.
    Returns the final mp4 path."""
    return _localize(pipe.assemble(shots, vo or None, music or None, sfx or None,
                                   width, height, fps, vo_volume, music_volume,
                                   sfx_volume, vo_start=vo_start, loudnorm=loudnorm),
                     "final")


@mcp.tool()
def media_trim(source: str, start: float = 0.0, end: float | None = None,
               duration: float | None = None, fps: int | None = None,
               width: int | None = None, height: int | None = None) -> str:
    """Cut a clip to a time range: `end` (absolute seconds) or `duration`
    (length) — not both. Optional fps/width/height normalization. `source` is a
    pipeline path. Returns the trimmed clip path."""
    return _localize(pipe.trim(source, start, end, duration, fps, width, height), "trim")


@mcp.tool()
def media_freeze(source: str, duration: float = 2.0, frame: int | None = None,
                 fps: int = 24, width: int | None = None, height: int | None = None) -> str:
    """Freeze a still image (or a video frame: `frame` = frame index) into a
    static N-second clip. `source` is a pipeline path. Returns the clip path."""
    return _localize(pipe.freeze(source, duration, frame, fps, width, height), "freeze")


@mcp.tool()
def media_caption(source: str, text: str, start: float | None = None,
                  end: float | None = None, position: str = "bottom",
                  font_size: int | None = None, color: str = "white",
                  outline: int = 3) -> str:
    """Burn text into a clip (drawtext; multiline supported). `source` is a
    pipeline path. Returns the captioned clip path."""
    return _localize(pipe.caption(source, text, start, end, position, font_size,
                                  color, outline), "caption")


@mcp.tool()
def media_info(path: str) -> dict:
    """Probe metadata for any media file (duration_s, width, height, fps,
    codecs, size, bitrate). `path` is a pipeline (GPU-host) path."""
    return pipe.info(path)


@mcp.tool()
def media_upload_local(source: str, subdirectory: str = "") -> str:
    """Bridge a ComfyUI basedir/ file on the GPU host into media_jobs/uploads/
    (only useful when the MCP server runs ON the GPU host). Returns the
    media_jobs path."""
    return pipe.upload_local(source, subdirectory)


@mcp.tool()
def media_download_url(url: str, subdirectory: str = "") -> str:
    """Ingest an http(s) URL into media_jobs/uploads/ on the GPU host. Returns
    the media_jobs path."""
    return pipe.download_url(url, subdirectory)


@mcp.tool()
def media_upload_file(local_path: str, subdirectory: str = "") -> str:
    """Upload a LOCAL file into media_jobs/uploads/ on the GPU host (multipart;
    500 MB cap). Returns the media_jobs path."""
    return pipe.upload_file(local_path, subdirectory)


@mcp.tool()
def media_dl_token(path: str, ttl_hours: float = 24.0) -> dict:
    """Mint a signed pull URL for a media_jobs file: {token, url_path,
    expires_at}. The URL (MEDIA_PIPELINE_URL + url_path) is a no-auth download
    link — path-bound, time-limited (default 24h, max 168h). For off-LAN
    clients."""
    return pipe.dl_token(path, ttl_hours)


@mcp.tool()
def media_fetch_dl(token: str, local_dir: str = "") -> str:
    """Download a file via a signed token (GET /dl/{token}). Saves to
    local_dir (default: current dir) and returns the local path."""
    return pipe.fetch_dl(token, local_dir or ".")


if __name__ == "__main__":
    mcp.run()