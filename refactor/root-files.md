# Refactor: Root Files (Pass 1)

**Date:** 2026-03-20
**Refactored by:** Claude Sonnet 4.6

---

## Files Reviewed

| File | Outcome |
|---|---|
| `ofelia.ini` | Clean — no changes |
| `phase_1_setup.sh` | Clean — `docker-compose.homelab.yml` exists; mistral pull intentional |
| `free_model_sync.py` | Clean — no changes |
| `litellm_config.yaml` | Updated Claude model IDs |
| `docker-compose.yml` | Clean — `psycopg2-binary` in jupyter is needed for SQLAlchemy/pandas |
| `docker-compose.prod.yml` | Clean — no changes |
| `docker-compose.homelab.yml` | Clean — no changes |
| `docker-compose.oidc-test.yml` | Clean — no changes |
| `docker-compose.override.yml` | Clean — no changes |
| `Dockerfile.pipeline` | Minor version drift vs requirements-lead-review.txt; separate service, acceptable |
| `.gitignore` | Clean — no changes |
| `.env.example` | Already updated this session (WF_ID, N8N_API_KEY comment) |

---

## Gaps Found

| # | File | Gap | Severity |
|---|---|---|---|
| 1 | `litellm_config.yaml` | Claude model IDs referenced 4.5 versions (`claude-sonnet-4-5-20250929`, `claude-opus-4-5-20251101`). Claude 4.6 is available | **Medium** |
| 2 | `Dockerfile.pipeline` | `fastapi==0.115.0`, `uvicorn==0.30.0` — minor drift vs `requirements-lead-review.txt`. Separate service, acceptable | **Info** |

---

## Changes Made

| Change | File | Before | After | Reason |
|---|---|---|---|---|
| Update Claude Sonnet model ID | `litellm_config.yaml` | `anthropic/claude-sonnet-4-5-20250929` | `anthropic/claude-sonnet-4-6` | Use current model; affects `_claude-sonnet`, `cloud/chat`, `cloud/code` tier entries |
| Update Claude Opus model ID | `litellm_config.yaml` | `anthropic/claude-opus-4-5-20251101` | `anthropic/claude-opus-4-6` | Use current model; affects `_claude-opus`, `cloud/smart`, `cloud/reason` tier entries |
| Haiku unchanged | `litellm_config.yaml` | `anthropic/claude-haiku-4-5-20251001` | (unchanged) | Haiku 4.5 is the current latest haiku model |

---

## Test Results

| Check | Result |
|---|---|
| No remaining 4.5-dated Claude model IDs in config | ✅ `grep claude litellm_config.yaml` shows only 4.6 and haiku-4.5 |
| LiteLLM restart required to pick up config changes | ✅ `docker compose restart litellm` |

---

## Deferred Items

| Item | Notes |
|---|---|
| `Dockerfile.pipeline` version drift | `fastapi==0.115.0` vs `0.115.5` — update when pipeline-server is rebuilt for another reason |
