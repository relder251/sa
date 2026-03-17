#!/usr/bin/env bash
set -Eeuo pipefail

umask 077

# Config via environment or /etc/default/twingate-connector-guard
COMPOSE_DIR="${COMPOSE_DIR:-/opt/agentic-sdlc}"
COMPOSE_SERVICE="${COMPOSE_SERVICE:-twingate}"
ENV_FILE="${ENV_FILE:-${COMPOSE_DIR}/.env}"
ROTATE_COMMAND="${ROTATE_COMMAND:-${COMPOSE_DIR}/scripts/twingate/rotate-twingate-connector.sh --verbose}"
LOG_LINES="${LOG_LINES:-200}"
LOOKBACK_SECONDS="${LOOKBACK_SECONDS:-900}"
EXPIRY_THRESHOLD_SECONDS="${EXPIRY_THRESHOLD_SECONDS:-86400}"
FAIL_THRESHOLD="${FAIL_THRESHOLD:-3}"
STATE_FILE="${STATE_FILE:-/var/lib/twingate-connector-guard/state.env}"
LOCK_FILE="${LOCK_FILE:-/var/lock/twingate-connector-guard.lock}"
FORCE_ROTATE="${FORCE_ROTATE:-0}"
DRY_RUN="${DRY_RUN:-0}"
VERBOSE="${VERBOSE:-0}"

mkdir -p "$(dirname "$STATE_FILE")" "$(dirname "$LOCK_FILE")"
exec 9>"$LOCK_FILE"
flock -n 9 || { echo "guard already running"; exit 0; }

log() { printf '%s %s\n' "$(date -u +'%Y-%m-%dT%H:%M:%SZ')" "$*"; }
verbose() { [ "$VERBOSE" = "1" ] && log "$*" || true; }
run() { if [ "$DRY_RUN" = "1" ]; then log "DRY_RUN: $*"; else eval "$@"; fi; }

