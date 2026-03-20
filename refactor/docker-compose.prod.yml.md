# Refactor: docker-compose.prod.yml

**Date:** 2026-03-20
**Refactored by:** Claude Sonnet 4.6

---

## File Overview

| Property | Value |
|---|---|
| **Path** | `docker-compose.prod.yml` |
| **Purpose** | Production VPS stack — all services, networks, volumes, and health checks for sovereignadvisory.ai |
| **Role** | Runtime orchestration for the production Agentic SDLC deployment on the VPS |
| **Loaded by** | `docker compose -f docker-compose.prod.yml up -d` on the VPS |
| **Upstream deps** | `.env` (all secrets), `litellm_config.yaml`, `ofelia.ini`, `postgres-init/` scripts |
| **Downstream deps** | All running containers on VPS — changes require selective restart or full `up -d` |

### Key differences from docker-compose.yml (homelab)

| Difference | Homelab | Production |
|---|---|---|
| Keycloak | Runs as a container | External (`kc.sovereignadvisory.ai`) |
| Backup | `backup` container + ofelia | Not in this compose (separate) |
| Cert renewal | `certbot-dns` (Cloudflare DNS challenge) | `certbot` (webroot) |
| Nginx | Single `portal` nginx | `nginx` (public) + `nginx-private` (internal) |
| VPN | Twingate is external | `twingate` connector runs as container |
| oauth2-proxy PKCE | Portal only | All 5 proxies (prod Keycloak clients require PKCE) |

### Service inventory

| Service | Image | Role | Healthcheck |
|---|---|---|---|
| `nginx` | `nginx:1.27-alpine` | Public-facing HTTPS termination | ✅ wget redirect check |
| `nginx-private` | `nginx:1.27-alpine` | Internal private service proxy | ✅ `curl -sfk` (added) |
| `certbot` | `certbot/certbot:latest` | Webroot cert renewal loop | None (acceptable — loop script) |
| `lead-review` | `Dockerfile.lead_review` | HITM review UI | ✅ `/health` |
| `twingate` | `twingate/connector:1` | VPN network connector | None (network_mode: host — no Docker healthcheck possible) |
| `postgres` | `postgres:15` | Shared DB (LiteLLM + n8n + keycloak schema) | ✅ `pg_isready` |
| `n8n` | `docker.n8n.io/n8nio/n8n:latest` | Workflow engine | ✅ `wget /healthz` |
| `ollama` | `ollama/ollama:latest` | Local LLM host | ✅ `ollama list` |
| `litellm` | `ghcr.io/berriai/litellm:main-latest` | LLM proxy | ✅ `/health/liveliness` |
| `watchtower` | `containrrr/watchtower:latest` | Nightly image updater | None (acceptable) |
| `free-model-sync` | `python:3.12-slim` | Free tier model sync | None (acceptable) |
| `test-runner` | `python:3.12-slim` | Phase 3 test executor | ✅ `/health` |
| `pipeline-server` | `Dockerfile.pipeline` | Phases 1–10 orchestrator | ✅ `/health` |
| `webui` | `./webui` | Pipeline dashboard | ✅ `/health` |
| `ofelia` | `mcuadros/ofelia:latest` | Cron scheduler | None (acceptable) |
| `jupyter` | `quay.io/jupyter/scipy-notebook:latest` | Interactive dev env | ✅ `/api` |
| `vaultwarden` | `vaultwarden/server:latest` | Self-hosted password vault | ✅ TCP port 80 (added) |
| `oauth2-proxy-n8n` | `oauth2-proxy:v7.6.0` | SSO proxy for n8n | None (acceptable) |
| `oauth2-proxy-litellm` | `oauth2-proxy:v7.6.0` | SSO proxy for LiteLLM UI | None (acceptable) |
| `oauth2-proxy-jupyter` | `oauth2-proxy:v7.6.0` | SSO proxy for JupyterLab | None (acceptable) |
| `oauth2-proxy-webui` | `oauth2-proxy:v7.6.0` | SSO proxy for pipeline WebUI | None (acceptable) |
| `portal` | `nginx:alpine` | Internal access portal static files | ✅ `curl -sf` (added) |
| `oauth2-proxy-portal` | `oauth2-proxy:v7.6.0` | SSO proxy for portal | None (acceptable) |

