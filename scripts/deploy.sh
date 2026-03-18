#!/usr/bin/env bash
# deploy.sh — pull latest git changes and bring the stack up-to-date
# Usage (from local machine): ssh vps 'bash ~/sa/scripts/deploy.sh'
# Usage (on VPS directly):    bash ~/sa/scripts/deploy.sh
#
# What it does:
#   1. git pull (fails fast if there are local uncommitted changes)
#   2. docker compose up -d --build (recreates only changed services)
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
docker compose up -d --build

echo "=== Deploy complete ==="
