# Internal Access Portal — Design Spec

**Date:** 2026-03-19
**Status:** Approved for implementation

---

## Overview

A self-hosted internal access portal at `home.private.sovereignadvisory.ai` that gives authenticated users a single, branded launchpad for all Sovereign Advisory services — internal and external. The portal handles service discovery, category management, favorites, and a wizard that automatically provisions the authentication and network infrastructure for new internal services.

---

## Goals

- One login grants seamless access to all internal services (SSO passthrough)
- External and credential-managed services get assisted autofill via the Vaultwarden browser extension
- Adding a new service takes under two minutes and requires no manual config file edits
- The portal matches the Sovereign Advisory brand exactly

---

## Architecture

### Two Nginx containers — do not conflate them

| Container | Role |
|---|---|
| `sa_nginx_private` | Existing external container (not defined in `docker-compose.yml`); acts as TLS-terminating edge proxy for `*.private.sovereignadvisory.ai`; routes to service-specific oauth2-proxy containers on `vibe_net` |
| `portal` (new) | New `nginx:alpine` container added to `docker-compose.yml`; serves the portal static files; sits behind `oauth2_proxy_portal` |

### Request flow

```
Browser → Twingate (home.private.sovereignadvisory.ai)
        → sa_nginx_private (TLS termination, routes to oauth2_proxy_portal:4180)
        → oauth2_proxy_portal (authenticates against Keycloak agentic-sdlc realm)
        → portal:80 (serves static HTML/JS/CSS; proxies /api/ calls to n8n)
```

`sa_nginx_private` needs a new server block for `home.private.sovereignadvisory.ai` that proxies to `oauth2_proxy_portal:4180` (see Nginx Configuration section).

Clicking a card for an **internal service** follows the same pattern for that service's own subdomain — each has its own `oauth2_proxy_<name>` container or native OIDC integration. Clicking a card for an **external service** opens the URL in a new tab; Vaultwarden autofill handles credentials.

The portal itself is a **static HTML/CSS/JS application** served by the `portal` container. Dynamic state (service registry, categories) is stored in a `services.json` file that the portal fetches on load. All mutations to `services.json` go through **n8n provisioning webhooks** — the portal never writes config directly.

### Components

| Component | Role |
|---|---|
| `portal/index.html` | Main application (sidebar + grid UI) |
| `portal/services.json` | Source of truth for registered services and categories (writable by n8n) |
| `portal/assets/` | CSS, fonts, icons |
| `nginx/conf.d/portal.conf` | Nginx vhost config for the `portal` container: serves static files, proxies `/api/` to n8n |
| `nginx-private/conf.d/home.conf` | New server block in `sa_nginx_private` config: routes `home.private.sovereignadvisory.ai` to `oauth2_proxy_portal:4180` |
| `docker-compose.yml` | `portal` service and `oauth2_proxy_portal` service (with full env vars); also `portal` volume mount added to n8n service |
| `docker-compose.override.yml` | New `oauth2_proxy_<name>` containers appended by n8n provisioning workflow; auto-merged by Docker Compose (same directory as `docker-compose.yml`) |
| n8n workflow: `portal-provision` | Handles Add Service webhook: Keycloak + oauth2-proxy + Nginx + Twingate + services.json |
| n8n workflow: `portal-update` | Handles Edit service webhook: updates services.json |
| n8n workflow: `portal-delete` | Handles Delete service webhook: removes entry from services.json |
| n8n workflow: `portal-update-categories` | Handles category mutations: replaces categories array in services.json |

---

## Visual Design

The portal uses the Sovereign Advisory brand palette pulled directly from `sovereignadvisory.ai`:

