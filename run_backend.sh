#!/usr/bin/env bash
#
# run_backend.sh - build and (re)launch the Sprinkler backend container on a
# Linux VPS, always-on. One container serves all three backends:
#
#   routing (R1.0)  http://<vps-ip>:9000/health      <- plugin  -routing
#   v1              http://<vps-ip>:9001/api/health  <- plugin  -sprinkler_p1
#   v2              http://<vps-ip>:9002/api/health  <- plugin  -sprinkler_p2
#
# "--restart always" keeps it running across crashes and VPS reboots.
# Run from the repo root:   ./run_backend.sh
#
# Override any of these with environment variables if you need to:
#   SPRIRO_IMAGE, SPRIRO_CONTAINER,
#   SPRIRO_PORT_ROUTING, SPRIRO_PORT_P1, SPRIRO_PORT_P2

set -euo pipefail

IMAGE="${SPRIRO_IMAGE:-spriro-backend}"
NAME="${SPRIRO_CONTAINER:-spriro-backend}"
P_ROUTING="${SPRIRO_PORT_ROUTING:-9000}"
P1="${SPRIRO_PORT_P1:-9001}"
P2="${SPRIRO_PORT_P2:-9002}"

# Always operate from the directory this script lives in (the repo root).
cd "$(dirname "$0")"

if ! command -v docker >/dev/null 2>&1; then
  echo "error: docker is not installed or not on PATH." >&2
  exit 1
fi

echo ">> building image '$IMAGE' (this can take a few minutes the first time) ..."
docker build -t "$IMAGE" .

echo ">> replacing any existing container '$NAME' ..."
docker rm -f "$NAME" >/dev/null 2>&1 || true

echo ">> starting '$NAME' with --restart always ..."
docker run -d \
  --name "$NAME" \
  --restart always \
  -p "${P_ROUTING}:9000" \
  -p "${P1}:9001" \
  -p "${P2}:9002" \
  "$IMAGE"

echo
echo ">> up. endpoints (replace <vps-ip> with the server address):"
echo "     routing       http://<vps-ip>:${P_ROUTING}/health        (-routing)"
echo "     sprinkler_p1  http://<vps-ip>:${P1}/api/health       (-sprinkler_p1)"
echo "     sprinkler_p2  http://<vps-ip>:${P2}/api/health       (-sprinkler_p2)"
echo
echo ">> follow logs:   docker logs -f $NAME"
echo ">> status:        docker ps --filter name=$NAME"
echo ">> stop+remove:   docker rm -f $NAME"
