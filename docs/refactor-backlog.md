# Refactor Backlog — Agent Team Work Log
> Maintained by Manager Agent. Updated as tasks move through Fix → Test → Document.

## Status Legend
- 🔴 PENDING — not started
- 🟡 IN_PROGRESS — fixer working
- 🟢 FIXED — fixer done, awaiting test
- ✅ VERIFIED — tester passed
- 📄 DOCUMENTED — docs/rollback written
- ❌ BLOCKED — needs human input

---

## Task Registry

| ID  | Title | Status | Priority | Depends On |
|-----|-------|--------|----------|-----------|
| T-06 | Configurable container port in phase8_deployment.py | ✅ VERIFIED | HIGH | T-04 ✓ |
| T-07 | Phase 8 SSH deploy: graceful fallback when DOCKER_REGISTRY unset | ✅ VERIFIED | HIGH | T-06 ✓ |
| T-10 | Dedup pipeline_server + test_runner shared utilities | ✅ VERIFIED | MEDIUM | T-04 ✓, T-06 ✓, T-07 ✓ |
| T-13 | free_model_sync: remove duplicate tier group entries | ✅ VERIFIED | MEDIUM | none |
| T-14 | free_model_sync: slugify model names to avoid LiteLLM prefix clash | ✅ VERIFIED | MEDIUM | T-13 ✓ |
| T-15 | mypy: use proper tmpdir (not /tmp/mypy_cache in project root) | ✅ VERIFIED | LOW | none |
| T-16 | Dedicated Postgres users for n8n and Keycloak (separate from litellm) | ✅ VERIFIED | HIGH | none |
| T-17 | Dockerfile.pipeline: pin base image version, add upgrade step | ✅ VERIFIED | MEDIUM | none |
| T-19 | Rename phase3_report.md → phase4_report.md in phase4 code | ✅ VERIFIED | LOW | none |
| T-20 | Runbook: how to update n8n workflow IDs after import | ✅ VERIFIED | MEDIUM | T-02 ✓ |
| T-24 | Schema migration strategy for pipeline DB tables | ✅ VERIFIED | LOW | T-16 ✓ |

---

## Work Log

### [FIXER] Assignment Queue
<!-- Manager writes assignments here; Fixer picks up and marks IN_PROGRESS -->

### [TESTER] Test Results
<!-- Fixer marks FIXED; Tester verifies and records results -->

### [DOC] Documentation Output
<!-- Tester marks VERIFIED; Doc agent writes runbooks/rollback here -->

---

## Completed (all sessions)
- T-01: Credential rotation (Vaultwarden + git history rewrite) ✅
- T-02: LITELLM_API_KEY injected via envsubst in portal nginx ✅
- T-04: DOCKER_REGISTRY inline comment stripping in phase7/phase8 ✅
- T-06: Configurable DEPLOY_PORT in phase8_deployment.py ✅
- T-07: SSH deploy uses --build fallback when DOCKER_REGISTRY unset ✅
- T-08: PKCE S256 added to all oauth2-proxy services ✅
- T-09: SSL cert path mismatch fixed (docker-compose.prod.yml certbot volumes) ✅
- T-10: Shared utilities extracted to scripts/shared_utils.py ✅
- T-11: n8n late-binding in nginx-public + nginx-private ✅
- T-12: ssl_stapling added to all 9 HTTPS blocks in nginx-private ✅
- T-13: free_model_sync sync_tier_groups delete-then-recreate idempotency ✅
- T-14: free_model_sync slugify includes provider prefix ✅
- T-15: phase5 mypy uses tempfile.mkdtemp with cleanup ✅
- T-16: Dedicated Postgres users for n8n and Keycloak ✅
- T-17: Dockerfile.pipeline pinned to python:3.12.9-slim, fastapi/uvicorn updated ✅
- T-18: NOTIFY_SMS_EMAIL placeholder added to .env ✅
- T-19: phase4_report.md used throughout pipeline code ✅
- T-20: n8n workflow ID rotation runbook written (docs/runbooks/) ✅
- T-24: Schema migration tracking with schema_migrations table ✅
