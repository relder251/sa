#!/usr/bin/env bash
# vault-rotate.sh — rotate one or more secrets in Vault and refresh .env.prod
# Usage: bash scripts/vault-rotate.sh KEY1=newvalue KEY2=newvalue ...
# Example (rotate Groq key):
#   bash scripts/vault-rotate.sh GROQ_API_KEY=gsk_newkeyhere
set -euo pipefail

VAULT_ADDR="${VAULT_ADDR:-http://localhost:8200}"
KEYS_FILE="/root/.vault-keys"
SECRET_PATH="sdlc/prod"

if [[ $# -eq 0 ]]; then
    echo "Usage: $0 KEY=value [KEY2=value2 ...]"
    exit 1
fi

source "$KEYS_FILE"
VAULT_TOKEN="${VAULT_TOKEN:-$VAULT_ROOT_TOKEN}"
export VAULT_TOKEN VAULT_ADDR

# Build patch JSON (only update specified keys, preserve others via KV v2 patch)
UPDATES="{}"
for ARG in "$@"; do
    KEY="${ARG%%=*}"
    VAL="${ARG#*=}"
    UPDATES=$(echo "$UPDATES" | python3 -c "
import json, sys
d = json.load(sys.stdin)
d['$KEY'] = '$VAL'
print(json.dumps(d))
")
    echo "[vault-rotate] Queued: $KEY=***"
done

PATCH_PAYLOAD="{\"data\": $UPDATES}"

echo "[vault-rotate] Patching secret/data/$SECRET_PATH..."
RESPONSE=$(curl -sf -X PATCH "$VAULT_ADDR/v1/secret/data/$SECRET_PATH" \
    -H "X-Vault-Token: $VAULT_TOKEN" \
    -H "Content-Type: application/merge-patch+json" \
    -d "$PATCH_PAYLOAD")

VERSION=$(echo "$RESPONSE" | python3 -c "import json,sys; print(json.load(sys.stdin)['data']['version'])")
echo "[vault-rotate] ✓ Secret updated — new version: $VERSION"

echo "[vault-rotate] Refreshing .env.prod from Vault..."
bash "$(dirname "${BASH_SOURCE[0]}")/vault-env.sh"

echo "[vault-rotate] ✓ Done. Restart affected services to pick up new values:"
echo "   make restart SVC=<service> ENV=prod"
