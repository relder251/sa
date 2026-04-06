#!/usr/bin/env bash
# vault-push.sh — migrate .env.prod into Vault KV v2 at secret/sdlc/prod
# Run once during migration; safe to re-run (KV v2 versions each write).
set -euo pipefail

VAULT_ADDR="${VAULT_ADDR:-http://localhost:8200}"
KEYS_FILE="/root/.vault-keys"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$REPO_DIR/.env.prod"
SECRET_PATH="sdlc/prod"

if [[ ! -f "$KEYS_FILE" ]]; then
    echo "[vault-push] ERROR: $KEYS_FILE not found. Run vault-init.sh first."
    exit 1
fi

source "$KEYS_FILE"
VAULT_TOKEN="${VAULT_TOKEN:-$VAULT_ROOT_TOKEN}"
export VAULT_TOKEN VAULT_ADDR

# Verify Vault is unsealed
STATUS=$(curl -sf "$VAULT_ADDR/v1/sys/health" | python3 -c "import json,sys; d=json.load(sys.stdin); print('sealed' if d.get('sealed') else 'ok')" 2>/dev/null)
if [[ "$STATUS" != "ok" ]]; then
    echo "[vault-push] Vault is sealed or unreachable. Run vault-unseal.sh first."
    exit 1
fi

echo "[vault-push] Reading $ENV_FILE..."
# Build JSON payload from .env.prod (skip blanks and comments)
SECRET_JSON=$(python3 << 'PYEOF'
import json, sys, re

env = {}
with open("/opt/agentic-sdlc/.env.prod") as f:
    for line in f:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.split(" #")[0].strip()  # strip inline comments
        if key:
            env[key] = val

print(json.dumps({"data": env}))
PYEOF
)

KEY_COUNT=$(echo "$SECRET_JSON" | python3 -c "import json,sys; print(len(json.load(sys.stdin)['data']))")
echo "[vault-push] Writing $KEY_COUNT keys to secret/data/$SECRET_PATH..."

RESPONSE=$(curl -sf -X POST "$VAULT_ADDR/v1/secret/data/$SECRET_PATH" \
    -H "X-Vault-Token: $VAULT_TOKEN" \
    -H "Content-Type: application/json" \
    -d "$SECRET_JSON")

VERSION=$(echo "$RESPONSE" | python3 -c "import json,sys; print(json.load(sys.stdin)['data']['version'])")
echo "[vault-push] ✓ Written as version $VERSION at secret/data/$SECRET_PATH"
echo "[vault-push]   Verify: curl -s $VAULT_ADDR/v1/secret/data/$SECRET_PATH -H 'X-Vault-Token: \$VAULT_TOKEN' | python3 -m json.tool | head -20"
