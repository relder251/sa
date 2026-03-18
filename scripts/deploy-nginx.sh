#!/usr/bin/env bash
# deploy-nginx.sh — GitOps deploy for public nginx config
# Usage: bash scripts/deploy-nginx.sh [--dry-run]
#
# Enforces linear flow: local → GitHub → production
# nginx-public/ files are normally chattr +i (immutable).
# This script is the ONLY sanctioned way to update them on prod.
# Direct editing requires explicit: chattr -R -i nginx-public/
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NGINX_DIR="$REPO_DIR/nginx-public"

DRY_RUN=false
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=true

run() {
  if $DRY_RUN; then
    echo "[dry-run] $(printf '%q ' "$@")"
  else
    "$@"
  fi
}

echo "=== nginx GitOps deploy (dry-run: $DRY_RUN) ==="

# 1. Lift immutable flag so git can update files
echo "--- lifting immutable flag on nginx-public/ ---"
run chattr -R -i "$NGINX_DIR"

# 2. Pull latest from GitHub
echo "--- git pull ---"
run git -C "$REPO_DIR" pull

# 3. Re-lock: immutable flag on all files (not dirs — dirs need +i too but
#    git needs to traverse them, so lock files only)
echo "--- re-locking nginx-public/ files ---"
if ! $DRY_RUN; then
  find "$NGINX_DIR" -type f -exec chattr +i {} \;
else
  echo "[dry-run] find $NGINX_DIR -type f -exec chattr +i {} \\;"
fi

# 4. Reload nginx with new config
echo "--- reloading nginx ---"
run docker compose -f "$REPO_DIR/docker-compose.prod.yml" up -d nginx

echo "=== deploy complete ==="
