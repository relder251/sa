-- Mirror postgres init: create additional database for litellm-mirror
-- n8n_mirror is created automatically via POSTGRES_DB env var
SELECT 'CREATE DATABASE litellm_mirror OWNER mirror_user'
WHERE NOT EXISTS (
  SELECT FROM pg_database WHERE datname = 'litellm_mirror'
)\gexec
