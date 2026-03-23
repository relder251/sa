#!/bin/sh
# vault-inject — entrypoint wrapper for containers that need vault credentials.
#
# Usage in docker-compose.yml:
#   volumes:
#     - ./vault-sync/entrypoint.sh:/vault-entrypoint.sh:ro
#   entrypoint: ["/bin/sh", "/vault-entrypoint.sh"]
#   command: [... original command ...]
#   environment:
#     - VAULT_INJECT_SERVICE=<service-name>
#     - VAULT_SYNC_URL=http://vault-sync:8777
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

_fetch() {
    # Try curl → wget → python3, whichever is available
    URL="${VAULT_URL}/inject/${SERVICE}?format=shell"
    if command -v curl > /dev/null 2>&1; then
        curl -sf --connect-timeout 5 "$URL" 2>/dev/null
    elif command -v wget > /dev/null 2>&1; then
        wget -q -O- "$URL" 2>/dev/null
    elif command -v python3 > /dev/null 2>&1; then
        python3 -c "
import urllib.request, sys
try:
    with urllib.request.urlopen('$URL', timeout=5) as r:
        sys.stdout.write(r.read().decode())
except Exception:
    pass
" 2>/dev/null
    fi
}

if [ -n "$SERVICE" ]; then
    echo "[vault-inject] Fetching credentials for service: $SERVICE" >&2

    attempt=0
    CREDS=""
    while [ "$attempt" -lt "$MAX_RETRIES" ]; do
        attempt=$((attempt + 1))
        CREDS=$(_fetch) && [ -n "$CREDS" ] && break
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
