#!/usr/bin/env bash
set -euo pipefail
PROD=$(docker inspect --format='{{index .RepoDigests 0}}' \
  "${PROJECT_SLUG}-app" 2>/dev/null || echo "NOT_RUNNING")
MIRROR=$(docker inspect --format='{{index .RepoDigests 0}}' \
  "${PROJECT_SLUG}-mirror" 2>/dev/null || echo "NOT_RUNNING")
if [ "$PROD" != "$MIRROR" ]; then
  echo "DRIFT DETECTED — run: bash scripts/mirror-sync.sh" >&2
  echo "  Prod:   $PROD" >&2
  echo "  Mirror: $MIRROR" >&2
  exit 1
fi
echo "Mirror aligned. Digest: $PROD"
