# Refactor: tests/

**Date:** 2026-03-20
**Refactored by:** Claude Sonnet 4.6

---

## Files Reviewed

| File | Outcome |
|---|---|
| `tests/requirements.txt` | Clean — no changes |
| `tests/conftest.py` | Clean — no changes |
| `tests/test_post_deploy.py` | Clean — no changes |

---

## Gaps Found

None.

---

## Notes

- `tests/` contains post-deploy regression tests using `pytest-playwright`. Tests confirm services are reachable and authentication flows (Keycloak redirects) are working.
- `conftest.py` exposes per-service URL fixtures built from `BASE_URL` env var or explicit overrides (e.g. `N8N_URL`, `WEBUI_URL`). Pattern correctly handles both homelab (localhost) and VPS (private hostnames) topologies.
- `tests/requirements.txt` uses pinned versions — appropriate for a test harness that runs in a known environment.
- `test_post_deploy.py` is well-structured:
  - Skips gracefully when docker CLI is unavailable (backup container check)
  - Skips authenticated tests when `LEAD_REVIEW_PASSWORD` is not set
  - Skips 404-based paths for token-URL configurations
  - Fresh browser context per test prevents cookie/session bleed
- Run instructions are documented in the module docstring.

---

## Changes Made

None.

---

## Test Results

| Check | Result |
|---|---|
| Fixtures cleanly composable for homelab and VPS | ✅ |
| No secrets hardcoded | ✅ |
| Graceful skips for missing infra | ✅ |

---

## Deferred Items

None.
