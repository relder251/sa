# Sovereign Advisory AI — Full Stack Documentation

**Snapshot date:** 2026-03-22
**VPS:** root@187.77.208.197
**Production path:** /opt/agentic-sdlc/
**Repository:** relder251/sa (private, GitHub)
**Status:** Live baseline — captured before FRAMEWORK Phase 0 implementation

---

## 1. Stack Overview

The Sovereign Advisory AI stack is a self-hosted, AI-augmented SDLC and business operations platform running on a single VPS. It combines:

- **AI orchestration** — LiteLLM proxy provides a unified API across local Ollama models and cloud providers (Anthropic, OpenAI, Gemini, Groq, Perplexity). Four model tiers (`local/`, `hybrid/`, `cloud/`, `free/`) allow cost-tiered routing.
- **Workflow automation** — n8n handles multi-step business workflows including the Notion→Claude Code dispatch loop, portal management, lead pipeline, and credential sync.
- **Agent dispatch** — Notion tasks trigger Claude Code CLI executions via n8n. The dispatch loop is the primary interface for agentic development work.
- **Developer tools** — JupyterLab for interactive development, a custom WebUI for chat, and a web-terminal (shell-gateway) accessible from the portal.
- **Business logic** — Lead review service, contact pipeline, opportunity pipeline, SA Lead Reminder.
- **Infrastructure services** — nginx reverse proxy (public + private), Keycloak SSO, Vaultwarden secrets, GlitchTip error tracking, Twingate zero-trust network access, Watchtower auto-updates, certbot TLS.

Key design goals:
- All internal services are SSO-protected (Keycloak + oauth2-proxy); no service ports are exposed directly to the internet.
- AI calls are routed through a single LiteLLM proxy for spend tracking, key management, and model-tier fallbacks.
- The dispatch loop allows any Notion task to trigger a Claude Code agent on the VPS with full repo context.

---

## 2. Services Inventory

32 services running as of snapshot date. All containers are on the `agentic-sdlc_vibe_net` Docker bridge network unless noted.

