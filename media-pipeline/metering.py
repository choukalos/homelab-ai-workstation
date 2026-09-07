"""Media-work metering — attributes per-job GPU work to user/client as work units + $.

Design (contract: docs/matrix_media_pipeline_api.md §5):
  * Work units: steps×MP (images), frames×MP (video), output audio seconds
    (tts/music/sfx). Output artifacts are measured (ffprobe / image headers)
    where the payload doesn't carry the values.
  * Storyboard: real vLLM tokens (from the API response `usage`), priced at the
    live LiteLLM matrix-coder nominal rate.
  * Cost = work_units × rate(kind) — full-cost rates (electricity + GPU
    amortization), calibrated per spec §5.
  * Emission: Prometheus /metrics + durable jobs.jsonl. Everything here is
    best-effort: a metering failure must NEVER break job completion.

Config (env — loaded by server.load_dotenv from /home/chuck/homelab/.env):
  MEDIA_METRICS_ENABLED        kill switch (default true)
  MEDIA_PRICE_MPPIX_STEP_USD   $ per (step × megapixel)
  MEDIA_PRICE_MPPIX_FRAME_USD  $ per (frame × megapixel)
  MEDIA_PRICE_AUDIO_SEC_USD    $ per output audio second
  MEDIA_MATRIX_CODER_IN_USD    live thor LiteLLM rate, $/input token
  MEDIA_MATRIX_CODER_OUT_USD   live thor LiteLLM rate, $/output token
"""
import json
import os
import struct
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path

from prometheus_client import (CONTENT_TYPE_LATEST, Counter, Gauge, Histogram,
                               generate_latest)

# ------------------------------------------------------------------ config
def _env_bool(k: str, d: bool) -> bool:
    return os.environ.get(k, str(d)).strip().lower() in ("1", "true", "yes", "on")

def _env_f(k: str, d: float) -> float:
    try:
        return float(os.environ.get(k, str(d)))
    except (TypeError, ValueError):
        return d

ENABLED = _env_bool("MEDIA_METRICS_ENABLED", True)
PRICE_MPPIX_STEP = _env_f("MEDIA_PRICE_MPPIX_STEP_USD", 0.001)
PRICE_MPPIX_FRAME = _env_f("MEDIA_PRICE_MPPIX_FRAME_USD", 0.001)
PRICE_AUDIO_SEC = _env_f("MEDIA_PRICE_AUDIO_SEC_USD", 0.0005)
CODER_IN = _env_f("MEDIA_MATRIX_CODER_IN_USD", 0.00000075)
CODER_OUT = _env_f("MEDIA_MATRIX_CODER_OUT_USD", 0.0000045)

JOB_DIR = Path("/home/chuck/data/comfyui/run/media_jobs")
METRICS_DIR = JOB_DIR / "metrics"
JSONL_PATH = METRICS_DIR / "jobs.jsonl"
JSONL_MAX_BYTES = 10 * 1024 * 1024
JSONL_KEEP_BYTES = 5 * 1024 * 1024

CONTAINER = "comfyui_backend"
RUN_DIR = "/home/chuck/data/comfyui/run"
MNT = "/comfy/mnt"  # RUN_DIR as seen from inside the container

MODEL_BY_FLOW = {
    "storyboard": "qwen38-27b",
    "images": "qwen-image-2512",
    "images_edit": "qwen-image-edit-2511",
    "shots": "ltxv-2b-0.9.6-distilled",
    "tts": "xtts-v2",
    "music": "ace-step-1.5",
    "sfx": "mmaudio-large-44k-v2",
    "assemble": "ffmpeg",
    "trim": "ffmpeg",
    "freeze": "ffmpeg",
    "caption": "ffmpeg",
}
KIND_RATE = {
    "mpix_steps": PRICE_MPPIX_STEP,
    "mpix_frames": PRICE_MPPIX_FRAME,
    "audio_seconds": PRICE_AUDIO_SEC,
}

# ------------------------------------------------------------------ metrics
Buckets = (1, 5, 15, 30, 60, 120, 300, 600, 1800, 3600, 7200)
JOBS_TOTAL = Counter("media_jobs_total", "Media jobs finished",
                     ["user", "client", "stage", "status"])
JOB_DURATION = Histogram("media_job_duration_seconds",
                         "Media job execution duration (queue-pop -> completion)",
                         ["user", "stage"], buckets=Buckets)
TOKENS_TOTAL = Counter("media_tokens_total", "Real LLM tokens (storyboard only)",
                       ["user", "stage", "kind"])
COST_TOTAL = Counter("media_cost_usd_total", "Approx $ attributed to media jobs",
                     ["user", "stage"])
