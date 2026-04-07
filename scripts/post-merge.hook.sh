#!/usr/bin/env bash
# FRAMEWORK post-merge hook — fires after successful git pull
# Notifies PCIRT orchestrator of the push event via the pcirt-push webhook.
#
# Installation (already done during FRAMEWORK Phase 0.9):
#   cp scripts/post-merge.hook.sh /opt/agentic-sdlc/.git/hooks/post-merge
#   chmod +x /opt/agentic-sdlc/.git/hooks/post-merge
#
# This hook is NOT tracked by git (git ignores .git/hooks/).
# This scripts/ copy is the reference source — re-deploy after edits.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMMIT=$(git rev-parse HEAD)
BRANCH=$(git rev-parse --abbrev-ref HEAD)

# Fix runtime file permissions — git pull resets tracked files to 644/755
# n8n (uid=1000) needs group-write on services.json and shared output dirs
bash "$REPO_DIR/scripts/fix-permissions.sh" 2>/dev/null || true

# Resolve n8n host — prefer the container network name, fall back to localhost
N8N_HOST="${N8N_HOST:-localhost}"
N8N_PORT="${N8N_PORT:-5678}"
PCIRT_WEBHOOK_URL="http://${N8N_HOST}:${N8N_PORT}/webhook/pcirt-push"

# Fire pcirt-push webhook — errors are non-fatal (|| true prevents hook failure)
curl -s -X POST "${PCIRT_WEBHOOK_URL}" \
  -H "Content-Type: application/json" \
  -d "{\"trigger\": \"git-push\", \"commit\": \"${COMMIT}\", \"branch\": \"${BRANCH}\", \"project\": \"agentic-sdlc\", \"event\": \"post-merge\"}" \
  > /dev/null 2>&1 || true

echo "[FRAMEWORK] Post-merge hook fired for ${BRANCH}@${COMMIT:0:8} → ${PCIRT_WEBHOOK_URL}"
