#!/usr/bin/env python3
"""Films B2 QA — CPU production tools (no GPU): /animatic, /extract_frame,
/assemble lut+grain. Run on the GPU host as chuck:

    python3 qa_films_b2.py

Hits the live API (192.168.4.55:8189) + verifies on-disk / ffmpeg-direct
state. All assets are generated with ffmpeg (CPU) in the comfyui_backend
container. The FLF2V (end_keyframe) GPU path is QA'd separately (it needs a
real wan14b render + the VRAM gate).
"""
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

BASE = "http://192.168.4.55:8189"  # Phase 17 LAN-only bind (not 127.0.0.1)
RUN = Path("/home/chuck/data/comfyui/run")
SCRATCH = RUN / "media_jobs" / "qa_b2"
CONTAINER = "comfyui_backend"
RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  [{detail}]" if detail and not ok else ""))


def req(method, path, body=None, raw=None):
    cmd = ["curl", "-s", "-m", "600", "-w", "\\n%{http_code}", "-X", method, f"{BASE}{path}"]
    if body is not None:
        cmd += ["-H", "Content-Type: application/json", "-d", json.dumps(body)]
    if raw is not None:
        cmd += ["--data-binary", raw, "-H", "Content-Type: application/json"]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=660)
    out = r.stdout.strip()
    body_s, _, code = out.rpartition("\n")
    try:
        return int(code), json.loads(body_s) if body_s else {}
    except Exception:
        return (int(code) if code.isdigit() else 0), {"raw": body_s}


