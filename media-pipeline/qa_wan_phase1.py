#!/usr/bin/env python3
"""Phase 1 manual validation for Wan 2.2 14B I2V (wan_shots_todo.md).

Direct ComfyUI :8188 API prompts (bypasses the media-pipeline service) to
measure peak VRAM + wall time at 480p / 720p with the official Comfy-Org
dual-expert graph:

  UnetLoaderGGUF (high/low noise Q5_K_M) -> ModelSamplingSD3 (shift 5.0)
  -> WanImageToVideo (start_image latent; NO clip-vision for 2.2 I2V)
  -> KSamplerAdvanced x2 (euler/simple, cfg 5.0, 20 steps, split at step 10)
  -> VAEDecode -> VHS_VideoCombine (16 fps)

CLIPLoader device=cpu keeps umt5-xxl (6.74 GB) out of VRAM.
VRAM metric = nvidia-smi memory.used delta vs pre-run baseline (vLLM holds
~55-57 GB of the 72 GB card; the delta is the ComfyUI/Wan footprint).

Usage:
  python3 qa_wan_phase1.py 480          # 832x480, 81f @16fps
  python3 qa_wan_phase1.py 720          # 1280x720, 81f @16fps
  python3 qa_wan_phase1.py 480 720
"""
import json
import subprocess
import sys
import threading
import time
import urllib.request

COMFY = "http://127.0.0.1:8188"
KEYFRAME = "c1_k11_edited.png"
PROMPT = ("Cinematic shot, subtle ambient motion, very slow gentle camera push-in, "
          "soft natural lighting, photorealistic, high detail, sharp focus.")
NEG = ("low quality, blurry, distorted, deformed, jittery, flickering, watermark, "
       "text, logo, static camera when motion is described")
SEED = 42
LENGTH = 81  # 4n+1; 81f @ 16fps ~= 5.1s


def graph(w: int, h: int) -> dict:
    return {
        "1": {"class_type": "UnetLoaderGGUF",
              "inputs": {"unet_name": "Wan2.2-I2V-A14B-HighNoise-Q5_K_M.gguf"}},
        "2": {"class_type": "UnetLoaderGGUF",
              "inputs": {"unet_name": "Wan2.2-I2V-A14B-LowNoise-Q5_K_M.gguf"}},
        "3": {"class_type": "CLIPLoader",
              "inputs": {"clip_name": "umt5_xxl_fp8_e4m3fn_scaled.safetensors",
                         "type": "wan", "device": "cpu"}},
        "4": {"class_type": "CLIPTextEncode", "inputs": {"text": PROMPT, "clip": ["3", 0]}},
        "5": {"class_type": "CLIPTextEncode", "inputs": {"text": NEG, "clip": ["3", 0]}},
        "6": {"class_type": "VAELoader", "inputs": {"vae_name": "wan_2.1_vae.safetensors"}},
        "7": {"class_type": "LoadImage", "inputs": {"image": KEYFRAME}},
        "8": {"class_type": "WanImageToVideo",
              "inputs": {"positive": ["4", 0], "negative": ["5", 0], "vae": ["6", 0],
                         "width": w, "height": h, "length": LENGTH, "batch_size": 1,
                         "start_image": ["7", 0]}},
        "9":  {"class_type": "ModelSamplingSD3", "inputs": {"model": ["1", 0], "shift": 5.0}},
        "10": {"class_type": "ModelSamplingSD3", "inputs": {"model": ["2", 0], "shift": 5.0}},
        "11": {"class_type": "KSamplerAdvanced",
               "inputs": {"model": ["9", 0], "add_noise": "enable", "noise_seed": SEED,
                          "steps": 20, "cfg": 5.0, "sampler_name": "euler",
                          "scheduler": "simple", "positive": ["8", 0], "negative": ["8", 1],
                          "latent_image": ["8", 2], "start_at_step": 0, "end_at_step": 10,
                          "return_with_leftover_noise": "disable"}},
        "12": {"class_type": "KSamplerAdvanced",
               "inputs": {"model": ["10", 0], "add_noise": "disable", "noise_seed": SEED,
                          "steps": 20, "cfg": 5.0, "sampler_name": "euler",
                          "scheduler": "simple", "positive": ["8", 0], "negative": ["8", 1],
                          "latent_image": ["11", 0], "start_at_step": 10, "end_at_step": 20,
                          "return_with_leftover_noise": "disable"}},
        "13": {"class_type": "VAEDecode", "inputs": {"samples": ["12", 0], "vae": ["6", 0]}},
        "14": {"class_type": "VHS_VideoCombine",
               "inputs": {"images": ["13", 0], "frame_rate": 16,
                          "filename_prefix": f"wan22_p1_{w}x{h}", "format": "video/h264-mp4",
                          "pix_fmt": "yuv420p", "crf": 19, "save_metadata": False,
                          "save_output": True, "pingpong": False, "loop_count": 0}},
    }


def vram_sampler(samples: list, stop: threading.Event) -> None:
    """Append used-MiB from nvidia-smi at 1 Hz until stop is set."""
    p = subprocess.Popen(
        ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits", "-l", "1"],
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


def run(tag: str, w: int, h: int) -> dict:
    samples: list = []
    stop = threading.Event()
    t = threading.Thread(target=vram_sampler, args=(samples, stop), daemon=True)
    t.start()
    time.sleep(4)  # let the GPU settle
    baseline = samples[-1] if samples else None
    print(f"[{tag}] baseline_vram={baseline} MiB (total used, incl. vLLM)", flush=True)

    payload = json.dumps({"prompt": graph(w, h)}).encode()
    req = urllib.request.Request(f"{COMFY}/prompt", data=payload,
                                 headers={"Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=60) as r:
        pid = json.load(r)["prompt_id"]
    print(f"[{tag}] prompt_id={pid}", flush=True)

    status, outputs = None, {}
    while True:
        time.sleep(5)
        try:
            with urllib.request.urlopen(f"{COMFY}/history/{pid}", timeout=30) as r:
                hist = json.load(r)
        except Exception:
            continue
        if pid in hist:
            status = hist[pid].get("status", {})
            outputs = hist[pid].get("outputs", {})
            if status.get("status_str") in ("success", "error"):
                break
        if time.time() - t0 > 7200:
            raise SystemExit(f"[{tag}] timeout after 2h")
    wall = time.time() - t0
    stop.set()
    t.join(timeout=10)

    peak = max(samples) if samples else None
    out = None
    if status and status.get("status_str") == "success":
        for o in outputs.values():
            for v in (o.get("videos") or []) + (o.get("gifs") or []):
                out = v.get("filename")
    err = None
    if status and status.get("status_str") == "error":
        msgs = status.get("messages") or [{}]
        err = msgs[-1].get("exception_message")
    result = {
        "tag": tag, "w": w, "h": h,
        "status": status.get("status_str") if status else "unknown",
        "wall_s": round(wall, 1),
        "baseline_mib": baseline, "peak_mib": peak,
        "peak_delta_mib": (peak - baseline) if (peak is not None and baseline is not None) else None,
        "output": out, "error": err,
    }
    print(f"[{tag}] RESULT {json.dumps(result)}", flush=True)
    return result


if __name__ == "__main__":
    tags = sys.argv[1:] or ["480"]
    results = []
    for tag in tags:
        w, h = (832, 480) if tag == "480" else (1280, 720)
        results.append(run(tag, w, h))
    print("SUMMARY:", json.dumps(results, indent=1))