| Service | Container | Image | Purpose | Internal Port | External (nginx vhost) | SSO Protected |
|---|---|---|---|---|---|---|
| nginx | sa_nginx | nginx:1.27-alpine | Public reverse proxy; handles `sovereignadvisory.ai` and routes `/review/`, `/auth/`, `/n8n/` | 80/443 (bound to 187.77.208.197) | sovereignadvisory.ai | No (public) |
| nginx-private | sa_nginx_private | nginx:1.27-alpine | Private reverse proxy; terminates TLS for `*.private.sovereignadvisory.ai`; routes all SSO-protected services | 443 (bound to 127.0.0.1 — Twingate only) | *.private.sovereignadvisory.ai | Via oauth2-proxy |
| keycloak | keycloak | quay.io/keycloak/keycloak:24.0.5 | SSO identity provider for all private services | 8080 | kc.private.sovereignadvisory.ai | No (it is the IdP) |
| postgres | litellm_db | postgres:15 | Shared PostgreSQL: databases for LiteLLM (spend/keys), n8n (workflows/executions), and Keycloak | 5432 | — | — |
| n8n | n8n | docker.n8n.io/n8nio/n8n:latest | Workflow automation; hosts all business and portal workflows | 5678 | n8n.private.sovereignadvisory.ai | Yes (oauth2-proxy-n8n) |
| litellm | litellm | ghcr.io/berriai/litellm:main-latest | Unified LLM proxy; exposes `/v1/chat/completions` for all tiers | 4000 | litellm.private.sovereignadvisory.ai | Yes (oauth2-proxy-litellm) |
| ollama | ollama | ollama/ollama:latest | Local LLM inference (RTX 3070, 8GB VRAM); models loaded: qwen2.5-coder:7b, deepseek-r1:7b, mistral:7b, llama3.2:3b, llama3.1:8b | 11434 | ollama.private.sovereignadvisory.ai | No (internal network only) |
| jupyter | jupyter | quay.io/jupyter/scipy-notebook:latest | JupyterLab for interactive development; shared LiteLLM proxy access | 8888 | jupyter.private.sovereignadvisory.ai | Yes (oauth2-proxy-jupyter) |
| webui | webui | agentic-sdlc-webui | Custom chat web interface backed by LiteLLM | 3000 | webui.private.sovereignadvisory.ai | Yes (oauth2-proxy-webui) |
| portal | portal | nginx:alpine | Internal service portal nginx; serves portal SPA + proxies portal API calls to n8n webhooks | 80 | home.private.sovereignadvisory.ai | Yes (oauth2-proxy-portal) |
| vaultwarden | vaultwarden | vaultwarden/server:latest | Bitwarden-compatible password manager; stores all credentials and API keys | 80 (UI), 3012 (WebSocket) | vault.private.sovereignadvisory.ai | No (own auth) |
| shell-gateway | shell_gateway | agentic-sdlc-shell-gateway | Web terminal (ttyd/xterm.js) for portal shell sessions; proxied at portal `/terminal/` | 7681 | home.private.sovereignadvisory.ai/terminal/ | Via portal SSO |
| pipeline-server | pipeline_server | agentic-sdlc-pipeline-server | Custom pipeline execution server | 5002 | — | — |
| lead-review | sa_lead_review | sa_lead_review:latest | Lead review web app; accessible at `sovereignadvisory.ai/review/` | 5003 | sovereignadvisory.ai/review/ | Own auth (/auth/) |
| glitchtip-web | glitchtip_web | glitchtip/glitchtip:latest | Sentry-compatible error tracking UI | 8000 | sentry.private.sovereignadvisory.ai | No (own auth) |
| glitchtip-worker | glitchtip_worker | glitchtip/glitchtip:latest | GlitchTip background worker (issue processing, alerts) | — | — | — |
| glitchtip-db | glitchtip_db | postgres:15-alpine | Dedicated PostgreSQL for GlitchTip | 5432 | — | — |
| glitchtip-redis | glitchtip_redis | redis:7-alpine | Redis for GlitchTip task queue | 6379 | — | — |
| vault-sync | vault_sync | agentic-sdlc-vault-sync | Syncs credentials from Vaultwarden into container env vars | 8777 | — | — |
| free-model-sync | free_model_sync | python:3.12-slim | Discovers free models (OpenRouter/Groq/Gemini) and syncs them into LiteLLM via management API every 6h | — | — | — |
| backup | backup | postgres:15 | Sidecar container for `pg_dumpall` backups; sleeps unless backup script is run | — | — | — |
| ofelia | ofelia | mcuadros/ofelia:latest | Cron scheduler; runs `free_model_sync.py` every 6 hours | — | — | — |
| watchtower | watchtower | containrrr/watchtower:latest | Auto-updates labelled containers nightly at 03:00 | — | — | — |
| test-runner | test_runner | python:3.12-slim | Automated test runner service | 5001 | — | — |
| oauth2-proxy-n8n | oauth2_proxy_n8n | quay.io/oauth2-proxy/oauth2-proxy:v7.6.0 | SSO gate for n8n | 5679 | — | — |
| oauth2-proxy-litellm | oauth2_proxy_litellm | quay.io/oauth2-proxy/oauth2-proxy:v7.6.0 | SSO gate for LiteLLM | 4001 | — | — |
| oauth2-proxy-jupyter | oauth2_proxy_jupyter | quay.io/oauth2-proxy/oauth2-proxy:v7.6.0 | SSO gate for JupyterLab | 8889 | — | — |
| oauth2-proxy-portal | oauth2_proxy_portal | quay.io/oauth2-proxy/oauth2-proxy:v7.6.0 | SSO gate for portal | 4185 | — | — |
| oauth2-proxy-webui | oauth2_proxy_webui | quay.io/oauth2-proxy/oauth2-proxy:v7.6.0 | SSO gate for WebUI | 3001 | — | — |
| certbot | sa_certbot | certbot/certbot:latest | Renews TLS certs for `sovereignadvisory.ai` | — | — | — |
| certbot-dns | sa_certbot_dns | certbot/dns-cloudflare:latest | Renews wildcard TLS cert for `*.private.sovereignadvisory.ai` via DNS-01 (Cloudflare) | — | — | — |
| twingate | twingate | twingate/connector:1 | Zero-trust network access; routes `*.private.sovereignadvisory.ai` → 127.0.0.1:443 (nginx-private) | host network | — | — |

