#!/usr/bin/env python3
"""Phase 3 E2E + LTXV regression for the wan14b shot model (wan_shots_todo.md §5).

Full pipeline path, direct API (127.0.0.1:8189):
  storyboard (2 shots) -> 2x /images (qwen21 keyframes)
  -> LTXV regression shot (model=ltxv2b, 768x512, 97f — path intact?)
  -> 2x /shots model=wan14b (832x480, 81f @16fps)
  -> /tts (storyboard VO) + /music
  -> /assemble (upscale_each=true SeedVR2 -> 1080p, VO + music, loudnorm)
  -> /info on the final mp4.

Sequential (pipeline queue is 1-at-a-time anyway). ~45 min wall.

Usage: python3 qa_wan_e2e.py
"""
import json
import time
import urllib.parse
import urllib.request
import uuid

BASE = "http://127.0.0.1:8189"
BRIEF = ("Cinematic 10-second commercial for artisan coffee: a slow pour of hot "
         "water over fresh grounds in a ceramic pour-over, then a close-up of the "
         "finished cup on a rustic wooden windowsill in warm morning light. "
         "Photorealistic, shallow depth of field, warm tones.")
USER, CLIENT = "chuck", "qa-e2e"
report = {}


def post_json(path: str, payload: dict) -> str:
    payload = {**payload, "user": USER, "client": CLIENT}
    req = urllib.request.Request(
        BASE + path, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())["job_id"]


def post_multipart(path: str, file_path: str, fields: dict) -> str:
    """Multipart POST: 'file' upload + string fields (the /shots shape)."""
    boundary = uuid.uuid4().hex
    body = b""
    with open(file_path, "rb") as f:
        data = f.read()
    body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
             f"filename=\"{file_path.split('/')[-1]}\"\r\n"
             f"Content-Type: application/octet-stream\r\n\r\n").encode() + data + b"\r\n"
    for k, v in fields.items():
        body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n"
                 f"{v}\r\n").encode()
    body += f"--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        BASE + path, data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())["job_id"]


def run(job_id: str, what: str, timeout_s: float = 9000) -> dict:
    t0 = time.time()
    while True:
        with urllib.request.urlopen(BASE + f"/jobs/{job_id}", timeout=30) as r:
            j = json.loads(r.read())
        if j["status"] in ("done", "error", "timeout"):
            dt = time.time() - t0
            print(f"[{what}] {j['status']} in {dt:.0f}s", flush=True)
            if j["status"] != "done":
                raise RuntimeError(f"{what} failed: {j.get('error')}")
            return j
        if time.time() - t0 > timeout_s:
            raise TimeoutError(f"{what} timed out after {timeout_s}s (last: {j['status']})")
        time.sleep(5)


def main():
    # 1. storyboard
    j = run(post_json("/storyboard", {"brief": BRIEF, "n_shots": 2, "aspect": "16:9"}),
            "storyboard")
    sb = json.load(open(f"/home/chuck/data/comfyui/run/media_jobs/{j['id']}/storyboard.json"))
    shots = sb["shots"]
    report["storyboard"] = shots
    print(json.dumps(shots, indent=2), flush=True)

    # 2. keyframes (qwen21)
    images = []
    for i, s in enumerate(shots, 1):
        j = run(post_json("/images", {"prompt": s["visual"], "width": 1280, "height": 720,
                                      "seed": 42}), f"image {i}")
        images.append(j["output"]["image"])
        print(f"  image {i}: {j['output']['image']}", flush=True)
    report["images"] = images

    # 3. LTXV regression (explicit model=ltxv2b — path must be intact)
    # (image paths from /images are absolute host paths under RUN_DIR)
    j = run(post_multipart("/shots", images[0],
                           {"prompt": shots[0]["visual"], "model": "ltxv2b",
                            "width": "768", "height": "512", "frames": "97",
                            "fps": "24", "seed": "42"}), "ltxv regression")
    report["ltxv_regression"] = j["output"]
    print(f"  ltxv: {j['output']}", flush=True)

    # 4. wan14b shots
    wan_videos = []
    for i, s in enumerate(shots, 1):
        j = run(post_multipart("/shots", images[i-1],
                               {"prompt": s["visual"], "model": "wan14b",
                                "width": "832", "height": "480", "frames": "81",
                                "fps": "16", "seed": "42"}), f"wan shot {i}", timeout_s=9000)
        wan_videos.append(j["output"]["video"])
        print(f"  wan {i}: {j['output']['video']}", flush=True)
    report["wan_shots"] = wan_videos

    # 5. VO + music
    vo_text = " ".join(s.get("vo", "") for s in shots).strip()
    vo_path = None
    if vo_text:
        j = run(post_json("/tts", {"text": vo_text, "voice": "trailer"}), "tts")
        vo_path = j["output"]["audio"]
        print(f"  vo: {vo_path}", flush=True)
    report["vo"] = vo_path
    j = run(post_json("/music", {"prompt": "warm cinematic ambient, soft piano and strings, "
                                           "hopeful, slow build", "duration": 25}), "music")
    music_path = j["output"]["audio"]
    print(f"  music: {music_path}", flush=True)
    report["music"] = music_path

    # 6. assemble: SeedVR2-upscale each wan shot -> 1080p, mix VO + music
    payload = {"shots": wan_videos, "upscale_each": True, "upscale_resolution": 1080,
               "width": 1920, "height": 1080, "fps": 24, "loudnorm": True,
               "music": music_path, "music_volume": 0.35}
    if vo_path:
        payload["vo"] = vo_path
    j = run(post_json("/assemble", payload), "assemble (upscale_each)", timeout_s=9000)
    final = j["output"]["video"]
    report["final"] = final
    print(f"  final: {final}", flush=True)

    # 7. probe
    with urllib.request.urlopen(BASE + "/info?" + urllib.parse.urlencode({"path": final}),
                                timeout=30) as r:
        info = json.loads(r.read())
    report["final_info"] = info
    print(json.dumps(info, indent=2), flush=True)

    with open("/home/chuck/homelab/media-pipeline/qa_wan_e2e_report.json", "w") as f:
        json.dump(report, f, indent=2)
    print("E2E DONE — report: media-pipeline/qa_wan_e2e_report.json", flush=True)


if __name__ == "__main__":
    main()