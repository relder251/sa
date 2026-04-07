# Refactor: scripts/

**Date:** 2026-03-20
**Refactored by:** Claude Sonnet 4.6

---

## Directory Overview

| Property | Value |
|---|---|
| **Path** | `scripts/` |
| **Purpose** | Operational scripts: pipeline servers, deployment, backups, Keycloak bootstrap, Twingate management, lead review PDF generation |

### File inventory

| File | Purpose |
|---|---|
| `pipeline_server.py` | Core FastAPI server — runs Phases 1–10 of the AI pipeline |
| `test_runner_server.py` | Flask server — runs Phase 3/4 (test + fix loop) as a sidecar |
| `lead_review_server.py` | FastAPI server — lead review portal API (port 5003) |
| `lead_pdf_generator.py` | ReportLab PDF generator — called by lead_review_server |
| `backup.sh` | Daily/weekly PostgreSQL backup with atomic write + retention |
| `backup_test.sh` | Restore-validates backup into throwaway Postgres container |
| `deploy.sh` | git pull + docker compose up -d on remote host |
| `deploy-nginx.sh` | GitOps nginx deploy with chattr immutable flag pattern |
| `deploy-sso.sh` | SSO activation: secret generation, OIDC verify |
| `deploy_workflow.sh` | Import n8n workflow + sync published version via n8n API |
| `pipeline_smoke_test.sh` | End-to-end smoke test: POST → poll → per-phase pass/fail |
| `keycloak_bootstrap.py` | Idempotent realm/client/user bootstrap for Keycloak |
| `keycloak_export_realm.sh` | Export realm to keycloak/realm-export.json (strips secrets) |
| `keycloak_portal_bootstrap.py` | Creates/updates the `portal` OIDC client in Keycloak |
| `extract_files.js` | Parses `===FILE:===` blocks from AI output; writes files to disk |
| `extract_files.py` | Python version of extract_files.js |
| `postprocess.js` | Strips stdlib from requirements.txt; creates conftest.py |
| `validate_format.js` | Validates AI executor output format before extraction |
| `opportunity_intake.js` | Moves opportunity files pending→running; outputs path JSON |
| `portal_docker_up.sh` | Starts a container on vibe_net via docker run |
| `portal_nginx_reload.sh` | nginx -t + nginx -s reload inside sa_nginx_private |
| `twingate/twingate_add_resource.py` | Adds Twingate resource via GraphQL Admin API |
| `twingate/twingate_connector_rotate.py` | Provisions/rotates Twingate connector + updates .env |
| `twingate/add-twingate-resource.sh` | Wrapper: sources .env → calls twingate_add_resource.py |
| `twingate/rotate-twingate-connector.sh` | Wrapper: sets defaults → calls twingate_connector_rotate.py |
| `twingate-guard/twingate_connector_guard.sh` | Systemd-run guard: monitors connector logs, rotates on token expiry |
| `twingate-guard/install-twingate-guard.sh` | Installs guard + systemd timer |
| `templates/lead_review.html` | SPA template for lead review portal (served by lead_review_server) |
| `requirements-lead-review.txt` | Pinned deps for Dockerfile.lead_review |
| `Dockerfile.lead_review` | Builds lead_review_server image |

---

## Gaps Found

