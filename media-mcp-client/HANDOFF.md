# media-mcp → media-pipeline handoff

> **2026-09-07 update (matrix Part 1):** 8 new pipeline endpoints + 9 new MCP tools.
> Job flows: `trim`, `freeze`, `caption` (CPU ffmpeg). Sync: `info`, `upload_local`,
> `download`, `upload`, `dl_token`, `dl/{token}`. `/assemble` now accepts object shots
> (`{path,in,out,duration}`), a timestamped SFX list (`[{path,at}]`), `vo_start`, and
> `loudnorm`. The pipeline on matrix :8189 is **unauthenticated by design** (LAN trust;
> public auth is the Caddy layer on thor) — off-LAN pulls use signed `dl_token` URLs.
> Canonical contract: `docs/matrix_media_pipeline_api.md` on the GPU host.

**What this is.** A single-file handoff for wiring a **remote media-mcp server** to the
**GPU-host media-pipeline service** (port 8189). The GPU host already runs the pipeline
(ComfyUI + VLLM + TTS/music/SFX workers, containerized). The remote box is a **thin HTTP
client** — it does no GPU work; it just POSTs jobs, polls, and returns results.

**Architecture**
```
[ remote media-mcp server ]  --HTTP-->  [ GPU host :8189 media-pipeline ]
   (this file's 2 code files)              (already deployed; you do NOT need its code)
```

**You (the integrating AI) need to do 3 things:**
1. Copy `media_pipeline_client.py` (below, §2) into the media-mcp server's directory.
2. Set `MEDIA_PIPELINE_URL` to the GPU host (e.g. `http://<gpu-host>:8189`).
3. Register the 17 MCP tools from `mcp_tools.py` (below, §3) with your MCP server.

Everything else (job queue, GPU locking, VRAM budgeting, model loading) is handled on the
GPU host. The client is **stdlib-only (zero deps)** — it drops into any Python 3.10+ box.

---

