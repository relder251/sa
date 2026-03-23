#!/usr/bin/env bash
# validate-prod.sh — master production validation gate
# Runs smoke_test.sh + validate-upstreams.sh on VPS, then validate-browser.sh locally.
# This is the canonical TEST_CMD for the completion gate.
#
# Usage: bash scripts/validate-prod.sh [--skip-browser] [--skip-smoke] [--skip-upstreams]
# Exit 0 = all suites passed, non-zero = at least one suite failed
set -euo pipefail

VPS="root@187.77.208.197"
REPO_DIR_VPS="/opt/agentic-sdlc"
REPO_DIR_LOCAL="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

SKIP_BROWSER=false
SKIP_SMOKE=false
SKIP_UPSTREAMS=false

for arg in "$@"; do
  case "$arg" in
    --skip-browser)    SKIP_BROWSER=true ;;
    --skip-smoke)      SKIP_SMOKE=true ;;
    --skip-upstreams)  SKIP_UPSTREAMS=true ;;
  esac
done

green='\033[0;32m'
red='\033[0;31m'
yellow='\033[0;33m'
bold='\033[1m'
reset='\033[0m'

SUITE_PASS=0
SUITE_FAIL=0
SUITE_ERRORS=()

run_suite() {
  local name="$1"
  local skip="$2"
  shift 2
  local cmd=("$@")

  if [ "$skip" = "true" ]; then
    echo -e "  ${yellow}⏭  SKIP${reset}  $name"
    return
  fi

  echo ""
  echo -e "${bold}▶ Running: $name${reset}"
  echo "  Command: ${cmd[*]}"
  echo "  ─────────────────────────────────────────────"

  if "${cmd[@]}"; then
    echo -e "  ${green}✅ SUITE PASS${reset}  $name"
    SUITE_PASS=$((SUITE_PASS + 1))
  else
    local exit_code=$?
    echo -e "  ${red}❌ SUITE FAIL${reset}  $name (exit $exit_code)"
    SUITE_ERRORS+=("$name")
    SUITE_FAIL=$((SUITE_FAIL + 1))
  fi
}

echo ""
echo "╔════════════════════════════════════════════════╗"
echo "║     Production Validation Gate                ║"
echo "╚════════════════════════════════════════════════╝"
echo ""
echo "VPS: $VPS"
echo "Started: $(date)"

# ── Verify SSH reachable ──────────────────────────────────────────────────────
echo ""
echo "── SSH connectivity ────────────────────────────"
if ssh -o ConnectTimeout=5 -o BatchMode=yes "$VPS" 'echo ok' &>/dev/null; then
  echo -e "  ${green}✅ PASS${reset}  SSH to $VPS"
else
  echo -e "  ${red}❌ FAIL${reset}  Cannot SSH to $VPS — aborting"
  exit 1
fi

# ── Sync latest scripts to VPS before running ────────────────────────────────
echo ""
echo "── Syncing scripts to VPS ──────────────────────"
ssh "$VPS" "cd $REPO_DIR_VPS && git pull --rebase origin master" 2>&1 | tail -3
echo -e "  ${green}✅${reset}  Scripts synced"

# ── Suite 1: smoke_test.sh (on VPS) ──────────────────────────────────────────
run_suite "Smoke tests (VPS)" "$SKIP_SMOKE" \
  ssh "$VPS" "cd $REPO_DIR_VPS && bash scripts/smoke_test.sh"

# ── Suite 2: validate-upstreams.sh (on VPS) ──────────────────────────────────
run_suite "Nginx upstream reachability (VPS)" "$SKIP_UPSTREAMS" \
  ssh "$VPS" "cd $REPO_DIR_VPS && bash scripts/validate-upstreams.sh"

# ── Suite 3: validate-browser.sh (local, requires Twingate) ──────────────────
run_suite "Browser validation (local/Twingate)" "$SKIP_BROWSER" \
  bash "$REPO_DIR_LOCAL/scripts/validate-browser.sh"

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "════════════════════════════════════════════════"
echo "Finished: $(date)"
TOTAL=$((SUITE_PASS + SUITE_FAIL))
if [ $SUITE_FAIL -eq 0 ]; then
  echo -e "${green}${bold}✅ ALL $TOTAL VALIDATION SUITES PASSED${reset}"
  echo ""
  echo "  Completion gate: CLEAR"
  echo "  You may mark the task complete and update Notion."
else
  echo -e "${red}${bold}❌ $SUITE_FAIL/$TOTAL VALIDATION SUITES FAILED${reset}"
  echo ""
  echo "  Failed suites:"
  for err in "${SUITE_ERRORS[@]}"; do
    echo -e "    ${red}•${reset} $err"
  done
  echo ""
  echo "  Completion gate: BLOCKED"
  echo "  Fix all failures before marking the task complete."
fi
echo "════════════════════════════════════════════════"
echo ""

exit $SUITE_FAIL
