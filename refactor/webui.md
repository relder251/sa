# Refactor: webui/

**Date:** 2026-03-20
**Refactored by:** Claude Sonnet 4.6

---

## Files Reviewed

| File | Outcome |
|---|---|
| `webui/Dockerfile` | Clean — no changes |
| `webui/requirements.txt` | Clean — no changes |
| `webui/main.py` | Fixed: wrong default ports for internal service URLs |
| `webui/static/` | Not reviewed (static assets) |
| `webui/templates/` | Not reviewed (Jinja2 templates) |

---

## Gaps Found

| # | File | Gap | Severity |
|---|---|---|---|
| 1 | `webui/main.py` | `PIPELINE_SERVER_URL` defaulted to port `8000`; actual service listens on `5002` | **Medium** |
| 2 | `webui/main.py` | `TEST_RUNNER_URL` defaulted to port `9000`; actual service listens on `5001` | **Medium** |

Both defaults were masked in compose deployments because `docker-compose.yml` sets `PIPELINE_SERVER_URL` and `TEST_RUNNER_URL` explicitly via environment. The wrong defaults would surface in bare `uvicorn main:app` runs (e.g. local dev without compose) or if the compose env vars were ever dropped.

---

## Changes Made

| Change | File | Before | After | Reason |
|---|---|---|---|---|
| Fix PIPELINE_SERVER_URL default port | `webui/main.py` | `http://pipeline-server:8000` | `http://pipeline-server:5002` | Match actual pipeline-server listen port |
| Fix TEST_RUNNER_URL default port | `webui/main.py` | `http://test-runner:9000` | `http://test-runner:5001` | Match actual test-runner listen port |

---

## Test Results

| Check | Result |
|---|---|
| `docker compose config` shows compose-level env vars still override defaults | ✅ (no behaviour change in compose deployments) |
| Code defaults now match actual service ports | ✅ |

---

## Deferred Items

| Item | Notes |
|---|---|
| `webui/static/` and `webui/templates/` | Static assets and Jinja2 templates not audited — frontend-only, no security surface in this pass |
