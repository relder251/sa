# Infrastructure Resilience Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement automated backups, a GitOps deploy script, and a full test suite (automated restore validation + Playwright browser regression) for the sovereignadvisory.ai Docker stack.

**Architecture:** A new `backup` Docker service runs `pg_dumpall` + `tar` snapshots daily via Ofelia. A `deploy.sh` script standardises VPS deploys. Automated tests validate backup integrity before any deploy reaches production; Playwright regression tests confirm no service disruption after deploy. A manual DR drill checklist documents the full-loss recovery procedure.

**Tech Stack:** Bash, PostgreSQL 15 (`pg_dumpall`), Docker Compose, Ofelia (cron), Python + Playwright (browser tests), pytest.

---

## Deployment pattern

Deploy = `git push` then `ssh vps 'bash ~/sa/scripts/deploy.sh'`.

Never `docker rm` running containers — use `docker compose up -d` (recreates only changed services) or `docker restart <name>`.

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `scripts/backup.sh` | **Create** | Run pg_dumpall + tar snapshots; `--dry-run` mode prints commands without executing |
| `scripts/backup_test.sh` | **Create** | Spin up throwaway postgres, restore from a dump, validate key tables, clean up |
| `scripts/deploy.sh` | **Create** | `git pull` + `docker compose up -d --build` on the VPS |
| `docker-compose.yml` | **Modify** | Add `backup` service (postgres:15 image, `sleep infinity`, proper volumes) |
| `ofelia.ini` | **Modify** | Add `[job-exec "daily-backup"]` at 02:00; add 1-min test job during validation |
| `scripts/smoke_test.sh` | **Modify** | Add backup container health check section |
| `tests/test_post_deploy.py` | **Create** | Playwright regression: lead review login + dashboard, n8n loads, webui loads |
| `tests/conftest.py` | **Create** | Playwright base URL fixture (reads `BASE_URL` env var, defaults to localhost) |
| `tests/requirements.txt` | **Create** | `playwright`, `pytest-playwright` pinned versions |
| `docs/runbooks/dr-drill-checklist.md` | **Create** | Step-by-step manual DR drill procedure |

---

## Task 1: `scripts/backup.sh` with `--dry-run`

**Files:**
- Create: `scripts/backup.sh`

- [ ] **Step 1: Write the script**

```bash
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
echo "--- postgres ---"
run bash -c "pg_dumpall | gzip > \"$BACKUP_DIR/postgres_${DATE}.sql.gz\""

# --- Output directory ---
echo "--- output ---"
run tar -czf "$BACKUP_DIR/output_${DATE}.tar.gz" -C /data output

# --- Opportunities directory ---
echo "--- opportunities ---"
run tar -czf "$BACKUP_DIR/opportunities_${DATE}.tar.gz" -C /data opportunities

# --- SSL certificates ---
echo "--- ssl ---"
run tar -czf "$BACKUP_DIR/ssl_${DATE}.tar.gz" -C / ssl

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
find "$BACKUP_DIR" -name "*.gz" -mtime +7 \
  | grep -vE "($SUNDAYS)" \
  | xargs -r rm -f

# Pass 2: delete Sunday files older than 28 days
find "$BACKUP_DIR" -name "*.gz" -mtime +28 | xargs -r rm -f

echo "=== Backup complete: $DATE ==="
```

- [ ] **Step 2: Make it executable**

```bash
chmod +x scripts/backup.sh
```

- [ ] **Step 3: Dry-run test locally (no Docker needed — runs from host)**

This tests the script logic without touching any data. `BACKUP_DIR` is overridden to `/tmp/backup-dry-test` so even if `run` accidentally executes something it won't affect `./backup/`.

```bash
BACKUP_DIR=/tmp/backup-dry-test bash scripts/backup.sh --dry-run
```

