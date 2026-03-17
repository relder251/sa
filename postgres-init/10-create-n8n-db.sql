-- Create the n8n database if it doesn't exist.
-- This runs automatically on first Postgres container start.
SELECT 'CREATE DATABASE n8n'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'n8n')\gexec
