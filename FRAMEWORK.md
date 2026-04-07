# PROJECT FRAMEWORK — Universal Development Standard
**Version:** 3.0 | **Status:** Canonical — supersedes PCIR-CYCLE.md and PCIRT-PLUS.md
**Scope:** All projects. Drop `FRAMEWORK.md` into every project root.

---

## DESIGN PRINCIPLES

This framework runs two loops simultaneously:

**Planned Loop (PCIRT+)** — episodic, human-initiated. Five phases across five clean
sessions. Produces shippable increments with full test coverage and documentation.

**Continuous Quality Loop (CQL)** — event-driven, autonomous. Three adversarial agents
running between planned cycles. Catches what the planned loop missed.

Both loops are governed by the **Competitive Quality System (CQS)** — a persistent
scoring layer that makes agents genuinely adversarial, routes scoring outcomes to
model selection and trust tier, and creates a self-improving quality feedback loop.

```
  PLANNED LOOP
  ┌────────┐  ┌───────────┐  ┌───────────┐  ┌──────┐  ┌────────┐
  │  PULL  │─▶│ CONFIGURE │─▶│ IMPLEMENT │─▶│ TEST │─▶│ REVIEW │
  └────────┘  └───────────┘  └───────────┘  └──────┘  └───┬────┘
       ▲                                                    │
       └────────────── next cycle (clean session) ──────────┘

  CONTINUOUS QUALITY LOOP
  git push / file event / cron (03:00)
       │
  ┌────▼──────────────────────────────────────┐
  │           n8n ORCHESTRATOR                │
  └──────┬────────────────┬──────────┬────────┘
         │                │          │
  ┌──────▼──┐    ┌────────▼──┐  ┌───▼─────┐
  │  AUDIT  │    │ BREAK-FIX │  │   DOC   │
  │ Tier 1  │    │ Tier 2→3  │  │ Tier 3  │
  │read-only│    │HITL gated │  │doc-only │
  └──────┬──┘    └────┬──────┘  └──────┬──┘
         │            │ .APPROVED        │
         │       ┌────▼──────┐          │
         │       │  YOU      │          │
         │       │ approve?  │          │
         │       └────┬──────┘          │
         └────────────▼─────────────────┘
                      │
            ┌─────────▼──────────┐
            │    MIRROR ENV      │
            │  apply → test      │
            │  sanity → gate     │
            └─────────┬──────────┘
               PASS ──┴── FAIL
                 │            │
           push prod     discard + carry-over
           notify         notify

  COMPETITIVE QUALITY SYSTEM (runs continuously)
  Every scoring event → updates PostgreSQL score registry
  Score → determines model tier (Opus/Sonnet/Haiku) and trust tier
  Challenge mechanic → agents can dispute each other's outputs
```

---

## GLOBAL RULES — NON-NEGOTIABLE

```
CYCLE       1. One phase per session. /clear between every phase. No exceptions.
            2. Never start a new phase in the same session as the previous one.
            3. Handoff is the only state that crosses session boundaries.

CODE        4. Read every file in full before modifying it.
            5. No TODO / FIXME / INCOMPLETE / PLACEHOLDER in shipped code — build fails.
            6. Build + all tests pass before any output block is written.
            7. No hardcoded credentials anywhere — hooks enforce, registry fingerprints.

TESTING     8. Tests are written before implementation (TDD). Always.
            9. Tests must fail before implementation, pass after.
           10. Runtime execution tests are mandatory — compile success ≠ correctness.

MIRROR     11. Every change — planned or break-fix — hits mirror before prod.
           12. Mirror must be aligned (drift check passes) before TEST phase runs.
           13. mirror-staging is written only by the TEST phase.

AGENTS     14. Agents propose within their trust tier. No tier escalation without HITL.
           15. Break-fix patches require a physical .APPROVED file before Stage 2.
           16. Audit findings enter the planned loop via CYCLE-TASKS.md Priority 1 only.

SCORING    17. Scores must change behavior — model selection, trust tier, suspension.
           18. Bug definitions and scoring events are objective, not interpretive.
           19. Repeat bugs carry double penalty — bug registry is consulted every cycle.
           20. Score decay runs every 10 cycles — recent performance outweighs history.
```

---

## PHASE 0 — INFRASTRUCTURE (one-time per project)

### 0.1 Mirror Environment

**`docker-compose.mirror.yml`** — project root:

```yaml
version: "3.9"
# Mirror environment — isolated Docker replica for PCIRT+ testing
# Same images as production. Separate DB. No live API keys.
# LiteLLM routes to local Ollama only — never cloud in mirror.

services:
  app-mirror:
    build: .
    container_name: ${PROJECT_SLUG}-mirror
    ports:
      - "${MIRROR_PORT:-8081}:8080"
    environment:
      ENV: mirror
      DATABASE_URL: postgresql://mirror_user:mirror_pass@db-mirror:5432/mirror_db
      LITELLM_BASE_URL: http://litellm-mirror:4001
    depends_on: [db-mirror, litellm-mirror]
    volumes:
      - ./:/app
    networks: [mirror-net]

  db-mirror:
    image: postgres:15-alpine
    container_name: ${PROJECT_SLUG}-db-mirror
    environment:
      POSTGRES_USER: mirror_user
      POSTGRES_PASSWORD: mirror_pass
      POSTGRES_DB: mirror_db
    ports:
      - "${MIRROR_DB_PORT:-5433}:5432"
    volumes:
      - mirror-db-data:/var/lib/postgresql/data
    networks: [mirror-net]

  litellm-mirror:
    image: ghcr.io/berriai/litellm:main-latest
    container_name: ${PROJECT_SLUG}-litellm-mirror
    ports:
      - "${MIRROR_LITELLM_PORT:-4002}:4001"
    environment:
      LITELLM_MASTER_KEY: ${MIRROR_LITELLM_KEY}
      OLLAMA_API_BASE: http://host.docker.internal:11434
    networks: [mirror-net]

  score-db:
    image: postgres:15-alpine
    container_name: ${PROJECT_SLUG}-score-db
    environment:
      POSTGRES_USER: scores_user
      POSTGRES_PASSWORD: scores_pass
      POSTGRES_DB: cqs_scores
    ports:
      - "${SCORE_DB_PORT:-5434}:5432"
    volumes:
      - score-db-data:/var/lib/postgresql/data
      - ./scripts/cqs-schema.sql:/docker-entrypoint-initdb.d/schema.sql
    networks: [mirror-net]

volumes:
  mirror-db-data:
  score-db-data:

networks:
  mirror-net:
    driver: bridge
```

**`scripts/mirror-sync.sh`:**

```bash
#!/usr/bin/env bash
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
echo "==> Syncing mirror..."
docker compose -f docker-compose.mirror.yml pull --quiet
docker compose -f docker-compose.mirror.yml run --rm app-mirror \
  bash -c "${MIGRATE_CMD:-echo 'no migrate cmd'}"
docker compose -f docker-compose.mirror.yml up -d --force-recreate
echo "==> Mirror synced at $(date)"
echo "    App:     http://localhost:${MIRROR_PORT:-8081}"
echo "    DB:      localhost:${MIRROR_DB_PORT:-5433}"
```

**`scripts/mirror-drift-check.sh`:**

```bash
#!/usr/bin/env bash
set -euo pipefail
PROD=$(docker inspect --format='{{index .RepoDigests 0}}' \
  "${PROJECT_SLUG}-app" 2>/dev/null || echo "NOT_RUNNING")
MIRROR=$(docker inspect --format='{{index .RepoDigests 0}}' \
  "${PROJECT_SLUG}-mirror" 2>/dev/null || echo "NOT_RUNNING")
if [ "$PROD" != "$MIRROR" ]; then
  echo "DRIFT DETECTED — run: bash scripts/mirror-sync.sh" >&2
  echo "  Prod:   $PROD" >&2
  echo "  Mirror: $MIRROR" >&2
  exit 1
fi
echo "Mirror aligned. Digest: $PROD"
```

**`.env.mirror.example`:**

```bash
PROJECT_SLUG=replace-me
MIRROR_PORT=8081
MIRROR_DB_PORT=5433
MIRROR_LITELLM_PORT=4002
MIRROR_LITELLM_KEY=mirror-test-key-not-real
SCORE_DB_PORT=5434
MIGRATE_CMD=alembic upgrade head
```

---

### 0.2 Competitive Quality System — Score Registry

**`scripts/cqs-schema.sql`** — auto-applied when score-db container starts:

```sql
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
```

**`scripts/cqs-score.sh`** — CLI tool for recording score events:

```bash
#!/usr/bin/env bash
# Usage: bash scripts/cqs-score.sh <agent> <event_type> <points> <description> [evidence]
# Example: bash scripts/cqs-score.sh tester BUG_FOUND +10 "null ptr in auth.py" "auth.py:42"
set -euo pipefail

AGENT="${1:?agent required}"
EVENT="${2:?event_type required}"
POINTS="${3:?points required}"
DESC="${4:?description required}"
EVIDENCE="${5:-}"
CYCLE="${CYCLE_ID:-unknown}"
SLUG="${PROJECT_SLUG:?PROJECT_SLUG not set}"

PGPASSWORD=scores_pass psql \
  -h localhost -p "${SCORE_DB_PORT:-5434}" \
  -U scores_user -d cqs_scores \
  -c "INSERT INTO score_events (project_slug, cycle_id, agent_name, event_type, points, description, evidence)
      VALUES ('${SLUG}', '${CYCLE}', '${AGENT}', '${EVENT}', ${POINTS}, '${DESC}', '${EVIDENCE}');" \
  -c "UPDATE agent_scores SET current_score = current_score + ${POINTS}, last_updated = NOW()
      WHERE project_slug = '${SLUG}' AND agent_name = '${AGENT}';" \
  -c "SELECT cqs_update_tier('${SLUG}', '${AGENT}');" \
  > /dev/null

echo "CQS: [${AGENT}] ${EVENT} ${POINTS}pts — ${DESC}"
```

**`scripts/cqs-report.sh`** — prints current standings:

```bash
#!/usr/bin/env bash
set -euo pipefail
SLUG="${PROJECT_SLUG:?PROJECT_SLUG not set}"

PGPASSWORD=scores_pass psql \
  -h localhost -p "${SCORE_DB_PORT:-5434}" \
  -U scores_user -d cqs_scores \
  --pset=format=aligned \
  -c "SELECT agent_name, current_score, model_tier, trust_tier,
             bugs_introduced, bugs_caught, repeat_bugs, clean_cycles,
             challenges_won, challenges_lost
      FROM agent_scores
      WHERE project_slug = '${SLUG}'
      ORDER BY current_score DESC;"
```

**`scripts/cqs-bug-register.sh`** — fingerprint and record a bug:

```bash
#!/usr/bin/env bash
# Usage: bash scripts/cqs-bug-register.sh <file> <function> <error_class> <description>
set -euo pipefail

FILE="${1:?file required}"
FUNC="${2:?function required}"
ERR_CLASS="${3:?error_class required}"
DESC="${4:?description required}"
SLUG="${PROJECT_SLUG:?PROJECT_SLUG not set}"
CYCLE="${CYCLE_ID:-unknown}"

FINGERPRINT=$(echo "${SLUG}:${FILE}:${FUNC}:${ERR_CLASS}:${DESC}" \
  | sha256sum | cut -d' ' -f1)

# Check for repeat
EXISTING=$(PGPASSWORD=scores_pass psql \
  -h localhost -p "${SCORE_DB_PORT:-5434}" \
  -U scores_user -d cqs_scores -tAc \
  "SELECT times_seen FROM bug_registry
   WHERE project_slug='${SLUG}' AND fingerprint='${FINGERPRINT}';")

if [ -n "$EXISTING" ]; then
  # Repeat bug — update and signal double penalty
  PGPASSWORD=scores_pass psql \
    -h localhost -p "${SCORE_DB_PORT:-5434}" \
    -U scores_user -d cqs_scores \
    -c "UPDATE bug_registry SET times_seen = times_seen + 1,
            last_seen = '${CYCLE}', status = 'regressed'
        WHERE project_slug='${SLUG}' AND fingerprint='${FINGERPRINT}';" \
    > /dev/null
  echo "REPEAT_BUG fingerprint=${FINGERPRINT} times_seen=$((EXISTING+1))"
  exit 2  # Exit 2 = repeat bug signal to caller
else
  # New bug — register
  PGPASSWORD=scores_pass psql \
    -h localhost -p "${SCORE_DB_PORT:-5434}" \
    -U scores_user -d cqs_scores \
    -c "INSERT INTO bug_registry
            (project_slug, fingerprint, first_seen, last_seen, status)
        VALUES ('${SLUG}', '${FINGERPRINT}', '${CYCLE}', '${CYCLE}', 'open');" \
    > /dev/null
  echo "NEW_BUG fingerprint=${FINGERPRINT}"
  exit 0  # Exit 0 = new bug
fi
```

---

### 0.3 CQS Scoring Rules

#### Objective Bug Definition

A "bug" is recorded in the registry only when ALL of the following are true:

```
1. The code compiles / passes static analysis                (not a syntax error)
2. The code produces incorrect output for a defined input    (logic error)
   OR raises an unhandled exception on a valid input path    (runtime error)
   OR silently discards an error without logging             (silent failure)
   OR violates a documented business rule                    (logic drift)
3. The incorrect behavior is demonstrated with:
   - Input:    the exact value or request that triggers the bug
   - Expected: what correct behavior looks like
   - Actual:   what the code actually produces
4. The bug exists in committed code (not in a draft or WIP branch)
```

Style issues, formatting, naming preferences, and non-actionable warnings do NOT
qualify as bugs. An agent flagging these as bugs is itself a scoring event
(INVALID_BUG_CLAIM, -5 pts against the claimant).

#### Objective Functional Gap Definition

A functional gap is recorded against the orchestrator only when:

```
1. A requirement was explicitly listed in CYCLE-TASKS.md
2. The requirement was not in the OUT OF SCOPE section
3. The requirement was not completed and not tracked as a carry-over
4. The gap is discovered after REVIEW phase closes the cycle
```

Gaps from incomplete requirements, scope drift initiated by the human, or
requirements added after CYCLE-TASKS.md was written are NOT orchestrator gaps.

#### Scoring Events Table

| Event | Agent Gains | Agent Loses | Notes |
|-------|-------------|-------------|-------|
| Tester finds new bug (TEST phase) | Tester +10 | Implementer -10 | Bug registered |
| Tester finds repeat bug | Tester +10 | Implementer -20 | Double penalty; repeat flag |
| Audit/Doc finds bug tester missed | Audit/Doc +5 | Tester -15 | Bug must meet definition |
| Bug reaches prod post-REVIEW | — | Implementer -30, Tester -20, Orchestrator -10 | Highest penalty |
| Clean cycle, prod sanity passes | Implementer +5, Tester +3 | — | Both must be zero-defect |
| Clean cycle 3 in a row | Implementer +10, Tester +5 | — | Streak bonus |
| Doc error caught by audit | Audit +5 | Doc -10 | Stale docstring, wrong sig, etc. |
| Doc error caught by tester | Tester +3 | Doc -5 | Lower penalty — not primary role |
| Functional gap post-REVIEW | Tester +5 | Orchestrator -10 | Must meet gap definition |
| Break-fix patch passes mirror | Break-fix +5 | — | |
| Break-fix patch fails mirror | — | Break-fix -10 | Mirror reverted automatically |
| CRITICAL audit finding resolved this cycle | Audit +3 | — | Encourages thoroughness |
| CRITICAL reopened (was "resolved" in prior handoff) | Audit +10 | Implementer -15, Tester -10 | Regression |
| Invalid bug claim | — | Claimant -5 | Style/warnings flagged as bugs |
| Challenge upheld | Challenger +8 | Challenged -8 | Third-party validated |
| Challenge dismissed | — | Challenger -8 | Frivolous challenge |
| Agent suspended | — | — | Score < 40; human must reset |
| Agent restored after suspension | — | — | 3 clean cycles to reach 50 |

#### Model and Trust Routing

```
Score ≥ 85  → model: Opus       trust: 3 (extended autonomy, can spawn sub-agents)
Score 65–84 → model: Sonnet     trust: 2 (normal operations)
Score 40–64 → model: Haiku      trust: 1 (outputs reviewed by one higher tier before acting)
Score < 40  → model: suspended  trust: 0 (human must intervene and reset to 50)
```

Trust tier affects tool grants in agent definitions — not just model selection.
An agent at trust tier 1 has its proposed actions reviewed by the next tier up
before execution. This is enforced by n8n routing, not agent self-restraint.

#### Score Decay

Run every 10 cycles via n8n scheduled trigger:

```sql
SELECT cqs_apply_decay('${PROJECT_SLUG}');
```

Effect: each score moves 10% toward baseline (70). A score of 90 becomes 88.
A score of 40 becomes 43. Suspended agents are excluded from decay — they
must earn back to 50 through clean cycles before decay resumes.

#### Challenge Mechanic

Any agent can issue a formal challenge against another agent's output before
the next phase begins. Challenges must be:

```
1. Specific — identifies the exact claim being disputed (file:line or output block section)
2. Evidenced — provides concrete counter-evidence, not just disagreement
3. Timely — raised before the next phase's output block is written

Resolution:
  - A third agent (or the orchestrator if neither party is the orchestrator) arbitrates
  - Outcome: upheld (challenger +8, challenged -8) or dismissed (challenger -8)
  - All challenges logged to challenge_log table regardless of outcome
  - Frivolous patterns (3+ dismissed in 5 cycles) trigger a -15 abuse penalty
```

Challenge example: Tester challenges Implementer's "IMPLEMENT COMPLETE — zero failures"
claim by pointing to a runtime check that was not executed. If the TEST phase confirms
the check was skipped, challenge is upheld.

---

### 0.4 OpenClaw Agent Team

Three agents per project. Store in `.openclaw/agents/{PROJECT_SLUG}/`.
Scores are injected into each agent's context at runtime by n8n from the registry.