Expected output:
```
=== Backup starting: 2026-XX-XX (dry-run: true) ===
--- postgres ---
[dry-run] bash -c pg_dumpall | gzip > "/tmp/backup-dry-test/postgres_2026-XX-XX.sql.gz"
--- output ---
[dry-run] tar -czf /tmp/backup-dry-test/output_2026-XX-XX.tar.gz -C /data output
--- opportunities ---
[dry-run] tar -czf /tmp/backup-dry-test/opportunities_2026-XX-XX.tar.gz -C /data opportunities
--- ssl ---
[dry-run] tar -czf /tmp/backup-dry-test/ssl_2026-XX-XX.tar.gz -C / ssl
=== Dry-run complete — no files written ===
```

Verify no files were created: `ls /tmp/backup-dry-test/ 2>/dev/null || echo "empty (correct)"`

- [ ] **Step 4: Commit**

```bash
git add scripts/backup.sh
git commit -m "feat: add backup.sh with --dry-run flag"
```

---

## Task 2: `scripts/backup_test.sh` — restore validation

**Files:**
- Create: `scripts/backup_test.sh`

This script is the automated test for backup integrity. It spins up a fresh throwaway postgres container, restores a dump, validates key tables, and cleans up. Run it after every backup to confirm the dump is actually restorable.

- [ ] **Step 1: Write the script**

```bash
#!/usr/bin/env bash
# backup_test.sh — validates a postgres backup dump can be restored
# Usage: bash scripts/backup_test.sh <path-to-postgres_YYYY-MM-DD.sql.gz>
#
# Creates a throwaway postgres:15 container, restores the dump,
# queries key tables across all three databases, reports pass/fail,
# and always cleans up the throwaway container on exit.

set -euo pipefail

DUMP_FILE="${1:?Usage: backup_test.sh <path-to-postgres_YYYY-MM-DD.sql.gz>}"

if [ ! -f "$DUMP_FILE" ]; then
  echo "❌ Dump file not found: $DUMP_FILE"
  exit 1
fi

CONTAINER="pg_restore_test_$$"
PG_USER="litellm"
PG_PASS="litellm_password"
PASS=0
FAIL=0

green='\033[0;32m'
red='\033[0;31m'
reset='\033[0m'

cleanup() {
  echo ""
  echo "Cleaning up $CONTAINER..."
  docker rm -f "$CONTAINER" > /dev/null 2>&1 || true
}
trap cleanup EXIT

echo ""
echo "╔══════════════════════════════════════════════╗"
echo "║       Backup Restore Validation              ║"
echo "╚══════════════════════════════════════════════╝"
echo "Dump: $DUMP_FILE"
echo ""

# Start throwaway postgres
echo "Starting throwaway postgres container..."
docker run -d --name "$CONTAINER" \
  -e POSTGRES_USER="$PG_USER" \
  -e POSTGRES_PASSWORD="$PG_PASS" \
  -e POSTGRES_DB="postgres" \
  postgres:15 > /dev/null

# Wait for it to be ready (up to 30s)
echo "Waiting for postgres to be ready..."
for i in $(seq 1 30); do
  docker exec "$CONTAINER" pg_isready -U "$PG_USER" > /dev/null 2>&1 && break
  sleep 1
done
docker exec "$CONTAINER" pg_isready -U "$PG_USER" > /dev/null 2>&1 \
  || { echo "❌ Postgres did not become ready in 30s"; exit 1; }

# Restore from dump
echo "Restoring from dump..."
# Connect to maintenance DB so pg_dumpall's \connect directives work
# Pass PGPASSWORD into the container via -e (env var set on local shell is NOT
# visible inside docker exec — must be passed explicitly with -e)
gunzip -c "$DUMP_FILE" \
  | docker exec -i -e PGPASSWORD="$PG_PASS" "$CONTAINER" psql -U "$PG_USER" -d postgres -q

echo ""
echo "── Validating tables ────────────────────────────"

check() {
  local label="$1"
  local db="$2"
  local query="$3"
  local result
  # Must pass PGPASSWORD via -e — env var prefixing does not forward into docker exec
  result=$(docker exec -e PGPASSWORD="$PG_PASS" "$CONTAINER" \
    psql -U "$PG_USER" -d "$db" -t -c "$query" 2>/dev/null | tr -d '[:space:]')
  if [ -n "$result" ] && [ "$result" != "0" ]; then
    echo -e "  ${green}✅ PASS${reset}  $label: $result"
    PASS=$((PASS + 1))
  else
    echo -e "  ${red}❌ FAIL${reset}  $label: got '$result'"
    FAIL=$((FAIL + 1))
  fi
}

# litellm database
check "sa_leads table exists" "litellm" \
  "SELECT count(*) FROM information_schema.tables WHERE table_name='sa_leads'"
check "sa_leads row count > 0" "litellm" \
  "SELECT count(*) FROM sa_leads"
check "litellm_verificationtoken table" "litellm" \
  "SELECT count(*) FROM information_schema.tables WHERE table_name='litellm_verificationtoken'"

# n8n database
check "n8n workflow_entity table exists" "n8n" \
  "SELECT count(*) FROM information_schema.tables WHERE table_name='workflow_entity'"

# keycloak database — optional check (keycloak DB may not exist if never initialised)
# CREATE ROLE errors during pg_dumpall restore are expected and non-fatal
keycloak_exists=$(docker exec -e PGPASSWORD="$PG_PASS" "$CONTAINER" \
  psql -U "$PG_USER" -d postgres -t -c \
  "SELECT count(*) FROM pg_database WHERE datname='keycloak'" 2>/dev/null | tr -d '[:space:]')
if [ "$keycloak_exists" = "1" ]; then
  check "keycloak realm table exists" "keycloak" \
    "SELECT count(*) FROM information_schema.tables WHERE table_name='realm'"
else
  echo "  ℹ SKIP   keycloak database not present in this dump (not an error)"
fi

echo ""
echo "════════════════════════════════════════════════"
TOTAL=$((PASS + FAIL))
if [ "$FAIL" -eq 0 ]; then
  echo -e "${green}✅ ALL $TOTAL CHECKS PASSED — dump is valid${reset}"
  exit 0
else
  echo -e "${red}❌ $FAIL/$TOTAL CHECKS FAILED — dump may be corrupt${reset}"
  exit 1
fi
```

