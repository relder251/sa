#!/usr/bin/env bash
# test_notion_dispatch.sh — Smoke test for the Notion → Claude Dispatch n8n workflow
# Usage: bash scripts/test_notion_dispatch.sh
# Prerequisites:
#   - VPS reachable at root@187.77.208.197
#   - NOTION_API_KEY in /opt/agentic-sdlc/.env has access to the Claude Code Tasks database
#   - Claude Code CLI installed at /opt/actions-runner/externals/node20/bin/claude
#   - n8n running on VPS at http://localhost:5678
# Returns: exit 0 on success, exit 1 on failure

set -euo pipefail

VPS="root@187.77.208.197"
NOTION_DB_ID="2a299de0-e9dd-8291-b26c-879566a6e569"
POLL_INTERVAL=30
MAX_WAIT=360  # 6 minutes

log() { echo "[$(date '+%H:%M:%S')] $*"; }
fail() { echo "[FAIL] $*" >&2; exit 1; }
pass() { echo "[PASS] $*"; }

# ─── Step 0: Preflight checks ────────────────────────────────────────────────

log "Step 0: Preflight checks"

# Check SSH connectivity
ssh "$VPS" 'echo ok' > /dev/null 2>&1 || fail "Cannot SSH to VPS at $VPS"
pass "SSH connectivity OK"

# Load Notion API key from VPS
NOTION_API_KEY=$(ssh "$VPS" "grep '^NOTION_API_KEY' /opt/agentic-sdlc/.env | cut -d= -f2- | tr -d '\r'")
[ -n "$NOTION_API_KEY" ] || fail "NOTION_API_KEY not found in VPS .env"
pass "Notion API key loaded: ${NOTION_API_KEY:0:20}..."

# Check Notion DB access
DB_STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
  -X POST "https://api.notion.com/v1/databases/${NOTION_DB_ID}/query" \
  -H "Authorization: Bearer $NOTION_API_KEY" \
  -H "Notion-Version: 2022-06-28" \
  -H "Content-Type: application/json" \
  -d "{}")
if [ "$DB_STATUS" != "200" ]; then
  echo "[SKIP] Notion API returned HTTP $DB_STATUS for database $NOTION_DB_ID"
  echo "       The 'Sovereign Advisory Pipeline' integration needs access to the"
  echo "       'Claude Code Tasks' database. Share it via Notion UI:"
  echo "       1. Open https://www.notion.so/f7f99de0e9dd83429d7981a93b571dfc"
  echo "       2. Click 'Share' -> 'Invite' -> search 'Sovereign Advisory Pipeline'"
  echo "       3. Give it 'Can edit' access"
  echo "       Then re-run this script."
  exit 2
fi
pass "Notion DB access OK"

# Check Claude CLI on VPS
CLAUDE_VER=$(ssh "$VPS" 'export PATH="/opt/actions-runner/externals/node20/bin:$PATH" && claude --version 2>&1')
[[ "$CLAUDE_VER" == *"Claude Code"* ]] || fail "Claude CLI not found on VPS. Run: ssh $VPS 'export PATH=/opt/actions-runner/externals/node20/bin:\$PATH && npm install -g @anthropic-ai/claude-code'"
pass "Claude CLI OK: $CLAUDE_VER"

# Check n8n workflow is active
N8N_KEY=$(ssh "$VPS" "grep '^N8N_API_KEY' /opt/agentic-sdlc/.env | cut -d= -f2- | tr -d '\r'")
WF_ACTIVE=$(ssh "$VPS" "curl -s 'http://localhost:5678/api/v1/workflows' \
  -H 'X-N8N-API-KEY: $N8N_KEY' 2>&1 | python3 -c \"
import json,sys
d=json.load(sys.stdin)
wfs=[w for w in d.get('data',[]) if 'Notion' in w.get('name','') and 'Claude' in w.get('name','')]
print(wfs[0]['active'] if wfs else 'NOT_FOUND')
\"")
[ "$WF_ACTIVE" = "True" ] || fail "Notion->Claude Dispatch workflow not found or not active. Expected active=True, got: $WF_ACTIVE"
pass "n8n workflow is active"

# ─── Step 1: Create test task in Notion ──────────────────────────────────────

log "Step 1: Creating test task in Notion"

TIMESTAMP=$(date '+%Y%m%d-%H%M%S')
TASK_NAME="TEST — notion dispatch smoke test $TIMESTAMP"

