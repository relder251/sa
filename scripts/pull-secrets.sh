#!/usr/bin/env bash
# pull-secrets.sh — fetch production secrets from Vaultwarden into .env.prod
# Bootstrap: source /root/.env.vault before running this script.
#
# /root/.env.vault must contain:
#   BW_CLIENTID=user.xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
#   BW_CLIENTSECRET=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
#   BW_PASSWORD=your-master-password
#
# Usage:
#   source /root/.env.vault && bash /opt/agentic-sdlc/scripts/pull-secrets.sh

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$REPO_DIR/.env.prod"
NOTE_NAME="${BW_NOTE_NAME:-SDLC Production Secrets}"

echo "[pull-secrets] Configuring Vaultwarden server..."
bw config server https://vault.private.sovereignadvisory.ai >/dev/null

echo "[pull-secrets] Logging in with API key..."
export BW_SESSION
BW_SESSION=$(bw login --apikey --raw 2>/dev/null || true)

if [[ -z "$BW_SESSION" ]]; then
    echo "[pull-secrets] Login produced no session token, attempting unlock..."
    BW_SESSION=$(bw unlock --passwordenv BW_PASSWORD --raw)
fi

echo "[pull-secrets] Unlocking vault..."
BW_SESSION=$(bw unlock --passwordenv BW_PASSWORD --raw)
export BW_SESSION

echo "[pull-secrets] Syncing vault..."
bw sync >/dev/null

echo "[pull-secrets] Fetching note: '$NOTE_NAME'..."
NOTE_CONTENT=$(bw get notes "$NOTE_NAME" 2>/dev/null)

if [[ -z "$NOTE_CONTENT" ]]; then
    echo "[pull-secrets] ERROR: Note '$NOTE_NAME' not found in Vaultwarden."
    echo "  1. Log in to https://vault.private.sovereignadvisory.ai"
    echo "  2. Create a Secure Note named exactly: $NOTE_NAME"
    echo "  3. Paste the full contents of .env.prod as the note body"
    exit 1
fi

# Backup current .env.prod
if [[ -f "$ENV_FILE" && ! -L "$ENV_FILE" ]]; then
    cp "$ENV_FILE" "${ENV_FILE}.bak.$(date +%Y%m%d_%H%M%S)"
fi

# Write new .env.prod (overwrite the symlink-target if needed)
REAL_TARGET=$(readlink -f "$ENV_FILE" 2>/dev/null || echo "$ENV_FILE")
echo "$NOTE_CONTENT" > "$REAL_TARGET"
chmod 600 "$REAL_TARGET"

echo "[pull-secrets] Done. Secrets written to $REAL_TARGET"
echo "[pull-secrets] Lock vault..."
bw lock >/dev/null