- [ ] **Step 2: Make it executable**

```bash
chmod +x scripts/backup_test.sh
```

- [ ] **Step 3: Run it against a live local backup to verify it works**

First bring up local postgres and run the backup container to generate a real dump:

```bash
# Bring up postgres + backup locally
docker compose up -d postgres backup

# Wait for postgres to be healthy
docker compose ps postgres  # should show "(healthy)"

# Run the backup manually inside the backup container
docker exec backup bash /backup.sh

# Verify dump was created
ls -lh ./backup/postgres_*.sql.gz
```

Expected: `./backup/postgres_<today>.sql.gz` exists and is > 0 bytes.

Now validate it:

```bash
bash scripts/backup_test.sh ./backup/postgres_$(date +%Y-%m-%d).sql.gz
```

Expected output: all checks PASS, exit 0.

- [ ] **Step 4: Commit**

```bash
git add scripts/backup_test.sh
git commit -m "feat: add backup_test.sh restore validation script"
```

---

## Task 3: Add `backup` service to `docker-compose.yml`

**Files:**
- Modify: `docker-compose.yml` — add backup service after the `free-model-sync` service block

- [ ] **Step 1: Add the backup service**

Add the following block to `docker-compose.yml`, after the `free-model-sync` service and before the `test-runner` service:

```yaml
  # -----------------------------------------
  # Backup — daily pg_dumpall + tar snapshots
  # Stays alive via sleep infinity so Ofelia
  # can job-exec into it on schedule.
  # Output: ./backup/postgres_YYYY-MM-DD.sql.gz
  #         ./backup/output_YYYY-MM-DD.tar.gz
  #         ./backup/opportunities_YYYY-MM-DD.tar.gz
  #         ./backup/ssl_YYYY-MM-DD.tar.gz
  # -----------------------------------------
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
      # litellm is the bootstrap superuser (POSTGRES_USER in docker-compose).
      # pg_dumpall requires superuser — do not replace with an app-scoped role.
      - PGUSER=${LITELLM_USER:-litellm}
      - PGPASSWORD=${LITELLM_PASSWORD:-litellm_password}
    command: ["sleep", "infinity"]
    networks:
      - vibe_net
    depends_on:
      postgres:
        condition: service_healthy
    restart: unless-stopped
    # No watchtower label — postgres:15 is pinned; update manually
```

