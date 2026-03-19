#!/bin/bash
# Usage: portal_docker_up.sh <container_name> <image> [docker run args...]
# Starts a container directly via docker run (avoids compose file path dependency)
set -euo pipefail
CONTAINER_NAME="${1:?missing container name}"; shift
IMAGE="${1:?missing image}"; shift
echo "Starting ${CONTAINER_NAME}..."
docker run -d --name "${CONTAINER_NAME}" --network vibe_net --restart unless-stopped "$IMAGE" "$@"
echo "Started: ${CONTAINER_NAME}"
