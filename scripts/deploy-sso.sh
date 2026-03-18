#!/usr/bin/env bash
# deploy-sso.sh — one-shot SSO + Vaultwarden activation on prod.
#
# Usage: bash scripts/deploy-sso.sh [--dry-run]
#
# What it does:
#   1. Pulls latest code from GitHub
#   2. Generates any missing secrets (OAUTH2_PROXY_COOKIE_SECRET_WEBUI,
#      VAULTWARDEN_ADMIN_TOKEN) and appends them to .env
#   3. Verifies all required OIDC client secrets are present in .env
#      (these must have been added after running keycloak_bootstrap.py)
#   4. Restarts n8n (picks up SSO env vars)
#   5. Starts all oauth2-proxy sidecars and Vaultwarden
#   6. Reloads nginx-private to pick up new proxy routes
#   7. Exports Keycloak realm to keycloak/realm-export.json
#
# After this script: commit+push keycloak/realm-export.json from your dev machine.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.."; pwd)"
ENV_FILE="$REPO_DIR/.env"

DRY_RUN=false
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=true

run() {
  if $DRY_RUN; then
    echo "[dry-run] $*"
  else
    "$@"
  fi
}

# ── helpers ────────────────────────────────────────────────────────────────────

env_get() { grep -E "^${1}=" "$ENV_FILE" 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"' || true; }

env_set() {
  local key="$1" val="$2"
  if grep -qE "^${key}=" "$ENV_FILE" 2>/dev/null; then
    # key exists but is empty — fill it in
    if $DRY_RUN; then
      echo "[dry-run] sed -i update ${key} in .env"
    else
      sed -i "s|^${key}=.*|${key}=${val}|" "$ENV_FILE"
    fi
  else
    if $DRY_RUN; then
      echo "[dry-run] echo ${key}=... >> .env"
    else
      echo "${key}=${val}" >> "$ENV_FILE"
    fi
  fi
}

check_required() {
  local key="$1"
  local val
  val="$(env_get "$key")"
  if [[ -z "$val" ]]; then
    echo "ERROR: $key is not set in $ENV_FILE"
    echo "  Run scripts/keycloak_bootstrap.py and copy the printed secrets to .env"
    return 1
  fi
}

echo "=== SSO + Vaultwarden deploy (dry-run: $DRY_RUN) ==="

# ── 1. Pull latest ─────────────────────────────────────────────────────────────
echo "--- git pull ---"
run git -C "$REPO_DIR" pull

# ── 2. Generate missing secrets ────────────────────────────────────────────────
echo "--- checking/generating secrets ---"

webui_secret="$(env_get OAUTH2_PROXY_COOKIE_SECRET_WEBUI)"
if [[ -z "$webui_secret" ]]; then
  echo "  generating OAUTH2_PROXY_COOKIE_SECRET_WEBUI"
  env_set OAUTH2_PROXY_COOKIE_SECRET_WEBUI "$(openssl rand -base64 32)"
else
  echo "  OAUTH2_PROXY_COOKIE_SECRET_WEBUI already set"
fi

vault_token="$(env_get VAULTWARDEN_ADMIN_TOKEN)"
if [[ -z "$vault_token" ]]; then
  echo "  generating VAULTWARDEN_ADMIN_TOKEN"
  env_set VAULTWARDEN_ADMIN_TOKEN "$(openssl rand -base64 48)"
else
  echo "  VAULTWARDEN_ADMIN_TOKEN already set"
fi

# ── 3. Verify OIDC secrets ─────────────────────────────────────────────────────
echo "--- verifying OIDC client secrets ---"
required_secrets=(
  N8N_OIDC_CLIENT_SECRET
  WEBUI_OIDC_CLIENT_SECRET
  LITELLM_OIDC_CLIENT_SECRET
  JUPYTER_OIDC_CLIENT_SECRET
  LEAD_REVIEW_OIDC_CLIENT_SECRET
  VAULTWARDEN_OIDC_CLIENT_SECRET
  OAUTH2_PROXY_COOKIE_SECRET
)
missing=0
for key in "${required_secrets[@]}"; do
  if ! check_required "$key"; then
    missing=$((missing + 1))
  else
    echo "  $key ✓"
  fi
done
if [[ $missing -gt 0 ]]; then
  if $DRY_RUN; then
    echo "WARNING: $missing required secret(s) missing (would abort in live run — add them to .env first)"
  else
    echo "FATAL: $missing required secret(s) missing. Fix .env then re-run."
    exit 1
  fi
fi

# ── 4. Restart n8n (SSO env vars) ─────────────────────────────────────────────
echo "--- restarting n8n ---"
run docker compose -f "$REPO_DIR/docker-compose.prod.yml" up -d n8n

# ── 5. Start oauth2-proxies + Vaultwarden ─────────────────────────────────────
echo "--- starting oauth2-proxies and vaultwarden ---"
run docker compose -f "$REPO_DIR/docker-compose.prod.yml" up -d \
  oauth2-proxy-webui \
  oauth2-proxy-litellm \
  oauth2-proxy-jupyter \
  vaultwarden \
  lead-review

# ── 6. Reload nginx-private ───────────────────────────────────────────────────
echo "--- reloading nginx-private ---"
run docker compose -f "$REPO_DIR/docker-compose.prod.yml" up -d nginx-private

# ── 7. Export Keycloak realm ──────────────────────────────────────────────────
echo "--- exporting Keycloak realm ---"
run bash "$REPO_DIR/scripts/keycloak_export_realm.sh"

echo ""
echo "=== deploy complete ==="
echo ""
echo "Next steps (run locally):"
echo "  scp root@sovereignadvisory.ai:${REPO_DIR}/keycloak/realm-export.json keycloak/realm-export.json"
echo "  git add keycloak/realm-export.json"
echo "  git commit -m 'chore: update Keycloak realm snapshot with all SSO clients'"
echo "  git push origin master"
