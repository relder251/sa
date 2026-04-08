#!/usr/bin/env bash
# snapshot-digests.sh
# Captures current image digests for all Watchtower-monitored containers
# and commits them to git as a rollback reference point.
#
# Runs at 02:55 daily (5 min before Watchtower's 03:00 schedule).
# Safe to run manually at any time.
#
# Exit codes: 0 = success (committed or no changes), 1 = fatal error

set -euo pipefail

REPO_DIR="/opt/agentic-sdlc"
DIGEST_DIR="${REPO_DIR}/digests"
DIGEST_FILE="${DIGEST_DIR}/image-digests.txt"
DIGEST_DATA_TMP="${DIGEST_DIR}/.digests-data.tmp"
LOG_TAG="snapshot-digests"

# log() writes to stderr so it never pollutes stdout/file redirects
log() { echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) [${LOG_TAG}] $*" >&2; }

# Require running as root (or with docker access)
if ! docker info > /dev/null 2>&1; then
  log "ERROR: Cannot connect to Docker daemon. Exiting."
  exit 1
fi

mkdir -p "${DIGEST_DIR}"

log "Capturing image digests for Watchtower-monitored containers..."

# Get all containers with watchtower.enable=true label
MONITORED=$(docker ps --format '{{.Names}}' --filter "label=com.centurylinklabs.watchtower.enable=true" 2>/dev/null | sort)

if [[ -z "${MONITORED}" ]]; then
  log "WARNING: No Watchtower-monitored containers found."
  exit 0
fi

# Build digest data lines (no headers) to a temp file for comparison
: > "${DIGEST_DATA_TMP}"
while IFS= read -r container; do
  # Get the image tag the container was started from
  IMAGE=$(docker inspect "${container}" --format '{{.Config.Image}}' 2>/dev/null || echo "unknown")
  CREATED=$(docker inspect "${container}" --format '{{.Created}}' 2>/dev/null | cut -c1-19 || echo "unknown")

  # Get the full repo digest from the image itself (most reliable source)
  DIGEST=""
  if [[ "${IMAGE}" != "unknown" ]]; then
    DIGEST=$(docker image inspect "${IMAGE}" --format '{{range .RepoDigests}}{{.}}{{end}}' 2>/dev/null | head -1 || echo "")
  fi

  # Fall back to full image ID sha256 if no repo digest (local builds, pulled without digest)
  if [[ -z "${DIGEST}" ]]; then
    DIGEST=$(docker inspect "${container}" --format '{{.Image}}' 2>/dev/null || echo "unknown")
  fi

  echo "${container}  image=${IMAGE}  digest=${DIGEST}  created=${CREATED}" >> "${DIGEST_DATA_TMP}"
  log "  ${container}: ${DIGEST}"
done <<< "${MONITORED}"

# Compare new digest data against existing file (strip comment lines from existing)
CHANGED=false
if [[ ! -f "${DIGEST_FILE}" ]]; then
  CHANGED=true
else
  EXISTING_DATA=$(grep -v '^#' "${DIGEST_FILE}" 2>/dev/null | grep -v '^[[:space:]]*$' || true)
  NEW_DATA=$(cat "${DIGEST_DATA_TMP}")
  if [[ "${EXISTING_DATA}" != "${NEW_DATA}" ]]; then
    CHANGED=true
  fi
fi

# Always write final file with fresh timestamp header + digest lines
{
  echo "# Image digest snapshot"
  echo "# Generated: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "# Purpose: pre-update rollback reference (Watchtower runs at 03:00)"
  echo "#"
  cat "${DIGEST_DATA_TMP}"
} > "${DIGEST_FILE}"

rm -f "${DIGEST_DATA_TMP}"

# Git commit only when actual digest values changed
if [[ "${CHANGED}" == "true" ]]; then
  cd "${REPO_DIR}"
  git add digests/image-digests.txt
  git commit -m "chore: pre-update image digest snapshot $(date -u +%Y-%m-%d)

Automatic snapshot before Watchtower 03:00 update window.
Containers monitored: $(echo "${MONITORED}" | tr '\n' ' ' | sed 's/ $//')
"
  log "Committed digest snapshot to git (digests changed)."
else
  log "No digest changes since last snapshot — nothing to commit."
fi

log "Done."
