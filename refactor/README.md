# Refactor Documentation

This folder contains per-file refactor tracking documents for the Agentic SDLC stack.

## Purpose
- Reference space for changes made during systematic refactoring
- RAG source for future issue mitigation and feature development
- Audit trail of gaps found and how they were resolved

## Document Format
Each file gets its own tracking document named `<filename>.md` (e.g., `ofelia.ini.md`).

### Structure per document:
1. **File Overview** — purpose, role in stack, dependencies
2. **Gaps Found** — issues identified during review
3. **Changes Made** — what was refactored and why
4. **Test Results** — syntax checks, upstream/downstream dependency validation
5. **Final State** — summary of improvements

## Refactor Order (Root → Subdirectories)

### Root files (pass 1)
- [x] `ofelia.ini` — 2026-03-20 (clean)
- [x] `phase_1_setup.sh` — 2026-03-20 (clean)
- [x] `free_model_sync.py` — 2026-03-20 (clean)
- [x] `litellm_config.yaml` — 2026-03-20 (Claude 4.6 model IDs)
- [x] `docker-compose.yml` — 2026-03-20 (CF_API_TOKEN empty default)
- [x] `docker-compose.prod.yml` — 2026-03-20 (clean)
- [x] `docker-compose.oidc-test.yml` — 2026-03-20 (clean)
- [x] `Dockerfile.pipeline` — 2026-03-20 (minor version drift, deferred)
- [x] `.gitignore` — 2026-03-20 (clean)
- [x] `.env.example` — 2026-03-20 (WF_ID, N8N_API_KEY added)

### Subdirectories (pass 2)
- [x] `nginx-public/` — 2026-03-20
- [x] `nginx-private/` — 2026-03-20
- [x] `nginx/` — 2026-03-20
- [x] `certbot/` — 2026-03-20 (clean)
- [x] `postgres-init/` — 2026-03-20
- [x] `keycloak/` — 2026-03-20
- [x] `portal/` — 2026-03-20 (clean, no changes)
- [x] `scripts/` — 2026-03-20
- [x] `phases/` — 2026-03-20
- [x] `workflows/` — 2026-03-20 (clean)
- [x] `webui/` — 2026-03-20 (default port fixes)
- [x] `tests/` — 2026-03-20 (clean)
- [x] `notebooks/` — 2026-03-20 (clean)