---

## 3. Network Architecture

### External Access

```
Internet users
   │
   ├─▶ 187.77.208.197:80/443 (sa_nginx / public nginx)
   │     ├─▶ sovereignadvisory.ai/review/  →  lead-review:5003
   │     ├─▶ sovereignadvisory.ai/auth/    →  lead-review:5003
   │     ├─▶ sovereignadvisory.ai/n8n/     →  n8n:5678
   │     └─▶ sovereignadvisory.ai/         →  redirect to www
   │
   └─▶ (Twingate authorized clients only)
         │
         ▼ Twingate connector (host network) → 127.0.0.1:443
         │
         ▼ sa_nginx_private (nginx-private)
               ├─▶ home.private.sovereignadvisory.ai        →  oauth2_proxy_portal:4185  →  portal:80
               ├─▶ n8n.private.sovereignadvisory.ai         →  oauth2_proxy_n8n:5679     →  n8n:5678
               ├─▶ litellm.private.sovereignadvisory.ai     →  oauth2_proxy_litellm:4001 →  litellm:4000
               ├─▶ jupyter.private.sovereignadvisory.ai     →  oauth2_proxy_jupyter:8889 →  jupyter:8888
               ├─▶ webui.private.sovereignadvisory.ai       →  oauth2_proxy_webui:3001   →  webui:3000
               ├─▶ vault.private.sovereignadvisory.ai       →  vaultwarden:80
               ├─▶ kc.private.sovereignadvisory.ai          →  keycloak:8080
               ├─▶ ollama.private.sovereignadvisory.ai      →  ollama:11434
               └─▶ sentry.private.sovereignadvisory.ai      →  glitchtip_web:8000
```

### SSO Perimeter

Every service behind `*.private.sovereignadvisory.ai` (except vault and keycloak themselves) is protected by an oauth2-proxy sidecar. The oauth2-proxy containers:
- Validate session cookies against Keycloak at `kc.private.sovereignadvisory.ai`
- On unauthenticated request, redirect to Keycloak for login
- Forward authenticated requests upstream with `X-Auth-User` headers

### Docker Networks

| Network | Driver | Used By |
|---|---|---|
| `agentic-sdlc_vibe_net` | bridge | All services (default) |
| `agentic-sdlc_sa_web` | bridge | Public-facing services |
| host network | — | twingate (connector needs host networking) |

### Portal Internal Routing

The portal nginx (`nginx/conf.d/portal.conf`) proxies API calls from the SPA to n8n webhooks:

| Portal endpoint | n8n webhook target |
|---|---|
| `/api/portal-services` | `n8n:5678/webhook/VHUS5Dx1q9HBZPln/webhook/portal-services` |
| `/api/portal-provision` | `n8n:5678/webhook/DXjMzKwRB6c54GCY/webhook/portal-provision` |
| `/api/portal-update-categories` | `n8n:5678/webhook/portal-update-categories` |
| `/api/portal-update` | `n8n:5678/webhook/portal-update` |
| `/api/portal-delete` | `n8n:5678/webhook/portal-delete` |
| `/api/portal-track-recent` | `n8n:5678/webhook/GOpCkjqJyPjy5dgG/webhook/portal-track-recent` |
| `/api/litellm-health` | `litellm:4000/health?model=cloud/smart` |
| `/terminal/` | `shell_gateway:7681` (WebSocket upgrade) |

---

