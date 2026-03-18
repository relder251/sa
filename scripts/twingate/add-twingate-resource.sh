#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"

# Source .env from the repo root if present (provides TWINGATE_API_KEY / TWINGATE_NETWORK)
REPO_ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd)"
if [[ -f "$REPO_ROOT/.env" ]]; then
  # shellcheck disable=SC1091
  set -o allexport
  source "$REPO_ROOT/.env"
  set +o allexport
fi

export TWINGATE_NETWORK="${TWINGATE_NETWORK:-}"
export TWINGATE_REMOTE_NETWORK="${TWINGATE_REMOTE_NETWORK:-}"

if [[ -z "${TWINGATE_API_KEY:-}" ]]; then
  echo "TWINGATE_API_KEY is required. Generate it in Twingate: Settings > API > Generate Token." >&2
  exit 1
fi

exec "$PYTHON_BIN" "$SCRIPT_DIR/twingate_add_resource.py" "$@"
