"""Synchronous ComfyUI API client (queue, poll, collect outputs)."""
import time
import requests

COMFY = "http://127.0.0.1:8188"
OUTPUT_DIR = "/home/chuck/data/comfyui/basedir/output"


def queue_prompt(workflow: dict) -> str:
    r = requests.post(f"{COMFY}/prompt", json={"prompt": workflow}, timeout=30)
    r.raise_for_status()
    d = r.json()
    if "error" in d:
        raise RuntimeError(f"ComfyUI queue error: {d.get('error')} {d.get('node_errors')}")
    return d["prompt_id"]


def wait_for_job(prompt_id: str, timeout: float = 7200.0) -> dict:
    t0 = time.time()
    while time.time() - t0 < timeout:
        r = requests.get(f"{COMFY}/history/{prompt_id}", timeout=30)
        r.raise_for_status()
        hist = r.json()
        if prompt_id in hist:
            entry = hist[prompt_id]
            status = entry.get("status", {})
            if status.get("status_str") == "error":
                msgs = []
                messages = status.get("messages") or []
                for m in messages:
                    if isinstance(m, (list, tuple)) and len(m) > 1 and m[0] == "execution_error":
                        msgs.append(str(m[1]))
                raise RuntimeError(f"ComfyUI job failed: {' '.join(msgs)[:2000]}")
            return entry
        time.sleep(2)
    raise TimeoutError(f"ComfyUI job {prompt_id} timed out after {timeout}s")


def job_outputs(entry: dict) -> dict:
    """{node_id: {kind: [filenames]}} for image/video/audio outputs."""
    out = {}
    for node_id, node_out in entry.get("outputs", {}).items():
        files = {}
        for key in ("images", "videos", "audios", "audio", "gifs"):
            for f in node_out.get(key, []):
                fname = f.get("filename")
                if fname:
                    files.setdefault(key, []).append(fname)
        if files:
            out[node_id] = files
    return out


def comfy_run(workflow: dict, timeout: float = 7200.0) -> dict:
    """Queue + wait + collect. Returns {'prompt_id', 'outputs'}."""
    prompt_id = queue_prompt(workflow)
    entry = wait_for_job(prompt_id, timeout=timeout)
    return {"prompt_id": prompt_id, "outputs": job_outputs(entry)}