**`.openclaw/agents/{PROJECT_SLUG}/audit.md`:**

```markdown
---
name: audit
trust_tier: 1
model: ${AUDIT_MODEL}           # injected from score registry at runtime
description: >
  Read-only codebase auditor. Fires on git push and daily cron.
  Writes findings to AUDIT.md and priority-tagged items to CYCLE-TASKS.md.
  Never modifies source files. Earns points for CRITICAL/HIGH findings
  later confirmed valid. Penalized for findings that fail the bug definition.
tools: [Read, Grep, Glob, Bash]
allowed_bash: [find, grep, cat, wc, git log, git diff, git show, sha256sum]
disallowed_bash: [rm, mv, cp, git commit, git push, chmod, curl, wget, docker]
---

## YOUR SCORING CONTEXT
Current score: ${AUDIT_SCORE}
Model tier:    ${AUDIT_MODEL}
Trust tier:    ${AUDIT_TRUST}
Bugs caught this project: ${AUDIT_BUGS_CAUGHT}
Challenges won/lost: ${AUDIT_CHALLENGES}

You earn points for findings that are confirmed by the TEST phase or reach prod.
You lose points for findings that fail the objective bug definition (see FRAMEWORK.md).
File an invalid finding and you are penalized. Be thorough AND precise.

## Audit Protocol

### Security Pass
- Credentials in source:
  grep -rn 'password\|secret\|api_key\|token\|bearer' {SRC_DIR} \
    --include="*.py" --include="*.ts" --include="*.js" \
    | grep -vE '\.env|example|test|mock'
- World-writable files: find {PROJECT_ROOT} -perm -o+w -type f | grep -v '.git'
- .env.example completeness: compare declared vars to vars referenced in source
- eval/exec/shell=True usage: grep -rn 'eval(\|exec(\|shell=True' {SRC_DIR}

### Logic Pass
- Bare exception handlers: grep -rn 'except:\|catch (e) {}' {SRC_DIR}
- Async without await: grep -rn 'async def\|async function' {SRC_DIR} (cross-ref awaits)
- Missing return type annotations: grep -rn '^def \|^function ' {SRC_DIR} | grep -v '->'
- Compare HANDOFF.md DONE items to actual file state — flag drift

### Documentation Pass
- Functions without docstrings (Python): grep -rn '^    def ' {SRC_DIR} (cross-ref triple-quote)
- README vs source drift: compare documented endpoints/vars to actual code
- CHANGELOG currency: compare last changelog entry to most recent commit date

### Dependency Pass
- Run: {DEP_AUDIT_CMD}

## Output — AUDIT.md (append, never overwrite)

```
── AUDIT RUN ──────────────────────────────────────
Date:     {DATE} {TIME}
Trigger:  push|cron|manual
Commit:   {COMMIT_HASH}
Score:    {MY_SCORE} ({MY_MODEL})

CRITICAL — block next cycle if unresolved:
  [file:line] {input} → {actual} ≠ {expected} [fingerprint: {hash}]

HIGH:
  [file:line] {finding}

MEDIUM / LOW / INFO:
  [file:line] {finding}

Entries added to CYCLE-TASKS.md: {N}
── END AUDIT ──────────────────────────────────────
```

After writing AUDIT.md:
1. Append CRITICAL/HIGH to {PROJECT_ROOT}/CYCLE-TASKS.md tagged [AUDIT:CRITICAL] or [AUDIT:HIGH]
2. For each finding, call: bash scripts/cqs-bug-register.sh {file} {function} {error_class} {desc}
   - Exit 0 = new bug (record normally)
   - Exit 2 = repeat bug (add [REPEAT] tag — double penalty applies to implementer)
```

---

**`.openclaw/agents/{PROJECT_SLUG}/break-fix.md`:**

```markdown
---
name: break-fix
trust_tier: 2  # stage 1 / becomes 3 in stage 2 after approval
model: ${BREAKFIX_MODEL}
description: >
  Two-stage targeted fix agent. Stage 1: proposes patches to .patches/ (Tier 2).
  Stage 2: applies approved patches to mirror only (Tier 3). HITL gate between
  stages is mechanical — requires physical .APPROVED file. Never touches prod.
  Penalized for patches that fail mirror tests. Rewarded for patches that pass.
tools: [Read, Write, Bash]
---

## YOUR SCORING CONTEXT
Current score:   ${BREAKFIX_SCORE}
Model tier:      ${BREAKFIX_MODEL}
Trust tier:      ${BREAKFIX_TRUST}
Patches passed:  ${BREAKFIX_PASS}
Patches failed:  ${BREAKFIX_FAIL}

You earn +5 for every patch that passes mirror tests.
You lose -10 for every patch that fails mirror tests.
A patch that is applied to prod without mirror validation costs you -25.

## Stage 1 — PROPOSE (Tier 2, automatic after audit)

Trigger: n8n detects CRITICAL or HIGH in latest AUDIT.md

1. Read AUDIT.md — extract most recent CRITICAL/HIGH findings only
2. For each finding, read affected file IN FULL
3. Draft minimal targeted patch — fix only what the finding describes
4. Fingerprint check: bash scripts/cqs-bug-register.sh {file} {func} {class} {desc}
   - If exit 2 (repeat): add [REPEAT-BUG] tag to proposal — double penalty context
5. Write patch: .patches/{CYCLE_ID}-{finding-slug}.patch (git diff format)
6. Write proposal: .patches/{CYCLE_ID}-{finding-slug}.proposal.md

Proposal format:
  ── PATCH PROPOSAL ──────────────────────────────
  Finding:    {audit finding verbatim}
  File:       {file:line}
  Repeat bug: YES (seen {N} times) | NO
  Patch:      .patches/{filename}.patch
  Risk:       LOW | MEDIUM | HIGH
  Risk notes: {what could go wrong if this patch is incorrect}
  Test plan:  {exact commands to verify on mirror}
  Rollback:   {exact command to revert if mirror test fails}
  ── AWAITING YOUR APPROVAL ──────────────────────
  Approve: touch .patches/{filename}.APPROVED
  Reject:  touch .patches/{filename}.REJECTED
  ────────────────────────────────────────────────

7. STOP. Do not apply. Do not proceed without .APPROVED file.

## Stage 2 — APPLY TO MIRROR (Tier 3, post-approval only)

Trigger: n8n detects .patches/{filename}.APPROVED

1. Confirm .APPROVED exists — if missing, STOP immediately with error
2. Drift check: bash scripts/mirror-drift-check.sh
   If drift: STOP — notify, wait for sync
3. Apply to mirror branch:
   git checkout mirror-staging
   git apply .patches/{filename}.patch
4. Run tests on mirror:
   docker compose -f docker-compose.mirror.yml run --rm app-mirror {TEST_CMD}
5. Run sanity check:
   docker compose -f docker-compose.mirror.yml run --rm app-mirror {SANITY_CMD}
6. If PASS:
   - Write .patches/{filename}.MIRROR_PASS
   - Run: bash scripts/cqs-score.sh break-fix PATCH_PASS +5 "{finding-slug}"
7. If FAIL:
   - git checkout -- . (revert mirror)
   - Write .patches/{filename}.MIRROR_FAIL with full failure output
   - Run: bash scripts/cqs-score.sh break-fix PATCH_FAIL -10 "{finding-slug}"
   - Add [MIRROR-FAIL] carry-over to CYCLE-TASKS.md
8. Notify n8n with result
```

---

**`.openclaw/agents/{PROJECT_SLUG}/doc.md`:**

```markdown
---
name: doc
trust_tier: 3
model: ${DOC_MODEL}
description: >
  Documentation sync agent. Fires after IMPLEMENT commits and after break-fix
  MIRROR_PASS. Diffs docstrings and comments against actual function signatures.
  Updates stale docs on mirror branch. Commits during REVIEW phase only.
  Penalized for doc errors caught by audit. Penalized for doc errors caught by tester.
tools: [Read, Write, Edit, Bash]
allowed_bash: [find, grep, cat, git diff, git log, docker compose]
disallowed_bash: [git push, git commit, rm, chmod]
---

## YOUR SCORING CONTEXT
Current score:    ${DOC_SCORE}
Model tier:       ${DOC_MODEL}
Doc errors caught by audit: ${DOC_AUDIT_CATCHES}
Doc errors caught by tester: ${DOC_TESTER_CATCHES}

You lose -10 for every doc error caught by audit after you ran.
You lose -5 for every doc error caught by tester.
Audit catching your errors means you missed something. Be thorough.

## Doc Sync Protocol

1. Read HANDOFF.md — identify files modified in the last cycle
2. For each modified file:
   a. Read the file in full
   b. For each function/class/module:
      - Compare docstring to actual signature and behavior
      - Compare parameter names in docstring to actual parameter names
      - Compare return type in docstring to actual return type
      - If stale or missing: rewrite — do NOT change logic, only documentation
3. Check README.md:
   - Compare documented endpoints to actual routes
   - Compare documented config vars to .env.example
   - Update README to match source — never update source to match README
4. Write to CHANGELOG.md:
   ## [{CYCLE_ID}] - {DATE}
   ### Documentation
   - {file}: {function} — {what changed and why}
5. Validate on mirror:
   docker compose -f docker-compose.mirror.yml run --rm app-mirror {BUILD_CMD}
6. Write .doc-changes/{CYCLE_ID}.md with summary of all changes made
7. Do not commit — REVIEW phase commits doc-changes alongside the cycle commit
```

