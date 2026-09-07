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