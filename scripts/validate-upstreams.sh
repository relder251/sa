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
# Test that each portal webhook path is actually registered in an active n8n workflow.
# Strategy: query /api/v1/workflows, scan each workflow's nodes for Webhook trigger
# nodes whose path parameter matches the expected path. Only active workflows count.
echo ""
echo "── n8n portal webhook registrations ────────────"
N8N_API_KEY=$(grep '^N8N_API_KEY=' /opt/agentic-sdlc/.env | cut -d= -f2 | tr -d '[:space:]')

# Fetch all workflow nodes once, extract active webhook paths into a newline-separated list
_all_wh_paths=$(docker exec sa_nginx_private \
  wget -qO- --header "X-N8N-API-KEY: ${N8N_API_KEY}" \
  "http://n8n:5678/api/v1/workflows?limit=100" 2>/dev/null \
  | python3 -c "
import sys, json
try:
  data = json.load(sys.stdin)
  for w in data.get('data', []):
    if not w.get('active', False):
      continue
    for n in w.get('nodes', []):
      if n.get('type') == 'n8n-nodes-base.webhook':
        path = n.get('parameters', {}).get('path', '')
        if path:
          print(path)
except Exception as e:
  pass
" 2>/dev/null || echo "")

for path in portal-services portal-provision portal-update portal-update-categories portal-delete portal-track-recent; do
  if echo "$_all_wh_paths" | grep -qxF "$path"; then
    echo -e "  ${green}✅ PASS${reset}  n8n webhook registered (active): $path"
    PASS=$((PASS + 1))
  else
    echo -e "  ${red}❌ FAIL${reset}  n8n webhook not found in active workflows: $path"
    ERRORS+=("n8n webhook missing or inactive: $path")
    FAIL=$((FAIL + 1))
  fi
done

# ── Config drift check ────────────────────────────────────────────────────────
# Verify that the nginx-private container is serving the repo's config.
# The config is bind-mounted from the repo into the container — they must match.
echo ""
echo "── Config drift check ──────────────────────────"
REPO_PRIVATE="/opt/agentic-sdlc/nginx-private/conf.d/private.conf"

REPO_MD5=$(md5sum "$REPO_PRIVATE" | cut -d' ' -f1)
LIVE_MD5=$(docker exec sa_nginx_private md5sum /etc/nginx/conf.d/private.conf 2>/dev/null | cut -d' ' -f1 || echo "unavailable")

if [ "$REPO_MD5" = "$LIVE_MD5" ]; then
  echo -e "  ${green}✅ PASS${reset}  nginx-private: container config matches repo (md5: $REPO_MD5)"
  PASS=$((PASS + 1))
elif [ "$LIVE_MD5" = "unavailable" ]; then
  echo -e "  ${yellow}⚠ WARN${reset}  nginx-private: could not read container config (container may be stopped)"
else
  echo -e "  ${red}❌ FAIL${reset}  nginx-private: container config DIFFERS from repo"
  echo "  Repo md5:  $REPO_MD5"
  echo "  Live md5:  $LIVE_MD5"
  echo "  Run: docker exec sa_nginx_private cat /etc/nginx/conf.d/private.conf | diff - $REPO_PRIVATE"
  ERRORS+=("nginx-private config drift: container config differs from repo — restart container to reload bind mount")
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
