#!/usr/bin/env python3
"""Films B2 FLF2V QA — part 2 (GPU). The 480p FLF2V render already completed
(part 1, job df9bdb87ef70: done, video, end_keyframe:true, metered as wan14b,
peak 23066 MiB total on the card). This part does the remaining checks:

  1. End-pose landing (CPU): the FLF2V clip's last frame is closer (MSE) to the
     END keyframe than the start — the exact end-pose control.
  2. i2v 480p (GPU): same start frame + seed, NO end_keyframe -> the "without
     end frame" case still renders (existing i2v path unchanged).
  3. 720p FLF2V (GPU): the VRAM gate / OOM check. If it OOMs, FLF2V is
     restricted to 480p + documented.

~65 min (i2v ~10 min + 720p ~55 min). Run in the background:
    python3 qa_films_b2_flf2v_part2.py
"""
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

BASE = "http://192.168.4.55:8189"
RUN = Path("/home/chuck/data/comfyui/run")
SCRATCH = RUN / "media_jobs" / "qa_b2_flf2v"
CONTAINER = "comfyui_backend"
GPU = 1
SEED = 42
W, H = 832, 480
FLF2V_VIDEO = str(RUN / "media_jobs/df9bdb87ef70/mp_df9bdb87ef70_00001.mp4")
RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  [{detail}]" if detail and not ok else ""),
          flush=True)


def cpath(p):
    s = str(p)
    return "/comfy/mnt" + s[len(str(RUN)):] if s.startswith(str(RUN)) else s


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


def submit_shots(start, prompt, seed, w, h, frames, fps, end=None):
    cmd = ["curl", "-s", "-m", "120", "-w", "\\n%{http_code}", "-X", "POST",
           f"{BASE}/shots", "-F", f"file=@{start}", "-F", "prompt=" + prompt,
           "-F", "model=wan14b", "-F", f"seed={seed}", "-F", f"width={w}",
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
    wall = jr.get("duration_s")
    print(f"[{tag}] status={jr.get('status')} peak_vram={peak} MiB wall={wall}s "
          f"error={jr.get('error')}", flush=True)
    return jr, peak


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
    for i in range(0, n, 3):
        s += (a[i] - b[i]) ** 2 + (a[i + 1] - b[i + 1]) ** 2 + (a[i + 2] - b[i + 2]) ** 2
    return s / (n / 3)


def main():
    start = SCRATCH / "start.png"
    end = SCRATCH / "end.png"
    print(f"== Films B2 FLF2V QA part 2 — scratch {SCRATCH} (GPU {GPU}, seed {SEED})")

    # ---- 1. end-pose landing (CPU) ----
    print("-- end-pose landing (CPU)")
    lf = SCRATCH / "flf2v_last_p2.png"
    subprocess.run(["docker", "exec", "-u", "comfy", CONTAINER, "ffmpeg", "-y",
                    "-sseof", "-0.1", "-i", cpath(FLF2V_VIDEO), "-frames:v", "1",
                    "-update", "1", cpath(lf)], capture_output=True, timeout=120)
    s_raw = raw_rgb(start, W, H)
    e_raw = raw_rgb(end, W, H)
    lf_raw = raw_rgb(lf, W, H)
    mse_start = mse(lf_raw, s_raw)
    mse_end = mse(lf_raw, e_raw)
    print(f"  MSE(last, start)={mse_start:.1f}  MSE(last, end)={mse_end:.1f}", flush=True)
    check("flf2v last frame lands closer to END than START", mse_end < mse_start,
          f"mse_end={mse_end:.1f} mse_start={mse_start:.1f}")

    PROMPT = ("A slow camera drift across a minimal scene: a warm red panel on the "
              "left gradually gives way to a cool blue panel on the right, smooth "
              "continuous motion, static background, no text")

    # ---- 2. i2v 480p (start only, same seed) ----
    print("-- i2v 480p (start only, same seed)")
    jr_i2v, i2v_peak = run_shots("i2v480", start, PROMPT, SEED, W, H, 81, 16.0, end=None)
    check("i2v480 done", jr_i2v.get("status") == "done", str(jr_i2v.get("error")))
    i2v_vid = jr_i2v.get("output", {}).get("video")
    check("i2v480 video", bool(i2v_vid) and Path(i2v_vid).is_file(), str(i2v_vid))
    check("i2v480 no end_keyframe flag", jr_i2v.get("output", {}).get("end_keyframe") in (None, False),
          str(jr_i2v.get("output", {}).get("end_keyframe")))

    # ---- 3. 720p FLF2V (OOM gate) ----
    print("-- 720p FLF2V (OOM gate)")
    jr720, d720 = run_shots("flf2v720", start, PROMPT, SEED, 1280, 720, 81, 16.0, end=end)
    if jr720.get("status") == "done":
        check("flf2v720 done (720p admitted)", True)
        print(f"  720p FLF2V peak_vram={d720} MiB total on the 24 GB card", flush=True)
    else:
        err = str(jr720.get("error", ""))
        oom = ("out of memory" in err.lower() or "cuda" in err.lower() or "oom" in err.lower()
               or "vram" in err.lower())
        check("flf2v720 OOM -> restrict to 480p", oom,
              f"status={jr720.get('status')} {err[:200]}")
        if not oom:
            print(f"  NOTE: 720p failed non-OOM: {err[:300]}", flush=True)

    fails = [n for n, ok, _ in RESULTS if not ok]
    print(f"\n== B2 FLF2V QA part 2: {len(RESULTS) - len(fails)}/{len(RESULTS)} passed")
    if fails:
        print("FAILED:", fails)
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
