#!/usr/bin/env bash
# sync-keycloak.sh — force-sync 'Keycloak SSO' vault item to Keycloak.
# Run on VPS: bash scripts/sync-keycloak.sh
# Reads vault item via bw CLI in vault_sync container, pushes to Keycloak.
set -euo pipefail

BW_MASTER=$(docker inspect vault_sync --format '{{range .Config.Env}}{{println .}}{{end}}' | grep '^BW_MASTER_PASS=' | cut -d= -f2-)
BW_SERVER=$(docker inspect vault_sync --format '{{range .Config.Env}}{{println .}}{{end}}' | grep '^BW_SERVER=' | cut -d= -f2-)
BW_CLIENTID=$(docker inspect vault_sync --format '{{range .Config.Env}}{{println .}}{{end}}' | grep '^BW_CLIENTID=' | cut -d= -f2-)
BW_CLIENTSECRET=$(docker inspect vault_sync --format '{{range .Config.Env}}{{println .}}{{end}}' | grep '^BW_CLIENTSECRET=' | cut -d= -f2-)

docker cp "$(dirname "$0")/sync-keycloak.py" vault_sync:/tmp/sync_kc.py
docker exec \
  -e "BW_MASTER_PASS=$BW_MASTER" \
  -e "BW_SERVER=$BW_SERVER" \
  -e "BW_CLIENTID=$BW_CLIENTID" \
  -e "BW_CLIENTSECRET=$BW_CLIENTSECRET" \
  vault_sync python3 /tmp/sync_kc.py
