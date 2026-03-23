#!/usr/bin/env bash
# SANITY_CMD — validate config formats and static correctness
# Exits 0 if all checks pass, 1 on first failure.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PASS=0; FAIL=0

ok()   { echo "  ✅ PASS  $*"; PASS=$((PASS+1)); }
fail() { echo "  ❌ FAIL  $*"; FAIL=$((FAIL+1)); }

echo "══ Format / Sanity Validation ══════════════════════"

# 1. docker-compose files are valid YAML / parseable by docker compose
for f in docker-compose.prod.yml docker-compose.mirror.yml docker-compose.yml; do
  if [ -f "$REPO_ROOT/$f" ]; then
    if docker compose -f "$REPO_ROOT/$f" config --quiet 2>/dev/null; then
      ok "$f parses cleanly"
    else
      fail "$f — docker compose config error"
    fi
  fi
done

# 2. litellm_config.yaml is valid YAML
if command -v python3 &>/dev/null && [ -f "$REPO_ROOT/litellm_config.yaml" ]; then
  if python3 -c "import yaml, sys; yaml.safe_load(open('$REPO_ROOT/litellm_config.yaml'))" 2>/dev/null; then
    ok "litellm_config.yaml is valid YAML"
  else
    fail "litellm_config.yaml — YAML parse error"
  fi
fi

# 3. nginx templates — no stale {{PLACEHOLDER}} (unsubstituted envsubst vars)
NGINX_TEMPLATES=$(ls "$REPO_ROOT"/nginx/conf.d/*.template 2>/dev/null | wc -l)
if [ "$NGINX_TEMPLATES" -gt 0 ]; then
  STALE=$(grep -rh '\$\${' "$REPO_ROOT"/nginx/conf.d/*.template 2>/dev/null | wc -l)
  if [ "$STALE" -eq 0 ]; then
    ok "nginx templates — no stale double-dollar placeholders"
  else
    fail "nginx templates — $STALE stale \$\${...} placeholder(s) found"
  fi
fi

# 4. scripts are executable
NOT_EXEC=0
for f in "$REPO_ROOT"/scripts/*.sh; do
  [ -x "$f" ] || { fail "$(basename "$f") not executable"; NOT_EXEC=$((NOT_EXEC+1)); }
done
[ "$NOT_EXEC" -eq 0 ] && ok "all scripts/*.sh are executable"

# 5. .env.example has no filled-in secrets (lines with 32+ char values)
if [ -f "$REPO_ROOT/.env.example" ]; then
  SECRETS=$(grep -E '^[A-Z_]+=[A-Za-z0-9/+]{32,}' "$REPO_ROOT/.env.example" 2>/dev/null | wc -l | tr -d ' ')
  if [ "$SECRETS" -eq 0 ]; then
    ok ".env.example — no long secrets detected"
  else
    fail ".env.example — $SECRETS line(s) appear to contain long secret values"
  fi
fi

echo "════════════════════════════════════════════════════"
echo "  Passed: $PASS   Failed: $FAIL"
echo "════════════════════════════════════════════════════"

[ "$FAIL" -eq 0 ]
