#!/usr/bin/env bash
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
echo "==> Syncing mirror..."
docker compose -f docker-compose.mirror.yml pull --quiet
docker compose -f docker-compose.mirror.yml run --rm app-mirror \
  bash -c "${MIGRATE_CMD:-echo 'no migrate cmd'}"
docker compose -f docker-compose.mirror.yml up -d --force-recreate
echo "==> Mirror synced at $(date)"
echo "    App:     http://localhost:${MIRROR_PORT:-8081}"
echo "    DB:      localhost:${MIRROR_DB_PORT:-5433}"
