# Runbook: Production Database Backup & Restore

**Backup strategy:** Daily automated backup via the `backup` container + ofelia cron at 02:00 UTC.
**Retention:** 7 daily backups + 4 weekly (Sunday) backups — hard cutoff at 28 days.
**Scope:** PostgreSQL (all databases) + output directory + opportunities directory + SSL certificates.

---

## Architecture

```
ofelia (02:00 UTC) ──▶  docker exec backup bash /backup.sh
                               │
                               ├── pg_dumpall → backup_data:/backup/postgres_YYYY-MM-DD.sql.gz
                               ├── tar output  → backup_data:/backup/output_YYYY-MM-DD.tar.gz
                               ├── tar opps    → backup_data:/backup/opportunities_YYYY-MM-DD.tar.gz
                               └── tar ssl     → backup_data:/backup/ssl_YYYY-MM-DD.tar.gz
```

The `backup_data` Docker volume persists backups across container restarts. For off-host durability, mount it to an external path or sync it to cloud storage (see [Off-Host Sync](#off-host-sync) below).

---

## Verifying Backups Are Running

```bash
# Check ofelia scheduled job ran
docker logs ofelia --tail=50 | grep backup

# Check backup files exist and are recent
docker exec backup ls -lh /backup/ | head -20

# Dry-run the backup script manually (no files written)
docker exec backup bash /backup.sh --dry-run
```

---

## Manual Backup (On-Demand)

```bash
docker exec backup bash /backup.sh
```

Verify exit 0 and check `/backup/` for today's files.

---

## Restore Procedure

### Restore PostgreSQL

```bash
# 1. Stop all services that use the DB
docker compose -f docker-compose.prod.yml stop n8n litellm lead-review pipeline-server webui jupyter keycloak

# 2. Drop and recreate all databases (WARNING: destroys all current data)
docker exec -it litellm_db psql -U litellm -c "DROP DATABASE IF EXISTS n8n;"
docker exec -it litellm_db psql -U litellm -c "DROP DATABASE IF EXISTS keycloak;"
docker exec -it litellm_db psql -U litellm -c "DROP DATABASE IF EXISTS litellm;"

# 3. Restore from backup (replace DATE with target date)
DATE=2026-03-20
docker exec backup bash -c "zcat /backup/postgres_${DATE}.sql.gz | psql -U litellm -d postgres"

# 4. Restart services
docker compose -f docker-compose.prod.yml up -d
```

### Restore Output / Opportunities Directories

```bash
DATE=2026-03-20
# Restore to /data/output (mounted at ./output in compose)
docker exec backup bash -c "tar -xzf /backup/output_${DATE}.tar.gz -C /data"
# Restore to /data/opportunities
docker exec backup bash -c "tar -xzf /backup/opportunities_${DATE}.tar.gz -C /data"
```

### Restore SSL Certificates

```bash
DATE=2026-03-20
docker exec backup bash -c "tar -xzf /backup/ssl_${DATE}.tar.gz -C /"
# Reload nginx to pick up restored certs
docker exec sa_nginx_private nginx -s reload
docker exec sa_nginx nginx -s reload
```

---

## Restore Validation

Run the automated restore test (uses a throwaway Postgres container, does not touch production):

```bash
bash scripts/backup_test.sh
```

This script:
1. Starts a fresh `postgres:15` container
2. Pipes the latest `postgres_*.sql.gz` into `psql`
3. Checks that `sa_leads`, `n8n`, and `keycloak` databases exist
4. Tears down the container

Run this monthly or after any major DB schema change.

---

## Off-Host Sync

The `backup_data` volume lives on the VPS. A single disk/host failure loses all backups. Sync to cloud storage for durability.

**Option A — rclone to S3/Backblaze (recommended):**
```bash
# Install rclone on VPS, configure remote, then add a daily cron:
0 3 * * * rclone sync /var/lib/docker/volumes/agentic_sdlc_backup_data/_data s3:my-bucket/agentic-sdlc-backups/
```

**Option B — Host-volume bind mount:**
Replace the named volume with a bind mount to an NFS/CIFS share or an S3-mounted directory in `docker-compose.prod.yml`:
```yaml
volumes:
  - /mnt/backups/agentic-sdlc:/backup
```

---

## Backup Timeout Behaviour

`backup.sh` applies per-operation timeouts:
- `pg_dumpall`: 300s (5 min) — fail-fast if DB is locked
- `tar output`: 120s — fail-fast if output directory is very large
- `tar opportunities`: 60s
- `tar ssl`: 30s

If a timeout fires, the `.tmp` file is left in `/backup/` and the script exits non-zero. Ofelia logs the failure. The partial file does NOT overwrite the previous successful backup (atomic rename pattern).

Check for leftover `.tmp` files:
```bash
docker exec backup ls /backup/*.tmp 2>/dev/null && echo "Stale .tmp file — last backup failed"
```
