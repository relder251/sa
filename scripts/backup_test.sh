#!/usr/bin/env bash
# backup_test.sh — validates a postgres backup dump can be restored
# Usage: bash scripts/backup_test.sh <path-to-postgres_YYYY-MM-DD.sql.gz>
#
# Creates a throwaway postgres:15 container, restores the dump,
# queries key tables across all three databases, reports pass/fail,
# and always cleans up the throwaway container on exit.

set -euo pipefail

DUMP_FILE="${1:?Usage: backup_test.sh <path-to-postgres_YYYY-MM-DD.sql.gz>}"

if [ ! -f "$DUMP_FILE" ]; then
  echo "❌ Dump file not found: $DUMP_FILE"
  exit 1
fi

CONTAINER="pg_restore_test_$$"
PG_USER="${LITELLM_USER:-litellm}"
PG_PASS="${LITELLM_PASSWORD:-litellm_password}"
PASS=0
FAIL=0

green='\033[0;32m'
red='\033[0;31m'
reset='\033[0m'

cleanup() {
  echo ""
  echo "Cleaning up $CONTAINER..."
  docker rm -f "$CONTAINER" > /dev/null 2>&1 || true
}
trap cleanup EXIT

echo ""
echo "╔══════════════════════════════════════════════╗"
echo "║       Backup Restore Validation              ║"
echo "╚══════════════════════════════════════════════╝"
echo "Dump: $DUMP_FILE"
echo ""

# Start throwaway postgres
echo "Starting throwaway postgres container..."
docker run -d --name "$CONTAINER" \
  -e POSTGRES_USER="$PG_USER" \
  -e POSTGRES_PASSWORD="$PG_PASS" \
  -e POSTGRES_DB="postgres" \
  postgres:15 > /dev/null

# Wait for it to be ready (up to 30s)
echo "Waiting for postgres to be ready..."
for i in $(seq 1 30); do
  docker exec "$CONTAINER" pg_isready -U "$PG_USER" > /dev/null 2>&1 && break
  sleep 1
done
docker exec "$CONTAINER" pg_isready -U "$PG_USER" > /dev/null 2>&1 \
  || { echo "❌ Postgres did not become ready in 30s"; exit 1; }

# Restore from dump
echo "Restoring from dump..."
# Connect to maintenance DB so pg_dumpall's \connect directives work
# Pass PGPASSWORD into the container via -e (env var set on local shell is NOT
# visible inside docker exec — must be passed explicitly with -e)
gunzip -c "$DUMP_FILE" \
  | docker exec -i -e PGPASSWORD="$PG_PASS" "$CONTAINER" psql -U "$PG_USER" -d postgres -q

echo ""
echo "── Validating tables ────────────────────────────"

check() {
  local label="$1"
  local db="$2"
  local query="$3"
  local result
  # Must pass PGPASSWORD via -e — env var prefixing does not forward into docker exec
  result=$(docker exec -e PGPASSWORD="$PG_PASS" "$CONTAINER" \
    psql -U "$PG_USER" -d "$db" -t -c "$query" 2>/dev/null | tr -d '[:space:]')
  if [ -n "$result" ] && [ "$result" != "0" ]; then
    echo -e "  ${green}✅ PASS${reset}  $label: $result"
    PASS=$((PASS + 1))
  else
    echo -e "  ${red}❌ FAIL${reset}  $label: got '$result'"
    FAIL=$((FAIL + 1))
  fi
}

# litellm database
check "sa_leads table exists" "litellm" \
  "SELECT count(*) FROM information_schema.tables WHERE table_name='sa_leads'"
check "sa_leads row count > 0" "litellm" \
  "SELECT count(*) FROM sa_leads"
check "litellm_verificationtoken table" "litellm" \
  "SELECT count(*) FROM information_schema.tables WHERE table_name='litellm_verificationtoken'"

# n8n database
check "n8n workflow_entity table exists" "n8n" \
  "SELECT count(*) FROM information_schema.tables WHERE table_name='workflow_entity'"

# keycloak database — optional check (keycloak DB may not exist if never initialised)
# CREATE ROLE errors during pg_dumpall restore are expected and non-fatal
keycloak_exists=$(docker exec -e PGPASSWORD="$PG_PASS" "$CONTAINER" \
  psql -U "$PG_USER" -d postgres -t -c \
  "SELECT count(*) FROM pg_database WHERE datname='keycloak'" 2>/dev/null | tr -d '[:space:]')
if [ "$keycloak_exists" = "1" ]; then
  check "keycloak realm table exists" "keycloak" \
    "SELECT count(*) FROM information_schema.tables WHERE table_name='realm'"
else
  echo "  ℹ SKIP   keycloak database not present in this dump (not an error)"
fi

echo ""
echo "════════════════════════════════════════════════"
TOTAL=$((PASS + FAIL))
if [ "$FAIL" -eq 0 ]; then
  echo -e "${green}✅ ALL $TOTAL CHECKS PASSED — dump is valid${reset}"
  exit 0
else
  echo -e "${red}❌ $FAIL/$TOTAL CHECKS FAILED — dump may be corrupt${reset}"
  exit 1
fi
