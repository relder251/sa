# OpenClaw Agent Team — agentic-sdlc

## What Is OpenClaw?

OpenClaw is the **3-agent Continuous Quality Loop (CQL)** embedded in the FRAMEWORK. It runs
autonomously after every git push and on a daily cron schedule, finding bugs, proposing patches,
and keeping documentation in sync — all with a human-in-the-loop gate before any code changes
reach production.

The three agents are:

| Agent | Trust Tier | Role |
|-------|-----------|------|
| `audit` | 1 (read-only) | Scans source for security, logic, doc, and dependency issues. Writes to AUDIT.md and CYCLE-TASKS.md. Never modifies source. |
| `break-fix` | 2 → 3 | Stage 1: proposes patches to `.patches/` and halts. Stage 2: applies approved patches to `mirror-staging` branch and runs tests. |
| `doc` | 3 | Syncs docstrings, README, and CHANGELOG against actual source after every IMPLEMENT commit or break-fix MIRROR_PASS. |

Each agent definition (`audit.md`, `break-fix.md`, `doc.md`) contains:
- A YAML front-matter header with `name`, `trust_tier`, `model`, `tools`, and shell allow/deny lists.
- A scoring context block (injected at runtime by n8n from the CQS registry).
- The agent's operating protocol.

## How to Trigger

**Automatic — git push:**
Every push to the VPS bare repo (or after `git pull` via the post-merge hook) fires the
`pcirt-push` webhook at `http://localhost:5678/webhook/pcirt-push`. The PCIRT Framework
Orchestrator workflow receives the event, injects the current CQS scores, and runs the
audit agent.

**Automatic — daily cron:**
The PCIRT orchestrator includes a cron node that fires the full audit at 03:00 regardless
of whether a push occurred.

**Manual:**
POST to the n8n webhook directly:
```bash
curl -X POST http://localhost:5678/webhook/pcirt-push \
  -H "Content-Type: application/json" \
  -d '{"trigger":"manual","commit":"HEAD","branch":"master","project":"agentic-sdlc"}'
```

## How to Approve a Break-Fix Patch

When the audit agent finds a CRITICAL or HIGH severity issue, the break-fix agent automatically
runs Stage 1 and writes two files under `.patches/`:

- `{CYCLE_ID}-{slug}.patch` — the git diff–format patch
- `{CYCLE_ID}-{slug}.proposal.md` — risk assessment, test plan, and rollback command

To approve a patch for Stage 2 (apply to mirror branch):
```bash
touch .patches/{CYCLE_ID}-{slug}.APPROVED
```

To reject:
```bash
touch .patches/{CYCLE_ID}-{slug}.REJECTED
```

The n8n Patch Approval Watcher node polls for `.APPROVED` files and automatically triggers
break-fix Stage 2 when one appears.

**Important:** Never manually apply a patch to production. The HITL gate exists specifically
to prevent unapproved code from reaching the live stack.

## How Scores Route to Models

Each agent starts at score 70 (Sonnet, trust tier 2) when a project is initialized. The CQS
scoring system adjusts scores up or down based on outcomes:

| Score range | Model tier assigned |
|-------------|-------------------|
| 90–100 | Opus (highest capability) |
| 70–89 | Sonnet (default) |
| 50–69 | Haiku (degraded) |
| < 50 | Haiku + flag for human review |
| < 40 | Agent suspended — human reset required |

See FRAMEWORK.md §0.3 (CQS Score Registry) for the full scoring table, decay rules, and
the `cqs_apply_decay()` postgres function that applies periodic score reduction.

The `${AUDIT_MODEL}`, `${BREAKFIX_MODEL}`, and `${DOC_MODEL}` placeholders in the agent
front-matter are replaced at runtime by the n8n "Inject Score Context" node, which queries
the `agent_scores` table and substitutes the current model tier for the project.

## Directory Layout

```
.openclaw/
└── agents/
    └── agentic-sdlc/
        ├── audit.md        ← read-only audit agent
        ├── break-fix.md    ← two-stage patch agent
        ├── doc.md          ← documentation sync agent
        └── README.md       ← this file

.patches/                   ← patch proposals, approvals, mirror results
.doc-changes/               ← doc sync summaries per cycle
AUDIT.md                    ← append-only audit log
CYCLE-TASKS.md              ← priority-ordered task queue for current cycle
```
