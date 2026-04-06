#!/usr/bin/env bash
# vault-init.sh — one-time Vault initialization
# Run ONCE after first `docker compose up vault`.
# Saves unseal keys and root token to /root/.vault-keys (chmod 600).
set -euo pipefail

VAULT_ADDR="${VAULT_ADDR:-http://localhost:8200}"
KEYS_FILE="/root/.vault-keys"

if [[ -f "$KEYS_FILE" ]]; then
    echo "[vault-init] $KEYS_FILE already exists — vault may already be initialized."
    echo "  To re-init, delete $KEYS_FILE first (DESTRUCTIVE — all secrets lost)."
    exit 1
fi

echo "[vault-init] Waiting for Vault to become ready..."
for i in {1..30}; do
    STATUS=$(curl -sf "$VAULT_ADDR/v1/sys/health" 2>/dev/null | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('initialized','?'))" 2>/dev/null || echo "false")
    if [[ "$STATUS" == "False" || "$STATUS" == "false" ]]; then
        echo "[vault-init] Vault is up (uninitialized). Initializing..."
        break
    elif [[ "$STATUS" == "True" || "$STATUS" == "true" ]]; then
        echo "[vault-init] Vault is already initialized. Check $KEYS_FILE."
        exit 0
    fi
    sleep 2
done

echo "[vault-init] Initializing with 5 key shares, 3 required to unseal..."
INIT_OUTPUT=$(curl -sf -X POST "$VAULT_ADDR/v1/sys/init" \
    -H "Content-Type: application/json" \
    -d '{"secret_shares": 5, "secret_threshold": 3}')

ROOT_TOKEN=$(echo "$INIT_OUTPUT" | python3 -c "import json,sys; print(json.load(sys.stdin)['root_token'])")
UNSEAL_KEYS=$(echo "$INIT_OUTPUT" | python3 -c "import json,sys; [print(k) for k in json.load(sys.stdin)['keys_base64']]")

cat > "$KEYS_FILE" << VAULT_KEYS_EOF
# HashiCorp Vault init data — $(date -u +%Y-%m-%dT%H:%M:%SZ)
# PROTECT THIS FILE: chmod 600 $KEYS_FILE
# Unseal requires 3 of 5 keys.
VAULT_ROOT_TOKEN=$ROOT_TOKEN
VAULT_KEYS_EOF

I=1
while IFS= read -r KEY; do
    echo "VAULT_UNSEAL_KEY_$I=$KEY" >> "$KEYS_FILE"
    ((I++))
done <<< "$UNSEAL_KEYS"

chmod 600 "$KEYS_FILE"
echo "[vault-init] Keys saved to $KEYS_FILE"

# Unseal with first 3 keys
echo "[vault-init] Unsealing..."
for N in 1 2 3; do
    KEY=$(grep "VAULT_UNSEAL_KEY_$N=" "$KEYS_FILE" | cut -d= -f2)
    curl -sf -X POST "$VAULT_ADDR/v1/sys/unseal" -H "Content-Type: application/json" -d "{\"key\": \"$KEY\"}" > /dev/null
    echo "[vault-init]   Applied key $N"
done

echo "[vault-init] Vault is unsealed. Configuring..."

export VAULT_TOKEN="$ROOT_TOKEN"

# Enable KV v2
vault kv enable-versioning secret/ 2>/dev/null || \
    curl -sf -X POST "$VAULT_ADDR/v1/sys/mounts/secret/tune" \
        -H "X-Vault-Token: $ROOT_TOKEN" \
        -H "Content-Type: application/json" \
        -d '{"options": {"version": "2"}}' > /dev/null || true

# Check if KV is already v2
curl -sf "$VAULT_ADDR/v1/sys/mounts" -H "X-Vault-Token: $ROOT_TOKEN" | \
    python3 -c "import json,sys; m=json.load(sys.stdin); print('KV version:', m.get('secret/',{}).get('options',{}).get('version','?'))"

# Write policies
echo "[vault-init] Writing policies..."
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

for POLICY in ops services; do
    POLICY_FILE="$SCRIPT_DIR/vault/policies/${POLICY}.hcl"
    if [[ -f "$POLICY_FILE" ]]; then
        curl -sf -X POST "$VAULT_ADDR/v1/sys/policies/acl/$POLICY" \
            -H "X-Vault-Token: $ROOT_TOKEN" \
            -H "Content-Type: application/json" \
            -d "{\"policy\": $(python3 -c "import json; print(json.dumps(open('$POLICY_FILE').read()))")}" > /dev/null
        echo "[vault-init]   Policy '$POLICY' written"
    fi
done

# Enable userpass auth for CLI operators
curl -sf -X POST "$VAULT_ADDR/v1/sys/auth/userpass" \
    -H "X-Vault-Token: $ROOT_TOKEN" \
    -H "Content-Type: application/json" \
    -d '{"type": "userpass"}' > /dev/null || true
echo "[vault-init]   userpass auth enabled"

# Enable AppRole for service accounts
curl -sf -X POST "$VAULT_ADDR/v1/sys/auth/approle" \
    -H "X-Vault-Token: $ROOT_TOKEN" \
    -H "Content-Type: application/json" \
    -d '{"type": "approle"}' > /dev/null || true
echo "[vault-init]   approle auth enabled"

# Create an AppRole for services
curl -sf -X POST "$VAULT_ADDR/v1/auth/approle/role/sdlc-services" \
    -H "X-Vault-Token: $ROOT_TOKEN" \
    -H "Content-Type: application/json" \
    -d '{"policies": ["services"], "token_ttl": "1h", "token_max_ttl": "24h"}' > /dev/null
echo "[vault-init]   AppRole 'sdlc-services' created"

# Get AppRole credentials and save them
ROLE_ID=$(curl -sf "$VAULT_ADDR/v1/auth/approle/role/sdlc-services/role-id" \
    -H "X-Vault-Token: $ROOT_TOKEN" | python3 -c "import json,sys; print(json.load(sys.stdin)['data']['role_id'])")
SECRET_ID=$(curl -sf -X POST "$VAULT_ADDR/v1/auth/approle/role/sdlc-services/secret-id" \
    -H "X-Vault-Token: $ROOT_TOKEN" | python3 -c "import json,sys; print(json.load(sys.stdin)['data']['secret_id'])")

echo "" >> "$KEYS_FILE"
echo "# AppRole for services (used by vault-env.sh)" >> "$KEYS_FILE"
echo "VAULT_ROLE_ID=$ROLE_ID" >> "$KEYS_FILE"
echo "VAULT_SECRET_ID=$SECRET_ID" >> "$KEYS_FILE"

echo "[vault-init] ✓ Vault initialized and configured."
echo "[vault-init]   Root token and keys in: $KEYS_FILE"
echo ""
echo "Next steps:"
echo "  1. bash scripts/vault-push.sh     # migrate current .env.prod secrets to Vault"
echo "  2. Create your ops user:           # vault-init creates AppRole; add your user manually:"
echo "     source /root/.vault-keys"
echo "     curl -X POST http://localhost:8200/v1/auth/userpass/users/relder \\"
echo "       -H \"X-Vault-Token: \$VAULT_ROOT_TOKEN\" \\"
echo "       -d '{\"password\": \"<yourpassword>\", \"policies\": \"ops\"}'"
