#!/usr/bin/env bash
# validate-browser.sh — headless browser validation of key private URLs via Twingate
# Requires: python3 + playwright installed locally, Twingate connected
# Usage: bash scripts/validate-browser.sh [--screenshots-dir DIR]
set -euo pipefail

SCREENSHOTS_DIR="${1:-/tmp/browser-validate-$(date +%Y%m%d-%H%M%S)}"
mkdir -p "$SCREENSHOTS_DIR"

PASS=0
FAIL=0
ERRORS=()

green='\033[0;32m'
red='\033[0;31m'
yellow='\033[0;33m'
reset='\033[0m'

echo ""
echo "╔══════════════════════════════════════════════╗"
echo "║     Browser Validation (Playwright)         ║"
echo "╚══════════════════════════════════════════════╝"
echo ""

# Check playwright is available
if ! python3 -c "import playwright" 2>/dev/null; then
  echo -e "  ${yellow}⚠ SKIP${reset}  Playwright not installed (pip install playwright + playwright install chromium)"
  echo "  Browser validation skipped — install Playwright to enable."
  exit 0
fi

# Validate a URL: loads it, checks title/body, takes screenshot on failure
validate_url() {
  local label="$1"
  local url="$2"
  local expect_text="$3"      # text that must appear in page content
  local expect_not="$4:-"    # text that must NOT appear (error indicators)
  local screenshot_name
  screenshot_name=$(echo "$label" | tr ' /.' '-' | tr '[:upper:]' '[:lower:]')

  result=$(python3 - <<PYEOF 2>/tmp/bv_err_${screenshot_name}
import sys
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

url = "$url"
expect = "$expect_text"
expect_not = "$expect_not"
screenshot = "$SCREENSHOTS_DIR/${screenshot_name}.png"

try:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(ignore_https_errors=True)
        page = ctx.new_page()
        page.goto(url, timeout=20000, wait_until="domcontentloaded")
        content = page.content()
        title = page.title()

        passed = True
        reason = ""

        if expect and expect not in content:
            passed = False
            reason = f"expected text '{expect}' not found in page"

        if expect_not and expect_not != "-" and expect_not in content:
            passed = False
            reason = f"error text '{expect_not}' found in page"

        if not passed:
            page.screenshot(path=screenshot)

        print(f"{'PASS' if passed else 'FAIL'}|{title}|{reason}")
        browser.close()
except PWTimeout:
    print(f"FAIL||timeout loading {url}")
except Exception as e:
    print(f"FAIL||{str(e)[:120]}")
PYEOF
  )

  status=$(echo "$result" | cut -d'|' -f1)
  title=$(echo "$result" | cut -d'|' -f2)
  reason=$(echo "$result" | cut -d'|' -f3)
  err_output=$(cat "/tmp/bv_err_${screenshot_name}" 2>/dev/null | tail -3 || true)

  if [ "$status" = "PASS" ]; then
    echo -e "  ${green}✅ PASS${reset}  $label — \"$title\""
    PASS=$((PASS + 1))
  else
    echo -e "  ${red}❌ FAIL${reset}  $label — ${reason:-$err_output}"
    [ -f "$SCREENSHOTS_DIR/${screenshot_name}.png" ] && \
      echo -e "         Screenshot: $SCREENSHOTS_DIR/${screenshot_name}.png"
    ERRORS+=("$label: $reason")
    FAIL=$((FAIL + 1))
  fi
}

# Check Twingate is connected first
if ! resolvectl query sentry.private.sovereignadvisory.ai &>/dev/null && \
   ! dig +short sentry.private.sovereignadvisory.ai @100.95.0.251 2>/dev/null | grep -q '^100\.'; then
  echo -e "  ${yellow}⚠ WARN${reset}  Twingate may not be connected — DNS resolution may fail"
  echo "  Run: sudo systemctl start twingate (or connect via Twingate client)"
fi

echo "── Private portal URLs (via Twingate) ──────────"

# Portal — should show the portal UI (not a login error, not blank)
validate_url "Portal home" \
  "https://home.private.sovereignadvisory.ai" \
  "" \
  "502 Bad Gateway"

# GlitchTip — should show the login page or dashboard
validate_url "GlitchTip (Sentry)" \
  "https://sentry.private.sovereignadvisory.ai" \
  "" \
  "502 Bad Gateway"

# Keycloak — should show the Keycloak admin/login
validate_url "Keycloak SSO" \
  "https://kc.private.sovereignadvisory.ai/realms/sovereign/account" \
  "" \
  "502 Bad Gateway"

# Vaultwarden — web vault should load
validate_url "Vaultwarden web vault" \
  "https://vault.private.sovereignadvisory.ai" \
  "" \
  "502 Bad Gateway"

# n8n — should redirect to SSO (not 502)
validate_url "n8n (SSO redirect)" \
  "https://n8n.private.sovereignadvisory.ai" \
  "" \
  "502 Bad Gateway"

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "════════════════════════════════════════════════"
TOTAL=$((PASS + FAIL))
if [ $FAIL -eq 0 ]; then
  echo -e "${green}✅ ALL $TOTAL BROWSER CHECKS PASSED${reset}"
  echo "  Screenshots: $SCREENSHOTS_DIR (none — all passed)"
else
  echo -e "${red}❌ $FAIL/$TOTAL BROWSER CHECKS FAILED${reset}"
  echo "  Failure screenshots: $SCREENSHOTS_DIR"
  for err in "${ERRORS[@]}"; do
    echo -e "  ${red}•${reset} $err"
  done
fi
echo "════════════════════════════════════════════════"
echo ""

exit $FAIL
