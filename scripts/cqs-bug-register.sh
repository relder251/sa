#!/usr/bin/env bash
# Usage: bash scripts/cqs-bug-register.sh <file> <function> <error_class> <description>
set -euo pipefail

FILE="${1:?file required}"
FUNC="${2:?function required}"
ERR_CLASS="${3:?error_class required}"
DESC="${4:?description required}"
SLUG="${PROJECT_SLUG:?PROJECT_SLUG not set}"
CYCLE="${CYCLE_ID:-unknown}"

FINGERPRINT=$(echo "${SLUG}:${FILE}:${FUNC}:${ERR_CLASS}:${DESC}" \
  | sha256sum | cut -d' ' -f1)

CONTAINER="${PROJECT_SLUG}-score-db"

# Check for repeat
EXISTING=$(docker exec "$CONTAINER" psql -U scores_user -d cqs_scores -tAc \
  "SELECT times_seen FROM bug_registry
   WHERE project_slug='${SLUG}' AND fingerprint='${FINGERPRINT}';")

if [ -n "$EXISTING" ]; then
  # Repeat bug — update and signal double penalty
  docker exec "$CONTAINER" psql -U scores_user -d cqs_scores \
    -c "UPDATE bug_registry SET times_seen = times_seen + 1,
            last_seen = '${CYCLE}', status = 'regressed'
        WHERE project_slug='${SLUG}' AND fingerprint='${FINGERPRINT}';" \
    > /dev/null
  echo "REPEAT_BUG fingerprint=${FINGERPRINT} times_seen=$((EXISTING+1))"
  exit 2  # Exit 2 = repeat bug signal to caller
else
  # New bug — register
  docker exec "$CONTAINER" psql -U scores_user -d cqs_scores \
    -c "INSERT INTO bug_registry
            (project_slug, fingerprint, first_seen, last_seen, status)
        VALUES ('${SLUG}', '${FINGERPRINT}', '${CYCLE}', '${CYCLE}', 'open');" \
    > /dev/null
  echo "NEW_BUG fingerprint=${FINGERPRINT}"
  exit 0  # Exit 0 = new bug
fi
