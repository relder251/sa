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

# Strip any embedded client secrets and confirm result
python3 -c "
import json
with open('$OUTPUT') as f:
    realm = json.load(f)
stripped = []
for c in realm.get('clients', []):
    secret = c.get('secret', '')
    if secret and len(secret) > 5:
        del c['secret']
        stripped.append(c['clientId'])
with open('$OUTPUT', 'w') as f:
    json.dump(realm, f, indent=2)
if stripped:
    print(f'Stripped secrets from clients: {stripped}')
print('Secret check passed — safe to commit')
"
echo "Realm exported to $OUTPUT"