| # | File | Gap | Severity | Description |
|---|---|---|---|---|
| 1 | `deploy_workflow.sh` | N8N_API_KEY JWT hardcoded in file | **High** | Long-lived JWT (exp ~2027) was committed to git. Anyone with repo access had full n8n API access. |
| 2 | `deploy_workflow.sh` | `WF_ID` hardcoded | **Medium** | If the workflow is recreated in n8n, the script silently deploys to a dead workflow ID. |
| 3 | `deploy_workflow.sh` | Direct `psql` manipulation of n8n internal DB | **Medium** | Bypasses n8n APIs to sync published version. No public API exists for this operation. Documented. |
| 4 | `test_runner_server.py` | Missing version-loosening pass in `run_postprocess` | **Medium** | `pipeline_server.py` was fixed to loosen `pkg==X.Y.Z` → `pkg>=X.Y`. Same fix missing here. |
| 5 | `test_runner_server.py` | Code duplication with `pipeline_server.py` | **Medium** | `read_source_files`, `run_postprocess`, `call_llm_fix`, etc. copied verbatim. Will drift. Deferred. |
| 6 | `test_runner_server.py` | Missing pytest binary guard | **Low** | `pipeline_server.py` checks `if not pytest_bin.exists()` before calling it; `test_runner_server.py` did not. |
| 7 | `keycloak_bootstrap.py` | `web_origins=["*"]` on all OAuth2 proxy clients | **Low** | Every OAuth2 proxy client allowed any web origin. Should be the specific service domain. |
| 8 | `requirements-lead-review.txt` | `psycopg2-binary` listed but unused | **Low** | `lead_review_server.py` uses `asyncpg` throughout. `psycopg2-binary` added install time for no benefit. |
| 9 | `twingate/rotate-twingate-connector.sh` | Hardcoded `friendly-jaguar`, `Homelab Network` defaults | **Info** | Env-overridable; match actual deployment. Acceptable. |
| 10 | `test_runner_server.py` vs `pipeline_server.py` | Flask vs FastAPI | **Info** | Different frameworks. Not a bug, but maintenance friction. Out of scope. |

---

## Changes Made

| Change | File | Before | After | Reason |
|---|---|---|---|---|
| Remove hardcoded JWT; source from .env | `deploy_workflow.sh` | `N8N_API_KEY="eyJ..."` literal in file | Sourced from `.env` (same pattern as twingate wrappers); `${N8N_API_KEY:?...}` guard | JWT committed to git = credential in repo history |
| Require WF_ID from env | `deploy_workflow.sh` | `WF_ID="Wyc4UIvCYgByrAwP"` literal | `WF_ID="${WF_ID:?WF_ID must be set in .env}"` | Makes it explicit when WF_ID is stale after workflow recreation |
| Document direct-DB psql approach | `deploy_workflow.sh` | No comment | Added comment: n8n has no public API for this; stable across n8n v1.x; test after major upgrades | Surfaces intentional coupling; prevents future confusion |
| Add version-loosening pass | `test_runner_server.py` | `run_postprocess` only stripped stdlib + flask pin | Added `pkg==X.Y.Z` → `pkg>=X.Y` conversion | Same fix as `pipeline_server.py`; prevents pip failures on hallucinated patch versions |
| Add pytest binary guard | `test_runner_server.py` | Calls `pytest_bin` without checking existence | `if not pytest_bin.exists(): return False, "pytest binary missing..."` | Matches `pipeline_server.py` behaviour; produces clean error instead of `FileNotFoundError` |
| Narrow `web_origins` from `["*"]` | `keycloak_bootstrap.py` | `web_origins=["*"]` default for all clients | Derives origins from `redirect_uris` by parsing scheme+netloc | Least-privilege: only the actual service domain is an allowed origin |
| Remove `psycopg2-binary` | `requirements-lead-review.txt` | `psycopg2-binary==2.9.10` listed | Removed | Unused; `lead_review_server.py` uses `asyncpg` |

---

## Test Results

| Check | Result |
|---|---|
| `python3 -m py_compile scripts/test_runner_server.py` | ✅ |
| `python3 -m py_compile scripts/keycloak_bootstrap.py` | ✅ |
| `bash -n scripts/deploy_workflow.sh` | ✅ |
| Version-loosening: `pkg==1.2.3` → `pkg>=1.2` | ✅ regex matches only when patch component present (`m.group(3)`) |
| Pytest guard: missing binary produces clean error string | ✅ matches pipeline_server.py pattern |
| web_origins derivation: `https://n8n.private.sovereignadvisory.ai/*` → `["https://n8n.private.sovereignadvisory.ai"]` | ✅ urlparse strips path |

---

## Deferred Items

| Item | Notes |
|---|---|
| Deduplicate `test_runner_server.py` + `pipeline_server.py` | Large refactor — shared module or merge into one server. Both serve different endpoints (Flask `/run` vs FastAPI). Not worth merging without a clear migration plan. |
| `deploy_workflow.sh` — WF_ID in `.env.example` | Add `WF_ID=` with comment to `.env.example` so it's documented alongside N8N_API_KEY. Tracked here. |
| Remove N8N_API_KEY from git history | `git filter-repo` or BFG to scrub the old JWT. Low urgency (internal homelab), but good practice. |