| Token | Value | Use |
|---|---|---|
| `--bg` | `#0b0f1c` | Page background |
| `--bg-card` | `#0e1324` | Card backgrounds |
| `--bg-hover` | `#121830` | Hover states |
| `--copper` | `#d4924a` | Primary accent (active states, stars, arrows) |
| `--copper-light` | `#f0a535` | Logo, hover highlights |
| `--copper-pale` | `rgba(240,165,53,0.10)` | Subtle fills |
| `--text` | `#f0ece4` | Primary text (warm white) |
| `--text-3` | `#a09aaf` | Muted/secondary text |
| Display font | Cormorant Garamond | Logo, section headings |
| Body font | Raleway 300/400/500 | All UI text |

---

## UI Layout

### Topbar
- Left: `⬡ SOVEREIGN ADVISORY` logo (Cormorant Garamond, copper) + "Internal Portal" subtitle
- Right: status indicator ("All systems operational" + green dot), `+ Add Service` button, user chip with avatar

### Sidebar (196px)
**Quick Access section**
- Favorites filter (shows count badge, filters grid to starred cards)
- Recently Used filter (shows last 5 clicked services inline with relative timestamps)

**Categories section**
- All Services (total count)
- One button per category (icon + label + count badge)
- Active category gets copper left-border indicator

### Main grid
- `repeat(auto-fill, minmax(170px, 1fr))` — fills available width
- Section header: current filter name + service count
- Copper divider line between header and grid

### Service card
- Category tag (bottom-left, color-coded per category)
- Live status dot + "live" label (bottom-right)
- Star button (top-right, toggles favorite, copper when active)
- ✎ edit button (bottom-left, appears on hover)
- ↗ arrow (bottom-right, copper on hover)
- Hover: raises 2px, copper border glow, top-edge copper gradient

### Default categories

| Key | Label | Icon | Tag color |
|---|---|---|---|
| `automation` | Automation | ⚡ | Green `#6ee7a0` |
| `ai` | AI / Models | 🤖 | Indigo `#a5b4fc` |
| `security` | Security | 🔐 | Red `#fca5a5` |
| `productivity` | Productivity | 📝 | Violet `#c4b5fd` |
| `infra` | Infrastructure | 🌐 | Sky `#7dd3fc` |

Categories are user-extensible (see Add Category below).

### Initial service registry

> **Notes on initial SSO tiers:**
> - **n8n**: Has `N8N_SSO_OIDC_ENABLED=true` in `docker-compose.yml` (full Keycloak OIDC config). `ssoTier: 1`.
> - **LiteLLM**: `oauth2_proxy_litellm` container already defined in `docker-compose.yml`. `ssoTier: 1`.
> - **JupyterLab**: `oauth2_proxy_jupyter` container already defined in `docker-compose.yml`. `ssoTier: 1`.
> - **Vaultwarden**: Has native `SSO_ENABLED=true` OIDC integration in `docker-compose.yml` (no oauth2-proxy needed). `ssoTier: 1`.
> - **Pipeline Board**: Subdomain removed from `sa_nginx_private` and Twingate in commit `44ff980`. Portal card links to `pipeline.private.sovereignadvisory.ai` but opens without SSO passthrough until infra is re-provisioned. `ssoTier: 3`.
> - All external SaaS services: `ssoTier: 3` (Vaultwarden autofill).

| Service | Category | URL | SSO Tier |
|---|---|---|---|
| n8n | Automation | `https://n8n.private.sovereignadvisory.ai` | 1 |
| Pipeline Board | Automation | `https://pipeline.private.sovereignadvisory.ai` | 3 (infra removed) |
| LiteLLM | AI / Models | `https://litellm.private.sovereignadvisory.ai` | 1 |
| JupyterLab | AI / Models | `https://jupyter.private.sovereignadvisory.ai` | 1 |
| Vaultwarden | Security | `https://vault.private.sovereignadvisory.ai` | 1 (native OIDC) |
| Keycloak | Security | `https://kc.sovereignadvisory.ai` | 3 |
| Twingate | Infrastructure | `https://www.twingate.com/dashboard` | 3 |
| Cloudflare | Infrastructure | `https://dash.cloudflare.com` | 3 |
| Hostinger | Infrastructure | `https://hpanel.hostinger.com` | 3 |
| Notion | Productivity | `https://notion.so` | 3 |

