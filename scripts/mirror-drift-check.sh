#!/usr/bin/env bash
set -euo pipefail

# Compare n8n image digest between prod and mirror.
# Exits 1 with DRIFT DETECTED if digests differ; exits 0 if aligned.

PROD=$(docker inspect --format='{{index .RepoDigests 0}}' \
  "n8n" 2>/dev/null || echo "NOT_RUNNING")
MIRROR=$(docker inspect --format='{{index .RepoDigests 0}}' \
  "agentic-sdlc-n8n-mirror" 2>/dev/null || echo "NOT_RUNNING")

if [ "$PROD" != "$MIRROR" ]; then
  echo "DRIFT DETECTED — run: bash scripts/mirror-sync.sh" >&2
  echo "  Prod n8n:   $PROD" >&2
  echo "  Mirror n8n: $MIRROR" >&2
  exit 1
fi
echo "Mirror aligned. n8n digest: $PROD"
