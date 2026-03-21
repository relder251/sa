#!/usr/bin/env bash
# smoke_test.sh — post-deploy validation for all Agentic SDLC services
# Run after: docker compose up -d
# Usage: bash scripts/smoke_test.sh
# Exit code 0 = all checks passed, non-zero = failures found

set -euo pipefail

PASS=0
FAIL=0
ERRORS=()

green='\033[0;32m'
red='\033[0;31m'
yellow='\033[0;33m'
reset='\033[0m'

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

check() {
  local label="$1"
  local expected_code="$2"
  local url="$3"
  local extra_args="${4:-}"

  actual_code=$(curl -s -o /tmp/smoke_body.txt -w "%{http_code}" --max-time 10 $extra_args "$url" 2>/dev/null || echo "000")

  if [ "$actual_code" = "$expected_code" ]; then
    echo -e "  ${green}✅ PASS${reset}  [$actual_code] $label"
    PASS=$((PASS + 1))
  else
    echo -e "  ${red}❌ FAIL${reset}  [$actual_code != $expected_code] $label"
    ERRORS+=("$label: expected $expected_code got $actual_code")
    FAIL=$((FAIL + 1))
  fi
}

check_contains() {
  local label="$1"
  local url="$2"
  local expected_text="$3"

  body=$(curl -s --max-time 10 "$url" 2>/dev/null || echo "")

  if echo "$body" | grep -q "$expected_text"; then
    echo -e "  ${green}✅ PASS${reset}  [contains '$expected_text'] $label"
    PASS=$((PASS + 1))
  else
    echo -e "  ${red}❌ FAIL${reset}  [missing '$expected_text'] $label"
    ERRORS+=("$label: response did not contain '$expected_text'")
    FAIL=$((FAIL + 1))
  fi
}

check_container() {
  local label="$1"
  local container="$2"
  local status
  status=$(docker inspect --format '{{.State.Status}}' "$container" 2>/dev/null || echo "not_found")
  if [ "$status" = "running" ]; then
    echo -e "  ${green}✅ PASS${reset}  [running] $label"
    PASS=$((PASS + 1))
  else
    echo -e "  ${red}❌ FAIL${reset}  [$status] $label"
    ERRORS+=("$label: expected running, got $status")
    FAIL=$((FAIL + 1))
  fi
}

# POST to a webhook and assert {"status":"ok"} in response.
# Extra curl args are passed as an array: check_webhook "label" "url" [extra_arg ...]
check_webhook() {
  local label="$1"
  local url="$2"
  shift 2
  local body
  body=$(curl -sk -X POST "$url" \
    -H "Content-Type: application/json" \
    -d '{"smoke_test":true}' \
    --max-time 10 "$@" 2>/dev/null || echo "")
  if echo "$body" | python3 -c "import sys,json; d=json.load(sys.stdin); assert d.get('status')=='ok'" 2>/dev/null; then
    echo -e "  ${green}✅ PASS${reset}  [status=ok] $label"
    PASS=$((PASS + 1))
  else
    echo -e "  ${red}❌ FAIL${reset}  [bad response] $label — ${body:0:120}"
    ERRORS+=("$label: expected {\"status\":\"ok\"}")
    FAIL=$((FAIL + 1))
  fi
}

echo ""
echo "╔══════════════════════════════════════════════╗"
echo "║     Agentic SDLC — Smoke Test Suite         ║"
echo "╚══════════════════════════════════════════════╝"
echo ""

# ── Core infrastructure ───────────────────────────────────────────────────────
echo "── Infrastructure ──────────────────────────────"
check "LiteLLM health"              200 "http://localhost:4000/health/liveliness"
check "n8n health"                  200 "http://localhost:5678/healthz"
check "JupyterLab health"           200 "http://localhost:8888/api"
# test-runner has no host port (internal only) — check via docker exec
tr_health=$(docker exec test_runner python3 -c \
  "import urllib.request; print(urllib.request.urlopen('http://localhost:5001/health').read().decode())" \
  2>/dev/null || echo "error")
if echo "$tr_health" | grep -q '"ok"'; then
  echo -e "  ${green}✅ PASS${reset}  [internal] test-runner health"
  PASS=$((PASS + 1))
else
  echo -e "  ${red}❌ FAIL${reset}  [internal] test-runner health: $tr_health"
  ERRORS+=("test-runner health: $tr_health")
  FAIL=$((FAIL + 1))
