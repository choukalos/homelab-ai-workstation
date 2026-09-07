#!/usr/bin/env python3
"""QA suite for media-pipeline Part 1 (M1-M8). Run on matrix.

Fixtures + artifacts under /home/chuck/data/comfyui/run/media_jobs/qa_tests/
"""
import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = os.environ.get("MEDIA_PIPELINE_URL", "http://127.0.0.1:8189")
QA = "/home/chuck/data/comfyui/run/media_jobs/qa_tests"
CQA = "/comfy/mnt/media_jobs/qa_tests"  # same dir, container-side
RESULTS = []


def log(msg):
    print(f"[qa] {msg}", flush=True)


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    log(f"{'PASS' if cond else 'FAIL'}: {name} {detail}")


def http(method, path, body=None, raw_body=None, headers=None, timeout=600):
    url = BASE + path
    data = None
    hdrs = dict(headers or {})
    if body is not None:
        data = json.dumps(body).encode()
        hdrs.setdefault("Content-Type", "application/json")
    if raw_body is not None:
        data = raw_body
    req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers)


def probe(path):
    st, body, _ = http("GET", f"/info?path={urllib.parse.quote(path)}")
    if st != 200:
        return None
    return json.loads(body)


def run_ffmpeg_host(args, timeout=600):
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)


def gen_fixture(name, args):
    out = f"{CQA}/{name}"
    r = run_ffmpeg_host(["docker", "exec", "-u", "comfy", "comfyui_backend", "ffmpeg", "-y"]
                        + args + [out])
    if r.returncode != 0:
        log(f"fixture gen failed: {name}: {r.stderr[-400:]}")
        sys.exit(1)
    log(f"fixture: {out}")


def submit(path, body):
    st, resp, _ = http("POST", path, body)
    if st != 200:
        check(f"submit {path}", False, f"HTTP {st}: {resp.decode()[:300]}")
        return None
    return json.loads(resp)["job_id"]


def wait_job(jid, timeout=1800):
    t0 = time.time()
    while time.time() - t0 < timeout:
        st, body, _ = http("GET", f"/jobs/{jid}")
        j = json.loads(body)
        if j["status"] in ("done", "error"):
            return j
        time.sleep(2)
    return {"status": "timeout"}


def job_video(jid):
    st, body, _ = http("GET", f"/jobs/{jid}")
    return json.loads(body)["output"]["video"]


def multipart_upload(path, filename, data):
    boundary = "----qaBoundary7d2f"
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
            f"filename=\"{filename}\"\r\nContent-Type: application/octet-stream\r\n\r\n").encode() \
           + data + f"\r\n--{boundary}--\r\n".encode()
    return http("POST", path, raw_body=body,
                headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})


def cpath(p):
    # any path under the media run dir maps to /comfy/mnt inside comfyui_backend
    # (job outputs live in sibling job dirs, not just qa_tests/)
    RUN = "/home/chuck/data/comfyui/run"
    if p.startswith(RUN + "/"):
        return "/comfy/mnt" + p[len(RUN):]
    return p


def frame_psnr(path, t1, t2):
    """PSNR (dB) between two frames of a clip. >40 = effectively identical.
    (CRF-18 P-frame quantization drift means exact pixel identity is impossible.)"""
    r = run_ffmpeg_host(["docker", "exec", "-u", "comfy", "comfyui_backend", "bash", "-c",
                         f"ffmpeg -y -v quiet -ss {t1} -i {cpath(path)} -frames:v 1 /tmp/qa_a.png && "
                         f"ffmpeg -y -v quiet -ss {t2} -i {cpath(path)} -frames:v 1 /tmp/qa_b.png && "
                         f"ffmpeg -i /tmp/qa_a.png -i /tmp/qa_b.png -filter_complex psnr -f null - 2>&1 | grep -o 'average:[0-9.]*' | head -1 | cut -d: -f2"])
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def frame_hashes(path, n=6):
    d = probe(path)
    dur = d["duration_s"]
    out = []
    for i in range(n):
        t = dur * (i + 0.5) / n
        r = subprocess.run(["docker", "exec", "-u", "comfy", "comfyui_backend", "ffmpeg",
                            "-ss", f"{t:.3f}", "-i", cpath(path), "-frames:v", "1",
                            "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:"],
                           capture_output=True, timeout=120)
        out.append(hashlib.sha256(r.stdout).hexdigest())
    return out


