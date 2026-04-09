#!/usr/bin/env bash
# vault-unseal.sh — interactively unseal vault-root (Shamir threshold=1)
# Security: unseal key is entered at runtime and never written to disk.
# vault-root (port 8300) is the transit seal provider; main vault auto-unseals once vault-root is open.
set -euo pipefail

# Resolve vault-root container IP dynamically — it has no host port binding
VAULT_ROOT_IP=$(docker inspect vault-root --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' 2>/dev/null | awk '{print $1}')
if [[ -z "$VAULT_ROOT_IP" ]]; then
    echo "[vault-unseal] ERROR: vault-root container is not running."
    exit 1
fi
VAULT_ROOT_ADDR="http://${VAULT_ROOT_IP}:8300"

# Check current seal status
STATUS=$(curl -s "$VAULT_ROOT_ADDR/v1/sys/health" \
    | python3 -c "import json,sys; d=json.load(sys.stdin); print('sealed' if d.get('sealed') else 'unsealed')" \
    2>/dev/null || echo "unreachable")

if [[ "$STATUS" == "unsealed" ]]; then
    echo "[vault-unseal] vault-root is already unsealed."
    exit 0
elif [[ "$STATUS" == "unreachable" ]]; then
    echo "[vault-unseal] ERROR: vault-root is unreachable at $VAULT_ROOT_ADDR"
    exit 1
fi

echo "[vault-unseal] vault-root is sealed."
echo "[vault-unseal] Retrieve the unseal key from Vaultwarden, then enter it below."
echo ""
read -r -s -p "  Unseal key: " UNSEAL_KEY
echo ""

if [[ -z "$UNSEAL_KEY" ]]; then
    echo "[vault-unseal] ERROR: No key entered — aborting."
    exit 1
fi

RESPONSE=$(curl -sf -X POST "$VAULT_ROOT_ADDR/v1/sys/unseal" \
    -H "Content-Type: application/json" \
    -d "{\"key\": \"$UNSEAL_KEY\"}")
SEALED=$(echo "$RESPONSE" | python3 -c "import json,sys; print(json.load(sys.stdin)['sealed'])")

if [[ "$SEALED" == "False" || "$SEALED" == "false" ]]; then
    echo "[vault-unseal] vault-root unsealed successfully."
    echo "[vault-unseal] Main vault will auto-unseal via transit seal within ~30s."
    exit 0
else
    echo "[vault-unseal] ERROR: vault-root is still sealed after key application — wrong key?"
    exit 1
fi