fi
check "pipeline-server health"      200 "http://localhost:5002/health"
check "webui health"                200 "http://localhost:3000/health"

# ── Backup service ────────────────────────────────────────────────────────────
echo ""
echo "── Backup service ──────────────────────────────"

backup_status=$(docker inspect --format '{{.State.Status}}' backup 2>/dev/null || echo "not_found")
if [ "$backup_status" = "running" ]; then
  echo -e "  ${green}✅ PASS${reset}  backup container is running"
  PASS=$((PASS + 1))
else
  echo -e "  ${red}❌ FAIL${reset}  backup container status: $backup_status"
  ERRORS+=("backup container: expected running, got $backup_status")
  FAIL=$((FAIL + 1))
fi

# Verify most recent backup file exists and is < 25 hours old
latest_backup=$(find "$REPO_DIR/backup" -name "postgres_*.sql.gz" -mtime -1 2>/dev/null | head -1 || true)
if [ -n "$latest_backup" ]; then
  size=$(du -h "$latest_backup" | cut -f1)
  echo -e "  ${green}✅ PASS${reset}  latest postgres backup: $latest_backup ($size)"
  PASS=$((PASS + 1))
else
  echo -e "  ${yellow}⚠ WARN${reset}   no postgres backup from last 24h (expected after first Ofelia run)"
  # Not a hard failure — backup may not have run yet on first deploy
fi

# ── Web UI pages ──────────────────────────────────────────────────────────────
echo ""
echo "── Web UI pages ────────────────────────────────"
check "Homepage (board)"            200 "http://localhost:3000/"
check "System health page"          200 "http://localhost:3000/system"
check_contains "Homepage renders form"     "http://localhost:3000/"        "Launch Pipeline"
check_contains "Homepage renders board"    "http://localhost:3000/"        "PENDING"
check_contains "System page renders tiers" "http://localhost:3000/system"  "hybrid"

# ── Web UI HTMX partials ──────────────────────────────────────────────────────
echo ""
echo "── Web UI partials ─────────────────────────────"
check "Board partial"               200 "http://localhost:3000/partials/board"
check "System health partial"       200 "http://localhost:3000/partials/system/health"
check "Approvals partial"           200 "http://localhost:3000/partials/approvals"

# ── Run detail pages (for any existing runs) ──────────────────────────────────
echo ""
echo "── Run detail pages ────────────────────────────"
for dir in /home/user/vibe_coding/Agentic_SDLC/output/opportunities/*/; do
  for f in "$dir"*.json; do
    [ -f "$f" ] || continue
    name=$(basename "$f" .json)
    check "Run detail: $name" 200 "http://localhost:3000/runs/$name"
    check "Run phases partial: $name" 200 "http://localhost:3000/partials/run/$name/phases"
    check "Run files partial: $name" 200 "http://localhost:3000/partials/run/$name/files"
  done
done

# ── Pipeline server routes ────────────────────────────────────────────────────
echo ""
echo "── Pipeline server ─────────────────────────────"
check_contains "Pipeline /health returns ok" "http://localhost:5002/health" '"ok"'
check_contains "Pipeline root lists endpoints" "http://localhost:5002/" "run-opportunity"

# ── LiteLLM model tiers ───────────────────────────────────────────────────────
echo ""
echo "── LiteLLM model tiers ─────────────────────────"
LITELLM_KEY=$(grep '^LITELLM_API_KEY=' "$REPO_DIR/.env" | cut -d= -f2 | sed 's/#.*//' | tr -d '[:space:]')
models_body=$(curl -s --max-time 10 \
  -H "Authorization: Bearer $LITELLM_KEY" \
  "http://localhost:4000/model/info" 2>/dev/null || echo "")
if echo "$models_body" | grep -q "hybrid"; then
  echo -e "  ${green}✅ PASS${reset}  [contains 'hybrid'] hybrid/chat registered"
  PASS=$((PASS + 1))
else
  echo -e "  ${red}❌ FAIL${reset}  [missing 'hybrid'] hybrid/chat registered"
  ERRORS+=("hybrid/chat registered: not found in /model/info response")
  FAIL=$((FAIL + 1))
fi

