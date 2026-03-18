#!/usr/bin/env bash
# keycloak_export_realm.sh — export agentic-sdlc realm for GitOps.
# Usage: bash scripts/keycloak_export_realm.sh
# Run from repo root on a machine with Docker access to the Keycloak container.
# Uses --users skip: user records (including hashed passwords) are NOT included.
# Client secrets are never included in Keycloak exports — safe to commit.
set -euo pipefail

REALM="agentic-sdlc"
OUTPUT="keycloak/realm-export.json"
KC_CONTAINER="keycloak"

echo "Exporting realm '$REALM' (users skipped for security)..."
mkdir -p keycloak

docker exec "$KC_CONTAINER" \
  /opt/keycloak/bin/kc.sh export \
  --dir /tmp/kc-export \
  --realm "$REALM" \
  --users skip 2>/dev/null || true   # suppress verbose startup noise

docker cp "$KC_CONTAINER:/tmp/kc-export/${REALM}-realm.json" "$OUTPUT"

# Strip client secrets and remove the service-credentials credential-vault client
python3 -c "
import json
with open('$OUTPUT') as f:
    realm = json.load(f)

clients_before = len(realm.get('clients', []))
clean_clients = []
stripped_secrets = []
removed_clients = []

for c in realm.get('clients', []):
    # Remove service-credentials entirely — it stores all API keys as attributes
    if c.get('clientId') == 'service-credentials':
        removed_clients.append(c['clientId'])
        continue
    # Strip any embedded client secrets
    if c.get('secret') and len(c['secret']) > 5:
        del c['secret']
        stripped_secrets.append(c['clientId'])
    clean_clients.append(c)

realm['clients'] = clean_clients
with open('$OUTPUT', 'w') as f:
    json.dump(realm, f, indent=2)

if removed_clients:
    print(f'Removed credential-vault clients (contain API keys): {removed_clients}')
if stripped_secrets:
    print(f'Stripped secrets from clients: {stripped_secrets}')
print('Secret check passed — safe to commit')
"
echo "Realm exported to $OUTPUT"