---

### 0.5 n8n Orchestration Workflow

**`n8n/PCIRT-Framework-Orchestrator.json`:**

```json
{
  "name": "PCIRT Framework Orchestrator",
  "nodes": [
    { "name": "Git Push Webhook",       "type": "webhook",      "path": "pcirt-push"         },
    { "name": "Cron Daily Audit",       "type": "cron",         "rule": {"hour":3,"minute":0} },
    { "name": "Patch Approval Watcher", "type": "watchFiles",   "paths": [".patches/*.APPROVED"] },
    { "name": "Cycle Complete Webhook", "type": "webhook",      "path": "pcirt-cycle-complete"},
    { "name": "Decay Trigger Cron",     "type": "cron",         "note": "every 10 cycles — fire manually or via cycle counter" },
    {
      "name": "Router",
      "type": "switch",
      "rules": [
        {"trigger": "git-push",      "output": 0},
        {"trigger": "cron",          "output": 1},
        {"trigger": "patch-approved","output": 2},
        {"trigger": "decay",         "output": 3}
      ]
    },
    { "name": "Inject Score Context",    "type": "postgres", "note": "SELECT * FROM agent_scores WHERE project_slug=$slug" },
    { "name": "Run Audit Agent",         "type": "exec",     "cmd": "openclaw run audit --project $slug" },
    { "name": "Check Severity",          "type": "if",       "condition": "has_critical_or_high == true" },
    { "name": "Run BreakFix Stage1",     "type": "exec",     "cmd": "openclaw run break-fix --stage propose --project $slug" },
    { "name": "Notify HITL Proposal",   "type": "notify",   "channel": "#pcirt-alerts", "msg": "Patch proposal ready for $slug. Approve: touch .patches/$file.APPROVED" },
    { "name": "Run BreakFix Stage2",     "type": "exec",     "cmd": "openclaw run break-fix --stage apply --patch $patch --project $slug" },
    { "name": "Mirror Result Check",     "type": "if",       "condition": "mirror_result == PASS" },
    { "name": "Run Doc Agent",           "type": "exec",     "cmd": "openclaw run doc --project $slug" },
    { "name": "Notify Mirror Pass",      "type": "notify",   "channel": "#pcirt-alerts", "msg": "Mirror PASS — $slug. Run /review to push to prod." },
    { "name": "Notify Mirror Fail",      "type": "notify",   "channel": "#pcirt-alerts", "msg": "Mirror FAIL — $slug. Patch reverted. See .patches/$file.MIRROR_FAIL" },
    { "name": "Write BreakFix Scores",   "type": "exec",     "cmd": "bash scripts/cqs-score.sh break-fix $event $pts '$desc'" },
    { "name": "Apply Score Decay",       "type": "postgres", "cmd": "SELECT cqs_apply_decay('$slug')" },
    { "name": "Notify Suspended Agent",  "type": "notify",   "channel": "#pcirt-alerts", "msg": "AGENT SUSPENDED: $agent in $slug (score < 40). Human reset required." }
  ],
  "connections": {
    "Git Push Webhook":       ["Router"],
    "Cron Daily Audit":       ["Router"],
    "Patch Approval Watcher": ["Router"],
    "Cycle Complete Webhook": ["Router"],
    "Decay Trigger Cron":     ["Router"],
    "Router":                 ["Inject Score Context"],
    "Inject Score Context":   ["Run Audit Agent (push/cron)", "Run BreakFix Stage2 (patch-approved)", "Apply Score Decay (decay)"],
    "Run Audit Agent":        ["Check Severity"],
    "Check Severity":         ["Run BreakFix Stage1 (true)", "— (false, audit only)"],
    "Run BreakFix Stage1":    ["Notify HITL Proposal"],
    "Run BreakFix Stage2":    ["Mirror Result Check"],
    "Mirror Result Check":    ["Run Doc Agent + Notify Mirror Pass (true)", "Notify Mirror Fail + Write BreakFix Scores (false)"],
    "Write BreakFix Scores":  ["Notify Suspended Agent (if score < 40)"]
  }
}
```

**`.git/hooks/post-receive`** — triggers n8n on every push:

```bash
#!/usr/bin/env bash
curl -s -X POST "${N8N_WEBHOOK_URL}/pcirt-push" \
  -H "Content-Type: application/json" \
  -d "{\"trigger\":\"git-push\",\"commit\":\"$(git rev-parse HEAD)\",
       \"branch\":\"$(git rev-parse --abbrev-ref HEAD)\",
       \"project\":\"${PROJECT_SLUG}\"}" > /dev/null
```

---

### 0.6 Phase 0 Completion Checklist

```
MIRROR
[ ] docker-compose.mirror.yml created and tested
[ ] .env.mirror created (test values only — no prod secrets)
[ ] scripts/mirror-sync.sh executable (chmod +x)
[ ] scripts/mirror-drift-check.sh executable
[ ] Mirror starts: docker compose -f docker-compose.mirror.yml up -d
[ ] mirror-staging branch created and pushed: git checkout -b mirror-staging

CQS SCORE REGISTRY
[ ] scripts/cqs-schema.sql created
[ ] score-db container starts and schema applied
[ ] Project initialized: psql ... -c "SELECT cqs_init_project('${PROJECT_SLUG}');"
[ ] scripts/cqs-score.sh executable
[ ] scripts/cqs-report.sh executable — verify: bash scripts/cqs-report.sh
[ ] scripts/cqs-bug-register.sh executable
[ ] All agents start at score 70 (Sonnet, trust tier 2)
[ ] .patches/ directory created
[ ] .doc-changes/ directory created

OPENCLAW AGENTS
[ ] .openclaw/agents/${PROJECT_SLUG}/audit.md created
[ ] .openclaw/agents/${PROJECT_SLUG}/break-fix.md created
[ ] .openclaw/agents/${PROJECT_SLUG}/doc.md created
[ ] Score context injection configured in n8n (Inject Score Context node)

N8N
[ ] PCIRT-Framework-Orchestrator workflow imported and activated
[ ] .git/hooks/post-receive installed and executable
[ ] N8N_WEBHOOK_URL set in environment
[ ] SCORE_DB_PORT set in environment
[ ] Notification channel configured (#pcirt-alerts or equivalent)
[ ] Test trigger: push an empty commit, confirm audit agent fires

ENVIRONMENT
[ ] .env.example documents every required variable with purpose and example format
[ ] BUILD_CMD confirmed: exits 0, zero warnings
[ ] TEST_CMD confirmed: exits 0
[ ] SANITY_CMD defined and documented
[ ] DEP_AUDIT_CMD defined (pip check / npm audit / cargo audit)
[ ] MIGRATE_CMD defined

→ All boxes checked: proceed to first cycle with /pull
```
---

## PHASE 1 — PULL

**Purpose:** Sync the latest state of the project. Read all pending signals —
handoff, audit findings, mirror-fail carry-overs. Produce a scoped, prioritized
`CYCLE-TASKS.md`. No code changes in this phase.

### Steps

```
0. FETCH LIVE STATE — before reading anything else:
   ssh root@187.77.208.197 'cat /opt/agentic-sdlc/.env'   # live credentials
   ssh root@187.77.208.197 'docker ps --format "table {{.Names}}\t{{.Status}}"'  # service state
   ssh root@187.77.208.197 'docker logs --tail=30 <any_failing_container> 2>&1'  # targeted logs
   → Do NOT use credential values, config states, or log output recalled from
     a prior session. Fetch live. Prior session data is assumed stale.
   → Session start hook auto-fetches docker ps via SSH — verify it loaded.

1. READ last handoff:
   cat {PROJECT_ROOT}/HANDOFF.md || echo "FIRST RUN"

2. READ latest audit findings:
   cat {PROJECT_ROOT}/AUDIT.md | tail -150 || echo "No audit findings"

3. PULL latest changes:
   cd {PROJECT_ROOT} && git pull --rebase origin {BRANCH}
   bash scripts/mirror-sync.sh

4. DISCOVER file tree:
   find {PROJECT_ROOT} -type f \
     | grep -vE 'node_modules|\.git|__pycache__|dist/|\.patches/|\.doc-changes/' \
     | sort

5. CHECK unresolved markers:
   grep -rn 'TODO\|FIXME\|INCOMPLETE\|PLACEHOLDER' {SRC_DIR} \
     || echo "Clean"

6. CONFIRM build baseline:
   cd {PROJECT_ROOT} && {BUILD_CMD}
   → If build fails: fix now. A broken baseline cannot enter CONFIGURE.

7. CHECK agent scores and model tiers:
   bash scripts/cqs-report.sh
   Note any suspended agents — these need human reset before CQL can run.

8. PRODUCE CYCLE-TASKS.md (priority order, see format below)
```