- [ ] **Step 2: Start the backup container locally and verify it stays alive**

```bash
docker compose up -d backup
sleep 3
docker ps --filter name=backup --format "{{.Names}} {{.Status}}"
```

Expected: `backup Up X seconds` (not `Exited`).

- [ ] **Step 3: Confirm pg_dumpall can connect**

```bash
docker exec backup bash -c "pg_dumpall --globals-only | head -5"
```

Expected: SQL output starting with `-- PostgreSQL database cluster dump` (not an auth error).

- [ ] **Step 4: Verify backup.sh produces valid output files**

```bash
docker exec backup bash /backup.sh
ls -lh ./backup/
```

Expected: four `.gz` files dated today, each > 0 bytes.

- [ ] **Step 5: Run restore validation against the new dump**

```bash
bash scripts/backup_test.sh ./backup/postgres_$(date +%Y-%m-%d).sql.gz
```

Expected: all PASS.

- [ ] **Step 6: Ensure `backup/` is in `.gitignore`**

The `./backup/` directory holds pg dumps (large + potentially sensitive). Confirm it is gitignored:

```bash
grep "^backup/" .gitignore || echo "backup/" >> .gitignore
```

- [ ] **Step 7: Commit**

```bash
git add docker-compose.yml .gitignore
git commit -m "feat: add backup service to docker-compose"
```

---

## Task 4: `ofelia.ini` — add daily backup job

**Files:**
- Modify: `ofelia.ini`

- [ ] **Step 1: Add a 1-minute test job to verify Ofelia can exec into the backup container**

```ini
# ofelia.ini — Cron schedule for free model sync
# Ofelia runs jobs inside existing containers via docker exec.
# Docs: https://github.com/mcuadros/ofelia

[job-exec "free-model-sync"]
schedule  = @every 6h
container = free_model_sync
command   = python /app/free_model_sync.py

[job-exec "backup-test-1min"]
schedule  = @every 1m
container = backup
command   = bash /backup.sh --dry-run

# NOTE: Ofelia uses 6-field cron (second minute hour day month weekday),
# not the standard 5-field format. @every macros work as-is.
```

- [ ] **Step 2: Restart Ofelia and watch logs for the test job firing**

```bash
docker compose up -d ofelia
sleep 70   # wait for first 1-minute trigger
docker compose logs --tail=20 ofelia
```

Expected: log line containing `backup-test-1min` and `dry-run: true` (no errors).

- [ ] **Step 3: Replace test job with the real daily schedule**

```ini
# ofelia.ini — Cron schedule for free model sync and daily backup
# Ofelia runs jobs inside existing containers via docker exec.
# Docs: https://github.com/mcuadros/ofelia

[job-exec "free-model-sync"]
schedule  = @every 6h
container = free_model_sync
command   = python /app/free_model_sync.py

[job-exec "daily-backup"]
# Ofelia uses 6-field cron: second minute hour day month weekday
# 0 0 2 * * * = at 02:00:00 every day
schedule  = 0 0 2 * * *
container = backup
command   = bash /backup.sh
```

- [ ] **Step 4: Restart Ofelia with final config**

```bash
docker compose up -d ofelia
docker compose logs --tail=10 ofelia
```

Expected: Ofelia starts cleanly, schedules shown for both jobs, no errors.

- [ ] **Step 5: Commit**

```bash
git add ofelia.ini
git commit -m "feat: add daily-backup job to ofelia schedule"
```

---

## Task 5: `scripts/deploy.sh` — GitOps deploy helper

**Files:**
- Create: `scripts/deploy.sh`

- [ ] **Step 1: Write the script**