## 4. n8n Workflows

14 workflows total. 11 active, 3 inactive.

| ID | Name | Trigger | Purpose | Status |
|---|---|---|---|---|
| oasCNCvMnbeU4nMD | Notion -> Claude Dispatch | Schedule (poll Notion) | Polls Notion tasks database for `In Progress` tasks; invokes Claude Code CLI on VPS via `child_process`; writes output back to Notion `Agent status` field | Active |
| DXjMzKwRB6c54GCY | Portal: Provision Service | Webhook | Receives new service data from portal UI; adds entry to `portal/services.json`; returns updated service list | Active |
| VHUS5Dx1q9HBZPln | Portal Services (GET) | Webhook | Returns current `portal/services.json` to portal SPA on page load | Active |
| MdZMVjJ2zUPmDtyu | Portal Update Categories | Webhook | Handles category edits from portal UI | Active |
| NCvllVAgkG91FXzD | Portal Update | Webhook | Handles service PATCH edits from portal UI | Active |
| q6C6PUPzyYhhT8gG | Portal Delete | Webhook | Handles service DELETE from portal UI | Active |
| GOpCkjqJyPjy5dgG | Portal Track Recent | Webhook | Records recently accessed services for portal "recent" section | Active |
| VtUltSyncCrd001 | Vault Update Credential | Webhook | Updates a credential in Vaultwarden when called (used by vault-sync service) | Active |
| Wyc4UIvCYgByrAwP | SA Contact Lead Pipeline | Webhook | Processes incoming contact leads; routes through LiteLLM for scoring/categorization | Active |
| agentic-pipeline-004 | Phase 4: Opportunity Pipeline | File trigger | Monitors output directory for new opportunity files; processes through pipeline | Active |
| mGXFJnMVMPadFrbg | SA Lead Reminder (Business Day Check) | Schedule | Sends reminders for outstanding leads on business days | Active |
| U6nMfP8gXMYPqyOn | Webhook Test | Webhook | Test workflow | Inactive |
| tvu8HkcW70eS73Vr | DB Diagnostic | Manual | Database diagnostic queries | Inactive |
| eB0F0sGSOsw8SCoP | test with node | — | Development test workflow | Inactive |

---

## 5. Dispatch Loop

The Notion → Claude Code dispatch loop is the primary interface for agentic development tasks.

**Current state: Working** (confirmed 2026-03-22)

### Flow

```
Notion tasks database
  │  (task with Status="In Progress" appears)
  ▼
n8n: "Notion -> Claude Dispatch" (oasCNCvMnbeU4nMD)
  │  Schedule trigger polls every N minutes
  │  "Extract Task Data" node reads task title + "Agent status" field (currently dual-purpose: input + output)
  │
  ▼
n8n: Code node (child_process.execFile)
  │  Calls: claude --print --dangerously-skip-permissions -p "<task_prompt>"
  │  Uses: $env.ANTHROPIC_API_KEY (injected into n8n container environment)
  │  Working directory: /opt/agentic-sdlc (full repo available)
  │
  ▼
Claude Code CLI runs as agentic task
  │  Has access to all MCP servers configured in .mcp.json
  │  Has access to /opt/agentic-sdlc repo
  │
  ▼
n8n: writes output to Notion
  │  Updates "Agent status" rich_text field with result
  │  Updates "Status" to "Done" or "Failed"
```

### Known Design Issue (TODO2.md §9a)

The `Agent status` field currently serves as both the input prompt AND the output log. If a task fails and is retried, the error message becomes the new prompt. Fix pending: add a dedicated `Agent prompt` field to the Notion database.

---

## 6. Data Layer

### PostgreSQL (single instance: litellm_db / postgres:15)

Three databases share one PostgreSQL instance at `172.20.0.2:5432`:

| Database | Owner | Purpose |
|---|---|---|
| `litellm` | litellm | LiteLLM spend tracking, API key management, model config, audit logs, agent spend, daily metrics. Full schema includes: `LiteLLM_VerificationToken`, `LiteLLM_SpendLogs`, `LiteLLM_ModelTable`, `LiteLLM_TeamTable`, `LiteLLM_UserTable`, `LiteLLM_AuditLog`, `LiteLLM_BudgetTable`, and 30+ related tables |
| `n8n` | n8n_user | n8n workflow definitions (`workflow_entity`), execution history, credentials, workflow activation state. **Note (F2):** n8n uses `workflow_history` table for active execution — `workflow_entity.nodes` is not the source of truth for running workflows |
| `keycloak` | keycloak_user | Keycloak realm data: users, clients, realms, sessions, roles, protocol mappers |

### GlitchTip (separate postgres:15-alpine instance: glitchtip_db)

Stores all error events, issue groupings, projects, teams, and alerts for the GlitchTip error tracker.

### File Storage

| Path | Used By | Purpose |
|---|---|---|
| `./portal/services.json` | portal SPA + n8n | Service catalogue for portal homepage |
| `./output/` | n8n + JupyterLab | Shared output directory for pipeline artifacts |
| `./notebooks/` | JupyterLab | JupyterLab workspace |
| `./workflows/` | n8n (import) | Exported workflow JSON files (committed to git) |
| `/opt/agentic-sdlc/` | all services | Root project directory on VPS |

---

## 7. Secrets Management

### Vaultwarden

Self-hosted Bitwarden-compatible server at `https://vault.private.sovereignadvisory.ai`.

- All API keys, credentials, and secrets are stored here as Bitwarden items
- Accessible via Bitwarden browser extension or CLI (`bw`) by authorized users
- The `vault-sync` service reads credentials from Vaultwarden and can inject them into container environments
- Playwright automation scripts on the VPS use Vaultwarden for credential retrieval

### Keycloak SSO

Keycloak at `https://kc.private.sovereignadvisory.ai` provides:
- OIDC/OAuth2 flows for all `oauth2-proxy` sidecars
- A single realm for all private services (realm name configured in `.env`)
- User management for all internal tool access
- Client definitions for each oauth2-proxy instance (n8n, litellm, jupyter, portal, webui)

### .env File

All runtime secrets are in `/opt/agentic-sdlc/.env` (never committed). Key variables include:

| Variable | Used By |
|---|---|
| `LITELLM_API_KEY` | LiteLLM, n8n, portal nginx |
| `ANTHROPIC_API_KEY` | n8n dispatch loop (Claude Code CLI) |
| `N8N_API_KEY` | n8n management API |
| `LITELLM_USER` / `LITELLM_DB` | PostgreSQL connection |
| `KEYCLOAK_*` | Keycloak setup |
| `CLOUDFLARE_*` | certbot-dns TLS renewal |
| `OPENROUTER_API_KEY`, `GROQ_API_KEY`, `GEMINI_API_KEY`, etc. | LiteLLM cloud providers |

---

## 8. LiteLLM Model Tier System

LiteLLM at port 4000 exposes four named tiers via `litellm_config.yaml`:

| Tier | Suffixes | Providers | Use Case |
|---|---|---|---|
| `local/*` | `chat`, `code`, `reason`, `fast` | Ollama only | Zero cost, on-device |
| `hybrid/*` | same | Ollama first → Groq/Gemini/DeepSeek fallback | Cost-aware with reliability |
| `cloud/*` | `chat`, `smart`, `code`, `reason`, `fast`, `search` | Anthropic, OpenAI, Gemini, Perplexity | Premium quality |
| `free/*` | `chat`, `code`, `reason`, `fast` | Dynamically populated by free-model-sync | Zero cost cloud |

**Loaded Ollama models:** qwen2.5-coder:7b, deepseek-r1:7b, mistral:7b, llama3.2:3b, llama3.1:8b

Router strategy: `least-busy` within each group. Cross-tier fallbacks are defined in `litellm_config.yaml` under `router_settings.fallbacks`.