### CYCLE-TASKS.md Format

```markdown
# CYCLE-TASKS — {CYCLE_ID}
Generated: {DATE} {TIME} | Branch: {BRANCH}

## Priority 1 — Audit Critical [block cycle if unresolved]
- [ ] [AUDIT:CRITICAL] {file:line} — {finding}
      Bug definition: Input={x} Expected={y} Actual={z}
      Test: {exact command to verify fix}
      Fingerprint: {hash from cqs-bug-register.sh}
      Repeat: YES (N times) | NO

## Priority 2 — Carry-Overs from Last Handoff
- [ ] [CARRY-OVER] {item} — {why deferred}

## Priority 3 — Mirror-Fail Items
- [ ] [MIRROR-FAIL] {item} — {what failed, patch file reference}

## Priority 4 — Audit High
- [ ] [AUDIT:HIGH] {file:line} — {finding}

## Priority 5 — Planned Work
- [ ] {task} — Acceptance: {single verifiable criterion}

## Priority 6 — Audit Low / Info
- [ ] [AUDIT:LOW] {item}

## Out of Scope This Cycle
- {explicit exclusion — required to prevent orchestrator gap penalties}

## Done Condition
{Single verifiable criterion that closes this cycle}

## Test Plan
Unit tests:        {test files or test commands}
Integration tests: {integration targets}
Runtime checks:    {input → expected output pairs for actual execution}
Logic assertions:  {business rules to verify}
Edge cases:        {empty, boundary, auth-failure, dep-failure inputs}
Sanity command:    {SANITY_CMD}
```

### PULL Output Block

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PULL COMPLETE — {DATE} {TIME}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Cycle ID:       {CYCLE_ID}
Branch:         {BRANCH}
Last commit:    {COMMIT_HASH} — {MSG}
Build:          PASS | FAIL → [fixed: describe]
Audit:          {N} CRITICAL | {N} HIGH | {N} MEDIUM | {N} LOW
Carry-overs:    {N} from last handoff
Mirror:         SYNCED | DRIFT → [action taken]
Agent scores:   [paste cqs-report.sh output or "all nominal"]
Suspended:      [list suspended agents or "None"]
Cycle scope:    [2–3 sentence summary]
Done when:      [single verifiable criterion]
Next phase:     CONFIGURE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

**STOP. `/clear`. Start a clean session for CONFIGURE.**

---

## PHASE 2 — CONFIGURE

**Purpose:** Verify both production and mirror environments. Scaffold new
files/dirs needed by this cycle. Ensure both environments build cleanly
before implementation begins. No logic code written in this phase.

### Steps

```
1. READ CYCLE-TASKS.md:
   cat {PROJECT_ROOT}/CYCLE-TASKS.md

2. VERIFY production environment variables:
   - Diff .env.example against all vars referenced in source
   - Confirm all required vars are set in .env (do not print values)
   - Missing var = STOP and report — never invent a value

3. VERIFY mirror environment variables:
   docker compose -f docker-compose.mirror.yml config | grep -A2 environment
   - Confirm mirror has test equivalents for all required prod vars
   - Confirm no prod secrets are in mirror config
   - Confirm score-db is running: docker compose -f docker-compose.mirror.yml ps score-db

4. RUN mirror drift check:
   bash scripts/mirror-drift-check.sh
   → If drift: bash scripts/mirror-sync.sh, then re-check

5. VERIFY dependencies:
   {DEP_AUDIT_CMD}
   docker compose -f docker-compose.mirror.yml run --rm app-mirror {DEP_AUDIT_CMD}

6. SCAFFOLD new files/dirs from CYCLE-TASKS.md:
   - Create directory structure
   - Create files with header comments only — no implementation code
   - Add any new env vars to .env.example with purpose comment

7. VALIDATE scaffold on both environments:
   cd {PROJECT_ROOT} && {BUILD_CMD}
   docker compose -f docker-compose.mirror.yml run --rm app-mirror {BUILD_CMD}
   Both must exit 0.

8. COMMIT scaffold:
   git add -A && git commit -m "chore(scaffold): {CYCLE_ID}"
   bash scripts/mirror-sync.sh
```

### CONFIGURE Output Block

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CONFIGURE COMPLETE — {DATE} {TIME}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Prod env vars:   VERIFIED | GAPS → [list]
Mirror env vars: VERIFIED | GAPS → [list]
Score DB:        RUNNING | DOWN → [action]
Mirror drift:    ALIGNED | SYNCED → [action taken]
Dependencies:    CLEAN | ISSUES → [describe]
Scaffold:        [list new files/dirs or "None"]
Prod build:      PASS
Mirror build:    PASS
Scaffold commit: {COMMIT_HASH}
Next phase:      IMPLEMENT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

**STOP. `/clear`. Start a clean session for IMPLEMENT.**

---

## PHASE 3 — IMPLEMENT

**Purpose:** Build every task in CYCLE-TASKS.md using TDD. Write failing tests
first. Implement until tests pass. Document as you build. No stubs, no
placeholders, no silent failures. Zero-defect before handoff.

### Steps

```
1. READ before touching anything:
   cat {PROJECT_ROOT}/CYCLE-TASKS.md
   cat {PROJECT_ROOT}/HANDOFF.md
   [Read every file you will modify — in full]

2. CHECK implementer agent score and model:
   bash scripts/cqs-report.sh | grep implementer
   Note: score affects model available — high score = Opus, low = Haiku

3. FOR EACH TASK in CYCLE-TASKS.md (priority order):

   a. WRITE TESTS FIRST (mandatory):
      - Write unit test(s) covering: happy path, error path, ≥1 edge case
      - For AUDIT:CRITICAL/HIGH items: write a test that reproduces the bug first
      - Run tests: {TEST_CMD}
      - Confirm tests FAIL before implementation (proves tests are real)

   b. IMPLEMENT:
      - Write implementation until tests pass
      - Every function: docstring/JSDoc — signature, params, return, raises
      - Every module: header comment — purpose, dependencies, author
      - Every non-obvious decision: inline comment or entry in docs/decisions/
      - External calls: timeout, error handling, logged failure path

   c. VERIFY after each task:
      cd {PROJECT_ROOT} && {BUILD_CMD} && {TEST_CMD}
      → Both must exit 0 before moving to next task
      → Fix all errors and warnings — zero tolerance

   d. CHECK bug registry for AUDIT items:
      bash scripts/cqs-bug-register.sh {file} {func} {error_class} {desc}
      → Exit 0 = new bug being fixed (record normally)
      → Exit 2 = repeat bug being fixed (note in output — extra scrutiny in TEST)

   e. CHECK OFF the task in CYCLE-TASKS.md

4. FINAL marker check:
   grep -rn 'TODO\|FIXME\|INCOMPLETE\|PLACEHOLDER' {SRC_DIR} \
     || echo "Clean"
   → Any marker found = task is not complete, do not proceed

5. FINAL build + test:
   cd {PROJECT_ROOT} && {BUILD_CMD} && {TEST_CMD}
   → Must exit 0 with zero warnings

6. COMMIT:
   git add -A && git commit -m "feat({scope}): {description} [{CYCLE_ID}]"
```

### Sub-Agent Pattern (large cycles)

```
Orchestrator reads CYCLE-TASKS.md → splits by module boundary
→ Spawns implementer sub-agents in parallel (one per independent module)
→ Each sub-agent: reads targets, writes failing tests, implements, runs build
→ Sub-agent returns: {files changed, test results, build status, repeat-bug flags}
→ Orchestrator: integrates results, runs final build+test pass, commits
→ Sub-agent contexts discarded after results returned to orchestrator

Orchestrator scoring: gaps in task delegation penalize orchestrator (-10 per gap).
Sub-agent results reviewed at trust tier 1 (Haiku-tier) get one-layer review
from orchestrator before integration.
```

**`.claude/agents/implementer.md`:**

```markdown
---
name: implementer
description: Implements one self-contained module. TDD mandatory. Returns result
  summary to orchestrator. Invoked by orchestrator during IMPLEMENT phase.
tools: [Read, Write, Edit, Bash]
disallowedTools: [WebSearch, WebFetch]
model: claude-sonnet-4-6
---
Read all target files in full. Write failing tests first. Implement until tests
pass. Run build. Fix all errors. Return: files changed, tests written, build
status, repeat-bug flags. Never write placeholder code. One task only.
```

### IMPLEMENT Output Block

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
IMPLEMENT COMPLETE — {DATE} {TIME}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Implementer score: {SCORE} ({MODEL})
Tasks completed:   ✓ [list with brief result]
Tasks deferred:    [list with reason or "None"]
Repeat bugs fixed: [list fingerprints or "None"]
Files modified:    [list]
Files created:     [list]
Tests written:     {N} new test cases across {N} files
Prod build:        PASS
Prod tests:        PASS ({N} passed | {N} skipped)
Markers:           CLEAN | FOUND → [locations]
Commit:            {COMMIT_HASH}
Known risks:       [anything the TEST phase should scrutinize closely]
Next phase:        TEST
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

