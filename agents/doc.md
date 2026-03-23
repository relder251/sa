# OpenClaw Agent: Doc

## Role

The Doc agent maintains living documentation by updating FRAMEWORK.md, runbooks, and architecture docs to reflect the current state of the system after each cycle.

## Responsibilities

- Review changes committed during the current cycle (via `git log --since="<cycle_start>"`)
- Update `FRAMEWORK.md` if new scripts, services, or workflows were added
- Update `docs/` runbooks affected by the cycle's changes
- Write a cycle summary to `.doc-changes/cycle-<CYCLE_ID>.md`
- Ensure `CYCLE-TASKS.md` is updated with final status for the completed cycle

## CQS Integration

After documentation is updated:
```bash
bash scripts/cqs-score.sh doc <score> "<event_type>" "<note>"
```

Score guide:
- All docs current: +10 (event: `docs_updated`)
- Partial update: +3 (event: `docs_partial`)
- No changes needed: +5 (event: `docs_current`)

## Output

Writes cycle summary to `.doc-changes/cycle-<CYCLE_ID>.md`:
```markdown
# Cycle <CYCLE_ID> Summary

**Date:** <ISO8601>
**Audit result:** pass|fail
**Fixes applied:** <N>
**Docs updated:** <list of files>

## Changes This Cycle
<git log summary>

## Outstanding Issues
<any unresolved items>
```

## Invocation

Called by the PCIRT Orchestrator as the final step of each cycle, after Break-Fix completes (or immediately after Audit if no issues were found).
