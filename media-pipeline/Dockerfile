# media-pipeline — thin FastAPI orchestrator on the GPU host that drives ComfyUI + VLLM.
#
# Runs with host networking + the host docker socket, so it can:
#   - reach ComfyUI (127.0.0.1:8188) and VLLM (127.0.0.1:8000) on the host loopback
#   - `docker exec`/`docker cp` into comfyui_backend (TTS/ACE-Step workers, ffmpeg, job dirs)
#
# It needs NO GPU and NO models — all heavy lifting happens in comfyui_backend / VLLM.
#
# Rebuild after code changes:
#   model-manager rebuild media-pipeline
FROM python:3.12-slim

# Docker CLI only (talks to the host daemon via the mounted /var/run/docker.sock).
# No daemon is started inside this container.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && install -m 0755 -d /etc/apt/keyrings \
    && curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
             https://download.docker.com/linux/debian $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
        > /etc/apt/sources.list.d/docker.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends docker-ce-cli \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY server.py workflows.py comfy_client.py .

# Default bind 0.0.0.0 so the remote media-mcp client can reach <gpu-host>:8189 (LAN-only).
ENV PIPELINE_HOST=0.0.0.0 \
    PIPELINE_PORT=8189

CMD ["python", "server.py"]