The `free/*` tier is managed dynamically by `free_model_sync.py` (runs every 6h via ofelia) and is not in the static config file — it is written to LiteLLM's database via the management API.

---

## 9. CI/CD Pipeline

### Deployment Process

1. Developer pushes to `master` branch on GitHub (`relder251/sa`)
2. GitHub Actions workflow (`.github/workflows/deploy.yml`) triggers on push to `master`
3. Runs on the self-hosted runner at `/opt/actions-runner` on VPS (label: `self-hosted, linux, prod`)
4. Calls `scripts/deploy.sh` which:
   - `git pull` (fast-fail on uncommitted changes)
   - `docker compose -f docker-compose.yml -f docker-compose.override.yml -f docker-compose.prod.yml up -d --build`
   - Fixes portal `services.json` permissions for n8n write access
5. Calls `scripts/smoke_test.sh` for post-deploy health verification

### Compose File Layering

| File | Purpose |
|---|---|
| `docker-compose.yml` | Base stack: all shared services |
| `docker-compose.override.yml` | Local dev overrides (auto-applied) |
| `docker-compose.prod.yml` | VPS-only additions: nginx, twingate, certbot, glitchtip, lead-review, shell-gateway, vault-sync, pipeline-server, webui, portal |

### GitHub Actions Workflows

| Workflow | Trigger | Action |
|---|---|---|
| `deploy.yml` | Push to `master` | SSH-free deploy via self-hosted runner + smoke test |
| `release.yml` | (see file) | Release automation |

---

## 10. Known Fragile Areas

From `TODO2.md` — these areas require extra caution before modification:

### F1 — Portal nginx webhook routing (BLOCKING)
`nginx/conf.d/portal.conf.template` proxies 6 portal API endpoints to n8n webhooks. As of snapshot:
- `portal-provision` and `portal-services` are confirmed working (active n8n workflows with matching webhook IDs)
- `portal-update`, `portal-delete`, `portal-update-categories` have active n8n workflows but the nginx proxy targets may use incorrect webhook path format — these endpoints may return 502 if called
- **Fix this before any portal or nginx work.**

### F2 — n8n workflow persistence
n8n executes from the `workflow_history` table (using `activeVersionId`), NOT from `workflow_entity.nodes`. The n8n UI and API PUT operations only update `workflow_entity`. Any direct DB edits must update both tables. Access: `psql` via `backup` container → `172.20.0.2:5432`, user `n8n_user`, db `n8n`.

### F3 — Keycloak + oauth2-proxy SSO chain
All `*.private.sovereignadvisory.ai` services are protected by oauth2-proxy → Keycloak. A misconfigured oauth2-proxy client ID, redirect URI, or environment variable will lock out the service. Always test new service oauth2-proxy config before touching the production nginx config.

### F4 — docker-compose.prod.yml surface area
32+ services in one compose file. Adding a new service risks port conflicts on nginx 80/443 (bound to `187.77.208.197` directly), DNS resolution failures in `vibe_net`, or Watchtower auto-updating a container mid-deploy. Add services incrementally; verify healthchecks before moving on.

### F5 — LiteLLM config stability
Adding new `success_callback` or `environment_variables` to `litellm_config.yaml` can silently break proxy startup. LiteLLM serves all AI calls from n8n, the portal, JupyterLab, and WebUI. Test config changes with:
```bash
docker compose -f docker-compose.prod.yml run --rm litellm --config /app/config.yaml --test
```

---

## 11. Access URLs

All private services require Twingate access to reach `127.0.0.1:443` on the VPS.

