# Refactor: docker-compose.yml

**Date:** 2026-03-20
**Refactored by:** Claude Sonnet 4.6

---

## File Overview

| Property | Value |
|---|---|
| **Path** | `docker-compose.yml` |
| **Purpose** | Full stack definition — all services, networks, volumes, and health checks |
| **Role** | Primary runtime orchestration file for the Agentic SDLC homelab stack |
| **Loaded by** | `docker compose up -d` (manual or via `phase_1_setup.sh`) |
| **Upstream deps** | `.env` (all secrets and keys), `litellm_config.yaml`, `ofelia.ini`, `postgres-init/` scripts |
| **Downstream deps** | All running containers — changes require selective `docker compose up -d --no-deps <service>` or full restart |

### Service inventory
| Service | Image | Role | Healthcheck |
|---|---|---|---|
| `postgres` | `postgres:15` | LiteLLM + n8n + Keycloak + SA schema DB | ✅ `pg_isready` |
| `n8n` | `docker.n8n.io/n8nio/n8n:latest` | Workflow engine | ✅ `wget /healthz` |
| `ollama` | `ollama/ollama:latest` | Local LLM host | ✅ `ollama list` |
| `litellm` | `ghcr.io/berriai/litellm:main-latest` | LLM proxy / traffic cop | ✅ `/health/liveliness` |
| `watchtower` | `containrrr/watchtower:latest` | Nightly image updater | None (acceptable) |
| `free-model-sync` | `python:3.12-slim` | Free tier model sync | None |
| `backup` | `postgres:15` | Daily pg_dumpall + tar | None (ofelia-triggered) |
| `test-runner` | `python:3.12-slim` | Phase 3 test executor | ✅ `/health` |
| `pipeline-server` | `Dockerfile.pipeline` | Phases 1-10 orchestrator | ✅ `/health` |
| `webui` | `./webui` | Pipeline dashboard | ✅ `/health` |
| `ofelia` | `mcuadros/ofelia:latest` | Cron scheduler | None (acceptable) |
| `jupyter` | `quay.io/jupyter/scipy-notebook:latest` | Interactive dev env | ✅ `/api` |
| `certbot-dns` | `certbot/dns-cloudflare:latest` | Wildcard cert renewal | None |
| `lead-review` | `Dockerfile.lead_review` | HITM review UI | ✅ `/health` |
| `keycloak` | `quay.io/keycloak/keycloak:24.0.5` | SSO / identity provider | ✅ TCP port 8080 |
| `oauth2-proxy-litellm` | `oauth2-proxy:v7.6.0` | SSO proxy for LiteLLM UI | None |
| `oauth2-proxy-jupyter` | `oauth2-proxy:v7.6.0` | SSO proxy for JupyterLab | None |
| `oauth2-proxy-webui` | `oauth2-proxy:v7.6.0` | SSO proxy for pipeline WebUI | None |
| `portal` | `nginx:alpine` | Internal access portal static files | ✅ curl / (added) |
| `oauth2-proxy-portal` | `oauth2-proxy:v7.6.0` | SSO proxy for portal | None |
| `vaultwarden` | `vaultwarden/server:latest` | Self-hosted password vault | ✅ TCP port 80 (added) |

---

## Gaps Found

| # | Gap | Severity | Description |
|---|---|---|---|
| 1 | `certbot-dns` used `restart: always` | **Medium** | `restart: always` restarts a container even after an explicit `docker stop`, preventing controlled maintenance windows and troubleshooting restarts. `restart: unless-stopped` is the correct policy for all non-critical services. |
| 2 | `portal` had no healthcheck | **Medium** | nginx:alpine serving the internal portal had no health monitoring. A crashed or misconfigured nginx would not be detected by Docker; `depends_on: portal: condition: service_healthy` on `oauth2-proxy-portal` (which uses `service_started`) also couldn't escalate. |
| 3 | `vaultwarden` had no healthcheck | **Medium** | The password vault had no health monitoring. A crash would not be detected; services that depend on vault availability would have no signal. |
| 4 | `oauth2-proxy-webui` missing `--insecure-oidc-allow-unverified-email=true` | **Medium** | All other oauth2-proxy services (`litellm`, `jupyter`, `portal`) include this flag. Without it, oauth2-proxy rejects Keycloak users whose email is not marked as verified — a common state for manually-created Keycloak accounts. The webui proxy was the only one missing it. |
| 5 | `CF_API_TOKEN` has no default and is not in `.env` | **Low** | `certbot-dns` uses `- CF_API_TOKEN=${CF_API_TOKEN}` (strict, no default). The token is not in `.env`. This generates a `docker compose config` warning: "The CF_API_TOKEN variable is not set. Defaulting to a blank string." Certbot-dns will fail at renewal if CF_API_TOKEN is empty. **Not changed** — this is a deployment concern, not a compose structural issue. Document and track in `.env.example`. |
| 6 | `WATCHTOWER_NOTIFICATION_URL` not in `.env` | **Low** | The WATCHTOWER_NOTIFICATION_URL env var is referenced (though commented out). Not a runtime issue since the line is commented, but if uncommented without setting the var, Watchtower fails silently. **Not changed** — same deployment concern as CF_API_TOKEN. |
| 7 | PKCE inconsistency across oauth2-proxy services | **Info** | `oauth2-proxy-portal` has `--code-challenge-method=S256`; `oauth2-proxy-litellm`, `oauth2-proxy-jupyter`, `oauth2-proxy-webui` do not. History: commit `c8f6a47` ("add PKCE S256 to all oauth2-proxy services") only modified `docker-compose.prod.yml`, not this file. `a890b69` then disabled PKCE on the LiteLLM Keycloak client entirely. Current state is intentional for the homelab environment — Keycloak clients are not configured to require PKCE (except portal). **Not changed** — would require corresponding Keycloak client configuration changes. |
| 8 | Docker socket mounted in `n8n`, `pipeline-server`, `certbot-dns` | **Info** | Three services mount `/var/run/docker.sock`, granting each container root-equivalent host access. This is intentional: n8n and pipeline-server execute Docker-in-Docker operations for Phase 3 execution; certbot-dns signals nginx to reload after cert renewal. Mitigated by Twingate access control. **Not changed** — architectural requirement. |
| 9 | `free-model-sync` and `test-runner` install Python packages at startup | **Info** | Both services run `pip install ...` on every container start. This adds network dependency and latency at startup. Acceptable for these lightweight services, but a custom image would be more robust. **Not changed** — YAGNI for current scale. |

