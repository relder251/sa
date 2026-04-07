# Credential Management Service — Design Spec
**Date:** 2026-03-23
**Status:** Approved — implementation in progress
**Replaces:** SEC-01 through SEC-10 (superseded)

---

## Problem Statement

The existing `vault-sync` service wraps the Bitwarden CLI (`bw`) as subprocesses from Python. This is structurally wrong: the CLI is an interactive desktop tool, not a library. It has fragile TTY/stdout dependencies, version-specific regressions (2026.2.0 silent unlock failure), and no concept of credential lifecycle (only UPDATE was supported — no create, delete, or rotation). The result was six consecutive `fix:` commits patching symptoms rather than solving the root cause.

Additionally, all service credentials live in plaintext in `.env` on disk. No credential taxonomy exists, making targeted rotation, auditing, and debugging across user vs. system vs. provider credentials impossible.

---

## Goals

1. Replace bw CLI subprocess approach with `bitwarden-sdk` (official Python SDK)
2. Support full credential lifecycle: create, read, update, delete, rotate
3. Establish a three-tier credential taxonomy in a Vaultwarden organization
4. Provide a container startup injection mechanism — no secrets on disk
5. Sync user credentials bidirectionally with Keycloak
6. Provide per-service adapters for system credential rotation
7. Enable service onboarding as a first-class operation

---

## Architecture

```
vault-sync (FastAPI + bitwarden-sdk)
│
├── vault/           # bitwarden-sdk CRUD wrapper
├── models.py        # CredItem, CredCollection, ServiceRegistry
├── adapters/
│   ├── base.py      # SyncAdapter Protocol
│   ├── keycloak.py  # user-credentials ↔ Keycloak (bidirectional)
│   ├── litellm.py   # system-credentials: LiteLLM key rotation
│   ├── n8n.py       # system-credentials: n8n key rotation
│   ├── postgres.py  # system-credentials: PostgreSQL credential update
│   ├── jupyter.py   # system-credentials: JupyterLab token
│   └── glitchtip.py # system-credentials: GlitchTip DSN + secret key
├── inject.py        # /inject/{service} endpoint logic
└── main.py          # FastAPI app, routes, startup
```

### Adapter Interface

```python
class SyncAdapter(Protocol):
    name: str           # "keycloak", "litellm", "n8n", etc.
    collection: str     # "user-credentials" or "system-credentials"

    def create(self, item: CredItem) -> None: ...
    def update(self, item: CredItem) -> None: ...
    def delete(self, item: CredItem) -> None: ...
    def rotate(self, item: CredItem) -> CredItem: ...
```

---

## Credential Taxonomy

Three collections inside Vaultwarden organization **"Agentic-SDLC Infra"**:

| Collection | Contents | Adapters |
|---|---|---|
| `user-credentials` | Keycloak SSO logins, portal accounts | keycloak (bidirectional) |
| `system-credentials` | n8n, LiteLLM, PostgreSQL, JupyterLab, GlitchTip, Vaultwarden admin | per-service adapters |
| `provider-credentials` | Anthropic, Hostinger, etc. | Phase 2 only |

Each `CredItem` carries:
- `name` — vault item name
- `collection` — which of the three tiers
- `service_tags` — which adapters own it (e.g., `["keycloak", "portal"]`)
- `username`, `password`, `notes` — standard fields
- `custom_fields` — for non-password secrets (API keys, DSNs, tokens)

---

## Secret Injection Model

All containers (except vault-sync itself) receive secrets at startup via HTTP injection:

1. `vault-sync` starts first; all other services declare `depends_on: vault-sync (healthy)`
2. Each container's `entrypoint.sh` calls:
   ```sh
   eval $(wget -qO- http://vault_sync:8777/inject/SERVICE_NAME)
   exec "$@"
   ```
3. `GET /inject/{service}` returns `export KEY=value` lines for that service's required credentials
4. Secrets exist only in process memory — never written to disk, never in Docker inspect output

**Bootstrap exception:** vault-sync itself requires `BW_CLIENTID`, `BW_CLIENTSECRET`, `BW_MASTER_PASS` to start. These three values remain in `.env` as the only on-disk secrets. All other service secrets move into the vault.

---

## Phase 1 — Internal Services

### Block 1: Foundation (CRED-01 + CRED-01T)
Replace vault-sync entirely:
- `python:3.12-slim` base image (remove Node.js/bw CLI dependency)
- `bitwarden-sdk` for all vault operations
- FastAPI replacing Flask
- `/health`, `/status` endpoints
- `vault.py`: thin CRUD wrapper around bitwarden-sdk
- **Tests:** unit tests for vault CRUD, health endpoint returns 200, bitwarden-sdk auth succeeds against live Vaultwarden

