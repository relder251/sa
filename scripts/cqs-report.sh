#!/usr/bin/env bash
set -euo pipefail
SLUG="${PROJECT_SLUG:?PROJECT_SLUG not set}"

PGPASSWORD=scores_pass psql \
  -h localhost -p "${SCORE_DB_PORT:-5433}" \
  -U scores_user -d cqs_scores \
  --pset=format=aligned \
  -c "SELECT agent_name, current_score, model_tier, trust_tier,
             bugs_introduced, bugs_caught, repeat_bugs, clean_cycles,
             challenges_won, challenges_lost
      FROM agent_scores
      WHERE project_slug = '${SLUG}'
      ORDER BY current_score DESC;"
