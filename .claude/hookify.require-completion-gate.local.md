---
name: require-completion-gate
enabled: true
event: stop
action: block
pattern: .*
---

🚫 **STOP — Run the Completion Gate First**

Before ending this session or marking any task complete, run the full validation suite:

```bash
bash scripts/validate-prod.sh
```

This runs three suites (all must exit 0):
1. **smoke_test.sh** on VPS — containers, endpoints, portal webhook roundtrip
2. **validate-upstreams.sh** on VPS — every nginx proxy_pass upstream reachable, no 502s, config drift check
3. **validate-browser.sh** locally — headless Playwright confirms key URLs render

**If `validate-prod.sh` doesn't cover what you just changed:**
Extend the relevant script first, then run it.

**Skip flags** (only when explicitly agreed with user):
- `--skip-browser` — skip Playwright (use if Twingate disconnected)
- `--skip-smoke` — skip smoke tests (use only for pure-doc changes)

Only proceed past this gate if you can state: "validate-prod.sh exited 0" or list exactly which suites were run and their exit codes.
