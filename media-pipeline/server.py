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
  POST /assemble     shots+audio -> final mp4 (ffmpeg, CPU)

GPU serialization: a bounded FIFO job queue (MAX_CONCURRENT_JOBS, default 1) +
fixed worker pool. Waiting depth is bounded by MAX_QUEUE_DEPTH (default 5); once
running+waiting reaches MAX_CONCURRENT_JOBS + MAX_QUEUE_DEPTH, new jobs are
rejected with HTTP 503 + a retry_after back-off hint (FIFO, no priority). GPU
flows additionally serialize on a single thread lock.
"""
import asyncio
import json
import os
import shutil
import subprocess
import threading
import time
import uuid
import urllib.request
from contextlib import contextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse
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

# ------------------------------------------------------------------ job queue
# Bounded FIFO queue + fixed worker pool. At most MAX_CONCURRENT_JOBS media jobs
# run at once; the rest wait with status=queued (position visible in /health).
# Waiting depth is bounded by MAX_QUEUE_DEPTH: once running+waiting reaches
# MAX_CONCURRENT_JOBS + MAX_QUEUE_DEPTH, new jobs are rejected with HTTP 503 and
# a retry_after back-off hint (FIFO, no priority).
MAX_CONCURRENT_JOBS = int(os.environ.get("MAX_CONCURRENT_JOBS", "1"))
MAX_QUEUE_DEPTH = int(os.environ.get("MAX_QUEUE_DEPTH", "5"))
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
        except Exception as e:  # noqa: BLE001 - keep the worker alive
            set_status(jid, "error", error=f"{type(e).__name__}: {e}")
        finally:
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
                 "payload": {k: v for k, v in payload.items() if k != "file_bytes"}}
    return jid


def set_status(jid: str, status: str, **kw):
    JOBS[jid]["status"] = status
    JOBS[jid].update(kw)
    if status in ("done", "error"):
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
        "temperature": 0.7, "max_tokens": 2000,
    }).encode()
    req = urllib.request.Request(VLLM, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        text = json.loads(r.read())["choices"][0]["message"]["content"]
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
    return {"storyboard": str(out), "n_shots": len(data.get("shots", []))}


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


def flow_assemble(payload: dict, jid: str):
    """ffmpeg (in-container): concat video shots, mix audio tracks, output final mp4."""
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
        shots = [_upscale_shot(s, jid, i, res, ns, ufps, useed)
                 for i, s in enumerate(shots)]

    def resolve(p):
        p = Path(p)
        return p if p.is_absolute() else RUN_DIR / p

    norm = []
    for i, s in enumerate(shots):
        sp_host = resolve(s)
        if not sp_host.exists():
            raise FileNotFoundError(f"shot not found: {sp_host}")
        sp = to_container(sp_host)
        n_host = JOB_DIR / jid / f"norm_{i:02d}.mp4"
        n = to_container(n_host)
        run_ffmpeg(["-y", "-i", sp, "-vf",
                    f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                    f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps}",
                    "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-pix_fmt", "yuv420p",
                    "-an", n])
        norm.append(n)
    concat_list_host = JOB_DIR / jid / "concat.txt"
    concat_list = to_container(concat_list_host)
    concat_list_host.write_text("".join(f"file '{n}'\n" for n in norm))
    video_only_host = JOB_DIR / jid / "video_only.mp4"
    video_only = to_container(video_only_host)
    run_ffmpeg(["-y", "-f", "concat", "-safe", "0", "-i", concat_list,
                "-c:v", "libx264", "-preset", "fast", "-crf", "18", video_only])

    tracks = [(vo, float(payload.get("vo_volume", 1.0)), "vo"),
              (music, float(payload.get("music_volume", 0.35)), "music"),
              (sfx, float(payload.get("sfx_volume", 0.9)), "sfx")]
    present = []
    for (p, v, l) in tracks:
        if not p:
            continue
        ap_host = resolve(p)
        if not ap_host.exists():
            raise FileNotFoundError(f"audio not found: {ap_host}")
        present.append((to_container(ap_host), v, l))
    if not present:
        import shutil as _sh
        _sh.copy2(video_only_host, out_host)
        return {"video": _apply_text_overlays(out_host, text_overlays, jid)}

    cmd = ["-y", "-i", video_only]
    filters, labels = [], []
    for i, (p, v, l) in enumerate(present):
        cmd += ["-i", p]
        filters.append(f"[{i + 1}:a]aresample=44100,aformat=channel_layouts=stereo,volume={v}[a{l}]")
        labels.append(l)
    mix = f"{''.join(f'[a{l}]' for l in labels)}amix=inputs={len(labels)}:duration=longest:dropout_transition=0:normalize=0,alimiter=limit=0.95[aout]"
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
                          seed: int = Form(42), steps: int = Form(8)):
    with job_slot():
        jid = new_job("images_edit", {"prompt": prompt, "seed": seed, "steps": steps})
        await save_upload(jid, file)
        copy_to_comfy_input(jid, file.filename)
        enqueue_job(jid, "images_edit",
                    {"prompt": prompt, "seed": seed, "steps": steps},
                    {"image_name": file.filename})
    return {"job_id": jid}


@app.post("/shots")
async def api_shots(file: UploadFile = File(...), prompt: str = Form(...),
                    width: int = Form(768), height: int = Form(512),
                    frames: int = Form(97), fps: float = Form(25.0),
                    seed: int = Form(42), steps: int = Form(8),
                    strength: float = Form(0.7)):
    # strength: how strongly the keyframe anchors the clip. Lower = less
    # deviation/warble (empirically 0.7 is the knee; 0.6 marginally smoother,
    # 0.8+ adds motion but warble). Prompt for visual STYLE, not fast motion.
    with job_slot():
        jid = new_job("shots", {"prompt": prompt, "width": width, "height": height,
                                "frames": frames, "fps": fps, "seed": seed,
                                "steps": steps, "strength": strength})
        await save_upload(jid, file)
        copy_to_comfy_input(jid, file.filename)
        enqueue_job(jid, "shots",
                    {"prompt": prompt, "width": width, "height": height,
                     "frames": frames, "fps": fps, "seed": seed,
                     "steps": steps, "strength": strength},
                    {"keyframe_name": file.filename})
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
                  prompt: str = Form(""), negative_prompt: str = Form(""), fps: int = Form(24)):
    with job_slot():
        jid = new_job("sfx", {"duration": duration, "steps": steps, "cfg": cfg, "seed": seed,
                              "prompt": prompt, "negative_prompt": negative_prompt, "fps": fps})
        await save_upload(jid, file)
        video_rel = f"media_jobs/{jid}/input/{file.filename}"
        enqueue_job(jid, "sfx",
                    {"duration": duration, "steps": steps, "cfg": cfg, "seed": seed,
                     "prompt": prompt, "negative_prompt": negative_prompt, "fps": fps},
                    {"video_rel": video_rel})
    return {"job_id": jid}


@app.post("/upscale")
async def api_upscale(file: UploadFile = File(...), pipeline: str = Form("b"),
                      resolution: int = Form(1080), noise_scale: float = Form(0.0),
                      fps: int = Form(24), seed: int = Form(42)):
    with job_slot():
        jid = new_job("upscale", {"pipeline": pipeline, "resolution": resolution,
                                  "noise_scale": noise_scale, "fps": fps, "seed": seed})
        await save_upload(jid, file)
        video_rel = f"media_jobs/{jid}/input/{file.filename}"
        enqueue_job(jid, "upscale",
                    {"pipeline": pipeline, "resolution": resolution,
                     "noise_scale": noise_scale, "fps": fps, "seed": seed},
                    {"video_rel": video_rel})
    return {"job_id": jid}


@app.post("/assemble")
async def api_assemble(payload: dict):
    with job_slot():
        jid = new_job("assemble", payload)
        enqueue_job(jid, "assemble", payload, {})
    return {"job_id": jid}


if __name__ == "__main__":
    # Bind host is configurable so it can run on the host loopback (127.0.0.1)
    # or on all interfaces (0.0.0.0) for LAN/remote MCP access. Default 0.0.0.0
    # so the remote media-mcp client can reach <gpu-host>:8189 (LAN-only, like
    # ComfyUI — do NOT expose publicly).
    _host = os.environ.get("PIPELINE_HOST", "0.0.0.0")
    _port = int(os.environ.get("PIPELINE_PORT", "8189"))
    uvicorn.run(app, host=_host, port=_port, log_level="info")