# ── n8n workflow imports ──────────────────────────────────────────────────────
echo ""
echo "── n8n workflows ───────────────────────────────"
N8N_API_KEY=$(grep '^N8N_API_KEY=' "$REPO_DIR/.env" | cut -d= -f2)
wf_count=$(curl -s --max-time 10 \
  -H "X-N8N-API-KEY: $N8N_API_KEY" \
  "http://localhost:5678/api/v1/workflows" 2>/dev/null \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d.get('data',[])))" 2>/dev/null || echo "?")
echo -e "  ${yellow}ℹ INFO${reset}   n8n workflows loaded: $wf_count"

# ── Portal ────────────────────────────────────────────────────────────────────
echo ""
echo "── Portal ──────────────────────────────────────"

# Validate services.json syntax and structure
svc_count=$(python3 -c "
import json, sys
try:
  d = json.load(open('$REPO_DIR/portal/services.json'))
  assert isinstance(d.get('services'), list) and len(d['services']) > 0
  print(len(d['services']))
except Exception as e:
  print('ERR:' + str(e), file=sys.stderr)
  sys.exit(1)
" 2>/tmp/smoke_svc_err)
if [ $? -eq 0 ]; then
  echo -e "  ${green}✅ PASS${reset}  services.json valid JSON ($svc_count services)"
  PASS=$((PASS + 1))
else
  echo -e "  ${red}❌ FAIL${reset}  services.json invalid: $(cat /tmp/smoke_svc_err)"
  ERRORS+=("services.json: $(cat /tmp/smoke_svc_err)")
  FAIL=$((FAIL + 1))
fi

# Portal source HTML contains expected title (catches truncated/wrong file deploys)
if grep -q "SA Portal" "$REPO_DIR/portal/index.html"; then
  echo -e "  ${green}✅ PASS${reset}  [contains 'SA Portal'] portal/index.html"
  PASS=$((PASS + 1))
else
  echo -e "  ${red}❌ FAIL${reset}  [missing 'SA Portal'] portal/index.html"
  ERRORS+=("portal/index.html: 'SA Portal' not found")
  FAIL=$((FAIL + 1))
fi
check_container "portal static server" "portal"

# Portal API + webhook roundtrip — uses a temporary service to avoid corrupting real data
# Hit portal container directly (port 80) to bypass SSO; this tests n8n webhook plumbing.
# The SSO gate (302 redirect) is tested separately in the nginx-private SSO section below.
SMOKE_SVC_ID="smoke-test-$$"
_pcurl() { docker exec portal curl -sk --max-time 10 "$@" 2>/dev/null || echo ""; }

# GET: live services.json via n8n
_before=$(_pcurl "http://localhost/api/portal-services")
_before_count=$(echo "$_before" | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d.get('services',[])))" 2>/dev/null || echo "0")
if [ "$_before_count" -gt 0 ]; then
  echo -e "  ${green}✅ PASS${reset}  [GET /api/portal-services → ${_before_count} services] portal-services API"
  PASS=$((PASS + 1))
else
  echo -e "  ${red}❌ FAIL${reset}  [GET returned 0 or error] portal-services API"
  ERRORS+=("portal-services API: expected >0 services")
  FAIL=$((FAIL + 1))
fi

# PROVISION: add a smoke-test service
_prov=$(_pcurl -X POST "http://localhost/api/portal-provision" \
  -H "Content-Type: application/json" \
  -d "{\"id\":\"$SMOKE_SVC_ID\",\"name\":\"Smoke Test\",\"url\":\"https://example.com\",\"category\":\"infra\"}")
if echo "$_prov" | python3 -c "import sys,json; d=json.load(sys.stdin); assert d.get('status')=='ok'" 2>/dev/null; then
  echo -e "  ${green}✅ PASS${reset}  [status=ok] portal-provision webhook"
  PASS=$((PASS + 1))
else
  echo -e "  ${red}❌ FAIL${reset}  [bad response] portal-provision webhook — ${_prov:0:100}"
  ERRORS+=("portal-provision webhook: expected {\"status\":\"ok\"}")
  FAIL=$((FAIL + 1))
fi

# UPDATE: modify the smoke-test service
_upd=$(_pcurl -X POST "http://localhost/api/portal-update" \
  -H "Content-Type: application/json" \
  -d "{\"id\":\"$SMOKE_SVC_ID\",\"fields\":{\"description\":\"updated by smoke test\"}}")
if echo "$_upd" | python3 -c "import sys,json; d=json.load(sys.stdin); assert d.get('status')=='ok'" 2>/dev/null; then
  echo -e "  ${green}✅ PASS${reset}  [status=ok] portal-update webhook"
  PASS=$((PASS + 1))
