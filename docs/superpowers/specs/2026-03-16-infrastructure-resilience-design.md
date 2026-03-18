# Infrastructure Resilience — Design Spec

**Date:** 2026-03-16
**Status:** Approved
**Sub-project:** 1 — Infrastructure Resilience

---

## Overview

Establish a practical resilience posture for the Agentic SDLC + Sovereign Advisory stack running at `sovereignadvisory.ai`. The design covers three pillars: automated backups, GitOps-based version control, and concrete rollback runbooks. The goal is to be able to recover from any failure — config mistake, broken container update, or full VPS loss — with minimal downtime and no permanent data loss.

---

## Stack Reference

| Service | Container | State location |
|---|---|---|
| PostgreSQL | `litellm_db` | `pg_data` Docker volume |
| n8n | `n8n` | `n8n_data` volume + postgres (`n8n` DB) |
| LiteLLM | `litellm` | postgres (`litellm` DB) + `litellm_config.yaml` |
| Keycloak | `keycloak` | postgres (`keycloak` DB) |
| Lead Review | `sa_lead_review` | postgres (`litellm` DB, `sa_leads` table) |
| Ollama | `ollama` | `ollama_data` volume (model weights) |
| nginx | `sa_nginx_*` | `nginx/`, `nginx-private/`, `nginx-public/` (in git) |
| Certbot | `sa_certbot_dns` | `/opt/sovereignadvisory/ssl` (host path) |
| Output data | n/a | `./output/`, `./opportunities/` (host paths) |

---

## Pillar 1: Backups

### What to back up

| Target | Contents | Method | Priority |
|---|---|---|---|
| `pg_data` volume | All databases: `litellm` (LiteLLM keys/spend + SA leads), `n8n` (workflow state), `keycloak` (SSO config) | `pg_dumpall` → gzip | **Critical** |
| `output/` | Lead PDFs, pipeline outputs, opportunity results | `tar` → gzip | Important |
| `opportunities/` | Pipeline input opportunity data | `tar` → gzip | Important |
| `/opt/sovereignadvisory/ssl` | TLS certificates for `sovereignadvisory.ai` | `tar` → gzip | Medium |
| `.env` | All API keys and secrets | Manual → password manager | **Critical (out-of-band)** |
| `ollama_data` | Model weights | **Skip** — cheaper to re-pull | Low |
| `n8n_data` | n8n encrypted credential store | **Known gap** — see note below | Low |
| Project files | All code and config | **Already in git** | n/a |

**Why `pg_dumpall` instead of `pg_dump`:** The single postgres instance hosts three separate databases (`litellm`, `n8n`, `keycloak`). `pg_dumpall` captures all three in a single operation, avoiding the need for separate jobs and ensuring a consistent point-in-time snapshot.

**Note on `n8n_data`:** This volume holds n8n's `N8N_ENCRYPTION_KEY`-encrypted credential store (API keys entered via the n8n UI). These credentials are not stored in postgres or git and cannot be recovered from either. Skipping this volume means that after a full DR, all n8n credentials (Slack, Twilio, etc.) must be re-entered manually. This is an accepted known gap for v1. A future improvement would be to `tar` this volume alongside the postgres dump.

**Off-site backup gap:** The backup design is local-only (files written to `./backup/` on the VPS). If the VPS is lost, the backup files are also lost. Full disaster recovery therefore requires an off-site transfer mechanism (rclone to B2/S3, nightly rsync to a second server, etc.). This is out of scope for v1 but is a required follow-up before the DR runbook can provide its stated RTO guarantee.

### Backup service implementation

Add a `backup` service to `docker-compose.yml`. The container stays alive (via `sleep infinity`) so Ofelia can `job-exec` into it on schedule:

```yaml
backup:
  image: postgres:15
  container_name: backup
  volumes:
    - ./backup:/backup
    - ./output:/data/output:ro
    - ./opportunities:/data/opportunities:ro
    - /opt/sovereignadvisory/ssl:/ssl:ro
    - ./scripts/backup.sh:/backup.sh:ro
  environment:
    - PGHOST=postgres
    # litellm is the bootstrap superuser for this postgres instance (created via
    # POSTGRES_USER in docker-compose.yml). pg_dumpall requires superuser privileges.
    # Do not replace this with an application-scoped role.
    - PGUSER=${LITELLM_USER:-litellm}
    - PGPASSWORD=${LITELLM_PASSWORD:-litellm_password}
  command: ["sleep", "infinity"]
  networks:
    - vibe_net
  depends_on:
    postgres:
      condition: service_healthy
  restart: unless-stopped
```

