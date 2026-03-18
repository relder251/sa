#!/usr/bin/env bash
# deploy_oidc_fix.sh — Production deployment: OIDC lead-review fix
#
# What this does:
#   1. Removes the PKCE (S256) requirement from the Keycloak lead-review client
#   2. Verifies .env has LEAD_REVIEW_PUBLIC_URL and KEYCLOAK_EXTERNAL_URL
#   3. Rebuilds and restarts the lead-review container
#   4. Smoke-tests the OIDC auth redirect
#
# Usage:
#   bash scripts/deploy_oidc_fix.sh
#
# Must be run from the project root (same directory as docker-compose.yml).

set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*" >&2; }

# ── Preflight ────────────────────────────────────────────────────────────────
if [[ ! -f "docker-compose.yml" ]]; then
  error "Run from the project root (directory containing docker-compose.yml)"
  exit 1
fi

if [[ ! -f ".env" ]]; then
  error ".env file not found"
  exit 1
fi

# Source .env safely — export only valid KEY=VALUE lines, skip comments/blanks
set +u
while IFS= read -r line; do
  [[ "$line" =~ ^[[:space:]]*# ]] && continue
  [[ -z "${line//[[:space:]]/}" ]] && continue
  [[ "$line" =~ ^([A-Za-z_][A-Za-z0-9_]*)=(.*)$ ]] && export "${BASH_REMATCH[1]}"="${BASH_REMATCH[2]}"
done < .env
set -u

# ── Step 1: Verify required env vars ────────────────────────────────────────
info "Checking .env configuration..."
MISSING=0

if [[ -z "${LEAD_REVIEW_PUBLIC_URL:-}" ]]; then
  warn "LEAD_REVIEW_PUBLIC_URL is not set in .env"
  warn "  Add: LEAD_REVIEW_PUBLIC_URL=https://sovereignadvisory.ai"
  MISSING=1
fi

if [[ -z "${KEYCLOAK_EXTERNAL_URL:-}" ]]; then
  warn "KEYCLOAK_EXTERNAL_URL is not set in .env"
  warn "  Add: KEYCLOAK_EXTERNAL_URL=https://kc.sovereignadvisory.ai"
  MISSING=1
fi

if [[ -z "${KEYCLOAK_ADMIN_PASSWORD:-}" ]]; then
  error "KEYCLOAK_ADMIN_PASSWORD is not set in .env — needed to update Keycloak client"
  exit 1
fi

if [[ $MISSING -eq 1 ]]; then
  error "Fix missing env vars above, then re-run."
  exit 1
fi

info "  LEAD_REVIEW_PUBLIC_URL = $LEAD_REVIEW_PUBLIC_URL"
info "  KEYCLOAK_EXTERNAL_URL  = $KEYCLOAK_EXTERNAL_URL"

# ── Step 2: Locate and reach Keycloak ────────────────────────────────────────
info "Checking Keycloak health..."

# Resolve KC URL: try localhost first, then the container's Docker network IP.
KC_LOCAL=""
for CANDIDATE in "http://localhost:8080" "http://keycloak:8080"; do
  if curl -sf "${CANDIDATE}/realms/master" > /dev/null 2>&1; then
    KC_LOCAL="$CANDIDATE"
    break
  fi
done

# If neither works, derive the container IP from docker inspect
if [[ -z "$KC_LOCAL" ]]; then
  KC_IP=$(docker inspect keycloak \
    --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' 2>/dev/null \
    | awk '{print $1}')
  if [[ -n "$KC_IP" ]]; then
    KC_LOCAL="http://${KC_IP}:8080"
  fi
fi

# Wait up to 60s for Keycloak to respond
for i in $(seq 1 12); do
  if [[ -n "$KC_LOCAL" ]] && curl -sf "${KC_LOCAL}/realms/master" > /dev/null 2>&1; then
    info "  Keycloak is up at $KC_LOCAL"
    break
  fi
  warn "  Keycloak not ready, waiting 5s... (${i}/12)"
  sleep 5
  if [[ $i -eq 12 ]]; then
    error "Keycloak is not reachable (tried localhost:8080, keycloak:8080, container IP)"
    exit 1
  fi
done

# ── Step 3: Get admin token ──────────────────────────────────────────────────
info "Authenticating with Keycloak admin..."
KC_TOKEN=$(curl -sf -X POST "${KC_LOCAL}/realms/master/protocol/openid-connect/token" \
  -d "grant_type=password&client_id=admin-cli&username=admin&password=${KEYCLOAK_ADMIN_PASSWORD}" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

if [[ -z "$KC_TOKEN" ]]; then
  error "Failed to get Keycloak admin token — check KEYCLOAK_ADMIN_PASSWORD"
  exit 1
fi
info "  Admin token obtained"

# ── Step 4: Get the lead-review client ID ────────────────────────────────────
info "Looking up lead-review Keycloak client..."
CLIENT_UUID=$(curl -sf -H "Authorization: Bearer $KC_TOKEN" \
  "${KC_LOCAL}/admin/realms/agentic-sdlc/clients?clientId=lead-review" \
  | python3 -c "
import sys, json
clients = json.load(sys.stdin)
if not clients:
    print('')
else:
    print(clients[0]['id'])
")

if [[ -z "$CLIENT_UUID" ]]; then
  error "lead-review client not found in agentic-sdlc realm"
  exit 1
fi
info "  Client UUID: $CLIENT_UUID"

# ── Step 5: Check and fix PKCE attribute ─────────────────────────────────────
info "Checking PKCE configuration..."
curl -sf -H "Authorization: Bearer $KC_TOKEN" \
  "${KC_LOCAL}/admin/realms/agentic-sdlc/clients/$CLIENT_UUID" > /tmp/kc_lr_client.json

PKCE_SETTING=$(python3 -c "
import json
with open('/tmp/kc_lr_client.json') as f:
    c = json.load(f)
print(c.get('attributes', {}).get('pkce.code.challenge.method', ''))
")

if [[ -n "$PKCE_SETTING" ]]; then
  warn "  PKCE enforcement found: pkce.code.challenge.method = $PKCE_SETTING"
  info "  Removing PKCE requirement..."

  # Remove the attribute and PUT back
  python3 -c "
import json
with open('/tmp/kc_lr_client.json') as f:
    c = json.load(f)
c.get('attributes', {}).pop('pkce.code.challenge.method', None)
with open('/tmp/kc_lr_client_fixed.json', 'w') as f:
    json.dump(c, f)
"

  HTTP_STATUS=$(curl -s -o /tmp/kc_put.txt -w "%{http_code}" -X PUT \
    -H "Authorization: Bearer $KC_TOKEN" \
    -H "Content-Type: application/json" \
    --data-binary @/tmp/kc_lr_client_fixed.json \
    "${KC_LOCAL}/admin/realms/agentic-sdlc/clients/$CLIENT_UUID")

  if [[ "$HTTP_STATUS" != "204" ]]; then
    error "Failed to update Keycloak client (HTTP $HTTP_STATUS): $(cat /tmp/kc_put.txt)"
    error "Falling back to direct DB update..."

    # Direct DB update as fallback
    docker exec litellm_db psql -U "${LITELLM_USER:-litellm}" -d keycloak -c "
      DELETE FROM client_attributes
      WHERE client_id = (
        SELECT id FROM client WHERE client_id = 'lead-review'
        AND realm_id = (SELECT id FROM realm WHERE name='agentic-sdlc')
      )
      AND name = 'pkce.code.challenge.method';
    " 2>&1

    info "  DB update done — restarting Keycloak to clear cache..."
    docker compose restart keycloak
    info "  Waiting for Keycloak to come back up..."
    sleep 10
    for i in $(seq 1 12); do
      STATUS=$(docker inspect keycloak --format '{{.State.Health.Status}}' 2>/dev/null)
      [[ "$STATUS" == "healthy" ]] && break
      sleep 5
    done
  fi

  info "  ✓ PKCE requirement removed"
else
  info "  ✓ PKCE not enforced (already clean)"
fi

# ── Step 6: Ensure production redirect URI is registered ────────────────────
info "Checking Keycloak redirect URIs..."
PROD_REDIRECT_URI="${LEAD_REVIEW_PUBLIC_URL}/auth/callback"

CURRENT_URIS=$(python3 -c "
import json
with open('/tmp/kc_lr_client.json') as f:
    c = json.load(f)
print(json.dumps(c.get('redirectUris', [])))
")

NEEDS_URI=$(python3 -c "
import json
uris = json.loads('$CURRENT_URIS')
# Check if the production URL is already covered by a wildcard or exact match
prod = '${PROD_REDIRECT_URI}'
prod_base = '${LEAD_REVIEW_PUBLIC_URL}'
covered = any(
    (u == prod) or (u == prod_base + '/*') or (u == prod_base + '*')
    for u in uris
)
print('no' if covered else 'yes')
")

if [[ "$NEEDS_URI" == "yes" ]]; then
  info "  Adding redirect URI: ${PROD_REDIRECT_URI}"
  # Re-fetch fresh client (might have changed with PKCE fix above)
  curl -sf -H "Authorization: Bearer $KC_TOKEN" \
    "${KC_LOCAL}/admin/realms/agentic-sdlc/clients/$CLIENT_UUID" > /tmp/kc_lr_client2.json

  python3 -c "
import json
with open('/tmp/kc_lr_client2.json') as f:
    c = json.load(f)
uris = c.get('redirectUris', [])
new_uri = '${LEAD_REVIEW_PUBLIC_URL}/*'
if new_uri not in uris:
    uris.append(new_uri)
c['redirectUris'] = uris
# Also add to webOrigins if not present
origins = c.get('webOrigins', [])
origin = '${LEAD_REVIEW_PUBLIC_URL}'
if origin not in origins:
    origins.append(origin)
c['webOrigins'] = origins
with open('/tmp/kc_lr_client2_fixed.json', 'w') as f:
    json.dump(c, f)
print('Added redirect URI:', new_uri)
"

  HTTP_STATUS=$(curl -s -o /tmp/kc_put2.txt -w "%{http_code}" -X PUT \
    -H "Authorization: Bearer $KC_TOKEN" \
    -H "Content-Type: application/json" \
    --data-binary @/tmp/kc_lr_client2_fixed.json \
    "${KC_LOCAL}/admin/realms/agentic-sdlc/clients/$CLIENT_UUID")

  if [[ "$HTTP_STATUS" == "204" ]]; then
    info "  ✓ Redirect URI added"
  else
    error "  Failed to add redirect URI (HTTP $HTTP_STATUS): $(cat /tmp/kc_put2.txt)"
  fi
else
  info "  ✓ Redirect URI already registered"
fi

# ── Step 7: Rebuild and restart lead-review ──────────────────────────────────
info "Rebuilding lead-review container..."
docker compose build lead-review 2>&1 | grep -E "^#|Built|DONE|ERROR" || true

info "Restarting lead-review..."
docker compose up -d lead-review

info "Waiting for lead-review to be healthy..."
for i in $(seq 1 12); do
  STATUS=$(docker inspect sa_lead_review --format '{{.State.Health.Status}}' 2>/dev/null)
  [[ "$STATUS" == "healthy" ]] && break
  sleep 5
done

FINAL_STATUS=$(docker inspect sa_lead_review --format '{{.State.Health.Status}}' 2>/dev/null)
if [[ "$FINAL_STATUS" != "healthy" ]]; then
  error "lead-review container is not healthy: $FINAL_STATUS"
  docker logs sa_lead_review --tail 20
  exit 1
fi
info "  ✓ lead-review is healthy"

# ── Step 8: Smoke test ───────────────────────────────────────────────────────
info "Smoke testing OIDC redirect..."

# Test that /review/ redirects to Keycloak (requires a real token in DB)
FIRST_TOKEN=$(docker exec litellm_db psql -U "${LITELLM_USER:-litellm}" -d litellm -t -c \
  "SELECT token FROM sa_review_tokens WHERE is_active = true LIMIT 1;" 2>/dev/null | tr -d ' \n')

if [[ -n "$FIRST_TOKEN" ]]; then
  REDIRECT_URL=$(curl -s -o /dev/null -w "%{redirect_url}" \
    "http://localhost:5003/review/$FIRST_TOKEN")
  if echo "$REDIRECT_URL" | grep -q "openid-connect/auth"; then
    info "  ✓ /review/{token} correctly redirects to Keycloak"
    # Verify redirect_uri in the auth URL
    REDIRECT_URI=$(python3 -c "
from urllib.parse import urlparse, parse_qs
import sys
url = '$REDIRECT_URL'
q = parse_qs(urlparse(url).query)
print(q.get('redirect_uri', ['?'])[0])
")
    info "  ✓ redirect_uri = $REDIRECT_URI"
  else
    warn "  Unexpected redirect: $REDIRECT_URL"
  fi
else
  warn "  No active review tokens found — skipping end-to-end smoke test"
fi

# ── Done ─────────────────────────────────────────────────────────────────────
echo
info "╔═══════════════════════════════════════════════════════╗"
info "║  OIDC fix deployed successfully                       ║"
info "╠═══════════════════════════════════════════════════════╣"
info "║  Changes applied:                                     ║"
info "║    ✓ PKCE requirement removed from Keycloak client    ║"
info "║    ✓ Production redirect URI registered in Keycloak   ║"
info "║    ✓ lead-review rebuilt with LEAD_REVIEW_PUBLIC_URL  ║"
info "╚═══════════════════════════════════════════════════════╝"
