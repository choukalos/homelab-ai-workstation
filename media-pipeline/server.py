"""Media pipeline API — GPU-host orchestrator for the remote media-mcp server.

Flows (each returns a job_id; poll GET /jobs/{id}):
  POST /storyboard   brief -> shot list (VLLM, no GPU)
  POST /images       text -> image (Qwen-Image-2512 GGUF + Lightning LoRA)
  POST /images/edit  image+text -> image (Qwen-Image-Edit-2511)
  POST /shots        keyframe+text -> video clip (LTXV 2B I2V)
  POST /tts          text -> speech (XTTS-v2, trailer voice)
  POST /music        prompt+lyrics -> song (ACE-Step 1.5)
  POST /sfx          video -> synced SFX bed (MMAudio)
  POST /upscale      video -> 1080p (A2=Ultrasharp fast | B=SeedVR2 quality)
  POST /assemble     shots+audio -> final mp4 (ffmpeg, CPU; object shots,
                      timestamped sfx list, vo_start, loudnorm — backward compatible)
  POST /trim         cut a clip to a time range (ffmpeg, CPU)
  POST /freeze       still image or video frame -> static N-second clip (ffmpeg, CPU)
  POST /caption      burn text into a clip (ffmpeg drawtext, CPU)
  GET  /info         ffprobe metadata (sync, not a job)
  POST /upload_local bridge an arbitrary host file into media_jobs (sync, not a job)
  POST /download     ingest a URL into media_jobs (sync, not a job)
  POST /upload       client file upload, multipart (sync, not a job; no auth on
                      matrix — public auth is Caddy-layer only, see auth_todo.md)
  POST /dl_token     mint an HMAC-signed pull URL for a media_jobs file (sync)
  GET  /dl/<token>   signed file download (sync; the token IS the credential)

GPU serialization: a bounded FIFO job queue (MAX_CONCURRENT_JOBS, default 1) +
fixed worker pool. Waiting depth is bounded by MAX_QUEUE_DEPTH (default 5); once
running+waiting reaches MAX_CONCURRENT_JOBS + MAX_QUEUE_DEPTH, new jobs are
rejected with HTTP 503 + a retry_after back-off hint (FIFO, no priority). GPU
flows additionally serialize on a single thread lock.
"""
import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import shutil
import subprocess
import threading
import time
import urllib.parse
import urllib.request
import uuid
from contextlib import contextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse, Response
import uvicorn

import workflows as wf
import comfy_client as cc

RUN_DIR = Path("/home/chuck/data/comfyui/run")
JOB_DIR = RUN_DIR / "media_jobs"
JOB_DIR_CONTAINER = "/comfy/mnt/media_jobs"  # same dir, container path
COMFY_INPUT = Path("/home/chuck/data/comfyui/basedir/input")
COMFY_OUTPUT = Path("/home/chuck/data/comfyui/basedir/output")
VLLM = "http://127.0.0.1:8000/v1/chat/completions"
VLLM_MODEL = "qwen38-27b"
CONTAINER = "comfyui_backend"
WORKERS = "/comfy/mnt/media_workers"
WORKER_PY = {
    "tts_worker.py": "/comfy/mnt/venvs/venv-tts/bin/python",
    "acestep_worker.py": "/comfy/mnt/ACE-Step-1.5/.venv/bin/python",
}
FFMPEG = "ffmpeg"
logger = logging.getLogger("media-pipeline")

app = FastAPI(title="media-pipeline")
GPU_LOCK = threading.Lock()  # flows run in worker threads; thread-safe lock
JOBS: dict[str, dict] = {}


# ------------------------------------------------------------------ config
# The single source of config is the .env file in the homelab dir (gitignored).
# Load it before reading any env-driven settings so the whole service honors it.
def load_dotenv(path: str | None = None) -> None:
    """Load KEY=VALUE pairs from a .env file into os.environ (no override of existing)."""
    p = Path(path or os.environ.get("MEDIA_ENV_FILE", "/home/chuck/homelab/.env"))
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


load_dotenv()  # must run before MAX_CONCURRENT_JOBS is read below

# Metering is imported AFTER load_dotenv() so the MEDIA_* env vars from .env
# are visible to it (it reads config at import time).
import metering  # noqa: E402

# ------------------------------------------------------------------ job queue
# Bounded FIFO queue + fixed worker pool. At most MAX_CONCURRENT_JOBS media jobs
# run at once; the rest wait with status=queued (position visible in /health).
# Waiting depth is bounded by MAX_QUEUE_DEPTH: once running+waiting reaches
# MAX_CONCURRENT_JOBS + MAX_QUEUE_DEPTH, new jobs are rejected with HTTP 503 and
# a retry_after back-off hint (FIFO, no priority).
MAX_CONCURRENT_JOBS = int(os.environ.get("MAX_CONCURRENT_JOBS", "1"))
MAX_QUEUE_DEPTH = int(os.environ.get("MAX_QUEUE_DEPTH", "5"))

# M8 client file transfer / upload cap (read AFTER load_dotenv above)
MEDIA_DL_SECRET = os.environ.get("MEDIA_DL_SECRET", "")
UPLOAD_MAX_MB = int(os.environ.get("MEDIA_UPLOAD_MAX_MB", "500"))
UPLOADS_DIR = JOB_DIR / "uploads"
BASEDIR = Path("/home/chuck/data/comfyui/basedir")  # ComfyUI input/output root
MAX_PENDING = MAX_CONCURRENT_JOBS + MAX_QUEUE_DEPTH  # total in-flight capacity
_qlock = threading.Lock()
_qcond = threading.Condition(_qlock)
_queue: list[tuple] = []   # FIFO of (jid, flow, payload, extra)
_running: list[str] = []   # jids currently executing (start order)
_pending = 0               # reserved slots = jobs running + waiting + being-created


class QueueFull(Exception):
    """Raised when the bounded queue is at capacity."""

    def __init__(self, depth: int):
        self.depth = depth
        super().__init__(f"queue full ({depth} in flight, limit {MAX_PENDING})")


def _retry_after(ahead: int) -> int:
    """Rough back-off hint: ~60s per job ahead, floored 90s, capped 600s."""
    return max(90, min(600, ahead * 60))


