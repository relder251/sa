# Schema Changelog

Migration versions are tracked in `postgres-init/99-migrations.sql`.

## Process
1. Add idempotent `DO $$ ... END $$;` block to `99-migrations.sql`
2. Add `INSERT INTO schema_migrations (version, description) VALUES (N, '...') ON CONFLICT DO NOTHING;`
3. Add section to this file
4. Existing deployments: `docker exec -i litellm_db psql -U litellm < postgres-init/99-migrations.sql`

## Versions

### v1 — Initial SA schema
**Applied:** 2026-03-20
**Changes:** Initial table creation via init scripts. No ALTERs needed.
