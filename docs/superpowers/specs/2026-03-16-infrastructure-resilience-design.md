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
| `n8n_data` | n8n internal settings | **Skip** — covered by postgres + git | Low |
| Project files | All code and config | **Already in git** | n/a |

**Why `pg_dumpall` instead of `pg_dump`:** The single postgres instance hosts three separate databases (`litellm`, `n8n`, `keycloak`). `pg_dumpall` captures all three in a single operation, avoiding the need for separate jobs and ensuring a consistent point-in-time snapshot.

### Backup service implementation

Add a `backup` service to `docker-compose.yml`:

```yaml
backup:
  image: postgres:15
  container_name: backup
  volumes:
    - pg_data:/var/lib/postgresql/data:ro
    - ./backup:/backup
    - ./output:/data/output:ro
    - ./opportunities:/data/opportunities:ro
    - /opt/sovereignadvisory/ssl:/ssl:ro
    - ./scripts/backup.sh:/backup.sh:ro
  environment:
    - PGHOST=postgres
    - PGUSER=${LITELLM_USER:-litellm}
    - PGPASSWORD=${LITELLM_PASSWORD:-litellm_password}
  entrypoint: ["bash", "/backup.sh"]
  networks:
    - vibe_net
  depends_on:
    postgres:
      condition: service_healthy
  restart: "no"
```

### Backup script — `scripts/backup.sh`

```bash
#!/bin/bash
set -euo pipefail

DATE=$(date +%Y-%m-%d)
BACKUP_DIR=/backup

# --- Postgres (all databases) ---
pg_dumpall -h postgres -U "$PGUSER" | gzip > "$BACKUP_DIR/postgres_${DATE}.sql.gz"

# --- Output directory ---
tar -czf "$BACKUP_DIR/output_${DATE}.tar.gz" -C /data output

# --- Opportunities directory ---
tar -czf "$BACKUP_DIR/opportunities_${DATE}.tar.gz" -C /data opportunities

# --- SSL certificates ---
tar -czf "$BACKUP_DIR/ssl_${DATE}.tar.gz" -C / ssl

# --- Retention: keep 7 daily + 4 weekly ---
# Daily: delete files older than 7 days that are not a Sunday backup
find "$BACKUP_DIR" -name "*.gz" -mtime +7 ! -newer "$BACKUP_DIR" \
  | grep -v "$(date -d 'last sunday' +%Y-%m-%d)" | xargs -r rm -f

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
- Backup script prunes older files automatically

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
ssh vps 'gunzip -c ~/sa/backup/postgres_YYYY-MM-DD.sql.gz \
  | docker exec -i litellm_db psql -U litellm'

# 3. Restart services
ssh vps 'cd ~/sa && docker compose start n8n litellm lead-review keycloak'
```

### Full disaster recovery (VPS total loss)

Estimated recovery time: **~15 minutes** (excluding Ollama model re-pull).

```bash
# 1. Provision fresh VPS with Docker installed

# 2. Clone the repo
git clone git@github.com:relder251/sa.git && cd sa

# 3. Restore .env from password manager

# 4. Bring the stack up
docker compose up -d

# 5. Restore the database from latest backup
gunzip -c backup/postgres_<date>.sql.gz \
  | docker exec -i litellm_db psql -U litellm

# 6. (Optional) Restore output and opportunities data
tar -xzf backup/output_<date>.tar.gz
tar -xzf backup/opportunities_<date>.tar.gz

# 7. Restore SSL certs (or let certbot re-issue)
tar -xzf backup/ssl_<date>.tar.gz -C /opt/sovereignadvisory/

# 8. Re-pull Ollama models as needed
docker exec ollama ollama pull <model>
```

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
