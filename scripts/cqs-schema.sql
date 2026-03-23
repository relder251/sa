-- CQS: Competitive Quality System — persistent score registry
-- Never truncated. Survives across all project cycles.

CREATE TABLE IF NOT EXISTS agent_scores (
    id              SERIAL PRIMARY KEY,
    project_slug    TEXT NOT NULL,
    agent_name      TEXT NOT NULL,         -- implementer|tester|audit|break-fix|doc|orchestrator
    current_score   INTEGER NOT NULL DEFAULT 70,
    cycle_count     INTEGER NOT NULL DEFAULT 0,
    bugs_introduced INTEGER NOT NULL DEFAULT 0,
    bugs_caught     INTEGER NOT NULL DEFAULT 0,
    repeat_bugs     INTEGER NOT NULL DEFAULT 0,
    clean_cycles    INTEGER NOT NULL DEFAULT 0,
    challenges_won  INTEGER NOT NULL DEFAULT 0,
    challenges_lost INTEGER NOT NULL DEFAULT 0,
    suspensions     INTEGER NOT NULL DEFAULT 0,
    model_tier      TEXT NOT NULL DEFAULT 'sonnet', -- opus|sonnet|haiku|suspended
    trust_tier      INTEGER NOT NULL DEFAULT 2,     -- 1=read-only 2=normal 3=extended
    last_updated    TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (project_slug, agent_name)
);

CREATE TABLE IF NOT EXISTS score_events (
    id              SERIAL PRIMARY KEY,
    project_slug    TEXT NOT NULL,
    cycle_id        TEXT NOT NULL,
    event_ts        TIMESTAMPTZ DEFAULT NOW(),
    agent_name      TEXT NOT NULL,
    event_type      TEXT NOT NULL,    -- see CQS scoring events table
    points          INTEGER NOT NULL, -- negative = penalty
    description     TEXT NOT NULL,
    evidence        TEXT,             -- file:line or commit hash
    validated_by    TEXT             -- agent that confirmed this event
);

CREATE TABLE IF NOT EXISTS bug_registry (
    id              SERIAL PRIMARY KEY,
    project_slug    TEXT NOT NULL,
    fingerprint     TEXT NOT NULL,    -- SHA256(file:function:error_class:description_hash)
    first_seen      TEXT NOT NULL,    -- cycle_id
    last_seen       TEXT NOT NULL,    -- cycle_id
    times_seen      INTEGER NOT NULL DEFAULT 1,
    status          TEXT NOT NULL DEFAULT 'open', -- open|closed|regressed
    introduced_by   TEXT,            -- implementer agent instance
    caught_by       TEXT,            -- tester|audit|doc|orchestrator|prod
    closed_cycle    TEXT,
    UNIQUE (project_slug, fingerprint)
);

CREATE TABLE IF NOT EXISTS challenge_log (
    id              SERIAL PRIMARY KEY,
    project_slug    TEXT NOT NULL,
    cycle_id        TEXT NOT NULL,
    challenger      TEXT NOT NULL,
    challenged      TEXT NOT NULL,
    claim           TEXT NOT NULL,
    evidence        TEXT NOT NULL,
    outcome         TEXT,             -- upheld|dismissed|pending
    arbitrated_by   TEXT,
    resolved_ts     TIMESTAMPTZ
);

-- Seed default scores for a new project
-- Run: psql ... -c "SELECT cqs_init_project('your-slug');"
CREATE OR REPLACE FUNCTION cqs_init_project(slug TEXT) RETURNS VOID AS $$
BEGIN
    INSERT INTO agent_scores (project_slug, agent_name)
    VALUES
        (slug, 'implementer'),
        (slug, 'tester'),
        (slug, 'audit'),
        (slug, 'break-fix'),
        (slug, 'doc'),
        (slug, 'orchestrator')
    ON CONFLICT (project_slug, agent_name) DO NOTHING;
END;
$$ LANGUAGE plpgsql;

-- Score decay function — run every 10 cycles from n8n
-- Drifts scores 10% toward 70 (baseline). Recent performance outweighs history.
CREATE OR REPLACE FUNCTION cqs_apply_decay(slug TEXT) RETURNS VOID AS $$
BEGIN
    UPDATE agent_scores
    SET current_score = current_score + ROUND((70 - current_score) * 0.10),
        last_updated  = NOW()
    WHERE project_slug = slug
      AND model_tier   != 'suspended';
END;
$$ LANGUAGE plpgsql;

-- Model tier routing — called after every score update
CREATE OR REPLACE FUNCTION cqs_update_tier(slug TEXT, agent TEXT) RETURNS VOID AS $$
DECLARE
    s INTEGER;
BEGIN
    SELECT current_score INTO s
    FROM agent_scores
    WHERE project_slug = slug AND agent_name = agent;

    UPDATE agent_scores SET
        model_tier = CASE
            WHEN s >= 85 THEN 'opus'
            WHEN s >= 65 THEN 'sonnet'
            WHEN s >= 40 THEN 'haiku'
            ELSE 'suspended'
        END,
        trust_tier = CASE
            WHEN s >= 85 THEN 3
            WHEN s >= 65 THEN 2
            WHEN s >= 40 THEN 1
            ELSE 0
        END,
        last_updated = NOW()
    WHERE project_slug = slug AND agent_name = agent;
END;
$$ LANGUAGE plpgsql;
