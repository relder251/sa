#!/usr/bin/env bash
# fix-permissions.sh — idempotent runtime permissions for the Agentic SDLC stack
#
# WHY THIS EXISTS:
#   Several repo files and directories need permissions that git does not preserve:
#     - n8n runs as uid=1000 (node) inside its container; needs group-write on files
#       it modifies at runtime (services.json, output/, opportunities/)
#     - git pull / git checkout reset file permissions to what git stored (644/755)
#     - Docker bind-mounts expose host filesystem ownership directly to containers
#
# USAGE:
#   bash scripts/fix-permissions.sh              # apply all fixes
#   bash scripts/fix-permissions.sh --check      # report what needs fixing (exit 1 if any)
#   bash scripts/fix-permissions.sh --verbose    # show every action taken
#
# WHEN TO RUN:
#   - After every git pull / git checkout on VPS (post-merge hook calls this)
#   - After deploy.sh
#   - Before running smoke tests (validate-prod.sh calls this)
#   - After any manual git restore of a tracked file
#
# PERMISSION MAP:
#   Path                        Mode  Owner       Reason
#   ─────────────────────────── ────  ──────────  ──────────────────────────────────────
#   portal/                     775   root:1000   n8n (gid=1000) creates files here
#   portal/services.json        664   root:1000   n8n writes CRUD ops at runtime
#   output/                     775   root:1000   n8n writes workflow outputs
#   opportunities/              775   root:1000   n8n writes opportunity files
#   workflows/                  775   root:1000   n8n reads/writes workflow exports

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CHECK_ONLY=false
VERBOSE=false

for arg in "$@"; do
  case "$arg" in
    --check)   CHECK_ONLY=true ;;
    --verbose) VERBOSE=true ;;
  esac
done

green='\033[0;32m'
red='\033[0;31m'
yellow='\033[0;33m'
reset='\033[0m'

ISSUES=()
FIXED=()

# ── Helper: check and optionally fix a file/directory ────────────────────────
# Usage: fix_perm PATH EXPECTED_MODE EXPECTED_OWNER [create_if_missing]
fix_perm() {
  local path="$1"
  local expected_mode="$2"
  local expected_owner="$3"   # "uid:gid" or "user:group" — compared numerically
  local create_missing="${4:-false}"

  if [ ! -e "$path" ]; then
    if [ "$create_missing" = "true" ]; then
      if [ "$CHECK_ONLY" = "false" ]; then
        if [[ "$path" == */ ]]; then
          mkdir -p "$path"
        else
          touch "$path"
        fi
        [ "$VERBOSE" = "true" ] && echo -e "  ${yellow}CREATED${reset}  $path"
      else
        ISSUES+=("missing: $path")
        return
      fi
    else
      [ "$VERBOSE" = "true" ] && echo -e "  ${yellow}SKIP${reset}    $path (does not exist)"
      return
    fi
  fi

  local current_mode current_owner needs_fix=false
  current_mode=$(stat -c "%a" "$path" 2>/dev/null || echo "???")
  current_owner=$(stat -c "%u:%g" "$path" 2>/dev/null || echo "?:?")

  # Resolve expected_owner to uid:gid for comparison
  local exp_uid exp_gid
  if [[ "$expected_owner" =~ ^[0-9]+:[0-9]+$ ]]; then
    exp_uid="${expected_owner%%:*}"
    exp_gid="${expected_owner##*:}"
  else
    exp_uid=$(id -u "${expected_owner%%:*}" 2>/dev/null || echo "${expected_owner%%:*}")
    exp_gid=$(getent group "${expected_owner##*:}" 2>/dev/null | cut -d: -f3 || echo "${expected_owner##*:}")
  fi
  local expected_owner_numeric="${exp_uid}:${exp_gid}"

  [ "$current_mode"  != "$expected_mode"         ] && needs_fix=true
  [ "$current_owner" != "$expected_owner_numeric" ] && needs_fix=true

  if [ "$needs_fix" = "false" ]; then
    [ "$VERBOSE" = "true" ] && echo -e "  ${green}OK${reset}      [$current_mode $current_owner] $path"
    return
  fi

  local issue="$path: mode=$current_mode (want $expected_mode) owner=$current_owner (want $expected_owner_numeric)"

  if [ "$CHECK_ONLY" = "true" ]; then
    echo -e "  ${red}NEEDS FIX${reset}  $issue"
    ISSUES+=("$issue")
  else
    chmod "$expected_mode" "$path"
    chown "${exp_uid}:${exp_gid}" "$path"
    FIXED+=("$path → mode=$expected_mode owner=$expected_owner_numeric")
    [ "$VERBOSE" = "true" ] && echo -e "  ${green}FIXED${reset}   $path ($current_mode $current_owner → $expected_mode $expected_owner_numeric)"
  fi
}

# ── Also set assume-unchanged on services.json to prevent git from flagging ──
# runtime writes as "unstaged changes". This is VPS-only (no-op on local).
set_assume_unchanged() {
  local path="$1"
  if git -C "$REPO_DIR" ls-files --error-unmatch "$path" &>/dev/null 2>&1; then
    git -C "$REPO_DIR" update-index --assume-unchanged "$path" 2>/dev/null || true
  fi
}

# ── Main ──────────────────────────────────────────────────────────────────────
[ "$CHECK_ONLY" = "false" ] && [ "$VERBOSE" = "false" ] && echo "Fixing permissions in $REPO_DIR ..."

# n8n (uid=1000, gid=1000) needs write access to portal data files
fix_perm "$REPO_DIR/portal"               775  "0:1000"
fix_perm "$REPO_DIR/portal/services.json" 664  "0:1000"  true

# n8n needs write access to output directories for workflow results
fix_perm "$REPO_DIR/output"               775  "0:1000"
fix_perm "$REPO_DIR/opportunities"        775  "0:1000"

# n8n reads/writes workflow exports (import/export commands)
fix_perm "$REPO_DIR/workflows"            775  "0:1000"

# Git: tell git to ignore runtime changes to services.json
set_assume_unchanged "portal/services.json"

# ── Report ────────────────────────────────────────────────────────────────────
if [ "$CHECK_ONLY" = "true" ]; then
  echo ""
  if [ ${#ISSUES[@]} -eq 0 ]; then
    echo -e "${green}✅ All permissions correct${reset}"
    exit 0
  else
    echo -e "${red}❌ ${#ISSUES[@]} permission issue(s) found:${reset}"
    for issue in "${ISSUES[@]}"; do
      echo -e "  ${red}•${reset} $issue"
    done
    exit 1
  fi
else
  if [ ${#FIXED[@]} -eq 0 ]; then
    echo -e "${green}✅ All permissions already correct${reset}"
  else
    echo -e "${green}✅ Fixed ${#FIXED[@]} permission(s):${reset}"
    for f in "${FIXED[@]}"; do
      echo -e "  ${green}•${reset} $f"
    done
  fi
fi