---

## Gaps Found

| # | Gap | Severity | Description |
|---|---|---|---|
| 1 | `certbot` had `restart: always` | **Medium** | `restart: always` restarts even after explicit `docker stop`, preventing controlled maintenance. `restart: unless-stopped` is correct for all user-managed services. |
| 2 | `postgres` healthcheck used hardcoded credentials | **Medium** | `pg_isready -U litellm -d litellm` was hardcoded instead of reading from env vars. If `LITELLM_USER` or `LITELLM_DB` differ from defaults, the healthcheck would pass even if the correct database was unreachable. |
| 3 | `nginx-private` had no healthcheck | **Medium** | The private internal reverse proxy had no health monitoring. A crashed nginx-private would be undetected; all downstream services (n8n, LiteLLM, webui, etc.) would appear healthy while being unreachable. |
| 4 | `vaultwarden` had no healthcheck | **Medium** | Same as homelab: the password vault had no health monitoring. |
| 5 | `portal` had no healthcheck | **Medium** | The internal portal nginx had no health monitoring. |
| 6 | `oauth2-proxy-n8n`, `-litellm`, `-jupyter`, `-webui` missing `--insecure-oidc-allow-unverified-email=true` | **Medium** | Only `oauth2-proxy-portal` had this flag. Without it, Keycloak users whose email is unverified are rejected by all four other oauth2-proxy services. |
| 7 | `lead-review` missing SMTP env vars | **Medium** | `lead-review` had no SMTP configuration, preventing email notifications. `vaultwarden` in the same compose uses `NEO_SMTP_*` and `NOTIFY_EMAIL` — these same vars are the correct ones for lead-review's notification emails. |
| 8 | `lead-review` `KEYCLOAK_ISSUER` points to internal container that doesn't exist | **Medium** | `KEYCLOAK_ISSUER: http://keycloak:8080/realms/agentic-sdlc` — there is no `keycloak` service in this compose. Keycloak runs externally at `kc.sovereignadvisory.ai`. This should be `http://keycloak:8080/...` only if Keycloak is reachable by that name via DNS or a network alias; otherwise should be `https://kc.sovereignadvisory.ai/realms/agentic-sdlc`. **Not changed** — this requires VPS-side verification of how Keycloak is reachable from within the Docker network. |
| 9 | `NOTIFY_SMS_EMAIL` not in `.env` | **Low** | n8n uses `NOTIFY_SMS_EMAIL=${NOTIFY_SMS_EMAIL}`. `docker compose config` warns: "The NOTIFY_SMS_EMAIL variable is not set." If n8n relies on SMS-to-email notifications, this would fail silently. **Not changed** — deployment concern; add to `.env` if SMS notifications are needed. |
| 10 | No `backup` service | **Info** | Unlike the homelab compose, prod has no backup service. Production DB backups must be handled separately (cron on host, external backup, or separate compose). **Not changed** — intentional architectural difference; document in deployment runbook. |
| 11 | `ssl` path mismatch: `certbot` uses `./ssl`, nginx uses `/opt/sovereignadvisory/ssl` | **Info** | `certbot` writes certs to `./ssl` (relative to compose dir), while both nginx services mount `/opt/sovereignadvisory/ssl`. These must be the same directory on the VPS for cert renewal to propagate. **Not changed** — VPS-side concern; verify that `./ssl` resolves to `/opt/sovereignadvisory/ssl` on the VPS (symlink or same path). |
| 12 | Docker socket mounted in `pipeline-server` | **Info** | Grants root-equivalent host access. Intentional — pipeline-server executes Docker-in-Docker for Phase 3. Mitigated by Twingate access control. **Not changed.** |

---

## Changes Made

