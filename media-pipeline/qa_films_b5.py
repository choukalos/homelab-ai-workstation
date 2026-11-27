#!/usr/bin/env python3
"""Films B5 QA — /montage (generic video -> grid PNG, CPU/ffmpeg only).

    python3 qa_films_b5.py

Checks:
  1. 60 s test video -> /montage n=9 -> done, 3x3 grid PNG (1440x810), 9 timestamps
     evenly spaced, correct vs /info duration.
  2. n=4 -> 2x2 grid (960x540).
  3. n=5 -> 3x2 grid with 1 black pad cell (1440x540).
  4. label=false -> done.
  5. bad n (1, 37, non-int) -> 400; missing source -> 400.
"""
import json
import subprocess
import sys
import time
from pathlib import Path

BASE = "http://192.168.4.55:8189"
RUN = Path("/home/chuck/data/comfyui/run")
SCRATCH = RUN / "media_jobs" / "qa_b5"
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


def main():
    root_exec(f"mkdir -p {SCRATCH} && chmod 777 {SCRATCH}")
    src = SCRATCH / "clip60s.mp4"
    ffmpeg(["-y", "-f", "lavfi", "-i", f"testsrc=duration=60:size={CELL_W}x{CELL_H}:rate=24",
            cpath(src)])
    print(f"== Films B5 QA — scratch {SCRATCH}")

    # --- 1. n=9 (3x3 grid) on a 60 s clip ---
    print("-- /montage n=9 (60 s clip)")
    st, r = req("POST", "/montage", {"source": str(src), "n": 9})
    check("montage n=9 submit", st == 200 and "job_id" in r, f"{st} {r}")
    jr = wait_job(r["job_id"])
    check("montage n=9 done", jr.get("status") == "done", str(jr.get("error")))
    img = jr.get("output", {}).get("image")
    check("montage n=9 image", bool(img) and Path(img).is_file(), str(img))
    w, h = probe_dims(img)
    check("montage n=9 grid 3x3 dims", (w, h) == (3 * CELL_W, 3 * CELL_H), f"{w}x{h}")
    check("montage n=9 grid field", jr.get("output", {}).get("grid") == "3x3",
          str(jr.get("output", {}).get("grid")))
    ts = jr.get("output", {}).get("timestamps", [])
    check("montage n=9 9 timestamps", len(ts) == 9, str(ts))
    # evenly spaced across 60 s: 0, 7.5, 15, ..., 59.9
    check("montage n=9 spacing", abs(ts[0]) < 0.01 and abs(ts[1] - 7.5) < 0.1
          and abs(ts[4] - 30.0) < 0.1 and abs(ts[-1] - 59.9) < 0.1, str(ts))
    # timestamps correct vs /info duration
    st, info = req("GET", f"/info?path={src}")
    dur = info.get("duration_s", 0) if st == 200 else 0
    check("montage n=9 ts correct vs /info", abs(dur - 60.0) < 0.5 and abs(ts[-1] - (dur - 0.1)) < 0.15,
          f"info_dur={dur} last_ts={ts[-1] if ts else None}")

    # --- 2. n=4 (2x2) ---
    print("-- /montage n=4 (2x2)")
    st, r = req("POST", "/montage", {"source": str(src), "n": 4})
    check("montage n=4 submit", st == 200 and "job_id" in r, f"{st} {r}")
    jr = wait_job(r["job_id"])
    check("montage n=4 done", jr.get("status") == "done", str(jr.get("error")))
    img4 = jr.get("output", {}).get("image")
    if img4 and Path(img4).is_file():
        w, h = probe_dims(img4)
        check("montage n=4 grid 2x2 dims", (w, h) == (2 * CELL_W, 2 * CELL_H), f"{w}x{h}")
        check("montage n=4 grid field", jr.get("output", {}).get("grid") == "2x2",
              str(jr.get("output", {}).get("grid")))
    else:
        check("montage n=4 grid 2x2 dims", False, str(img4))
        check("montage n=4 grid field", False, "no image")

    # --- 3. n=5 (3x2 with 1 pad) ---
    print("-- /montage n=5 (3x2 + pad)")
    st, r = req("POST", "/montage", {"source": str(src), "n": 5})
    check("montage n=5 submit", st == 200 and "job_id" in r, f"{st} {r}")
    jr = wait_job(r["job_id"])
    check("montage n=5 done", jr.get("status") == "done", str(jr.get("error")))
    img5 = jr.get("output", {}).get("image")
    if img5 and Path(img5).is_file():
        w, h = probe_dims(img5)
        # cols=ceil(sqrt(5))=3, rows=ceil(5/3)=2 -> 3*480 x 2*270
        check("montage n=5 grid 3x2 dims (padded)", (w, h) == (3 * CELL_W, 2 * CELL_H), f"{w}x{h}")
        check("montage n=5 grid field", jr.get("output", {}).get("grid") == "3x2",
              str(jr.get("output", {}).get("grid")))
    else:
        check("montage n=5 grid 3x2 dims (padded)", False, str(img5))
        check("montage n=5 grid field", False, "no image")

    # --- 4. label=false ---
    print("-- /montage label=false")
    st, r = req("POST", "/montage", {"source": str(src), "n": 4, "label": False})
    check("montage label=false submit", st == 200 and "job_id" in r, f"{st} {r}")
    jr = wait_job(r["job_id"])
    check("montage label=false done", jr.get("status") == "done", str(jr.get("error")))

    # --- 5. validation ---
    print("-- validation")
    st, r = req("POST", "/montage", {"source": str(src), "n": 1})
    check("montage n=1 -> 400", st == 400, f"{st} {r}")
    st, r = req("POST", "/montage", {"source": str(src), "n": 37})
    check("montage n=37 -> 400", st == 400, f"{st} {r}")
    st, r = req("POST", "/montage", {"source": str(src), "n": "abc"})
    check("montage n=abc -> 400", st == 400, f"{st} {r}")
    st, r = req("POST", "/montage", {"n": 9})
    check("montage missing source -> 400", st == 400, f"{st} {r}")

    fails = [n for n, ok, _ in RESULTS if not ok]
    print(f"\n== B5 QA: {len(RESULTS) - len(fails)}/{len(RESULTS)} passed")
    if fails:
        print("FAILED:", fails)
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
