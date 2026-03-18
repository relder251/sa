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
latest_backup=$(find ./backup -name "postgres_*.sql.gz" -mtime -1 2>/dev/null | head -1)
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
LITELLM_KEY=$(grep '^LITELLM_API_KEY=' /home/user/vibe_coding/Agentic_SDLC/.env | cut -d= -f2 | sed 's/#.*//' | tr -d '[:space:]')
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
N8N_API_KEY=$(grep '^N8N_API_KEY=' /home/user/vibe_coding/Agentic_SDLC/.env | cut -d= -f2)
wf_count=$(curl -s --max-time 10 \
  -H "X-N8N-API-KEY: $N8N_API_KEY" \
  "http://localhost:5678/api/v1/workflows" 2>/dev/null \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d.get('data',[])))" 2>/dev/null || echo "?")
echo -e "  ${yellow}ℹ INFO${reset}   n8n workflows loaded: $wf_count"

# ── Template variable audit ───────────────────────────────────────────────────
echo ""
echo "── Template variable audit ─────────────────────"
TEMPLATE_DIR="/home/user/vibe_coding/Agentic_SDLC/webui/templates"
MAIN_PY="/home/user/vibe_coding/Agentic_SDLC/webui/main.py"

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
