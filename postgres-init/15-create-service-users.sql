-- ============================================================
-- 15-create-service-users.sql
-- Creates dedicated low-privilege DB users for n8n and Keycloak.
--
-- NOTE: This script runs automatically ONLY on fresh Postgres
-- volume initialization (docker-entrypoint-initdb.d/).
--
-- EXISTING DEPLOYMENTS: Run manually ONCE:
--   docker exec -i litellm_db psql -U litellm -f /docker-entrypoint-initdb.d/15-create-service-users.sql
-- Then restart n8n to pick up the new credentials:
--   docker compose -f docker-compose.prod.yml restart n8n
-- (Keycloak runs externally and must be reconfigured separately if needed.)
-- ============================================================

-- Create n8n dedicated user (idempotent)
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'n8n_user') THEN
    CREATE ROLE n8n_user LOGIN PASSWORD 'CHANGE_ME_n8n';
  END IF;
END $$;

-- Create keycloak dedicated user (idempotent)
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'keycloak_user') THEN
    CREATE ROLE keycloak_user LOGIN PASSWORD 'CHANGE_ME_keycloak';
  END IF;
END $$;

-- Grant database ownership (databases must already exist — created by earlier init scripts)
GRANT ALL PRIVILEGES ON DATABASE n8n TO n8n_user;
GRANT ALL PRIVILEGES ON DATABASE keycloak TO keycloak_user;

-- Grant schema and object access (required for PostgreSQL 15+)
\c n8n
GRANT ALL ON SCHEMA public TO n8n_user;
GRANT ALL ON ALL TABLES IN SCHEMA public TO n8n_user;
GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO n8n_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO n8n_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO n8n_user;

\c keycloak
GRANT ALL ON SCHEMA public TO keycloak_user;
GRANT ALL ON ALL TABLES IN SCHEMA public TO keycloak_user;
GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO keycloak_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO keycloak_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO keycloak_user;
