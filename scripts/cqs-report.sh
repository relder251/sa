#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$REPO_DIR/.env.prod"

: "${CQS_DB_PASSWORD:?CQS_DB_PASSWORD not set in .env.prod}"

SLUG="${PROJECT_SLUG:?PROJECT_SLUG not set}"

PGPASSWORD=$CQS_DB_PASSWORD psql \
  -h localhost -p "${SCORE_DB_PORT:-5433}" \
  -U scores_user -d cqs_scores \
  --pset=format=aligned \
  -c "SELECT agent_name, current_score, model_tier, trust_tier,
             bugs_introduced, bugs_caught, repeat_bugs, clean_cycles,
             challenges_won, challenges_lost
      FROM agent_scores
      WHERE project_slug = '${SLUG}'
      ORDER BY current_score DESC;"
