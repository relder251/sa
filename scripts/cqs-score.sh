#!/usr/bin/env bash
# Usage: bash scripts/cqs-score.sh <agent> <event_type> <points> <description> [evidence]
# Example: bash scripts/cqs-score.sh tester BUG_FOUND +10 "null ptr in auth.py" "auth.py:42"
set -euo pipefail

AGENT="${1:?agent required}"
EVENT="${2:?event_type required}"
POINTS="${3:?points required}"
DESC="${4:?description required}"
EVIDENCE="${5:-}"
CYCLE="${CYCLE_ID:-unknown}"
SLUG="${PROJECT_SLUG:?PROJECT_SLUG not set}"

CONTAINER="${PROJECT_SLUG}-score-db"

docker exec "$CONTAINER" psql -U scores_user -d cqs_scores \
  -c "INSERT INTO score_events (project_slug, cycle_id, agent_name, event_type, points, description, evidence)
      VALUES ('${SLUG}', '${CYCLE}', '${AGENT}', '${EVENT}', ${POINTS}, '${DESC}', '${EVIDENCE}');" \
  -c "UPDATE agent_scores SET current_score = current_score + ${POINTS}, last_updated = NOW()
      WHERE project_slug = '${SLUG}' AND agent_name = '${AGENT}';" \
  -c "SELECT cqs_update_tier('${SLUG}', '${AGENT}');" \
  > /dev/null

echo "CQS: [${AGENT}] ${EVENT} ${POINTS}pts — ${DESC}"