def queue_full_detail(e: QueueFull) -> dict:
    ra = _retry_after(e.depth)
    return {
        "error": "queue_full",
        "message": (f"Media job queue is full ({e.depth} in flight; "
                    f"limit {MAX_PENDING}). Try again in ~{ra // 60} min."),
        "retry_after_seconds": ra,
        "queue_depth": e.depth,
        "max_pending": MAX_PENDING,
        "max_queue_depth": MAX_QUEUE_DEPTH,
    }


def reserve_slot() -> None:
    """Atomically reserve an in-flight slot; raise QueueFull if at capacity."""
    global _pending
    with _qlock:
        if _pending >= MAX_PENDING:
            raise QueueFull(_pending)
        _pending += 1


def release_slot() -> None:
    """Release a reserved in-flight slot (called when a job finishes or fails)."""
    global _pending
    with _qlock:
        _pending = max(0, _pending - 1)


@contextmanager
def job_slot():
    """Reserve an in-flight slot for a new job.

    On success the slot stays reserved until the worker releases it when the job
    finishes (done/error). If the job body raises (e.g. upload/submit failed), the
    slot is released immediately. Raises HTTP 503 with a back-off hint if the
    bounded queue is full (no job is created in that case).
    """
    try:
        reserve_slot()
    except QueueFull as e:
        raise HTTPException(503, detail=queue_full_detail(e))
    try:
        yield
    except Exception:
        release_slot()
        raise


def queue_position(jid: str) -> int | None:
    """0-based position in the waiting queue, or None if not queued."""
    with _qlock:
        for i, (j, *_r) in enumerate(_queue):
            if j == jid:
                return i
        return None


def enqueue_job(jid: str, flow: str, payload: dict, extra: dict) -> None:
    """Put a job on the FIFO queue and wake a worker (non-blocking)."""
    with _qcond:
        _queue.append((jid, flow, payload, extra))
        _qcond.notify()


def _worker_loop() -> None:
    """Pull jobs off the queue and run them (blocking). At most one per thread."""
    while True:
        with _qcond:
            while not _queue:
                _qcond.wait()
            jid, flow, payload, extra = _queue.pop(0)
            _running.append(jid)
        try:
            set_status(jid, "running", started=time.time())
            result = FLOW_MAP[flow](payload, jid, **extra)
            set_status(jid, "done", output=result)
        except subprocess.TimeoutExpired as e:
            set_status(jid, "timeout", error=f"timeout: {e}")
        except TimeoutError as e:
            set_status(jid, "timeout", error=f"timeout: {e}")
        except Exception as e:  # noqa: BLE001 - keep the worker alive
            set_status(jid, "error", error=f"{type(e).__name__}: {e}")
        finally:
            metering.record_job(JOBS.get(jid) or {})  # best-effort, never raises
            with _qcond:
                if jid in _running:
                    _running.remove(jid)
            release_slot()  # this job no longer counts against the bounded queue


_WORKER_THREADS = [
    threading.Thread(target=_worker_loop, name=f"jobworker-{i}", daemon=True)
    for i in range(MAX_CONCURRENT_JOBS)
]
for _t in _WORKER_THREADS:
    _t.start()


