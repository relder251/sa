#!/usr/bin/env bash
# docker-prune.sh — weekly Docker image and build cache cleanup
# Run by systemd timer: docker-prune.timer (Sundays 04:00)
# Removes images unused for 7+ days and build cache older than 7 days.
# Safe: Watchtower runs at 03:00 daily, so any replaced image is already
# superseded well before this runs.

set -euo pipefail
LOG_TAG=docker-prune

log() { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*"; }

log "Starting Docker prune"

IMAGE_OUTPUT=$(docker image prune -f --filter "until=168h" 2>&1)
log "Image prune: $IMAGE_OUTPUT"

BUILD_OUTPUT=$(docker builder prune -f --filter "until=168h" 2>&1)
log "Build cache prune: $BUILD_OUTPUT"

log "Done"