---

## Authentication & SSO Passthrough

Authentication to the portal is handled by **oauth2-proxy + Keycloak** (agentic-sdlc realm). After a single login, cards open services directly based on one of three tiers:

### `isInternal` vs `ssoTier` — distinction

`isInternal` is a **URL-derived signal** used only during provisioning. It answers: "does this URL belong to the `*.private.sovereignadvisory.ai` domain?" If yes, the provisioning workflow creates the full oauth2-proxy + Nginx + Twingate + Keycloak stack for the service.

`ssoTier` is a **capability label** stored in `services.json` and set by the provisioning workflow. It answers: "what kind of authentication experience does the user get right now?" A service may have `isInternal: true` but `ssoTier: 3` if its infrastructure has not been provisioned (e.g., Pipeline Board).

### Tier 1 — Full SSO

Services get Tier 1 access via one of two paths:

- **oauth2-proxy pattern** (n8n, LiteLLM, JupyterLab, future wizard-provisioned services): Each service has its own `oauth2_proxy_<name>` container on `vibe_net`. `sa_nginx_private` routes the service's subdomain to this proxy, which validates the Keycloak session and forwards to the upstream. The shared `OAUTH2_PROXY_COOKIE_SECRET` and `cookie-domain=.sovereignadvisory.ai` (where applicable) means the Keycloak session is recognised across subdomains.
- **Native OIDC pattern** (Vaultwarden): The service integrates directly with Keycloak OIDC without an oauth2-proxy container. `SSO_ENABLED=true` in `docker-compose.yml`.

### Tier 2 — Header injection (token-accepting services)
For services that accept `Authorization: Bearer` or `X-Auth-Request-User` / `X-Auth-Request-Email` headers, `sa_nginx_private` injects these from the oauth2-proxy session using `auth_request`. Transparent access without a second prompt.

**Applies to:** JupyterLab (token-based), LiteLLM admin UI.

### Tier 3 — Vaultwarden autofill assist
For services that cannot federate with Keycloak, the portal opens the service URL in a new tab. The Vaultwarden browser extension detects the login form and autofills stored credentials. The optional `credentialHint` field in `services.json` links to the relevant Vaultwarden item URI.

**Applies to:** Pipeline Board (infra removed), Keycloak admin console (is the IdP itself), Cloudflare, Hostinger, Notion, Twingate.

> **Note:** Tier 3 for Pipeline Board is temporary — re-running the Add Service wizard for `pipeline.private.sovereignadvisory.ai` will restore Tier 1. Keycloak and external SaaS are permanently Tier 3.

---

## Add Service Wizard

A 4-step modal triggered by `+ Add Service` in the topbar.

### Step 1 — Details
Fields: Service URL, Display Name, Description, Icon (emoji with live preview).

On URL input, the portal detects whether the hostname matches `*.private.sovereignadvisory.ai`:
- **Internal match:** green hint — "Internal service — SSO, Nginx, Twingate will be auto-provisioned"
- **External:** blue hint — "External service — portal card + Vaultwarden credential link"

The detection result sets `isInternal` which controls Step 3 provisioning plan and Step 4 n8n payload.

### Step 2 — Category
Tile picker showing all current categories. Includes `＋ New` dashed tile that expands an inline form:
- Emoji icon picker + category name input + Add button
- Creates category in the registry, adds it to the sidebar, auto-selects it
- New categories persist in `services.json` via `portal-update-categories`

### Step 3 — Review
Displays a checklist of what will be provisioned. Content differs by `isInternal`:

