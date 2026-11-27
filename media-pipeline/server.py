"""Media pipeline API — GPU-host orchestrator for the remote media-mcp server.

Flows (each returns a job_id; poll GET /jobs/{id}):
  POST /storyboard   brief -> shot list (VLLM, no GPU)
  POST /images       text -> image (Qwen-Image-2512 GGUF + Lightning LoRA)
  POST /images/edit  image+text -> image (Qwen-Image-Edit-2511)
  POST /shots        keyframe+text -> video clip (LTXV 2B I2V)
  POST /tts          text -> speech (XTTS-v2; voice = trailer | default | a
                      library name from GET /voices | a reference wav path,
                      or explicit reference_audio)
  POST /music        prompt+lyrics -> song (ACE-Step 1.5)
  POST /sfx          video -> synced SFX bed (MMAudio)
  POST /upscale      video -> 1080p (A2=Ultrasharp fast | B=SeedVR2 quality)
  POST /assemble     shots+audio -> final mp4 (ffmpeg, CPU; object shots,
                      timestamped sfx list, vo_start, loudnorm — backward compatible)
  POST /trim         cut a clip to a time range (ffmpeg, CPU)
  POST /freeze       still image or video frame -> static N-second clip (ffmpeg, CPU)
  POST /caption      burn text into a clip (ffmpeg drawtext, CPU)
  GET  /jobs         list jobs, newest first (sync): durable jobs.jsonl ledger
                      (finished jobs, survives restarts) merged with the live
                      in-memory registry (in-flight + this process's outputs).
                      Filters: status, client, user, flow, since (ISO), limit
  DELETE /jobs/{id}  kill a QUEUED job (frees its FIFO slot; running -> 409;
                      terminal -> 200 no-op; alias: POST /jobs/{id}/kill)
  GET  /info         ffprobe metadata (sync, not a job)
  GET  /voices       list the TTS voice library (sync)
  POST /voices       register a TTS voice from a reference clip (job; XTTS
                      zero-shot cloning — QC + normalize + sample generation)
  DELETE /voices/{name} remove a TTS voice (sync; trailer/default protected)
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
import faulthandler
import hashlib
import hmac
import json
import logging
import math
import re
import os
import shutil
import signal
import subprocess
import threading
import time
import urllib.parse
import urllib.request
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
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
    "transcribe_worker.py": "/comfy/mnt/venvs/venv-transcribe/bin/python",
}
FFMPEG = "ffmpeg"
logger = logging.getLogger("media-pipeline")
if not logger.handlers:  # make app INFO logs visible in container logs (uvicorn
    # only configures its own loggers; without a handler, app INFO is dropped)
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s"))
    logger.addHandler(_h)
    logger.setLevel(logging.INFO)

app = FastAPI(title="media-pipeline")
# Ops: `docker exec media_pipeline kill -USR1 1` dumps every thread's stack to
# stderr (docker logs) — the hang/deadlock diagnostic (no ptrace in the
# container, so py-spy can't be used).
faulthandler.register(signal.SIGUSR1, all_threads=True)
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

# projects reads MEDIA_PROJECTS_ROOT / MEDIA_NAS_ROOT at import time, so it
# must be imported AFTER load_dotenv() (same reason as metering below).
import projects  # noqa: E402

# Metering is imported AFTER load_dotenv() so the MEDIA_* env vars from .env
# are visible to it (it reads config at import time).
import metering  # noqa: E402


# ------------------------------------------------------------------ LUT staging
# Bundled starting-point LUTs ship in the image at /app/luts/ (33^3 Iridas .cube).
# comfyui_backend's ffmpeg (where /assemble runs lut3d) only sees /comfy/mnt
# (= RUN_DIR) and /basedir, so we stage a copy into RUN_DIR/luts/ at startup.
# Idempotent: only copies when missing or stale (by mtime), so a user can drop
# their own .cube into RUN_DIR/luts/ and it won't be clobbered on restart.
BUNDLED_LUTS = Path("/app/luts")
STAGED_LUTS = RUN_DIR / "luts"


def stage_luts() -> list[str]:
    """Copy bundled .cube LUTs into RUN_DIR/luts/ (visible to comfyui_backend
    as /comfy/mnt/luts/<name>.cube). Returns the staged filenames."""
    staged: list[str] = []
    if not BUNDLED_LUTS.is_dir():
        return staged
    try:
        STAGED_LUTS.mkdir(parents=True, exist_ok=True)
    except OSError:
        return staged
    for cube in sorted(BUNDLED_LUTS.glob("*.cube")):
        dest = STAGED_LUTS / cube.name
        try:
            if dest.exists() and dest.stat().st_mtime >= cube.stat().st_mtime:
                continue  # fresh enough; don't clobber a user's same-named file
            dest.write_bytes(cube.read_bytes())
            staged.append(cube.name)
        except OSError as e:
            logger.warning("LUT staging failed for %s: %s", cube.name, e)
    if staged:
        logger.info("staged %d bundled LUT(s) into %s: %s",
                    len(staged), STAGED_LUTS, ", ".join(staged))
    return staged


def resolve_lut(lut: str) -> Path:
    """Resolve a /assemble lut= value to a .cube file readable by comfyui_backend.
    Accepts a bundled name ('cinematic_teal_amber'), a bare filename with .cube,
    a RUN_DIR-relative path (e.g. 'luts/x.cube' or 'media_projects/<slug>/grade.cube'),
    or an absolute path. Raises ValueError if it can't be resolved to an existing
    .cube file."""
    lut = (lut or "").strip()
    if not lut:
        raise ValueError("lut is empty")
    p = Path(lut)
    if p.is_absolute():
        if p.is_file() and p.suffix == ".cube":
            return p
        raise ValueError(f"lut not found: {p}")
    name = lut if lut.endswith(".cube") else lut + ".cube"
    for base in (STAGED_LUTS, BUNDLED_LUTS, RUN_DIR):
        c = base / name
        if c.is_file() and c.suffix == ".cube":
            return c
    # RUN_DIR-relative path with a subdir (e.g. media_projects/<slug>/grade.cube)
    c = RUN_DIR / lut
    if c.is_file() and c.suffix == ".cube":
        return c
    raise ValueError(f"lut not found (looked for '{name}' in {STAGED_LUTS}, "
                     f"{BUNDLED_LUTS}, {RUN_DIR})")

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

# Job-dir retention: media_jobs/ accumulates one dir per job (inputs, outputs,
# intermediates) plus uploads/, so it grows without bound. A background sweeper
# deletes job dirs (and stale top-level files in uploads/) whose mtime is older
# than MEDIA_JOB_RETENTION_DAYS (default 14). `metrics/` (jobs.jsonl billing/
# audit trail) is NEVER touched, and live jobs (queued/running) are never
# touched. Sweep state is exposed on /health.
MEDIA_JOB_RETENTION_DAYS = float(os.environ.get("MEDIA_JOB_RETENTION_DAYS", "14"))
RETENTION_SWEEP_INTERVAL_S = int(os.environ.get("MEDIA_RETENTION_SWEEP_INTERVAL_S", "3600"))
BASEDIR = Path("/home/chuck/data/comfyui/basedir")  # ComfyUI input/output root

# ------------------------------------------------------------------ TTS voice library
# XTTS-v2 is zero-shot cloning: every voice is a reference clip + a manifest
# entry. The library lives under basedir (NOT media_jobs) so the 14-day
# retention sweeper never touches it. The server runs as root in its container
# and writes here directly; the TTS worker (uid 1024 in comfyui_backend) only
# reads. trailer/default are protected (seeded; trailer_ref.wav is generated
# on first use by the worker).
VOICES_DIR = BASEDIR / "models" / "tts" / "voices"
VOICES_MANIFEST = VOICES_DIR / "manifest.json"
VOICES_PROTECTED = ("trailer", "default")
VOICES_NAME_RE = re.compile(r"^[a-z0-9_]{2,32}$")
VOICES_SAMPLE_TEXT = ("In a world of endless noise, one clear voice can change "
                      "everything. This is a sample of that voice.")
VOICES_REF_MIN_S = 3.0    # below this XTTS cloning degrades
VOICES_REF_MAX_S = 15.0   # longer clips drag the timbre / pick up noise
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
            if JOBS.get(jid, {}).get("killed"):
                # M2: DELETE /jobs/{id} removed this queued job (or the tiny
                # pre-enqueue window). The killer already released the slot —
                # skip it without double-releasing.
                continue
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


# ------------------------------------------------------------------ retention
# Background sweeper for media_jobs/ (see MEDIA_JOB_RETENTION_DAYS above).
# Age is judged by dir/file mtime (a finished job's dir mtime ~= its last
# output write). The JOBS table only covers this process's lifetime, so the
# mtime rule is the primary guard; the live-status check is a second guard
# against sweeping a job that is queued/running right now.
RETENTION_STATE = {"last_run": None, "last_deleted": 0, "last_bytes_freed": 0}


def _dir_size(p: Path) -> int:
    total = 0
    try:
        for f in p.rglob("*"):
            if f.is_file():
                total += f.stat().st_size
    except OSError:
        pass
    return total


def _retention_sweep() -> None:
    """Delete job dirs + uploaded files older than the retention window."""
    global RETENTION_STATE
    cutoff = time.time() - MEDIA_JOB_RETENTION_DAYS * 86400
    live = {j for j, st in JOBS.items() if st.get("status") in ("queued", "running")}
    deleted, freed = 0, 0
    for entry in sorted(JOB_DIR.iterdir()):
        if entry.name in ("metrics", "uploads"):
            continue  # billing/audit trail + uploads are handled separately
        if not entry.is_dir() or entry.name in live:
            continue
        try:
            mtime = entry.stat().st_mtime
            if mtime >= cutoff:
                continue
        except OSError:
            continue
        size = _dir_size(entry)
        try:
            shutil.rmtree(entry)
        except OSError as e:
            logger.warning("retention: could not remove job dir %s: %s", entry.name, e)
            continue
        deleted += 1
        freed += size
        logger.info("retention: removed job dir %s (%.1f MB, mtime %s)",
                    entry.name, size / 1e6,
                    time.strftime("%Y-%m-%d", time.gmtime(mtime)))
    if UPLOADS_DIR.is_dir():
        for f in sorted(UPLOADS_DIR.iterdir()):
            if not f.is_file():
                continue
            try:
                if f.stat().st_mtime >= cutoff:
                    continue
            except OSError:
                continue
            size = f.stat().st_size
            try:
                f.unlink()
            except OSError as e:
                logger.warning("retention: could not remove upload %s: %s", f.name, e)
                continue
            deleted += 1
            freed += size
            logger.info("retention: removed stale upload %s (%.1f MB)", f.name, size / 1e6)
    RETENTION_STATE = {"last_run": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                       "last_deleted": deleted, "last_bytes_freed": freed}
    if deleted:
        logger.info("retention sweep: removed %d entries, freed %.1f MB "
                    "(retention=%sd)", deleted, freed / 1e6, MEDIA_JOB_RETENTION_DAYS)


def _retention_loop() -> None:
    """Hourly sweeper; a failure must never kill the service."""
    while True:
        try:
            _retention_sweep()
        except Exception as e:  # noqa: BLE001 - keep the sweeper alive
            logger.warning("retention sweep failed: %s", e)
        try:
            # Films B1: retry pending NAS project mirrors on the same cadence.
            projects.nas_pending_sync()
        except Exception as e:  # noqa: BLE001
            logger.warning("NAS pending sync failed: %s", e)
        time.sleep(RETENTION_SWEEP_INTERVAL_S)


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
    # Job IDs must be recoverable from `docker logs media_pipeline` even when
    # the caller dies (2026-11-02 incident: lost IDs were unrecoverable).
    logger.info("job submitted: %s flow=%s client=%s user=%s",
                jid, flow, payload.get("client") or "-", payload.get("user") or "-")
    return jid


def set_status(jid: str, status: str, **kw):
    JOBS[jid]["status"] = status
    JOBS[jid].update(kw)
    if status in ("done", "error", "timeout"):
        JOBS[jid]["finished"] = time.time()
        logger.info("job finished: %s status=%s error=%s",
                    jid, status,
                    str(kw.get("error"))[:200] if kw.get("error") else "-")


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
    """Run a worker script inside the comfyui container (GPU work).
    Worker stdout is logged on success too — the stage/coverage lines
    (e.g. transcribe's pitch-contour coverage) are the only job trace."""
    py = WORKER_PY.get(worker, "/comfy/mnt/venv/bin/python")
    cmd = ["docker", "exec", "-u", "comfy", CONTAINER, py, f"{WORKERS}/{worker}"] + args
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(f"worker failed: {r.stderr[-2000:]}")
    for line in r.stdout.strip().splitlines():
        logger.info("worker %s: %s", worker, line)
    return r.stdout.strip()


# ------------------------------------------------------------------ VRAM headroom
# ComfyUI retains the last-loaded model resident — after a wan14b shot ~10 GiB
# stays on the card. Out-of-ComfyUI GPU workers (ACE-Step music ~6.5 GiB, TTS)
# then OOM against it (found in E2E 2026-10-31: music job failed with 10 MiB
# free on the GPU 0 co-location layout). ComfyUI only offloads when *its own*
# allocation fails, which never happens for a foreign process — so before such
# a flow, ensure free VRAM: (1) if the Ollama copywriter is resident on GPU 1,
# stop it (instant — plan §4.1.4, Phase 6); (2) else restart comfyui_backend
# (~40 s, only when its queue is idle).
VRAM_HEADROOM_GIB = {"music": 8.0, "tts": 2.0, "transcribe": 4.0}
OLLAMA_CONTAINER = "ollama"
OLLAMA_COPYWRITER_MODEL = os.environ.get("OLLAMA_COPYWRITER_MODEL", "gemma4:31b")


def _gpu_free_gib() -> float | None:
    """Free GPU memory in GiB, via comfyui_backend's nvidia-smi (the pipeline
    container has the docker socket but no nvidia-smi itself). None =
    unmeasurable — callers proceed as-is (never block a job on a probe)."""
    try:
        r = subprocess.run(["docker", "exec", CONTAINER, "nvidia-smi",
                            "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
                           capture_output=True, text=True, timeout=30)
        return float(r.stdout.strip().splitlines()[0]) / 1024.0
    except Exception:
        return None


def _wait_comfyui_idle(timeout_s: float = 900) -> None:
    """Wait until ComfyUI's queue is empty (never restart mid-job)."""
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        try:
            with urllib.request.urlopen(cc.COMFY + "/queue", timeout=10) as r:
                if not json.loads(r.read()).get("queue_running"):
                    return
        except Exception:
            return  # ComfyUI down = nothing running
        time.sleep(5)


def _wait_comfyui_ready(timeout_s: float = 300) -> None:
    """Wait until ComfyUI answers /queue again after a restart."""
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        try:
            with urllib.request.urlopen(cc.COMFY + "/queue", timeout=10) as r:
                json.loads(r.read())
            return
        except Exception:
            time.sleep(5)
    raise RuntimeError(f"{CONTAINER} did not come back within {timeout_s:.0f}s")


def _ollama_copywriter_resident() -> bool:
    """Is the Ollama copywriter loaded on GPU 1 right now? (Phase 6, plan
    §4.1.4) True = `ollama ps` lists the configured copywriter model."""
    try:
        r = subprocess.run(["docker", "exec", OLLAMA_CONTAINER, "ollama", "ps"],
                           capture_output=True, text=True, timeout=15)
        return OLLAMA_COPYWRITER_MODEL in r.stdout
    except Exception:
        return False


def _ollama_stop_copywriter() -> None:
    """Unload the copywriter instantly (weights stay on disk; the next copy
    request reloads in ~10–30 s). Cheaper than restarting ComfyUI (~40 s).
    No-op if the model isn't loaded."""
    subprocess.run(["docker", "exec", OLLAMA_CONTAINER, "ollama", "stop",
                    OLLAMA_COPYWRITER_MODEL], capture_output=True, text=True,
                   timeout=60)


def _ensure_vram_headroom(flow: str, jid: str) -> None:
    """Free GPU 1's resident-model VRAM before a non-ComfyUI GPU worker.

    No-op when headroom is already sufficient or unmeasurable. When low:
    (1) stop the Ollama copywriter if resident (instant, Phase 6 / plan
    §4.1.4); (2) re-check; (3) if still low, wait for an idle ComfyUI queue
    and restart comfyui_backend (frees its resident model) and wait for it
    to come back (~40 s)."""
    min_gib = VRAM_HEADROOM_GIB.get(flow)
    if min_gib is None:
        return
    free = _gpu_free_gib()
    if free is None or free >= min_gib:
        return
    if _ollama_copywriter_resident():
        print(f"[{jid}] {flow}: VRAM headroom {free:.1f} GiB < {min_gib:.0f} GiB — "
              f"stopping Ollama copywriter ({OLLAMA_COPYWRITER_MODEL}) to free GPU 1",
              flush=True)
        _ollama_stop_copywriter()
        free = _gpu_free_gib()
        if free is not None and free >= min_gib:
            print(f"[{jid}] {flow}: copywriter stopped, free {free:.1f} GiB — proceeding",
                  flush=True)
            return
    _wait_comfyui_idle()
    free = _gpu_free_gib()
    if free is None or free >= min_gib:
        return
    print(f"[{jid}] {flow}: VRAM headroom {free:.1f} GiB < {min_gib:.0f} GiB — "
          f"restarting {CONTAINER} to free ComfyUI's resident model", flush=True)
    subprocess.run(["docker", "restart", CONTAINER], capture_output=True, text=True,
                   timeout=300)
    _wait_comfyui_ready()
    free2 = _gpu_free_gib()
    if free2 is not None:
        print(f"[{jid}] {flow}: {CONTAINER} back up (free {free2:.1f} GiB)", flush=True)
    else:
        print(f"[{jid}] {flow}: {CONTAINER} back up", flush=True)


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


def _stage_references(jid: str, references) -> list[str]:
    """Stage up to 9 reference images into ComfyUI's input dir (qwen21 edit).

    Each entry is either a filename already in ComfyUI input/ (used as-is)
    or a media_jobs-relative / absolute host path (e.g. media_jobs/<jid>/x.png)
    copied in under a unique name. Returns the ComfyUI input/ filenames in
    order. Raises on a missing source (fail fast at enqueue time is too late;
    this runs in the flow, so the job errors cleanly)."""
    if not references:
        return []
    if isinstance(references, str):
        refs = [r.strip() for r in references.split(",") if r.strip()]
    else:
        refs = [str(r) for r in references if str(r).strip()]
    refs = refs[:wf.QWEN21_MAX_REFS]
    out = []
    for i, ref in enumerate(refs):
        if "/" in ref:
            src = _resolve_src(ref)  # media_jobs-relative or absolute host path
            dest_name = f"ref_{jid}_{i}{src.suffix}"
            r = subprocess.run(["docker", "cp", str(src), f"{CONTAINER}:/basedir/input/{dest_name}"],
                               capture_output=True, text=True, timeout=120)
            if r.returncode != 0:
                raise RuntimeError(f"stage reference {ref} failed: {r.stderr[-500:]}")
            out.append(dest_name)
        else:
            out.append(ref)  # already a ComfyUI input/ filename
    return out


def flow_images(payload: dict, jid: str):
    prefix = f"mp_{jid}"
    model = wf.resolve_image_model(payload)
    with GPU_LOCK:
        if model == "qwen21":
            w, h = int(payload.get("width", 1280)), int(payload.get("height", 720))
            if w * h > 1_600_000:
                print(f"[{jid}] WARNING: qwen21 at {w}x{h} ({w * h / 1e6:.1f} MP) is above the "
                      f"~1 MP sweet spot for the 12 GB VRAM budget; expect slow/offloaded runs", flush=True)
            res = cc.comfy_run(wf.qwen_image_21_t2i(
                payload["prompt"], w, h, int(payload.get("seed", 42)),
                steps=wf.qwen21_steps(payload), prefix=prefix))
        else:
            res = cc.comfy_run(wf.qwen_image_t2i(payload["prompt"], int(payload.get("width", 1280)),
                                                 int(payload.get("height", 720)), int(payload.get("seed", 42)),
                                                 payload.get("lora", "Qwen-Image-2512-Lightning-4steps-V1.0-bf16.safetensors"),
                                                 int(payload.get("steps") or 4), prefix=prefix))
    paths = copy_comfy_outputs(jid, res["outputs"], prefix)
    if not paths:
        raise RuntimeError("no image output found")
    return {"image": paths[0], "model": model}


def flow_images_edit(payload: dict, jid: str, image_name: str):
    prefix = f"mp_{jid}"
    model = wf.resolve_image_model(payload)
    refs = []
    with GPU_LOCK:
        if model == "qwen21":
            refs = _stage_references(jid, payload.get("references"))
            res = cc.comfy_run(wf.qwen_image_21_edit(
                payload["prompt"], image_name, references=refs,
                seed=int(payload.get("seed", 42)), steps=wf.qwen21_steps(payload), prefix=prefix))
        else:
            res = cc.comfy_run(wf.qwen_image_edit(payload["prompt"], image_name, int(payload.get("seed", 42)),
                                                  steps=int(payload.get("steps") or 8), prefix=prefix))
    paths = copy_comfy_outputs(jid, res["outputs"], prefix)
    if not paths:
        raise RuntimeError("no image output found")
    return {"image": paths[0], "model": model, "references": len(refs)}


def flow_shots(payload: dict, jid: str, keyframe_name: str):
    prefix = f"mp_{jid}"
    model = wf.resolve_shot_model(payload)
    d = wf.SHOT_MODEL_DEFAULTS[model]
    with GPU_LOCK:
        if model == "wan14b":
            if payload.get("end_keyframe_name"):
                # Films B2: first-last-frame (FLF2V) — official Comfy-Org
                # video_wan2_2_14B_flf2v base-mode graph.
                res = cc.comfy_run(wf.wan22_flf2v(keyframe_name, payload["end_keyframe_name"],
                                                  payload["prompt"],
                                                  int(payload.get("width") or d["width"]),
                                                  int(payload.get("height") or d["height"]),
                                                  int(payload.get("frames") or d["frames"]),
                                                  float(payload.get("fps") or d["fps"]),
                                                  int(payload.get("seed", 42)),
                                                  prefix=prefix), timeout=7200)
            else:
                res = cc.comfy_run(wf.wan22_i2v(keyframe_name, payload["prompt"],
                                                int(payload.get("width") or d["width"]),
                                                int(payload.get("height") or d["height"]),
                                                int(payload.get("frames") or d["frames"]),
                                                float(payload.get("fps") or d["fps"]),
                                                int(payload.get("seed", 42)),
                                                prefix=prefix), timeout=7200)
        else:
            res = cc.comfy_run(wf.ltxv_i2v(keyframe_name, payload["prompt"],
                                           int(payload.get("width") or d["width"]),
                                           int(payload.get("height") or d["height"]),
                                           int(payload.get("frames") or d["frames"]),
                                           float(payload.get("fps") or d["fps"]),
                                           int(payload.get("seed", 42)),
                                           prefix=prefix,
                                           strength=float(payload.get("strength", 0.7))), timeout=7200)
    paths = copy_comfy_outputs(jid, res["outputs"], prefix)
    if not paths:
        raise RuntimeError("no video output found")
    result = {"video": paths[0], "model": model,
              "frames": int(payload.get("frames") or d["frames"]),
              "fps": float(payload.get("fps") or d["fps"]),
              "end_keyframe": bool(payload.get("end_keyframe_name"))}
    # Films B1: shots registered to a project land in takes.json +
    # production/clips/ (bookkeeping only — a failure here must never fail a
    # finished render).
    proj = payload.get("project")
    if proj:
        try:
            take = _project_register_shot(proj, payload.get("shot_id") or f"shot_{jid}",
                                          payload, jid, paths[0])
            result["project"] = proj
            result["take"] = take
        except Exception as e:  # noqa: BLE001
            logger.warning("project take registration failed for %s/%s: %s",
                           proj, payload.get("shot_id"), e)
    return result


# ------------------------------------------------------------------ TTS voices
# Voice library management (manifest + reference clips under basedir) and
# resolution of the /tts 'voice' value. The worker CLI is unchanged: it takes
# --voice {trailer,default} or --reference-audio <path>; all library logic
# lives here so existing jobs/clients keep working.

def _load_voices_manifest() -> dict:
    if VOICES_MANIFEST.exists():
        try:
            d = json.loads(VOICES_MANIFEST.read_text())
            if isinstance(d, dict) and isinstance(d.get("voices"), dict):
                return d
        except Exception as e:
            logger.warning("voices manifest unreadable (%s); treating as empty", e)
    return {"voices": {}}


def _save_voices_manifest(m: dict) -> None:
    VOICES_DIR.mkdir(parents=True, exist_ok=True)
    tmp = VOICES_MANIFEST.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(m, indent=2) + "\n")
    tmp.replace(VOICES_MANIFEST)  # atomic


def _seed_voices_manifest() -> None:
    """Create the voice manifest with the two legacy voices on first boot."""
    if VOICES_MANIFEST.exists():
        return
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    m = {"voices": {
        "trailer": {"file": "trailer_ref.wav", "sample": "trailer_sample.wav",
                    "description": "Deep male movie-trailer narrator (pitch-shifted "
                                   "clone of the stock sample). The default voice.",
                    "gender": "male", "style": "trailer", "added": now},
        "default": {"file": "en_sample.wav", "sample": "default_sample.wav",
                    "description": "Standard male XTTS stock voice (unmodified "
                                   "en_sample.wav).",
                    "gender": "male", "style": "default", "added": now},
    }}
    try:
        _save_voices_manifest(m)
        logger.info("voices manifest seeded at %s", VOICES_MANIFEST)
    except Exception as e:
        logger.warning("could not seed voices manifest: %s", e)


def _list_voices() -> list[dict]:
    out = []
    for name, v in _load_voices_manifest()["voices"].items():
        ref = VOICES_DIR / v.get("file", f"{name}_ref.wav")
        sample = VOICES_DIR / v.get("sample", f"{name}_sample.wav")
        out.append({
            "name": name,
            "description": v.get("description", ""),
            "gender": v.get("gender", "unknown"),
            "style": v.get("style", ""),
            "added": v.get("added"),
            "protected": name in VOICES_PROTECTED,
            "ref_exists": ref.is_file(),
            "sample": str(sample) if sample.is_file() else None,
        })
    out.sort(key=lambda x: x["name"])
    return out


def _resolve_voice(voice: str) -> tuple[str, str]:
    """Resolve a /tts 'voice' value to (kind, value).

    kind="voice"      -> pass through to the worker's --voice {trailer,default}
    kind="reference"  -> pass to --reference-audio (container path)

    Rules:
      1. "trailer"/"default"               -> legacy worker voices (the worker
         generates trailer_ref.wav on first use)
      2. a manifest name                   -> its reference clip
      3. a path (contains "/" or ends .wav) -> host path under RUN_DIR/BASEDIR
    Raises ValueError (with the available voice names) otherwise.
    """
    v = str(voice or "trailer").strip()
    if v in VOICES_PROTECTED:
        return "voice", v
    m = _load_voices_manifest()["voices"]
    available = ", ".join(sorted(set(m) | set(VOICES_PROTECTED)))
    if v in m:
        return "reference", to_container_any(VOICES_DIR / m[v].get("file", f"{v}_ref.wav"))
    if "/" in v or v.lower().endswith(".wav"):
        p = Path(v).expanduser()
        if not p.is_absolute():
            for base in (JOB_DIR, RUN_DIR):
                cand = (base / v).resolve()
                if cand.is_file():
                    p = cand
                    break
        p = p.resolve()
        if p.is_file() and (str(p).startswith(str(RUN_DIR) + "/")
                            or str(p).startswith(str(BASEDIR) + "/")):
            return "reference", to_container_any(p)
        raise ValueError(
            f"voice {v!r} is not a registered voice and not a usable reference "
            f"wav (must be a file under {RUN_DIR} or {BASEDIR}). "
            f"Available voices: {available}")
    raise ValueError(
        f"unknown voice {v!r}. Available: {available} "
        "(or pass a reference wav path, or 'reference_audio')")


def _probe_duration_s(path) -> float:
    """ffprobe duration in seconds (host path; ffprobe runs in the container)."""
    r = subprocess.run(["docker", "exec", "-u", "comfy", CONTAINER, "ffprobe",
                        "-v", "quiet", "-show_entries", "format=duration",
                        "-of", "csv=p=0", to_container_any(str(path))],
                       capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {r.stderr[-500:]}")
    return float(r.stdout.strip())


def flow_tts(payload: dict, jid: str):
    out_host = JOB_DIR / jid / "vo.wav"
    out_container = f"{JOB_DIR_CONTAINER}/{jid}/vo.wav"  # worker runs inside container
    ref = str(payload.get("reference_audio") or payload.get("voice") or "trailer").strip()
    kind, val = _resolve_voice(ref)
    args = ["--text", payload["text"]]
    if kind == "voice":
        args += ["--voice", val]
    else:
        args += ["--reference-audio", val]
    args += ["--out", out_container]
    _ensure_vram_headroom("tts", jid)
    run_worker("tts_worker.py", args, timeout=1800)
    return {"audio": str(out_host)}


def flow_transcribe(payload: dict, jid: str):
    """Song -> guitar tab + timestamped lyrics (spec: docs/transcribe_pipeline_spec.md).
    Tab data is deterministic from the audio; the LLM only labels/cleans/annotates
    (validated, with a deterministic fallback) — the job is never blocked on it."""
    instrument = str(payload.get("instrument", "guitar")).strip()
    if instrument != "guitar":
        raise ValueError("v1 supports instrument=guitar only")
    tuning = str(payload.get("tuning", "standard")).strip()
    if tuning != "standard":
        raise ValueError("v1 supports tuning=standard only")
    transpose = payload.get("transpose_semitones", 0)
    if isinstance(transpose, bool) or not isinstance(transpose, int) \
            or not (-12 <= transpose <= 12):
        raise ValueError("transpose_semitones must be an integer in -12..12")
    target_key = str(payload.get("target_key") or "").strip()
    if target_key and not re.match(r"^[A-G][#b]?m?$", target_key):
        raise ValueError("target_key must be a tonic like 'E', 'Bb', 'F#m'")
    chord_style = str(payload.get("chord_style", "as_detected")).strip().lower()
    if chord_style not in ("as_detected", "triads", "power"):
        raise ValueError("chord_style must be as_detected|triads|power")
    if not isinstance(payload.get("shape_hints", True), bool):
        raise ValueError("shape_hints must be a boolean")
    capo = payload.get("capo", False)
    if not isinstance(capo, bool):
        raise ValueError("capo must be a boolean")
    src = _resolve_src(payload["path"])  # media_jobs-relative or absolute host path
    out_host = JOB_DIR / jid
    out_container = f"{JOB_DIR_CONTAINER}/{jid}"
    args = ["--path", to_container(src), "--out-dir", out_container,
            "--source-display", str(payload["path"]),
            "--instrument", instrument, "--tuning", tuning,
            "--lyrics", str(payload.get("lyrics", True)).lower(),
            "--stems", str(payload.get("stems", True)).lower(),
            "--full-notes", str(payload.get("full_notes", False)).lower(),
            "--transpose", str(transpose),
            "--target-key", target_key,
            "--chord-style", chord_style,
            "--shape-hints", str(payload.get("shape_hints", True)).lower(),
            "--capo", str(capo).lower(),
            "--whisper-model", str(payload.get("whisper_model")
                                    or os.environ.get("MEDIA_TRANSCRIBE_WHISPER_MODEL", "large-v3")),
            "--whisper-device", os.environ.get("MEDIA_TRANSCRIBE_WHISPER_DEVICE", "cuda"),
            "--demucs-model", os.environ.get("MEDIA_TRANSCRIBE_DEMUCS_MODEL", "htdemucs"),
            "--max-fret", os.environ.get("MEDIA_TRANSCRIBE_MAX_FRET", "15"),
            "--min-section-s", os.environ.get("MEDIA_TRANSCRIBE_MIN_SECTION_S", "4"),
            # vLLM is reached via the host LAN IP from inside the comfyui container
            # (the pipeline container itself is host-net; the worker is not).
            "--vllm-url", os.environ.get("MEDIA_TRANSCRIBE_VLLM_URL",
                                         "http://192.168.4.55:8000/v1/chat/completions"),
            "--vllm-model", os.environ.get("MEDIA_TRANSCRIBE_VLLM_MODEL", VLLM_MODEL)]
    _ensure_vram_headroom("transcribe", jid)
    timeout = int(os.environ.get("MEDIA_TRANSCRIBE_TIMEOUT_S", "1800"))
    run_worker("transcribe_worker.py", args, timeout=timeout)
    return {"analysis": str(out_host / "analysis.json"),
            "tab": str(out_host / "tab.md"),
            "lyrics": str(out_host / "lyrics.txt"),
            "midi": str(out_host / "song.mid"),
            "audio": str(out_host / "audio.wav")}


def flow_voices_add(payload: dict, jid: str):
    """Register a TTS voice: QC + normalize a reference clip, upsert the
    manifest, generate a sample with the new voice (GPU)."""
    name = str(payload.get("name", "")).strip()
    if not VOICES_NAME_RE.match(name):
        raise ValueError(f"invalid voice name {name!r} (use [a-z0-9_] 2-32 chars)")
    if name in VOICES_PROTECTED:
        raise ValueError(f"voice {name!r} is protected; choose another name")
    src = Path(str(payload.get("source", ""))).expanduser()
    if not src.is_absolute() or not src.is_file():
        raise ValueError("source must be an existing absolute path on this host "
                         f"(e.g. a media_jobs file: {JOB_DIR}/<jid>/<file>)")
    dur = _probe_duration_s(src)
    if not (VOICES_REF_MIN_S <= dur <= VOICES_REF_MAX_S):
        raise ValueError(f"reference clip is {dur:.1f}s; XTTS wants "
                         f"{VOICES_REF_MIN_S:.0f}-{VOICES_REF_MAX_S:.0f}s "
                         "(>=6s recommended for stable cloning)")
    # Normalize to 16k mono wav in the job dir, then install into the library.
    ref_job = JOB_DIR / jid / "ref_16k.wav"
    run_ffmpeg(["-y", "-i", to_container_any(str(src)), "-ac", "1", "-ar", "16000",
                "-c:a", "pcm_s16le", to_container_any(str(ref_job))])
    VOICES_DIR.mkdir(parents=True, exist_ok=True)
    ref_host = VOICES_DIR / f"{name}_ref.wav"
    shutil.copy2(ref_job, ref_host)
    # Sample with the new voice (GPU) so it can be auditioned immediately.
    sample_text = str(payload.get("sample_text") or VOICES_SAMPLE_TEXT)
    _ensure_vram_headroom("tts", jid)
    run_worker("tts_worker.py",
               ["--text", sample_text, "--reference-audio", to_container_any(ref_host),
                "--out", f"{JOB_DIR_CONTAINER}/{jid}/sample.wav"],
               timeout=1800)
    sample_host = VOICES_DIR / f"{name}_sample.wav"
    shutil.copy2(JOB_DIR / jid / "sample.wav", sample_host)
    # Manifest upsert (atomic; keeps the existing description on re-register).
    m = _load_voices_manifest()
    prev = m["voices"].get(name, {})
    m["voices"][name] = {
        "file": f"{name}_ref.wav",
        "sample": f"{name}_sample.wav",
        "description": str(payload.get("description") or prev.get("description") or ""),
        "gender": str(payload.get("gender") or prev.get("gender") or "unknown"),
        "style": str(payload.get("style") or prev.get("style") or ""),
        "added": prev.get("added") or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    _save_voices_manifest(m)
    logger.info("voice registered: %s (ref=%s, dur=%.1fs)", name, ref_host, dur)
    return {"voice": name, "ref": str(ref_host), "sample": str(sample_host),
            "ref_duration_s": round(dur, 2)}


def flow_music(payload: dict, jid: str):
    out_host = JOB_DIR / jid / "music.wav"
    out_container = f"{JOB_DIR_CONTAINER}/{jid}/music.wav"  # worker runs inside container
    with GPU_LOCK:
        _ensure_vram_headroom("music", jid)
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
        # ffmpeg ends stderr with version/stream info, so the useful error is
        # often earlier — include the first Error line plus the tail.
        err_lines = [l for l in r.stderr.splitlines() if "Error" in l or "error" in l]
        head = err_lines[0][:800] if err_lines else ""
        raise RuntimeError(f"ffmpeg failed: {head} | tail: {r.stderr[-800:]}")
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


# Films B2: animatic camera moves (zoompan expressions from gemini-insights.md
# "Build a Talking/Moving Animatic First" — pre-scale to 3x before zoompan to
# avoid jittery pixel-stepping). {frames} is substituted per shot.
ANIMATIC_MOVES = {
    "push_in":   "z='min(zoom+0.0015,1.12)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'",
    "pull_out":  "z='if(lte(zoom,1.0),1.12,max(1.001,zoom-0.0015))':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'",
    "pan_left":  "z='1.1':x='(iw-iw/zoom)*(on/{frames})':y='ih/2-(ih/zoom/2)'",
    "pan_right": "z='1.1':x='(iw-iw/zoom)*(1-on/{frames})':y='ih/2-(ih/zoom/2)'",
    "shake":     "z='1.08':x='iw/2-(iw/zoom/2)+sin(on*2.5)*18':y='ih/2-(ih/zoom/2)+cos(on*3.1)*18'",
    "static":    "z='1.0':x='0':y='0'",
}


def _grade_filter(payload: dict) -> str:
    """Films B2: build the /assemble grade filter (3D LUT + temporal film grain).
    Returns '' when no grade is requested (backward compatible — params absent
    = today's behavior). `lut` resolves via resolve_lut(); `grain` in [0,1]
    maps to a subtle temporal noise amplitude."""
    parts = []
    lut = payload.get("lut")
    if lut:
        lp = resolve_lut(str(lut))
        parts.append(f"lut3d={to_container_any(lp)}")
    grain = payload.get("grain")
    if grain is not None:
        g = float(grain)
        if not (0.0 <= g <= 1.0):
            raise ValueError("grain must be in [0.0, 1.0]")
        if g > 0:
            alls = max(1, round(g * 8))  # subtle: 1.0 -> alls=8
            parts.append(f"noise=alls={alls}:allf=t")
    return ",".join(parts)


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


# M1: |natural - duration| tolerance for assemble shot duration hold/trim.
DURATION_TOL_S = 0.1


def flow_animatic(payload: dict, jid: str):
    """Films B2: Tier-0 pacing gate (ffmpeg only, no GPU). Stitch storyboard
    keyframes into a preview cut with simulated camera moves (zoompan) + shot
    labels, so you can 'watch the whole scene in 20 s before burning 11 min/
    clip'. Per-shot: pre-scale to 3x (anti-jitter) -> zoompan move -> drawtext
    label; then concat + optional audio mux (-shortest)."""
    manifest = payload.get("shots") or payload.get("manifest")
    if not isinstance(manifest, list) or not manifest:
        raise ValueError("shots: non-empty list of {image, duration, move} required")
    width = int(payload.get("width", 1280))
    height = int(payload.get("height", 720))
    fps = int(payload.get("fps", 24))
    font = payload.get("font", FONT_BOLD)
    pre_w, pre_h = width * 3, height * 3  # 3x pre-scale (anti-jitter)
    job = JOB_DIR / jid
    segs = []
    total_s = 0.0
    for i, shot in enumerate(manifest):
        if not isinstance(shot, dict) or not shot.get("image"):
            raise ValueError(f"shots[{i}]: {{image, duration, move}} object required")
        img = _resolve_src(shot["image"])
        dur = float(shot.get("duration", 2.0))
        if dur <= 0:
            raise ValueError(f"shots[{i}].duration must be positive")
        move = str(shot.get("move", "static")).strip().lower()
        if move not in ANIMATIC_MOVES:
            raise ValueError(f"shots[{i}].move: unknown move '{move}' "
                             f"(valid: {', '.join(ANIMATIC_MOVES)})")
        frames = max(1, round(dur * fps))
        zoom = ANIMATIC_MOVES[move].format(frames=frames)
        label = f"Shot {i + 1} · {dur:.1f}s"
        lt = job / f"label_{i:02d}.txt"
        lt.write_text(label)
        seg = job / f"seg_{i:03d}.mp4"
        fs = max(16, height // 24)
        vf = (f"scale={pre_w}:{pre_h}:force_original_aspect_ratio=increase,"
              f"crop={pre_w}:{pre_h},"
              f"zoompan={zoom}:d={frames}:s={width}x{height}:fps={fps},"
              f"drawtext=fontfile={font}:textfile={to_container(lt)}:fontsize={fs}:"
              f"fontcolor=white:borderw=2:bordercolor=black:x=24:y=24")
        run_ffmpeg(["-y", "-i", to_container_any(img), "-vf", vf,
                    "-frames:v", str(frames), "-c:v", "libx264", "-crf", "18",
                    "-preset", "veryfast", "-pix_fmt", "yuv420p", "-r", str(fps),
                    to_container(seg)], timeout=900)
        segs.append(seg)
        total_s += dur
    # concat (identical-encode segments -> demuxer + stream copy)
    lst = job / "concat.txt"
    lst.write_text("".join(f"file '{to_container(s)}'\n" for s in segs))
    out = job / f"mp_{jid}_00001.mp4"
    cmd = ["-y", "-f", "concat", "-safe", "0", "-i", to_container(lst)]
    audio = payload.get("audio")
    if audio:
        ap = _resolve_src(audio)
        cmd += ["-i", to_container_any(ap), "-c:a", "aac", "-b:a", "192k", "-shortest"]
    cmd += ["-c:v", "copy", to_container(out)]
    run_ffmpeg(cmd, timeout=900)
    return {"video": str(out), "shots": len(segs),
            "duration_s": round(total_s, 3), "width": width, "height": height, "fps": fps}


def flow_extract_frame(payload: dict, jid: str):
    """Films B2: extract frame(s) from a video (ffmpeg only). Exactly one of:
    `frame` (int index), `time` (seconds), `positions` (list of
    'first'/'middle'/'last'), or `n` (int -> n frames evenly spaced across the
    duration). Returns PNG path(s). Used for frame-chaining (last clean frame
    -> next shot's start) and QC."""
    src = _resolve_src(payload["source"])
    job = JOB_DIR / jid
    frame = payload.get("frame")
    t = payload.get("time")
    positions = payload.get("positions")
    n = payload.get("n")
    if sum(x is not None for x in (frame, t, positions, n)) != 1:
        raise ValueError("provide exactly one of: frame (int), time (s), "
                         "positions (list), n (int even-spacing)")
    dur = _probe_summary(src)["duration_s"] or 0.0
    if not dur:
        raise ValueError("source has no video duration")
    outs = []
    if frame is not None:
        fi = int(frame)
        out = job / f"frame_{fi:05d}.png"
        run_ffmpeg(["-y", "-i", to_container_any(src), "-vf", f"select=eq(n\\,{fi})",
                    "-frames:v", "1", "-update", "1", to_container(out)], timeout=600)
        outs.append(out)
    elif t is not None:
        ts = float(t)
        out = job / f"frame_t{ts:08.3f}.png"
        run_ffmpeg(["-y", "-ss", f"{ts:.3f}", "-i", to_container_any(src),
                    "-frames:v", "1", "-update", "1", to_container(out)], timeout=600)
        outs.append(out)
    else:
        # positions: list of 'first'/'middle'/'last', or n for even spacing
        if positions is not None:
            pts = []
            for p in positions:
                p = str(p).strip().lower()
                if p == "first":
                    pts.append(0.0)
                elif p == "middle":
                    pts.append(dur / 2.0)
                elif p == "last":
                    pts.append(max(0.0, dur - 0.04))
                else:
                    raise ValueError(f"positions: unknown '{p}' (first/middle/last)")
        else:
            # n: even spacing — n frames across the duration (inclusive ends)
            n = max(2, int(n))
            pts = [dur * k / (n - 1) for k in range(n)]
            pts[-1] = max(0.0, dur - 0.04)
        for k, ts in enumerate(pts):
            out = job / f"frame_{k:02d}_{ts:08.3f}.png"
            run_ffmpeg(["-y", "-ss", f"{ts:.3f}", "-i", to_container_any(src),
                        "-frames:v", "1", "-update", "1", to_container(out)], timeout=600)
            outs.append(out)
    return {"frames": [str(o) for o in outs], "count": len(outs)}


def flow_assemble(payload: dict, jid: str):
    """ffmpeg (in-container): normalize shots, concat, mix audio tracks, output final mp4.

    Backward compatible: string-array shots + single sfx path behave exactly as
    before. New: object shots {path, in?, out?, duration?} (still + duration =
    static clip), sfx as a list of {path, at} for timestamped placement, vo_start
    (VO offset from t=0), loudnorm (EBU R128 on the final mix).

    M1: for object shots with `duration`, in/out define the source window first
    and `duration` then applies to the resulting clip: hold the last frame
    (tpad=stop_mode=clone) when duration > natural, trim the tail when
    duration < natural (tolerance 0.1 s). Every adjustment is reported in the
    job result AND the ledger row as `duration_adjustments` (omitted when no
    shot requested a duration). With upscale_each the adjustment happens
    BEFORE upscaling — the held/trimmed clip is upscaled in one pass.
    M5: in-flight progress (stage/completed/total/label) is exposed on
    GET /jobs/{id}; the terminal progress lands in the result + ledger row.
    """
    shots = payload["shots"]
    vo, music, sfx = payload.get("vo"), payload.get("music"), payload.get("sfx")
    width = int(payload.get("width", 1920))
    height = int(payload.get("height", 1080))
    fps = int(payload.get("fps", 24))
    text_overlays = payload.get("text_overlays", []) or []
    out_host = JOB_DIR / jid / "final.mp4"
    out = to_container(out_host)
    upscale_each = bool(payload.get("upscale_each", False))
    n_shots = len(shots)

    def _progress(stage, completed, label):
        """M5: in-memory in-flight progress (dies with the job; the terminal
        progress in the result/ledger row is the durable record)."""
        JOBS[jid]["progress"] = {"stage": stage, "completed": completed,
                                 "total": n_shots, "label": label}

    def _result():
        r = {"video": _apply_text_overlays(out_host, text_overlays, jid)}
        if adjustments:
            r["duration_adjustments"] = adjustments
        r["progress"] = {"stage": "done", "completed": n_shots, "total": n_shots}
        return r

    def resolve(p):
        p = Path(p)
        return p if p.is_absolute() else RUN_DIR / p

    # 1. Normalize each shot (scale/pad/fps) and apply in/out + M1 duration
    #    hold/trim. Runs BEFORE the optional per-shot upscale so the
    #    held/trimmed clip is upscaled in one pass (M1 rule 5).
    norm = []
    adjustments = []
    for i, s in enumerate(shots):
        spec = _shot_spec(s)
        sp_host = resolve(spec["path"])
        if not sp_host.exists():
            raise FileNotFoundError(f"shot not found: {sp_host}")
        sp = to_container_any(sp_host)
        n_host = JOB_DIR / jid / f"norm_{i:02d}.mp4"
        n = to_container(n_host)
        cmd = ["-y"]
        vf_hold = ""
        if not _is_video(sp_host):
            # Still image: no duration keeps today's behavior (1-frame ~0s clip);
            # explicit duration renders a pixel-static clip (M2 logic).
            if spec["duration"] is not None:
                cmd += ["-loop", "1", "-t", f"{float(spec['duration']):.3f}"]
            cmd += ["-i", sp]
        else:
            cmd += ["-i", sp]
            in_s = float(spec["in"]) if spec["in"] is not None else 0.0
            if spec["in"] is not None:
                cmd += ["-ss", f"{in_s:.3f}"]
            # M1: in/out define the source window first; `duration` then applies
            # to the resulting clip (natural length of that window).
            window_s = None
            if spec["out"] is not None:
                window_s = float(spec["out"]) - in_s
                if window_s <= 0:
                    raise ValueError(f"shot {i}: non-positive trim duration")
            if spec["duration"] is not None:
                req_s = float(spec["duration"])
                if req_s <= 0:
                    raise ValueError(f"shot {i}: non-positive duration")
                if window_s is None:
                    window_s = max(0.0, _probe_duration_s(sp_host) - in_s)
                if abs(window_s - req_s) <= DURATION_TOL_S:
                    applied, held_s, trimmed_s = "none", None, None
                elif req_s > window_s:
                    # Hold the last frame for the shortfall (the
                    # production-proven tpad=stop_mode=clone fix-forward).
                    applied, held_s, trimmed_s = "hold", req_s - window_s, None
                    if spec["out"] is not None:
                        cmd += ["-t", f"{window_s:.3f}"]
                    vf_hold = f",tpad=stop_mode=clone:stop_duration={held_s:.3f}"
                else:
                    applied, held_s, trimmed_s = "trim", None, window_s - req_s
                    cmd += ["-t", f"{req_s:.3f}"]
                adj = {"shot": i, "natural_s": round(window_s, 3),
                       "requested_s": round(req_s, 3), "applied": applied}
                if held_s is not None:
                    adj["held_s"] = round(held_s, 3)
                if trimmed_s is not None:
                    adj["trimmed_s"] = round(trimmed_s, 3)
                adjustments.append(adj)
            elif spec["out"] is not None:
                cmd += ["-t", f"{window_s:.3f}"]
        cmd += ["-vf",
                f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps}{vf_hold}",
                "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-pix_fmt", "yuv420p",
                "-an", n]
        run_ffmpeg(cmd, timeout=1800)
        norm.append(n)
        _progress("normalize" if upscale_each else "assemble", i + 1,
                  f"{'normalize ' if upscale_each else ''}shot {i + 1}/{n_shots}")

    # 2. Optionally B-upscale (SeedVR2) each normalized shot for 1080p-quality
    #    assembly — AFTER the M1 duration adjustment (one pass per clip).
    if upscale_each:
        res = int(payload.get("upscale_resolution", 1080))
        ns = float(payload.get("upscale_noise_scale", 0.0))
        ufps = int(payload.get("upscale_fps", fps))
        useed = int(payload.get("upscale_seed", 42))
        up = []
        for i, n in enumerate(norm):
            up.append(_upscale_shot(n, jid, i, res, ns, ufps, useed))
            _progress("upscale", i + 1, f"upscale shot {i + 1}/{n_shots}")
        norm = up

    _progress("concat", n_shots, "concat")
    concat_list_host = JOB_DIR / jid / "concat.txt"
    concat_list = to_container(concat_list_host)
    concat_list_host.write_text("".join(f"file '{n}'\n" for n in norm))
    video_only_host = JOB_DIR / jid / "video_only.mp4"
    video_only = to_container(video_only_host)
    # Films B2: optional grade (3D LUT + temporal grain) applied to the final
    # concat in the same re-encode pass (no extra generation pass).
    grade = _grade_filter(payload)
    concat_cmd = ["-y", "-f", "concat", "-safe", "0", "-i", concat_list]
    if grade:
        concat_cmd += ["-vf", grade]
    concat_cmd += ["-c:v", "libx264", "-preset", "fast", "-crf", "18", video_only]
    run_ffmpeg(concat_cmd, timeout=1800)

    _progress("finalize", n_shots, "finalize")
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
        return _result()

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
    return _result()


# ------------------------------------------------------------------ runner
FLOW_MAP = {
    "storyboard": flow_storyboard,
    "images": flow_images,
    "images_edit": flow_images_edit,
    "shots": flow_shots,
    "tts": flow_tts,
    "voices_add": flow_voices_add,
    "music": flow_music,
    "sfx": flow_sfx,
    "upscale": flow_upscale,
    "assemble": flow_assemble,
    "trim": flow_trim,
    "freeze": flow_freeze,
    "caption": flow_caption,
    "animatic": flow_animatic,
    "extract_frame": flow_extract_frame,
    "transcribe": flow_transcribe,
}



# ------------------------------------------------------------------ routes
@app.get("/health")
def health():
    now = time.time()
    with _qlock:
        running = list(_running)
        queued = [j for (j, *_r) in _queue]
        pending = _pending
        # M3: FIFO wait order (oldest first) with per-job wait age.
        # `running`/`queued` are lists of job IDs (not counts).
        queued_jobs = [
            {"job_id": j,
             "flow": JOBS.get(j, {}).get("flow"),
             "client": JOBS.get(j, {}).get("client"),
             "created": _iso(JOBS.get(j, {}).get("created")),
             "waited_s": round(now - JOBS[j]["created"], 1)
             if JOBS.get(j, {}).get("created") else None}
            for j in queued
        ]
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
        "queue": {
            "counts": {"running": len(running), "queued": len(queued)},
            "queued_jobs": queued_jobs,
            "oldest_queued": queued_jobs[0] if queued_jobs else None,
        },
        # M3: feature detection for clients.
        "features": ["assemble-duration-hold", "job-kill", "queue-summary",
                     "job-progress", "projects", "storyboard-pages",
                     "animatic", "extract-frame", "assemble-grade"],
        "retention_days": MEDIA_JOB_RETENTION_DAYS,
        "retention_last_run": RETENTION_STATE["last_run"],
        "retention_last_deleted": RETENTION_STATE["last_deleted"],
        "retention_last_bytes_freed": RETENTION_STATE["last_bytes_freed"],
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


# ------------------------------------------------------------------ job listing
# Durable ledger of finished jobs: the metering jobs.jsonl (one line per
# completed job; 10 MB cap with keep-newest rotation; never touched by the
# retention sweeper). The in-memory JOBS table is authoritative for in-flight
# jobs and this process's outputs; the ledger covers finished jobs across
# restarts. GET /jobs merges the two (contract: docs/matrix_media_pipeline_api.md).
JOBS_JSONL = JOB_DIR / "metrics" / "jobs.jsonl"
_LEDGER_MAX_LINES = 5000  # safety bound; the 10 MB cap holds well under this


def _iso(ts) -> str | None:
    """epoch float -> ISO-8601 UTC string (the jobs.jsonl ts format)."""
    if not ts:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(s: str) -> float | None:
    """ISO-8601 (Z suffix ok) -> epoch float, or None."""
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return None


def _read_jobs_ledger() -> list[dict]:
    """Parse the durable jobs.jsonl ledger (oldest first). Never raises."""
    recs: list[dict] = []
    if not JOBS_JSONL.is_file():
        return recs
    try:
        with open(JOBS_JSONL, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(r, dict) and r.get("job_id"):
                    recs.append(r)
    except OSError:
        return []
    if len(recs) > _LEDGER_MAX_LINES:
        recs = recs[-_LEDGER_MAX_LINES:]  # keep the newest
    return recs


def _job_outputs(jid: str) -> list[str] | None:
    """Run-dir-relative output files of a job dir (excluding input/), or None
    if the dir no longer exists (retention-swept)."""
    jdir = JOB_DIR / jid
    if not jdir.is_dir():
        return None
    outs: list[str] = []
    try:
        for p in sorted(jdir.rglob("*")):
            rel = p.relative_to(JOB_DIR)
            if p.is_file() and "input" not in rel.parts[:1]:
                outs.append(str(rel))
            if len(outs) >= 20:
                break
    except OSError:
        return None
    return outs or None


@app.get("/jobs")
def list_jobs(status: str = None, client: str = None, user: str = None,
              flow: str = None, since: str = None, limit: int = 50):
    """List jobs, newest first (sync, no job_id). Merges the durable
    jobs.jsonl ledger (finished jobs — survives restarts) with the in-memory
    registry (in-flight jobs + this process's outputs). Read-only.

    Filters (all optional, combinable):
      status  comma-separated subset of queued,running,done,error,timeout,killed
              (or 'all')
      client  exact match on the client identity (e.g. 'pi', 'siri')
      user    exact match on the user identity
      flow    exact match on the flow (e.g. 'shots', 'tts')
      since   ISO-8601; keep jobs created at/after this time (for ledger jobs
              created ≈ done − queue_wait − duration, i.e. approximate)
      limit   1..200 (default 50)

    Per-job fields: job_id, flow, status, client, user, created, done,
    queue_position (queued only), model, duration_s, cost_usd (ledger jobs),
    error, output (result dict, this process only), outputs (run-dir-relative
    files from disk; null when the job dir was retention-swept).
    """
    try:
        limit = max(1, min(int(limit or 50), 200))
    except (TypeError, ValueError):
        raise HTTPException(400, "limit must be an integer 1..200")
    statuses = {s.strip() for s in (status or "").lower().split(",") if s.strip()}
    if statuses - {"all", "queued", "running", "done", "error", "timeout", "killed"}:
        raise HTTPException(400, "status must be a comma-separated subset of "
                                 "queued,running,done,error,timeout,killed (or 'all')")
    if "all" in statuses:
        statuses = set()
    since_epoch = None
    if since:
        since_epoch = _parse_iso(since)
        if since_epoch is None:
            raise HTTPException(400, "since must be ISO-8601 (e.g. 2026-11-01T00:00:00Z)")

    merged: dict[str, dict] = {}
    # 1) finished jobs from the durable ledger (oldest first)
    for r in _read_jobs_ledger():
        jid = r["job_id"]
        done_epoch = _parse_iso(r.get("ts"))
        created = (done_epoch - (r.get("queue_wait_s") or 0) - (r.get("duration_s") or 0)
                   if done_epoch is not None else None)
        merged[jid] = {
            "job_id": jid, "flow": r.get("flow"), "status": r.get("status"),
            "client": r.get("client"), "user": r.get("user"),
            "created": _iso(created), "done": r.get("ts"),
            "model": r.get("model"), "duration_s": r.get("duration_s"),
            "cost_usd": r.get("cost_usd"),
            "error": r.get("error"), "output": None,
        }
    # 2) overlay the in-memory registry (authoritative for in-flight + outputs)
    for jid, j in list(JOBS.items()):
        st = j.get("status") or "queued"
        rec = merged.get(jid)
        if rec is not None:
            rec["status"] = st
            if j.get("created"):
                rec["created"] = _iso(j["created"])
            if j.get("finished"):
                rec["done"] = _iso(j["finished"])
            if j.get("error"):
                rec["error"] = str(j["error"])[:500]
            if j.get("output"):
                rec["output"] = j["output"]
        else:
            rec = {
                "job_id": jid, "flow": j.get("flow"), "status": st,
                "client": j.get("client"), "user": j.get("user"),
                "created": _iso(j.get("created")),
                "done": _iso(j.get("finished")),
                "model": None, "duration_s": None, "cost_usd": None,
                "error": str(j["error"])[:500] if j.get("error") else None,
                "output": j.get("output"),
            }
        if st == "queued":
            rec["queue_position"] = queue_position(jid)
        merged[jid] = rec

    # 3) filter
    rows = []
    for rec in merged.values():
        if statuses and rec["status"] not in statuses:
            continue
        if client and rec.get("client") != client:
            continue
        if user and rec.get("user") != user:
            continue
        if flow and rec.get("flow") != flow:
            continue
        if since_epoch is not None:
            c = _parse_iso(rec.get("created"))
            if c is None or c < since_epoch:
                continue
        rows.append(rec)

    # 4) newest first (missing created sorts last)
    rows.sort(key=lambda r: _parse_iso(r.get("created")) or 0.0, reverse=True)
    rows = rows[:limit]

    # 5) attach on-disk outputs (works across restarts; null when swept)
    for rec in rows:
        rec["outputs"] = _job_outputs(rec["job_id"])
    return {"count": len(rows), "jobs": rows}


@app.get("/jobs/{jid}")
def job_status(jid: str):
    if jid not in JOBS:
        raise HTTPException(404, {
            "error": "unknown_job",
            "message": f"job {jid} is not in the live registry",
            "hint": "it may have finished before this process started or its "
                    "dir been retention-swept; list recent jobs with "
                    "GET /jobs?since=<iso>&limit=100 (durable ledger)",
        })
    j = dict(JOBS[jid])
    if j.get("status") == "queued":
        j["queue_position"] = queue_position(jid)
    return j


# ------------------------------------------------------------------ M2: job kill
# Only QUEUED jobs can be killed (frees the FIFO slot so the next job starts).
# Running jobs are never killed (GPU work in progress) — 409. Terminal jobs
# (done/error/timeout/killed) are a 200 no-op. Client scoping is nominal
# (the pipeline is unauthenticated by design): optional ?client= tag check.
@app.delete("/jobs/{jid}")
def kill_job(jid: str, client: str = None):
    return _kill_job(jid, client)


@app.post("/jobs/{jid}/kill")
def kill_job_alias(jid: str, client: str = None):
    return _kill_job(jid, client)


def _kill_job(jid: str, client: str | None) -> dict:
    with _qcond:  # same lock as the worker pop — no race between kill and start
        job = JOBS.get(jid)
        if job is None:
            # Terminal jobs from before a restart live only in the ledger.
            for r in reversed(_read_jobs_ledger()):
                if r.get("job_id") == jid:
                    job = {"id": jid, "status": r.get("status"),
                           # "unknown" is the ledger's no-tag placeholder
                           "client": r.get("client")
                           if r.get("client") not in (None, "unknown") else None}
                    break
        if job is None:
            raise HTTPException(404, {
                "error": "unknown_job",
                "message": f"job {jid} not found",
                "hint": "list jobs with GET /jobs",
            })
        if client and job.get("client") and job["client"] != client:
            raise HTTPException(403, {
                "error": "client_mismatch",
                "message": f"job {jid} belongs to client {job['client']!r}, "
                           f"not {client!r}",
            })
        if jid in _running:
            raise HTTPException(409, {
                "error": "job is running (GPU work in progress); kill is queued-only",
                "status": "running",
            })
        if job.get("status") != "queued":
            # done/error/timeout/killed: nothing to kill (idempotent no-op).
            return {"job_id": jid, "killed": False, "status": job.get("status")}
        pos = next((i for i, (j, *_r) in enumerate(_queue) if j == jid), 0)
        if pos < len(_queue):
            _queue.pop(pos)
        job["status"] = "killed"
        job["killed"] = True
        job["finished"] = time.time()
    # Slot release + ledger write happen OUTSIDE the queue lock: release_slot
    # takes _qlock (a non-reentrant threading.Lock) — calling it while holding
    # _qcond would deadlock. The job was already popped from _queue above and
    # the worker's killed-guard skips it, so there is no double release.
    release_slot()
    logger.info("job killed: %s (queue position %d freed)", jid, pos)
    metering.record_job(job)  # durable ledger row (status=killed)
    return {"job_id": jid, "killed": True, "status": "killed",
            "queue_position_freed": pos}


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
                          seed: int = Form(42), steps: int = Form(None),
                          model: str = Form(None),
                          references: str = Form(None),
                          user: str = Form(None), client: str = Form(None)):
    # model: 'qwen21' (default) | 'legacy' (2511 GGUF + Lightning).
    # references: comma-separated; each = ComfyUI input/ filename OR a
    # media_jobs-relative path (qwen21 only; max 9 + the edit target).
    with job_slot():
        payload = {"prompt": prompt, "seed": seed, "steps": steps,
                   "model": model, "references": references,
                   "user": user, "client": client}
        jid = new_job("images_edit", payload)
        try:
            await save_upload(jid, file)
            copy_to_comfy_input(jid, file.filename)
        except Exception as e:  # M4: failed submission = error, not zombie "queued"
            set_status(jid, "error", error=f"submission failed: {type(e).__name__}: {e}")
            raise
        enqueue_job(jid, "images_edit", payload, {"image_name": file.filename})
    return {"job_id": jid}


@app.post("/shots")
async def api_shots(file: UploadFile = File(...), prompt: str = Form(...),
                    width: int = Form(None), height: int = Form(None),
                    frames: int = Form(None), fps: float = Form(None),
                    seed: int = Form(42), steps: int = Form(8),
                    strength: float = Form(0.7), model: str = Form(None),
                    end_keyframe: UploadFile = File(None),
                    project: str = Form(None), shot_id: str = Form(None),
                    user: str = Form(None), client: str = Form(None)):
    # model: 'ltxv2b' (fast, ~8 s) | 'wan14b' (Wan 2.2 14B quality, ~10 min,
    # 480p only — 720p failed the VRAM gate). Validate up front so a bad name
    # is a clean 400, not a job error. Omitted dims/frames/fps -> model-native
    # defaults (ltxv2b: 768x512/97f/25fps; wan14b: 832x480/81f/16fps).
    # steps/strength are LTXV-only (Wan sampling is fixed: 20+20 steps, cfg 5).
    # project/shot_id (Films B1): optional — register the finished take in the
    # project's takes.json + production/clips/. Absent = legacy behavior.
    # end_keyframe (Films B2): optional second keyframe -> FLF2V (wan14b only);
    # the clip interpolates from `file` to `end_keyframe` (exact end-pose).
    try:
        m = wf.resolve_shot_model({"model": model})
    except ValueError as e:
        raise HTTPException(400, str(e))
    if end_keyframe is not None and m == "ltxv2b":
        raise HTTPException(400, "end_keyframe is wan14b-only (FLF2V); omit it for ltxv2b")
    if project and not projects.exists(project):
        raise HTTPException(400, f"project not found: {project}")
    d = wf.SHOT_MODEL_DEFAULTS[m]
    w = int(width) if width is not None else d["width"]
    h = int(height) if height is not None else d["height"]
    f = int(frames) if frames is not None else d["frames"]
    r = float(fps) if fps is not None else d["fps"]
    if m == "wan14b":
        if (w, h) not in wf.WAN_RESOLUTIONS:
            raise HTTPException(400, f"wan14b supports {sorted(wf.WAN_RESOLUTIONS)} only")
        if (f - 1) % 4 != 0:
            raise HTTPException(400, "wan14b frames must be 4k+1 (e.g. 49, 81, 121)")
    # collision-free name for the end keyframe (the start keyframe keeps its
    # own filename; both land in ComfyUI input/ under distinct names).
    end_name = None
    if end_keyframe is not None:
        end_name = "end_" + Path(end_keyframe.filename or "end_keyframe.png").name
    with job_slot():
        payload = {"prompt": prompt, "model": m, "width": w, "height": h,
                   "frames": f, "fps": r, "seed": seed,
                   "steps": steps, "strength": strength,
                   "end_keyframe_name": end_name,
                   "project": project, "shot_id": shot_id,
                   "user": user, "client": client}
        jid = new_job("shots", payload)
        try:
            await save_upload(jid, file)
            copy_to_comfy_input(jid, file.filename)
            if end_name:
                data = await end_keyframe.read()
                (JOB_DIR / jid / "input" / end_name).write_bytes(data)
                copy_to_comfy_input(jid, end_name)
        except Exception as e:  # M4: failed submission = error, not zombie "queued"
            set_status(jid, "error", error=f"submission failed: {type(e).__name__}: {e}")
            raise
        enqueue_job(jid, "shots", payload, {"keyframe_name": file.filename})
    return {"job_id": jid}


@app.post("/tts")
async def api_tts(payload: dict):
    # Validate the voice up front so a bad name is a clean 400, not a job error.
    ref = str(payload.get("reference_audio") or payload.get("voice") or "trailer").strip()
    try:
        _resolve_voice(ref)
    except ValueError as e:
        raise HTTPException(400, str(e))
    with job_slot():
        jid = new_job("tts", payload)
        enqueue_job(jid, "tts", payload, {})
    return {"job_id": jid}


@app.post("/transcribe")
async def api_transcribe(payload: dict):
    """Song -> guitar tab + timestamped lyrics (job flow; spec:
    docs/transcribe_pipeline_spec.md). v1: instrument=guitar, tuning=standard."""
    instrument = str(payload.get("instrument", "guitar")).strip()
    if instrument != "guitar":
        raise HTTPException(400, "v1 supports instrument=guitar only")
    tuning = str(payload.get("tuning", "standard")).strip()
    if tuning != "standard":
        raise HTTPException(400, "v1 supports tuning=standard only")
    if not str(payload.get("path", "")).strip():
        raise HTTPException(400, "path (media_jobs file) is required")
    transpose = payload.get("transpose_semitones", 0)
    if isinstance(transpose, bool) or not isinstance(transpose, int) \
            or not (-12 <= transpose <= 12):
        raise HTTPException(400, "transpose_semitones must be an integer in -12..12")
    target_key = str(payload.get("target_key") or "").strip()
    if target_key and not re.match(r"^[A-G][#b]?m?$", target_key):
        raise HTTPException(400, "target_key must be a tonic like 'E', 'Bb', 'F#m'")
    if str(payload.get("chord_style", "as_detected")).strip().lower() \
            not in ("as_detected", "triads", "power"):
        raise HTTPException(400, "chord_style must be as_detected|triads|power")
    if not isinstance(payload.get("shape_hints", True), bool):
        raise HTTPException(400, "shape_hints must be a boolean")
    if not isinstance(payload.get("capo", False), bool):
        raise HTTPException(400, "capo must be a boolean")
    with job_slot():
        jid = new_job("transcribe", payload)
        enqueue_job(jid, "transcribe", payload, {})
    return {"job_id": jid}


# ------------------------------------------------------------------ TTS voice library
@app.get("/voices")
def api_voices():
    """List the TTS voice library (sync)."""
    return {"voices": _list_voices()}


@app.post("/voices")
async def api_voices_add(payload: dict):
    """Register a TTS voice (job). XTTS-v2 is zero-shot cloning: every voice
    is a 3-15s single-speaker reference clip. The job QC's the clip
    (duration + audio), normalizes it to 16k mono, installs it in the
    library, and generates a sample with the new voice.

    payload: {name, source, description?, gender?, style?, sample_text?,
              user?, client?}
      name     [a-z0-9_] 2-32 chars; trailer/default are protected
      source   absolute host path to the reference clip (e.g. a media_jobs
               file from POST /upload)
    output: {voice, ref, sample, ref_duration_s}
    """
    name = str(payload.get("name", "")).strip()
    if not VOICES_NAME_RE.match(name):
        raise HTTPException(400, f"invalid voice name {name!r} (use [a-z0-9_] 2-32 chars)")
    if name in VOICES_PROTECTED:
        raise HTTPException(400, f"voice {name!r} is protected")
    with job_slot():
        jid = new_job("voices_add", payload)
        enqueue_job(jid, "voices_add", payload, {})
    return {"job_id": jid}


@app.delete("/voices/{name}")
def api_voices_delete(name: str):
    """Remove a TTS voice (sync). trailer/default are protected."""
    if name in VOICES_PROTECTED:
        raise HTTPException(400, f"voice {name!r} is protected")
    m = _load_voices_manifest()
    if name not in m["voices"]:
        raise HTTPException(404, f"voice {name!r} not found")
    del m["voices"][name]
    _save_voices_manifest(m)
    for f in (f"{name}_ref.wav", f"{name}_sample.wav"):
        pth = VOICES_DIR / f
        if pth.is_file():
            pth.unlink()
    logger.info("voice deleted: %s", name)
    return {"deleted": name}


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
        try:
            await save_upload(jid, file)
        except Exception as e:  # M4: failed submission = error, not zombie "queued"
            set_status(jid, "error", error=f"submission failed: {type(e).__name__}: {e}")
            raise
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
        try:
            await save_upload(jid, file)
        except Exception as e:  # M4: failed submission = error, not zombie "queued"
            set_status(jid, "error", error=f"submission failed: {type(e).__name__}: {e}")
            raise
        video_rel = f"media_jobs/{jid}/input/{file.filename}"
        enqueue_job(jid, "upscale", payload, {"video_rel": video_rel})
    return {"job_id": jid}


@app.post("/assemble")
async def api_assemble(payload: dict):
    # Films B2: validate the optional grade (lut/grain) up front so a bad value
    # is a clean 400 at submit, not a job error. Absent = today's behavior.
    if payload.get("lut"):
        try:
            resolve_lut(str(payload["lut"]))
        except ValueError as e:
            raise HTTPException(400, str(e))
    if payload.get("grain") is not None:
        try:
            g = float(payload["grain"])
        except (TypeError, ValueError):
            raise HTTPException(400, "grain must be a number in [0.0, 1.0]")
        if not (0.0 <= g <= 1.0):
            raise HTTPException(400, "grain must be in [0.0, 1.0]")
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


# ------------------------------------------------------------------ Films B2: production tools
@app.post("/animatic")
async def api_animatic(payload: dict):
    # Validate the manifest up front so a bad move / shape is a clean 400 at
    # submit, not a job error (the flow re-validates as a backstop).
    manifest = payload.get("shots") or payload.get("manifest")
    if not isinstance(manifest, list) or not manifest:
        raise HTTPException(400, "shots: non-empty list of {image, duration, move} required")
    for i, shot in enumerate(manifest):
        if not isinstance(shot, dict) or not shot.get("image"):
            raise HTTPException(400, f"shots[{i}]: {{image, duration, move}} object required")
        move = str(shot.get("move", "static")).strip().lower()
        if move not in ANIMATIC_MOVES:
            raise HTTPException(400, f"shots[{i}].move: unknown move '{move}' "
                                     f"(valid: {', '.join(ANIMATIC_MOVES)})")
    with job_slot():
        jid = new_job("animatic", payload)
        enqueue_job(jid, "animatic", payload, {})
    return {"job_id": jid}


@app.post("/extract_frame")
async def api_extract_frame(payload: dict):
    if "source" not in payload:
        raise HTTPException(400, "source is required")
    frame = payload.get("frame")
    t = payload.get("time")
    positions = payload.get("positions")
    n = payload.get("n")
    if sum(x is not None for x in (frame, t, positions, n)) != 1:
        raise HTTPException(400, "provide exactly one of: frame (int), time (s), "
                                 "positions (list), n (int even-spacing)")
    with job_slot():
        jid = new_job("extract_frame", payload)
        enqueue_job(jid, "extract_frame", payload, {})
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


# ------------------------------------------------------------------ Films B1: project store
# The "project bible": one dir per film under RUN_DIR/media_projects (env
# MEDIA_PROJECTS_ROOT), mirrored append-only to the NAS (projects.nas_sync).
# media_projects/ is OUTSIDE media_jobs/ — the retention sweeper never touches
# it (projects are durable by design; see the API doc).
# All routes below are SYNC (fast CPU/ffmpeg ops, no job queue).

FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


def _project_register_shot(slug: str, shot_id: str, payload: dict, jid: str,
                            video_host: str) -> dict:
    """Copy a finished shot clip into production/clips/<shot_id>_<take>.mp4
    (takes are never overwritten) and append it to takes.json."""
    root = projects.project_dir(slug)
    tp = root / "takes.json"
    takes = json.loads(tp.read_text()) if tp.is_file() else {}
    n = len(takes.get(shot_id, [])) + 1
    dest = root / "production" / "clips" / f"{shot_id}_{n:02d}.mp4"
    shutil.copy2(video_host, dest)
    entry = projects.register_take(slug, shot_id, {
        "job_id": jid, "seed": payload.get("seed"), "prompt": payload.get("prompt"),
        "model": payload.get("model"), "path": str(dest),
        "source_job_path": str(video_host)})
    return entry[shot_id][-1]


def _render_storyboard_pages(slug: str, per_page: int, page_size: str) -> dict:
    """Render labeled grid pages from the saved shotplan + existing keyframes
    (ffmpeg-only, no GPU; missing keyframes -> placeholder cell). Deterministic
    ordering = shotplan order. Output: storyboard/pages/page_NN.png."""
    meta = projects.load(slug)
    sp = projects.load_shotplan(slug)
    shots = projects.micro_shots(sp)
    if not shots:
        raise ValueError("shotplan has no micro_shots")
    pw, ph = (int(x) for x in page_size.lower().split("x"))
    cols = math.ceil(math.sqrt(per_page))
    n_pages = math.ceil(len(shots) / per_page)
    root = projects.project_dir(slug)
    caps_dir = projects.PROJECTS_ROOT / ".caps" / f"{slug}_{_ts()}"
    caps_dir.mkdir(parents=True, exist_ok=True)
    fs = max(12, int(ph * 0.06))
    pages = []
    try:
        for pi in range(n_pages):
            cells = shots[pi * per_page:(pi + 1) * per_page]
            n_cells = len(cells)
            rows_pg = math.ceil(n_cells / cols)
            inputs: list[str] = []
            fexprs: list[str] = []
            n_in = 0
            for ci, ms in enumerate(cells):
                sid = str(ms.get("shot_id", f"cell_{ci}"))
                kf = root / "storyboard" / "keyframes" / f"{sid}.png"
                cap = (f"{sid} · {ms.get('shot_type', '?')} · "
                       f"{ms.get('duration_frames', '?')}f · "
                       f"{ms.get('animatic_move', '?')} · risk:{ms.get('risk_level', '?')}")
                cap_f = caps_dir / f"cap_{pi}_{ci}.txt"
                cap_f.write_text(cap)
                idx = n_in
                n_in += 1
                if kf.is_file():
                    inputs += ["-i", to_container(str(kf))]
                else:
                    inputs += ["-f", "lavfi", "-t", "1",
                               "-i", f"color=c=0x2b2b2b:s={pw}x{ph}"]
                f = (f"[{idx}:v]scale={pw}:{ph}:force_original_aspect_ratio=decrease,"
                     f"pad={pw}:{ph}:(ow-iw)/2:(oh-ih)/2:color=black")
                if not kf.is_file():
                    nk_f = caps_dir / f"nk_{pi}_{ci}.txt"
                    nk_f.write_text("no keyframe")
                    f += (f",drawtext=fontfile={FONT_BOLD}:textfile={to_container(nk_f)}:"
                          f"fontsize={int(fs * 1.4)}:fontcolor=0x999999:"
                          f"x=(w-text_w)/2:y=(h-text_h)/2")
                f += (f",drawtext=fontfile={FONT_BOLD}:textfile={to_container(cap_f)}:"
                      f"fontsize={fs}:fontcolor=white:box=1:boxcolor=black@0.6:boxborderw=6:"
                      f"x=(w-text_w)/2:y=h-th-8[c{ci}]")
                fexprs.append(f)
            # grid: rows of hstack, then vstack (each needs >=2 inputs;
            # single-cell rows / single-row pages pass the label through)
            row_exprs = []
            for ri in range(rows_pg):
                row_cells = list(range(ri * cols, min((ri + 1) * cols, n_cells)))
                if len(row_cells) == 1:
                    row_exprs.append(f"[c{row_cells[0]}]null[r{ri}]")
                else:
                    row_exprs.append(
                        f"{''.join(f'[c{c}]' for c in row_cells)}"
                        f"hstack=inputs={len(row_cells)}[r{ri}]")
            fexprs += row_exprs
            if rows_pg == 1:
                fexprs.append("[r0]null[g]")
            else:
                fexprs.append(f"{''.join(f'[r{r}]' for r in range(rows_pg))}vstack=inputs={rows_pg}[g]")
            footer_f = caps_dir / f"footer_{pi}.txt"
            footer_f.write_text(f"{meta.get('title', slug)} — page {pi + 1}/{n_pages}")
            fexprs.append(
                f"[g]pad=iw:ih+28:0:0:color=black,"
                f"drawtext=fontfile={FONT_BOLD}:textfile={to_container(footer_f)}:"
                f"fontsize=18:fontcolor=white:x=(w-text_w)/2:y=h-24[out]")
            out_host = root / "storyboard" / "pages" / f"page_{pi + 1:02d}.png"
            run_ffmpeg(["-y", *inputs, "-filter_complex", ";".join(fexprs),
                        "-map", "[out]", "-frames:v", "1", str(to_container(out_host))], timeout=900)
            pages.append(str(out_host))
        return {"pages": pages, "n_shots": len(shots), "n_pages": n_pages,
                "page_size": f"{pw}x{ph}", "per_page": per_page}
    finally:
        shutil.rmtree(caps_dir, ignore_errors=True)


@app.post("/projects")
def api_projects_create(payload: dict):
    """Create a film project (the "project bible" dir + project.json)."""
    slug = str(payload.get("slug", "")).strip()
    title = str(payload.get("title", "")).strip() or slug
    if not projects.SLUG_RE.match(slug):
        raise HTTPException(400, "slug must match [a-z0-9_] (1-64 chars)")
    try:
        meta = projects.create(slug, title)
    except FileExistsError:
        raise HTTPException(409, f"project exists: {slug}")
    projects.nas_sync(slug)  # initial mirror (best-effort; sets pending on failure)
    return {"slug": slug, "project": str(projects.project_dir(slug)),
            "status": meta["status"], "nas_sync": meta["nas_sync"]}


@app.get("/projects")
def api_projects_list():
    """List projects: slug + status + updated + NAS pending flag."""
    return {"projects": projects.list_projects(),
            "root": str(projects.PROJECTS_ROOT),
            "nas_root": str(projects.NAS_ROOT)}


@app.get("/projects/{slug}")
def api_projects_get(slug: str):
    """Manifest (project.json) + tree summary + takes + nas_sync state."""
    try:
        meta = projects.load(slug)
    except FileNotFoundError:
        raise HTTPException(404, f"project not found: {slug}")
    root = projects.project_dir(slug)
    tree = {}
    for top in ("bible", "storyboard", "production"):
        d = root / top
        n = sum(1 for p in d.rglob("*") if p.is_file()) if d.is_dir() else 0
        tree[top] = n
    tp = root / "takes.json"
    takes = json.loads(tp.read_text()) if tp.is_file() else {}
    return {**meta, "tree": tree, "takes": takes}


@app.post("/projects/{slug}/assets")
async def api_projects_assets(slug: str, path: str = Form(...),
                              file: UploadFile = File(...)):
    """Register an asset (char sheet / env plate / keyframe / voice ref).
    `path` = project-relative destination (e.g. bible/characters/hero/turnaround.png
    or storyboard/keyframes/s01.png)."""
    if not projects.exists(slug):
        raise HTTPException(404, f"project not found: {slug}")
    rel = str(path).strip().lstrip("/")
    if not rel or ".." in Path(rel).parts:
        raise HTTPException(400, "path must be project-relative (no '..')")
    proj_root = projects.project_dir(slug).resolve()
    dest = (proj_root / rel).resolve()
    if not str(dest).startswith(str(proj_root) + "/"):
        raise HTTPException(400, "path must stay inside the project dir")
    data = await file.read()
    cap = UPLOAD_MAX_MB * 1024 * 1024
    if len(data) > cap:
        raise HTTPException(413, f"file exceeds {UPLOAD_MAX_MB}MB cap")
    dest.parent.mkdir(parents=True, exist_ok=True)
    projects._open_up(dest.parent)  # comfy uid must traverse/write project dirs
    dest.write_bytes(data)
    projects.register_asset(slug, rel, len(data))
    return {"path": str(dest), "size": len(data)}


@app.post("/projects/{slug}/shotplan")
def api_projects_shotplan_save(slug: str, payload: dict):
    """Save/replace the two-tier shotplan (validated; 400 with the exact field
    list on failure). Accepts {shotplan: {...}} or a bare shotplan object."""
    if not projects.exists(slug):
        raise HTTPException(404, f"project not found: {slug}")
    sp = payload.get("shotplan") if isinstance(payload, dict) and "shotplan" in payload else payload
    try:
        return projects.save_shotplan(slug, sp)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/projects/{slug}/shotplan")
def api_projects_shotplan_get(slug: str):
    """Fetch the saved shotplan (incl. schema_warnings + saved ts)."""
    try:
        return projects.load_shotplan(slug)
    except FileNotFoundError:
        raise HTTPException(404, f"no shotplan saved for project {slug}")


@app.post("/projects/{slug}/storyboard_pages")
def api_projects_storyboard_pages(slug: str, payload: dict):
    """Render labeled grid pages from the shotplan + existing keyframes
    (ffmpeg-only; missing keyframes -> placeholder cell). Sync, not a job."""
    if not projects.exists(slug):
        raise HTTPException(404, f"project not found: {slug}")
    try:
        per_page = int(payload.get("per_page", 6))
        page_size = str(payload.get("page_size", "640x360"))
        if not (1 <= per_page <= 24):
            raise ValueError("per_page must be 1..24")
        m = re.match(r"^(\d{2,4})x(\d{2,4})$", page_size.lower())
        if not m or not (64 <= int(m.group(1)) <= 2048 and 64 <= int(m.group(2)) <= 2048):
            raise ValueError("page_size must be WxH (64..2048 each), e.g. 640x360")
    except (TypeError, ValueError) as e:
        raise HTTPException(400, str(e))
    try:
        return _render_storyboard_pages(slug, per_page, page_size)
    except (FileNotFoundError, ValueError) as e:
        raise HTTPException(400, str(e))


@app.post("/projects/{slug}/approve")
def api_projects_approve(slug: str, payload: dict):
    """Set the storyboard approval gate (status -> storyboard_approved;
    idempotent). The anti-suck-atude gate: no draft video before this."""
    if not projects.exists(slug):
        raise HTTPException(404, f"project not found: {slug}")
    what = str(payload.get("what", "")).strip()
    if not what:
        raise HTTPException(400, "what is required (v1: 'storyboard')")
    try:
        meta = projects.approve(slug, what, payload.get("note"), payload.get("by"))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"slug": slug, "status": meta["status"], "approval": meta["approval"]}


@app.post("/projects/{slug}/sync")
def api_projects_sync(slug: str):
    """Force the NAS rsync now; returns synced file count + ok."""
    if not projects.exists(slug):
        raise HTTPException(404, f"project not found: {slug}")
    return projects.nas_sync(slug, force=True)


if __name__ == "__main__":
    _seed_voices_manifest()  # idempotent; creates the library manifest on first boot
    stage_luts()  # idempotent; stages bundled LUTs into RUN_DIR/luts/ (Films B2)
    # Start the media_jobs retention sweeper (hourly; see MEDIA_JOB_RETENTION_DAYS).
    threading.Thread(target=_retention_loop, name="retention", daemon=True).start()
    # Bind host is configurable so it can run on the host loopback (127.0.0.1)
    # or on all interfaces (0.0.0.0) for LAN/remote MCP access. Default 0.0.0.0
    # so the remote media-mcp client can reach <gpu-host>:8189 (LAN-only, like
    # ComfyUI — do NOT expose publicly).
    _host = os.environ.get("PIPELINE_HOST", "0.0.0.0")
    _port = int(os.environ.get("PIPELINE_PORT", "8189"))
    uvicorn.run(app, host=_host, port=_port, log_level="info")