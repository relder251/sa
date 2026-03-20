#!/usr/bin/env bash
# pipeline_smoke_test.sh — End-to-end smoke test for the Agentic SDLC pipeline
#
# Posts a minimal /run-opportunity and polls run_state.json until all 10 phases
# complete or the timeout expires. Prints per-phase pass/fail as they land.
#
# Usage (from repo root, stack must be up):
#   bash scripts/pipeline_smoke_test.sh
#
# Usage (inside pipeline container):
#   docker exec pipeline_server bash /data/scripts/pipeline_smoke_test.sh
#
# Environment overrides:
#   PIPELINE_URL   — base URL of pipeline server  (default: http://localhost:5002)
#   SMOKE_TIMEOUT  — max seconds to wait          (default: 600)
#   SMOKE_NAME     — project name for the run     (default: pipeline-smoke)
#   SMOKE_PROMPT   — prompt sent to pipeline      (default: trivial hello-world)

set -euo pipefail

PIPELINE_URL="${PIPELINE_URL:-http://localhost:5002}"
SMOKE_TIMEOUT="${SMOKE_TIMEOUT:-600}"
SMOKE_NAME="${SMOKE_NAME:-pipeline-smoke}"
SMOKE_PROMPT="${SMOKE_PROMPT:-Write a single Python file hello.py that prints Hello World and exits 0. No dependencies beyond the standard library.}"
POLL_INTERVAL=5

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
RESET='\033[0m'

pass()  { echo -e "${GREEN}[PASS]${RESET} $*"; }
fail()  { echo -e "${RED}[FAIL]${RESET} $*"; }
info()  { echo -e "${BLUE}[INFO]${RESET} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${RESET} $*"; }

PHASE_NAMES=(
  ""                    # placeholder (phases are 1-indexed)
  "Plan generation"
  "Code generation"
  "Format & validate"
  "Test & fix loop"
  "Quality gate"
  "Documentation"
  "Git push"
  "Deployment"
  "Monitoring"
  "Approval gate"
)

echo ""
echo "╔══════════════════════════════════════════════════╗"
echo "║   Agentic SDLC — Pipeline Smoke Test             ║"
echo "╚══════════════════════════════════════════════════╝"
echo ""

# ── 1. Health check ───────────────────────────────────────────────────────────

info "Health check: $PIPELINE_URL/health"
health=$(curl -sf --max-time 8 "$PIPELINE_URL/health" 2>&1) || {
  fail "Pipeline server unreachable at $PIPELINE_URL"
  exit 1
}
health_status=$(echo "$health" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status',''))" 2>/dev/null || echo "")
if [[ "$health_status" == "ok" ]]; then
  pass "/health → $health"
else
  fail "/health returned unexpected response: $health"
  exit 1
fi

# ── 2. POST /run-opportunity ──────────────────────────────────────────────────

echo ""
info "Posting pipeline run (name=$SMOKE_NAME) ..."
payload=$(python3 -c "
import json, sys
print(json.dumps({'name': '$SMOKE_NAME', 'prompt': '''$SMOKE_PROMPT'''}))
")

response=$(curl -sf --max-time 20 \
  -X POST "$PIPELINE_URL/run-opportunity" \
  -H "Content-Type: application/json" \
  -d "$payload" 2>&1) || {
  fail "/run-opportunity POST failed: $response"
  exit 1
}

run_id=$(echo "$response" | python3 -c "import sys,json; print(json.load(sys.stdin).get('run_id',''))" 2>/dev/null || echo "")
project_base=$(echo "$response" | python3 -c "import sys,json; print(json.load(sys.stdin).get('project_base',''))" 2>/dev/null || echo "")

if [[ -z "$run_id" || -z "$project_base" ]]; then
  fail "Response missing run_id or project_base: $response"
  exit 1
fi

pass "Pipeline started — run_id=$run_id"
info "  state file: $project_base/run_state.json"
echo ""

# ── 3. Poll run_state.json ────────────────────────────────────────────────────

state_file="$project_base/run_state.json"
declare -A phase_reported=()
elapsed=0
final_status=""

info "Polling every ${POLL_INTERVAL}s (max ${SMOKE_TIMEOUT}s) ..."
echo ""

while [[ $elapsed -lt $SMOKE_TIMEOUT ]]; do
  if [[ ! -f "$state_file" ]]; then
    sleep "$POLL_INTERVAL"
    elapsed=$((elapsed + POLL_INTERVAL))
    continue
  fi

  # Parse state; python3 handles partial writes more gracefully than jq
  state=$(python3 - <<PYEOF 2>/dev/null || echo "{}"
import json
try:
    d = json.load(open("$state_file"))
    print(json.dumps(d))
except Exception:
    print("{}")
PYEOF
)

  pipeline_status=$(echo "$state" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status',''))" 2>/dev/null || echo "")

  # Report each phase once, as soon as it leaves pending/running
  for i in $(seq 1 10); do
    [[ "${phase_reported[$i]:-}" == "1" ]] && continue

    phase_data=$(echo "$state" | python3 - <<PYEOF 2>/dev/null || echo "{}"
import sys, json
d = json.load(sys.stdin)
print(json.dumps(d.get("phases", {}).get("$i", {})))
PYEOF
)
    phase_status=$(echo "$phase_data" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status','pending'))" 2>/dev/null || echo "pending")

    label="Phase $i: ${PHASE_NAMES[$i]}"
    case "$phase_status" in
      done)
        pass "$label"
        phase_reported[$i]=1
        ;;
      failed)
        result=$(echo "$phase_data" | python3 -c "import sys,json; print(json.load(sys.stdin).get('result','') or '')" 2>/dev/null || echo "")
        fail "$label — $result"
        phase_reported[$i]=1
        ;;
      skipped)
        warn "$label (skipped)"
        phase_reported[$i]=1
        ;;
    esac
  done

  # Terminal pipeline states
  case "$pipeline_status" in
    done|failed|rejected|blocked) final_status="$pipeline_status"; break ;;
  esac

  sleep "$POLL_INTERVAL"
  elapsed=$((elapsed + POLL_INTERVAL))
done

# ── 4. Summary ────────────────────────────────────────────────────────────────

echo ""
echo "════════════════════════════════════════════════════"
if [[ "$final_status" == "done" ]]; then
  pass "All phases complete — pipeline status: done"
  echo "════════════════════════════════════════════════════"
  echo ""
  exit 0
elif [[ -z "$final_status" ]]; then
  fail "Timeout after ${SMOKE_TIMEOUT}s — pipeline still running"
  info "run_id : $run_id"
  info "state  : cat $state_file"
  info "logs   : cat $project_base/pipeline.log"
  echo "════════════════════════════════════════════════════"
  echo ""
  exit 1
else
  fail "Pipeline ended with status='$final_status'"
  info "run_id : $run_id"
  info "state  : cat $state_file"
  info "logs   : cat $project_base/pipeline.log"
  echo "════════════════════════════════════════════════════"
  echo ""
  exit 1
fi
