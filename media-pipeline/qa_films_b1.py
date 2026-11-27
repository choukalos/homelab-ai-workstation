#!/usr/bin/env python3
"""Films B1 QA — project store + NAS mirror + storyboard pages.

Hits the live API (127.0.0.1:8189) + verifies on-disk state (host paths).
No GPU work except the regression /images job (qwen21, small).

Usage: python3 qa_films_b1.py
Exit 0 = all pass.
"""
import json
import re
import shutil
import subprocess
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

BASE = "http://192.168.4.55:8189"  # Phase 17 LAN-only bind (not 127.0.0.1)
SCRATCH = Path("/home/chuck/workspace/films-plan/state")
# ffmpeg runs inside comfyui_backend; it only sees RUN_DIR (-> /comfy/mnt).
# Keyframe scratch must live under RUN_DIR for the container to write it.
RUN = Path("/home/chuck/data/comfyui/run")
KF_SCRATCH = RUN / "media_jobs" / "qa_b1_kf"
NAS = Path("/mnt/lego/media_projects")
CONTAINER = "comfyui_backend"

SLUG = f"qa_films_b1_{time.strftime('%H%M%S')}"
PROJ = RUN / "media_projects" / SLUG

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  [{detail}]" if detail and not cond else ""))
    return bool(cond)


def req(method, path, body=None, raw=False):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method,
                                headers={"Content-Type": "application/json"} if data else {})
    try:
        with urllib.request.urlopen(r, timeout=600) as resp:
            return resp.status, json.loads(resp.read()) if not raw else resp.read()
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read())
        except Exception:
            return e.code, {}


def upload(path, field, file_path, extra=None):
    """multipart upload via curl (python's multipart is a pain)."""
    cmd = ["curl", "-s", "-w", "\n%{http_code}", "-X", "POST", f"{BASE}{path}",
           "-F", f"{field}=@{file_path}"]
    for k, v in (extra or {}).items():
        cmd += ["-F", f"{k}={v}"]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    out = r.stdout.strip()
    body, _, code = out.rpartition("\n")
    try:
        return int(code), json.loads(body) if body else {}
    except Exception:
        return (int(code) if code.isdigit() else 0), {"raw": body}


def poll(jid, timeout=900):
    t0 = time.time()
    while time.time() - t0 < timeout:
        st, j = req("GET", f"/jobs/{jid}")
        if j.get("status") in ("done", "error", "timeout", "killed"):
            return j
        time.sleep(3)
    raise TimeoutError(f"job {jid} not finished in {timeout}s")