## Table of contents
- [§1. Setup on the remote machine](#1-setup-on-the-remote-machine)
- [§2. `media_pipeline_client.py` (copy this file)](#2-media_pipeline_clientpy-copy-this-file)
- [§3. `mcp_tools.py` (the 17 MCP tools)](#3-mcp_toolspy-the-17-mcp-tools)
- [§4. Pipeline HTTP API contract](#4-pipeline-http-api-contract)
- [§5. The 17 MCP tools (quick reference)](#5-the-17-mcp-tools-quick-reference)
- [§6. Config / env vars](#6-config--env-vars)
- [§7. Integration notes](#7-integration-notes)
- [§8. Quality tips (for good-looking output)](#8-quality-tips)
- [§9. Troubleshooting](#9-troubleshooting)

---

## 1. Setup on the remote machine

```bash
# 1. Copy the client into the media-mcp server's directory (see §2 for the code)
cp media_pipeline_client.py /path/to/media-mcp/

# 2. Point it at the GPU host (or put it in a .env next to the client — it auto-loads .env)
export MEDIA_PIPELINE_URL=http://<gpu-host>:8189

# 3. (optional) If the remote box has NO shared filesystem with the GPU host,
#    set this so results are downloaded locally and LOCAL paths are returned:
export MEDIA_LOCAL_DIR=/tmp/media_mcp_out

# 4. Ensure the MCP framework is available (mcp_tools.py uses FastMCP)
pip install "mcp[cli]"        # if not already present

# 5. Sanity check: reach the pipeline
curl -s http://<gpu-host>:8189/health
# -> {"ok":true,"gpu_locked":false,"jobs":0,"max_concurrent":1,"max_queue_depth":5,...}
```

**Integrating into your existing media-mcp server.** `mcp_tools.py` uses **FastMCP**
(`from mcp.server.fastmcp import FastMCP`). If your server uses the same framework, mount
its tools:
```python
from mcp_tools import mcp as media_mcp
# serve media_mcp alongside your other tools
```
If your server uses a **different** MCP framework, copy each `@mcp.tool()` function body
into your framework's decorator — the logic is identical (each tool is a thin wrapper
around a `MediaPipelineClient` method).

---

## 2. `media_pipeline_client.py` (copy this file)

Self-contained, stdlib-only. All high-level methods **BLOCK** until the job finishes and
return the GPU-host path of the result. Use `.fetch()` to download a result locally.

```python
"""media_pipeline_client — thin HTTP client for the GPU-host media-pipeline service.

This is the file the REMOTE machine ships to its media-mcp server. It is
self-contained (Python stdlib only — no third-party deps) so it drops into any
MCP server with zero extra installs.

Configure the pipeline location via the MEDIA_PIPELINE_URL env var
(e.g. http://<gpu-host>:8189). All high-level methods BLOCK until the job
finishes and return the GPU-host path of the result. Use .fetch() to download
a result to the local machine.

Example:
    from media_pipeline_client import MediaPipelineClient
    pipe = MediaPipelineClient()
    shot = pipe.generate_shot("keyframe.jpg", "neon reflections, slow push-in")
    final = pipe.assemble(shots=[shot], vo="vo.wav", music="music.wav")
"""
from __future__ import annotations
import getpass
import json
import mimetypes
import os
import time
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

def _load_dotenv(path: str | None = None) -> None:
    """Load KEY=VALUE pairs from a .env into os.environ (no override of existing).
    Lets the remote box keep MEDIA_PIPELINE_URL in a .env instead of exporting it."""
    p = Path(path or os.environ.get("MEDIA_ENV_FILE", ".env"))
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


# If MEDIA_PIPELINE_URL isn't already in the environment, try a .env file.
if "MEDIA_PIPELINE_URL" not in os.environ:
    _load_dotenv()
DEFAULT_URL = os.environ.get("MEDIA_PIPELINE_URL", "http://127.0.0.1:8189")
# /files/{name} is relative to the pipeline's job dir (JOB_DIR on the GPU host).
_JOB_PREFIX = "/home/chuck/data/comfyui/run/media_jobs/"


def _default_user() -> str:
    for var in ("USER", "LOGNAME"):
        v = os.environ.get(var)
        if v:
            return v
    try:
        return getpass.getuser()
    except Exception:
        return "unknown"


class PipelineError(RuntimeError):
    pass


class MediaPipelineClient:
    def __init__(self, base_url: str | None = None, poll: float = 5.0,
                 user: str | None = None, client: str | None = None):
        self.base = (base_url or DEFAULT_URL).rstrip("/")
        self.poll = poll
        # Identity for metering attribution on the GPU host (spec:
        # docs/matrix_media_pipeline_api.md §5). Precedence: explicit arg > MEDIA_USER /
        # MEDIA_CLIENT env > OS username. Forwarded as `user`/`client` fields
        # on every job POST; the pipeline records them in /metrics + jobs.jsonl.
        self.user = user or os.environ.get("MEDIA_USER") or _default_user()
        self.client = client or os.environ.get("MEDIA_CLIENT") or "mcp"

    def _identity(self) -> dict:
        return {"user": self.user, "client": self.client}

    # ------------------------------------------------------------ low level
    def _post_json(self, endpoint: str, payload: dict) -> str:
        payload = dict(payload)
        for k, v in self._identity().items():
            payload.setdefault(k, v)
        req = urllib.request.Request(
            f"{self.base}{endpoint}", data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read())["job_id"]

    def _post_multipart(self, endpoint: str, filepath: str, fields: dict) -> str:
        boundary = "----mpb" + uuid.uuid4().hex
        fname = os.path.basename(filepath)
        ctype = mimetypes.guess_type(fname)[0] or "application/octet-stream"
        body = b""
        for k, v in {**fields, **self._identity()}.items():
            body += (f"--{boundary}\r\nContent-Disposition: form-data; "
                     f"name=\"{k}\"\r\n\r\n{v}\r\n").encode()
        with open(filepath, "rb") as f:
            fdata = f.read()
        body += (f"--{boundary}\r\nContent-Disposition: form-data; "
                 f"name=\"file\"; filename=\"{fname}\"\r\n"
                 f"Content-Type: {ctype}\r\n\r\n").encode()
        body += fdata + f"\r\n--{boundary}--\r\n".encode()
        req = urllib.request.Request(
            f"{self.base}{endpoint}", data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST")
        with urllib.request.urlopen(req, timeout=300) as r:
            return json.loads(r.read())["job_id"]

    def _wait(self, jid: str, timeout: float) -> dict:
        deadline = time.time() + timeout
        while time.time() < deadline:
            with urllib.request.urlopen(f"{self.base}/jobs/{jid}", timeout=30) as r:
                j = json.loads(r.read())
            if j.get("status") == "done":
                return j.get("output", {})
            if j.get("status") in ("error", "timeout"):
                raise PipelineError(f"job {jid} {j.get('status')}: {j.get('error')}")
            time.sleep(self.poll)
        raise PipelineError(f"job {jid} timed out after {timeout:.0f}s")

    def _post_json_sync(self, endpoint: str, payload: dict) -> dict:
        """POST JSON and return the FULL response (sync endpoints: /info,
        /upload_local, /download, /upload, /dl_token — no job_id)."""
        req = urllib.request.Request(
            f"{self.base}{endpoint}", data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=600) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            raise PipelineError(f"{endpoint} -> HTTP {e.code}: {e.read()[:300]!r}")

    def _get(self, path: str, timeout: float = 600) -> tuple[int, bytes, dict]:
        req = urllib.request.Request(f"{self.base}{path}")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, r.read(), dict(r.headers)
        except urllib.error.HTTPError as e:
            return e.code, e.read(), dict(e.headers)

    # ------------------------------------------------------------- utilities
    def health(self) -> dict:
        with urllib.request.urlopen(f"{self.base}/health", timeout=15) as r:
            return json.loads(r.read())

    def fetch(self, host_path: str, local_dir: str = ".") -> str:
        """Download a result file from the GPU host to the local machine."""
        rel = host_path[len(_JOB_PREFIX):] if host_path.startswith(_JOB_PREFIX) \
            else os.path.basename(host_path)
        name = urllib.parse.quote(rel)
        with urllib.request.urlopen(f"{self.base}/files/{name}", timeout=600) as r:
            data = r.read()
        os.makedirs(local_dir, exist_ok=True)
        out = Path(local_dir) / os.path.basename(host_path)
        out.write_bytes(data)
        return str(out)

    # ----------------------------------------------------------- high level
    def storyboard(self, brief: str, n_shots: int = 5, aspect: str = "16:9",
                   timeout: float = 300) -> dict:
        """LLM shot list -> {"shots": [{"id","visual","vo"}]}."""
        out = self._wait(self._post_json("/storyboard",
                                         {"brief": brief, "n_shots": n_shots,
                                          "aspect": aspect}), timeout)
        local = self.fetch(out["storyboard"], "/tmp")
        return json.loads(Path(local).read_text())

    def generate_image(self, prompt: str, width: int = 1344, height: int = 768,
                       seed: int = 42, steps: int = 4, timeout: float = 600) -> str:
        """Text -> image (keyframe). Returns GPU-host path of the PNG."""
        return self._wait(self._post_json("/images",
                                          {"prompt": prompt, "width": width,
                                           "height": height, "seed": seed,
                                           "steps": steps}), timeout)["image"]

    def edit_image(self, image: str, prompt: str, seed: int = 42, steps: int = 8,
                   timeout: float = 600) -> str:
        """Image+text -> edited image. `image` is a LOCAL path (uploaded)."""
        return self._wait(self._post_multipart("/images/edit", image,
                                               {"prompt": prompt, "seed": str(seed),
                                                "steps": str(steps)}), timeout)["image"]

    def generate_shot(self, keyframe: str, prompt: str, width: int = 768,
                      height: int = 512, frames: int = 97, fps: float = 24.0,
                      seed: int = 42, strength: float = 0.7, timeout: float = 3600) -> str:
        """Keyframe (LOCAL path) + style prompt -> ~4s I2V clip. Returns host path.

        strength: how strongly the keyframe anchors the clip. Lower = less
        warble/morphing (0.7 is the tuned default; 0.6 marginally smoother,
        0.8+ more motion but more warble). Prompt for visual STYLE, not motion.
        """
        return self._wait(self._post_multipart("/shots", keyframe,
                                               {"prompt": prompt, "width": str(width),
                                                "height": str(height),
                                                "frames": str(frames), "fps": str(fps),
                                                "seed": str(seed),
                                                "strength": str(strength)}), timeout)["video"]

    def text_to_speech(self, text: str, voice: str = "trailer",
                       timeout: float = 1800) -> str:
        """Script -> voice-over wav. Returns GPU-host path."""
        return self._wait(self._post_json("/tts", {"text": text, "voice": voice}),
                          timeout)["audio"]

    def generate_music(self, prompt: str, lyrics: str = "", duration: int = 30,
                       seed: int = 42, timeout: float = 3600) -> str:
        """Prompt(+lyrics) -> song/instrumental wav. Returns GPU-host path."""
        return self._wait(self._post_json("/music",
                                          {"prompt": prompt, "lyrics": lyrics,
                                           "duration": duration, "seed": seed}),
                          timeout)["audio"]

    def sfx(self, video: str, description: str = "", duration: float = 8.0,
            steps: int = 25, cfg: float = 4.5, seed: int = 42,
            timeout: float = 3600) -> str:
        """Video (LOCAL path) -> synced SFX bed. Returns GPU-host path."""
        return self._wait(self._post_multipart("/sfx", video,
                                               {"duration": str(duration),
                                                "steps": str(steps), "cfg": str(cfg),
                                                "seed": str(seed), "prompt": description,
                                                "negative_prompt": "", "fps": "24"}),
                          timeout)["audio"]

    def upscale(self, video: str, pipeline: str = "b", resolution: int = 1080,
                noise_scale: float = 0.0, fps: int = 24, seed: int = 42,
                timeout: float = 7200) -> str:
        """Video (LOCAL path) -> upscaled. pipeline 'b'=SeedVR2 | 'a2'=fast."""
        return self._wait(self._post_multipart("/upscale", video,
                                               {"pipeline": pipeline,
                                                "resolution": str(resolution),
                                                "noise_scale": str(noise_scale),
                                                "fps": str(fps), "seed": str(seed)}),
                          timeout)["video"]

    def assemble(self, shots: list, vo: str | None = None, music: str | None = None,
                 sfx: str | None = None, width: int = 1920, height: int = 1080,
                 fps: int = 24, vo_volume: float = 1.0, music_volume: float = 0.35,
                 sfx_volume: float = 0.9, upscale_each: bool = False,
                 upscale_resolution: int = 1080, upscale_noise_scale: float = 0.0,
                 upscale_fps: int = 24, upscale_seed: int = 42,
                 text_overlays: list | None = None, vo_start: float = 0.0,
                 loudnorm: bool = False, timeout: float = 1800) -> str:
        """Concat shots + mix audio -> final mp4. `shots` are GPU-host paths
        (or objects {path, in, out, duration} for trims/stills). `sfx` may be a
        single path or a list of {path, at} for timestamped SFX. `vo_start`
        delays the VO (silence before it). `loudnorm` applies EBU R128 to the
        mix. All new params are backward-compatible (defaults = old behavior).
        """
        payload = {"shots": shots, "width": width, "height": height, "fps": fps,
                   "vo_volume": vo_volume, "music_volume": music_volume,
                   "sfx_volume": sfx_volume, "vo_start": vo_start,
                   "loudnorm": loudnorm}
        if upscale_each:
            payload.update({"upscale_each": True, "upscale_resolution": upscale_resolution,
                            "upscale_noise_scale": upscale_noise_scale,
                            "upscale_fps": upscale_fps, "upscale_seed": upscale_seed})
        if text_overlays:
            payload["text_overlays"] = text_overlays
        for k, v in (("vo", vo), ("music", music), ("sfx", sfx)):
            if v:
                payload[k] = v
        return self._wait(self._post_json("/assemble", payload), timeout)["video"]

    # ------------------------------------------------- ffmpeg post tools (2026-09-07)
    def trim(self, source: str, start: float = 0.0, end: float | None = None,
             duration: float | None = None, fps: int | None = None,
             width: int | None = None, height: int | None = None,
             timeout: float = 1800) -> str:
        """Cut a clip to a time range. `end` (absolute seconds) or `duration`
        (length) — not both. `source` is a GPU-host path. Returns the trimmed
        clip's host path."""
        payload = {"source": source, "start": start}
        if end is not None:
            payload["end"] = end
        elif duration is not None:
            payload["duration"] = duration
        for k, v in (("fps", fps), ("width", width), ("height", height)):
            if v is not None:
                payload[k] = v
        return self._wait(self._post_json("/trim", payload), timeout)["video"]

    def freeze(self, source: str, duration: float = 2.0, frame: float | None = None,
               fps: int = 24, width: int | None = None, height: int | None = None,
               timeout: float = 1800) -> str:
        """Still image (or a video + `frame` = frame index) -> static N-second
        clip. `source` is a GPU-host path. Returns the frozen clip's host path."""
        payload = {"source": source, "duration": duration, "fps": fps}
        if frame is not None:
            payload["frame"] = frame
        for k, v in (("width", width), ("height", height)):
            if v is not None:
                payload[k] = v
        return self._wait(self._post_json("/freeze", payload), timeout)["video"]

    def caption(self, source: str, text: str, start: float | None = None,
                end: float | None = None, position: str = "bottom", size: int | None = None,
                color: str = "white", timeout: float = 1800) -> str:
        """Burn text into a clip (ffmpeg drawtext; multiline supported).
        `source` is a GPU-host path. Returns the captioned clip's host path."""
        payload = {"source": source, "text": text, "position": position, "color": color}
        for k, v in (("start", start), ("end", end), ("size", size)):
            if v is not None:
                payload[k] = v
        return self._wait(self._post_json("/caption", payload), timeout)["video"]

    # ------------------------------------------------- sync endpoints (2026-09-07)
    def info(self, path: str) -> dict:
        """ffprobe metadata (duration_s, width, height, fps, codecs, size,
        bitrate) for any media file. Raises PipelineError on 404/400."""
        code, body, _ = self._get(f"/info?path={urllib.parse.quote(path)}", timeout=120)
        if code != 200:
            raise PipelineError(f"info -> HTTP {code}: {body[:200]!r}")
        return json.loads(body)

    def upload_local(self, source: str, subdirectory: str = "") -> str:
        """Bridge a ComfyUI basedir/ host file into media_jobs/uploads/ (sync).
        Only meaningful when this client runs ON the GPU host. Returns the
        media_jobs path."""
        return self._post_json_sync("/upload_local",
                                    {"source": source, "subdirectory": subdirectory})["path"]

    def download_url(self, url: str, subdirectory: str = "") -> str:
        """Ingest an http(s) URL into media_jobs/uploads/ (sync). Returns the
        media_jobs path."""
        return self._post_json_sync("/download",
                                    {"url": url, "subdirectory": subdirectory})["path"]

    def upload_file(self, local_path: str, subdirectory: str = "") -> str:
        """Upload a LOCAL file into media_jobs/uploads/ (sync multipart).
        Returns the media_jobs path. Cap: MEDIA_UPLOAD_MAX_MB (default 500)."""
        boundary = "----mpc" + uuid.uuid4().hex
        fname = os.path.basename(local_path)
        ctype = mimetypes.guess_type(fname)[0] or "application/octet-stream"
        with open(local_path, "rb") as f:
            fdata = f.read()
        body = (f"--{boundary}\r\nContent-Disposition: form-data; "
                f"name=\"subdirectory\"\r\n\r\n{subdirectory}\r\n"
                f"--{boundary}\r\nContent-Disposition: form-data; "
                f"name=\"file\"; filename=\"{fname}\"\r\n"
                f"Content-Type: {ctype}\r\n\r\n").encode() + fdata + f"\r\n--{boundary}--\r\n".encode()
        req = urllib.request.Request(
            f"{self.base}/upload", data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST")
        try:
            with urllib.request.urlopen(req, timeout=900) as r:
                return json.loads(r.read())["path"]
        except urllib.error.HTTPError as e:
            raise PipelineError(f"/upload -> HTTP {e.code}: {e.read()[:300]!r}")

    def dl_token(self, path: str, ttl_hours: float = 24.0) -> dict:
        """Mint a signed pull URL for a media_jobs file: {token, url_path,
        expires_at}. The token is path-bound + time-limited (HMAC-SHA256).
        Share `self.base + url_path` as a no-auth download link (off-LAN
        clients)."""
        return self._post_json_sync("/dl_token", {"path": path, "ttl_hours": ttl_hours})

    def fetch_dl(self, token: str, local_dir: str = ".") -> str:
        """Download via a signed token (GET /dl/{token}). Saves to local_dir,
        returns the local path. 404 on bad/expired token."""
        code, body, hdrs = self._get(f"/dl/{token}", timeout=900)
        if code != 200:
            raise PipelineError(f"/dl -> HTTP {code}: {body[:200]!r}")
        cd = hdrs.get("Content-Disposition", "")
        name = cd.split("filename=")[-1].strip('"') if "filename=" in cd else "download"
        os.makedirs(local_dir, exist_ok=True)
        out = Path(local_dir) / name
        out.write_bytes(body)
        return str(out)


# Convenience singleton (reads MEDIA_PIPELINE_URL from env)
pipe = MediaPipelineClient()
```

## 3. `mcp_tools.py` (the 17 MCP tools)

One MCP tool per pipeline flow. Each **BLOCKS** until the GPU-host job finishes.

```python
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
                  size: int | None = None, color: str = "white") -> str:
    """Burn text into a clip (drawtext; multiline supported). `source` is a
    pipeline path. Returns the captioned clip path."""
    return _localize(pipe.caption(source, text, start, end, position, size, color),
                     "caption")


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
```

## 4. Pipeline HTTP API contract

Base URL: `http://<gpu-host>:8189`. Job lifecycle: `queued` → `running` → `done`/`error`.

| Method | Path | Inputs | `output` on done |
|---|---|---|---|
| GET | `/health` | — | `{"ok":true,"gpu_locked":bool,"jobs":int,"max_concurrent":int,"max_queue_depth":int,"max_pending":int,"pending":int,"running":[],"queued":[],"queue_depth":int}` |
| GET | `/jobs/{id}` | — | job status: `{"id","flow","status","created","started","finished","output","error","payload","queue_position"?}` |
| GET | `/files/{name:path}` | — | file bytes (download) |
| POST | `/storyboard` | JSON `{brief, n_shots=5, aspect="16:9"}` | `{"storyboard":"<path>/storyboard.json","n_shots":N}` |
| POST | `/images` | JSON `{prompt, width=1280, height=720, seed=42, lora?, steps=4}` | `{"image":"<path>.png"}` |
| POST | `/images/edit` | multipart `file, prompt, seed=42, steps=8` | `{"image":"<path>.png"}` |
| POST | `/shots` | multipart `file(keyframe), prompt, width=768, height=512, frames=97, fps=24, strength=0.7, seed=42` | `{"video":"<path>.mp4"}` |
| POST | `/tts` | JSON `{text, voice="trailer"}` | `{"audio":"<path>/vo.wav"}` |
| POST | `/music` | JSON `{prompt, lyrics="", duration=30, seed=42}` | `{"audio":"<path>/music.wav"}` |
| POST | `/sfx` | multipart `file(video), duration=8, steps=25, cfg=4.5, seed=42, prompt="", negative_prompt="", fps=24` | `{"audio":"<path>.flac"}` |
| POST | `/upscale` | multipart `file(video), pipeline="b"\|"a2", resolution=1080, noise_scale=0.0, fps=24, seed=42` | `{"video":"<path>.mp4"}` |
| POST | `/assemble` | JSON `{shots:[path \| {path,in?,out?,duration?}], vo?, music?, sfx?: path \| [{path,at?}], vo_start?, loudnorm?, width=1920, height=1080, fps=24, vo_volume=1.0, music_volume=0.35, sfx_volume=0.9}` | `{"video":"<path>/final.mp4"}` |
| POST | `/trim` | JSON `{source, start=0.0, end? \| duration?, fps?, width?, height?}` | `{"video":"<path>.mp4"}` |
| POST | `/freeze` | JSON `{source, duration=2.0, frame?=0, fps=24, width=1280, height=720}` | `{"video":"<path>.mp4"}` |
| POST | `/caption` | JSON `{source, text, start?, end?, position="bottom", size?, color="white"}` | `{"video":"<path>.mp4"}` |
| GET | `/info` | query `path` (sync) | `{duration_s, width, height, fps, video_codec, audio_codec, size_bytes}` |
| POST | `/upload_local` | JSON `{source}` (sync; source MUST be under the ComfyUI basedir) | `{"path":"<media_jobs path>"}` |
| POST | `/download` | JSON `{url, subdirectory?}` (sync; http/https ingest) | `{"path":"<media_jobs path>"}` |
| POST | `/upload` | multipart `file, subdirectory?` (sync; 500 MB cap → 413) | `{"path":"<media_jobs path>"}` |
| POST | `/dl_token` | JSON `{path, ttl_hours?=24}` (sync; max 168) | `{token, url_path, expires_at}` |
| GET | `/dl/{token}` | — (sync; Range-capable; 404 on bad/expired) | file bytes |

**Notes**
- `shots`, `upscale`, `sfx`, `images/edit` accept **multipart file uploads** (the server
  copies them into the job dir). `assemble`/`storyboard`/`images`/`tts`/`music` accept
  **JSON** with host paths (or paths relative to the run dir).
- `pipeline` for `/upscale`: `b` = SeedVR2 3B (quality, ~5 min), `a2` = 4xUltrasharp (fast, ~1 min).
- `voice` for `/tts`: `trailer` (bundled movie-trailer reference, zero-shot clone) or a path
  to a custom reference wav.
- Output paths are **host paths** on the GPU host. The remote MCP server fetches them via
  `GET /files/{name}` (name = path relative to the run dir) or directly if it has filesystem access.
- **Job queue (bounded):** at most `MAX_CONCURRENT_JOBS` (default 1) run at once; up to
  `MAX_QUEUE_DEPTH` (default 5) wait. Total in-flight = 6. When full, a new job is rejected
  with **HTTP 503** + a `retry_after_seconds` field — back off and retry instead of hammering.

---

## 5. The 17 MCP tools (quick reference)

| MCP tool | Pipeline endpoint | Params (MCP) | Returns |
|---|---|---|---|
| `media_storyboard` | `/storyboard` | `brief:str, n_shots:int=5, aspect:str="16:9"` | `{"shots":[{id,visual,vo}]}` |
| `media_generate_image` | `/images` | `prompt:str, width=1280, height=720, seed=42, steps=4` | image path |
| `media_edit_image` | `/images/edit` | `image:path, prompt:str, seed=42, steps=8` | image path |
| `media_generate_shot` | `/shots` | `keyframe:path, prompt:str, width=768, height=512, frames=97, fps=24, strength=0.7, seed=42` | video path |
| `media_text_to_speech` | `/tts` | `text:str, voice="trailer"` | wav path |
| `media_generate_music` | `/music` | `prompt:str, lyrics="", duration=30, seed=42` | wav path |
| `media_sfx` | `/sfx` | `video:path, description="", duration=8.0` | audio path |
| `media_upscale_video` | `/upscale` | `video:path, pipeline="b"\|"a2", resolution=1080, noise_scale=0.0, seed=42` | video path |
| `media_assemble` | `/assemble` | `shots:[path\|{path,in?,out?,duration?}], vo?, music?, sfx?: path\|[{path,at?}], vo_start?, loudnorm?, width=1920, height=1080, fps=24, vo_volume=1.0, music_volume=0.35, sfx_volume=0.9` | final mp4 path |
| `media_trim` | `/trim` | `source:path, start=0.0, end?\|duration?, fps?, width?, height?` | clip path |
| `media_freeze` | `/freeze` | `source:path, duration=2.0, frame?=0, fps=24, width=1280, height=720` | clip path |
| `media_caption` | `/caption` | `source:path, text:str, start?, end?, position="bottom", size?, color="white"` | clip path |
| `media_info` | `/info` | `path:str` (sync) | `{duration_s, width, height, fps, ...}` |
| `media_upload_local` | `/upload_local` | `source:path` (sync; GPU-host basedir only) | media_jobs path |
| `media_download_url` | `/download` | `url:str, subdirectory?` (sync) | media_jobs path |
| `media_upload_file` | `/upload` | `local_path:str, subdirectory?` (sync multipart, 500 MB) | media_jobs path |
| `media_dl_token` | `/dl_token` | `path:str, ttl_hours=24` (sync; max 168) | `{token, url_path, expires_at}` |
| `media_fetch_dl` | `/dl/{token}` | `token:str, local_dir?` (sync) | local path |

- Inputs that are **local paths** (keyframe for `media_generate_shot`, image for
  `media_edit_image`, video for `media_sfx`/`media_upscale_video`) are **uploaded** to the
  pipeline automatically.
- Outputs are **GPU-host paths** by default. If `MEDIA_LOCAL_DIR` is set, results are
  downloaded there and **local paths** are returned.

**Typical commercial flow (6–8 shots, 1080p):**
1. `media_storyboard(brief, n_shots=6)` → shot list
2. per shot: `media_generate_image(...)` (keyframe) → `media_generate_shot(keyframe, style_prompt)`
3. `media_text_to_speech(vo_script, voice="trailer")`
4. `media_generate_music(prompt, duration=total)`
5. `media_assemble(shots=[...], vo, music, sfx, width=1920, height=1080)` — for 1080p
   quality, B-upscale each shot first (`media_upscale_video(pipeline="b")`) or rely on the
   pipeline's `upscale_each` (see §8).

---

## 6. Config / env vars

| Var | Required | Default | Meaning |
|---|---|---|---|
| `MEDIA_PIPELINE_URL` | yes* | `http://127.0.0.1:8189` | GPU-host pipeline base URL. *Must be set if the remote box is not the GPU host. |
| `MEDIA_LOCAL_DIR` | no | `""` (host paths) | If set, results are downloaded here and **local** paths are returned. Only needed if there's no shared filesystem with the GPU host. |
| `MEDIA_ENV_FILE` | no | `.env` | Path to a `.env` the client auto-loads (for `MEDIA_PIPELINE_URL` etc.). |

The client auto-loads `.env` (from `MEDIA_ENV_FILE` or the CWD) **only** if `MEDIA_PIPELINE_URL`
isn't already in the environment — so an exported env var always wins.

---

## 7. Integration notes

- **Zero deps.** `media_pipeline_client.py` uses only the Python stdlib (`urllib`, `json`,
  `mimetypes`, ...). It works on any Python 3.10+ box with network access to the GPU host.
- **Blocking tools.** Each MCP tool waits for the job (per-flow timeout) so the LLM gets a
  single call → result. Long flows (shot gen, upscale) have generous timeouts (1–2 h).
- **Local vs host paths.** By default tools return **GPU-host paths** (useful when the remote
  box shares a filesystem with the GPU host). Set `MEDIA_LOCAL_DIR` to download results and
  return **local** paths instead.
- **Job queue + back-pressure.** The GPU host runs a bounded FIFO queue (1 running, up to 5
  waiting). If you submit more than 6 jobs, the 7th gets **HTTP 503** with `retry_after_seconds`.
  The client's `_wait`/`_post_*` will raise on that; a well-behaved caller should catch the
  503, sleep `retry_after_seconds`, and retry. (The default client does NOT auto-retry — add
  that in your wrapper if you want fire-and-forget.)
- **Different MCP framework?** Copy each `@mcp.tool()` body into your framework's decorator.
  The logic is identical; each tool is a one-line wrapper around a `MediaPipelineClient` method.

---

## 8. Quality tips

These make the output look good (tuned on the GPU host):

- **Prompt visual STYLE, not fast motion.** LTXV warbles when the prompt describes big
  movement. Describe appearance/lighting/mood + *gentle* motion only.
- **`strength` (I2V):** default **0.7** is the tuned knee — erratic motion drops ~80× vs 1.0
  with minimal sharpness loss. `0.6` = marginally smoother/less sharp; `0.8+` = more motion but
  more warble. Expose it per-shot if you want to tune.
- **1080p quality:** assemble from **B-upscaled (SeedVR2) shots**, never raw 768×512. Either
  call `media_upscale_video(pipeline="b")` per shot, or let the pipeline's `/assemble` do it
  with `upscale_each=true` (the MCP `media_assemble` doesn't currently expose that flag — call
  the pipeline directly or extend the tool).
- **Readable text:** don't bake text into the video (LTXV warps it). Composite crisp titles in
  post via the pipeline's `/assemble` `text_overlays` (list of `{text,start,end,position,size,color}`)
  → ffmpeg `drawtext`.
- **Character consistency:** build a character sheet first (Qwen-Image T2I), then compose each
  keyframe with `media_edit_image` using the sheet as reference; I2V from those keyframes keeps
  identity stable (expect 1–3 retries/shot).

---

## 9. Troubleshooting

| Symptom | Action |
|---|---|
| `Connection refused` / can't reach 8189 | Verify `MEDIA_PIPELINE_URL` is correct + reachable; `curl http://<gpu-host>:8189/health`. The pipeline must be **up** (it's a profile service on the GPU host). |
| `/health` not `ok` | The GPU-host pipeline is down or still starting. Check it's running there. |
| Job `error` with OOM | Transient on the GPU host — retry. (The host manages VRAM; you don't.) |
| Job queued but slow | Another job is ahead. `GET /health` shows `running`/`queued`/`queue_position`. |
| `503 queue full` | The GPU host queue is full. Wait `retry_after_seconds` and retry. |
| Returned path not accessible from the remote box | Set `MEDIA_LOCAL_DIR` so results are downloaded locally (local paths returned). |
| `ModuleNotFoundError: mcp` | `pip install "mcp[cli]"` (only needed for `mcp_tools.py`; the client itself is stdlib-only). |

---

## Files on the GPU host (for reference — NOT needed on the remote box)
- `media-pipeline/` — the service (Docker build context): `server.py`, `workflows.py`,
  `comfy_client.py`, `Dockerfile`, `requirements.txt`. Runs as a container on port 8189.
- `media-mcp-client/` — this handoff's source dir (the 2 code files + `README.md` + this file).
- The pipeline is managed via `model-manager` on the GPU host (`rebuild media-pipeline` after
  code changes). The remote box never touches any of this.