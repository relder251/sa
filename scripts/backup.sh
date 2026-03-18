#!/usr/bin/env bash
# backup.sh — daily backup: pg_dumpall + tar snapshots + retention pruning
# Usage: bash backup.sh [--dry-run]
#   --dry-run  Print commands without executing (safe to run anytime)
#
# Environment (set by docker-compose backup service):
#   PGHOST, PGUSER, PGPASSWORD — postgres connection
#   BACKUP_DIR                 — defaults to /backup
#
# Volumes expected:
#   /backup            — output directory (host: ./backup)
#   /data/output       — lead PDFs and pipeline output (read-only)
#   /data/opportunities — pipeline input data (read-only)
#   /ssl               — TLS certs at /opt/sovereignadvisory/ssl (read-only)

set -euo pipefail

DRY_RUN=false
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=true

DATE=$(date +%Y-%m-%d)
BACKUP_DIR="${BACKUP_DIR:-/backup}"

run() {
  if $DRY_RUN; then
    # printf '%q ' safely quotes args without shell metacharacter expansion
    echo "[dry-run] $(printf '%q ' "$@")"
  else
    "$@"
  fi
}

echo "=== Backup starting: $DATE (dry-run: $DRY_RUN) ==="

# --- Postgres: all databases via pg_dumpall ---
# litellm is the bootstrap superuser (POSTGRES_USER in docker-compose.yml).
# pg_dumpall requires superuser — do not replace with an application-scoped role.
# Write to .tmp first; atomic rename on success prevents corrupt partial files.
echo "--- postgres ---"
run bash -c "pg_dumpall | gzip > \"$BACKUP_DIR/postgres_${DATE}.sql.gz.tmp\" && mv \"$BACKUP_DIR/postgres_${DATE}.sql.gz.tmp\" \"$BACKUP_DIR/postgres_${DATE}.sql.gz\""

# --- Output directory ---
echo "--- output ---"
run bash -c "tar -czf \"$BACKUP_DIR/output_${DATE}.tar.gz.tmp\" -C /data output && mv \"$BACKUP_DIR/output_${DATE}.tar.gz.tmp\" \"$BACKUP_DIR/output_${DATE}.tar.gz\""

# --- Opportunities directory ---
echo "--- opportunities ---"
run bash -c "tar -czf \"$BACKUP_DIR/opportunities_${DATE}.tar.gz.tmp\" -C /data opportunities && mv \"$BACKUP_DIR/opportunities_${DATE}.tar.gz.tmp\" \"$BACKUP_DIR/opportunities_${DATE}.tar.gz\""

# --- SSL certificates ---
echo "--- ssl ---"
run bash -c "tar -czf \"$BACKUP_DIR/ssl_${DATE}.tar.gz.tmp\" -C / ssl && mv \"$BACKUP_DIR/ssl_${DATE}.tar.gz.tmp\" \"$BACKUP_DIR/ssl_${DATE}.tar.gz\""

if $DRY_RUN; then
  echo "=== Dry-run complete — no files written ==="
  exit 0
fi

# --- Retention: 7 daily + 4 weekly (Sunday) backups ---
echo "--- pruning old backups ---"

# Collect the 4 most recent Sunday dates to preserve as weekly snapshots
SUNDAYS=$(for i in 0 1 2 3; do date -d "sunday -${i} weeks" +%Y-%m-%d; done \
  | tr '\n' '|' | sed 's/|$//')

# Pass 1: delete non-Sunday files older than 7 days
# grep -vE exits 1 when no lines match (no old files) — || true prevents pipefail abort
find "$BACKUP_DIR" -name "*.gz" -mtime +7 \
  | grep -vE "($SUNDAYS)" \
  | xargs -r rm -f || true

# Pass 2: delete ALL files older than 28 days (hard cutoff — even Sunday snapshots expire)
find "$BACKUP_DIR" -name "*.gz" -mtime +28 | xargs -r rm -f

echo "=== Backup complete: $DATE ==="
