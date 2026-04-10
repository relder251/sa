#!/usr/bin/env bash
# Usage: bash scripts/cqs-bug-register.sh <file> <function> <error_class> <description>
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$REPO_DIR/.env.prod"

: "${CQS_DB_PASSWORD:?CQS_DB_PASSWORD not set in .env.prod}"

FILE="${1:?file required}"
FUNC="${2:?function required}"
ERR_CLASS="${3:?error_class required}"
DESC="${4:?description required}"
SLUG="${PROJECT_SLUG:?PROJECT_SLUG not set}"
CYCLE="${CYCLE_ID:-unknown}"

FINGERPRINT=$(echo "${SLUG}:${FILE}:${FUNC}:${ERR_CLASS}:${DESC}" \
  | sha256sum | cut -d' ' -f1)

# Check for repeat
EXISTING=$(PGPASSWORD=$CQS_DB_PASSWORD psql \
  -h localhost -p "${SCORE_DB_PORT:-5433}" \
  -U scores_user -d cqs_scores -tAc \
  "SELECT times_seen FROM bug_registry
   WHERE project_slug='${SLUG}' AND fingerprint='${FINGERPRINT}';")

if [ -n "$EXISTING" ]; then
  # Repeat bug — update and signal double penalty
  PGPASSWORD=$CQS_DB_PASSWORD psql \
    -h localhost -p "${SCORE_DB_PORT:-5433}" \
    -U scores_user -d cqs_scores \
    -c "UPDATE bug_registry SET times_seen = times_seen + 1,
            last_seen = '${CYCLE}', status = 'regressed'
        WHERE project_slug='${SLUG}' AND fingerprint='${FINGERPRINT}';" \
    > /dev/null
  echo "REPEAT_BUG fingerprint=${FINGERPRINT} times_seen=$((EXISTING+1))"
  exit 2  # Exit 2 = repeat bug signal to caller
else
  # New bug — register
  PGPASSWORD=$CQS_DB_PASSWORD psql \
    -h localhost -p "${SCORE_DB_PORT:-5433}" \
    -U scores_user -d cqs_scores \
    -c "INSERT INTO bug_registry
            (project_slug, fingerprint, first_seen, last_seen, status)
        VALUES ('${SLUG}', '${FINGERPRINT}', '${CYCLE}', '${CYCLE}', 'open');" \
    > /dev/null
  echo "NEW_BUG fingerprint=${FINGERPRINT}"
  exit 0  # Exit 0 = new bug
fi
