-- ============================================================
-- 99-migrations.sql — Idempotent schema migration tracking.
-- Safe to re-run on any existing deployment.
-- HOW TO ADD: add DO block + INSERT below, increment version,
-- document in docs/SCHEMA_CHANGELOG.md
-- EXISTING DEPLOYMENTS: docker exec -i litellm_db psql -U litellm < postgres-init/99-migrations.sql
-- ============================================================

CREATE TABLE IF NOT EXISTS schema_migrations (
    version     INT PRIMARY KEY,
    description TEXT NOT NULL,
    applied_at  TIMESTAMPTZ DEFAULT NOW()
);

INSERT INTO schema_migrations (version, description)
VALUES (1, 'Initial SA schema')
ON CONFLICT (version) DO NOTHING;
