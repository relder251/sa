# Refactor: docker-compose.oidc-test.yml

**Date:** 2026-03-20
**Refactored by:** Claude Sonnet 4.6

---

## File Overview

| Property | Value |
|---|---|
| **Path** | `docker-compose.oidc-test.yml` |
| **Purpose** | Local OIDC test override — overrides `lead-review` env vars to use localhost callback URLs |
| **Role** | Merged on top of `docker-compose.yml` during local development to test OIDC flow without a VPS |
| **Loaded by** | `docker compose -f docker-compose.yml -f docker-compose.oidc-test.yml up -d --build lead-review` |
| **Upstream deps** | `docker-compose.yml` (base `lead-review` service definition) |
| **Downstream deps** | `lead-review` container only |

### What it overrides

| Env var | docker-compose.yml value | docker-compose.oidc-test.yml value | Reason |
|---|---|---|---|
| `OIDC_ENABLED` | `"true"` | `"true"` | Same — no change |
| `LEAD_REVIEW_PUBLIC_URL` | `https://sovereignadvisory.ai` | `http://localhost:5003` | Points to local port for OIDC callback |
| `KEYCLOAK_EXTERNAL_URL` | `https://kc.sovereignadvisory.ai` | `http://localhost:8080` | Points to locally-running Keycloak |

---

## Gaps Found

None. File is minimal, correct, and intentionally narrow in scope.

---

## Changes Made

None.

---

## Test Results

| Check | Result |
|---|---|
| `docker compose -f docker-compose.yml -f docker-compose.oidc-test.yml config --quiet` | ✅ VALID |
| Env var names match `lead-review` service definition | ✅ All three vars present in base `docker-compose.yml` |

---

## Final State

`docker-compose.oidc-test.yml` is correct and requires no changes.
