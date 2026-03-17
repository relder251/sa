#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"

export COMPOSE_DIR="${COMPOSE_DIR:-/opt/agentic-sdlc}"
export ENV_FILE="${ENV_FILE:-$COMPOSE_DIR/.env}"
export COMPOSE_SERVICE="${COMPOSE_SERVICE:-twingate}"
export TWINGATE_NETWORK="${TWINGATE_NETWORK:-relder}"
export TWINGATE_REMOTE_NETWORK="${TWINGATE_REMOTE_NETWORK:-Homelab Network}"
export TWINGATE_CONNECTOR_NAME="${TWINGATE_CONNECTOR_NAME:-friendly-jaguar}"
export TWINGATE_LABEL_HOSTNAME="${TWINGATE_LABEL_HOSTNAME:-$(hostname -f 2>/dev/null || hostname)}"

if [[ -z "${TWINGATE_API_KEY:-}" ]]; then
  echo "TWINGATE_API_KEY is required. Generate it in Twingate: Settings > API > Generate Token." >&2
  exit 1
fi

exec "$PYTHON_BIN" "$SCRIPT_DIR/twingate_connector_rotate.py" "$@"