b64url_decode() {
  local input="$1"
  local rem=$(( ${#input} % 4 ))
  if [ $rem -eq 2 ]; then input+="=="; elif [ $rem -eq 3 ]; then input+="="; elif [ $rem -eq 1 ]; then return 1; fi
  printf '%s' "$input" | tr '_-' '/+' | base64 -d 2>/dev/null
}

jwt_exp_from_token() {
  local token="$1"
  [ -n "$token" ] || return 1
  local payload
  payload="$(printf '%s' "$token" | cut -d'.' -f2)"
  [ -n "$payload" ] || return 1
  b64url_decode "$payload" | python3 - <<'PY'
import json,sys
try:
    data=json.load(sys.stdin)
    exp=data.get('exp')
    if exp is None:
        raise SystemExit(1)
    print(int(exp))
except Exception:
    raise SystemExit(1)
PY
}

env_value() {
  local key="$1"
  [ -f "$ENV_FILE" ] || return 1
  python3 - "$ENV_FILE" "$key" <<'PY'
import sys
path,key=sys.argv[1:3]
for raw in open(path,'r',encoding='utf-8',errors='ignore'):
    line=raw.strip()
    if not line or line.startswith('#') or '=' not in line:
        continue
    k,v=line.split('=',1)
    if k.strip()==key:
        print(v.strip().strip('"').strip("'"))
        break
PY
}

save_state() {
  cat > "$STATE_FILE" <<EOFSTATE
LAST_CHECK_EPOCH=${NOW}
LAST_REASON=${LAST_REASON:-none}
LAST_ACTION=${LAST_ACTION:-none}
LAST_ROTATE_EPOCH=${LAST_ROTATE_EPOCH:-0}
FAIL_STREAK=${FAIL_STREAK:-0}
EOFSTATE
}

load_state() {
  LAST_REASON=none
  LAST_ACTION=none
  LAST_ROTATE_EPOCH=0
  FAIL_STREAK=0
  [ -f "$STATE_FILE" ] && source "$STATE_FILE" || true
}

NOW="$(date -u +%s)"
load_state

cd "$COMPOSE_DIR"

# Gather live state
LIVE_STATE="$(docker inspect "$COMPOSE_SERVICE" --format '{{.State.Status}}' 2>/dev/null || true)"
HEALTH_STATE="$(docker inspect "$COMPOSE_SERVICE" --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' 2>/dev/null || true)"
LOG_TAIL="$(docker compose logs --tail="$LOG_LINES" "$COMPOSE_SERVICE" 2>&1 || true)"
ACCESS_TOKEN="$(env_value TWINGATE_ACCESS_TOKEN || true)"
TOKEN_EXP=""
TOKEN_SECONDS_LEFT=""
if [ -n "$ACCESS_TOKEN" ]; then
  TOKEN_EXP="$(jwt_exp_from_token "$ACCESS_TOKEN" 2>/dev/null || true)"
  if [ -n "$TOKEN_EXP" ]; then
    TOKEN_SECONDS_LEFT=$(( TOKEN_EXP - NOW ))
  fi
fi

# Signals
ERROR_COUNT="$(printf '%s\n' "$LOG_TAIL" | grep -Eci 'State: Error|Could not connect|authentication|token is expired|expired token|invalid token' || true)"
ONLINE_SEEN=0
printf '%s\n' "$LOG_TAIL" | grep -q 'State: Online' && ONLINE_SEEN=1 || true
TOKEN_EXPIRED_LOG=0
printf '%s\n' "$LOG_TAIL" | grep -Eqi 'token is expired|expired token' && TOKEN_EXPIRED_LOG=1 || true
AUTH_FAIL_PATTERN=0
printf '%s\n' "$LOG_TAIL" | grep -Eqi 'State: Error|Could not connect|authentication' && AUTH_FAIL_PATTERN=1 || true

verbose "state=${LIVE_STATE:-unknown} health=${HEALTH_STATE:-unknown} errors=${ERROR_COUNT} online_seen=${ONLINE_SEEN} token_left=${TOKEN_SECONDS_LEFT:-unknown}"

SHOULD_ROTATE=0
LAST_REASON="healthy"

if [ "$FORCE_ROTATE" = "1" ]; then
  SHOULD_ROTATE=1
  LAST_REASON="forced"
elif [ "$TOKEN_EXPIRED_LOG" = "1" ]; then
  SHOULD_ROTATE=1
  LAST_REASON="token-expired-log"
elif [ -n "$TOKEN_SECONDS_LEFT" ] && [ "$TOKEN_SECONDS_LEFT" -le "$EXPIRY_THRESHOLD_SECONDS" ]; then
  # Preemptive rotation window.
  SHOULD_ROTATE=1
  LAST_REASON="token-near-expiry"
elif [ "$AUTH_FAIL_PATTERN" = "1" ] && [ -n "$TOKEN_SECONDS_LEFT" ] && [ "$TOKEN_SECONDS_LEFT" -le 0 ]; then
  SHOULD_ROTATE=1
  LAST_REASON="auth-failure-and-token-expired"
elif [ "$AUTH_FAIL_PATTERN" = "1" ] && [ "$ERROR_COUNT" -ge "$FAIL_THRESHOLD" ] && [ "$ONLINE_SEEN" = "0" ]; then
  # Connection is broken, but only auto-rotate if token metadata also suggests expiry risk.
  if [ -z "$TOKEN_SECONDS_LEFT" ]; then
    LAST_REASON="auth-failure-token-unreadable"
  else
    LAST_REASON="auth-failure-not-token-related"
  fi
fi

if [ "$SHOULD_ROTATE" = "1" ]; then
  if [ $(( NOW - LAST_ROTATE_EPOCH )) -lt 600 ]; then
    LAST_ACTION="suppressed-recent-rotation"
    log "rotation suppressed; last rotation attempt was less than 10 minutes ago"
  else
    log "rotation trigger matched: $LAST_REASON"
    if run "$ROTATE_COMMAND"; then
      LAST_ROTATE_EPOCH="$NOW"
      LAST_ACTION="rotated"
      FAIL_STREAK=0
      save_state
      exit 0
    else
      LAST_ACTION="rotation-failed"
      FAIL_STREAK=$(( FAIL_STREAK + 1 ))
      save_state
      exit 1
    fi
  fi
else
  LAST_ACTION="none"
  if [ "$AUTH_FAIL_PATTERN" = "1" ]; then
    FAIL_STREAK=$(( FAIL_STREAK + 1 ))
  else
    FAIL_STREAK=0
  fi
fi

save_state

if [ "$VERBOSE" = "1" ]; then
  log "no rotation performed; reason=$LAST_REASON action=$LAST_ACTION"
fi
