#!/usr/bin/env bash
# validate-upstreams.sh — test every nginx proxy_pass upstream for reachability
# Runs on VPS inside the docker network. Exit 0 = all reachable, non-zero = failures.
# Usage: bash scripts/validate-upstreams.sh
set -euo pipefail

PASS=0
FAIL=0
ERRORS=()

green='\033[0;32m'
red='\033[0;31m'
yellow='\033[0;33m'
reset='\033[0m'

# Test an upstream from inside a given container.
# Returns non-502/503/000 as PASS (upstream is reachable).
check_upstream() {
  local label="$1"
  local from_container="$2"
  local upstream_url="$3"

  code=$(docker exec "$from_container" \
    curl -sk -o /dev/null -w "%{http_code}" --max-time 5 "$upstream_url" 2>/dev/null || echo "000")

  if [[ "$code" == "000" || "$code" == "502" || "$code" == "503" ]]; then
    echo -e "  ${red}❌ FAIL${reset}  [$code] $label → $upstream_url"
    ERRORS+=("$label: upstream $upstream_url returned $code (connection failed or refused)")
    FAIL=$((FAIL + 1))
  else
    echo -e "  ${green}✅ PASS${reset}  [$code] $label → $upstream_url"
    PASS=$((PASS + 1))
  fi
}

# WebSocket: check the negotiate endpoint specifically
check_ws_upstream() {
  local label="$1"
  local from_container="$2"
  local negotiate_url="$3"

  code=$(docker exec "$from_container" \
    curl -sk -o /dev/null -w "%{http_code}" --max-time 5 "$negotiate_url" 2>/dev/null || echo "000")

  if [[ "$code" == "000" || "$code" == "502" || "$code" == "503" ]]; then
    echo -e "  ${red}❌ FAIL${reset}  [$code] $label (WebSocket/negotiate) → $negotiate_url"
    ERRORS+=("$label WebSocket: $negotiate_url returned $code")
    FAIL=$((FAIL + 1))
  else
    echo -e "  ${green}✅ PASS${reset}  [$code] $label (WebSocket/negotiate) → $negotiate_url"
    PASS=$((PASS + 1))
  fi
}

echo ""
echo "╔══════════════════════════════════════════════╗"
echo "║   nginx Upstream Reachability Validation    ║"
echo "╚══════════════════════════════════════════════╝"
echo ""

# ── sa_nginx_private upstreams ────────────────────────────────────────────────
echo "── sa_nginx_private upstreams ──────────────────"
check_upstream "oauth2_proxy_n8n"       "sa_nginx_private" "http://oauth2_proxy_n8n:5679/"
check_upstream "oauth2_proxy_webui"     "sa_nginx_private" "http://oauth2_proxy_webui:3001/"
check_upstream "oauth2_proxy_litellm"   "sa_nginx_private" "http://oauth2_proxy_litellm:4001/"
check_upstream "oauth2_proxy_jupyter"   "sa_nginx_private" "http://oauth2_proxy_jupyter:8889/"
check_upstream "ollama"                 "sa_nginx_private" "http://ollama:11434/"
check_upstream "vaultwarden (main)"     "sa_nginx_private" "http://vaultwarden:80/"
check_upstream "keycloak"               "sa_nginx_private" "http://keycloak:8080/health/live"
check_upstream "glitchtip_web"          "sa_nginx_private" "http://glitchtip_web:8000/"
check_upstream "n8n (webhook path)"     "sa_nginx_private" "http://n8n:5678/healthz"
check_upstream "oauth2_proxy_portal"    "sa_nginx_private" "http://oauth2_proxy_portal:4185/"

# WebSocket upstreams (must not be 502)
echo ""
echo "── WebSocket upstreams ─────────────────────────"
check_ws_upstream "vaultwarden notifications/hub" "sa_nginx_private" \
  "http://vaultwarden:80/notifications/hub/negotiate"

# ── portal container upstreams ───────────────────────────────────────────────
echo ""
echo "── portal nginx upstreams ──────────────────────"
check_upstream "litellm (portal)"       "portal" "http://litellm:4000/health/liveliness"
check_upstream "n8n portal-services"    "portal" "http://n8n:5678/healthz"
check_upstream "oauth2_proxy_portal"    "portal" "http://oauth2_proxy_portal:4185/"
check_ws_upstream "shell_gateway (terminal)" "portal" "http://shell_gateway:7681/"

# ── n8n webhook registrations ─────────────────────────────────────────────────
# Test that each portal webhook path is actually registered in n8n
echo ""
echo "── n8n portal webhook registrations ────────────"
N8N_API_KEY=$(grep '^N8N_API_KEY=' /opt/agentic-sdlc/.env | cut -d= -f2 | tr -d '[:space:]')
for path in portal-services portal-provision portal-update portal-update-categories portal-delete portal-track-recent; do
  wh_count=$(docker exec n8n curl -sk \
    -H "X-N8N-API-KEY: ${N8N_API_KEY}" \
    "http://localhost:5678/api/v1/webhooks" 2>/dev/null \
    | python3 -c "
import sys, json
try:
  data = json.load(sys.stdin)
  webhooks = data if isinstance(data, list) else data.get('data', [])
  matches = [w for w in webhooks if '${path}' in w.get('webhookPath','') or '${path}' in w.get('path','')]
  print(len(matches))
except:
  print(0)
" 2>/dev/null || echo "0")

  if [ "$wh_count" -gt 0 ]; then
    echo -e "  ${green}✅ PASS${reset}  n8n webhook registered: $path"
    PASS=$((PASS + 1))
  else
    echo -e "  ${yellow}⚠ WARN${reset}   n8n webhook not found via API: $path (may use legacy path format)"
    # Warn only — legacy workflowId-prefixed paths won't appear in /api/v1/webhooks the same way
  fi
done

# ── Config drift check ────────────────────────────────────────────────────────
# Verify that VPS nginx config matches git repo (catches live edits that weren't committed)
echo ""
echo "── Config drift check ──────────────────────────"
REPO_PRIVATE="/opt/agentic-sdlc/nginx-private/conf.d/private.conf"
LIVE_PRIVATE="/etc/nginx/conf.d/private.conf"

if diff -q "$REPO_PRIVATE" "$LIVE_PRIVATE" > /dev/null 2>&1; then
  echo -e "  ${green}✅ PASS${reset}  nginx-private: live config matches repo"
  PASS=$((PASS + 1))
else
  echo -e "  ${red}❌ FAIL${reset}  nginx-private: live config DIFFERS from repo"
  echo "  Run: diff $REPO_PRIVATE $LIVE_PRIVATE"
  ERRORS+=("nginx-private config drift: live /etc/nginx/conf.d/private.conf differs from repo")
  FAIL=$((FAIL + 1))
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "════════════════════════════════════════════════"
TOTAL=$((PASS + FAIL))
if [ $FAIL -eq 0 ]; then
  echo -e "${green}✅ ALL $TOTAL UPSTREAM CHECKS PASSED${reset}"
else
  echo -e "${red}❌ $FAIL/$TOTAL UPSTREAM CHECKS FAILED${reset}"
  echo ""
  for err in "${ERRORS[@]}"; do
    echo -e "  ${red}•${reset} $err"
  done
fi
echo "════════════════════════════════════════════════"
echo ""

exit $FAIL