def ffmpeg(args, timeout=300):
    r = subprocess.run(["docker", "exec", "-u", "comfy", CONTAINER, "ffmpeg"] + args,
                       capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {r.stderr[-500:]}")
    return r


def root_exec(cmd: str):
    """Run a shell command as root in the media_pipeline container (RUN_DIR
    and media_projects are not writable by the host user)."""
    r = subprocess.run(["docker", "exec", "-u", "root", "media_pipeline", "bash", "-c", cmd],
                       capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise RuntimeError(f"root exec failed: {r.stderr[-300:]}")
    return r


def shotplan(n=7):
    """A valid two-tier shotplan with `n` micro_shots (2 beats)."""
    beats, per = [], (n + 1) // 2
    for bi in range(2):
        ms = []
        for si in range(per if bi == 0 else n - per):
            sid = f"s01_b{bi+1:02d}_sh{si+1:02d}"
            cat = "action_impact" if (bi + si) % 3 == 2 else "dialogue_drama"
            ms.append({
                "shot_id": sid,
                "shot_category": cat,
                "risk_level": "high" if cat == "action_impact" else "low",
                "shot_type": "medium_close_up" if cat == "dialogue_drama" else "wide",
                "character_refs": ["char_hero", "char_villain"],
                "expression_anchor": "narrowed eyes, tense jaw",
                "duration_frames": 49 if cat == "action_impact" else 81,
                "chain_from_prev_shot": False,
                "use_end_frame_anchor": False,
                # one motion verb on purpose -> schema_warning, not a failure
                "keyframe_prompt": (f"static composition, {sid}: the hero stands in the kitchen"
                                    if (bi + si) % 2 else
                                    f"static composition, {sid}: the hero runs to the door"),
                "motion_prompt": "slow camera push-in, ambient motion only, mouth closed",
                "animatic_move": "push_in" if (bi + si) % 2 else "static",
                "audio": {"voice_id": "trailer", "dialogue_text": "You cannot win.",
                          "lip_sync_required": False},
            })
        beats.append({"beat_id": f"beat_{bi+1:02d}",
                      "narrative_goal": f"beat {bi+1} goal",
                      "micro_shots": ms})
    return {
        "scene_id": "scene_01",
        "screen_direction_lock": "Hero: screen-left facing right | Villain: screen-right facing left",
        "environment_plate_id": "env_kitchen_night",
        "color_grade_lut": "luts/cinematic_teal_amber.cube",
        "beats": beats,
    }


def main():
    SCRATCH.mkdir(parents=True, exist_ok=True)
    print(f"== Films B1 QA — project {SLUG}")

    # 0. health
    st, h = req("GET", "/health")
    check("health", st == 200 and h.get("ok"), f"{st} {h}")
    check("features include projects", "projects" in h.get("features", []), str(h.get("features")))

    # 1. create
    st, r = req("POST", "/projects", {"slug": SLUG, "title": "QA Films B1 Test Film"})
    check("create project", st == 200 and r.get("status") == "draft", f"{st} {r}")
    st, r = req("POST", "/projects", {"slug": SLUG, "title": "dup"})
    check("duplicate -> 409", st == 409, f"{st} {r}")
    st, r = req("POST", "/projects", {"slug": "BAD SLUG!", "title": "x"})
    check("bad slug -> 400", st == 400, f"{st} {r}")
    check("project dir on disk", (PROJ / "project.json").is_file(), str(PROJ))
    check("layout dirs", all((PROJ / s).is_dir() for s in
          ("bible/characters", "storyboard/keyframes", "storyboard/pages",
           "production/clips", "production/audio", "production/final")), str(list(PROJ.iterdir())))

    # 2. assets
    kf_dir = KF_SCRATCH
    root_exec(f"mkdir -p {kf_dir} && chmod 777 {kf_dir}")
    colors = ["0xff5533", "0x33ff55", "0x3355ff", "0xff33ff", "0x33ffff", "0xffff33"]
    for i, c in enumerate(colors):
        p = kf_dir / f"asset_{i}.png"
        ffmpeg(["-y", "-f", "lavfi", "-i", f"color=c={c}:s=640x360", "-frames:v", "1",
                "/comfy/mnt/" + str(p)[len(str(RUN)) + 1:]])
    st, r = upload(f"/projects/{SLUG}/assets", "file", kf_dir / "asset_0.png",
                   {"path": "bible/characters/char_hero/turnaround.png"})
    check("upload char sheet", st == 200 and (PROJ / "bible/characters/char_hero/turnaround.png").is_file(),
          f"{st} {r}")
    st, r = upload(f"/projects/{SLUG}/assets", "file", kf_dir / "asset_1.png",
                   {"path": "bible/environments/env_kitchen_night/plate_wide.png"})
    check("upload env plate", st == 200, f"{st} {r}")
    st, r = upload(f"/projects/{SLUG}/assets", "file", kf_dir / "asset_2.png",
                   {"path": "../evil.png"})
    check("path traversal -> 400", st == 400, f"{st} {r}")

    # 3. shotplan
    sp = shotplan(7)
    st, r = req("POST", f"/projects/{SLUG}/shotplan", {"shotplan": sp})
    check("save valid shotplan", st == 200 and r.get("warnings"), f"{st} {r}")
    check("motion-verb warning captured",
          any("runs" in w.get("warning", "") for w in r.get("warnings", [])), str(r.get("warnings")))
    st, r = req("GET", f"/projects/{SLUG}/shotplan")
    check("fetch shotplan", st == 200 and r.get("scene_id") == "scene_01"
          and r.get("schema_warnings"), f"{st}")

    bad = []
    a = json.loads(json.dumps(sp))
    for ms in a["beats"][0]["micro_shots"]:
        ms["shot_category"] = "close_up_dance"
        break
    bad.append(("bad enum", a))
    b = json.loads(json.dumps(sp))
    b["beats"][0]["micro_shots"][0]["duration_frames"] = 80
    bad.append(("frames not 4k+1", b))
    c = json.loads(json.dumps(sp))
    for ms in c["beats"][0]["micro_shots"]:
        if ms["shot_category"] != "action_impact":
            ms["shot_category"] = "action_impact"
            ms["duration_frames"] = 81
            break
    bad.append(("action_impact >49f", c))
    d = json.loads(json.dumps(sp))
    d["beats"][1]["micro_shots"][0]["shot_id"] = d["beats"][0]["micro_shots"][0]["shot_id"]
    bad.append(("duplicate shot_id", d))
    for name, plan in bad:
        st, r = req("POST", f"/projects/{SLUG}/shotplan", {"shotplan": plan})
        check(f"invalid shotplan ({name}) -> 400", st == 400 and "validation" in str(r), f"{st} {r}")
    # re-save the good plan (the invalid ones must not have clobbered it)
    st, r = req("POST", f"/projects/{SLUG}/shotplan", {"shotplan": sp})
    check("re-save good shotplan", st == 200, f"{st} {r}")

    # 4. keyframes for 6 of 7 shots (leave one missing -> placeholder cell)
    sids = [m["shot_id"] for b in sp["beats"] for m in b["micro_shots"]]
    for i, sid in enumerate(sids[:6]):
        st, r = upload(f"/projects/{SLUG}/assets", "file", kf_dir / f"asset_{i}.png",
                       {"path": f"storyboard/keyframes/{sid}.png"})
        if st != 200:
            check(f"keyframe {sid}", False, f"{st} {r}")
            break
    else:
        check("6 keyframes uploaded", True)

    # 5. storyboard pages
    st, r = req("POST", f"/projects/{SLUG}/storyboard_pages", {"per_page": 6})
    check("storyboard_pages", st == 200 and r.get("n_pages") == 2 and len(r.get("pages", [])) == 2,
          f"{st} {r}")
    ok = True
    for i, p in enumerate(r.get("pages", [])):
        pp = Path(p)
        if not pp.is_file():
            ok = False
            continue
        st2, info = req("GET", f"/info?path={pp}")
        want = (3 * 640, 2 * 360 + 28) if i == 0 else (640, 360 + 28)
        if not (st2 == 200 and (info.get("width"), info.get("height")) == want):
            ok = False
    check("page PNGs exist with grid dims (1920x748 / 640x388)", ok)

    # 6. approve
    st, r = req("POST", f"/projects/{SLUG}/approve", {"what": "storyboard", "note": "QA approval", "by": "qa"})
    check("approve storyboard", st == 200 and r.get("status") == "storyboard_approved", f"{st} {r}")
    st, r = req("GET", f"/projects/{SLUG}")
    check("GET shows approved + approval ts",
          st == 200 and r.get("status") == "storyboard_approved"
          and r.get("approval", {}).get("storyboard") and r.get("approval", {}).get("by") == "qa",
          f"{st} {r.get('status')}")
    st, r = req("POST", f"/projects/{SLUG}/approve", {"what": "bogus"})
    check("approve bogus what -> 400", st == 400, f"{st} {r}")

    # 7. NAS sync
    st, r = req("POST", f"/projects/{SLUG}/sync")
    check("sync ok", st == 200 and r.get("ok") and r.get("files", 0) > 0, f"{st} {r}")
    nas_p = NAS / SLUG
    check("files present on NAS", (nas_p / "project.json").is_file()
          and (nas_p / "storyboard" / "shotplan.json").is_file()
          and (nas_p / "storyboard" / "pages" / "page_01.png").is_file(), str(nas_p))
    st, r = req("GET", f"/projects/{SLUG}")
    check("nas_sync state ok", r.get("nas_sync", {}).get("ok") is True
          and r.get("nas_sync", {}).get("pending") is False, str(r.get("nas_sync")))

    # 8. NAS failure path: point MEDIA_NAS_ROOT at a non-CIFS path (container
    #    /tmp) so the df-source mount probe reports "not mounted" -> pending.
    #    (No sudo on matrix: a live `umount /mnt/lego` isn't possible; this
    #    exercises the same probe + pending + retry path end-to-end.)
    env_file = Path("/home/chuck/homelab/.env")
    env_backup = env_file.read_text()
    env_file.write_text(env_backup + "\nMEDIA_NAS_ROOT=/tmp/media_nas_test\n")
    subprocess.run(["docker", "restart", "media_pipeline"], capture_output=True, timeout=120)
    for _ in range(24):
        try:
            st, h = req("GET", "/health")
            if st == 200:
                break
        except Exception:
            pass
        time.sleep(5)
    check("service back after restart", st == 200 and h.get("ok"))
    # mutation while NAS probe fails: upload a fresh asset
    st, r = upload(f"/projects/{SLUG}/assets", "file", kf_dir / "asset_5.png",
                   {"path": "bible/voices/qa_ref.png"})
    check("mutation succeeds while NAS broken", st == 200, f"{st} {r}")
    st, r = req("GET", f"/projects/{SLUG}")
    check("nas_sync.pending set", r.get("nas_sync", {}).get("pending") is True,
          str(r.get("nas_sync")))
    st, r = req("POST", f"/projects/{SLUG}/sync")
    check("forced sync reports failure+pending", st == 200 and r.get("ok") is False
          and r.get("pending") is True and "not mounted" in str(r.get("reason", "")),
          f"{st} {r}")
    env_file.write_text(env_backup)  # restore
    subprocess.run(["docker", "restart", "media_pipeline"], capture_output=True, timeout=120)
    for _ in range(24):
        try:
            st, h = req("GET", "/health")
            if st == 200:
                break
        except Exception:
            pass
        time.sleep(5)
    st, r = req("POST", f"/projects/{SLUG}/sync")
    check("sync recovers after fix", st == 200 and r.get("ok") is True
          and r.get("pending") is False, f"{st} {r}")

    # df-source probe evidence (the mount detector)
    r = subprocess.run(["df", "--output=source", "/mnt/lego"], capture_output=True, text=True)
    check("df source is CIFS share", "//" in r.stdout, r.stdout.strip())

    # 9. regression: existing flow untouched
    st, r = req("POST", "/images", {"prompt": "a red apple on a wooden table, studio light",
                                    "width": 512, "height": 512, "steps": 10, "seed": 42,
                                    "client": "qa_films_b1"})
    check("regression: /images submit", st == 200 and r.get("job_id"), f"{st} {r}")
    j = poll(r["job_id"], timeout=900)
    check("regression: /images done", j.get("status") == "done" and j.get("output", {}).get("image"),
          f"{j.get('status')} {j.get('error')}")

    # summary
    fails = [n for n, ok, _ in results if not ok]
    print(f"\n== B1 QA: {len(results) - len(fails)}/{len(results)} passed")
    if fails:
        print("FAILED:", fails)
        return 1
    # cleanup (root-owned dirs via the container)
    root_exec(f"rm -rf {PROJ} {NAS / SLUG} {KF_SCRATCH}")
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
