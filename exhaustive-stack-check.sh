#!/usr/bin/env bash
set -Eeuo pipefail

########################################
# Config
########################################
STACK_DIR="${STACK_DIR:-/opt/agentic-sdlc}"
PUBLIC_CERT_DIR="${PUBLIC_CERT_DIR:-/opt/sovereignadvisory/ssl}"
CERT_NAME="${CERT_NAME:-sovereignadvisory.ai}"

# Compose service names
PRIVATE_NGINX_SERVICE="${PRIVATE_NGINX_SERVICE:-nginx-private}"
TWINGATE_SERVICE_CANDIDATES="${TWINGATE_SERVICE_CANDIDATES:-twingate connector twingate-connector}"
POSTGRES_SERVICE="${POSTGRES_SERVICE:-postgres}"
N8N_SERVICE="${N8N_SERVICE:-n8n}"
OLLAMA_SERVICE="${OLLAMA_SERVICE:-ollama}"
LITELLM_SERVICE="${LITELLM_SERVICE:-litellm}"
WEBUI_SERVICE="${WEBUI_SERVICE:-webui}"
JUPYTER_SERVICE="${JUPYTER_SERVICE:-jupyter}"
PIPELINE_SERVICE="${PIPELINE_SERVICE:-pipeline-server}"

# Expected internal ports
N8N_PORT="${N8N_PORT:-5678}"
OLLAMA_PORT="${OLLAMA_PORT:-11434}"
LITELLM_PORT="${LITELLM_PORT:-4000}"
WEBUI_PORT="${WEBUI_PORT:-3000}"
JUPYTER_PORT="${JUPYTER_PORT:-8888}"
PIPELINE_PORT="${PIPELINE_PORT:-5002}"
POSTGRES_PORT="${POSTGRES_PORT:-5432}"

# Expected private listener
PRIVATE_BIND_EXPECTED="${PRIVATE_BIND_EXPECTED:-127.0.0.1:8443}"
PUBLIC_PORTS_REGEX=':(80|443)\b'
UNEXPECTED_PUBLIC_PORTS_REGEX=':(3000|4000|5002|5432|5678|8888|11434)\b'

########################################
# Helpers
########################################
red()    { printf '\033[31m%s\033[0m\n' "$*"; }
green()  { printf '\033[32m%s\033[0m\n' "$*"; }
yellow() { printf '\033[33m%s\033[0m\n' "$*"; }
blue()   { printf '\033[34m%s\033[0m\n' "$*"; }

PASS_COUNT=0
FAIL_COUNT=0
WARN_COUNT=0

pass() { green "[PASS] $*"; PASS_COUNT=$((PASS_COUNT+1)); }
fail() { red   "[FAIL] $*"; FAIL_COUNT=$((FAIL_COUNT+1)); }
warn() { yellow "[WARN] $*"; WARN_COUNT=$((WARN_COUNT+1)); }

have() { command -v "$1" >/dev/null 2>&1; }

section() {
  echo
  blue "============================================================"
  blue "$*"
  blue "============================================================"
}

check_cmd() {
  local desc="$1"
  shift
  if "$@" >/dev/null 2>&1; then
    pass "$desc"
  else
    fail "$desc"
  fi
}

capture_cmd() {
  local desc="$1"
  shift
  echo "--- $desc ---"
  "$@" 2>&1 || true
  echo
}

service_exists() {
  docker compose config --services 2>/dev/null | grep -Fxq "$1"
}

container_id_for_service() {
  docker compose ps -q "$1" 2>/dev/null | head -n1
}

service_running() {
  local cid
  cid="$(container_id_for_service "$1")"
  [ -n "$cid" ] && [ "$(docker inspect -f '{{.State.Running}}' "$cid" 2>/dev/null || echo false)" = "true" ]
}

get_networks_for_service() {
  local cid
  cid="$(container_id_for_service "$1")"
  [ -n "$cid" ] || return 1
  docker inspect -f '{{range $k,$v := .NetworkSettings.Networks}}{{println $k}}{{end}}' "$cid" 2>/dev/null | sort -u
}

