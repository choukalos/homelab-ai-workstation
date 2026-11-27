#!/usr/bin/env python3
"""Films B2 FLF2V QA — GPU (wan14b first-last-frame). Run on the GPU host as
chuck:

    python3 qa_films_b2_flf2v.py

This is the GPU-heavy B2 part:
  1. VRAM gate: 480p start+end peak vs the i2v start-only precedent; 720p
     FLF2V OOM check (if it OOMs, FLF2V is restricted to 480p + documented).
  2. Functional: same start frame + seed, with/without end_keyframe -> both
     render; the FLF2V clip's last frame lands closer to the END keyframe than
     the start (the exact end-pose control).
  3. Metering/ledger unchanged (the FLF2V job is metered like a wan14b shot).

GPU work is serial (the /shots flow holds GPU_LOCK). ~75 min total (two 480p
renders ~10 min each + one 720p render ~55 min). Run in the background.
"""
import json
import math
import subprocess
import sys
import threading
import time
from pathlib import Path

BASE = "http://192.168.4.55:8189"
RUN = Path("/home/chuck/data/comfyui/run")
SCRATCH = RUN / "media_jobs" / "qa_b2_flf2v"
CONTAINER = "comfyui_backend"
GPU = 1  # dedicated ComfyUI card (RTX 4000 24 GB)
SEED = 42
RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  [{detail}]" if detail and not ok else ""),
          flush=True)


def req(method, path, body=None):
    cmd = ["curl", "-s", "-m", "600", "-w", "\\n%{http_code}", "-X", method, f"{BASE}{path}"]
    if body is not None:
        cmd += ["-H", "Content-Type: application/json", "-d", json.dumps(body)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=660)
    out = r.stdout.strip()
    body_s, _, code = out.rpartition("\n")
    try:
        return int(code), json.loads(body_s) if body_s else {}
    except Exception:
        return (int(code) if code.isdigit() else 0), {"raw": body_s}


def cpath(p):
    s = str(p)
    return "/comfy/mnt" + s[len(str(RUN)):] if s.startswith(str(RUN)) else s