**STOP. `/clear`. Start a clean session for TEST.**

---

## PHASE 4 — TEST

**Purpose:** Full validation against the mirror environment. This phase is the
adversarial gate — the tester agent earns points for finding what the implementer
missed. Bugs found here penalize the implementer. Bugs that reach prod penalize
both. Nothing touches production until this phase exits clean.

### Steps

```
1. READ CYCLE-TASKS.md — TEST PLAN section specifically:
   cat {PROJECT_ROOT}/CYCLE-TASKS.md

2. READ IMPLEMENT output block (confirm commit hash to apply to mirror):
   Confirm IMPLEMENT result = PASS before proceeding.

3. CHECK tester agent score and model:
   bash scripts/cqs-report.sh | grep tester
   Note: tester at Haiku tier has outputs reviewed before scoring events fire.

4. VERIFY mirror drift:
   bash scripts/mirror-drift-check.sh
   → If drift: bash scripts/mirror-sync.sh, re-check

5. APPLY implement commit to mirror:
   git checkout mirror-staging
   git merge {IMPLEMENT_COMMIT_HASH} --no-ff -m "mirror: {CYCLE_ID} for testing"
   docker compose -f docker-compose.mirror.yml up -d --force-recreate

6. UNIT AND INTEGRATION TESTS on mirror:
   docker compose -f docker-compose.mirror.yml run --rm app-mirror {TEST_CMD}
   Record: {N} passed | {N} failed | {N} skipped

7. RUNTIME EXECUTION TESTS — actually invoke the built code:
   For each item in TEST PLAN > Runtime checks:
     Execute the exact command against mirror
     Capture actual output
     Assert actual == expected
     Record: PASS | FAIL (actual vs expected)

   Example:
     TEST: POST /api/trigger returns 202 with job_id field
     CMD:  curl -s -X POST http://localhost:{MIRROR_PORT}/api/trigger \
             -H "Content-Type: application/json" \
             -d '{"task":"smoke-test"}' | jq .
     EXPECTED: {"status": 202, "job_id": "<uuid>"}
     ACTUAL: {captured output}
     RESULT: PASS | FAIL

8. LOGIC ASSERTIONS — verify business rules:
   For each assertion in TEST PLAN > Logic assertions:
     Run input → capture output → verify business rule holds
     Any deviation = FAIL with exact delta described

9. EDGE CASE TESTS:
   - Empty / null / missing required fields
   - Boundary values (max, min, exactly-at-limit)
   - Concurrent calls (if async system)
   - Auth failure paths (invalid token, expired, missing)
   - Dependency failure (kill dep container, confirm graceful error + log)

10. REPEAT BUG VERIFICATION:
    For each repeat bug fixed in IMPLEMENT:
      Confirm the specific input that triggered the bug no longer triggers it
      Confirm the fix did not introduce a regression in adjacent code

11. SANITY CHECK — happy path end-to-end:
    {SANITY_CMD against mirror}
    Must pass before REVIEW is permitted.

12. SECURITY SCAN:
    docker compose -f docker-compose.mirror.yml run --rm app-mirror \
      bash -c "grep -rn 'password\|secret\|api_key\|token' /app/{SRC_DIR} \
        --include='*.py' --include='*.ts' --include='*.js' \
        | grep -vE '\.env|example|test|mock' || echo 'Clean'"

13. RECORD SCORING EVENTS:
    For each bug found (verify against objective bug definition first):
      bash scripts/cqs-bug-register.sh {file} {func} {class} {desc}
      If exit 0 (new bug):
        bash scripts/cqs-score.sh tester BUG_FOUND +10 "{desc}" "{file:line}"
        bash scripts/cqs-score.sh implementer BUG_INTRODUCED -10 "{desc}" "{file:line}"
      If exit 2 (repeat bug):
        bash scripts/cqs-score.sh tester REPEAT_BUG_FOUND +10 "{desc}" "{file:line}"
        bash scripts/cqs-score.sh implementer REPEAT_BUG -20 "{desc}" "{file:line}"

    After scoring, check for suspended agents:
      bash scripts/cqs-report.sh | grep suspended
      If any agent suspended: notify n8n, halt CQL for that agent until human resets

14. EVALUATE — all must be true for TEST to PASS:
    [ ] Unit/integration tests:    0 failures
    [ ] Runtime execution tests:   all assertions met
    [ ] Logic assertions:          all business rules confirmed
    [ ] Edge cases:                PASS or DOCUMENTED (with tracking issue)
    [ ] Sanity check:              PASS
    [ ] Security scan:             CLEAN
    [ ] Repeat bugs:               confirmed non-recurring

    If ANY fail: TEST FAILS
      → Write [TEST-FAIL] carry-overs to CYCLE-TASKS.md
      → Revert mirror: git checkout mirror-staging && git reset --hard HEAD~1
      → Return to IMPLEMENT in new clean session
      → DO NOT proceed to REVIEW
```

### Tester Challenge Mechanic

If the tester agent disputes the implementer's IMPLEMENT COMPLETE claim:

```
1. Write challenge to {PROJECT_ROOT}/.challenges/{CYCLE_ID}-{slug}.md:
   ── CHALLENGE ───────────────────────────────
   Challenger: tester
   Challenged: implementer
   Claim:      "IMPLEMENT COMPLETE — {specific claim being disputed}"
   Evidence:   {file:line or exact test output showing the claim is false}
   Requested:  {what outcome is expected if challenge is upheld}
   ────────────────────────────────────────────

2. The orchestrator arbitrates — reads both IMPLEMENT output and challenge evidence
3. If upheld: bash scripts/cqs-score.sh tester CHALLENGE_WON +8 "..."
              bash scripts/cqs-score.sh implementer CHALLENGE_LOST -8 "..."
4. If dismissed: bash scripts/cqs-score.sh tester CHALLENGE_DISMISSED -8 "..."
5. All outcomes logged to challenge_log table
```

### TEST Output Block

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
TEST COMPLETE — {DATE} {TIME}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Tester score:      {SCORE} ({MODEL})
Mirror:            ALIGNED | SYNCED
Unit/Integration:  {N} passed | {N} failed | {N} skipped
Runtime checks:    {N} PASS | {N} FAIL
  FAIL details:    [input → expected → actual for each failure]
Logic assertions:  {N} PASS | {N} FAIL
  FAIL details:    [rule violated → actual behavior]
Edge cases:        {N} PASS | {N} FAIL | {N} DOCUMENTED
Repeat bugs:       {N} confirmed fixed | {N} still recurring
Sanity check:      PASS | FAIL
Security scan:     CLEAN | ISSUES → [list]
Scoring events:    [list bugs found with points awarded/deducted]
Challenges filed:  [list or "None"]
Overall result:    PASS → proceed to REVIEW
                   FAIL → return to IMPLEMENT (new clean session)
                          [list carry-overs added to CYCLE-TASKS.md]
Next phase:        REVIEW (if PASS) | IMPLEMENT (if FAIL)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

**STOP. `/clear`. Start a clean session for REVIEW.**

---

## PHASE 5 — REVIEW

**Purpose:** Final validation gate. Confirm DONE condition is met. Verify the
full checklist. Merge mirror-staging to main. Push to prod. Run prod sanity.
Record final scoring events. Write HANDOFF.md. Close the cycle.

### Steps

```
1. CONFIRM TEST result = PASS in prior output block.
   If TEST result = FAIL: STOP — do not run REVIEW.

2. CONFIRM DONE condition from CYCLE-TASKS.md:
   State explicitly: DONE CONDITION MET: YES | NO
   If NO: STOP, list what is missing, return to IMPLEMENT.

3. FINAL MIRROR VALIDATION:
   docker compose -f docker-compose.mirror.yml run --rm app-mirror \
     bash -c "{BUILD_CMD} && {TEST_CMD}"
   Must exit 0.

4. COLLECT doc changes:
   cat {PROJECT_ROOT}/.doc-changes/{CYCLE_ID}.md \
     || echo "No doc changes this cycle"

5. FULL REVIEW CHECKLIST — all boxes required:
   [ ] DONE condition:             MET
   [ ] Unit/integration tests:     PASS on mirror
   [ ] Runtime execution tests:    PASS
   [ ] Logic assertions:           PASS
   [ ] Edge cases:                 PASS or DOCUMENTED with issue tracker ref
   [ ] Sanity check (mirror):      PASS
   [ ] Security scan:              CLEAN
   [ ] No TODO/FIXME/INCOMPLETE/PLACEHOLDER in source
   [ ] All external calls:         error handling + timeout present
   [ ] All config:                 externalized — no hardcoded env values
   [ ] Doc changes:                applied on mirror-staging
   [ ] AUDIT.md:                   CRITICAL items resolved or carry-overed
   [ ] OUT OF SCOPE items:         not implemented (no scope creep)
   [ ] Bug registry:               updated for all bugs found/fixed this cycle
   → If any box unchecked: STOP and resolve before merging

6. MERGE to main:
   git checkout {BRANCH}
   git merge mirror-staging --no-ff \
     -m "release({CYCLE_ID}): {DONE_CONDITION}"
   git push origin {BRANCH}

7. SYNC mirror after push:
   bash scripts/mirror-sync.sh

8. PROD SANITY CHECK:
   {SANITY_CMD against prod endpoint}
   → If fail: git revert HEAD && git push — then investigate

9. RECORD CYCLE SCORING EVENTS:
   If prod sanity passes AND zero bugs found in TEST:
     bash scripts/cqs-score.sh implementer CLEAN_CYCLE +5 "cycle {CYCLE_ID}"
     bash scripts/cqs-score.sh tester      CLEAN_CYCLE +3 "cycle {CYCLE_ID}"

   If this is a 3-cycle clean streak (check cqs-report clean_cycles field):
     bash scripts/cqs-score.sh implementer CLEAN_STREAK +10 "3-cycle streak"
     bash scripts/cqs-score.sh tester      CLEAN_STREAK +5  "3-cycle streak"

   Check if decay is due (every 10 cycles):
     If {CYCLE_COUNT} % 10 == 0:
       curl -X POST "${N8N_WEBHOOK_URL}/pcirt-decay" \
         -d "{\"project\":\"${PROJECT_SLUG}\"}"

10. WRITE HANDOFF.md (overwrite — see format below):

11. NOTIFY cycle complete:
    curl -s -X POST "${N8N_WEBHOOK_URL}/pcirt-cycle-complete" \
      -H "Content-Type: application/json" \
      -d "{\"cycle\":\"${CYCLE_ID}\",\"result\":\"PASS\",
           \"project\":\"${PROJECT_SLUG}\"}"
```