```bash
#!/usr/bin/env bash
# deploy.sh — pull latest git changes and bring the stack up-to-date
# Usage (from local machine): ssh vps 'bash ~/sa/scripts/deploy.sh'
# Usage (on VPS directly):    bash ~/sa/scripts/deploy.sh
#
# What it does:
#   1. git pull (fails fast if there are local uncommitted changes)
#   2. docker compose up -d --build (recreates only changed services)
#
# What it does NOT do:
#   - rm or stop containers (use docker compose stop <service> manually if needed)
#   - run migrations (do those manually before deploying)
#   - update .env (edit on the VPS directly, never committed)

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"

echo "=== Deploy starting from $REPO_DIR ==="
echo "--- git pull ---"
git pull

echo "--- docker compose up -d --build ---"
docker compose up -d --build

echo "=== Deploy complete ==="
```

- [ ] **Step 2: Make it executable**

```bash
chmod +x scripts/deploy.sh
```

- [ ] **Step 3: Test locally (runs git pull + docker compose up against local stack)**

```bash
bash scripts/deploy.sh
```

Expected: `git pull` reports `Already up to date.` (or pulls new commits), then `docker compose up -d --build` runs. No services should restart unless their config or image changed.

- [ ] **Step 4: Verify no running containers were unnecessarily restarted**

```bash
docker ps --format "{{.Names}} {{.Status}}"
```

Confirm all services still show `Up` with expected uptimes (none reset to `Up X seconds` unless they had config changes).

- [ ] **Step 5: Commit**

```bash
git add scripts/deploy.sh
git commit -m "feat: add deploy.sh GitOps deploy helper"
```

---

## Task 6: Playwright post-deploy regression tests

**Files:**
- Create: `tests/conftest.py`
- Create: `tests/test_post_deploy.py`
- Create: `tests/requirements.txt`

These tests run after every deploy to confirm existing services weren't disrupted. They cover lead review (login + dashboard), n8n (loads), and webui (loads). Set `BASE_URL` to override the default `http://localhost` for production runs.

- [ ] **Step 1: Create `tests/requirements.txt`**

```
pytest==8.1.1
pytest-playwright==0.4.4
playwright==1.44.0
```

- [ ] **Step 2: Create `tests/conftest.py`**

```python
import os
import pytest
from playwright.sync_api import Page, sync_playwright


BASE_URL = os.environ.get("BASE_URL", "http://localhost")
LEAD_REVIEW_PASSWORD = os.environ.get("LEAD_REVIEW_PASSWORD", "")


@pytest.fixture(scope="session")
def base_url() -> str:
    return BASE_URL.rstrip("/")


@pytest.fixture(scope="session")
def lead_review_password() -> str:
    return LEAD_REVIEW_PASSWORD
```

- [ ] **Step 3: Create `tests/test_post_deploy.py`**

Uses pytest-playwright's injected `page` fixture (one browser process, reused across tests in the session).

