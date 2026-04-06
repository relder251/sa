#!/usr/bin/env bash
# vault-unseal.sh — auto-unseal Vault after restart using saved keys
# Idempotent: exits cleanly if already unsealed.
set -euo pipefail

VAULT_ADDR="${VAULT_ADDR:-http://localhost:8200}"
KEYS_FILE="/root/.vault-keys"

if [[ ! -f "$KEYS_FILE" ]]; then
    echo "[vault-unseal] ERROR: $KEYS_FILE not found. Run vault-init.sh first."
    exit 1
fi

# Load keys
source "$KEYS_FILE"

# Check current status
STATUS=$(curl -s "$VAULT_ADDR/v1/sys/health" | python3 -c "import json,sys; d=json.load(sys.stdin); print('sealed' if d.get('sealed') else 'unsealed')" 2>/dev/null || echo "unreachable")

if [[ "$STATUS" == "unsealed" ]]; then
    echo "[vault-unseal] Vault is already unsealed."
    exit 0
elif [[ "$STATUS" == "unreachable" ]]; then
    echo "[vault-unseal] ERROR: Vault is unreachable at $VAULT_ADDR"
    exit 1
fi

echo "[vault-unseal] Vault is sealed. Applying 3 of 5 unseal keys..."
for N in 1 2 3; do
    KEY_VAR="VAULT_UNSEAL_KEY_$N"
    KEY="${!KEY_VAR}"
    RESPONSE=$(curl -sf -X POST "$VAULT_ADDR/v1/sys/unseal" \
        -H "Content-Type: application/json" \
        -d "{\"key\": \"$KEY\"}")
    SEALED=$(echo "$RESPONSE" | python3 -c "import json,sys; print(json.load(sys.stdin)['sealed'])")
    echo "[vault-unseal]   Key $N applied — sealed: $SEALED"
    if [[ "$SEALED" == "False" || "$SEALED" == "false" ]]; then
        echo "[vault-unseal] ✓ Vault is unsealed."
        exit 0
    fi
done

echo "[vault-unseal] ERROR: Still sealed after 3 keys — check key file integrity."
exit 1
