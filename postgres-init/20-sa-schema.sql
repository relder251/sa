-- =============================================================================
--  Sovereign Advisory — Lead Pipeline Schema
--  Tables live in the "sa" schema, not "public", so Prisma (LiteLLM) cannot
--  touch them via its migration sanity-check diff.
--  search_path is set on the role so app code can use unqualified names.
-- =============================================================================

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- Separate schema so Prisma migrate diff never generates DROP statements for us
CREATE SCHEMA IF NOT EXISTS sa;
GRANT USAGE  ON SCHEMA sa TO litellm;
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA sa TO litellm;
ALTER DEFAULT PRIVILEGES IN SCHEMA sa GRANT ALL ON TABLES TO litellm;

-- Make unqualified queries resolve to sa schema first for this role
ALTER ROLE litellm SET search_path TO sa, public;

-- ── Leads ─────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS sa.sa_leads (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at            TIMESTAMPTZ DEFAULT NOW(),
    first_name            VARCHAR(100),
    last_name             VARCHAR(100),
    email                 VARCHAR(255) NOT NULL,
    domain                VARCHAR(255),
    is_personal_email     BOOLEAN DEFAULT FALSE,
    service_area          VARCHAR(100),
    message               TEXT,
    company_research      JSONB,
    person_research       JSONB,
    summary               TEXT,
    approach              TEXT,
    conversation_starters JSONB,
    questions             JSONB,
    scenarios             JSONB,
    status                VARCHAR(50) DEFAULT 'pending_research',
    do_not_follow_up      BOOLEAN DEFAULT FALSE,
    archived              BOOLEAN DEFAULT FALSE,
    notion_page_id        VARCHAR(255),
    pdf_path              TEXT,
    research_completed_at TIMESTAMPTZ,
    draft_generated_at    TIMESTAMPTZ,
    first_notified_at     TIMESTAMPTZ,
    first_reminder_sent   BOOLEAN DEFAULT FALSE,
    first_reminder_at     TIMESTAMPTZ,
    second_reminder_sent  BOOLEAN DEFAULT FALSE,
    second_reminder_at    TIMESTAMPTZ,
    reviewed_at           TIMESTAMPTZ,
    sent_at               TIMESTAMPTZ
);

-- ── Email Drafts ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS sa.sa_lead_drafts (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    lead_id         UUID NOT NULL REFERENCES sa.sa_leads(id) ON DELETE CASCADE,
    version         INT DEFAULT 1,
    subject         TEXT,
    body_html       TEXT,
    body_text       TEXT,
    is_current      BOOLEAN DEFAULT TRUE,
    rejection_notes TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- ── Review Tokens ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS sa.sa_review_tokens (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    lead_id        UUID NOT NULL REFERENCES sa.sa_leads(id) ON DELETE CASCADE,
    token          VARCHAR(255) UNIQUE NOT NULL DEFAULT encode(gen_random_bytes(32), 'hex'),
    n8n_resume_url TEXT NOT NULL,
    created_at     TIMESTAMPTZ DEFAULT NOW(),
    used_at        TIMESTAMPTZ,
    is_active      BOOLEAN DEFAULT TRUE
);

-- ── Email Thread Tracking ─────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS sa.sa_email_threads (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    lead_id     UUID NOT NULL REFERENCES sa.sa_leads(id) ON DELETE CASCADE,
    message_id  VARCHAR(500),
    in_reply_to VARCHAR(500),
    direction   VARCHAR(10) CHECK (direction IN ('sent', 'received')),
    subject     TEXT,
    body_html   TEXT,
    body_text   TEXT,
    sent_at     TIMESTAMPTZ,
    received_at TIMESTAMPTZ
);

-- ── Indexes ───────────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_sa_leads_status  ON sa.sa_leads(status);
CREATE INDEX IF NOT EXISTS idx_sa_leads_email   ON sa.sa_leads(email);
CREATE INDEX IF NOT EXISTS idx_sa_leads_created ON sa.sa_leads(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_sa_leads_dnfu    ON sa.sa_leads(do_not_follow_up) WHERE do_not_follow_up = TRUE;
CREATE INDEX IF NOT EXISTS idx_sa_review_token  ON sa.sa_review_tokens(token) WHERE is_active = TRUE;
CREATE INDEX IF NOT EXISTS idx_sa_review_lead   ON sa.sa_review_tokens(lead_id);
CREATE INDEX IF NOT EXISTS idx_sa_drafts_lead   ON sa.sa_lead_drafts(lead_id);
CREATE INDEX IF NOT EXISTS idx_sa_threads_lead  ON sa.sa_email_threads(lead_id);
