#!/usr/bin/env bash
# deploy_workflow.sh — Safely import SA Contact Lead Pipeline and sync published version
# Run from the project root: bash scripts/deploy_workflow.sh <workflow.json>
#
# Required env vars (sourced from .env if present):
#   N8N_API_KEY  — n8n personal API token (Settings > API > Personal API tokens)
#   WF_ID        — n8n workflow ID to deploy into (find via: n8n list:workflow)
set -euo pipefail

# Source .env from repo root if present
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
if [[ -f "$REPO_ROOT/.env" ]]; then
  set -o allexport
  # shellcheck disable=SC1091
  source "$REPO_ROOT/.env"
  set +o allexport
fi

WF_JSON="${1:-/tmp/wf_fixed.json}"
# WF_ID: the n8n workflow ID. Update this when the workflow is recreated in n8n.
# Find it via: docker exec n8n n8n list:workflow
WF_ID="${WF_ID:?WF_ID must be set in .env or the environment}"
N8N_API_KEY="${N8N_API_KEY:?N8N_API_KEY must be set in .env or the environment}"

echo "[1/4] Importing workflow..."
docker exec -i n8n n8n import:workflow --input=/dev/stdin < "$WF_JSON"

echo "[2/4] Getting new versionId..."
# NOTE: n8n does not expose a public API for reading versionId or syncing the
# published version. This queries n8n's internal PostgreSQL schema directly.
# This is intentional but couples the script to n8n's internal schema — test
# after n8n upgrades. The workflow_entity and workflow_published_version tables
# are stable across n8n v1.x but may change in major versions.
NEW_VERSION=$(docker exec litellm_db psql -U litellm -d n8n -t -c \
  "SELECT \"versionId\" FROM workflow_entity WHERE id = '${WF_ID}';" | tr -d ' \n')
echo "  New versionId: $NEW_VERSION"

echo "[3/4] Syncing published version..."
docker exec litellm_db psql -U litellm -d n8n -c \
  "UPDATE workflow_published_version SET \"publishedVersionId\" = '${NEW_VERSION}', \"updatedAt\" = NOW() WHERE \"workflowId\" = '${WF_ID}';"

echo "[4/4] Reactivating workflow..."
curl -sf -X POST "http://localhost:5678/api/v1/workflows/${WF_ID}/activate" \
  -H "X-N8N-API-KEY: ${N8N_API_KEY}" | python3 -c "import json,sys; w=json.load(sys.stdin); print('  Active:', w.get('active'))"

echo "Done. Workflow deployed and active."
