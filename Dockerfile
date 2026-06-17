# Backend image for the three Sprinkler FastAPI services:
#   routing (R1.0)  -> :9000   used by the plugin's  -routing
#   v1              -> :9001   used by the plugin's  -sprinkler_p1
#   v2              -> :9002   used by the plugin's  -sprinkler_p2
#
# One container runs backend_run.py, which launches all three uvicorn processes.
# Build context is the repo root:  docker build -t spriro-backend .
FROM python:3.13-slim

# libgomp1: OpenMP runtime that some numpy/scipy wheels load at import time.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libgomp1 \
 && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install dependencies first so the layer caches unless requirements change.
COPY requirements.docker.txt /app/requirements.docker.txt
RUN pip install -r /app/requirements.docker.txt

# Launcher + the three backend packages. Only these are needed; .dockerignore
# keeps the Windows plugin projects and build artifacts out of the image.
COPY backend_run.py /app/backend_run.py
COPY Sprinkler_placement_v-1.0/backend /app/Sprinkler_placement_v-1.0/backend
COPY Sprinkler_placement_v-2.0/backend /app/Sprinkler_placement_v-2.0/backend
COPY R1.0/backend /app/R1.0/backend

EXPOSE 9000 9001 9002

# 0.0.0.0 so the ports are reachable from outside the container; --no-color
# because container logs are not a TTY.
CMD ["python", "backend_run.py", "--host", "0.0.0.0", "--no-color"]

# Liveness via the routing backend's health endpoint (no curl needed).
HEALTHCHECK --interval=30s --timeout=5s --start-period=45s --retries=3 \
  CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:9000/health', timeout=4).status==200 else 1)"]