WORK_UNITS_TOTAL = Counter("media_work_units_total", "Media work units",
                           ["user", "stage", "kind"])
QUEUE_DEPTH = Gauge("media_queue_depth", "Jobs waiting in queue")
JOBS_ACTIVE = Gauge("media_jobs_active", "Jobs currently running")
MEDIA_UP = Gauge("media_up", "Service up")
MEDIA_UP.set(1)

_lock = threading.Lock()


# ------------------------------------------------------------------ measurement
def _cpath(p) -> str:
    """Host path under RUN_DIR -> equivalent container path (for docker exec)."""
    p = str(p)
    return MNT + p[len(RUN_DIR):] if p.startswith(RUN_DIR) else p


def _ffprobe_media(path) -> dict:
    """{duration, width, height} of a media file via ffprobe in the comfyui
    container (ffprobe is not installed on the host)."""
    cmd = ["docker", "exec", "-u", "comfy", CONTAINER, "ffprobe", "-v", "error",
           "-show_entries", "format=duration:stream=width,height", "-of", "json",
           _cpath(path)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {r.stderr[-300:]}")
    d = json.loads(r.stdout or "{}")
    dur = float((d.get("format") or {}).get("duration") or 0)
    w = h = None
    for s in d.get("streams") or []:
        if s.get("width") and s.get("height"):
            w, h = int(s["width"]), int(s["height"])
            break
    return {"duration": dur, "width": w, "height": h}


def _image_dimensions(path) -> tuple[int, int]:
    """(w, h) from a PNG/JPEG header — pure python, no subprocess."""
    p = Path(path)
    with open(p, "rb") as f:
        head = f.read(32)
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return struct.unpack(">II", head[16:24])
    if head[:2] == b"\xff\xd8":  # JPEG: scan segments for a SOF marker
        with open(p, "rb") as f:
            f.read(2)  # SOI
            while True:
                b = f.read(1)
                if not b:
                    raise ValueError("truncated jpeg")
                while b == b"\xff":  # skip filler bytes
                    b2 = f.read(1)
                    if not b2:
                        raise ValueError("truncated jpeg")
                    b = b2
                if b in (b"\xc0", b"\xc1", b"\xc2", b"\xc3"):  # SOF0/1/2/3
                    f.read(3)  # segment length (2) + precision (1)
                    h, w = struct.unpack(">HH", f.read(4))
                    return w, h
                if b in (b"\xd8", b"\xd9"):  # markers without payload
                    continue
                ln = f.read(2)
                if len(ln) < 2:
                    raise ValueError("truncated jpeg")
                n = struct.unpack(">H", ln)[0]
                if n < 2:
                    raise ValueError("bad jpeg segment")
                f.seek(n - 2, 1)
    raise ValueError(f"unsupported image format: {p.name}")


def _mp(w: int, h: int) -> float:
    return (w * h) / 1e6


# ------------------------------------------------------------------ work units
def _work_units(jid: str, flow: str, payload: dict, output: dict):
    """(units, kind) for the job's GPU work; (None, None) if not applicable or
    not measurable. Best-effort: any failure -> (None, None)."""
    try:
        if flow == "images":
            steps = int(payload.get("steps", 4))
            return (steps * _mp(int(payload.get("width", 1280)),
                                int(payload.get("height", 720))), "mpix_steps")
        if flow == "images_edit":
            steps = int(payload.get("steps", 8))
            w, h = _image_dimensions(output["image"])
            return steps * _mp(w, h), "mpix_steps"
        if flow == "shots":
            frames = int(payload.get("frames", 97))
            return (frames * _mp(int(payload.get("width", 768)),
                                 int(payload.get("height", 512))), "mpix_frames")
        if flow == "upscale":
            m = _ffprobe_media(output["video"])
            if not (m["width"] and m["height"]):
                raise ValueError("no video dimensions")
            frames = int((m["duration"] or 0) * int(payload.get("fps", 24)))
            return frames * _mp(m["width"], m["height"]), "mpix_frames"
        if flow in ("tts", "music", "sfx"):
            m = _ffprobe_media(output["audio"])
            if not m["duration"]:
                raise ValueError("no audio duration")
            return m["duration"], "audio_seconds"
        if flow == "assemble" and payload.get("upscale_each"):
            # GPU work = the per-shot SeedVR2 upscases; sum frames×MP over them.
            total = 0.0
            fps = int(payload.get("upscale_fps", payload.get("fps", 24)))
            for shot in sorted((JOB_DIR / jid).glob(f"mp_{jid}_up*.mp4")):
                m = _ffprobe_media(shot)
                if m["width"] and m["height"]:
                    total += int((m["duration"] or 0) * fps) * _mp(m["width"], m["height"])
            return (total, "mpix_frames") if total else (None, None)
    except Exception:
        return None, None
    return None, None


def _cost(flow: str, units, kind, usage: dict | None) -> float:
    if flow == "storyboard":
        u = usage or {}
        return (u.get("prompt_tokens") or 0) * CODER_IN + \
               (u.get("completion_tokens") or 0) * CODER_OUT
    if units is None:
        return 0.0
    return units * KIND_RATE.get(kind, 0.0)


# ------------------------------------------------------------------ jobs.jsonl
def _rotate_jsonl() -> None:
    """Keep the newest ~JSONL_KEEP_BYTES (drop oldest lines)."""
    try:
        with open(JSONL_PATH, "rb") as f:
            data = f.read()
        cut = data.rfind(b"\n", 0, max(0, len(data) - JSONL_KEEP_BYTES))
        if cut < 0:
            cut = len(data) - JSONL_KEEP_BYTES
        with open(JSONL_PATH, "wb") as f:
            f.write(data[cut:].lstrip(b"\n"))
    except Exception:
        pass


def _append_jsonl(rec: dict) -> None:
    try:
        METRICS_DIR.mkdir(parents=True, exist_ok=True)
        if JSONL_PATH.exists() and JSONL_PATH.stat().st_size > JSONL_MAX_BYTES:
            _rotate_jsonl()
        with open(JSONL_PATH, "a") as f:
            f.write(json.dumps(rec, separators=(",", ":")) + "\n")
    except Exception:
        pass  # metering must never break a job


# ------------------------------------------------------------------ emission
def record_job(job: dict) -> None:
    """Completion hook: emit metrics + a JSONL line for a finished job.

    Called from the worker's finally block with the job record in its final
    state (status done|error|timeout). Never raises.
    """
    if not ENABLED:
        return
    try:
        jid = job["id"]
        flow = job["flow"]
        user = job.get("user") or "unknown"
        client = job.get("client") or "unknown"
        status = job.get("status") or "error"
        payload = job.get("payload") or {}
        output = job.get("output") or {}
        started, finished = job.get("started"), job.get("finished")
        duration = (finished - started) if (started and finished) else None
        queue_wait = (started - job["created"]) if (started and job.get("created")) else None

        usage = output.get("usage") if flow == "storyboard" else None
        units, kind = _work_units(jid, flow, payload, output)
        cost = _cost(flow, units, kind, usage)
        model = MODEL_BY_FLOW.get(flow)
        if flow == "upscale":
            model = "seedvr2-3b" if payload.get("pipeline", "b") == "b" else "4xultrasharp"

        with _lock:
            JOBS_TOTAL.labels(user=user, client=client, stage=flow,
                              status=status).inc()
            if duration is not None:
                JOB_DURATION.labels(user=user, stage=flow).observe(duration)
            if usage:
                if usage.get("prompt_tokens"):
                    TOKENS_TOTAL.labels(user=user, stage=flow, kind="prompt") \
                        .inc(usage["prompt_tokens"])
                if usage.get("completion_tokens"):
                    TOKENS_TOTAL.labels(user=user, stage=flow, kind="completion") \
                        .inc(usage["completion_tokens"])
            COST_TOTAL.labels(user=user, stage=flow).inc(cost)
            if units is not None:
                WORK_UNITS_TOTAL.labels(user=user, stage=flow, kind=kind).inc(units)

        rec = {
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "job_id": jid, "flow": flow, "user": user, "client": client,
            "status": status,
            "queue_wait_s": round(queue_wait, 2) if queue_wait is not None else None,
            "duration_s": round(duration, 2) if duration is not None else None,
            "work_units": round(units, 4) if units is not None else None,
            "work_unit_kind": kind,
            "tokens": (usage or {}).get("total_tokens"),
            "tokens_prompt": (usage or {}).get("prompt_tokens"),
            "tokens_completion": (usage or {}).get("completion_tokens"),
            "cost_usd": round(cost, 6),
            "model": model,
            "params": {k: v for k, v in payload.items()
                       if k not in ("file_bytes", "user", "client")},
        }
        if job.get("error"):
            rec["error"] = str(job["error"])[:500]
        _append_jsonl(rec)
    except Exception:
        pass  # metering must never break a job


def update_gauges(queue_depth: int, active: int) -> None:
    QUEUE_DEPTH.set(queue_depth)
    JOBS_ACTIVE.set(active)


def render() -> bytes:
    return generate_latest()