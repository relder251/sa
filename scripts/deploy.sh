#!/usr/bin/env bash
# deploy.sh — pull latest git changes and bring the stack up-to-date
# Usage (from local machine): ssh vps 'bash /opt/agentic-sdlc/scripts/deploy.sh'
# Usage (on VPS directly):    bash /opt/agentic-sdlc/scripts/deploy.sh
#
# What it does:
#   1. git pull (fails fast if there are local uncommitted changes)
#   2. docker compose up -d --build (recreates only changed services)
#
# Compose files used:
#   docker-compose.yml          — base stack (all shared services)
#   docker-compose.override.yml — auto-managed local overrides
#   docker-compose.prod.yml     — VPS-only services (nginx, twingate, certbot, etc.)
#
# What it does NOT do:
#   - rm or stop containers (use docker compose stop <service> manually if needed)
#   - run migrations (do those manually before deploying)
#   - update .env (edit on the VPS directly, never committed)

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"

echo "=== Deploy starting from $REPO_DIR ==="
echo "--- git pull ---"
git pull

echo "--- docker compose up -d --build ---"
docker compose \
  -f docker-compose.yml \
  -f docker-compose.override.yml \
  -f docker-compose.prod.yml \
  up -d --build

# n8n runs as uid=1000 (node) but git pull creates files as root.
# Ensure the portal data files n8n needs to write are group/world-writable.
echo "--- fixing portal data permissions ---"
chmod 666 "$REPO_DIR/portal/services.json" 2>/dev/null || true

echo "=== Deploy complete ==="