# ------------------------------------------------------------------ utils
def _mkdir_job(jid: str):
    """Create the job dir inside the container (owned by comfy uid 1024), then open it up.

    The host user (chuck, uid 1000) cannot mkdir under /home/chuck/data/comfyui/run
    (owned by 1024:1024), so we ask the container to create it, then chmod 777 so
    both the host server and the container workers can read/write.
    """
    r = subprocess.run(
        ["docker", "exec", "-u", "comfy", CONTAINER, "bash", "-c",
         f"mkdir -p {JOB_DIR_CONTAINER}/{jid}/input && chmod 777 {JOB_DIR_CONTAINER}/{jid} {JOB_DIR_CONTAINER}/{jid}/input"],
        capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        raise RuntimeError(f"mkdir job dir failed: {r.stderr[-500:]}")


def new_job(flow: str, payload: dict) -> str:
    jid = uuid.uuid4().hex[:12]
    _mkdir_job(jid)
    JOBS[jid] = {"id": jid, "flow": flow, "status": "queued", "created": time.time(),
                 "started": None, "finished": None, "output": {}, "error": None,
                 "user": payload.get("user"), "client": payload.get("client"),
                 "payload": {k: v for k, v in payload.items() if k != "file_bytes"}}
    return jid


def set_status(jid: str, status: str, **kw):
    JOBS[jid]["status"] = status
    JOBS[jid].update(kw)
    if status in ("done", "error", "timeout"):
        JOBS[jid]["finished"] = time.time()


async def save_upload(jid: str, f: UploadFile) -> str:
    data = await f.read()
    dest = JOB_DIR / jid / "input" / f.filename
    dest.write_bytes(data)
    return f.filename


def copy_to_comfy_input(jid: str, filename: str) -> None:
    """Copy an uploaded file into ComfyUI's input dir (1024-owned) via docker cp."""
    src = JOB_DIR / jid / "input" / filename
    r = subprocess.run(["docker", "cp", str(src), f"{CONTAINER}:/basedir/input/{filename}"],
                       capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise RuntimeError(f"copy to comfy input failed: {r.stderr[-500:]}")


def copy_comfy_outputs(jid: str, outputs: dict, prefix_filter: str = "") -> list[str]:
    paths = []
    for node_id, files in outputs.items():
        for kind, names in files.items():
            for name in names:
                if prefix_filter and prefix_filter not in name:
                    continue
                src = COMFY_OUTPUT / name
                if src.exists():
                    dest = JOB_DIR / jid / src.name
                    shutil.copy2(src, dest)
                    paths.append(str(dest))
    return paths


def run_worker(worker: str, args: list[str], timeout: float = 3600) -> str:
    """Run a worker script inside the comfyui container (GPU work)."""
    py = WORKER_PY.get(worker, "/comfy/mnt/venv/bin/python")
    cmd = ["docker", "exec", "-u", "comfy", CONTAINER, py, f"{WORKERS}/{worker}"] + args
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(f"worker failed: {r.stderr[-2000:]}")
    return r.stdout.strip()


# ------------------------------------------------------------------ flows
def flow_storyboard(payload: dict, jid: str):
    brief = payload["brief"]
    n = int(payload.get("n_shots", 5))
    aspect = payload.get("aspect", "16:9")
    sys = (
        'You are a film director. Given a commercial brief, produce a shot list as STRICT JSON: '
        '{"shots":[{"id":1,"visual":"detailed visual description of the shot (subject, action, '
        'camera move, lighting, style)","vo":"voice-over line for this shot (empty string if none)"}]}. '
        f'Exactly {n} shots. Aspect ratio {aspect}. Each visual must be a single continuous '
        'camera shot suitable for image-to-video generation (max ~4s of motion). VO lines must '
        'be short and punchy (movie-trailer style), total VO across shots under 15 words. '
        'No other text outside the JSON.'
    )
    body = json.dumps({
        "model": VLLM_MODEL,
        "messages": [{"role": "system", "content": sys},
                     {"role": "user", "content": f"Brief: {brief}"}],
        "temperature": 0.7, "max_tokens": 16000,
    }).encode()
    req = urllib.request.Request(VLLM, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        resp = json.loads(r.read())
    text = resp["choices"][0]["message"]["content"]
    text = text.strip()
    if text.startswith("```"):
        parts = text.split("```")
        text = parts[1] if len(parts) > 1 else text
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    data = json.loads(text)
    out = JOB_DIR / jid / "storyboard.json"
    out.write_text(json.dumps(data, indent=2))
    # Real token usage from vLLM (metering prices storyboard at the live
    # matrix-coder rate). prompt_tokens_details is null on this vLLM build,
    # so no cached-token split.
    return {"storyboard": str(out), "n_shots": len(data.get("shots", [])),
            "usage": resp.get("usage") or {}}


def flow_images(payload: dict, jid: str):
    prefix = f"mp_{jid}"
    with GPU_LOCK:
        res = cc.comfy_run(wf.qwen_image_t2i(payload["prompt"], int(payload.get("width", 1280)),
                                             int(payload.get("height", 720)), int(payload.get("seed", 42)),
                                             payload.get("lora", "Qwen-Image-2512-Lightning-4steps-V1.0-bf16.safetensors"),
                                             int(payload.get("steps", 4)), prefix=prefix))
    paths = copy_comfy_outputs(jid, res["outputs"], prefix)
    if not paths:
        raise RuntimeError("no image output found")
    return {"image": paths[0]}


def flow_images_edit(payload: dict, jid: str, image_name: str):
    prefix = f"mp_{jid}"
    with GPU_LOCK:
        res = cc.comfy_run(wf.qwen_image_edit(payload["prompt"], image_name, int(payload.get("seed", 42)),
                                              steps=int(payload.get("steps", 8)), prefix=prefix))
    paths = copy_comfy_outputs(jid, res["outputs"], prefix)
    if not paths:
        raise RuntimeError("no image output found")
    return {"image": paths[0]}


def flow_shots(payload: dict, jid: str, keyframe_name: str):
    prefix = f"mp_{jid}"
    with GPU_LOCK:
        res = cc.comfy_run(wf.ltxv_i2v(keyframe_name, payload["prompt"],
                                       int(payload.get("width", 768)), int(payload.get("height", 512)),
                                       int(payload.get("frames", 97)), float(payload.get("fps", 24.0)),
                                       int(payload.get("seed", 42)),
                                       prefix=prefix,
                                       strength=float(payload.get("strength", 0.7))), timeout=7200)
    paths = copy_comfy_outputs(jid, res["outputs"], prefix)
    if not paths:
        raise RuntimeError("no video output found")
    return {"video": paths[0], "frames": int(payload.get("frames", 97)),
            "fps": float(payload.get("fps", 25.0))}


def flow_tts(payload: dict, jid: str):
    out_host = JOB_DIR / jid / "vo.wav"
    out_container = f"{JOB_DIR_CONTAINER}/{jid}/vo.wav"  # worker runs inside container
    run_worker("tts_worker.py", ["--text", payload["text"],
                "--voice", payload.get("voice", "trailer"), "--out", out_container], timeout=1800)
    return {"audio": str(out_host)}


def flow_music(payload: dict, jid: str):
    out_host = JOB_DIR / jid / "music.wav"
    out_container = f"{JOB_DIR_CONTAINER}/{jid}/music.wav"  # worker runs inside container
    with GPU_LOCK:
        run_worker("acestep_worker.py", ["--prompt", payload["prompt"],
                    "--lyrics", payload.get("lyrics", ""),
                    "--duration", str(int(payload.get("duration", 30))),
                    "--seed", str(int(payload.get("seed", 42))), "--out", out_container], timeout=3600)
    return {"audio": str(out_host)}


def flow_sfx(payload: dict, jid: str, video_rel: str):
    prefix = f"mp_{jid}"
    with GPU_LOCK:
        res = cc.comfy_run(wf.mmaudio_sfx(video_rel, float(payload.get("duration", 8.0)),
                                          int(payload.get("steps", 25)), float(payload.get("cfg", 4.5)),
                                          int(payload.get("seed", 42)), payload.get("prompt", ""),
                                          payload.get("negative_prompt", ""),
                                          fps=int(payload.get("fps", 24)), prefix=prefix), timeout=3600)
    paths = copy_comfy_outputs(jid, res["outputs"], prefix)
    if not paths:
        raise RuntimeError("no audio output found")
    return {"audio": paths[0]}


def flow_upscale(payload: dict, jid: str, video_rel: str):
    pipeline = payload.get("pipeline", "b")
    fps = int(payload.get("fps", 24))
    seed = int(payload.get("seed", 42))
    prefix = f"mp_{jid}"
    if pipeline == "b":
        workflow = wf.upscale_seedvr2(video_rel, int(payload.get("resolution", 1080)),
                                      float(payload.get("noise_scale", 0.0)), fps=fps, seed=seed, prefix=prefix)
    elif pipeline == "a2":
        w, h = (1920, 1080) if int(payload.get("resolution", 1080)) >= 1080 else (1280, 720)
        workflow = wf.upscale_ultrasharp(video_rel, w, h, fps=fps, prefix=prefix)
    else:
        raise ValueError(f"unknown pipeline {pipeline}")
    with GPU_LOCK:
        res = cc.comfy_run(workflow, timeout=7200)
    paths = copy_comfy_outputs(jid, res["outputs"], prefix)
    if not paths:
        raise RuntimeError("no video output found")
    return {"video": paths[0]}


def to_container(p):
    """Convert a host path (under RUN_DIR) to the equivalent container path."""
    p = str(p)
    rp = str(RUN_DIR)
    if p.startswith(rp):
        return "/comfy/mnt" + p[len(rp):]
    return p


def to_container_any(p):
    """Host path -> container path for anything the comfyui container sees
    (RUN_DIR -> /comfy/mnt, basedir -> /basedir)."""
    p = str(p)
    if p.startswith(str(RUN_DIR) + "/"):
        return "/comfy/mnt" + p[len(str(RUN_DIR)):]
    if p.startswith(str(BASEDIR) + "/"):
        return "/basedir" + p[len(str(BASEDIR)):]
    return p


def _safe_media_jobs_path(p) -> Path | None:
    """Realpath p; must be inside JOB_DIR. Returns the resolved Path, else None."""
    rp = Path(str(p)).expanduser().resolve()
    base = JOB_DIR.resolve()
    if rp == base or str(rp).startswith(str(base) + "/"):
        return rp
    return None


def _probe_summary(path) -> dict:
    """ffprobe metadata for a host path (ffprobe runs in the comfyui container)."""
    cmd = ["docker", "exec", "-u", "comfy", CONTAINER, "ffprobe", "-v", "quiet",
           "-print_format", "json", "-show_format", "-show_streams",
           to_container_any(path)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {r.stderr[-500:]}")
    d = json.loads(r.stdout or "{}")
    fmt = d.get("format") or {}
    streams = d.get("streams") or []
    vstream = next((s for s in streams if s.get("codec_type") == "video"), None)
    astreams = [s for s in streams if s.get("codec_type") == "audio"]
    fps = None
    if vstream:
        for key in ("avg_frame_rate", "r_frame_rate"):
            num, _, den = (vstream.get(key) or "").partition("/")
            try:
                num, den = float(num), float(den)
                if den > 0:
                    fps = num / den
                    break
            except ValueError:
                continue
    return {
        "duration_s": round(float(fmt.get("duration") or 0), 3),
        "width": vstream.get("width") if vstream else None,
        "height": vstream.get("height") if vstream else None,
        "fps": round(fps, 3) if fps else None,
        "video_codec": vstream.get("codec_name") if vstream else None,
        "audio_codecs": [s.get("codec_name") for s in astreams],
        "size_bytes": int(fmt.get("size") or 0),
        "bitrate_bps": int(fmt.get("bit_rate") or 0),
    }


def _is_video(path) -> bool:
    """True if the media file has real video duration (> 0); images report 0."""
    return _probe_summary(path)["duration_s"] > 0


def _resolve_src(p) -> Path:
    """Resolve a media_jobs-relative or absolute host path; must exist."""
    p = Path(str(p))
    if not p.is_absolute():
        p = RUN_DIR / p
    p = p.resolve()
    if not p.is_file():
        raise FileNotFoundError(f"source not found: {p}")
    return p


def _uploads_dir(subdirectory: str | None = None) -> Path:
    """media_jobs/uploads (or a media_jobs-confined subdirectory), created."""
    d = JOB_DIR / Path(str(subdirectory)) if subdirectory else UPLOADS_DIR
    rp = d.resolve()
    base = JOB_DIR.resolve()
    if not (rp == base or str(rp).startswith(str(base) + "/")):
        raise HTTPException(400, "subdirectory must be inside media_jobs")
    rp.mkdir(parents=True, exist_ok=True)
    return rp


def _ts() -> str:
    return time.strftime("%Y%m%dT%H%M%S", time.gmtime())


def _unique(dest: Path) -> Path:
    """Avoid clobbering a same-second upload with the same name."""
    if not dest.exists():
        return dest
    i = 1
    while True:
        cand = dest.with_name(f"{dest.stem}-{i}{dest.suffix}")
        if not cand.exists():
            return cand
        i += 1


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _b64url_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _mint_token(path: str, expiry_epoch: int) -> str:
    """token = b64url("<path>|<expiry>") + "." + b64url(HMAC-SHA256(secret, "<path>|<expiry>"))

    The HMAC input matches the spec exactly; the payload half is included so the
    stateless /dl endpoint can recover which file the token binds to."""
    payload = f"{path}|{int(expiry_epoch)}"
    sig = hmac.new(MEDIA_DL_SECRET.encode(), payload.encode(), hashlib.sha256).digest()
    return _b64url(payload.encode()) + "." + _b64url(sig)


def _verify_token(token: str) -> Path | None:
    """Return the token's bound path (inside media_jobs) if valid + unexpired, else None."""
    if not MEDIA_DL_SECRET:
        return None
    try:
        payload_b64, sig_b64 = token.split(".", 1)
        payload = _b64url_decode(payload_b64).decode()
        sig = _b64url_decode(sig_b64)
        expect = hmac.new(MEDIA_DL_SECRET.encode(), payload.encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(sig, expect):
            return None
        path_s, expiry_s = payload.rsplit("|", 1)
        if int(expiry_s) < time.time():
            return None
        return _safe_media_jobs_path(path_s)
    except Exception:
        return None


def _shot_spec(s) -> dict:
    """Normalize an assemble shot entry (str or {path, in?, out?, duration?})."""
    if isinstance(s, str):
        return {"path": s, "in": None, "out": None, "duration": None}
    if isinstance(s, dict) and s.get("path"):
        return {"path": s["path"], "in": s.get("in"), "out": s.get("out"),
                "duration": s.get("duration")}
    raise ValueError(f"bad shot entry: {s!r}")


def run_ffmpeg(args, timeout=1800):
    """Run ffmpeg inside the ComfyUI container (ffmpeg is not installed on the host)."""
    cmd = ["docker", "exec", "-u", "comfy", CONTAINER, "ffmpeg"] + [str(a) for a in args]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {r.stderr[-1500:]}")
    return r


def _upscale_shot(shot_host, jid, i, resolution, noise_scale, fps, seed):
    """B-upscale (SeedVR2) a single shot; return the upscaled host path."""
    shot_host = Path(shot_host)
    rel = str(shot_host)
    rp = str(RUN_DIR)
    if rel.startswith(rp + "/"):
        video_rel = rel[len(rp) + 1:]
    elif rel.startswith(rp):
        video_rel = rel[len(rp):].lstrip("/")
    else:
        video_rel = rel  # already run-dir-relative
    prefix = f"mp_{jid}_up{i:02d}"
    with GPU_LOCK:
        res = cc.comfy_run(wf.upscale_seedvr2(video_rel, resolution, noise_scale,
                                              fps=fps, seed=seed, prefix=prefix), timeout=7200)
    paths = copy_comfy_outputs(jid, res["outputs"], prefix)
    if not paths:
        raise RuntimeError(f"upscale_each: no output for shot {i}")
    return paths[0]


def _apply_text_overlays(video_host, overlays, jid):
    """Apply ffmpeg drawtext overlays (titles/tagline) to video_host; return output path."""
    if not overlays:
        return str(video_host)
    filters = []
    for i, ov in enumerate(overlays):
        txt = str(ov.get("text", "")).strip()
        if not txt:
            continue
        tf_host = JOB_DIR / jid / f"ov_{i}.txt"
        tf_host.write_text(txt)
        tf_c = to_container(str(tf_host))
        font = ov.get("font", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
        size = int(ov.get("size", 72))
        color = ov.get("color", "white")
        pos = ov.get("position", "bottom")
        start = float(ov.get("start", 0.0))
        end = float(ov.get("end", 1e9))
        posmap = {
            "top": "x=(w-text_w)/2:y=60",
            "bottom": "x=(w-text_w)/2:y=h-th-80",
            "center": "x=(w-text_w)/2:y=(h-text_h)/2",
            "topleft": "x=40:y=40",
            "topright": "x=w-text_w-40:y=40",
            "bottomleft": "x=40:y=h-th-80",
            "bottomright": "x=w-text_w-40:y=h-th-80",
        }
        xy = posmap.get(pos, posmap["bottom"])
        enable = f"between(t,{start:.2f},{end:.2f})"
        filters.append(f"drawtext=fontfile={font}:textfile={tf_c}:fontsize={size}:"
                       f"fontcolor={color}:{xy}:enable='{enable}'")
    if not filters:
        return str(video_host)
    out_host = JOB_DIR / jid / "final_titled.mp4"
    out_c = to_container(out_host)
    run_ffmpeg(["-y", "-i", to_container(str(video_host)), "-vf", ",".join(filters),
                "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-c:a", "copy", str(out_c)],
               timeout=1800)
    return str(out_host)


def flow_trim(payload: dict, jid: str):
    """M1: cut a clip to a time range (frame-accurate reencode by default)."""
    src = _resolve_src(payload["source"])
    start = float(payload.get("start", 0.0))
    end, dur = payload.get("end"), payload.get("duration")
    if end is not None and dur is not None:
        raise ValueError("provide either end or duration, not both")
    if end is None and dur is None:
        raise ValueError("end or duration required")
    if end is not None:
        dur = float(end) - start
    if dur <= 0:
        raise ValueError("duration must be positive")
    reencode = str(payload.get("reencode", "true")).strip().lower() not in ("0", "false", "no")
    out = JOB_DIR / jid / f"mp_{jid}_00001.mp4"
    cmd = ["-y"]
    if reencode:
        # -ss after -i = output seek: frame-accurate (decodes from 0).
        cmd += ["-i", to_container_any(src), "-ss", f"{start:.3f}", "-t", f"{dur:.3f}",
                "-c:v", "libx264", "-crf", "18", "-preset", "medium", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "192k"]
    else:
        cmd += ["-ss", f"{start:.3f}", "-i", to_container_any(src), "-t", f"{dur:.3f}",
                "-c", "copy"]
    run_ffmpeg(cmd + [to_container(out)], timeout=1800)
    return {"video": str(out)}


def flow_freeze(payload: dict, jid: str):
    """M2: still image or video frame -> pixel-static N-second clip (no generative model)."""
    src = _resolve_src(payload["source"])
    frame = int(payload.get("frame", 0))
    dur = float(payload.get("duration", 2.0))
    w = int(payload.get("width", 1280))
    h = int(payload.get("height", 720))
    fps = int(payload.get("fps", 24))
    out = JOB_DIR / jid / f"mp_{jid}_00001.mp4"
    img = src
    if _is_video(src):
        img = JOB_DIR / jid / "freeze_src.png"
        run_ffmpeg(["-y", "-i", to_container_any(src), "-vf", f"select=eq(n\\,{frame})",
                    "-frames:v", "1", "-update", "1", to_container(img)], timeout=600)
    run_ffmpeg(["-y", "-loop", "1", "-t", f"{dur:.3f}", "-i", to_container_any(img),
                "-vf", (f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
                         f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,fps={fps}"),
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(fps),
                to_container(out)], timeout=1800)
    return {"video": str(out)}


def flow_caption(payload: dict, jid: str):
    """M3: burn text into a clip (textfile-based drawtext, like _apply_text_overlays)."""
    src = _resolve_src(payload["source"])
    text = str(payload.get("text", "")).strip()
    if not text:
        raise ValueError("text required")
    start = float(payload.get("start", 0.0))
    end = payload.get("end")
    if end is None:
        end = _probe_summary(src)["duration_s"] or 0
        if not end:
            end = 1e9
    font = payload.get("font", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
    color = payload.get("color", "white")
    outline = int(payload.get("outline", 3))
    pos = payload.get("position", "bottom")
    fs = payload.get("font_size")
    if fs is None:
        hgt = _probe_summary(src).get("height") or 720
        fs = max(12, int(hgt * 0.05))  # ~5% of height
    else:
        fs = int(fs)
    tf = JOB_DIR / jid / "caption.txt"
    tf.write_text(text)
    posmap = {
        "top": "x=(w-text_w)/2:y=60",
        "bottom": "x=(w-text_w)/2:y=h-th-80",
        "center": "x=(w-text_w)/2:y=(h-text_h)/2",
        "topleft": "x=40:y=40",
        "topright": "x=w-text_w-40:y=40",
        "bottomleft": "x=40:y=h-th-80",
        "bottomright": "x=w-text_w-40:y=h-th-80",
    }
    xy = posmap.get(pos, posmap["bottom"])
    filters = []
    if payload.get("background_bar"):
        filters.append("drawbox=x=0:y=2*ih/3:w=iw:h=ih/3:color=black@0.55:t=fill")
    filters.append(f"drawtext=fontfile={font}:textfile={to_container(tf)}:fontsize={fs}:"
                   f"fontcolor={color}:borderw={outline}:bordercolor=black:{xy}:"
                   f"enable='between(t,{start:.2f},{float(end):.2f})'")
    out = JOB_DIR / jid / f"mp_{jid}_00001.mp4"
    run_ffmpeg(["-y", "-i", to_container_any(src), "-vf", ",".join(filters),
                "-c:v", "libx264", "-crf", "18", "-preset", "medium", "-pix_fmt", "yuv420p",
                "-c:a", "copy", to_container(out)], timeout=1800)
    return {"video": str(out)}


def flow_assemble(payload: dict, jid: str):
    """ffmpeg (in-container): concat video shots, mix audio tracks, output final mp4.

    Backward compatible: string-array shots + single sfx path behave exactly as
    before. New: object shots {path, in?, out?, duration?} (still + duration =
    static clip), sfx as a list of {path, at} for timestamped placement, vo_start
    (VO offset from t=0), loudnorm (EBU R128 on the final mix).
    """
    shots = payload["shots"]
    vo, music, sfx = payload.get("vo"), payload.get("music"), payload.get("sfx")
    width = int(payload.get("width", 1920))
    height = int(payload.get("height", 1080))
    fps = int(payload.get("fps", 24))
    text_overlays = payload.get("text_overlays", []) or []
    out_host = JOB_DIR / jid / "final.mp4"
    out = to_container(out_host)

    # 0. Optionally B-upscale (SeedVR2) each shot for 1080p-quality assembly.
    if payload.get("upscale_each", False):
        res = int(payload.get("upscale_resolution", 1080))
        ns = float(payload.get("upscale_noise_scale", 0.0))
        ufps = int(payload.get("upscale_fps", fps))
        useed = int(payload.get("upscale_seed", 42))
        shots = [_upscale_shot(s["path"] if isinstance(s, dict) else s, jid, i, res, ns,
                               ufps, useed)
                 for i, s in enumerate(shots)]

    def resolve(p):
        p = Path(p)
        return p if p.is_absolute() else RUN_DIR / p

    norm = []
    for i, s in enumerate(shots):
        spec = _shot_spec(s)
        sp_host = resolve(spec["path"])
        if not sp_host.exists():
            raise FileNotFoundError(f"shot not found: {sp_host}")
        sp = to_container_any(sp_host)
        n_host = JOB_DIR / jid / f"norm_{i:02d}.mp4"
        n = to_container(n_host)
        cmd = ["-y"]
        if not _is_video(sp_host):
            # Still image: no duration keeps today's behavior (1-frame ~0s clip);
            # explicit duration renders a pixel-static clip (M2 logic).
            if spec["duration"] is not None:
                cmd += ["-loop", "1", "-t", f"{float(spec['duration']):.3f}"]
            cmd += ["-i", sp]
        else:
            cmd += ["-i", sp]
            if spec["in"] is not None:
                cmd += ["-ss", f"{float(spec['in']):.3f}"]
            trim_dur = None
            if spec["out"] is not None:
                trim_dur = float(spec["out"]) - (float(spec["in"]) if spec["in"] is not None else 0.0)
            elif spec["duration"] is not None:
                trim_dur = float(spec["duration"]) - (float(spec["in"]) if spec["in"] is not None else 0.0)
            if trim_dur is not None:
                if trim_dur <= 0:
                    raise ValueError(f"shot {i}: non-positive trim duration")
                cmd += ["-t", f"{trim_dur:.3f}"]
        cmd += ["-vf",
                f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps}",
                "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-pix_fmt", "yuv420p",
                "-an", n]
        run_ffmpeg(cmd, timeout=1800)
        norm.append(n)
    concat_list_host = JOB_DIR / jid / "concat.txt"
    concat_list = to_container(concat_list_host)
    concat_list_host.write_text("".join(f"file '{n}'\n" for n in norm))
    video_only_host = JOB_DIR / jid / "video_only.mp4"
    video_only = to_container(video_only_host)
    run_ffmpeg(["-y", "-f", "concat", "-safe", "0", "-i", concat_list,
                "-c:v", "libx264", "-preset", "fast", "-crf", "18", video_only])

    vo_start = float(payload.get("vo_start", 0.0) or 0.0)
    loudnorm = str(payload.get("loudnorm", "false")).strip().lower() in ("1", "true", "yes", "on")

    # Audio inputs: (host_path, volume, label, delay_s). Old-style calls (string
    # vo/music/sfx) produce exactly the same graph as before.
    inputs = []
    if vo:
        vp = resolve(vo)
        if not vp.exists():
            raise FileNotFoundError(f"vo not found: {vp}")
        inputs.append((vp, float(payload.get("vo_volume", 1.0)), "vo", vo_start))
    if music:
        mp = resolve(music)
        if not mp.exists():
            raise FileNotFoundError(f"music not found: {mp}")
        inputs.append((mp, float(payload.get("music_volume", 0.35)), "music", 0.0))
    sfx_vol = float(payload.get("sfx_volume", 0.9))
    if sfx:
        if isinstance(sfx, str):
            sfx_entries = [{"path": sfx, "at": 0.0}]
        elif isinstance(sfx, list):
            sfx_entries = []
            for e in sfx:
                if isinstance(e, str):
                    sfx_entries.append({"path": e, "at": 0.0})
                elif isinstance(e, dict) and e.get("path"):
                    sfx_entries.append({"path": e["path"], "at": float(e.get("at", 0.0))})
                else:
                    raise ValueError(f"bad sfx entry: {e!r}")
        else:
            raise ValueError("sfx must be a path or a list of paths/{path, at}")
        for k, e in enumerate(sfx_entries):
            sp = resolve(e["path"])
            if not sp.exists():
                raise FileNotFoundError(f"sfx not found: {sp}")
            inputs.append((sp, sfx_vol, f"sfx{k}", e["at"]))
    if not inputs:
        shutil.copy2(video_only_host, out_host)
        return {"video": _apply_text_overlays(out_host, text_overlays, jid)}

    cmd = ["-y", "-i", video_only]
    filters, labels = [], []
    for i, (p, v, l, delay) in enumerate(inputs):
        cmd += ["-i", to_container_any(p)]
        f = f"[{i + 1}:a]aresample=44100,aformat=channel_layouts=stereo,volume={v}"
        if delay > 0:
            ms = int(delay * 1000)
            f += f",adelay={ms}|{ms}"
        f += f"[a{l}]"
        filters.append(f)
        labels.append(l)
    mix = f"{''.join(f'[a{l}]' for l in labels)}amix=inputs={len(labels)}:duration=longest:dropout_transition=0:normalize=0"
    if loudnorm:
        mix += ",loudnorm=I=-16:TP=-1.5:LRA=11"
    # apad: audio must never be the shortest stream, else -shortest truncates
    # the video to the audio length. Video length is the source of truth.
    mix += ",apad,alimiter=limit=0.95[aout]"
    run_ffmpeg(cmd + ["-filter_complex", ";".join(filters + [mix]),
                      "-map", "0:v", "-map", "[aout]", "-c:v", "copy",
                      "-c:a", "aac", "-b:a", "192k", "-shortest", out])
    return {"video": _apply_text_overlays(out_host, text_overlays, jid)}


# ------------------------------------------------------------------ runner
FLOW_MAP = {
    "storyboard": flow_storyboard,
    "images": flow_images,
    "images_edit": flow_images_edit,
    "shots": flow_shots,
    "tts": flow_tts,
    "music": flow_music,
    "sfx": flow_sfx,
    "upscale": flow_upscale,
    "assemble": flow_assemble,
    "trim": flow_trim,
    "freeze": flow_freeze,
    "caption": flow_caption,
}



# ------------------------------------------------------------------ routes
@app.get("/health")
def health():
    with _qlock:
        running = list(_running)
        queued = [j for (j, *_r) in _queue]
        pending = _pending
    return {
        "ok": True,
        "gpu_locked": GPU_LOCK.locked(),
        "jobs": len(JOBS),
        "max_concurrent": MAX_CONCURRENT_JOBS,
        "max_queue_depth": MAX_QUEUE_DEPTH,
        "max_pending": MAX_PENDING,
        "pending": pending,
        "running": running,
        "queued": queued,
        "queue_depth": len(queued),
    }


@app.get("/metrics")
def metrics():
    """Prometheus text format (contract: docs/matrix_media_pipeline_api.md §5).
    404 when MEDIA_METRICS_ENABLED=false (kill switch = zero behavior change)."""
    if not metering.ENABLED:
        raise HTTPException(404, "metrics disabled")
    with _qlock:
        depth, active = len(_queue), len(_running)
    metering.update_gauges(depth, active)
    return Response(metering.render(), media_type=metering.CONTENT_TYPE_LATEST)


@app.get("/jobs/{jid}")
def job_status(jid: str):
    if jid not in JOBS:
        raise HTTPException(404, "unknown job")
    j = dict(JOBS[jid])
    if j.get("status") == "queued":
        j["queue_position"] = queue_position(jid)
    return j


@app.get("/files/{name:path}")
def get_file(name: str):
    p = (JOB_DIR / name).resolve()
    if not str(p).startswith(str(JOB_DIR.resolve())) or not p.exists():
        raise HTTPException(404, "not found")
    return FileResponse(p)


@app.post("/storyboard")
async def api_storyboard(payload: dict):
    with job_slot():
        jid = new_job("storyboard", payload)
        enqueue_job(jid, "storyboard", payload, {})
    return {"job_id": jid}


@app.post("/images")
async def api_images(payload: dict):
    with job_slot():
        jid = new_job("images", payload)
        enqueue_job(jid, "images", payload, {})
    return {"job_id": jid}


@app.post("/images/edit")
async def api_images_edit(file: UploadFile = File(...), prompt: str = Form(...),
                          seed: int = Form(42), steps: int = Form(8),
                          user: str = Form(None), client: str = Form(None)):
    with job_slot():
        payload = {"prompt": prompt, "seed": seed, "steps": steps,
                   "user": user, "client": client}
        jid = new_job("images_edit", payload)
        await save_upload(jid, file)
        copy_to_comfy_input(jid, file.filename)
        enqueue_job(jid, "images_edit", payload, {"image_name": file.filename})
    return {"job_id": jid}


@app.post("/shots")
async def api_shots(file: UploadFile = File(...), prompt: str = Form(...),
                    width: int = Form(768), height: int = Form(512),
                    frames: int = Form(97), fps: float = Form(25.0),
                    seed: int = Form(42), steps: int = Form(8),
                    strength: float = Form(0.7), user: str = Form(None),
                    client: str = Form(None)):
    # strength: how strongly the keyframe anchors the clip. Lower = less
    # deviation/warble (empirically 0.7 is the knee; 0.6 marginally smoother,
    # 0.8+ adds motion but warble). Prompt for visual STYLE, not fast motion.
    with job_slot():
        payload = {"prompt": prompt, "width": width, "height": height,
                   "frames": frames, "fps": fps, "seed": seed,
                   "steps": steps, "strength": strength,
                   "user": user, "client": client}
        jid = new_job("shots", payload)
        await save_upload(jid, file)
        copy_to_comfy_input(jid, file.filename)
        enqueue_job(jid, "shots", payload, {"keyframe_name": file.filename})
    return {"job_id": jid}


@app.post("/tts")
async def api_tts(payload: dict):
    with job_slot():
        jid = new_job("tts", payload)
        enqueue_job(jid, "tts", payload, {})
    return {"job_id": jid}


@app.post("/music")
async def api_music(payload: dict):
    with job_slot():
        jid = new_job("music", payload)
        enqueue_job(jid, "music", payload, {})
    return {"job_id": jid}


@app.post("/sfx")
async def api_sfx(file: UploadFile = File(...), duration: float = Form(8.0),
                  steps: int = Form(25), cfg: float = Form(4.5), seed: int = Form(42),
                  prompt: str = Form(""), negative_prompt: str = Form(""), fps: int = Form(24),
                  user: str = Form(None), client: str = Form(None)):
    with job_slot():
        payload = {"duration": duration, "steps": steps, "cfg": cfg, "seed": seed,
                   "prompt": prompt, "negative_prompt": negative_prompt, "fps": fps,
                   "user": user, "client": client}
        jid = new_job("sfx", payload)
        await save_upload(jid, file)
        video_rel = f"media_jobs/{jid}/input/{file.filename}"
        enqueue_job(jid, "sfx", payload, {"video_rel": video_rel})
    return {"job_id": jid}


@app.post("/upscale")
async def api_upscale(file: UploadFile = File(...), pipeline: str = Form("b"),
                      resolution: int = Form(1080), noise_scale: float = Form(0.0),
                      fps: int = Form(24), seed: int = Form(42),
                      user: str = Form(None), client: str = Form(None)):
    with job_slot():
        payload = {"pipeline": pipeline, "resolution": resolution,
                   "noise_scale": noise_scale, "fps": fps, "seed": seed,
                   "user": user, "client": client}
        jid = new_job("upscale", payload)
        await save_upload(jid, file)
        video_rel = f"media_jobs/{jid}/input/{file.filename}"
        enqueue_job(jid, "upscale", payload, {"video_rel": video_rel})
    return {"job_id": jid}


@app.post("/assemble")
async def api_assemble(payload: dict):
    with job_slot():
        jid = new_job("assemble", payload)
        enqueue_job(jid, "assemble", payload, {})
    return {"job_id": jid}


# ------------------------------------------------------------------ M1-M3: ffmpeg job flows
@app.post("/trim")
async def api_trim(payload: dict):
    with job_slot():
        jid = new_job("trim", payload)
        enqueue_job(jid, "trim", payload, {})
    return {"job_id": jid}


@app.post("/freeze")
async def api_freeze(payload: dict):
    with job_slot():
        jid = new_job("freeze", payload)
        enqueue_job(jid, "freeze", payload, {})
    return {"job_id": jid}


@app.post("/caption")
async def api_caption(payload: dict):
    with job_slot():
        jid = new_job("caption", payload)
        enqueue_job(jid, "caption", payload, {})
    return {"job_id": jid}


# ------------------------------------------------------------------ M5-M7: sync helpers
@app.get("/info")
def api_info(path: str):
    p = Path(path)
    if not p.is_absolute():
        p = RUN_DIR / p
    p = p.resolve()
    if not p.is_file():
        raise HTTPException(404, "not found")
    try:
        return _probe_summary(p)
    except Exception as e:
        raise HTTPException(400, f"probe failed: {e}")


@app.post("/upload_local")
def api_upload_local(payload: dict):
    src = Path(str(payload.get("source", ""))).expanduser()
    if not src.is_absolute() or not src.is_file():
        raise HTTPException(400, "source must be an existing absolute path on this host")
    # Confinement: only ComfyUI's input/output tree may be bridged in. The
    # endpoint is unauthenticated (LAN-trust) — arbitrary host files (e.g.
    # /etc/passwd) must NOT become media_jobs files (then /dl_token could
    # exfiltrate them).
    base = BASEDIR.resolve()
    rp = src.resolve()
    if not str(rp).startswith(str(base) + "/"):
        raise HTTPException(400, "source must be under the ComfyUI basedir "
                                 f"({base})")
    dest = _unique(_uploads_dir(payload.get("subdirectory")) / f"{_ts()}_{src.name}")
    shutil.copy2(src, dest)
    return {"path": str(dest)}


@app.post("/download")
def api_download(payload: dict):
    url = str(payload.get("url", ""))
    if not url.startswith(("http://", "https://")):
        raise HTTPException(400, "url must be http(s)")
    filename = os.path.basename(str(payload.get("filename") or
                                    urllib.parse.urlparse(url).path)) or "download"
    dest = _unique(_uploads_dir(payload.get("subdirectory")) / f"{_ts()}_{filename}")
    cap = UPLOAD_MAX_MB * 1024 * 1024
    with urllib.request.urlopen(url, timeout=600) as r:
        data = r.read()
    if len(data) > cap:
        raise HTTPException(413, f"file exceeds {UPLOAD_MAX_MB}MB cap")
    dest.write_bytes(data)
    return {"path": str(dest)}


# ------------------------------------------------------------------ M8: client file transfer
@app.post("/upload")
async def api_upload(file: UploadFile = File(...), subdirectory: str = Form(None)):
    dest = _unique(_uploads_dir(subdirectory) / f"{_ts()}_{os.path.basename(file.filename or 'upload')}")
    cap = UPLOAD_MAX_MB * 1024 * 1024
    written = 0
    with open(dest, "wb") as f:
        while True:
            chunk = file.file.read(1 << 20)
            if not chunk:
                break
            written += len(chunk)
            if written > cap:
                f.close()
                dest.unlink(missing_ok=True)
                raise HTTPException(413, f"file exceeds {UPLOAD_MAX_MB}MB cap")
            f.write(chunk)
    return {"path": str(dest)}


@app.post("/dl_token")
def api_dl_token(payload: dict):
    if not MEDIA_DL_SECRET:
        raise HTTPException(503, "MEDIA_DL_SECRET not configured")
    p = _safe_media_jobs_path(str(payload.get("path", "")))
    if p is None or not p.is_file():
        raise HTTPException(404, "not found")
    ttl_h = float(payload.get("ttl_hours", 24))
    if not (0 < ttl_h <= 168):
        raise HTTPException(400, "ttl_hours must be in (0, 168]")
    expiry = int(time.time()) + int(ttl_h * 3600)
    token = _mint_token(str(p), expiry)
    logger.info("dl_token minted: path=%s expires_at=%s",
                p, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(expiry)))
    return {"token": token, "url_path": f"/dl/{token}",
            "expires_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(expiry))}


@app.get("/dl/{token}")
def api_dl(token: str):
    p = _verify_token(token)
    if p is None or not p.is_file():
        raise HTTPException(404, "not found")
    return FileResponse(p)


if __name__ == "__main__":
    # Bind host is configurable so it can run on the host loopback (127.0.0.1)
    # or on all interfaces (0.0.0.0) for LAN/remote MCP access. Default 0.0.0.0
    # so the remote media-mcp client can reach <gpu-host>:8189 (LAN-only, like
    # ComfyUI — do NOT expose publicly).
    _host = os.environ.get("PIPELINE_HOST", "0.0.0.0")
    _port = int(os.environ.get("PIPELINE_PORT", "8189"))
    uvicorn.run(app, host=_host, port=_port, log_level="info")