def root_exec(cmd):
    r = subprocess.run(["docker", "exec", "-u", "root", "media_pipeline", "bash", "-c", cmd],
                       capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise RuntimeError(f"root exec failed: {r.stderr[-300:]}")
    return r


def ffmpeg(args, timeout=600):
    r = subprocess.run(["docker", "exec", "-u", "comfy", CONTAINER, "ffmpeg"] + args,
                       capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {r.stderr[-600:]}")
    return r


def submit_shots(start, prompt, seed, w, h, frames, fps, end=None):
    """multipart /shots submit; returns (status, body)."""
    cmd = ["curl", "-s", "-m", "120", "-w", "\\n%{http_code}", "-X", "POST",
           f"{BASE}/shots", "-F", f"file=@{start}", "-F", "prompt=" + prompt,
           "-F", f"model=wan14b", "-F", f"seed={seed}", "-F", f"width={w}",
           "-F", f"height={h}", "-F", f"frames={frames}", "-F", f"fps={fps}"]
    if end is not None:
        cmd += ["-F", f"end_keyframe=@{end}"]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    out = r.stdout.strip()
    body_s, _, code = out.rpartition("\n")
    try:
        return int(code), json.loads(body_s) if body_s else {}
    except Exception:
        return (int(code) if code.isdigit() else 0), {"raw": body_s}


def wait_job(jid, timeout=7200):
    t0 = time.time()
    while time.time() - t0 < timeout:
        st, r = req("GET", f"/jobs/{jid}")
        if st == 200 and r.get("status") in ("done", "error", "timeout", "killed"):
            return r
        time.sleep(10)
    raise TimeoutError(f"job {jid} timed out")


def vram_sampler(gpu, samples, stop):
    p = subprocess.Popen(["nvidia-smi", "-i", str(gpu), "--query-gpu=memory.used",
                          "--format=csv,noheader,nounits", "-l", "1"],
                         stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    try:
        for line in p.stdout:
            if stop.is_set():
                break
            line = line.strip()
            if line.isdigit():
                samples.append(int(line))
    finally:
        stop.set()
        p.terminate()


def run_shots(tag, start, prompt, seed, w, h, frames, fps, end=None):
    """Submit a /shots job with concurrent VRAM sampling; return (job, peak_delta)."""
    samples, stop = [], threading.Event()
    t = threading.Thread(target=vram_sampler, args=(GPU, samples, stop), daemon=True)
    t.start()
    time.sleep(4)
    baseline = samples[-1] if samples else 0
    print(f"[{tag}] baseline_vram={baseline} MiB", flush=True)
    st, r = submit_shots(start, prompt, seed, w, h, frames, fps, end)
    if st != 200 or "job_id" not in r:
        stop.set()
        raise RuntimeError(f"submit failed: {st} {r}")
    jid = r["job_id"]
    print(f"[{tag}] job_id={jid}", flush=True)
    jr = wait_job(jid)
    stop.set()
    peak = max(samples) if samples else 0
    peak_delta = peak - baseline
    wall = jr.get("duration_s")
    print(f"[{tag}] status={jr.get('status')} peak_vram={peak} MiB "
          f"peak_delta={peak_delta} MiB wall={wall}s error={jr.get('error')}", flush=True)
    return jr, peak_delta


def raw_rgb(path, w, h):
    r = subprocess.run(["docker", "exec", "-u", "comfy", CONTAINER, "ffmpeg", "-y",
                        "-i", cpath(path), "-vf", f"scale={w}:{h}",
                        "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                       capture_output=True, timeout=120)
    return r.stdout


def mse(a, b):
    n = min(len(a), len(b))
    if n == 0:
        return float("inf")
    s = 0
    for i in range(0, n, 3):  # sample every pixel (step 3 bytes = 1 px)
        s += (a[i] - b[i]) ** 2 + (a[i + 1] - b[i + 1]) ** 2 + (a[i + 2] - b[i + 2]) ** 2
    return s / (n / 3)


def last_frame(vid, out):
    # last frame via reverse seek
    ffmpeg(["-y", "-sseof", "-0.1", "-i", cpath(vid), "-frames:v", "1",
            "-update", "1", cpath(out)])
    return out


def main():
    root_exec(f"mkdir -p {SCRATCH} && chmod 777 {SCRATCH}")
    W, H = 832, 480
    # distinct start/end keyframes (left-red vs right-blue on dark)
    start = SCRATCH / "start.png"
    end = SCRATCH / "end.png"
    ffmpeg(["-y", "-f", "lavfi", "-i", f"color=c=0x222222:s={W}x{H}",
            "-vf", "drawbox=x=0:y=0:w=416:h=480:color=0xcc3333:t=fill",
            "-frames:v", "1", cpath(start)])
    ffmpeg(["-y", "-f", "lavfi", "-i", f"color=c=0x222222:s={W}x{H}",
            "-vf", "drawbox=x=416:y=0:w=416:h=480:color=0x3333cc:t=fill",
            "-frames:v", "1", cpath(end)])
    print(f"== Films B2 FLF2V QA — scratch {SCRATCH} (GPU {GPU}, seed {SEED})")

    PROMPT = ("A slow camera drift across a minimal scene: a warm red panel on the "
              "left gradually gives way to a cool blue panel on the right, smooth "
              "continuous motion, static background, no text")

    # ---- 480p FLF2V (start+end) — VRAM gate + functional ----
    print("-- 480p FLF2V (start+end)")
    jr_flf, flf_delta = run_shots("flf2v480", start, PROMPT, SEED, W, H, 81, 16.0, end=end)
    check("flf2v480 done", jr_flf.get("status") == "done", str(jr_flf.get("error")))
    flf_vid = jr_flf.get("output", {}).get("video")
    check("flf2v480 video", bool(flf_vid) and Path(flf_vid).is_file(), str(flf_vid))
    check("flf2v480 end_keyframe flag", jr_flf.get("output", {}).get("end_keyframe") is True,
          str(jr_flf.get("output", {}).get("end_keyframe")))
    # metering/ledger: the FLF2V job row should carry a wan model + cost
    # (metering lives in the durable jobs.jsonl ledger, not the in-memory record)
    ledger = RUN / "media_jobs" / "metrics" / "jobs.jsonl"
    led = {}
    for line in ledger.read_text().splitlines():
        try:
            d = json.loads(line)
        except Exception:
            continue
        if d.get("id") == jr_flf["id"] or d.get("job_id") == jr_flf["id"]:
            led = d
    check("flf2v480 metered (model+cost)", str(led.get("model", "")).startswith("wan")
          and (led.get("cost_usd") or 0) > 0,
          f"model={led.get('model')} cost={led.get('cost_usd')}")

    # ---- 480p i2v (start only, same seed) — the "without end frame" case ----
    print("-- 480p i2v (start only, same seed)")
    jr_i2v, i2v_delta = run_shots("i2v480", start, PROMPT, SEED, W, H, 81, 16.0, end=None)
    check("i2v480 done", jr_i2v.get("status") == "done", str(jr_i2v.get("error")))
    i2v_vid = jr_i2v.get("output", {}).get("video")
    check("i2v480 video", bool(i2v_vid) and Path(i2v_vid).is_file(), str(i2v_vid))

    # ---- end-pose landing: FLF2V last frame closer to END than to START ----
    print("-- end-pose landing check")
    lf = SCRATCH / "flf2v_last.png"
    last_frame(flf_vid, lf)
    s_raw = raw_rgb(start, W, H)
    e_raw = raw_rgb(end, W, H)
    lf_raw = raw_rgb(lf, W, H)
    mse_start = mse(lf_raw, s_raw)
    mse_end = mse(lf_raw, e_raw)
    print(f"  MSE(last, start)={mse_start:.1f}  MSE(last, end)={mse_end:.1f}", flush=True)
    check("flf2v last frame lands closer to END than START", mse_end < mse_start,
          f"mse_end={mse_end:.1f} mse_start={mse_start:.1f}")

    # ---- 720p FLF2V — VRAM gate / OOM check ----
    print("-- 720p FLF2V (OOM gate)")
    jr720, d720 = run_shots("flf2v720", start, PROMPT, SEED, 1280, 720, 81, 16.0, end=end)
    if jr720.get("status") == "done":
        check("flf2v720 done (720p admitted)", True)
        print(f"  720p FLF2V peak_delta={d720} MiB — fits the 24 GB card", flush=True)
    else:
        err = str(jr720.get("error", ""))
        oom = ("out of memory" in err.lower() or "CUDA" in err or "cuda" in err
               or "OOM" in err)
        check("flf2v720 OOM -> restrict to 480p", oom, f"status={jr720.get('status')} {err[:200]}")
        if not oom:
            print(f"  NOTE: 720p failed non-OOM: {err[:300]}", flush=True)

    # ---- VRAM gate summary ----
    print("\n== VRAM gate (GPU 1, 24 GB card; baseline incl. any resident models)")
    print(f"  480p FLF2V (start+end) peak_delta = {flf_delta} MiB")
    print(f"  480p i2v   (start only)  peak_delta = {i2v_delta} MiB")
    print(f"  720p FLF2V (start+end)   peak_delta = {d720} MiB "
          f"(status={jr720.get('status')})")

    fails = [n for n, ok, _ in RESULTS if not ok]
    print(f"\n== B2 FLF2V QA: {len(RESULTS) - len(fails)}/{len(RESULTS)} passed")
    if fails:
        print("FAILED:", fails)
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
