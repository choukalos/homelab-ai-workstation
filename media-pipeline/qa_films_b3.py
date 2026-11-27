#!/usr/bin/env python3
"""Films B3 QA — /qc_montage (CPU, ffmpeg only). Run on the GPU host as chuck:

    python3 qa_films_b3.py

Checks:
  1. /qc_montage n=3 (first/middle/last) -> done, single hstack PNG, dims = 3*cell_w x cell_h.
  2. The QC frames match /extract_frame positions=[first,middle,last] output (pixel-identical).
  3. /qc_montage n=5 even spacing -> 5*cell_w x cell_h.
  4. /qc_montage label=false -> done.
  5. bad n (1, 13, non-int) -> 400; missing source -> 400.
"""
import json
import subprocess
import sys
import time
from pathlib import Path

BASE = "http://192.168.4.55:8189"
RUN = Path("/home/chuck/data/comfyui/run")
SCRATCH = RUN / "media_jobs" / "qa_b3"
CONTAINER = "comfyui_backend"
CELL_W, CELL_H = 480, 270
RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  [{detail}]" if detail and not ok else ""),
          flush=True)


def req(method, path, body=None):
    cmd = ["curl", "-s", "-m", "300", "-w", "\\n%{http_code}", "-X", method, f"{BASE}{path}"]
    if body is not None:
        cmd += ["-H", "Content-Type: application/json", "-d", json.dumps(body)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=320)
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