else
  echo -e "  ${red}❌ FAIL${reset}  [bad response] portal-update webhook — ${_upd:0:100}"
  ERRORS+=("portal-update webhook: expected {\"status\":\"ok\"}")
  FAIL=$((FAIL + 1))
fi

# UPDATE-CATEGORIES: no-op with current categories (read them, write them back)
_cats=$(echo "$_before" | python3 -c "import sys,json; d=json.load(sys.stdin); print(__import__('json').dumps(d.get('categories',[])))" 2>/dev/null || echo "[]")
_uc=$(_pcurl -X POST "http://localhost/api/portal-update-categories" \
  -H "Content-Type: application/json" \
  -d "{\"categories\":$_cats}")
if echo "$_uc" | python3 -c "import sys,json; d=json.load(sys.stdin); assert d.get('status')=='ok'" 2>/dev/null; then
  echo -e "  ${green}✅ PASS${reset}  [status=ok] portal-update-categories webhook"
  PASS=$((PASS + 1))
else
  echo -e "  ${red}❌ FAIL${reset}  [bad response] portal-update-categories webhook — ${_uc:0:100}"
  ERRORS+=("portal-update-categories webhook: expected {\"status\":\"ok\"}")
  FAIL=$((FAIL + 1))
fi

# DELETE: remove the smoke-test service
_del=$(_pcurl -X POST "http://localhost/api/portal-delete" \
  -H "Content-Type: application/json" \
  -d "{\"id\":\"$SMOKE_SVC_ID\"}")
if echo "$_del" | python3 -c "import sys,json; d=json.load(sys.stdin); assert d.get('status')=='ok' and d.get('deleted',0)==1" 2>/dev/null; then
  echo -e "  ${green}✅ PASS${reset}  [status=ok, deleted=1] portal-delete webhook"
  PASS=$((PASS + 1))
else
  echo -e "  ${red}❌ FAIL${reset}  [bad response] portal-delete webhook — ${_del:0:100}"
  ERRORS+=("portal-delete webhook: expected {\"status\":\"ok\",\"deleted\":1}")
  FAIL=$((FAIL + 1))
fi

# VERIFY: service count is back to original
_after_count=$(_pcurl "http://localhost/api/portal-services" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d.get('services',[])))" 2>/dev/null || echo "-1")
if [ "$_after_count" -eq "$_before_count" ]; then
  echo -e "  ${green}✅ PASS${reset}  [count restored: $_after_count] portal data integrity"
  PASS=$((PASS + 1))
else
  echo -e "  ${red}❌ FAIL${reset}  [count $_after_count != expected $_before_count] portal data integrity"
  ERRORS+=("portal data integrity: service count mismatch after roundtrip")
  FAIL=$((FAIL + 1))
fi

# nginx-private SSO gate — expect 302 (Keycloak redirect), never 5xx
for vhost in home.private.sovereignadvisory.ai n8n.private.sovereignadvisory.ai \
             litellm.private.sovereignadvisory.ai jupyter.private.sovereignadvisory.ai \
             webui.private.sovereignadvisory.ai; do
  code=$(curl -sk -o /dev/null -w "%{http_code}" --max-time 10 \
    -H "Host: $vhost" "https://127.0.0.1/" 2>/dev/null || echo "000")
  if [[ "$code" =~ ^[23] ]]; then
    echo -e "  ${green}✅ PASS${reset}  [$code] nginx-private: $vhost"
    PASS=$((PASS + 1))
  else
    echo -e "  ${red}❌ FAIL${reset}  [$code] nginx-private: $vhost"
    ERRORS+=("nginx-private $vhost: expected 2xx/3xx, got $code")
    FAIL=$((FAIL + 1))
  fi
done

# Portal API SSO enforcement — /api/portal-* must also redirect to login (not 200 unauthed)
_api_code=$(curl -sk -o /dev/null -w "%{http_code}" --max-time 10 \
  -H "Host: home.private.sovereignadvisory.ai" \
  "https://127.0.0.1/api/portal-services" 2>/dev/null || echo "000")
if [[ "$_api_code" =~ ^3 ]]; then
  echo -e "  ${green}✅ PASS${reset}  [$_api_code] portal /api/portal-services requires SSO (no bypass)"
  PASS=$((PASS + 1))