Note: the `pg_data` volume is **not** mounted here. `pg_dumpall` connects to the running postgres instance over the network — it does not read volume files directly. Mounting `pg_data` would be misleading and could produce a corrupt backup if used as a file-level copy while postgres is running.

### Backup script — `scripts/backup.sh`

```bash
#!/bin/bash
set -euo pipefail

DATE=$(date +%Y-%m-%d)
BACKUP_DIR=/backup

# --- Postgres (all databases) ---
# Connects to running postgres via PGHOST/PGUSER/PGPASSWORD env vars.
# litellm is the bootstrap superuser — required for pg_dumpall.
pg_dumpall | gzip > "$BACKUP_DIR/postgres_${DATE}.sql.gz"

# --- Output directory ---
tar -czf "$BACKUP_DIR/output_${DATE}.tar.gz" -C /data output

# --- Opportunities directory ---
tar -czf "$BACKUP_DIR/opportunities_${DATE}.tar.gz" -C /data opportunities

# --- SSL certificates ---
tar -czf "$BACKUP_DIR/ssl_${DATE}.tar.gz" -C / ssl

# --- Retention: keep 7 daily + 4 weekly (Sunday) ---
# Step 1: collect the 4 most recent Sunday dates to preserve
SUNDAYS=$(for i in 0 1 2 3; do date -d "sunday -${i} weeks" +%Y-%m-%d; done | tr '\n' '|' | sed 's/|$//')

# Step 2: delete non-Sunday files older than 7 days
find "$BACKUP_DIR" -name "*.gz" -mtime +7 \
  | grep -vE "($SUNDAYS)" \
  | xargs -r rm -f

# Step 3: delete Sunday files older than 28 days
find "$BACKUP_DIR" -name "*.gz" -mtime +28 | xargs -r rm -f

echo "Backup complete: $DATE"
```

### Schedule

Add to `ofelia.ini`:

```ini
[job-exec "daily-backup"]
schedule  = 0 2 * * *
container = backup
command   = bash /backup.sh
```

Runs at `02:00` daily — 1 hour before Watchtower's `03:00` update window.

### Retention policy

- **7 daily backups** kept at all times
- **4 weekly backups** (Sunday snapshots) kept for 28 days
- Backup script prunes older files automatically (two-pass: daily first, then weekly)

### Out-of-band: `.env` backup

The `.env` file is never committed to git. It must be stored separately:

- Save a copy in a password manager (1Password, Bitwarden, etc.) after any change
- Use `.env.example` (committed to git) as the canonical list of required keys

---

## Pillar 2: GitOps Version Control

### The golden rule

> **No direct file edits on the VPS.** All changes flow through git.

The VPS is always downstream of `git@github.com:relder251/sa.git` (master branch). Changes are authored locally, committed, pushed, then applied to the VPS via `deploy.sh`.

### What is tracked in git

Everything except:

| Excluded | Reason |
|---|---|
| `.env` | Secrets — never committed |
| `certbot/` SSL certs | Auto-managed by certbot; stored at `/opt/sovereignadvisory/ssl` on host |
| `output/`, `backup/` | Runtime data — backed up separately |
| `deerflow/` | Nested git repo |
| `.claude/`, `.superpowers/` | Tool data |

### Day-to-day change workflow

| Change type | Steps |
|---|---|
| Code / config edit | Edit locally → commit → `git push` → `ssh vps 'bash ~/sa/scripts/deploy.sh'` |
| LiteLLM model config | Edit `litellm_config.yaml` → commit → deploy → `docker compose restart litellm` |
| n8n workflow | Export JSON from n8n UI → save to `./workflows/` → commit → deploy → `docker exec n8n n8n import:workflow --input=/data/workflows/<file>.json` |
| Secrets (`.env`) | Edit directly on VPS only — never committed |
| New service | Add to `docker-compose.yml` → commit → deploy |

### Deploy helper — `scripts/deploy.sh`

```bash
#!/bin/bash
set -euo pipefail
cd ~/sa
git pull
docker compose up -d --build
echo "Deploy complete."
```

Run from local machine:
```bash
ssh vps 'bash ~/sa/scripts/deploy.sh'
```

---