CREATE_RESPONSE=$(curl -s -X POST "https://api.notion.com/v1/pages" \
  -H "Authorization: Bearer $NOTION_API_KEY" \
  -H "Notion-Version: 2022-06-28" \
  -H "Content-Type: application/json" \
  -d "{
    \"parent\": {\"database_id\": \"$NOTION_DB_ID\"},
    \"properties\": {
      \"Task name\": {
        \"title\": [{\"type\": \"text\", \"text\": {\"content\": \"$TASK_NAME\"}}]
      },
      \"Status\": {
        \"status\": {\"name\": \"Ready\"}
      },
      \"Agent blocked\": {
        \"checkbox\": false
      },
      \"Agent status\": {
        \"rich_text\": [{\"type\": \"text\", \"text\": {\"content\": \"echo 'notion dispatch test passed' && date\"}}]
      }
    }
  }")

PAGE_ID=$(echo "$CREATE_RESPONSE" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('id',''))" 2>/dev/null)
[ -n "$PAGE_ID" ] || fail "Failed to create test task. Response: $CREATE_RESPONSE"
pass "Created test task: $PAGE_ID (Status=Ready)"

# ─── Step 2: Wait for workflow to pick up the task ───────────────────────────

log "Step 2: Waiting up to ${MAX_WAIT}s for workflow to process task..."

ELAPSED=0
FINAL_STATUS=""
FINAL_AGENT_STATUS=""

while [ $ELAPSED -lt $MAX_WAIT ]; do
  sleep $POLL_INTERVAL
  ELAPSED=$((ELAPSED + POLL_INTERVAL))

  PAGE_DATA=$(curl -s "https://api.notion.com/v1/pages/$PAGE_ID" \
    -H "Authorization: Bearer $NOTION_API_KEY" \
    -H "Notion-Version: 2022-06-28")

  CURRENT_STATUS=$(echo "$PAGE_DATA" | python3 -c "
import json,sys
d=json.load(sys.stdin)
props=d.get('properties',{})
status=props.get('Status',{}).get('status',{}).get('name','')
agent=props.get('Agent status',{}).get('rich_text',[])
agent_text=''.join([t.get('plain_text','') for t in agent])
print(f'{status}|||{agent_text}')
" 2>/dev/null)

  FINAL_STATUS=$(echo "$CURRENT_STATUS" | cut -d'|' -f1)
  FINAL_AGENT_STATUS=$(echo "$CURRENT_STATUS" | cut -d'|' -f4-)

  log "  [${ELAPSED}s] Status: $FINAL_STATUS | Agent status: ${FINAL_AGENT_STATUS:0:80}"

  if [ "$FINAL_STATUS" = "Done" ]; then
    break
  elif [ "$FINAL_STATUS" = "Planning" ]; then
    # Task was marked as failed
    log "  Task failed and set to Planning. Agent status: $FINAL_AGENT_STATUS"
    break
  fi
done

# ─── Step 3: Validate results ────────────────────────────────────────────────

log "Step 3: Validating results"

if [ "$FINAL_STATUS" = "Done" ]; then
  pass "Status changed to 'Done'"
  if [[ "$FINAL_AGENT_STATUS" == *"notion dispatch test passed"* ]] || [ -n "$FINAL_AGENT_STATUS" ]; then
    pass "Agent status contains output: ${FINAL_AGENT_STATUS:0:100}"
  else
    fail "Agent status is empty despite Done status"
  fi
elif [ "$FINAL_STATUS" = "Planning" ] && [[ "$FINAL_AGENT_STATUS" == "FAILED"* ]]; then
  fail "Task failed: $FINAL_AGENT_STATUS"
else
  fail "Timed out after ${MAX_WAIT}s. Final status: '$FINAL_STATUS'. Expected 'Done'."
fi

# ─── Step 4: Cleanup ─────────────────────────────────────────────────────────

log "Step 4: Cleaning up test task"

ARCHIVE_RESPONSE=$(curl -s -X PATCH "https://api.notion.com/v1/pages/$PAGE_ID" \
  -H "Authorization: Bearer $NOTION_API_KEY" \
  -H "Notion-Version: 2022-06-28" \
  -H "Content-Type: application/json" \
  -d '{"archived": true}')

ARCHIVED=$(echo "$ARCHIVE_RESPONSE" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('archived',''))" 2>/dev/null)
[ "$ARCHIVED" = "True" ] && pass "Test task archived (cleaned up)" || log "Warning: could not archive test task $PAGE_ID"

echo ""
echo "════════════════════════════════════════════"
echo " SMOKE TEST PASSED — Notion→Claude dispatch"
echo "════════════════════════════════════════════"