else
  echo -e "  ${red}❌ FAIL${reset}  [$_api_code] portal /api/portal-services should require SSO (got non-3xx)"
  ERRORS+=("portal /api/portal-services SSO: expected 3xx redirect, got $_api_code")
  FAIL=$((FAIL + 1))
fi

# ── Public nginx ──────────────────────────────────────────────────────────────
echo ""
echo "── Public nginx (sa_nginx) ─────────────────────"
check "HTTP→HTTPS redirect" 301 "http://187.77.208.197/"
check_container "sa_nginx"         "sa_nginx"
check_container "sa_nginx_private" "sa_nginx_private"

# ── Ollama ────────────────────────────────────────────────────────────────────
echo ""
echo "── Ollama ──────────────────────────────────────"
check_contains "Ollama API root"   "http://localhost:11434/"          "Ollama is running"
check          "Ollama /api/tags"  200 "http://localhost:11434/api/tags"

# ── Keycloak ──────────────────────────────────────────────────────────────────
echo ""
echo "── Keycloak ─────────────────────────────────────"
check_contains "Keycloak health/live" "http://localhost:8080/health/live" '"UP"'
check          "Keycloak ready"       200 "http://localhost:8080/health/ready"

# ── Vaultwarden ───────────────────────────────────────────────────────────────
echo ""
echo "── Vaultwarden ─────────────────────────────────"
check_container "vaultwarden container" "vaultwarden"

# ── Lead Review ───────────────────────────────────────────────────────────────
echo ""
echo "── Lead Review ─────────────────────────────────"
check "lead-review health" 200 "http://localhost:5003/health"

# ── Support services ─────────────────────────────────────────────────────────
echo ""
echo "── Support services ────────────────────────────"
check_container "ofelia (cron scheduler)"           "ofelia"
check_container "free_model_sync"                   "free_model_sync"
check_container "watchtower"                        "watchtower"
check_container "twingate"                          "twingate"
check_container "oauth2_proxy_portal"               "oauth2_proxy_portal"
check_container "oauth2_proxy_n8n"                  "oauth2_proxy_n8n"
check_container "oauth2_proxy_webui"                "oauth2_proxy_webui"
check_container "oauth2_proxy_litellm"              "oauth2_proxy_litellm"
check_container "oauth2_proxy_jupyter"              "oauth2_proxy_jupyter"

# ── Template variable audit ───────────────────────────────────────────────────
echo ""
echo "── Template variable audit ─────────────────────"
TEMPLATE_DIR="$REPO_DIR/webui/templates"
MAIN_PY="$REPO_DIR/webui/main.py"

# Extract all {{ var }} and {% if var %} usages from templates (top-level vars only)
template_vars=$(grep -roh '{{ *\([a-zA-Z_][a-zA-Z_0-9]*\)' "$TEMPLATE_DIR" | \
  grep -v '__\|loop\|range\|namespace\|joiner\|cycler\|lipsum' | \
  sed 's/.*{{ *//' | sort -u)

# Check each template top-level variable appears in a TemplateResponse context dict
audit_fail=0
while IFS= read -r var; do
  # Skip Jinja2 builtins, loop-scoped vars, and single-letter iteration vars
  case "$var" in
    request|true|false|none|loop|super|caller|varargs|kwargs) continue ;;
    i|key|status|run|f|p|g|svc|m|opp|tier|phase|grouped|counts) continue ;;
    label|dirname|model|phase_status|file|name|val|item|entry|row) continue ;;
  esac
  if ! grep -q "\"$var\"" "$MAIN_PY" 2>/dev/null; then
    echo -e "  ${yellow}⚠ WARN${reset}   Template var '$var' not found in main.py context dicts"
    audit_fail=$((audit_fail + 1))
  fi
done <<< "$template_vars"

if [ "$audit_fail" -eq 0 ]; then
  echo -e "  ${green}✅ PASS${reset}  Template variable audit clean"
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "════════════════════════════════════════════════"
TOTAL=$((PASS + FAIL))
if [ $FAIL -eq 0 ]; then
  echo -e "${green}✅ ALL $TOTAL CHECKS PASSED${reset}"
else
  echo -e "${red}❌ $FAIL/$TOTAL CHECKS FAILED${reset}"
  echo ""
  echo "Failures:"
  for err in "${ERRORS[@]}"; do
    echo -e "  ${red}•${reset} $err"
  done
fi
echo "════════════════════════════════════════════════"
echo ""

exit $FAIL
