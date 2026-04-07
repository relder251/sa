# Refactor: keycloak/

**Date:** 2026-03-20
**Refactored by:** Claude Sonnet 4.6

---

## Directory Overview

| Property | Value |
|---|---|
| **Path** | `keycloak/` |
| **Purpose** | Keycloak realm export — bootstraps the `agentic-sdlc` realm on first deploy |
| **Container** | `keycloak` (Keycloak 24.x) |
| **Realm** | `agentic-sdlc` |
| **Import mechanism** | `docker-compose.yml` passes `--import-realm` flag; file mounted at `/opt/keycloak/data/import/realm-export.json` |

### What is and isn't exported

Keycloak exports only realm-level configuration. Not included in the export:
- Users and their credentials
- Active sessions
- Client secrets (replaced with placeholders on export)
- Offline tokens

### Clients defined in realm

| Client ID | Purpose |
|---|---|
| `oauth2-proxy-*` | One per protected service (n8n, webui, litellm, jupyter, portal) |
| `lead-review` | OIDC client for the lead review portal |

---

## Gaps Found

| # | Gap | Severity | Description |
|---|---|---|---|
| 1 | `bruteForceProtected: false` | **Low** | No account lockout after repeated failed logins. All admin/SSO login attempts will succeed given enough tries. Acceptable for homelab but not production. |
| 2 | Export is a point-in-time snapshot | **Info** | If clients are added via UI without re-exporting, the file drifts from reality. Fresh deploys will be missing those clients. |

---

## Changes Made

| Change | File | Before | After | Reason |
|---|---|---|---|---|
| Enable brute force protection | `realm-export.json` | `"bruteForceProtected": false` | `"bruteForceProtected": true` | Existing `failureFactor: 30` and `maxFailureWaitSeconds: 900` settings become active — 30 failures triggers 15-minute lockout |

---

## Test Results

| Check | Result |
|---|---|
| JSON syntax valid | ✅ `python3 -c "import json; json.load(open('keycloak/realm-export.json'))"` |
| Existing realm config preserved | ✅ Only `bruteForceProtected` field changed |

---

## Deferred Items

| Item | Notes |
|---|---|
| Re-export realm after UI changes | Establish a runbook: after adding clients via Keycloak admin, export via `scripts/keycloak_export_realm.sh` and commit |
| Client secret rotation | Client secrets are not in the export; they're regenerated on import. Downstream services that reference `KC_CLIENT_SECRET` in `.env` must be updated after a fresh import. |