### HANDOFF.md Format

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
HANDOFF REPORT
Project:     {PROJECT_NAME}
Cycle ID:    {CYCLE_ID}
Completed:   {DATE} {TIME}
Branch:      {BRANCH}
Commit:      {COMMIT_HASH}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

DONE CONDITION
Criterion:   {DONE_CONDITION}
Met:         YES
Notes:       {caveats or "None"}

COMPLETED THIS CYCLE
✓ {task 1} — {brief result}
✓ {task 2} — {brief result}

CARRY-OVERS → Priority in next PULL phase
- [CARRY-OVER] {item}: {why deferred}
... or "None"

TEST RESULTS
Unit/Integration:  {N} passed | {N} failed
Runtime checks:    {N} PASS | {N} FAIL
Logic assertions:  {N} PASS | {N} FAIL
Edge cases:        {N} PASS | {N} DOCUMENTED
Sanity (mirror):   PASS
Sanity (prod):     PASS

SCORING EVENTS THIS CYCLE
Implementer: {score} ({delta this cycle}: {events summary})
Tester:      {score} ({delta this cycle}: {events summary})
Bugs found:  {N} new | {N} repeat | {N} fixed-repeat-confirmed
Clean cycle: YES | NO
Streak:      {N} consecutive clean cycles

AUDIT STATUS
CRITICAL resolved:  {N}/{N}
HIGH resolved:      {N}/{N}
Carried forward:    {N} items tagged [AUDIT] in next CYCLE-TASKS.md

KNOWN ISSUES
- {issue}: {impact} — {tracking reference}
... or "None"

ENV / INFRA CHANGES
- {new env var / service / port / image change}
... or "None"

DOC CHANGES
- {file}: {function} — {what was updated}
... or "None"

NEXT CYCLE SEED
Suggested priority:
- Priority 1: {highest impact item}
- Priority 2: {second item}
- Priority 3: {third item}
Level: HIGH | MEDIUM | LOW

FILES MODIFIED THIS CYCLE
{git diff --name-only HEAD~1}

PROJECT STATE SNAPSHOT
project_root:    {PROJECT_ROOT}
src_dir:         {SRC_DIR}
branch:          {BRANCH}
mirror_branch:   mirror-staging
build_cmd:       {BUILD_CMD}
test_cmd:        {TEST_CMD}
sanity_cmd:      {SANITY_CMD}
dep_audit_cmd:   {DEP_AUDIT_CMD}
migrate_cmd:     {MIGRATE_CMD}
tech_stack:      {summary}
cycle_count:     {N}
score_db_port:   {SCORE_DB_PORT}
cql_agents:      audit | break-fix | doc
n8n_workflow:    PCIRT-Framework-Orchestrator
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

---

## HOOKS

Save to `.claude/hooks/`. All files must be `chmod +x`.

**`pre-commit-credentials.sh`:**
```bash
#!/usr/bin/env bash
# PreToolUse — Bash: blocks git commits containing credential files
if echo "$TOOL_INPUT" | grep -q "git commit"; then
  STAGED=$(git diff --cached --name-only 2>/dev/null)
  if echo "$STAGED" | grep -qE '\.(env|key|pem|p12|pfx|crt)$|creds|secrets'; then
    echo "BLOCKED: credential file staged — remove from staging and use .env" >&2
    echo "Files: $STAGED" >&2
    exit 2
  fi
fi
exit 0
```

**`pre-commit-no-placeholders.sh`:**
```bash
#!/usr/bin/env bash
# PreToolUse — Bash: blocks commits with TODO/FIXME/INCOMPLETE/PLACEHOLDER
if echo "$TOOL_INPUT" | grep -q "git commit"; then
  MARKERS=$(git diff --cached -U0 2>/dev/null \
    | grep -E '^\+.*(TODO|FIXME|INCOMPLETE|PLACEHOLDER)')
  if [ -n "$MARKERS" ]; then
    echo "BLOCKED: unresolved markers in staged changes:" >&2
    echo "$MARKERS" >&2
    exit 2
  fi
fi
exit 0
```

**`pre-prod-push-guard.sh`:**
```bash
#!/usr/bin/env bash
# PreToolUse — Bash: blocks push to main/prod without TEST COMPLETE in HANDOFF.md
if echo "$TOOL_INPUT" | grep -qE 'git push.*origin.*(main|master|prod)'; then
  ROOT=$(git rev-parse --show-toplevel 2>/dev/null)
  if ! grep -q "TEST COMPLETE" "${ROOT}/HANDOFF.md" 2>/dev/null; then
    echo "BLOCKED: no TEST COMPLETE in HANDOFF.md — run TEST phase first" >&2
    exit 2
  fi
  if grep -q "Overall result:.*FAIL" "${ROOT}/HANDOFF.md" 2>/dev/null; then
    echo "BLOCKED: TEST phase result was FAIL — fix failures before pushing" >&2
    exit 2
  fi
fi
exit 0
```

**`pre-mirror-staging-guard.sh`:**
```bash
#!/usr/bin/env bash
# PreToolUse — Bash: mirror-staging written only by TEST phase
if echo "$TOOL_INPUT" | grep -qE 'git push.*mirror-staging'; then
  if [ "${PCIRT_PHASE:-}" != "TEST" ]; then
    echo "BLOCKED: mirror-staging is managed by the TEST phase only" >&2
    echo "Set PCIRT_PHASE=TEST if you are intentionally running TEST operations" >&2
    exit 2
  fi
fi
exit 0
```

**`notify-phase-complete.sh`:**
```bash
#!/usr/bin/env bash
# PostToolUse: desktop + n8n notification on phase output blocks
OUTPUT="${TOOL_OUTPUT:-}"
PHASE=$(echo "$OUTPUT" \
  | grep -oE 'PULL COMPLETE|CONFIGURE COMPLETE|IMPLEMENT COMPLETE|TEST COMPLETE|HANDOFF REPORT' \
  | head -1)
if [ -n "$PHASE" ]; then
  notify-send "PCIRT+" "$PHASE — ${PROJECT_SLUG:-project}" 2>/dev/null \
    || osascript -e "display notification \"$PHASE\" with title \"PCIRT+\"" 2>/dev/null
  [ -n "${N8N_WEBHOOK_URL:-}" ] && curl -s -X POST \
    "${N8N_WEBHOOK_URL}/pcirt-phase-complete" \
    -H "Content-Type: application/json" \
    -d "{\"phase\":\"$PHASE\",\"project\":\"${PROJECT_SLUG:-unknown}\"}" > /dev/null
fi
exit 0
```

**`.claude/settings.json`:**
```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          { "type": "command", "command": ".claude/hooks/pre-commit-credentials.sh" },
          { "type": "command", "command": ".claude/hooks/pre-commit-no-placeholders.sh" },
          { "type": "command", "command": ".claude/hooks/pre-prod-push-guard.sh" },
          { "type": "command", "command": ".claude/hooks/pre-mirror-staging-guard.sh" }
        ]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          { "type": "command", "command": ".claude/hooks/notify-phase-complete.sh" }
        ]
      }
    ]
  }
}
```

---

## SLASH COMMANDS

Save each to `.claude/commands/`. One file per command.

**`pull.md`:** Begin PULL phase. Sync repo, read handoff + audit findings, produce CYCLE-TASKS.md with correct priority structure. Check agent scores. Write PULL output block. Do not start CONFIGURE in this session.

**`configure.md`:** Begin CONFIGURE phase. Read CYCLE-TASKS.md. Verify prod AND mirror environments. Run drift check. Scaffold new files (headers only). Validate both builds. Write CONFIGURE output block. No implementation code.