**Internal:**
1. Keycloak OIDC Client — create confidential client in `agentic-sdlc` realm, set redirect URI to `https://<subdomain>.private.sovereignadvisory.ai/oauth2/callback`, assign `portal-user` realm role
2. oauth2-proxy container — generate config block from template, append to `docker-compose.override.yml`, run `docker compose up -d`
3. Nginx reverse proxy rule — write `nginx-private/conf.d/<name>.conf`, run `docker exec sa_nginx_private nginx -s reload`
4. Twingate resource — register private DNS alias via Twingate API
5. Portal card — service appears in selected category with live status

**External:**
1. Portal card — link card added to the portal
2. Vaultwarden credential hint (optional) — `credentialHint` field linked from the card

### Step 4 — Deploy
The portal POSTs to `/api/portal-provision` (Nginx proxies to `http://n8n:5678/webhook/portal-provision`). The step shows a **cosmetic** progress bar advancing on a fixed timer (~30s for internal, ~2s for external). The portal does not poll n8n — it waits for the HTTP response; n8n returns only when all provisioning steps complete or on error. On success, the bar completes, shows "Service is live", and the modal closes with the new card injected from the `card` object in the response.

If n8n returns an error, Step 4 shows the error message and a "Try again" button.

---

## Edit Service

A `✎ edit` button appears bottom-left on each card on hover. Opens a single-page modal pre-populated with the card's current values:

- Display Name, Service URL, Description, Icon (emoji)
- Category tile picker (same component as wizard step 2, current category pre-selected)
- **Save Changes** — fires `POST /api/portal-update` with updated fields, refreshes card in place
- **Remove** (red, left-aligned) — confirms then fires `POST /api/portal-delete`, removes card from grid; sidebar counts update

Infrastructure (oauth2-proxy container, Nginx config, Twingate resource) is **not** torn down on delete — manual cleanup required to avoid accidental data loss.

---

## Add Category

Available in wizard Step 2 and the Edit modal's category picker via the `＋ New` dashed tile.

Flow: enter emoji + name → creates entry in categories registry → adds sidebar filter button → adds tile in current picker. Calls `POST /api/portal-update-categories` with the **full current categories array** (including the new entry). The webhook **replaces** the entire `categories` array in `services.json`.

Auto-assigns a harmonious tag color from a rotating palette of muted hues.

---

## services.json Schema

```json
{
  "categories": [
    {
      "key": "automation",
      "label": "Automation",
      "icon": "⚡",
      "color": "rgba(74,222,128,0.08)",
      "text": "#6ee7a0",
      "border": "rgba(74,222,128,0.15)"
    }
  ],
  "services": [
    {
      "id": "n8n",
      "name": "n8n",
      "url": "https://n8n.private.sovereignadvisory.ai",
      "description": "Workflow automation",
      "icon": "⚡",
      "category": "automation",
      "favorite": false,
      "ssoTier": 1
    },
    {
      "id": "cloudflare",
      "name": "Cloudflare",
      "url": "https://dash.cloudflare.com",
      "description": "DNS & CDN management",
      "icon": "🌐",
      "category": "infra",
      "favorite": false,
      "ssoTier": 3,
      "credentialHint": "https://vault.private.sovereignadvisory.ai/#search/cloudflare"
    }
  ]
}
```

Field notes:
- `ssoTier` (1/2/3): set by provisioning workflow; used by the portal to annotate card tooltips.
- `credentialHint` (optional, Tier 3 only): URL or URI pointing to the Vaultwarden entry. Shown as a tooltip on the card. Omitted for Tier 1/2 services.

---

## n8n Provisioning Workflows

### Infrastructure access for n8n

**Write access to `services.json` and `docker-compose.override.yml`:** The `portal/` directory is bind-mounted into n8n as `./portal:/data/portal` (added to n8n service in `docker-compose.yml`). n8n uses "Write File" or "Code" nodes to read and update these files.

**Docker and Nginx control:** n8n uses the Docker socket (`/var/run/docker.sock`) which is the established pattern in this stack (used by Watchtower, Certbot, and Ofelia). An `Execute Command` node calls scripts in `/data/scripts/` (already mounted read-only from `./scripts`). Scripts required:
- `scripts/portal_docker_up.sh <service_name>` — runs `docker compose up -d oauth2_proxy_<name>`
- `scripts/portal_nginx_reload.sh` — runs `docker exec sa_nginx_private nginx -s reload`