def silencedetect(path, threshold="-30dB", min_dur=0.2):
    r = run_ffmpeg_host(["docker", "exec", "-u", "comfy", "comfyui_backend", "ffmpeg",
                         "-i", cpath(path), "-af", f"silencedetect=n={threshold}:d={min_dur}",
                         "-f", "null", "-"])
    return [l for l in r.stderr.splitlines() if "silence_" in l]


def main():
    os.makedirs(QA, exist_ok=True)

    # ---------------------------------------------------------------- fixtures
    log("=== generating fixtures ===")
    gen_fixture("standin_Peanut.mov", [
        "-f", "lavfi", "-i", "testsrc=duration=17.515:size=1280x720:rate=24",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=17.515",
        "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-shortest"])
    gen_fixture("still.png", [
        "-f", "lavfi", "-i", "testsrc=duration=1:size=1280x720:rate=24",
        "-frames:v", "1", "-update", "1"])
    gen_fixture("clip4s.mp4", [
        "-f", "lavfi", "-i", "testsrc=duration=4:size=1280x720:rate=24",
        "-f", "lavfi", "-i", "sine=frequency=330:duration=4",
        "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-shortest"])
    gen_fixture("sfx_ding.wav", [
        "-f", "lavfi", "-i", "sine=frequency=880:duration=1",
        "-af", "afade=t=in:d=0.05,afade=t=out:st=0.9:d=0.1"])
    gen_fixture("vo.wav", [
        "-f", "lavfi", "-i", "sine=frequency=220:duration=3",
        "-af", "afade=t=in:d=0.05,afade=t=out:st=2.9:d=0.1"])

    standin = f"{QA}/standin_Peanut.mov"
    still = f"{QA}/still.png"
    clip4s = f"{QA}/clip4s.mp4"
    ding = f"{QA}/sfx_ding.wav"
    vo = f"{QA}/vo.wav"

    # ---------------------------------------------------------------- M1 trim
    log("=== M1: trim ===")
    m1_trimmed = None
    jid = submit("/trim", {"source": standin, "start": 0, "end": 4.0})
    if jid:
        j = wait_job(jid)
        check("M1 trim job done", j["status"] == "done", j.get("error", ""))
        if j["status"] == "done":
            v = job_video(jid)
            d = probe(v)
            check("M1 trim duration 3.95-4.05", d and 3.95 <= d["duration_s"] <= 4.05,
                  f"duration={d and d['duration_s']}")
            check("M1 trim has audio", d and d["audio_codecs"], str(d and d["audio_codecs"]))
            m1_trimmed = v

    # ---------------------------------------------------------------- M2 freeze
    log("=== M2: freeze ===")
    m2_frozen = None
    jid = submit("/freeze", {"source": still, "duration": 2.0})
    if jid:
        j = wait_job(jid)
        check("M2 freeze job done", j["status"] == "done", j.get("error", ""))
        if j["status"] == "done":
            v = job_video(jid)
            d = probe(v)
            check("M2 freeze duration ~2.0", d and abs(d["duration_s"] - 2.0) < 0.1,
                  f"duration={d and d['duration_s']}")
            check("M2 freeze 24fps", d and d["fps"] == 24, f"fps={d and d['fps']}")
            psnr = frame_psnr(v, 0.25, 1.5)
            check("M2 freeze frames identical (PSNR>40)", psnr > 40, f"PSNR={psnr:.1f}dB")
            m2_frozen = v
    jid = submit("/freeze", {"source": standin, "frame": 48, "duration": 1.0,
                             "width": 640, "height": 360})
    if jid:
        j = wait_job(jid)
        check("M2 freeze-from-video done", j["status"] == "done", j.get("error", ""))

    # ---------------------------------------------------------------- M3 caption
    log("=== M3: caption ===")
    m3_captioned = None
    jid = submit("/caption", {"source": clip4s, "text": "I DON'T MAKE THE RULES.",
                              "start": 0.5, "end": 3.5, "position": "bottom",
                              "font_size": 56})
    if jid:
        j = wait_job(jid)
        check("M3 caption job done", j["status"] == "done", j.get("error", ""))
        if j["status"] == "done":
            v = job_video(jid)
            m3_captioned = v
            frames = f"{QA}/m3_frames"
            run_ffmpeg_host(["docker", "exec", "-u", "comfy", "comfyui_backend",
                             "mkdir", "-p", cpath(frames)])
            for i, t in enumerate([0.2, 2.0, 3.9]):
                run_ffmpeg_host(["docker", "exec", "-u", "comfy", "comfyui_backend",
                                 "ffmpeg", "-y", "-ss", str(t), "-i", cpath(v),
                                 "-frames:v", "1", f"{cpath(frames)}/f{i}.png"])
            check("M3 frames extracted", os.path.exists(f"{frames}/f0.png"))

    # ---------------------------------------------------------------- M4 assemble
    log("=== M4: assemble backward-compat (old style) ===")
    jid = submit("/assemble", {
        "shots": [clip4s, standin],
        "sfx": ding,
        "width": 1280, "height": 720, "fps": 24})
    if jid:
        j = wait_job(jid)
        check("M4 old-style assemble done", j["status"] == "done", j.get("error", ""))
        if j["status"] == "done":
            v = job_video(jid)
            d = probe(v)
            check("M4 old-style duration ~17.5+4", d and 20.5 <= d["duration_s"] <= 22.5,
                  f"duration={d and d['duration_s']}")

    log("=== M4: assemble new style (object shots + sfx list + vo_start + loudnorm) ===")
    # 3.0s still + 2.5s trimmed standin (1.0-3.5) + 4.0s clip = 9.5s
    jid = submit("/assemble", {
        "shots": [
            {"path": still, "duration": 3.0},
            {"path": standin, "in": 1.0, "out": 3.5},
            clip4s,
        ],
        "vo": vo, "vo_start": 1.0, "vo_volume": 0.8,
        "sfx": [{"path": ding, "at": 1.0}, {"path": ding, "at": 5.5}],
        "loudnorm": True,
        "width": 1280, "height": 720, "fps": 24})
    if jid:
        j = wait_job(jid)
        check("M4 new-style assemble done", j["status"] == "done", j.get("error", ""))
        if j["status"] == "done":
            v = job_video(jid)
            d = probe(v)
            check("M4 new-style duration ~9.5", d and 9.4 <= d["duration_s"] <= 9.6,
                  f"duration={d and d['duration_s']}")
            sd = silencedetect(v)
            text = "\n".join(sd)
            check("M4 sfx@1.0 audible", any("silence_end: 1." in l for l in sd), text[:300])
            check("M4 sfx@5.5 audible", any("silence_end: 5." in l for l in sd), text[:300])
            check("M4 vo_start=1 (silence 0-1)", any("silence_start: 0" in l for l in sd),
                  text[:300])

    # ---------------------------------------------------------------- M5 info
    log("=== M5: info ===")
    d = probe("/home/chuck/data/comfyui/run/media_jobs/11b290ce2acf/final.mp4")
    check("M5 info on real final.mp4", d is not None, str(d)[:150])
    if d:
        check("M5 duration ~58.6s", abs(d["duration_s"] - 58.6) < 0.15,
              f"duration={d['duration_s']}")
        check("M5 1080p h264/aac", d.get("height") == 1080 and d.get("video_codec") == "h264"
              and d.get("audio_codecs") == ["aac"],
              f"{d.get('width')}x{d.get('height')} {d.get('video_codec')}/{d.get('audio_codecs')}")
    st, _, _ = http("GET", "/info?path=/home/chuck/data/comfyui/run/media_jobs/does_not_exist.mp4")
    check("M5 info 404 on missing", st == 404, f"HTTP {st}")
    st, _, _ = http("GET", "/info?path=/home/chuck/data/comfyui/run/media_jobs/qa_tests/vo.wav")
    check("M5 info works on audio file", st == 200, f"HTTP {st}")

    # ---------------------------------------------------------------- M6 upload_local
    log("=== M6: upload_local ===")
    st, body, _ = http("POST", "/upload_local",
                       {"source": "/home/chuck/data/comfyui/basedir/output/peanut_freeze1_00001.mp4"})
    ok = st == 200
    p = json.loads(body).get("path", "") if ok else ""
    check("M6 upload_local bridges basedir file",
          ok and p.startswith("/home/chuck/data/comfyui/run/media_jobs/") and os.path.exists(p),
          f"HTTP {st} {p}")
    if ok and p:
        d = probe(p)
        check("M6 bridged file probes", d is not None, str(d and d["duration_s"]))
        jid = submit("/assemble", {"shots": [p], "width": 1280, "height": 720, "fps": 24})
        if jid:
            j = wait_job(jid)
            check("M6 bridged path accepted by assemble", j["status"] == "done",
                  j.get("error", ""))
    st, body, _ = http("POST", "/upload_local", {"source": "/etc/passwd"})
    check("M6 rejects outside basedir (passwd)", st == 400, f"HTTP {st}")
    st, body, _ = http("POST", "/upload_local", {"source": "/home/chuck/data/comfyui/run/media_jobs/does_not_exist.mp4"})
    check("M6 rejects missing source", st == 400, f"HTTP {st}")

    # ---------------------------------------------------------------- M7 download
    log("=== M7: download ===")
    srv = subprocess.Popen(["python3", "-m", "http.server", "18199", "--directory", QA],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1)
    try:
        st, body, _ = http("POST", "/download",
                           {"url": "http://127.0.0.1:18199/clip4s.mp4"})
        ok = st == 200
        p = json.loads(body).get("path", "") if ok else ""
        check("M7 download ingests URL", ok and os.path.exists(p)
              and os.path.getsize(p) > 0, f"HTTP {st} {p}")
        if ok and p:
            jid = submit("/trim", {"source": p, "start": 0, "end": 1.0})
            if jid:
                j = wait_job(jid)
                check("M7 downloaded path accepted by trim", j["status"] == "done",
                      j.get("error", ""))
        st, body, _ = http("POST", "/download", {"url": "file:///etc/passwd"})
        check("M7 rejects non-http url", st == 400, f"HTTP {st}")
    finally:
        srv.terminate()

    # ---------------------------------------------------------------- M8 client transfer
    log("=== M8: upload / dl_token / dl ===")
    with open(clip4s, "rb") as f:
        clip_bytes = f.read()
    st, body, _ = multipart_upload("/upload", "clip4s.mp4", clip_bytes)
    ok = st == 200
    up = json.loads(body).get("path", "") if ok else ""
    check("M8 multipart upload", ok and os.path.exists(up)
          and hashlib.sha256(open(up, 'rb').read()).hexdigest() == hashlib.sha256(clip_bytes).hexdigest(),
          f"HTTP {st} {up}")
    if ok:
        st, body, _ = http("POST", "/dl_token", {"path": up})
        ok = st == 200
        tok = json.loads(body).get("token", "") if ok else ""
        check("M8 dl_token minted", ok and tok.count(".") == 1, f"HTTP {st}")
        if ok:
            st, body, hdrs = http("GET", f"/dl/{tok}")
            check("M8 dl fetches file", st == 200 and body == clip_bytes,
                  f"HTTP {st} len={len(body)}")
            st, body, hdrs = http("GET", f"/dl/{tok}",
                                  headers={"Range": "bytes=0-99"})
            check("M8 dl Range -> 206", st == 206 and len(body) == 100,
                  f"HTTP {st} len={len(body)}")
            # tampered token -> 404
            bad = tok[:-4] + ("AAAA" if not tok.endswith("AAAA") else "BBBB")
            st, _, _ = http("GET", f"/dl/{bad}")
            check("M8 tampered token -> 404", st == 404, f"HTTP {st}")
            # expired token -> 404
            st, body, _ = http("POST", "/dl_token", {"path": up, "ttl_hours": 1 / 3600})
            tok2 = json.loads(body)["token"]
            time.sleep(2)
            st, _, _ = http("GET", f"/dl/{tok2}")
            check("M8 expired token -> 404", st == 404, f"HTTP {st}")
    st, body, _ = http("POST", "/dl_token", {"path": "/etc/passwd"})
    check("M8 dl_token rejects outside media_jobs", st == 404, f"HTTP {st}")
    st, body, _ = http("POST", "/dl_token", {"path": clip4s, "ttl_hours": 999})
    check("M8 ttl cap enforced", st == 400, f"HTTP {st}")

    # ---------------------------------------------------------------- summary
    log("=== summary ===")
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    for name, ok, detail in RESULTS:
        if not ok:
            log(f"  FAILED: {name} {detail}")
    log(f"{passed}/{len(RESULTS)} passed")
    sys.exit(0 if passed == len(RESULTS) else 1)


if __name__ == "__main__":
    main()