| Service | URL | Auth |
|---|---|---|
| Internal Portal | https://home.private.sovereignadvisory.ai | Keycloak SSO |
| n8n | https://n8n.private.sovereignadvisory.ai | Keycloak SSO |
| LiteLLM | https://litellm.private.sovereignadvisory.ai | Keycloak SSO |
| JupyterLab | https://jupyter.private.sovereignadvisory.ai | Keycloak SSO |
| WebUI (chat) | https://webui.private.sovereignadvisory.ai | Keycloak SSO |
| Vaultwarden | https://vault.private.sovereignadvisory.ai | Bitwarden account |
| Keycloak Admin | https://kc.private.sovereignadvisory.ai | Keycloak admin |
| Ollama API | https://ollama.private.sovereignadvisory.ai | Internal only |
| GlitchTip | https://sentry.private.sovereignadvisory.ai | GlitchTip account |
| Lead Review | https://sovereignadvisory.ai/review/ | /auth/ token |
| n8n webhooks (public) | https://sovereignadvisory.ai/n8n/webhook/ | Webhook secrets |

### Internal Ports (Docker network only)

| Port | Service | Container |
|---|---|---|
| 5678 | n8n | n8n |
| 4000 | LiteLLM proxy | litellm |
| 5432 | PostgreSQL (n8n + litellm + keycloak) | litellm_db |
| 11434 | Ollama | ollama |
| 8888 | JupyterLab | jupyter |
| 8080 | Keycloak | keycloak |
| 80 | Vaultwarden | vaultwarden |
| 4185 | oauth2-proxy portal | oauth2_proxy_portal |
| 7681 | Shell gateway | shell_gateway |
| 5003 | Lead review | sa_lead_review |
| 8000 | GlitchTip | glitchtip_web |
| 8777 | Vault sync | vault_sync |
| 5002 | Pipeline server | pipeline_server |
| 5001 | Test runner | test_runner |

---

## 12. Installed Claude Code Skills

Located in `/home/user/vibe_coding/Agentic_SDLC/.claude/skills/`:

| Skill | Trigger Use Case |
|---|---|
| `portal-feature` | Add a service card to the portal |
| `n8n-workflow` | Validate n8n webhook routing after deploys |
| `stack-validate` | Run a full stack health check (containers, Keycloak, Vaultwarden, nginx) |
| `rotate-credential` | Rotate a Vaultwarden API key |

---

## 13. Pre-FRAMEWORK Phase 0 Gaps

Items from `TODO2.md` not yet completed as of snapshot date:

- `.env.example` — does not exist; needs to be generated from live `.env`
- `integration-log.md` — missing from project root
- Agent state database schema (`agent_state` schema in PostgreSQL) — not yet created
- Portal webhook routing F1 fix — portal-update/delete/categories endpoints may 502
- Dispatch loop prompt/output field separation (F9a) — `Agent status` is both input and output
- FRAMEWORK.md Phase 0 infrastructure not yet started: mirror-staging branch, docker-compose.mirror.yml, CQS schema, PCIRT orchestrator workflow


---

## 14. Incident -- Docker Bind Mount Stale Inode (2026-04-07)

### What happened
sovereignadvisory.ai went down with HTTP 403. Root cause: the /opt/agentic-sdlc/www
directory (bind-mounted into sa_nginx at /usr/share/nginx/html) had its inode replaced --
a deploy script had done rm -rf www && mkdir www && cp ... -- so the running container
bind mount was pinned to the old, now-empty inode. nginx had no index.html and returned
403 for every request.

The same pattern occurred with prometheus.yml during task #7 (cAdvisor limits): Python
open(file, w) replaced the file inode; the running Prometheus container still read the
old inode until force-recreated.

### Fix
Force-recreate the container:
  docker stop <container> && docker rm <container>
  cd /opt/agentic-sdlc && make up ENV=prod SVC=<service>

A simple docker restart or make up SVC=... does NOT fix a stale bind mount inode.

### Prevention
- Deploy scripts must never replace a bind-mount directory with rm -rf + mkdir.
  Use rsync or copy individual files in-place to preserve the inode.
- Alternatively add a post-deploy step: docker rm sa_nginx && make up ENV=prod SVC=nginx

### Detection gap
No uptime monitoring exists for sovereignadvisory.ai. Outage was discovered manually.
Task #28 (website availability monitoring) addresses this.