### Block 2: Credential Taxonomy (CRED-02 + CRED-02T)
- Create Vaultwarden org "Agentic-SDLC Infra" (manual step via web UI)
- Create 3 collections: `user-credentials`, `system-credentials`, `provider-credentials`
- Implement `models.py` with `CredItem` dataclass + collection validation
- Migrate existing vault items (Keycloak SSO, LiteLLM key, etc.) into correct collections
- `GET /credentials/{collection}` — list items by tier
- `POST /credentials/{collection}` — create
- `PUT /credentials/{collection}/{name}` — update
- `DELETE /credentials/{collection}/{name}` — delete
- **Tests:** CRUD per collection, collection enforcement (wrong collection → error), item migration verification

### Block 3: Keycloak Adapter (CRED-03 + CRED-03T)
- `adapters/keycloak.py` implementing full SyncAdapter
- `create`: creates Keycloak user + sets password
- `update`: updates Keycloak user password when vault item changes
- `delete`: disables Keycloak user when vault item deleted
- `rotate`: generates new password, updates vault + Keycloak atomically
- `POST /sync/keycloak` — trigger full bidirectional sync
- `GET /drift/keycloak` — report users present in one system but not the other
- **Tests:** create user end-to-end (vault → Keycloak), update password propagation, delete/disable, drift detection with intentional mismatch, atomic rotate

### Block 4: Injection Endpoint (CRED-04 + CRED-04T)
- Service registry in `inject.py`: maps service names → required credential items
- `GET /inject/{service}` → returns `export KEY=value\n` lines
- `entrypoint.sh` wrapper script (shared, mounted into containers)
- Services registered: `litellm`, `n8n`, `postgres`, `jupyter`, `glitchtip`, `vaultwarden-admin`, `keycloak-admin`
- **Tests:** each registered service returns correct env var names, missing service → 404, wrapper script sources correctly in test container

### Block 5: Container Migration (CRED-05 + CRED-05T)
- Update `docker-compose.prod.yml`: add `depends_on: vault-sync (healthy)` to all services
- Add `entrypoint.sh` wrapper to each service container
- Remove secrets from `.env` (keep only vault-sync bootstrap vars)
- Verify startup ordering and injection for every service
- **Tests:** each container starts and passes service-specific health check, no secrets visible in `docker inspect`, `grep -r "password\|secret\|token" .env` returns only bootstrap vars

### Block 6: System Credential Adapters (CRED-06 + CRED-06T)
Per-service adapters, each implementing `rotate()`:
- `litellm.py` — generate new master key via LiteLLM REST API, update vault
- `n8n.py` — rotate n8n API key via n8n REST API, update vault
- `postgres.py` — `ALTER USER ... PASSWORD`, update vault, signal dependent services
- `jupyter.py` — generate new token, update vault, restart JupyterLab
- `glitchtip.py` — inject DSN + secret key at startup (rotation via GlitchTip admin)
- `vaultwarden_admin.py` — rotate admin token, update vault
- `POST /rotate/{service}` — trigger rotation for a service
- **Tests:** each adapter's `rotate()` produces a new secret, old secret stops working, new secret injected correctly, validate-prod.sh passes after rotation

### Phase 1 Validation Gate (CRED-VALIDATE)
- `bash scripts/validate-prod.sh` exits 0 (all 89 + 22 checks)
- Rotate one user credential end-to-end and verify Keycloak sync
- Rotate one system credential end-to-end and verify service still healthy
- `grep -r "password\|apikey\|secret\|token" /opt/agentic-sdlc/.env` returns only 3 bootstrap vars
- All containers confirmed getting secrets from vault (docker inspect shows no plaintext secrets)

---

## Phase 2 — External Services (future)

- `provider-credentials` collection populated
- Manual-rotation adapters: generate + store new key, emit notification for human to apply at external console
- Scheduled rotation via cron/scheduler
- Drift detection dashboard endpoint
- Services: Anthropic, Hostinger, any future external API providers

---

## Testing Philosophy

- Every block has unit tests (pytest) for core logic and integration tests against live services
- No block is considered complete until its tests pass AND `smoke_test.sh` passes
- Phase gates require `validate-prod.sh` (all suites: smoke + upstreams + browser)
- Tests live in `vault-sync/tests/` alongside the service code
- Service access is tested explicitly: after each adapter, verify the target service responds correctly with the new credential

---

## Files Changed

| File | Change |
|---|---|
| `vault-sync/Dockerfile` | Replace node:20-slim + bw CLI with python:3.12-slim + bitwarden-sdk |
| `vault-sync/app/` | New FastAPI application (replaces single app.py) |
| `vault-sync/tests/` | pytest test suite per block |
| `vault-sync/entrypoint.sh` | Shared injection wrapper |
| `docker-compose.prod.yml` | depends_on chains + entrypoint wrappers |
| `.env` | Remove all secrets except 3 bootstrap vars |
| `scripts/validate-prod.sh` | Extend to verify zero on-disk secrets |
