#!/usr/bin/env bash
# twingate-rotate.sh — regenerate Twingate connector tokens and restart connectors
# Run daily via cron to prevent token expiry. Safe to re-run.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$REPO_DIR/.env.prod"
LOG_PREFIX="[twingate-rotate]"

source "$ENV_FILE"

CONNECTORS=(
  "friendly-jaguar|Q29ubmVjdG9yOjc3NzcwNg==|TWINGATE_ACCESS_TOKEN|TWINGATE_REFRESH_TOKEN|twingate"
  "hasty-spider|Q29ubmVjdG9yOjc3NzM3OA==|TWINGATE_ACCESS_TOKEN_SPIDER|TWINGATE_REFRESH_TOKEN_SPIDER|twingate-spider"
)

for ENTRY in "${CONNECTORS[@]}"; do
  IFS="|" read -r NAME CID ACCESS_KEY REFRESH_KEY SVC <<< "$ENTRY"
  echo "$LOG_PREFIX Rotating tokens for $NAME..."

  RESPONSE=$(curl -sf -X POST https://relder.twingate.com/api/graphql/ \
    -H "Content-Type: application/json" \
    -H "X-API-KEY: $TWINGATE_API_KEY" \
    -d "{\"query\": \"mutation { connectorGenerateTokens(connectorId: \\\"$CID\\\") { connectorTokens { accessToken refreshToken } ok error } }\"}")

  OK=$(echo "$RESPONSE" | python3 -c "import json,sys; print(json.load(sys.stdin)[data][connectorGenerateTokens][ok])")
  if [[ "$OK" != "True" ]]; then
    echo "$LOG_PREFIX ERROR: token generation failed for $NAME"
    echo "$RESPONSE"
    continue
  fi

  NEW_ACCESS=$(echo "$RESPONSE" | python3 -c "import json,sys; print(json.load(sys.stdin)[data][connectorGenerateTokens][connectorTokens][accessToken])")
  NEW_REFRESH=$(echo "$RESPONSE" | python3 -c "import json,sys; print(json.load(sys.stdin)[data][connectorGenerateTokens][connectorTokens][refreshToken])")

  sed -i "s|^${ACCESS_KEY}=.*|${ACCESS_KEY}=${NEW_ACCESS}|" "$ENV_FILE"
  sed -i "s|^${REFRESH_KEY}=.*|${REFRESH_KEY}=${NEW_REFRESH}|" "$ENV_FILE"
  echo "$LOG_PREFIX ✓ $NAME tokens updated in .env.prod"

  cd "$REPO_DIR" && docker compose --env-file "$ENV_FILE" \
    -f docker-compose.yml -f docker-compose.prod.yml \
    up -d --force-recreate "$SVC"
  echo "$LOG_PREFIX ✓ $NAME connector restarted"
done

# Push updated tokens to Vault
echo "$LOG_PREFIX Syncing to Vault..."
bash "$REPO_DIR/scripts/vault-push.sh"
echo "$LOG_PREFIX ✓ Done — all connector tokens rotated"
