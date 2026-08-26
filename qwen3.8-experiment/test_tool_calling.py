#!/usr/bin/env python3
"""
Test Qwen3.8-27B tool calling via vLLM OpenAI-compatible API.

Usage:
  python test_tool_calling.py --backend vllm
  python test_tool_calling.py --backend vllm --query "What's the weather in San Francisco?"

Requires: vLLM running on port 8000 with --tool-call-parser qwen3_coder
"""
import argparse, json, sys, time, urllib.request

TOOLS = [
    {"type": "function", "function": {"name": "get_weather", "description": "Get current weather for a location", "parameters": {"type": "object", "properties": {"location": {"type": "string", "description": "City name"}, "units": {"type": "string", "enum": ["celsius", "fahrenheit"]}}, "required": ["location"]}}},
    {"type": "function", "function": {"name": "search_web", "description": "Search the web", "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}},
    {"type": "function", "function": {"name": "calculate", "description": "Evaluate math expression", "parameters": {"type": "object", "properties": {"expression": {"type": "string"}}, "required": ["expression"]}}}
]
MOCK_RESULTS = {
    "get_weather": '{"temp": 22, "condition": "sunny", "humidity": 45}',
    "search_web": '{"results": ["Qwen3.8-27B is a 27B multimodal model from Alibaba"]}',
    "calculate": '{"result": 42}'
}

def run_test(base_url, model_name, query, headers=None):
    if not headers:
        headers = {"Content-Type": "application/json"}

    print(f"\n{'='*60}")
    print(f"Backend: {base_url}")
    print(f"Model:   {model_name}")
    print(f"Query:   {query}")
    print(f"{'='*60}\n")

    messages = [
        {"role": "system", "content": "You are a helpful assistant with access to tools. Use them to answer the user's question."},
        {"role": "user", "content": query}
    ]

    # ---- Turn 1: Ask the model ----
    print(">>> Turn 1: Asking model...")
    payload = {
        "model": model_name,
        "messages": messages,
        "tools": TOOLS,
        "stream": True,
        "temperature": 1.0,
        "top_p": 0.95,
        "max_tokens": 4096,
    }

    data = json.dumps(payload).encode()
    req = urllib.request.Request(base_url + "/v1/chat/completions", data=data, headers=headers, method="POST")

    start = time.time()
    text_out = ""
    raw_tool_calls = []

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
                        tc = delta.get("tool_calls")

                        if rc:
                            print(f"[THINK] {rc}", end="", flush=True)
                        if ct:
                            print(ct, end="", flush=True)
                            text_out += ct
                        if tc:
                            for t in tc:
                                raw_tool_calls.append(t)
    except Exception as e:
        print(f"\nError on turn 1: {e}")
        return

    elapsed = time.time() - start
    print(f"\n\n  Time to respond: {elapsed:.2f}s")
    print(f"  Tool calls detected: {len(raw_tool_calls)}")

    if not raw_tool_calls:
        print("  No tool calls. Model answered directly.")
        print(f"  Response: {text_out[:200]}")
        return

    # Build the assistant message for history
    assistant_msg = {"role": "assistant", "content": text_out, "tool_calls": raw_tool_calls}
    messages.append(assistant_msg)

    # ---- Execute (mock) tools ----
    print(f"\n>>> Executing tools (mock)...")
    for tc in raw_tool_calls:
        func = tc.get("function", {})
        name = func.get("name", "unknown")
        args_str = func.get("arguments", "{}")
        tc_id = tc.get("id", "")
        result = MOCK_RESULTS.get(name, '{"error": "Unknown tool"}')
        print(f"  {name}({args_str}) => {result}")
        messages.append({"role": "tool", "tool_call_id": tc_id, "content": result})

    # ---- Turn 2: Feed results back ----
    print(f"\n>>> Turn 2: Getting final answer...")
    payload2 = {
        "model": model_name,
        "messages": messages,
        "tools": TOOLS,
        "stream": True,
        "temperature": 1.0,
        "top_p": 0.95,
        "max_tokens": 4096,
    }
    data2 = json.dumps(payload2).encode()
    req2 = urllib.request.Request(base_url + "/v1/chat/completions", data=data2, headers=headers, method="POST")

    try:
        with urllib.request.urlopen(req2, timeout=300) as resp:
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
                            print(f"[THINK] {rc}", end="", flush=True)
                        if ct:
                            print(ct, end="", flush=True)
    except Exception as e:
        print(f"\nError on turn 2: {e}")

    print(f"\n\n{'='*60}")
    print("Done!")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Test Qwen3.8-27B tool calling")
    parser.add_argument("--backend", choices=["vllm", "sglang"], default="vllm")
    parser.add_argument("--query", default="What's the weather in San Francisco?")
    args = parser.parse_args()

    backend_map = {
        "vllm": ("http://localhost:8000", "Qwen/Qwen3.8-27B"),
        "sglang": ("http://localhost:30000", "Qwen/Qwen3.8-27B"),
    }
    headers_map = {
        "vllm": {"Content-Type": "application/json", "Authorization": "Bearer EMPTY"},
        "sglang": {"Content-Type": "application/json"},
    }

    base_url, model_name = backend_map[args.backend]
    headers = headers_map[args.backend]
    run_test(base_url, model_name, args.query, headers)