```python
"""
Post-deploy regression tests.
Confirms no services were disrupted after deploying infrastructure changes.

Run locally:
    pip install -r tests/requirements.txt
    playwright install chromium
    pytest tests/test_post_deploy.py -v

Run against production:
    BASE_URL=https://sovereignadvisory.ai \
    LEAD_REVIEW_PASSWORD=<from .env> \
    pytest tests/test_post_deploy.py -v
"""

import shutil
import subprocess
import pytest
from playwright.sync_api import Page, expect


# ── Lead Review Portal ────────────────────────────────────────────────────────

def test_lead_review_login_page_renders(page: Page, base_url: str) -> None:
    """Login page must load and show the password form or dashboard."""
    page.goto(f"{base_url}:5003/review", timeout=15000)
    assert (
        page.locator("input[type='password']").count() > 0
        or page.locator("#dashboard-screen").count() > 0
    ), "Lead review page did not render login form or dashboard"


def test_lead_review_dashboard_loads(page: Page, base_url: str, lead_review_password: str) -> None:
    """After login, the lead dashboard table must be visible."""
    if not lead_review_password:
        pytest.skip("LEAD_REVIEW_PASSWORD not set — skipping authenticated test")

    page.goto(f"{base_url}:5003/review", timeout=15000)
    pwd_input = page.locator("input[type='password']")
    if pwd_input.count() > 0:
        pwd_input.fill(lead_review_password)
        page.locator("button[type='submit']").click()
        page.wait_for_selector("#dashboard-screen", timeout=10000)

    expect(page.locator("#leads-table, table")).to_be_visible(timeout=10000)


# ── n8n ───────────────────────────────────────────────────────────────────────

def test_n8n_loads(page: Page, base_url: str) -> None:
    """n8n UI must respond with a non-error status."""
    response = page.goto(f"{base_url}:5678", timeout=15000)
    assert response is not None and response.status < 400, \
        f"n8n returned unexpected status {response.status if response else 'None'}"


def test_n8n_health_endpoint(page: Page, base_url: str) -> None:
    """n8n /healthz must return 200."""
    response = page.goto(f"{base_url}:5678/healthz", timeout=10000)
    assert response is not None and response.status == 200, \
        f"n8n /healthz returned {response.status if response else 'None'}"


# ── Web UI ────────────────────────────────────────────────────────────────────

def test_webui_loads(page: Page, base_url: str) -> None:
    """Web UI homepage must load and contain 'Pipeline' text."""
    page.goto(f"{base_url}:3000", timeout=15000)
    expect(page.get_by_text("Pipeline")).to_be_visible(timeout=10000)


def test_webui_health(page: Page, base_url: str) -> None:
    """Web UI /health must return 200."""
    response = page.goto(f"{base_url}:3000/health", timeout=10000)
    assert response is not None and response.status == 200, \
        f"webui /health returned {response.status if response else 'None'}"


# ── Backup container ──────────────────────────────────────────────────────────

def test_backup_container_is_running() -> None:
    """Backup container must be running (not exited).
    Skipped automatically if docker CLI is not available (e.g. remote CI without socket).
    """
    if not shutil.which("docker"):
        pytest.skip("docker CLI not available — skipping container check")

    result = subprocess.run(
        ["docker", "inspect", "--format", "{{.State.Status}}", "backup"],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        pytest.skip("backup container not found — may not be deployed yet")

    status = result.stdout.strip()
    assert status == "running", f"backup container status is '{status}', expected 'running'"
```

- [ ] **Step 4: Install dependencies and run tests locally**

```bash
pip install -r tests/requirements.txt
playwright install chromium
```

```bash
# Ensure local stack is up
docker compose up -d

# Run tests
pytest tests/test_post_deploy.py -v
```

Expected: all tests PASS. Any failures indicate a service is not running properly.

- [ ] **Step 5: Commit**

```bash
git add tests/
git commit -m "test: add Playwright post-deploy regression tests"
```

---

## Task 7: Update `smoke_test.sh` with backup container health check

**Files:**
- Modify: `scripts/smoke_test.sh`

Add a backup section after the existing `── Infrastructure ──` block.

- [ ] **Step 1: Add backup health check to `smoke_test.sh`**

After the `check "webui health" ...` line, add:

```bash
# ── Backup service ────────────────────────────────────────────────────────────
echo ""
echo "── Backup service ──────────────────────────────"

backup_status=$(docker inspect --format '{{.State.Status}}' backup 2>/dev/null || echo "not_found")
if [ "$backup_status" = "running" ]; then
  echo -e "  ${green}✅ PASS${reset}  backup container is running"
  PASS=$((PASS + 1))
else
  echo -e "  ${red}❌ FAIL${reset}  backup container status: $backup_status"
  ERRORS+=("backup container: expected running, got $backup_status")
  FAIL=$((FAIL + 1))
fi

# Verify most recent backup file exists and is < 25 hours old
latest_backup=$(find ./backup -name "postgres_*.sql.gz" -mtime -1 2>/dev/null | head -1)
if [ -n "$latest_backup" ]; then
  size=$(du -h "$latest_backup" | cut -f1)
  echo -e "  ${green}✅ PASS${reset}  latest postgres backup: $latest_backup ($size)"
  PASS=$((PASS + 1))
else
  echo -e "  ${yellow}⚠ WARN${reset}   no postgres backup from last 24h (expected after first Ofelia run)"
  # Not a hard failure — backup may not have run yet on first deploy
fi
```

