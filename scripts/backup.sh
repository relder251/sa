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
#   /backup             — output directory (host: ./backup)
#   /data/output        — lead PDFs and pipeline output (read-only)
#   /data/opportunities — pipeline input data (read-only)
#   /data/open_webui    — Open WebUI SQLite data dir (read-only)
#   /ssl                — TLS certs at /opt/sovereignadvisory/ssl (read-only)

set -euo pipefail

DRY_RUN=false
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=true

DATE=$(date +%Y-%m-%d)
BACKUP_DIR="${BACKUP_DIR:-/backup}"

run() {
  if $DRY_RUN; then
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

# --- Open WebUI SQLite ---
# cp is safe at 2am with no active writes; copies main db file.
# The WAL file is also copied so the backup is consistent even mid-checkpoint.
echo "--- open_webui ---"
run bash -c "
  cp /data/open_webui/webui.db \"$BACKUP_DIR/open_webui_${DATE}.db.tmp\"
  [ -f /data/open_webui/webui.db-wal ] && cp /data/open_webui/webui.db-wal \"$BACKUP_DIR/open_webui_${DATE}.db-wal\" || true
  mv \"$BACKUP_DIR/open_webui_${DATE}.db.tmp\" \"$BACKUP_DIR/open_webui_${DATE}.db\"
"

# --- Output directory ---
echo "--- output ---"
run bash -c "tar -czf \"$BACKUP_DIR/output_${DATE}.tar.gz.tmp\" -C /data output && mv \"$BACKUP_DIR/output_${DATE}.tar.gz.tmp\" \"$BACKUP_DIR/output_${DATE}.tar.gz\""

# --- Opportunities directory ---
echo "--- opportunities ---"
run bash -c "tar -czf \"$BACKUP_DIR/opportunities_${DATE}.tar.gz.tmp\" -C /data opportunities && mv \"$BACKUP_DIR/opportunities_${DATE}.tar.gz.tmp\" \"$BACKUP_DIR/opportunities_${DATE}.tar.gz\""

# --- Vault raft snapshot ---
# Snapshot written by vault-snapshot Ofelia job at 01:30 into vault/data/
# Copied here (dated) so retention pruning applies uniformly.
echo "--- vault ---"
run bash -c "
  [ -f /data/vault/latest_snapshot.snap ] || { echo 'ERROR: vault snapshot missing'; exit 1; }
  cp /data/vault/latest_snapshot.snap \"$BACKUP_DIR/vault_${DATE}.snap.tmp\" &&
  mv \"$BACKUP_DIR/vault_${DATE}.snap.tmp\" \"$BACKUP_DIR/vault_${DATE}.snap\"
"

# --- Vaultwarden (SQLite + config + RSA key in single tar) ---
# WAL/SHM files included when present for a consistent SQLite backup.
echo "--- vaultwarden ---"
run bash -c "
  EXTRAS=''
  [ -f /data/vaultwarden/db.sqlite3-wal ] && EXTRAS='db.sqlite3-wal db.sqlite3-shm'
  tar -czf \"$BACKUP_DIR/vaultwarden_${DATE}.tar.gz.tmp\" \
    -C /data/vaultwarden db.sqlite3 \$EXTRAS config.json rsa_key.pem
  mv \"$BACKUP_DIR/vaultwarden_${DATE}.tar.gz.tmp\" \"$BACKUP_DIR/vaultwarden_${DATE}.tar.gz\"
"

# --- SSL certificates ---
echo "--- ssl ---"
run bash -c "tar -czf \"$BACKUP_DIR/ssl_${DATE}.tar.gz.tmp\" -C / ssl && mv \"$BACKUP_DIR/ssl_${DATE}.tar.gz.tmp\" \"$BACKUP_DIR/ssl_${DATE}.tar.gz\""

if $DRY_RUN; then
  echo "=== Dry-run complete — no files written ==="
  exit 0
fi

# --- Retention: 7 daily + 4 weekly (Sunday) backups ---
echo "--- pruning old backups ---"

SUNDAYS=$(for i in 0 1 2 3; do date -d "sunday -${i} weeks" +%Y-%m-%d; done \
  | tr '\n' '|' | sed 's/|$//')

# Pass 1: delete non-Sunday .gz files older than 7 days
find "$BACKUP_DIR" \( -name "*.gz" -o -name "*.snap" \) -mtime +7 \
  | grep -vE "($SUNDAYS)" \
  | xargs -r rm -f || true

# Pass 2: delete non-Sunday .db / .db-wal files older than 7 days
find "$BACKUP_DIR" \( -name "*.db" -o -name "*.db-wal" \) -mtime +7 \
  | grep -vE "($SUNDAYS)" \
  | xargs -r rm -f || true

# Pass 3: hard cutoff — delete everything older than 28 days
find "$BACKUP_DIR" \( -name "*.gz" -o -name "*.snap" \) -mtime +28 | xargs -r rm -f
find "$BACKUP_DIR" \( -name "*.db" -o -name "*.db-wal" \) -mtime +28 | xargs -r rm -f

echo "=== Backup complete: $DATE ==="
