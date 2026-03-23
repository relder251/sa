-- Migration: 001_agent_state_schema
-- Database: n8n (in litellm_db PostgreSQL container)
-- Purpose: Add agent_state schema for logging Claude dispatch runs
-- Run as: litellm (superuser) against the n8n database
--
-- Apply:
--   docker exec litellm_db psql -U litellm -d n8n -f /path/to/001_agent_state_schema.sql

-- ============================================================
-- Schema
-- ============================================================

CREATE SCHEMA IF NOT EXISTS agent_state;

-- ============================================================
-- Tables
-- ============================================================

CREATE TABLE IF NOT EXISTS agent_state.agent_runs (
  run_id         UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  workflow_id    TEXT        NOT NULL,
  task_id        TEXT,
  notion_page_id TEXT,
  status         TEXT        NOT NULL DEFAULT 'running',
  prompt         TEXT,
  started_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  completed_at   TIMESTAMPTZ,
  exit_code      INTEGER,
  duration_ms    INTEGER
);

CREATE TABLE IF NOT EXISTS agent_state.agent_logs (
  id      BIGSERIAL   PRIMARY KEY,
  run_id  UUID        REFERENCES agent_state.agent_runs(run_id) ON DELETE CASCADE,
  ts      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  level   TEXT        NOT NULL DEFAULT 'info',
  message TEXT        NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_state.agent_artifacts (
  id           BIGSERIAL   PRIMARY KEY,
  run_id       UUID        REFERENCES agent_state.agent_runs(run_id) ON DELETE CASCADE,
  filename     TEXT        NOT NULL,
  content_type TEXT        NOT NULL DEFAULT 'text/plain',
  content      TEXT,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ============================================================
-- Indexes
-- ============================================================

CREATE INDEX IF NOT EXISTS idx_agent_runs_workflow ON agent_state.agent_runs(workflow_id);
CREATE INDEX IF NOT EXISTS idx_agent_runs_notion   ON agent_state.agent_runs(notion_page_id);
CREATE INDEX IF NOT EXISTS idx_agent_runs_status   ON agent_state.agent_runs(status);
CREATE INDEX IF NOT EXISTS idx_agent_logs_run      ON agent_state.agent_logs(run_id);

-- ============================================================
-- Grants (n8n_user: SELECT, INSERT, UPDATE)
-- ============================================================

GRANT USAGE ON SCHEMA agent_state TO n8n_user;
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA agent_state TO n8n_user;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA agent_state TO n8n_user;

ALTER DEFAULT PRIVILEGES IN SCHEMA agent_state
  GRANT SELECT, INSERT, UPDATE ON TABLES TO n8n_user;

ALTER DEFAULT PRIVILEGES IN SCHEMA agent_state
  GRANT USAGE ON SEQUENCES TO n8n_user;