- [ ] **Step 2: Run smoke test to confirm the new checks work**

```bash
bash scripts/smoke_test.sh
```

Expected: the `── Backup service ──` section appears and the backup container check passes.

- [ ] **Step 3: Commit**

```bash
git add scripts/smoke_test.sh
git commit -m "test: add backup container health check to smoke_test.sh"
```

---

## Task 8: Manual DR drill checklist

**Files:**
- Create: `docs/runbooks/dr-drill-checklist.md`

- [ ] **Step 1: Write the checklist**

```markdown
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
```

- [ ] **Step 2: Create the runbooks directory and commit**

```bash
mkdir -p docs/runbooks
git add docs/runbooks/dr-drill-checklist.md
git commit -m "docs: add DR drill checklist"
```

---

## Task 9: Push to GitHub + deploy to VPS + full validation

This task deploys everything to production and runs the complete validation suite.

- [ ] **Step 1: Push all commits to GitHub**

```bash
git push origin master
```

- [ ] **Step 2: Deploy to VPS**

```bash
ssh root@sovereignadvisory.ai 'bash ~/sa/scripts/deploy.sh'
```

Expected output ends with `=== Deploy complete ===`.

- [ ] **Step 3: Verify backup container is running on VPS**

```bash
ssh root@sovereignadvisory.ai 'docker ps --filter name=backup --format "{{.Names}} {{.Status}}"'
```

Expected: `backup Up X seconds`

- [ ] **Step 4: Run backup manually on VPS to generate first backup**

```bash
ssh root@sovereignadvisory.ai 'docker exec backup bash /backup.sh'
ssh root@sovereignadvisory.ai 'ls -lh ~/sa/backup/'
```

Expected: four `.gz` files from today.

- [ ] **Step 5: Copy backup to local machine and run restore validation**

```bash
scp root@sovereignadvisory.ai:~/sa/backup/postgres_$(date +%Y-%m-%d).sql.gz ./backup/
bash scripts/backup_test.sh ./backup/postgres_$(date +%Y-%m-%d).sql.gz
```

Expected: all checks PASS.

- [ ] **Step 6: Run smoke test on VPS**

```bash
ssh root@sovereignadvisory.ai 'cd ~/sa && bash scripts/smoke_test.sh'
```

Expected: all checks PASS (including backup container check).

- [ ] **Step 7: Run Playwright regression tests against production**

```bash
LEAD_REVIEW_PASSWORD=$(grep LEAD_REVIEW_PASSWORD .env | cut -d= -f2) \
BASE_URL=https://sovereignadvisory.ai \
pytest tests/test_post_deploy.py -v
```

Expected: all tests PASS.

- [ ] **Step 8: Verify Ofelia job will fire at 02:00**

```bash
ssh root@sovereignadvisory.ai 'docker compose -f ~/sa/docker-compose.yml logs --tail=20 ofelia'
```

Expected: Ofelia logs show `daily-backup` scheduled, no errors.

- [ ] **Step 9: Final commit (if any fixup needed) and confirm clean state**

```bash
git status  # should be clean
git log --oneline -5
```

---

## Validation Summary

| Test | When | Command |
|---|---|---|
| Dry-run logic | Anytime | `bash scripts/backup.sh --dry-run` |
| Backup creates valid files | After backup runs | `ls -lh ./backup/` |
| Restore validation | After any backup | `bash scripts/backup_test.sh <dump.sql.gz>` |
| Smoke test (all services) | After any deploy | `bash scripts/smoke_test.sh` |
| Playwright regression | After any deploy | `pytest tests/test_post_deploy.py -v` |
| DR drill | Quarterly | See `docs/runbooks/dr-drill-checklist.md` |