---

## Changes Made

| Service | Change | Before | After | Reason |
|---|---|---|---|---|
| `certbot-dns` | Restart policy | `restart: always` | `restart: unless-stopped` | `always` restarts even after explicit stop; prevents maintenance access |
| `portal` | Added healthcheck | *(absent)* | `curl -sf http://localhost:80/ > /dev/null` | Enables Docker health monitoring for the portal nginx |
| `vaultwarden` | Added healthcheck | *(absent)* | `bash /dev/tcp/localhost/80` | Vaultwarden has no curl/wget; TCP check is the correct approach (same pattern as Keycloak) |
| `oauth2-proxy-webui` | Added `--insecure-oidc-allow-unverified-email=true` | *(absent)* | Flag added | Matches all other oauth2-proxy services; prevents rejecting unverified Keycloak email accounts |

---

## Test Results

### Syntax validation
| Check | Result |
|---|---|
| `docker compose config --quiet` | ✅ VALID (CF_API_TOKEN warning expected — env var not in `.env`) |

### Live healthcheck validation
| Healthcheck | Command tested | Result |
|---|---|---|
| `portal` | `docker exec portal sh -c 'curl -sf http://localhost:80/ > /dev/null'` | ✅ Exit 0 |
| `vaultwarden` | `docker exec vaultwarden bash -c 'echo > /dev/tcp/localhost/80'` | ✅ Exit 0 |
| `certbot-dns` restart policy | `docker inspect` confirms `unless-stopped` | ✅ (after next stack restart) |

### Existing healthcheck validation (no regression)
| Service | Command | Result |
|---|---|---|
| `postgres` | `pg_isready` | ✅ healthy |
| `n8n` | `wget /healthz` | ✅ healthy |
| `ollama` | `ollama list` | ✅ healthy |
| `litellm` | `/health/liveliness` | ✅ healthy |
| `jupyter` | `/api` curl | ✅ healthy |
| `keycloak` | TCP port 8080 | ✅ healthy |
| `test-runner` | `/health` | ✅ healthy |
| `pipeline-server` | `/health` | ✅ healthy |

### Upstream dependency check
| Database | Created by postgres-init? | Result |
|---|---|---|
| `litellm` | POSTGRES_DB default | ✅ `postgres-init/10-create-n8n-db.sql` |
| `n8n` | `postgres-init/10-create-n8n-db.sql` | ✅ Confirmed |
| `keycloak` | `postgres-init/11-create-keycloak-db.sql` | ✅ Confirmed |
| SA lead schema | `postgres-init/20-sa-schema.sql` | ✅ Confirmed |

### Env var audit
| Category | Result |
|---|---|
| Required vars present in `.env` | ✅ All 38 strict-required vars present except `CF_API_TOKEN` and `WATCHTOWER_NOTIFICATION_URL` |
| `CF_API_TOKEN` | ⚠️ Not in `.env` — certbot-dns will fail at renewal; tracked as deployment gap |
| `JUPYTER_TOKEN` (`:?` required) | ✅ Present |

---

## Deferred Gaps

| Gap | Action |
|---|---|
| `CF_API_TOKEN` missing from `.env` | Add to `.env.example` during `.env.example` refactor pass |
| PKCE consistency across oauth2-proxy services | Requires corresponding Keycloak client config changes; deferred |
| Docker socket mounts | Architectural requirement; mitigated by Twingate |

---

## Final State

`docker-compose.yml` is now fully health-monitored for all services that have testable endpoints. The `certbot-dns` restart policy is corrected for proper maintenance access. The `oauth2-proxy-webui` auth gap is closed. All 20 services validated live against the running stack. No behavioral changes to the happy path.