**`implement.md`:** Begin IMPLEMENT phase. TDD mandatory — write failing tests before implementation. Read CYCLE-TASKS.md and HANDOFF.md first. Process tasks in priority order. Run build+test after each task. Check bug registry for AUDIT items. Write IMPLEMENT output block.

**`test.md`:** Begin TEST phase. Read CYCLE-TASKS.md TEST PLAN. Apply commit to mirror. Run all 14 steps — unit, runtime execution, logic, edge cases, repeat-bug verification, sanity, security scan. Record scoring events. Write TEST output block. If FAIL: revert mirror-staging, add carry-overs, stop.

**`review.md`:** Begin REVIEW phase. Confirm TEST = PASS. Complete all 11 steps including full checklist, merge to main, prod push, prod sanity, cycle scoring events, decay check. Write HANDOFF.md. Notify n8n.

**`status.md`:** Show full current state — CYCLE-TASKS.md, HANDOFF.md tail, AUDIT.md tail, CQS scores. Summarize: current phase, DONE condition, open audit findings, agent scores and tiers, next action.

**`scores.md`:** Print CQS score report. Show all agent scores, model tiers, trust tiers, bugs introduced/caught, clean cycles, challenges. Highlight any suspended agents.

**`challenge.md`:** File a formal challenge. Requires $ARGUMENTS = "challenger:challenged:claim:evidence". Write to .challenges/ and log to challenge_log table.

**`audit.md`:** Manually trigger audit agent protocol. Inject current scores into context. Write findings to AUDIT.md. Add CRITICAL/HIGH to CYCLE-TASKS.md. No source modifications.

---

## CLAUDE.md UNIVERSAL REFERENCE BLOCK

Add this to every project's `CLAUDE.md`. Replace placeholders.

```markdown
## FRAMEWORK

This project uses the PCIRT+ Universal Development Framework (FRAMEWORK.md).
This block is the active reference — FRAMEWORK.md is the full specification.

### Identity
Project:   {PROJECT_NAME}
Slug:      {PROJECT_SLUG}
Branch:    {BRANCH}
Root:      {PROJECT_ROOT}
Src:       {SRC_DIR}

### Commands
Build:     {BUILD_CMD}
Test:      {TEST_CMD}
Sanity:    {SANITY_CMD}
Dep audit: {DEP_AUDIT_CMD}
Migrate:   {MIGRATE_CMD}

### Planned Loop (PCIRT+)
Phases:    /pull → /configure → /implement → /test → /review
Handoff:   HANDOFF.md (project root — read at start of every session)
Tasks:     CYCLE-TASKS.md (written by PULL, consumed by all phases)
Rule:      One phase per session. /clear between every phase.

### Continuous Quality Loop (CQL)
Triggers:  git push | cron (03:00 daily) | patch .APPROVED
Agents:    audit (Tier 1) | break-fix (Tier 2→3) | doc (Tier 3)
Findings:  AUDIT.md → feeds CYCLE-TASKS.md Priority 1 in next PULL
HITL:      break-fix patches require .APPROVED file — mechanical gate

### Competitive Quality System (CQS)
Scores:    PostgreSQL score-db container (port {SCORE_DB_PORT})
Report:    bash scripts/cqs-report.sh
Score CLI: bash scripts/cqs-score.sh <agent> <event> <pts> <desc> [evidence]
Bug reg:   bash scripts/cqs-bug-register.sh <file> <func> <class> <desc>
Decay:     runs every 10 cycles via n8n

### Trust Ladder
Tier 1 (audit):        Read-only. No file writes. No bash side effects.
Tier 2 (break-fix s1): Propose only. Writes to .patches/ exclusively.
Tier 3 (break-fix s2, doc): Mirror only. No prod access. No git push.
Tier 4 (prod):         YOU. After REVIEW confirms mirror PASS.

### Scoring Quick Reference
Bug found by tester:         Tester +10, Implementer -10
Repeat bug found by tester:  Tester +10, Implementer -20
Bug reaches prod:            Implementer -30, Tester -20, Orchestrator -10
Clean cycle:                 Implementer +5, Tester +3
3-cycle streak:              Implementer +10, Tester +5
Doc error caught by audit:   Audit +5, Doc -10
Score ≥ 85 → Opus | 65–84 → Sonnet | 40–64 → Haiku | <40 → Suspended

### Non-Negotiable Rules
- Never push to main without TEST COMPLETE in HANDOFF.md
- Never apply break-fix patches without .APPROVED file
- Never skip the mirror — all changes validated there first
- TDD mandatory — tests written before implementation
- Credentials never in source — hooks enforce at Claude Code layer
- Audit findings enter planned loop via CYCLE-TASKS.md Priority 1 only
- Bug definition is objective — style issues are not bugs
```

---

## QUICK REFERENCE

### Planned Loop

| Phase | Session | Reads | Produces | Mirror | Commits |
|-------|---------|-------|----------|--------|---------|
| PULL | Clean #1 | HANDOFF.md, AUDIT.md | CYCLE-TASKS.md | sync | optional |
| CONFIGURE | Clean #2 | CYCLE-TASKS.md | verified scaffold | drift check | scaffold |
| IMPLEMENT | Clean #3 | CYCLE-TASKS.md, HANDOFF.md | source + tests | none | feature |
| TEST | Clean #4 | CYCLE-TASKS.md TEST PLAN | scoring events | apply+test | none |
| REVIEW | Clean #5 | TEST output + checklist | HANDOFF.md | final verify | merge main |

### Continuous Quality Loop

| Agent | Trigger | Tier | Produces | HITL |
|-------|---------|------|----------|------|
| audit | push / cron | 1 (read-only) | AUDIT.md entries, CYCLE-TASKS.md Priority 1 | None |
| break-fix s1 | post-audit CRITICAL/HIGH | 2 (propose-only) | .patches/*.patch + .proposal.md | Required |
| break-fix s2 | .APPROVED file detected | 3 (mirror-only) | mirror-staging changes | None (approved) |
| doc | post-IMPLEMENT commit | 3 (mirror-only) | docstrings, README, CHANGELOG | None |

### Scoring At a Glance

| Score | Model | Trust | Effect |
|-------|-------|-------|--------|
| ≥ 85 | Opus | 3 | Extended autonomy, can spawn sub-agents |
| 65–84 | Sonnet | 2 | Normal operations |
| 40–64 | Haiku | 1 | Outputs reviewed before acting |
| < 40 | Suspended | 0 | Human intervention required |

### File Layout

```
{PROJECT_ROOT}/
├── FRAMEWORK.md              ← this document
├── HANDOFF.md                ← current cycle handoff (overwritten each REVIEW)
├── CYCLE-TASKS.md            ← current cycle tasks (overwritten each PULL)
├── AUDIT.md                  ← appended each CQL audit run (never truncated)
├── CHANGELOG.md              ← appended each REVIEW
├── docker-compose.mirror.yml
├── .env.mirror.example
├── .env.mirror               ← gitignored — test values only
├── scripts/
│   ├── mirror-sync.sh
│   ├── mirror-drift-check.sh
│   ├── cqs-schema.sql
│   ├── cqs-score.sh
│   ├── cqs-report.sh
│   └── cqs-bug-register.sh
├── .patches/                 ← break-fix proposals and approvals
├── .doc-changes/             ← doc agent output per cycle
├── .challenges/              ← challenge filings
├── .claude/
│   ├── settings.json
│   ├── agents/
│   │   └── implementer.md
│   ├── commands/
│   │   ├── pull.md
│   │   ├── configure.md
│   │   ├── implement.md
│   │   ├── test.md
│   │   ├── review.md
│   │   ├── status.md
│   │   ├── scores.md
│   │   ├── challenge.md
│   │   └── audit.md
│   └── hooks/
│       ├── pre-commit-credentials.sh
│       ├── pre-commit-no-placeholders.sh
│       ├── pre-prod-push-guard.sh
│       ├── pre-mirror-staging-guard.sh
│       └── notify-phase-complete.sh
├── .openclaw/
│   └── agents/{PROJECT_SLUG}/
│       ├── audit.md
│       ├── break-fix.md
│       └── doc.md
└── n8n/
    └── PCIRT-Framework-Orchestrator.json
```

### Cycle ID Format
`YYYY-MM-DD-NNN` where NNN is zero-padded cycle number (001, 002, …)

### Context Overflow
If any phase hits context limits: use implementer sub-agent pattern (IMPLEMENT phase).
If sub-agents also overflow: split tasks across two cycles, note in OUT OF SCOPE.

### Agent Suspension Recovery
1. Human reviews suspension reason (bash scripts/cqs-report.sh)
2. Human investigates root cause of score drop
3. Human resets to 50: psql ... -c "UPDATE agent_scores SET current_score=50,
   model_tier='haiku', trust_tier=1 WHERE project_slug='$SLUG' AND agent_name='$AGENT';"
4. Agent runs 3 clean cycles at Haiku/Tier 1 before normal decay resumes
5. Score naturally rises through clean runs and bug catches
```
