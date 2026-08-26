#!/usr/bin/env python3
"""
Test Qwen3.8-27B Image Analysis via vLLM OpenAI-compatible API.

Usage:
  python test_image_analysis.py --image /path/to/photo.jpg
  python test_image_analysis.py --image photo.jpg --query "What's in this image?"

Requires: vLLM running on port 8000 with Qwen3.8-27B (FP8 or NVFP4)
"""
import argparse
import base64
import json
import os
import sys
import time
import urllib.request


def encode_image(image_path):
    """Read image and return base64 data URL."""
    ext = os.path.splitext(image_path)[1].lower()
    mime_map = {".jpg": "jpeg", ".jpeg": "jpeg", ".png": "png", ".webp": "webp", ".bmp": "bmp", ".gif": "gif"}
    mime = mime_map.get(ext, "jpeg")
    with open(image_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("utf-8")
    return f"data:image/{mime};base64,{b64}"


def run_test(base_url, model_name, image_path, query, headers=None):
    if not headers:
        headers = {"Content-Type": "application/json"}

    print(f"\n{'='*60}")
    print(f"Backend: {base_url}")
    print(f"Model:   {model_name}")
    print(f"Image:   {image_path}")
    print(f"Query:   {query}")
    print(f"{'='*60}\n")

    image_b64 = encode_image(image_path)
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": image_b64}},
                {"type": "text", "text": query}
            ]
        }
    ]

    payload = {
        "model": model_name,
        "messages": messages,
        "stream": True,
        "temperature": 1.0,
        "top_p": 0.95,
        "top_k": 20,
        "max_tokens": 4096,
    }

    data = json.dumps(payload).encode()
    req = urllib.request.Request(base_url + "/v1/chat/completions", data=data, headers=headers, method="POST")

    start = time.time()
    first_token_time = None
    reasoning_out = ""
    answer_out = ""

    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            for line in resp:
                line_str = line.decode("utf-8").strip()
                if not line_str or line_str == "data: [DONE]":
                    continue
                if line_str.startswith("data: "):
                    chunk = json.loads(line_str[6:])
                    for choice in chunk.get("choices", []):
                        delta = choice.get("delta", {})
                        rc = delta.get("reasoning_content") or delta.get("reasoning")
                        ct = delta.get("content", "")

                        if rc:
                            print(f"[THINKING] {rc}", end="", flush=True)
                            reasoning_out += rc
                        if ct:
                            if first_token_time is None:
                                first_token_time = time.time() - start
                            print(ct, end="", flush=True)
                            answer_out += ct
    except Exception as e:
        print(f"\nError: {e}")
        return

    elapsed = time.time() - start
    print(f"\n\n--- Results ---")
    if first_token_time is not None:
        print(f"  Time to first token: {first_token_time:.2f}s")
    print(f"  Total time: {elapsed:.2f}s")
    print(f"  Reasoning length: {len(reasoning_out)} chars")
    print(f"  Answer length: {len(answer_out)} chars")
    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Test Qwen3.8-27B Image Analysis")
    parser.add_argument("--image", required=True, help="Path to image file")
    parser.add_argument("--query", default="Describe this image in detail.")
    args = parser.parse_args()

    if not os.path.exists(args.image):
        print(f"Error: Image not found: {args.image}")
        sys.exit(1)

    base_url = "http://localhost:8000"
    model_name = "Qwen/Qwen3.8-27B"
    headers = {"Content-Type": "application/json", "Authorization": "Bearer EMPTY"}
    run_test(base_url, model_name, args.image, args.query, headers)