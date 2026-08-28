"""mcp_tools — MCP tool definitions that wrap the media-pipeline service.

Drop this into the REMOTE machine's media-mcp server. It exposes one MCP tool
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
                   sfx_volume: float = 0.9) -> str:
    """Concat video shots and mix VO + music + SFX into a final mp4. `shots` is a
    list of video paths (use B-upscaled shots for 1080p quality). Returns the
    final mp4 path."""
    return _localize(pipe.assemble(shots, vo or None, music or None, sfx or None,
                                   width, height, fps, vo_volume, music_volume,
                                   sfx_volume), "final")


if __name__ == "__main__":
    mcp.run()