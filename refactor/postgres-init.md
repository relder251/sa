# Refactor: postgres-init/

**Date:** 2026-03-20
**Refactored by:** Claude Sonnet 4.6

---

## Directory Overview

| Property | Value |
|---|---|
| **Path** | `postgres-init/` |
| **Purpose** | SQL init scripts mounted at `/docker-entrypoint-initdb.d` — run once on first Postgres volume start, in filename order |
| **Execution order** | `10-create-n8n-db.sql` → `11-create-keycloak-db.sql` → `20-sa-schema.sql` |
| **Target container** | `litellm_db` (postgres:15, named volume `pg_data`) |
| **Superuser** | `${LITELLM_USER}` (default: `litellm`) — also owns n8n and keycloak databases |

### File inventory

| File | Purpose | Databases affected |
|---|---|---|
| `10-create-n8n-db.sql` | Creates `n8n` database if it doesn't exist | `n8n` |
| `11-create-keycloak-db.sql` | Creates `keycloak` database if it doesn't exist | `keycloak` |
| `20-sa-schema.sql` | Creates all `sa_*` tables, indexes, and pgcrypto extension for the lead pipeline | `litellm` (default `POSTGRES_DB`) |

---

## Gaps Found

| # | File | Gap | Severity | Description |
|---|---|---|---|---|
| 1 | `20-sa-schema.sql` | `archived` column missing from `sa_leads` | **Critical** | `lead_review_server.py` references `sa_leads.archived` in 8+ locations: SELECT column list, WHERE filter, UPDATE SET. Column was absent from the schema definition — any live lead query touching archived status would fail with `column "archived" does not exist`. |
| 2 | `20-sa-schema.sql` | Schema never applied to running database | **Critical** | `docker-entrypoint-initdb.d` scripts only execute on a fresh (empty) volume. The `pg_data` named volume was initialized before `20-sa-schema.sql` was added to the repo. The `sa_*` tables did not exist in any database — confirmed by `\dt sa_*` returning no rows across all three databases. `sa_lead_review` appeared healthy because its healthcheck is HTTP-only, not DB-connected. |
| 3 | `20-sa-schema.sql` | Misleading header comment | **Low** | Comment said "Run once: psql -U $POSTGRES_USER -d $POSTGRES_DB -f leads_schema.sql" — implied manual execution only. File lives in `docker-entrypoint-initdb.d`, which auto-runs on first start. Rewritten to describe both paths accurately. |
| 4 | `10-create-n8n-db.sql` | No `OWNER` clause | **Low** | `11-create-keycloak-db.sql` uses `CREATE DATABASE keycloak OWNER current_user`. `10-create-n8n-db.sql` omitted it — both DBs end up owned by the current user regardless, but the inconsistency is confusing. |
| 5 | All | No dedicated DB users for n8n/keycloak | **Info** | Both services connect as the `litellm` superuser. No privilege separation. Acceptable for homelab, deferred. |

---

## Changes Made

| Change | File | Before | After | Reason |
|---|---|---|---|---|
| Add `archived` column to `sa_leads` | `20-sa-schema.sql` | `archived` absent | `archived BOOLEAN DEFAULT FALSE` after `do_not_follow_up` | Required by `lead_review_server.py` — previously caused silent DB failure |
| Add archived index | `20-sa-schema.sql` | No index | `idx_sa_leads_archived ON sa_leads(archived) WHERE archived = TRUE` | Consistent with other partial indexes; archive queries filter on this column |
| Rewrite header comment | `20-sa-schema.sql` | "Run once: psql ... -f leads_schema.sql" | Describes both init.d auto-run and manual re-run command, idempotency guarantee | Eliminates confusion about when/how the file runs |
| Add `OWNER current_user` | `10-create-n8n-db.sql` | `CREATE DATABASE n8n` | `CREATE DATABASE n8n OWNER current_user` | Consistent with `11-create-keycloak-db.sql` |
| Apply schema to running DB | (live operation) | `sa_*` tables absent | All 4 tables + 9 indexes created in `litellm` DB | Init scripts don't re-run on existing volume |

---

## Test Results

| Check | Result |
|---|---|
| `\dt sa_*` in litellm DB | ✅ `sa_leads`, `sa_lead_drafts`, `sa_review_tokens`, `sa_email_threads` |
| `archived` column present in `sa_leads` | ✅ `archived boolean DEFAULT false` |
| `idx_sa_leads_archived` index present | ✅ |
| pgcrypto extension active | ✅ (already existed, `IF NOT EXISTS` skipped cleanly) |
| `lead_review_server` DB connectivity | ✅ `asyncpg` pool connected, `SELECT COUNT(*) FROM sa_leads` returned 0 |
| `lead_review_server` column access | ✅ All 29 columns present including `archived` |
| Schema re-run idempotency | ✅ `CREATE TABLE IF NOT EXISTS` — safe to re-run |
| n8n DB operational | ✅ 7+ tables present, n8n container healthy |
| keycloak DB operational | ✅ keycloak container healthy |

---

## Deferred Items

| Item | Notes |
|---|---|
| Dedicated DB users for n8n and keycloak | Both connect as litellm superuser — no privilege separation. Low risk for homelab; add `CREATE USER n8n_user` etc. if multi-tenant posture required |
| Schema migration strategy | Currently no migration tooling — schema changes require manual `ALTER TABLE` on existing installations. Consider adding a `99-migrations.sql` or Flyway/Liquibase if schema evolves frequently |
