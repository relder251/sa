-- Create the keycloak database if it doesn't already exist.
-- Runs once at first postgres container start (docker-entrypoint-initdb.d ordering: 10→11→20).
SELECT 'CREATE DATABASE keycloak OWNER ' || current_user
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'keycloak')\gexec