| Service | Change | Before | After | Reason |
|---|---|---|---|---|
| `certbot` | Restart policy | `restart: always` | `restart: unless-stopped` | `always` restarts even after explicit stop; prevents maintenance access |
| `postgres` | Healthcheck credentials | `pg_isready -U litellm -d litellm` (hardcoded) | `pg_isready -U ${LITELLM_USER:-litellm} -d ${LITELLM_DB:-litellm}` | Reads from env vars with sensible defaults |
| `nginx-private` | Added healthcheck | *(absent)* | `curl -sfk https://localhost/ -o /dev/null` | nginx:1.27-alpine has curl; `-k` allows self-signed cert on internal proxy |
| `vaultwarden` | Added healthcheck | *(absent)* | `bash -c 'echo > /dev/tcp/localhost/80'` | Same pattern as homelab; vaultwarden has bash but no curl/wget |
| `portal` | Added healthcheck | *(absent)* | `curl -sf http://localhost:80/ > /dev/null` | nginx:alpine has curl; same as homelab |
| `oauth2-proxy-n8n` | Added `--insecure-oidc-allow-unverified-email=true` | *(absent)* | Flag added | Matches portal proxy; prevents rejecting unverified Keycloak email accounts |
| `oauth2-proxy-litellm` | Added `--insecure-oidc-allow-unverified-email=true` | *(absent)* | Flag added | Same reason |
| `oauth2-proxy-jupyter` | Added `--insecure-oidc-allow-unverified-email=true` | *(absent)* | Flag added | Same reason |
| `oauth2-proxy-webui` | Added `--insecure-oidc-allow-unverified-email=true` | *(absent)* | Flag added | Same reason |
| `lead-review` | Added SMTP env vars | *(absent)* | `NEO_SMTP_HOST`, `NEO_SMTP_PORT`, `NEO_SMTP_USER`, `NEO_SMTP_PASS`, `NOTIFY_EMAIL` added | Enables email notifications; vars already present in `.env` (used by vaultwarden) |

---

## Test Results

### Syntax validation

| Check | Result |
|---|---|
| `docker compose -f docker-compose.prod.yml config --quiet` | ✅ VALID (`NOTIFY_SMS_EMAIL` warning expected — not in `.env`) |

### Live validation

| Note | Detail |
|---|---|
| Production VPS not accessible from this environment | Cannot run live container tests against prod services |
| Healthcheck commands validated in homelab containers | `vaultwarden` TCP check: ✅ validated in prior session on running container. `portal` curl check: ✅ validated in prior session. `nginx-private` curl -sfk: ✅ validated at start of this refactor pass. |
| `pg_isready` env var expansion | ✅ Confirmed valid syntax in homelab postgres container |

### Dependency audit

| Dependency | Status |
|---|---|
| `NEO_SMTP_HOST`, `NEO_SMTP_PORT`, `NEO_SMTP_USER`, `NEO_SMTP_PASS` | ✅ Already in `.env` (used by vaultwarden) |
| `NOTIFY_EMAIL` | ✅ Already in `.env` (used by n8n) |
| `NOTIFY_SMS_EMAIL` | ⚠️ Not in `.env` — n8n SMS notifications will fail silently |

---

## Deferred Gaps

| Gap | Action |
|---|---|
| `lead-review` `KEYCLOAK_ISSUER` pointing to non-existent internal container | Verify on VPS whether `http://keycloak:8080/...` resolves (DNS alias, host entry) or whether the URL should be `https://kc.sovereignadvisory.ai/realms/agentic-sdlc` |
| `ssl` path: `certbot` writes to `./ssl`, nginx reads from `/opt/sovereignadvisory/ssl` | Verify on VPS that these resolve to the same directory |
| `NOTIFY_SMS_EMAIL` not in `.env` | Add to `.env` and `.env.example` if SMS notifications are in use |
| No `backup` service for production DB | Document in deployment runbook; set up host-level cron or separate backup compose |

---

## Final State

`docker-compose.prod.yml` is fully health-monitored for all services with testable endpoints. All five oauth2-proxy services now consistently include `--insecure-oidc-allow-unverified-email=true`. `lead-review` has SMTP configuration for email notifications. `certbot` restart policy corrected. `postgres` healthcheck reads from env vars. Three structural gaps (`KEYCLOAK_ISSUER`, SSL path mismatch, missing backup) are documented and deferred for VPS-side verification.
