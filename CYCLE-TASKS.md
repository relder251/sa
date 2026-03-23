# CYCLE-TASKS.md — Cycle 1
**Cycle ID:** C1-2026-03-23
**Phase:** 1 (PULL complete, CONFIGURE pending)
**Date:** 2026-03-23
**Branch:** master
**Project slug:** agentic-sdlc

---

## Context

This is the **first operational cycle** of the PCIRT+ Framework on this project. Phase 0 (mirror setup, CQS registry, n8n webhook, git hook) is fully complete as of 2026-03-23. All refactor backlog tasks T-01 through T-24 are verified and closed.

The current cycle focuses on **operationalizing the PCIRT+ loop** for ongoing maintenance, hardening, and feature delivery — now that the foundation is proven.

---

## Carry-overs from Prior Cycles
None (first cycle).

---

## Discovered Issues (from PULL scan)

| ID | Source | Severity | Description |
|----|--------|----------|-------------|
| I-01 | git status (VPS) | LOW | portal/services.json and scripts/post-merge.hook.sh have unstaged local changes on VPS (stashed during PULL) |
| I-02 | git status (VPS) | LOW | docs/phase-0-completion.md is untracked on VPS — not committed to repo |
| I-03 | marker scan | INFO | certbot/cloudflare.ini PLACEHOLDER is intentional (by design per refactor/certbot.md) |
| I-04 | marker scan | INFO | venv TODOs/FIXMEs are third-party library internals — not project issues |

---

## Cycle Tasks

### C1-T01 — Commit stashed VPS changes and phase-0 doc
**Priority:** HIGH
**Agent:** implementer
**Description:**
The VPS has stashed changes to portal/services.json and scripts/post-merge.hook.sh, plus an untracked docs/phase-0-completion.md. Pop the stash, review diffs, commit the clean set to master, push, and pull on VPS.
**Acceptance criteria:**
- git stash list shows no stashes on VPS
- docs/phase-0-completion.md committed and pushed to origin/master
- VPS up to date with remote

---

### C1-T02 — PCIRT cycle smoke test: validate all webhooks post-deploy
**Priority:** HIGH
**Agent:** tester
**Description:**
Run the n8n-workflow skill to confirm all webhook proxy_pass targets in nginx-private resolve to active n8n webhooks. Confirm PCIRT Orchestrator webhook responds 200.
**Acceptance criteria:**
- /stack-validate skill passes (all healthy containers confirmed)
- PCIRT webhook curl to checklist-test returns 200
- No drift detected between nginx proxy targets and active n8n workflows

---

### C1-T03 — CQS scoring: establish first score event for cycle open
**Priority:** MEDIUM
**Agent:** orchestrator
**Description:**
Record a clean-cycle event for all agents using scripts/cqs-score.sh. This marks Cycle 1 as opened and establishes a baseline event row in score_events.
**Acceptance criteria:**
- scripts/cqs-report.sh shows clean_cycles >= 1 for orchestrator agent
- score_events table has at least one row with event_type='clean_cycle' for project 'agentic-sdlc'

---

### C1-T04 — Audit: verify backup integrity (restore test)
**Priority:** HIGH
**Agent:** audit
**Description:**
The backup/ directory has archives from 2026-03-18. Verify the backup process is still working (run scripts/backup.sh or check it ran via ofelia/watchtower logs) and that the most recent backup can be listed/extracted without errors.
**Acceptance criteria:**
- A backup newer than 2026-03-18 exists OR backup.sh runs successfully without error
- tar -tzf on the output backup does not error
- Postgres dump can be decompressed: gunzip -t backup/postgres_*.sql.gz returns 0

---

### C1-T05 — Review and harden post-merge hook for PCIRT webhook trigger
**Priority:** MEDIUM
**Agent:** implementer
**Description:**
scripts/post-merge.hook.sh has unstaged local changes on VPS. Review what changed vs. committed version. Ensure the hook correctly fires the PCIRT Orchestrator webhook on post-merge. Confirm N8N_WEBHOOK_URL is set in the environment the hook runs in.
**Acceptance criteria:**
- diff between stashed version and last committed version reviewed
- Hook fires curl to N8N_WEBHOOK_URL successfully in test run
- Changes committed if they are improvements, discarded if accidental

---

### C1-T06 — Documentation: write first CYCLE-HANDOFF.md
**Priority:** MEDIUM
**Agent:** doc
**Description:**
At the close of Cycle 1, produce HANDOFF.md in the project root documenting: cycle outcome, tasks completed/deferred, any new issues discovered, and recommended focus for Cycle 2.
**Acceptance criteria:**
- /opt/agentic-sdlc/HANDOFF.md exists and contains cycle summary
- File committed and pushed to master

---

## Done Condition for Cycle 1

Cycle 1 is DONE when:
1. C1-T01: stash cleared, phase-0 doc committed
2. C1-T02: webhook smoke test passes, no drift
3. C1-T03: first clean_cycle score event recorded
4. C1-T04: backup integrity verified
5. C1-T05: post-merge hook reviewed and resolved
6. C1-T06: HANDOFF.md written and committed

---

## Agent Assignments

| Task | Agent | Status |
|------|-------|--------|
| C1-T01 | implementer | PENDING |
| C1-T02 | tester | PENDING |
| C1-T03 | orchestrator | PENDING |
| C1-T04 | audit | PENDING |
| C1-T05 | implementer | PENDING |
| C1-T06 | doc | PENDING |

---

## Notes
- All agents at score 70, sonnet tier 2 — baseline nominal.
- No CRITICAL or HIGH audit findings from PULL scan.
- AUDIT.md not present — no prior audit to carry over.
- refactor-backlog.md: all tasks VERIFIED/COMPLETED — no open items.
- Phase 0 fully complete per docs/phase-0-completion.md.