def ffmpeg(args, timeout=600):
    r = subprocess.run(["docker", "exec", "-u", "comfy", CONTAINER, "ffmpeg"] + args,
                       capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {r.stderr[-600:]}")
    return r


def root_exec(cmd):
    r = subprocess.run(["docker", "exec", "-u", "root", "media_pipeline", "bash", "-c", cmd],
                       capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise RuntimeError(f"root exec failed: {r.stderr[-300:]}")
    return r


def cpath(p):
    """host path under RUN -> container /comfy/mnt path."""
    s = str(p)
    return "/comfy/mnt" + s[len(str(RUN)):] if s.startswith(str(RUN)) else s


def wait_job(jid, timeout=900):
    t0 = time.time()
    while time.time() - t0 < timeout:
        st, r = req("GET", f"/jobs/{jid}")
        if st == 200 and r.get("status") in ("done", "error", "timeout", "killed"):
            return r
        time.sleep(3)
    raise TimeoutError(f"job {jid} timed out")


def info(path):
    st, r = req("GET", f"/info?path={path}")
    return (st == 200), r


def main():
    # --- scratch setup (root-owned RUN_DIR; assets written by comfy) ---
    root_exec(f"mkdir -p {SCRATCH} && chmod 777 {SCRATCH}")
    # 3 animatic test images (distinct solid colors, 640x360)
    colors = ["0x3355ff", "0xff5533", "0x33cc66"]
    imgs = []
    for i, c in enumerate(colors):
        p = SCRATCH / f"anim_{i}.png"
        ffmpeg(["-y", "-f", "lavfi", "-i", f"color=c={c}:s=640x360",
                "-frames:v", "1", cpath(p)])
        imgs.append(p)
    # a mid-gray image for the LUT-shift check (0x808080)
    gray = SCRATCH / "gray.png"
    ffmpeg(["-y", "-f", "lavfi", "-i", "color=c=0x808080:s=640x360",
            "-frames:v", "1", cpath(gray)])
    # a 4.5 s sine audio bed (for the animatic mux)
    audio = SCRATCH / "bed.wav"
    ffmpeg(["-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=4.5",
            "-ar", "44100", cpath(audio)])
    # a known 100-frame testsrc clip for extract_frame (25 fps = 4 s)
    clip = SCRATCH / "testsrc.mp4"
    ffmpeg(["-y", "-f", "lavfi", "-i", "testsrc=size=640x360:rate=25:duration=4",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", cpath(clip)])
    print(f"== Films B2 QA — scratch {SCRATCH}")

    # ============ /animatic ============
    print("-- /animatic")
    manifest = [
        {"image": str(imgs[0]), "duration": 2.0, "move": "push_in"},
        {"image": str(imgs[1]), "duration": 1.5, "move": "pan_left"},
        {"image": str(imgs[2]), "duration": 1.0, "move": "shake"},
    ]
    st, r = req("POST", "/animatic", {"shots": manifest, "audio": str(audio),
                                      "width": 1280, "height": 720, "fps": 24})
    check("animatic submit", st == 200 and "job_id" in r, f"{st} {r}")
    jid = r.get("job_id")
    jr = wait_job(jid)
    check("animatic done", jr.get("status") == "done", str(jr.get("error")))
    vid = jr.get("output", {}).get("video")
    check("animatic video path", bool(vid) and Path(vid).is_file(), str(vid))
    ok, inf = info(vid)
    total = sum(m["duration"] for m in manifest)
    check("animatic duration ~= sum", ok and abs(inf["duration_s"] - total) <= 0.3,
          f"got {inf.get('duration_s')} want ~{total}")
    check("animatic dims", ok and (inf["width"], inf["height"]) == (1280, 720),
          f"{inf.get('width')}x{inf.get('height')}")
    # moves visually distinct: a frame in shot 1 (blue, push_in) vs a frame in
    # shot 3 (green, shake) must differ (different source image + motion).
    f0 = SCRATCH / "anim_f0.png"
    f_last = SCRATCH / "anim_flast.png"
    ffmpeg(["-y", "-ss", "0.1", "-i", cpath(vid), "-frames:v", "1", "-update", "1",
            cpath(f0)])
    ffmpeg(["-y", "-ss", "3.5", "-i", cpath(vid), "-frames:v", "1", "-update", "1",
            cpath(f_last)])
    # push_in starts on img0 (blue) and ends on img2 (green) -> very different
    def get_stat(stderr, key):
        for line in stderr.splitlines():
            if f"{key}=" in line:
                return line.split(f"{key}=")[1].strip()
        return None

    r0 = subprocess.run(["docker", "exec", "-u", "comfy", CONTAINER, "ffmpeg",
                         "-i", cpath(f0), "-vf", "signalstats,metadata=print", "-f", "null", "-"],
                        capture_output=True, text=True)
    r1 = subprocess.run(["docker", "exec", "-u", "comfy", CONTAINER, "ffmpeg",
                         "-i", cpath(f_last), "-vf", "signalstats,metadata=print", "-f", "null", "-"],
                        capture_output=True, text=True)
    y0 = get_stat(r0.stderr, "YAVG")
    y1 = get_stat(r1.stderr, "YAVG")
    check("animatic frames differ (motion)", y0 is not None and y1 is not None and y0 != y1,
          f"YAVG shot1={y0} shot3={y1}")
    # invalid move -> 400 (validated at submit)
    st, r = req("POST", "/animatic", {"shots": [{"image": str(imgs[0]), "duration": 1.0,
                                                 "move": "teleport"}]})
    check("animatic bad move -> 400", st == 400, f"{st} {r}")

    # ============ /extract_frame ============
    print("-- /extract_frame")
    # frame-index path: extract frames 0/40/80
    st, r = req("POST", "/extract_frame", {"source": str(clip), "frame": 40})
    check("extract frame submit", st == 200 and "job_id" in r, f"{st} {r}")
    jr = wait_job(r["job_id"])
    check("extract frame done", jr.get("status") == "done", str(jr.get("error")))
    api_f40 = jr.get("output", {}).get("frames", [None])[0]
    check("extract frame path", bool(api_f40) and Path(api_f40).is_file(), str(api_f40))
    # pixel-diff vs ffmpeg-direct (same select=eq(n,40)) — must be identical
    direct_f40 = SCRATCH / "direct_f40.png"
    ffmpeg(["-y", "-i", cpath(clip), "-vf", "select=eq(n\\,40)", "-frames:v", "1",
            "-update", "1", cpath(direct_f40)])
    a = Path(api_f40).read_bytes()
    b = direct_f40.read_bytes()
    check("extract frame pixel-identical to ffmpeg-direct", a == b,
          f"api {len(a)}B vs direct {len(b)}B")
    # time path
    st, r = req("POST", "/extract_frame", {"source": str(clip), "time": 2.0})
    jr = wait_job(r["job_id"])
    check("extract time done", jr.get("status") == "done", str(jr.get("error")))
    # positions path (first/middle/last)
    st, r = req("POST", "/extract_frame", {"source": str(clip),
                                           "positions": ["first", "middle", "last"]})
    jr = wait_job(r["job_id"])
    n = jr.get("output", {}).get("count")
    check("extract positions (first/middle/last) -> 3", jr.get("status") == "done" and n == 3,
          f"{jr.get('status')} count={n}")
    # n-spacing path (independent even-spacing mode)
    st, r = req("POST", "/extract_frame", {"source": str(clip), "n": 5})
    jr = wait_job(r["job_id"])
    check("extract n=5 even spacing -> 5", jr.get("status") == "done"
          and jr.get("output", {}).get("count") == 5, f"{jr.get('status')}")
    # exactly-one-of validation
    st, r = req("POST", "/extract_frame", {"source": str(clip), "frame": 1, "time": 1.0})
    check("extract both frame+time -> 400", st == 400, f"{st} {r}")
    st, r = req("POST", "/extract_frame", {"source": str(clip)})
    check("extract none -> 400", st == 400, f"{st} {r}")

    # ============ /assemble lut + grain ============
    print("-- /assemble lut+grain")
    shots = [str(gray), str(gray)]  # two mid-gray stills (each ~0s -> give duration)
    shots = [{"path": str(gray), "duration": 1.0}, {"path": str(gray), "duration": 1.0}]
    st, r = req("POST", "/assemble", {"shots": shots, "width": 640, "height": 360, "fps": 24})
    check("assemble (no grade) submit", st == 200 and "job_id" in r, f"{st} {r}")
    jr = wait_job(r["job_id"])
    check("assemble (no grade) done", jr.get("status") == "done", str(jr.get("error")))
    plain = jr.get("output", {}).get("video")
    ok, inf_p = info(plain)
    st, r = req("POST", "/assemble", {"shots": shots, "width": 640, "height": 360, "fps": 24,
                                      "lut": "cinematic_teal_amber", "grain": 0.5})
    check("assemble (graded) submit", st == 200 and "job_id" in r, f"{st} {r}")
    jr = wait_job(r["job_id"])
    check("assemble (graded) done", jr.get("status") == "done", str(jr.get("error")))
    graded = jr.get("output", {}).get("video")
    ok2, inf_g = info(graded)
    check("assemble grade: duration identical", ok and ok2
          and abs(inf_p["duration_s"] - inf_g["duration_s"]) <= 0.05,
          f"plain {inf_p.get('duration_s')} graded {inf_g.get('duration_s')}")
    # LUT shift: mid-gray (128,128,128) -> teal_amber shifts G up / teal
    fp = SCRATCH / "plain_frame.png"
    fg = SCRATCH / "graded_frame.png"
    ffmpeg(["-y", "-i", cpath(plain), "-vf", "select=eq(n\\,10)", "-frames:v", "1",
            "-update", "1", cpath(fp)])
    ffmpeg(["-y", "-i", cpath(graded), "-vf", "select=eq(n\\,10)", "-frames:v", "1",
            "-update", "1", cpath(fg)])

    def mean_rgb(p):
        r = subprocess.run(["docker", "exec", "-u", "comfy", CONTAINER, "ffmpeg",
                            "-i", cpath(p), "-vf", "signalstats,metadata=print", "-f", "null", "-"],
                           capture_output=True, text=True)

        def gs(key):
            for line in r.stderr.splitlines():
                if f"{key}=" in line:
                    return float(line.split(f"{key}=")[1].strip())
            return None

        y, u, v = gs("YAVG"), gs("UAVG"), gs("VAVG")
        return (y, u, v)

    mp = mean_rgb(fp)
    mg = mean_rgb(fg)
    lut_ok = (all(x is not None for x in mp) and all(x is not None for x in mg)
              and (abs(mp[1] - mg[1]) > 0.5 or abs(mp[2] - mg[2]) > 0.5))
    check("assemble grade: LUT shifts color (U/V differ)", lut_ok,
          f"plain YUV={mp} graded YUV={mg}")
    # bad lut -> 400
    st, r = req("POST", "/assemble", {"shots": shots, "lut": "nope_not_a_lut"})
    check("assemble bad lut -> 400", st == 400, f"{st} {r}")
    # grain out of range -> 400
    st, r = req("POST", "/assemble", {"shots": shots, "grain": 2.0})
    check("assemble grain>1 -> 400", st == 400, f"{st} {r}")

    # --- summary ---
    fails = [n for n, ok, _ in RESULTS if not ok]
    print(f"\n== B2 CPU QA: {len(RESULTS) - len(fails)}/{len(RESULTS)} passed")
    if fails:
        print("FAILED:", fails)
        return 1
    # cleanup
    root_exec(f"rm -rf {SCRATCH}")
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