The Docker socket is added to n8n's volume list in `docker-compose.yml`.

### `docker-compose.override.yml` bootstrapping

The file lives at `./docker-compose.override.yml` (same directory as `docker-compose.yml`; Docker Compose auto-merges it). If it does not exist, the provisioning workflow creates it with:

```yaml
services: {}
```

Subsequent provisioning steps append new service blocks. The workflow reads the existing file, parses it as YAML, merges the new service block, and writes back.

### `portal-provision` webhook
**Trigger:** `POST /webhook/portal-provision`
**Payload:** `{ name, url, description, icon, category, isInternal, credentialHint? }`

**For internal services:**
1. Call Keycloak Admin API → create confidential OIDC client in `agentic-sdlc` realm, redirect URI `https://<subdomain>/oauth2/callback`, assign `portal-user` realm role. Uses the `keycloak-admin` service account (stored in n8n credentials store; same account used by existing provisioning scripts).
2. Generate oauth2-proxy config block from existing `oauth2_proxy_litellm` template, append to `docker-compose.override.yml`
3. Run `scripts/portal_docker_up.sh <name>` via Execute Command node
4. Write `nginx-private/conf.d/<name>.conf` (location block), run `scripts/portal_nginx_reload.sh`
5. Call Twingate API → create resource for `<subdomain>.private.sovereignadvisory.ai`
6. Append service entry to `portal/services.json`, set `ssoTier: 1`
7. Return `{ success: true, card: { ... } }`

**For external services:**
1. Append service entry to `portal/services.json`, set `ssoTier: 3`, include `credentialHint` if provided
2. Return `{ success: true, card: { ... } }`

### `portal-update` webhook
**Trigger:** `POST /webhook/portal-update`
**Payload:** `{ id, fields: { name?, url?, description?, icon?, category? } }`
Partial update: merges `fields` into the matching service entry in `portal/services.json`.

### `portal-delete` webhook
**Trigger:** `POST /webhook/portal-delete`
**Payload:** `{ id }`
Removes the matching entry from `portal/services.json`. Does not tear down infrastructure.

### `portal-update-categories` webhook
**Trigger:** `POST /webhook/portal-update-categories`
**Payload:** `{ categories: [...] }`
**Replaces** the entire `categories` array in `portal/services.json` with the provided array.

---

## Docker Compose Changes

### New services (added to `docker-compose.yml`)

```yaml
services:
  portal:
    image: nginx:alpine
    container_name: portal
    volumes:
      - ./portal:/usr/share/nginx/html:ro
      - ./nginx/conf.d/portal.conf:/etc/nginx/conf.d/portal.conf:ro
    networks:
      - vibe_net
    restart: unless-stopped
    labels:
      - "com.centurylinklabs.watchtower.enable=true"

  oauth2-proxy-portal:
    image: quay.io/oauth2-proxy/oauth2-proxy:v7.6.0
    container_name: oauth2_proxy_portal
    command:
      - --provider=oidc
      - --oidc-issuer-url=https://kc.sovereignadvisory.ai/realms/agentic-sdlc
      - --client-id=portal
      - --client-secret=${PORTAL_OIDC_CLIENT_SECRET}
      - --redirect-url=https://home.private.sovereignadvisory.ai/oauth2/callback
      - --upstream=http://portal:80
      - --http-address=0.0.0.0:4185
      - --cookie-secret=${OAUTH2_PROXY_COOKIE_SECRET}
      - --cookie-secure=true
      - --email-domain=*
      - --skip-provider-button=true
      - --insecure-oidc-allow-unverified-email=true
      - --code-challenge-method=S256
    networks:
      - vibe_net
    restart: unless-stopped
    depends_on:
      keycloak:
        condition: service_healthy
      portal:
        condition: service_started
    labels:
      - "com.centurylinklabs.watchtower.enable=true"
```

