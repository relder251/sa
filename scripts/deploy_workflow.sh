#!/usr/bin/env bash
# deploy_workflow.sh — Safely import SA Contact Lead Pipeline and sync published version
# Run from the project root: bash scripts/deploy_workflow.sh <workflow.json>
set -euo pipefail

WF_JSON="${1:-/tmp/wf_fixed.json}"
WF_ID="Wyc4UIvCYgByrAwP"
N8N_API_KEY="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIyMjA5OGVkZC02NzIyLTRhODEtYWNmNS04OTk2YzRkNzIzNjUiLCJpc3MiOiJuOG4iLCJhdWQiOiJwdWJsaWMtYXBpIiwianRpIjoiODgzZTJlZDktODEyNS00MTQ0LTlhYTQtOGQzNWM4MmVhNTYyIiwiaWF0IjoxNzczMzUyNTM5LCJleHAiOjE4MDQ4ODg1Mzk1MDJ9.EFVDxAafcxYHD_uTGvbuCl7zdWLd11TRziYUzzkgW3g"

echo "[1/4] Importing workflow..."
docker exec -i n8n n8n import:workflow --input=/dev/stdin < "$WF_JSON"

echo "[2/4] Getting new versionId..."
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
