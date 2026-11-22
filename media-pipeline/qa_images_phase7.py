#!/usr/bin/env python3
"""Phase 7 T2/T3 QA (2026-11-22): LTXV 720p fast lane + Qwen-Image-2512 3-way
keyframe comparison (qwen21 vs 2512-Q4_0 vs 2512-Q6_K) at 1280x720.

Hits ComfyUI directly (like qa_wan_phase1.py) so no pipeline-container env
changes are needed for the Q6 candidate. nvidia-smi 1 Hz sampling for peaks.
Results JSON -> stdout. GPU: 1 (default).
"""
import copy
import json
import os
import subprocess
import sys
import threading
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import workflows as wf

COMFY = "http://127.0.0.1:8188"
GPU = int(os.environ.get("QA_GPU", "1"))
KEYFRAME = "c1_k11_edited.png"
PROMPT = ("Photorealistic cinematic keyframe, medium shot of a Pomeranian dog "
          "sitting in a sunlit living room, soft natural window light, shallow "
          "depth of field, 35mm film look, high detail, sharp focus.")
SEED = 42


def api(path, payload=None):
    req = urllib.request.Request(COMFY + path,
                                 data=json.dumps(payload).encode() if payload is not None else None,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def vram_sampler(stop, samples):
    while not stop.wait(1.0):
        try:
            out = subprocess.run(["nvidia-smi", "-i", str(GPU), "--query-gpu=memory.used",
                                  "--format=csv,noheader,nounits"],
                                 capture_output=True, text=True, timeout=5).stdout.strip()
            samples.append(int(out))
        except Exception:
            pass


def run(tag, graph, timeout=1800):
    stop = threading.Event()
    samples = []
    t = threading.Thread(target=vram_sampler, args=(stop, samples))
    t.start()
    try:
        pid = api("/prompt", {"prompt": graph, "client_id": f"qa-p7-{tag}"})["prompt_id"]
        t0 = time.time()
        while True:
            hist = api("/history/" + pid)
            if pid in hist:
                wall = time.time() - t0
                out = hist[pid].get("outputs", {})
                files = []
                for o in out.values():
                    files += [f.get("filename") for f in o.get("images", []) if f.get("filename")]
                    files += [f.get("filename") for f in o.get("gifs", []) if f.get("filename")]
                peak = max(samples) if samples else None
                return {"tag": tag, "status": "success", "wall_s": round(wall, 1),
                        "peak_mib": peak, "peak_delta_mib": (peak - samples[0]) if peak and samples else None,
                        "output": files[0] if files else None}
            if time.time() - t0 > timeout:
                return {"tag": tag, "status": "timeout", "wall_s": round(time.time() - t0, 1),
                        "peak_mib": max(samples) if samples else None, "output": None}
            time.sleep(2)
    finally:
        stop.set()
        t.join()


def main():
    results = []

    # T2: LTXV 720p fast lane (97f @25fps ~= 3.88 s)
    results.append(run("ltxv-720p", wf.ltxv_i2v(KEYFRAME, PROMPT, width=1280, height=720,
                                                frames=97, fps=25.0, prefix="p7_ltxv720")))

    # T3: 3-way keyframe comparison at 1280x720 (same prompt/seed)
    results.append(run("img-qwen21", wf.qwen_image_21_t2i(PROMPT, 1280, 720, SEED, prefix="p7_q21")))
    results.append(run("img-2512-q4", wf.qwen_image_t2i(PROMPT, 1280, 720, SEED, prefix="p7_q4")))
    g6 = copy.deepcopy(wf.qwen_image_t2i(PROMPT, 1280, 720, SEED, prefix="p7_q6"))
    g6["1"]["inputs"]["unet_name"] = "qwen-image-2512-Q6_K.gguf"
    results.append(run("img-2512-q6", g6))

    print("SUMMARY: " + json.dumps(results, indent=1))


if __name__ == "__main__":
    main()