first_user_network() {
  docker compose config --format json 2>/dev/null \
    | awk '
      /"networks":/ { in_n=1; next }
      in_n && /\}/ { exit }
      in_n && /"[A-Za-z0-9_.-]+":/ {
        gsub(/[",: ]/,"",$1); print $1
      }' \
    | head -n1
}

require_file() {
  local f="$1"
  [ -e "$f" ] && pass "Exists: $f" || fail "Missing: $f"
}

require_symlink_target() {
  local f="$1"
  if [ -L "$f" ] || [ -e "$f" ]; then
    if readlink -f "$f" >/dev/null 2>&1; then
      pass "Resolvable certificate path: $f -> $(readlink -f "$f")"
    else
      fail "Unresolvable symlink/path: $f"
    fi
  else
    fail "Missing certificate path: $f"
  fi
}

http_code_from_exec() {
  local service="$1"
  local url="$2"
  docker compose exec -T "$service" sh -lc "wget -S --spider -T 5 '$url' 2>&1 | awk '/HTTP\\// {print \$2; exit}'" 2>/dev/null || true
}

raw_get_from_exec() {
  local service="$1"
  local url="$2"
  docker compose exec -T "$service" sh -lc "wget -qO- -T 5 '$url'" 2>/dev/null || true
}

########################################
# Start
########################################
cd "$STACK_DIR"

section "Preflight"
check_cmd "docker is installed" have docker
check_cmd "docker compose is available" docker compose version
check_cmd "ss is available" have ss
check_cmd "grep is available" have grep

section "Compose syntax and service inventory"
if docker compose config >/dev/null 2>&1; then
  pass "docker compose config parses successfully"
else
  fail "docker compose config does not parse"
  docker compose config || true
  exit 1
fi

echo "Compose services:"
docker compose config --services || true
echo

for svc in "$PRIVATE_NGINX_SERVICE" "$POSTGRES_SERVICE" "$N8N_SERVICE" "$OLLAMA_SERVICE" "$LITELLM_SERVICE" "$WEBUI_SERVICE" "$JUPYTER_SERVICE"; do
  if service_exists "$svc"; then
    pass "Compose service exists: $svc"
  else
    warn "Compose service not found: $svc"
  fi
done

section "Critical file layout"
require_file "$STACK_DIR/docker-compose.yml"
require_file "$STACK_DIR/nginx-private/nginx.conf"
require_file "$STACK_DIR/nginx-private/conf.d/private.conf"
require_file "$PUBLIC_CERT_DIR/live"
require_file "$PUBLIC_CERT_DIR/archive"
require_symlink_target "$PUBLIC_CERT_DIR/live/$CERT_NAME/fullchain.pem"
require_symlink_target "$PUBLIC_CERT_DIR/live/$CERT_NAME/privkey.pem"

section "Certificate visibility inside private nginx container image context"
if docker run --rm -v "$PUBLIC_CERT_DIR:/etc/letsencrypt:ro" nginx:1.27-alpine \
  sh -lc "test -e /etc/letsencrypt/live/$CERT_NAME/fullchain.pem && test -e /etc/letsencrypt/live/$CERT_NAME/privkey.pem"; then
  pass "Certificate files are visible inside an nginx container"
else
  fail "Certificate files are NOT visible inside an nginx container"
fi

capture_cmd "Certificate symlink resolution inside nginx container" \
  docker run --rm -v "$PUBLIC_CERT_DIR:/etc/letsencrypt:ro" nginx:1.27-alpine \
  sh -lc "ls -la /etc/letsencrypt/live/$CERT_NAME && readlink -f /etc/letsencrypt/live/$CERT_NAME/fullchain.pem && readlink -f /etc/letsencrypt/live/$CERT_NAME/privkey.pem"

section "Service start state"
docker compose ps || true

for svc in "$PRIVATE_NGINX_SERVICE" "$POSTGRES_SERVICE" "$N8N_SERVICE" "$OLLAMA_SERVICE" "$LITELLM_SERVICE" "$WEBUI_SERVICE" "$JUPYTER_SERVICE"; do
  if service_exists "$svc"; then
    if service_running "$svc"; then
      pass "Service is running: $svc"
    else
      fail "Service is not running: $svc"
    fi
  fi
done

section "Docker network topology"
COMPOSE_NETWORKS="$(docker compose config --format json 2>/dev/null | grep -o '"[A-Za-z0-9_.-]\+": {' | tr -d '"' | sed 's/: {//' | sort -u || true)"
echo "Defined networks:"
printf '%s\n' "$COMPOSE_NETWORKS"
echo

COMMON_NETWORK=""
if service_exists "$PRIVATE_NGINX_SERVICE" && service_running "$PRIVATE_NGINX_SERVICE"; then
  COMMON_NETWORK="$(get_networks_for_service "$PRIVATE_NGINX_SERVICE" | head -n1 || true)"
fi

if [ -n "$COMMON_NETWORK" ]; then
  pass "Private nginx is attached to network: $COMMON_NETWORK"
else
  fail "Could not determine private nginx network attachment"
fi

for svc in "$N8N_SERVICE" "$OLLAMA_SERVICE" "$LITELLM_SERVICE" "$WEBUI_SERVICE" "$JUPYTER_SERVICE" "$POSTGRES_SERVICE"; do
  if service_exists "$svc" && service_running "$svc" && [ -n "$COMMON_NETWORK" ]; then
    if get_networks_for_service "$svc" | grep -Fxq "$COMMON_NETWORK"; then
      pass "$svc shares network $COMMON_NETWORK with $PRIVATE_NGINX_SERVICE"
    else
      fail "$svc does not share network $COMMON_NETWORK with $PRIVATE_NGINX_SERVICE"
      capture_cmd "Networks for $svc" get_networks_for_service "$svc"
    fi
  fi
done

if [ -n "$COMMON_NETWORK" ]; then
  capture_cmd "Members of network $COMMON_NETWORK" docker network inspect "$COMMON_NETWORK"
fi

section "Private nginx configuration validity"
if service_exists "$PRIVATE_NGINX_SERVICE" && service_running "$PRIVATE_NGINX_SERVICE"; then
  if docker compose exec -T "$PRIVATE_NGINX_SERVICE" nginx -t >/tmp/nginx_test.out 2>&1; then
    pass "nginx -t passes inside $PRIVATE_NGINX_SERVICE"
  else
    fail "nginx -t fails inside $PRIVATE_NGINX_SERVICE"
  fi
  cat /tmp/nginx_test.out || true
else
  fail "Cannot validate nginx config because $PRIVATE_NGINX_SERVICE is not running"
fi

section "DNS resolution from private nginx to internal services"
if service_exists "$PRIVATE_NGINX_SERVICE" && service_running "$PRIVATE_NGINX_SERVICE"; then
  for host in "$N8N_SERVICE" "$OLLAMA_SERVICE" "$LITELLM_SERVICE" "$WEBUI_SERVICE" "$JUPYTER_SERVICE" "$POSTGRES_SERVICE"; do
    if service_exists "$host"; then
      if docker compose exec -T "$PRIVATE_NGINX_SERVICE" getent hosts "$host" >/tmp/getent.out 2>&1; then
        pass "Private nginx resolves service name: $host"
        cat /tmp/getent.out
      else
        fail "Private nginx cannot resolve service name: $host"
      fi
    fi
  done
fi

section "Direct upstream reachability from private nginx"
if service_exists "$PRIVATE_NGINX_SERVICE" && service_running "$PRIVATE_NGINX_SERVICE"; then
  declare -A URLS
  service_exists "$N8N_SERVICE"     && URLS["$N8N_SERVICE"]="http://$N8N_SERVICE:$N8N_PORT/"
  service_exists "$OLLAMA_SERVICE"  && URLS["$OLLAMA_SERVICE"]="http://$OLLAMA_SERVICE:$OLLAMA_PORT/api/tags"
  service_exists "$LITELLM_SERVICE" && URLS["$LITELLM_SERVICE"]="http://$LITELLM_SERVICE:$LITELLM_PORT/"
  service_exists "$WEBUI_SERVICE"   && URLS["$WEBUI_SERVICE"]="http://$WEBUI_SERVICE:$WEBUI_PORT/"
  service_exists "$JUPYTER_SERVICE" && URLS["$JUPYTER_SERVICE"]="http://$JUPYTER_SERVICE:$JUPYTER_PORT/"
  service_exists "$PIPELINE_SERVICE" && URLS["$PIPELINE_SERVICE"]="http://$PIPELINE_SERVICE:$PIPELINE_PORT/"

  for svc in "${!URLS[@]}"; do
    code="$(http_code_from_exec "$PRIVATE_NGINX_SERVICE" "${URLS[$svc]}")"
    if [ -n "$code" ]; then
      pass "Reachable from private nginx: $svc -> ${URLS[$svc]} (HTTP $code)"
    else
      warn "Could not confirm HTTP status from private nginx to $svc -> ${URLS[$svc]}"
      capture_cmd "Manual fetch for $svc" docker compose exec -T "$PRIVATE_NGINX_SERVICE" sh -lc "wget -S --spider -T 5 '${URLS[$svc]}' 2>&1 || true"
    fi
  done

  if service_exists "$OLLAMA_SERVICE"; then
    OLLAMA_JSON="$(raw_get_from_exec "$PRIVATE_NGINX_SERVICE" "http://$OLLAMA_SERVICE:$OLLAMA_PORT/api/tags" || true)"
    if echo "$OLLAMA_JSON" | grep -q '"models"'; then
      pass "Ollama API returned model listing JSON"
    else
      warn "Ollama API did not return expected JSON"
    fi
  fi
fi

section "Host listener exposure"
capture_cmd "Listening sockets of interest" ss -tulpn
if ss -tulpn | grep -qE "$PUBLIC_PORTS_REGEX"; then
  pass "Host is listening on 80 and/or 443"
else
  fail "Host is not listening on expected public ports 80/443"
fi

if ss -tulpn | grep -qF "$PRIVATE_BIND_EXPECTED"; then
  pass "Private nginx is bound to expected loopback address $PRIVATE_BIND_EXPECTED"
else
  fail "Private nginx is not bound to expected loopback address $PRIVATE_BIND_EXPECTED"
fi

UNEXPECTED_PUBLIC="$(ss -tulpn | grep -E "$UNEXPECTED_PUBLIC_PORTS_REGEX" | grep -v '127\.0\.0\.1:' || true)"
if [ -z "$UNEXPECTED_PUBLIC" ]; then
  pass "No unexpected internal app ports are exposed publicly"
else
  fail "Unexpected internal app ports exposed publicly"
  echo "$UNEXPECTED_PUBLIC"
fi

section "Compose port publishing sanity"
capture_cmd "docker compose port mappings" docker compose ps
if docker compose ps 2>/dev/null | grep -E '0\.0\.0\.0:(3000|4000|5002|5432|5678|8888|11434)->|:::?(3000|4000|5002|5432|5678|8888|11434)->' >/dev/null; then
  fail "Compose shows internal service ports published publicly"
else
  pass "Compose does not show internal service ports published publicly"
fi

section "Postgres checks"
if service_exists "$POSTGRES_SERVICE" && service_running "$POSTGRES_SERVICE"; then
  if docker compose exec -T "$POSTGRES_SERVICE" pg_isready >/dev/null 2>&1; then
    pass "Postgres responds to pg_isready"
  else
    fail "Postgres does not respond to pg_isready"
  fi

  capture_cmd "Postgres databases" docker compose exec -T "$POSTGRES_SERVICE" psql -U postgres -lqt
  if docker compose exec -T "$POSTGRES_SERVICE" psql -U postgres -lqt 2>/dev/null | awk '{print $1}' | grep -Fxq n8n; then
    pass "Postgres database 'n8n' exists"
  else
    warn "Postgres database 'n8n' does not exist"
  fi
fi

section "Private reverse proxy path checks"
if service_exists "$PRIVATE_NGINX_SERVICE" && service_running "$PRIVATE_NGINX_SERVICE"; then
  for path in /n8n/ /litellm/ /webui/ /jupyter/ /ollama/; do
    code="$(docker compose exec -T "$PRIVATE_NGINX_SERVICE" sh -lc "wget -S --spider --no-check-certificate -T 5 https://127.0.0.1${path} 2>&1 | awk '/HTTP\\// {print \$2; exit}'" 2>/dev/null || true)"
    if [ -n "$code" ]; then
      pass "Private proxy responds on $path (HTTP $code)"
    else
      warn "Could not confirm private proxy response on $path"
    fi
  done
fi

section "Twingate connector presence"
TW_FOUND=0
for svc in $TWINGATE_SERVICE_CANDIDATES; do
  if service_exists "$svc"; then
    TW_FOUND=1
    pass "Twingate-related Compose service exists: $svc"
    if service_running "$svc"; then
      pass "Twingate-related service is running: $svc"
      capture_cmd "Recent logs for $svc" docker compose logs --tail=50 "$svc"
    else
      fail "Twingate-related service is not running: $svc"
    fi
  fi
done

if [ "$TW_FOUND" -eq 0 ]; then
  warn "No Twingate service found from candidates: $TWINGATE_SERVICE_CANDIDATES"
  capture_cmd "Any running containers containing 'twingate'" sh -lc "docker ps --format '{{.Names}} {{.Image}}' | grep -i twingate || true"
fi

section "Recent failure scan"
capture_cmd "Compose logs (last 150 lines per service)" docker compose logs --tail=150
if docker compose logs --tail=150 2>&1 | grep -qiE 'error|emerg|fatal|panic|address already in use|host not found in upstream|cannot load certificate'; then
  warn "Recent logs contain one or more error patterns; review the log output above"
else
  pass "No obvious critical error patterns found in recent logs"
fi

section "Summary"
echo "PASS: $PASS_COUNT"
echo "WARN: $WARN_COUNT"
echo "FAIL: $FAIL_COUNT"

if [ "$FAIL_COUNT" -eq 0 ]; then
  green "Environment validation completed with no failures."
  exit 0
else
  red "Environment validation completed with failures."
  exit 1
fi
