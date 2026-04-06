#!/usr/bin/env bash
set -euo pipefail

cd /opt/agentic-sdlc/tools

export TWINGATE_NETWORK="relder"
export TWINGATE_API_KEY="$(grep '^TWINGATE_API_KEY=' /opt/agentic-sdlc/.env | cut -d= -f2-)"
export TG_REMOTE_NETWORK="Homelab Network"
export TG_RESOURCE_NAME="Private Apps"
export TG_RESOURCE_ADDRESS="127.0.0.1"
export TG_RESOURCE_ALIAS="private.sovereignadvisory.ai"
export TG_RESOURCE_PORT="8443"

python3 ensure_twingate_private_resource.py
