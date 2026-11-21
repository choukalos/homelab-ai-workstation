#!/usr/bin/env python3
"""M1–M5 acceptance tests (matrix_media_pipeline_improvements_handoff.md).

Runs against the live media-pipeline on 127.0.0.1:8189 (host-side; stdlib only).
Creates its own test clips (ffmpeg in the comfyui container) under
/home/chuck/data/comfyui/run/qa_m1m5/. Results: PASS/FAIL per check + summary.

Usage: python3 qa_m1_m5.py [--only m3|m1|m4|m5|m2]
  --only <section>  run just one section (m2 includes the M3.2 queued check;
                    m2 is the long one — SeedVR2 upscale slow job + restart)
"""
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8189"
RUN = "/home/chuck/data/comfyui/run"
QA = "qa_m1m5"
CLIENT = "qa-m1m5"
COMFY = "comfyui_backend"

results = []


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""), flush=True)


def http(method, path, body=None, raw_body=None, ctype="application/json", timeout=120):
    data = raw_body if raw_body is not None else (
        json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Content-Type": ctype} if data else {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.load(e)
        except Exception:
            return e.code, {}


def probe_s(path_host):
    """Duration of a clip (host path) via ffprobe in the comfyui container."""
    cont = path_host.replace(RUN, "/comfy/mnt")
    r = subprocess.run(
        ["docker", "exec", COMFY, "ffprobe", "-v", "error",
         "-show_entries", "format=duration", "-of", "csv=p=0", cont],
        capture_output=True, text=True, timeout=60)
    return float(r.stdout.strip())


def make_clip(name, seconds):
    host = f"{RUN}/{QA}/{name}"
    cont = f"/comfy/mnt/{QA}/{name}"
    subprocess.run(["docker", "exec", "-u", "comfy", COMFY, "bash", "-c",
                    f"mkdir -p /comfy/mnt/{QA}"], capture_output=True, timeout=30)
    r = subprocess.run(
        ["docker", "exec", "-u", "comfy", COMFY, "ffmpeg", "-y", "-v", "error",
         "-f", "lavfi", f"-i", f"testsrc=duration={seconds}:size=640x360:rate=24",
         "-c:v", "libx264", "-preset", "fast", "-pix_fmt", "yuv420p", cont],
        capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise RuntimeError(f"clip gen failed: {r.stderr[-300:]}")
    return f"{QA}/{name}"


def submit_assemble(shots, client=CLIENT, **kw):
    payload = {"shots": shots, "width": 640, "height": 360, "fps": 24,
               "client": client}
    payload.update(kw)
    st, d = http("POST", "/assemble", body=payload)
    assert st == 200, f"assemble submit {st}: {d}"
    return d["job_id"]


def wait_job(jid, timeout=1800):
    t0 = time.time()
    while time.time() - t0 < timeout:
        st, j = http("GET", f"/jobs/{jid}")
        if st == 200 and j.get("status") in ("done", "error", "timeout", "killed"):
            return j
        time.sleep(1)
    raise TimeoutError(f"job {jid} not terminal in {timeout}s")


def wait_terminal(jid):
    st, j = http("GET", f"/jobs/{jid}")
    return st == 200 and j.get("status") in ("done", "error", "timeout", "killed")


def submit_trim(client=CLIENT, duration=2.0, src=f"{QA}/clip6.mp4"):
    st, d = http("POST", "/trim", body={"source": src, "duration": duration,
                                        "client": client})
    assert st == 200, f"trim submit {st}: {d}"
    return d["job_id"]


def submit_upscale(client=CLIENT):
    """Multipart file upload via curl (python-multipart rejects hand-built bodies)."""
    r = subprocess.run(
        ["curl", "-s", "-X", "POST", f"{BASE}/upscale",
         f"-F", f'file=@{RUN}/{QA}/clip6.mp4;type=video/mp4',
         "-F", "pipeline=b", "-F", f"client={client}"],
        capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise RuntimeError(f"upscale upload failed: {r.stderr[-300:]}")
    return json.loads(r.stdout)["job_id"]


# ------------------------------------------------------------------ sections
def sec_m3_idle():
    st, h = http("GET", "/health")
    q = h.get("queue", {})
    check("M3.1 idle queue", st == 200 and q.get("counts") == {"running": 0, "queued": 0}
          and q.get("oldest_queued") is None and q.get("queued_jobs") == [],
          f"counts={q.get('counts')}")
    legacy = {"ok", "gpu_locked", "jobs", "max_concurrent", "max_queue_depth",
              "max_pending", "pending", "running", "queued", "queue_depth",
              "retention_days"}
    check("M3.3 legacy /health fields intact", legacy <= set(h),
          f"missing={legacy - set(h)}")
    check("M3 features list", h.get("features") ==
          ["assemble-duration-hold", "job-kill", "queue-summary", "job-progress"],
          str(h.get("features")))


def sec_m1():
    # 1. Hold: 2× 6.0 s clips, duration 9 → 18.0 s, both hold, held_s 3.0
    jid = submit_assemble([{"path": f"{QA}/clip6.mp4", "duration": 9},
                           {"path": f"{QA}/clip6.mp4", "duration": 9}])
    j = wait_job(jid)
    dur = probe_s(f"{RUN}/media_jobs/{jid}/final.mp4") if j["status"] == "done" else -1
    adj = (j.get("output") or {}).get("duration_adjustments")
    check("M1.1 hold (9s × 2 → 18.0s)", j["status"] == "done" and abs(dur - 18.0) <= 0.15
          and adj and len(adj) == 2
          and all(a["applied"] == "hold" and abs(a["held_s"] - 3.0) < 0.05 for a in adj),
          f"job={jid} dur={dur:.3f}s adj={adj}")

    # 2. Trim: 2× 6.0 s clips, duration 4 → 8.0 s, both trim
    jid = submit_assemble([{"path": f"{QA}/clip6.mp4", "duration": 4},
                           {"path": f"{QA}/clip6.mp4", "duration": 4}])
    j = wait_job(jid)
    dur = probe_s(f"{RUN}/media_jobs/{jid}/final.mp4") if j["status"] == "done" else -1
    adj = (j.get("output") or {}).get("duration_adjustments")
    check("M1.2 trim (4s × 2 → 8.0s)", j["status"] == "done" and abs(dur - 8.0) <= 0.15
          and adj and all(a["applied"] == "trim" for a in adj),
          f"job={jid} dur={dur:.3f}s adj={adj}")

    # 3. No-op: 6.0 s clip, duration 6.05 → applied none
    jid = submit_assemble([{"path": f"{QA}/clip6.mp4", "duration": 6.05}])
    j = wait_job(jid)
    dur = probe_s(f"{RUN}/media_jobs/{jid}/final.mp4") if j["status"] == "done" else -1
    adj = (j.get("output") or {}).get("duration_adjustments")
    check("M1.3 no-op (6.05s → none)", j["status"] == "done" and abs(dur - 6.0) <= 0.15
          and adj and adj[0]["applied"] == "none",
          f"job={jid} dur={dur:.3f}s adj={adj}")

    # 4. Regression: plain string shots, no duration → natural concat, no field
    jid = submit_assemble([f"{QA}/clip6.mp4", f"{QA}/clip6.mp4"])
    j = wait_job(jid)
    dur = probe_s(f"{RUN}/media_jobs/{jid}/final.mp4") if j["status"] == "done" else -1
    out = j.get("output") or {}
    check("M1.4 regression (string shots → 12.0s, no adjustments field)",
          j["status"] == "done" and abs(dur - 12.0) <= 0.15
          and "duration_adjustments" not in out,
          f"job={jid} dur={dur:.3f}s keys={sorted(out)}")


def sec_m4():
    jids = [submit_trim() for _ in range(6)]
    saw_consistent = False
    t0 = time.time()
    while time.time() - t0 < 600:
        _, h = http("GET", "/health")
        _, jr = http("GET", "/jobs?status=running&limit=20")
        _, jq = http("GET", "/jobs?status=queued&limit=20")
        run_ids = [x["job_id"] for x in jr["jobs"]]
        que_ids = [x["job_id"] for x in jq["jobs"]]
        if run_ids and set(run_ids) == set(h["running"]) \
                and set(que_ids) == set(h["queued"]) \
                and h["pending"] == h["queue"]["counts"]["running"] + h["queue"]["counts"]["queued"]:
            saw_consistent = True
        if all(wait_terminal(x) for x in jids):
            break
        time.sleep(1)
    final = [wait_job(x) for x in jids]
    check("M4.1 status=running/queued == /health IDs (6 trims through the queue)",
          saw_consistent and all(f["status"] == "done" for f in final),
          f"consistent_seen={saw_consistent} statuses={[f['status'] for f in final]}")


def sec_m5():
    jid = submit_assemble([f"{QA}/clip2.mp4"] * 6)
    samples = []
    t0 = time.time()
    while time.time() - t0 < 600:
        st, j = http("GET", f"/jobs/{jid}")
        p = j.get("progress")
        if p:
            samples.append((p.get("stage"), p.get("completed"), p.get("total")))
        if j.get("status") in ("done", "error", "timeout"):
            break
        time.sleep(0.5)
    comp = [s[1] for s in samples if s[1] is not None]
    increasing = any(b > a for a, b in zip(comp, comp[1:]))
    term = (j.get("output") or {}).get("progress")
    _, rows = http("GET", f"/jobs?client={CLIENT}&limit=200")
    ledger = next((r for r in rows["jobs"] if r["job_id"] == jid), {})
    ledger_prog = (ledger.get("output") or {}).get("progress")
    check("M5.1 in-flight progress increases toward total",
          len(samples) >= 2 and increasing and samples[-1][2] == 6,
          f"job={jid} samples={samples[:3]}...{samples[-2:]}")
    check("M5.2 terminal progress in result + ledger row",
          term == {"stage": "done", "completed": 6, "total": 6}
          and ledger_prog == term,
          f"result={term} ledger={ledger_prog}")


def sec_m2():
    a = submit_upscale()                       # slow: SeedVR2 on GPU 1
    # wait until A is actually running (GPU work in progress)
    t0 = time.time()
    while time.time() - t0 < 300:
        _, h = http("GET", "/health")
        if a in h["running"]:
            break
        if wait_terminal(a):
            raise SystemExit(f"upscale job {a} died before running — "
                             f"see GET /jobs?client={CLIENT}")
        time.sleep(2)
    b = submit_trim()                          # queues behind A
    time.sleep(1)
    _, h = http("GET", "/health")
    qj = h["queue"]["queued_jobs"]
    check("M3.2 one queued: ID/flow/client/waited_s + pending invariant",
          h["queued"] == [b] and qj and qj[0]["job_id"] == b
          and qj[0]["flow"] == "trim" and qj[0]["client"] == CLIENT
          and isinstance(qj[0]["waited_s"], (int, float))
          and h["pending"] == h["queue"]["counts"]["running"] + h["queue"]["counts"]["queued"],
          f"queued_jobs={qj} pending={h['pending']}")
    st, d = http("DELETE", f"/jobs/{b}")
    check("M2.1 kill queued → slot freed", st == 200 and d.get("killed") is True
          and d.get("status") == "killed" and d.get("queue_position_freed") == 0,
          f"job={b} resp={d}")
    _, h = http("GET", "/health")
    check("M2.1b queue drained after kill",
          h["queued"] == [] and h["queue"]["oldest_queued"] is None,
          f"queued={h['queued']}")
    st, d = http("DELETE", f"/jobs/{a}")
    det = d.get("detail", d)  # FastAPI wraps HTTPException bodies in `detail`
    check("M2.2 kill running → 409", st == 409 and det.get("status") == "running"
          and "queued-only" in str(det.get("error", "")), f"resp={d}")
    st, d = http("DELETE", f"/jobs/{b}")
    check("M2.3 re-kill killed → 200 no-op", st == 200 and d.get("killed") is False
          and d.get("status") == "killed", f"resp={d}")
    st, d = http("DELETE", "/jobs/deadbeef0000")
    check("M2.4 unknown id → 404 + hint", st == 404 and "GET /jobs" in json.dumps(d),
          f"resp={d}")
    c = submit_trim()                          # tagged, queued behind A
    time.sleep(1)
    st, d = http("DELETE", f"/jobs/{c}?client=wrong-tag")
    check("M2.5a client mismatch → 403", st == 403, f"resp={d}")
    st, d = http("DELETE", f"/jobs/{c}?client={CLIENT}")
    check("M2.5b matching client → killed", st == 200 and d.get("killed") is True,
          f"job={c} resp={d}")
    ja = wait_job(a, timeout=3600)
    check("M2.6a running job completes (was not killed)", ja["status"] == "done",
          f"job={a} status={ja['status']}")
    subprocess.run(["docker", "restart", "media_pipeline"], capture_output=True, timeout=300)
    for _ in range(60):
        try:
            st, _ = http("GET", "/health", timeout=5)
            if st == 200:
                break
        except Exception:
            pass
        time.sleep(2)
    _, rows = http("GET", "/jobs?status=killed&limit=100")
    killed_ids = {r["job_id"] for r in rows["jobs"]}
    check("M2.6b killed jobs survive restart (ledger)", {b, c} <= killed_ids,
          f"killed={killed_ids}")


SECTIONS = {"m3": sec_m3_idle, "m1": sec_m1, "m4": sec_m4, "m5": sec_m5,
            "m2": sec_m2}


def main():
    only = None
    if "--only" in sys.argv:
        only = sys.argv[sys.argv.index("--only") + 1]
        assert only in SECTIONS, f"--only must be one of {list(SECTIONS)}"
    make_clip("clip6.mp4", 6)
    make_clip("clip2.mp4", 2)
    for name, fn in SECTIONS.items():
        if only in (None, name):
            fn()
    fails = [r for r in results if not r[1]]
    print(f"\n{'=' * 60}\nSUMMARY: {len(results) - len(fails)}/{len(results)} passed")
    for name, ok, detail in fails:
        print(f"  FAIL: {name} — {detail}")
    print(f"ledger client tag: {CLIENT} (GET /jobs?client={CLIENT})")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
