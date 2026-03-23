---
name: require-completion-gate
enabled: true
event: stop
action: block
pattern: .*
---

🚫 **STOP — Completion Gate Not Confirmed**

Before ending this session or marking any task complete, verify ALL of the following:

**1. Smoke tests passed**
```bash
ssh root@187.77.208.197 'cd /opt/agentic-sdlc && bash scripts/smoke_test.sh'
```
Must exit 0 with zero failures.

**2. All touched nginx upstreams return non-502/503/000**
- Run `/stack-validate` or manually curl each proxy path you changed
- WebSocket paths: verify `/negotiate` endpoint reachable (not 502)

**3. End-to-end test — not just "code is present"**
- Network path change → curl through the full proxy chain
- WebSocket → verify negotiate endpoint responds
- Credentials added → verify consuming service can read them
- n8n/Notion integration → trigger the workflow, confirm output
- UI change → Playwright or browser screenshot confirms it renders

**4. CLI AND browser/GUI tested** (where a UI is involved)

**If you have not run these checks, do not stop. Run them now.**

Only proceed past this gate if you can state explicitly which tests were run and what they returned.
