#!/usr/bin/env bash
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

# Load .env.mirror if present for variable defaults
if [ -f .env.mirror ]; then
  # shellcheck disable=SC1091
  set -a; source .env.mirror; set +a
fi

echo "==> Syncing mirror environment..."
docker compose -f docker-compose.mirror.yml pull --quiet
docker compose -f docker-compose.mirror.yml up -d --force-recreate
echo "==> Mirror synced at $(date)"
echo "    n8n:     http://localhost:${MIRROR_N8N_PORT:-5679}"
echo "    LiteLLM: http://localhost:${MIRROR_LITELLM_PORT:-4002}"
echo "    DB:      localhost:${MIRROR_DB_PORT:-5434}"
echo "    ScoreDB: localhost:${SCORE_DB_PORT:-5433}"
