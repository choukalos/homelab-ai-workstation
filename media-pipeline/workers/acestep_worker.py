#!/usr/bin/env python3
"""ACE-Step 1.5 music worker (runs in comfyui container).

Usage: acestep_worker.py --prompt "epic trailer theme" [--lyrics "verse..."]
       [--duration 15] --out /path.wav

Manages the acestep-api server lifecycle: starts it if not running (model load
~30-60s), submits the task, polls, downloads the wav, then stops the server to
free VRAM (set ACESTEP_KEEP_ALIVE=1 to keep it warm).
"""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.parse

import requests

BASE = "http://127.0.0.1:8001"
REPO = "/comfy/mnt/ACE-Step-1.5"
VENV_PY = f"{REPO}/.venv/bin/python"
KEEP_ALIVE = os.environ.get("ACESTEP_KEEP_ALIVE", "0") == "1"


def api_json(method: str, path: str, payload: dict, timeout: int = 30):
    r = requests.request(method, f"{BASE}{path}", json=payload, timeout=timeout)
    r.raise_for_status()
    body = r.json()
    if body.get("code") not in (200, None):
        raise RuntimeError(f"API error {body.get('code')}: {body.get('message')}")
    return body


def http_get_raw(url: str, timeout: int = 300):
    r = requests.get(url, timeout=timeout)
    r.raise_for_status()
    return r.content


def server_alive() -> bool:
    try:
        requests.get(f"{BASE}/health", timeout=3)
        return True
    except requests.RequestException:
        return False


def start_server() -> subprocess.Popen:
    print("starting acestep server...", flush=True)
    env = dict(os.environ)
    env.update({
        "ACESTEP_LM_MODEL_PATH": "acestep-5Hz-lm-1.7B",
        "ACESTEP_INIT_LLM": "true",
        "ACESTEP_DOWNLOAD_SOURCE": "huggingface",
        "HF_HOME": f"{REPO}/hf_cache",
        "HOME": "/comfy/mnt",
    })
    logf = open(f"/tmp/acestep_api.log", "ab")
    proc = subprocess.Popen(
        [f"{REPO}/.venv/bin/acestep-api", "--host", "127.0.0.1",
         "--port", "8001"],
        cwd=REPO, env=env, stdout=logf, stderr=subprocess.STDOUT)
    # wait for health (model load can take a minute)
    for _ in range(120):
        if server_alive():
            return proc
        if proc.poll() is not None:
            raise RuntimeError("acestep server exited during startup")
        time.sleep(5)
    raise TimeoutError("acestep server did not become healthy in 10 min")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--lyrics", default=None)
    ap.add_argument("--duration", type=int, default=15)
    ap.add_argument("--model", default="acestep-v15-turbo")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)

    proc = None
    if not server_alive():
        proc = start_server()
    else:
        print("acestep server already running", flush=True)

    try:
        payload = {
            "prompt": a.prompt,
            "lyrics": a.lyrics or "",
            "audio_duration": a.duration,
            "thinking": True,
            "vocal_language": "en",
            "model": a.model,
            "use_random_seed": False,
            "seed": a.seed,
            "inference_steps": 8,
            "audio_format": "wav",
            "batch_size": 1,
        }
        task = api_json("POST", "/release_task", payload, timeout=120)
        task_id = task["data"]["task_id"]
        print(f"task_id={task_id}", flush=True)

        deadline = time.time() + 1800
        while time.time() < deadline:
            res = api_json("POST", "/query_result",
                           {"task_id_list": [task_id]}, timeout=30)
            items = res.get("data") or []
            if not items:
                time.sleep(5)
                continue
            item = items[0]
            status = item.get("status")
            if status == 1:
                result_list = json.loads(item["result"]) if isinstance(item.get("result"), str) else item.get("result")
                if not result_list:
                    raise RuntimeError(f"empty result: {json.dumps(item)[:1000]}")
                path = result_list[0].get("file")
                if not path:
                    raise RuntimeError(f"no file in result: {json.dumps(result_list)[:1000]}")
                if str(path).startswith(("/v1/audio", "http")):
                    audio_url = f"{BASE}{path}" if path.startswith("/") else str(path)
                else:
                    audio_url = f"{BASE}/v1/audio?path={urllib.parse.quote(str(path))}"
                data = http_get_raw(audio_url, timeout=300)
                with open(a.out, "wb") as f:
                    f.write(data)
                print(f"WROTE {a.out} ({len(data)} bytes)", flush=True)
                return
            if status == 2:
                raise RuntimeError(f"generation failed: {json.dumps(item)[:1000]}")
            time.sleep(5)
        raise TimeoutError("generation timed out after 30 min")
    finally:
        if proc is not None and not KEEP_ALIVE:
            print("stopping acestep server (freeing VRAM)...", flush=True)
            proc.terminate()
            try:
                proc.wait(timeout=60)
            except subprocess.TimeoutExpired:
                proc.kill()


if __name__ == "__main__":
    main()