### n8n service additions (patch to existing n8n service)

Add to n8n's `volumes` list:
```yaml
      - ./portal:/data/portal          # read+write for services.json
      - /var/run/docker.sock:/var/run/docker.sock  # for docker compose up
```

### New env vars (added to `.env`)

```
PORTAL_OIDC_CLIENT_SECRET=<generated>
```

`OAUTH2_PROXY_COOKIE_SECRET` already exists in `.env` (shared with other oauth2-proxy containers).

### `docker-compose.override.yml` (initial state, committed to repo)

```yaml
services: {}
```

---

## Nginx Configuration

### `nginx/conf.d/portal.conf` (served by `portal` container)

```nginx
server {
  listen 80;
  server_name home.private.sovereignadvisory.ai;

  location / {
    root /usr/share/nginx/html;
    try_files $uri $uri/ /index.html;
  }

  location /api/ {
    proxy_pass http://n8n:5678/webhook/;
    proxy_set_header Host $host;
  }
}
```

### `nginx-private/conf.d/home.conf` (added to `sa_nginx_private` config)

```nginx
server {
  listen 443 ssl;
  server_name home.private.sovereignadvisory.ai;

  ssl_certificate     /etc/letsencrypt/live/private.sovereignadvisory.ai/fullchain.pem;
  ssl_certificate_key /etc/letsencrypt/live/private.sovereignadvisory.ai/privkey.pem;

  location / {
    proxy_pass http://oauth2_proxy_portal:4185;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
  }
}
```

---

## Keycloak Configuration

- **Realm:** `agentic-sdlc` (existing)
- **Client type:** Confidential (requires `client_secret`) — matches the pattern of `litellm`, `jupyter`, `webui` clients
- **Client ID:** `portal`
- **Redirect URI:** `https://home.private.sovereignadvisory.ai/oauth2/callback`
- **PKCE:** S256 required (`--code-challenge-method=S256` in oauth2-proxy command)
- **Client scopes:** `openid`, `email`, `profile`
- **Service account for provisioning:** `keycloak-admin` service account in the `agentic-sdlc` realm (or master realm admin); credentials stored in n8n credentials store under the name `keycloak-admin-api`

The `portal` Keycloak client must be created before the portal is deployed (bootstrap step in implementation plan).

---

## Twingate Resource

A Twingate resource for `home.private.sovereignadvisory.ai` pointing to `oauth2_proxy_portal` on port 4185, registered via the existing `twingate_add_resource` script pattern. `sa_nginx_private` is already accessible within the Twingate network.

---

## Out of Scope

- Mobile/responsive design (internal tool, desktop-only)
- Multi-user role differentiation (all authenticated users see the same portal)
- Service health monitoring / uptime polling (live dot is static for now)
- Automatic infrastructure teardown on service delete
- Notion/Cloudflare SAML federation (future upgrade path, not a blocker)

---

## Success Criteria

1. Navigating to `home.private.sovereignadvisory.ai` redirects to Keycloak login if not authenticated, then lands on the portal
2. All 10 initial services are visible and clickable as portal cards
3. Clicking n8n, LiteLLM, or JupyterLab opens the service with no second login prompt (Tier 1 — these already have oauth2-proxy or OIDC configured)
4. Clicking Cloudflare or Notion opens in a new tab; Vaultwarden extension autofills credentials (Tier 3)
5. The Add Service wizard completes end-to-end for an **external** URL (simpler path, no infra provisioning): submitting the form creates a new card in the portal grid. Smoke-test URL: `https://notion.so/test-service`
6. Edit service changes name/category and the card updates immediately (verified by changing an existing card and reloading `services.json`)
7. Add Category creates a new sidebar filter and the category is selectable in the wizard (verified by adding a "Design" category and confirming it appears in sidebar and wizard step 2)