## Pillar 3: Rollback

### A. Config rollback (most common)

Bad edit to `docker-compose.yml`, `litellm_config.yaml`, nginx config, etc.

```bash
# Option 1: revert the commit
git revert <bad-sha>
git push
ssh vps 'bash ~/sa/scripts/deploy.sh'

# Option 2: restore a single file to a known-good state
git checkout <good-sha> -- docker-compose.yml
git commit -m "fix: revert docker-compose to known-good state"
git push
ssh vps 'bash ~/sa/scripts/deploy.sh'
```

### B. Container image rollback

Watchtower auto-updates labelled containers nightly. If a pulled image breaks a service:

```bash
# 1. Find the last known-good image digest (run on VPS before restarting)
docker inspect --format='{{index .RepoDigests 0}}' n8n

# 2. Pin the digest in docker-compose.yml
#    Change:  image: docker.n8n.io/n8nio/n8n:latest
#    To:      image: docker.n8n.io/n8nio/n8n@sha256:<digest>

git commit -m "fix: pin n8n to last known-good digest"
git push
ssh vps 'bash ~/sa/scripts/deploy.sh'
```

**Watchtower-managed services** (those with `com.centurylinklabs.watchtower.enable=true`):
`postgres`, `n8n`, `litellm`, `ollama`, `jupyter`, `webui`, `pipeline-server`

Note: `keycloak` is intentionally excluded from Watchtower — major version upgrades require DB migration and must be done manually.

### C. Database rollback

For broken migrations or data corruption:

```bash
# 1. Stop services that use the database
ssh vps 'cd ~/sa && docker compose stop n8n litellm lead-review keycloak'

# 2. Restore from backup
# Must connect to the 'postgres' maintenance database so pg_dumpall's
# \connect directives (which switch between litellm/n8n/keycloak) work correctly.
ssh vps 'gunzip -c ~/sa/backup/postgres_YYYY-MM-DD.sql.gz \
  | docker exec -i litellm_db psql -U litellm -d postgres'

# 3. Restart services
ssh vps 'cd ~/sa && docker compose start n8n litellm lead-review keycloak'
```

### Full disaster recovery (VPS total loss)

Estimated recovery time: **~15 minutes** (excluding Ollama model re-pull, and assuming backups are available — see off-site gap note under Pillar 1).

```bash
# 1. Provision fresh VPS with Docker installed

# 2. Clone the repo
git clone git@github.com:relder251/sa.git && cd sa

# 3. Restore .env from password manager

# 4. Retrieve latest backups from off-site storage into ./backup/
#    (rclone, scp, or manual download — requires off-site sync to be configured)

# 5. Bring the stack up
docker compose up -d

# 6. Restore the database from latest backup
# Connect to maintenance DB so \connect directives in pg_dumpall output work
gunzip -c backup/postgres_<date>.sql.gz \
  | docker exec -i litellm_db psql -U litellm -d postgres

# 7. (Optional) Restore output and opportunities data
tar -xzf backup/output_<date>.tar.gz
tar -xzf backup/opportunities_<date>.tar.gz

# 8. Restore SSL certs (or let certbot re-issue — slower but free)
mkdir -p /opt/sovereignadvisory
tar -xzf backup/ssl_<date>.tar.gz -C /opt/sovereignadvisory/

# 9. Re-enter n8n credentials in the n8n UI (known gap — not backed up)

# 10. Re-pull Ollama models as needed
docker exec ollama ollama pull <model>
```

---

## Known Gaps (v1)

| Gap | Impact | Mitigation |
|---|---|---|
| No off-site backup transfer | Total VPS loss = backup loss | Future: add rclone/rsync job to push `./backup/` to B2/S3 nightly |
| `n8n_data` not backed up | n8n credentials lost on DR | Future: add `n8n_data` tar to backup script; for now, re-enter manually |

---

## Implementation Checklist

When implementing (task #6+):

- [ ] Create `scripts/backup.sh`
- [ ] Add `backup` service to `docker-compose.yml`
- [ ] Add `[job-exec "daily-backup"]` to `ofelia.ini`
- [ ] Create `scripts/deploy.sh`
- [ ] Remove `notebooks/` from `.gitignore`, delete `notebooks/.git` *(done — 2026-03-18)*
- [ ] Commit all changes and push to `github.com/relder251/sa`
- [ ] Deploy to VPS and run a manual backup dry-run to verify
