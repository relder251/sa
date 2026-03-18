# Disaster Recovery Drill Checklist

**Purpose:** Validate that a full VPS loss can be recovered from within the target RTO (~15 min, excluding Ollama model re-pull).

**Frequency:** Run this drill quarterly, or after any major infrastructure change.

**Prerequisites:**
- Latest backup files accessible (local `./backup/` or off-site storage)
- `.env` contents available from password manager
- A second machine or clean VM with Docker installed

---

## Pre-drill: Snapshot current state

Before starting the drill, record baseline state for comparison after restore.

- [ ] On production VPS: `docker exec litellm_db psql -U litellm -c "SELECT count(*) FROM sa_leads"`
  — Record count: ___________
- [ ] On production VPS: `docker exec litellm_db psql -U litellm -d n8n -c "SELECT count(*) FROM workflow_entity"`
  — Record count: ___________
- [ ] Note the most recent backup file: `ls -lt ./backup/postgres_*.sql.gz | head -1`
  — File: ___________

---

## DR Drill Steps

### 1. Prepare a clean environment

- [ ] Provision a fresh VM or use a local Docker environment
- [ ] Confirm Docker and Docker Compose are installed: `docker --version && docker compose version`

### 2. Clone the repo

```bash
git clone git@github.com:relder251/sa.git && cd sa
```

- [ ] Verify all expected files are present: `ls scripts/ docs/ docker-compose.yml`

### 3. Restore `.env`

- [ ] Copy `.env` contents from password manager into `~/sa/.env`
- [ ] Verify all required keys are set: `grep -c '=.' .env` (should be > 20)

### 4. Retrieve backup files

If running against a total-loss scenario (backup files not on this machine):

- [ ] Copy latest backup archive from off-site storage into `./backup/`:
  ```bash
  mkdir -p ./backup
  # scp / rclone / manual download here
  ```
- [ ] Verify dump file exists: `ls -lh ./backup/postgres_*.sql.gz`

### 5. Bring the stack up

```bash
docker compose up -d
```

- [ ] Wait for all services to be healthy: `docker compose ps`
  — Expected: all services show `(healthy)` or `Up`

### 6. Restore the database

```bash
# Set PGPASSWORD first (grab from .env), then restore.
# Must use -d postgres (maintenance DB) so pg_dumpall's \connect directives work.
export PGPASSWORD=$(grep '^LITELLM_PASSWORD=' .env | cut -d= -f2)
gunzip -c backup/postgres_<YYYY-MM-DD>.sql.gz \
  | docker exec -i -e PGPASSWORD="$PGPASSWORD" litellm_db psql -U litellm -d postgres
```

- [ ] No errors during restore (warnings about existing roles are OK)

### 7. Validate restore

Run the restore validation script:

```bash
bash scripts/backup_test.sh backup/postgres_<YYYY-MM-DD>.sql.gz
```

- [ ] All checks PASS

Manually verify row counts match pre-drill baseline:

- [ ] `docker exec litellm_db psql -U litellm -c "SELECT count(*) FROM sa_leads"` = ___________
- [ ] `docker exec litellm_db psql -U litellm -d n8n -c "SELECT count(*) FROM workflow_entity"` = ___________

### 8. Restore output and opportunities data (optional)

```bash
tar -xzf backup/output_<YYYY-MM-DD>.tar.gz
tar -xzf backup/opportunities_<YYYY-MM-DD>.tar.gz
```

- [ ] Files extracted successfully

### 9. Restore SSL certs

```bash
mkdir -p /opt/sovereignadvisory
tar -xzf backup/ssl_<YYYY-MM-DD>.tar.gz -C /opt/sovereignadvisory/
```

- [ ] Or note: certbot will re-issue on next startup if certs not restored

### 10. Run smoke test

```bash
bash scripts/smoke_test.sh
```

- [ ] All checks PASS

### 11. Run Playwright regression tests

```bash
pytest tests/test_post_deploy.py -v
```

- [ ] All tests PASS

### 12. Verify n8n credentials (known gap)

n8n encrypted credentials are NOT backed up (known gap — v1). After DR:

- [ ] Log in to n8n UI
- [ ] Check each workflow for broken credential references
- [ ] Re-enter credentials (Slack, Twilio, Notion, etc.) as needed

---

## Post-drill: Record results

- [ ] Time taken: ___________ minutes
- [ ] Issues encountered: ___________
- [ ] Follow-up tasks created: ___________
- [ ] Drill passed: YES / NO

---

## Known gaps

| Gap | Impact | Status |
|---|---|---|
| No off-site backup transfer | Total VPS loss = backup loss | Future work |
| `n8n_data` not backed up | n8n credentials must be re-entered | Future work |
