#!/usr/bin/env bash
# validate-browser.sh — headless browser validation of key private URLs via Twingate
# Requires: Node.js + playwright (from gsd-pi or local install), Twingate connected
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

# Resolve playwright module — try common locations
PW_MODULE=""
for candidate in \
  "/home/user/.npm-global/lib/node_modules/gsd-pi/node_modules/playwright" \
  "/usr/local/lib/node_modules/playwright" \
  "$(npm root -g)/playwright" \
  "$(npm root -g)/gsd-pi/node_modules/playwright"; do
  [ -f "$candidate/package.json" ] && PW_MODULE="$candidate" && break
done

echo ""
echo "╔══════════════════════════════════════════════╗"
echo "║     Browser Validation (Playwright)         ║"
echo "╚══════════════════════════════════════════════╝"
echo ""

if [ -z "$PW_MODULE" ]; then
  echo -e "  ${yellow}⚠ SKIP${reset}  Playwright not found. To enable: npm install -g playwright && npx playwright install chromium"
  echo "  Browser validation skipped."
  exit 0
fi

# Check Twingate DNS is working
if ! resolvectl query sentry.private.sovereignadvisory.ai &>/dev/null 2>&1 && \
   ! dig +short sentry.private.sovereignadvisory.ai 2>/dev/null | grep -q '^100\.'; then
  echo -e "  ${yellow}⚠ WARN${reset}  Twingate DNS may not be resolving — private URLs may fail"
fi

echo "── Private portal URLs (via Twingate) ──────────"
echo "   Playwright module: $PW_MODULE"
echo ""

# Run a single browser session for all checks to avoid repeated launch overhead
RESULTS=$(node - "$PW_MODULE" "$SCREENSHOTS_DIR" 2>/tmp/bv_node_err <<'NODESCRIPT'
const [,, PW_MODULE, SCREENSHOTS_DIR] = process.argv;
const { chromium } = require(PW_MODULE);
const fs = require('fs');
const path = require('path');

const checks = [
  { label: 'Portal home',        url: 'https://home.private.sovereignadvisory.ai',    expect_not: '502 Bad Gateway' },
  { label: 'GlitchTip (Sentry)', url: 'https://sentry.private.sovereignadvisory.ai',  expect_not: '502 Bad Gateway' },
  { label: 'Keycloak SSO',       url: 'https://kc.private.sovereignadvisory.ai/realms/sovereign/account', expect_not: '502 Bad Gateway' },
  { label: 'Vaultwarden vault',  url: 'https://vault.private.sovereignadvisory.ai',   expect_not: '502 Bad Gateway' },
  { label: 'n8n (SSO redirect)', url: 'https://n8n.private.sovereignadvisory.ai',     expect_not: '502 Bad Gateway' },
];

(async () => {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ ignoreHTTPSErrors: true });

  for (const check of checks) {
    const slugName = check.label.replace(/[\s\/\.]+/g, '-').toLowerCase();
    const screenshotPath = path.join(SCREENSHOTS_DIR, `${slugName}.png`);
    try {
      const page = await context.newPage();
      const resp = await page.goto(check.url, { timeout: 20000, waitUntil: 'domcontentloaded' });
      const title = await page.title();
      const content = await page.content();
      const status = resp ? resp.status() : 0;

      let passed = true;
      let reason = '';

      if (check.expect_not && content.includes(check.expect_not)) {
        passed = false;
        reason = `page contains '${check.expect_not}'`;
      }
      if (status === 502 || status === 503) {
        passed = false;
        reason = `HTTP ${status}`;
      }
      if (!passed) {
        await page.screenshot({ path: screenshotPath, fullPage: false });
      }
      console.log(`${passed ? 'PASS' : 'FAIL'}|${check.label}|${title}|${status}|${reason}`);
      await page.close();
    } catch (e) {
      console.log(`FAIL|${check.label}||0|${e.message.slice(0, 100)}`);
    }
  }
  await browser.close();
})().catch(e => {
  console.error('FATAL:', e.message);
  process.exit(1);
});
NODESCRIPT
) || { echo -e "  ${red}❌ FAIL${reset}  Node.js playwright error: $(cat /tmp/bv_node_err | tail -3)"; ERRORS+=("playwright: fatal error"); FAIL=$((FAIL+1)); }

# Parse results
while IFS='|' read -r status label title http_status reason; do
  [ -z "$status" ] && continue
  if [ "$status" = "PASS" ]; then
    echo -e "  ${green}✅ PASS${reset}  $label — \"$title\" [HTTP $http_status]"
    PASS=$((PASS + 1))
  else
    echo -e "  ${red}❌ FAIL${reset}  $label — ${reason} [HTTP $http_status]"
    slug=$(echo "$label" | tr ' /.' '-' | tr '[:upper:]' '[:lower:]')
    [ -f "$SCREENSHOTS_DIR/${slug}.png" ] && echo -e "         Screenshot: $SCREENSHOTS_DIR/${slug}.png"
    ERRORS+=("$label: $reason")
    FAIL=$((FAIL + 1))
  fi
done <<< "$RESULTS"

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "════════════════════════════════════════════════"
TOTAL=$((PASS + FAIL))
if [ $FAIL -eq 0 ]; then
  echo -e "${green}✅ ALL $TOTAL BROWSER CHECKS PASSED${reset}"
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
