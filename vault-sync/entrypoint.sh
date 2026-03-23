#!/bin/sh
# vault-inject — entrypoint wrapper for containers that need vault credentials.
#
# Usage in a Dockerfile:
#   COPY --from=vault-sync /app/entrypoint.sh /vault-entrypoint.sh
#   ENTRYPOINT ["/vault-entrypoint.sh"]
#   CMD ["your", "original", "command"]
#
# Required env vars:
#   VAULT_INJECT_SERVICE  — service name (e.g. litellm, n8n, postgres)
#   VAULT_SYNC_URL        — vault-sync base URL (default: http://vault-sync:8777)
#
# On startup:
#   1. Fetches shell-format credentials from vault-sync /inject/{service}?format=shell
#   2. Exports them into the current shell environment
#   3. Execs the original CMD
#
# If VAULT_INJECT_SERVICE is unset or vault-sync is unreachable, the original
# command is executed without modification (non-blocking fail).

set -e

SERVICE="${VAULT_INJECT_SERVICE:-}"
VAULT_URL="${VAULT_SYNC_URL:-http://vault-sync:8777}"
MAX_RETRIES=5
RETRY_DELAY=3

if [ -n "$SERVICE" ]; then
    echo "[vault-inject] Fetching credentials for service: $SERVICE" >&2

    attempt=0
    while [ "$attempt" -lt "$MAX_RETRIES" ]; do
        attempt=$((attempt + 1))
        CREDS=$(wget -q -O- "${VAULT_URL}/inject/${SERVICE}?format=shell" 2>/dev/null) && break
        echo "[vault-inject] Attempt $attempt/$MAX_RETRIES failed, retrying in ${RETRY_DELAY}s..." >&2
        sleep "$RETRY_DELAY"
        CREDS=""
    done

    if [ -n "$CREDS" ]; then
        # shellcheck disable=SC1090
        eval "$CREDS"
        echo "[vault-inject] Credentials injected for $SERVICE" >&2
    else
        echo "[vault-inject] WARNING: Could not fetch credentials for $SERVICE after $MAX_RETRIES attempts — continuing without injection" >&2
    fi
fi

exec "$@"
