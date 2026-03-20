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
- [ ] `ofelia.ini`
- [ ] `phase_1_setup.sh`
- [ ] `free_model_sync.py`
- [ ] `litellm_config.yaml`
- [ ] `docker-compose.yml`
- [ ] `docker-compose.prod.yml`
- [ ] `docker-compose.oidc-test.yml`
- [ ] `Dockerfile.pipeline`
- [ ] `.gitignore`
- [ ] `.env.example`

### Subdirectories (pass 2)
- [x] `nginx-public/` — 2026-03-20
- [x] `nginx-private/` — 2026-03-20
- [x] `nginx/` — 2026-03-20
- [ ] `certbot/`
- [x] `postgres-init/` — 2026-03-20
- [x] `keycloak/` — 2026-03-20
- [x] `portal/` — 2026-03-20 (clean, no changes)
- [x] `scripts/` — 2026-03-20
- [x] `phases/` — 2026-03-20
- [ ] `workflows/`
- [ ] `webui/`
- [ ] `tests/`
- [ ] `notebooks/`
