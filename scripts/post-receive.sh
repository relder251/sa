#!/usr/bin/env bash
# PCIRT+ post-receive hook — triggers PCIRT Orchestrator on push to master
# Hardened: master-only, timeout, logging, lock guard

set -euo pipefail

LOG="/opt/agentic-sdlc/logs/pcirt-hook.log"
LOCK="/tmp/pcirt-push.lock"
ENV_FILE="/opt/agentic-sdlc/.env"

# Source .env for N8N_WEBHOOK_URL and PROJECT_SLUG (if present)
[ -f "$ENV_FILE" ] && set +u && . "$ENV_FILE" && set -u || true

N8N_WEBHOOK_URL="${N8N_WEBHOOK_URL:-https://n8n.private.sovereignadvisory.ai}"
PROJECT_SLUG="${PROJECT_SLUG:-agentic-sdlc}"

mkdir -p "$(dirname "$LOG")"

log() { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*" >> "$LOG"; }

# Only run for master branch pushes
while read -r oldrev newrev refname; do
  branch="${refname#refs/heads/}"
  if [ "$branch" != "master" ]; then
    log "SKIP: push to $branch (not master)"
    continue
  fi

  # Lock guard — skip if another trigger is already in flight
  if [ -e "$LOCK" ]; then
    log "SKIP: lock file exists ($LOCK), skipping concurrent trigger"
    continue
  fi
  touch "$LOCK"
  trap 'rm -f "$LOCK"' EXIT

  COMMIT="$newrev"
  log "TRIGGER: branch=$branch commit=$COMMIT"

  # Fix n8n-writable file ownership (n8n runs as uid 1000; git pull resets to root)
  chown 1000:1000 /opt/agentic-sdlc/portal/services.json 2>/dev/null || true
  log "PERMS: portal/services.json ownership set to 1000:1000"

  HTTP_CODE=$(curl -s -o /tmp/pcirt-push-resp.json -w "%{http_code}" \
    --max-time 10 \
    -X POST "${N8N_WEBHOOK_URL}/webhook/pcirt-push" \
    -H "Content-Type: application/json" \
    -d "{\"trigger\":\"git-push\",\"commit\":\"${COMMIT}\",\"branch\":\"${branch}\",\"project\":\"${PROJECT_SLUG}\"}" \
    2>>"$LOG" || echo "000")

  if [ "$HTTP_CODE" = "200" ]; then
    log "OK: n8n responded 200"
  else
    log "WARN: n8n returned HTTP $HTTP_CODE (non-fatal)"
  fi

  rm -f "$LOCK"
  trap - EXIT
done