def ffmpeg(args, timeout=300):
    r = subprocess.run(["docker", "exec", "-u", "comfy", CONTAINER, "ffmpeg"] + args,
                       capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {r.stderr[-600:]}")
    return r


def wait_job(jid, timeout=600):
    t0 = time.time()
    while time.time() - t0 < timeout:
        st, r = req("GET", f"/jobs/{jid}")
        if st == 200 and r.get("status") in ("done", "error", "timeout", "killed"):
            return r
        time.sleep(3)
    raise TimeoutError(f"job {jid} timed out")


def probe_dims(p):
    r = subprocess.run(["docker", "exec", "-u", "comfy", CONTAINER, "ffprobe", "-v", "error",
                        "-show_entries", "stream=width,height", "-of", "csv=p=0",
                        cpath(p)], capture_output=True, text=True, timeout=60)
    parts = r.stdout.strip().split(",")
    if len(parts) < 2:
        raise RuntimeError(f"ffprobe failed: {r.stderr[-200:]}")
    return int(parts[0]), int(parts[1])


def raw_rgb(p):
    r = subprocess.run(["docker", "exec", "-u", "comfy", CONTAINER, "ffmpeg", "-y",
                        "-i", cpath(p), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                       capture_output=True, timeout=120)
    return r.stdout


def main():
    root_exec(f"mkdir -p {SCRATCH} && chmod 777 {SCRATCH}")
    src = SCRATCH / "clip5s.mp4"
    ffmpeg(["-y", "-f", "lavfi", "-i", f"testsrc=duration=5:size={CELL_W}x{CELL_H}:rate=24",
            cpath(src)])
    print(f"== Films B3 QA — scratch {SCRATCH}")

    # --- 1. n=3 (first/middle/last) ---
    print("-- /qc_montage n=3")
    st, r = req("POST", "/qc_montage", {"source": str(src), "n": 3})
    check("qc_montage n=3 submit", st == 200 and "job_id" in r, f"{st} {r}")
    qc_jid = r["job_id"]
    jr = wait_job(qc_jid)
    check("qc_montage n=3 done", jr.get("status") == "done", str(jr.get("error")))
    img = jr.get("output", {}).get("image")
    check("qc_montage n=3 image", bool(img) and Path(img).is_file(), str(img))
    w, h = probe_dims(img)
    check("qc_montage n=3 dims = 3*cell", (w, h) == (3 * CELL_W, CELL_H), f"{w}x{h}")
    check("qc_montage n=3 timestamps", len(jr.get("output", {}).get("timestamps", [])) == 3,
          str(jr.get("output", {}).get("timestamps")))

    # --- 2. QC frames match /extract_frame positions ---
    print("-- frames match /extract_frame")
    st, r = req("POST", "/extract_frame", {"source": str(src), "positions": ["first", "middle", "last"]})
    check("extract_frame positions submit", st == 200 and "job_id" in r, f"{st} {r}")
    jre = wait_job(r["job_id"])
    check("extract_frame positions done", jre.get("status") == "done", str(jre.get("error")))
    ef = jre.get("output", {}).get("frames", [])
    # the qc frames live under JOB_DIR/<qc_jid>/qc_*.png (format :08.3f)
    qc_dir = RUN / "media_jobs" / qc_jid
    qc_sorted = sorted(qc_dir.glob("qc_*.png"))
    if len(ef) >= 2 and len(qc_sorted) >= 2:
        ef0 = raw_rgb(ef[0])
        qc0 = raw_rgb(qc_sorted[0])
        check("qc frame[0] == extract first (pixel-identical)", qc0 == ef0,
              f"qc={qc_sorted[0].name} {len(qc0)}B extract={len(ef0)}B")
        ef1 = raw_rgb(ef[1])
        qc1 = raw_rgb(qc_sorted[1])
        check("qc frame[1] == extract middle (pixel-identical)", qc1 == ef1,
              f"qc={qc_sorted[1].name} {len(qc1)}B extract={len(ef1)}B")
    else:
        check("qc frame[0] == extract first (pixel-identical)", False,
              f"ef={len(ef)} qc_frames={len(qc_sorted)}")
        check("qc frame[1] == extract middle (pixel-identical)", False, "missing")

    # --- 3. n=5 even spacing ---
    print("-- /qc_montage n=5")
    st, r = req("POST", "/qc_montage", {"source": str(src), "n": 5})
    check("qc_montage n=5 submit", st == 200 and "job_id" in r, f"{st} {r}")
    jr = wait_job(r["job_id"])
    check("qc_montage n=5 done", jr.get("status") == "done", str(jr.get("error")))
    img5 = jr.get("output", {}).get("image")
    if img5 and Path(img5).is_file():
        w, h = probe_dims(img5)
        check("qc_montage n=5 dims = 5*cell", (w, h) == (5 * CELL_W, CELL_H), f"{w}x{h}")
        ts = jr.get("output", {}).get("timestamps", [])
        check("qc_montage n=5 timestamps even", len(ts) == 5 and abs(ts[0]) < 0.01
              and abs(ts[-1] - 4.96) < 0.1, str(ts))
    else:
        check("qc_montage n=5 dims = 5*cell", False, str(img5))
        check("qc_montage n=5 timestamps even", False, "no image")

    # --- 4. label=false ---
    print("-- /qc_montage label=false")
    st, r = req("POST", "/qc_montage", {"source": str(src), "n": 3, "label": False})
    check("qc_montage label=false submit", st == 200 and "job_id" in r, f"{st} {r}")
    jr = wait_job(r["job_id"])
    check("qc_montage label=false done", jr.get("status") == "done", str(jr.get("error")))

    # --- 5. validation ---
    print("-- validation")
    st, r = req("POST", "/qc_montage", {"source": str(src), "n": 1})
    check("qc_montage n=1 -> 400", st == 400, f"{st} {r}")
    st, r = req("POST", "/qc_montage", {"source": str(src), "n": 13})
    check("qc_montage n=13 -> 400", st == 400, f"{st} {r}")
    st, r = req("POST", "/qc_montage", {"source": str(src), "n": "abc"})
    check("qc_montage n=abc -> 400", st == 400, f"{st} {r}")
    st, r = req("POST", "/qc_montage", {"n": 3})
    check("qc_montage missing source -> 400", st == 400, f"{st} {r}")

    fails = [n for n, ok, _ in RESULTS if not ok]
    print(f"\n== B3 QA: {len(RESULTS) - len(fails)}/{len(RESULTS)} passed")
    if fails:
        print("FAILED:", fails)
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
