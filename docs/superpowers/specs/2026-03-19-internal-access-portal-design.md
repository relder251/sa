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

Two Nginx containers are involved — do not conflate them:

| Container | Role |
|---|---|
| `sa-nginx-private` | Existing edge reverse proxy; routes `*.private.sovereignadvisory.ai` traffic to service-specific oauth2-proxy containers |
| `portal` (new) | Dedicated Nginx container that serves the portal static files for `home.private.sovereignadvisory.ai` |

Request flow:

```
Browser → Twingate (home.private.sovereignadvisory.ai)
        → sa-nginx-private (TLS termination, routes to oauth2_proxy_portal)
        → oauth2_proxy_portal (authenticates against Keycloak agentic-sdlc realm)
        → portal (Nginx, serves static HTML/JS/CSS + proxies /api/ to n8n)
```

Clicking a card for an **internal service** follows the same pattern for that service's own subdomain (each has its own `oauth2_proxy_<name>` container). Clicking a card for an **external service** opens the URL directly in a new tab; Vaultwarden autofill handles credentials.

The portal itself is a **static HTML/CSS/JS application** served by the `portal` Nginx container. Dynamic state (service registry, categories) is stored in a `services.json` file that the portal fetches on load. All mutations to `services.json` go through an **n8n provisioning webhook** — the portal never writes config directly.

### Components

| Component | Role |
|---|---|
| `portal/index.html` | Main application (sidebar + grid UI) |
| `portal/services.json` | Source of truth for registered services and categories (writable by n8n) |
| `portal/assets/` | CSS, fonts, icons |
| `nginx/conf.d/portal.conf` | Nginx vhost for portal container: serves static files, proxies `/api/` to n8n |
| `docker-compose.yml` | `portal` service and `oauth2_proxy_portal` service (with full env vars) |
| `docker-compose.override.yml` | New `oauth2_proxy_<name>` containers appended by n8n provisioning workflow |
| n8n workflow: `portal-provision` | Handles Add Service webhook: Keycloak + oauth2-proxy + Nginx + Twingate + services.json |
| n8n workflow: `portal-update` | Handles Edit/Delete service webhook: updates services.json |

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

> **Note on Pipeline Board:** The Pipeline Board subdomain (`pipeline.private.sovereignadvisory.ai`) was removed from the Nginx and Twingate configuration in commit `44ff980`. It is included in the portal registry as a card but will be treated as an external/Tier 3 service (URL opens directly) until Nginx and Twingate resources are re-provisioned for it.

| Service | Category | URL | SSO Tier |
|---|---|---|---|
| n8n | Automation | `https://n8n.private.sovereignadvisory.ai` | 3 |
| Pipeline Board | Automation | `https://pipeline.private.sovereignadvisory.ai` | 3 (infra not provisioned) |
| LiteLLM | AI / Models | `https://litellm.private.sovereignadvisory.ai` | 1 (pending oauth2-proxy) |
| JupyterLab | AI / Models | `https://jupyter.private.sovereignadvisory.ai` | 1 (pending oauth2-proxy) |
| Vaultwarden | Security | `https://vault.private.sovereignadvisory.ai` | 1 (pending oauth2-proxy) |
| Keycloak | Security | `https://kc.sovereignadvisory.ai` | 3 |
| Twingate | Infrastructure | `https://www.twingate.com/dashboard` | 3 |
| Cloudflare | Infrastructure | `https://dash.cloudflare.com` | 3 |
| Hostinger | Infrastructure | `https://hpanel.hostinger.com` | 3 |
| Notion | Productivity | `https://notion.so` | 3 |

Services showing "pending oauth2-proxy" become Tier 1 once their `oauth2_proxy_<name>` containers are provisioned via the Add Service wizard or manual setup. Until then they are accessible but will prompt for their own login.

---

## Authentication & SSO Passthrough

Authentication to the portal is handled by **oauth2-proxy + Keycloak** (agentic-sdlc realm). After a single login, cards open services directly based on one of three tiers:

### `isInternal` vs `ssoTier` — distinction

`isInternal` is a **URL-derived signal** used only during provisioning. It answers: "does this URL belong to the `*.private.sovereignadvisory.ai` domain?" If yes, the provisioning workflow creates the full oauth2-proxy + Nginx + Twingate + Keycloak stack for the service.

`ssoTier` is a **capability label** stored in `services.json` and set by the provisioning workflow. It answers: "what kind of authentication experience does the user get right now?" A service may have `isInternal: true` but `ssoTier: 3` if its oauth2-proxy container has not yet been provisioned (e.g., n8n community edition, Pipeline Board without infra).

### Tier 1 — Full SSO (internal services behind oauth2-proxy)
Services provisioned through the Add Service wizard get their own `oauth2-proxy` container configured against the same Keycloak realm. The Keycloak session cookie is shared across all `*.private.sovereignadvisory.ai` subdomains (set as `cookie_domain = .sovereignadvisory.ai` in each oauth2-proxy config). Clicking the card opens the service with no second authentication prompt.

**Applies to:** LiteLLM, JupyterLab, Vaultwarden (web UI), and any future internal service provisioned via the wizard.

### Tier 2 — Header injection (token-accepting services)
For services that accept `Authorization: Bearer` or `X-Auth-Request-User` / `X-Auth-Request-Email` headers, `sa-nginx-private` uses `auth_request` to forward the Keycloak session and injects headers. The user gets transparent access without a second prompt.

**Applies to:** JupyterLab (token-based), LiteLLM admin UI.

### Tier 3 — Vaultwarden autofill assist
For services that have their own login UI and cannot federate with Keycloak, the portal opens the service URL in a new tab and the Vaultwarden browser extension detects the login form and autofills credentials stored in Vaultwarden. The `credentialHint` field in `services.json` optionally links to the relevant Vaultwarden item URI.

**Applies to:**
- **n8n community edition** — enterprise OIDC not included; autofill via Vaultwarden
- **Pipeline Board** — infrastructure not provisioned; opens directly
- **Keycloak admin console** — is the IdP itself; cannot proxy its own auth
- **Cloudflare, Hostinger, Notion, Twingate** — external SaaS with independent auth

> **Note:** The Tier 3 gap for n8n is addressable in future via the n8n Enterprise license (~$50/mo). The Notion SAML SSO option (Business plan) would move it to Tier 1. These are tracked as future upgrade paths, not blockers.

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
- New categories persist in `services.json`

### Step 3 — Review
Displays a checklist of what will be provisioned. Content differs by `isInternal`:

**Internal:**
1. Keycloak OIDC Client — create confidential client in `agentic-sdlc` realm, set redirect URI to `https://<subdomain>.private.sovereignadvisory.ai/oauth2/callback`, assign `portal-user` role
2. oauth2-proxy container — generate config block from template, append to `docker-compose.override.yml`
3. Nginx reverse proxy rule — add upstream + location block to `conf.d/<name>.conf`, reload `sa-nginx-private`
4. Twingate resource — register private DNS alias via Twingate API
5. Portal card — service appears in selected category with live status

**External:**
1. Portal card — link card added to the portal
2. Vaultwarden entry (optional) — credential record linked from the card via `credentialHint` field

### Step 4 — Deploy
The portal POSTs to the n8n provisioning webhook (`/api/portal-provision`, which Nginx proxies to n8n at `/webhook/portal-provision`). The step shows a **cosmetic** progress bar that advances on a fixed timer (roughly matching expected provisioning duration — ~30s for internal, instant for external). The portal does not poll n8n for intermediate status; it waits for the HTTP response (n8n returns only when all steps complete or on error). On success response, the progress bar completes, displays service name + "Service is live and accessible via SSO", and the modal closes. The new card is injected into the grid using the `card` object from the webhook response.

If n8n returns an error, Step 4 shows the error message and a "Try again" button.

The n8n workflow is responsible for all side-effects (Keycloak API, Docker, Nginx, Twingate API, `services.json` update). The portal is stateless — it only fires the webhook and reflects the result.

---

## Edit Service

A `✎ edit` button appears bottom-left on each card on hover. Opens a single-page modal (no steps) pre-populated with the card's current values:

- Display Name, Service URL, Description, Icon (emoji)
- Category tile picker (same component as wizard step 2, current category pre-selected)
- **Save Changes** — fires `POST /api/portal-update` with updated fields, refreshes card in place
- **Remove** (red, left-aligned) — confirms then fires `POST /api/portal-delete`, removes card from grid and sidebar counts update

---

## Add Category

Available in two places:
1. Wizard Step 2 — `＋ New` tile
2. Edit modal category picker — same `＋ New` tile

Flow: enter emoji + name → creates entry in `categories` registry → adds sidebar filter button → adds tile in current picker. Category persists to `services.json` via a `POST /api/portal-update-categories` call, which **replaces the entire `categories` array** in `services.json` with the new array sent in the payload. The portal sends the full current categories list (including any new entry) in every call to this endpoint.

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
      "description": "Workflow automation · 6 active",
      "icon": "⚡",
      "category": "automation",
      "favorite": false,
      "ssoTier": 3,
      "credentialHint": "bitwarden://sovereignadvisory/n8n"
    }
  ]
}
```

Field notes:
- `ssoTier` (1/2/3) is set by the provisioning workflow and used by the portal to annotate card tooltips indicating auth method.
- `credentialHint` (optional, Tier 3 only) — a URI or URL pointing to the relevant Vaultwarden credential entry. Shown as a tooltip on the card to help users identify which saved credential applies. For Tier 1/2 services this field is omitted.

---

## n8n Provisioning Workflows

### Infrastructure access for n8n workflows

n8n needs two capabilities to execute provisioning steps:

1. **Write access to `services.json` and `docker-compose.override.yml`**: These files are bind-mounted into n8n at `/data/portal/services.json` and `/data/portal/docker-compose.override.yml`. n8n uses the "Write Binary File" or "Write File" nodes to update them.

2. **Docker and Nginx control**: n8n runs `docker compose up -d` and `nginx -s reload` via a privileged sidecar container (`n8n-docker-proxy`) or by mounting the Docker socket. The `Execute Command` node in n8n calls scripts placed at `/data/scripts/` which are also bind-mounted from the host. This avoids running n8n itself as root.

### `portal-provision` webhook
**Trigger:** `POST /webhook/portal-provision`
**Payload:** `{ name, url, description, icon, category, isInternal }`

**For internal services, steps:**
1. Call Keycloak Admin API → create confidential OIDC client in `agentic-sdlc` realm, set redirect URI to `https://<subdomain>/oauth2/callback`, assign `portal-user` realm role
2. Generate `oauth2-proxy` config block from template, append service block to `docker-compose.override.yml`
3. Execute `docker compose up -d oauth2_proxy_<name>` via Docker sidecar
4. Write Nginx location block to `conf.d/<name>.conf`, execute `nginx -s reload` on `sa-nginx-private`
5. Call Twingate API → create resource with private DNS alias `<subdomain>.private.sovereignadvisory.ai`
6. Append service entry to `services.json`, set `ssoTier: 1`
7. Return `{ success: true, card: { ... } }`

**For external services, steps:**
1. Append service entry to `services.json`, set `ssoTier: 3`
2. Return `{ success: true, card: { ... } }`

### `portal-update` webhook
**Trigger:** `POST /webhook/portal-update`
**Payload:** `{ id, fields: { name?, url?, description?, icon?, category? } }`
Updates the matching entry in `services.json` by merging `fields` into the existing service object (partial update — only provided keys are changed).

### `portal-delete` webhook
**Trigger:** `POST /webhook/portal-delete`
**Payload:** `{ id }`
Removes entry from `services.json`. Does **not** automatically tear down infrastructure (oauth2-proxy container, Nginx config, Twingate resource) — those require a separate manual cleanup step to avoid accidental data loss.

### `portal-update-categories` webhook
**Trigger:** `POST /webhook/portal-update-categories`
**Payload:** `{ categories: [...] }`
**Replaces** the entire `categories` array in `services.json` with the provided array. The portal always sends the complete current categories list in every call (no incremental patching).

---

## Docker Compose

New oauth2-proxy services added to `docker-compose.override.yml` (not the base `docker-compose.yml`) to keep the base file clean.

The portal and its oauth2-proxy in `docker-compose.yml`:

```yaml
services:
  portal:
    image: nginx:alpine
    networks:
      - private
    volumes:
      - ./portal:/usr/share/nginx/html:ro
      - ./nginx/conf.d/portal.conf:/etc/nginx/conf.d/portal.conf:ro
    labels:
      - "com.centurylinklabs.watchtower.enable=true"

  oauth2_proxy_portal:
    image: quay.io/oauth2-proxy/oauth2-proxy:latest
    networks:
      - private
    environment:
      OAUTH2_PROXY_PROVIDER: oidc
      OAUTH2_PROXY_OIDC_ISSUER_URL: https://kc.sovereignadvisory.ai/realms/agentic-sdlc
      OAUTH2_PROXY_CLIENT_ID: portal
      OAUTH2_PROXY_CLIENT_SECRET: "${PORTAL_CLIENT_SECRET}"
      OAUTH2_PROXY_REDIRECT_URL: https://home.private.sovereignadvisory.ai/oauth2/callback
      OAUTH2_PROXY_UPSTREAMS: http://portal:80/
      OAUTH2_PROXY_COOKIE_SECRET: "${PORTAL_COOKIE_SECRET}"
      OAUTH2_PROXY_COOKIE_DOMAIN: .sovereignadvisory.ai
      OAUTH2_PROXY_EMAIL_DOMAINS: "*"
      OAUTH2_PROXY_HTTP_ADDRESS: "0.0.0.0:4180"
      OAUTH2_PROXY_SKIP_PROVIDER_BUTTON: "true"
      OAUTH2_PROXY_CODE_CHALLENGE_METHOD: S256
    labels:
      - "com.centurylinklabs.watchtower.enable=true"
```

`PORTAL_CLIENT_SECRET` and `PORTAL_COOKIE_SECRET` are added to `.env`.

Each new oauth2-proxy added by the provisioning workflow follows the same env var pattern, templated with the service's subdomain, client ID, and secrets generated at provision time.

Networks: all portal-related containers join the existing `private` Docker network so they can reach `sa-nginx-private` and n8n.

---

## Nginx Config (portal.conf)

```nginx
server {
  listen 80;
  server_name home.private.sovereignadvisory.ai;

  location / {
    root /usr/share/nginx/html;
    try_files $uri $uri/ /index.html;
  }

  # Proxy n8n webhook calls from the portal
  location /api/ {
    proxy_pass http://n8n:5678/webhook/;
    proxy_set_header Host $host;
  }
}
```

The portal calls `/api/portal-provision` etc., which Nginx proxies to n8n webhook paths (`/webhook/portal-provision`, etc.).

---

## Keycloak Configuration

- **Realm:** `agentic-sdlc` (existing)
- **Client type:** Confidential (requires `client_secret`)
- **Client ID convention:** matches service name (e.g., `portal`, `litellm`, `jupyter`)
- **Redirect URI pattern:** `https://<subdomain>.private.sovereignadvisory.ai/oauth2/callback`
- **PKCE:** S256 required (already enforced on existing clients)
- **Realm role:** `portal-user` — all authenticated users are assigned this role; portal clients require it
- **Client scopes:** `openid`, `email`, `profile`

The provisioning workflow calls the Keycloak Admin REST API using a service account with realm-admin rights. Credentials stored in n8n credentials store.

---

## Twingate Resource

A Twingate resource at `home.private.sovereignadvisory.ai` pointing to the `oauth2_proxy_portal` container on port 4180, registered via the existing `twingate_add_resource` script pattern. `sa-nginx-private` routes traffic from the Twingate network to `oauth2_proxy_portal`.

---

## Out of Scope

- Mobile/responsive design (internal tool, desktop-only)
- Multi-user role differentiation (all authenticated users see the same portal)
- Service health monitoring / uptime polling (live dot is static for now; can be wired to a healthcheck endpoint in a future iteration)
- Automatic infrastructure teardown on service delete
- Notion/Cloudflare SAML federation (future upgrade path, not a blocker)

---

## Success Criteria

1. Navigating to `home.private.sovereignadvisory.ai` redirects to Keycloak login if not authenticated, then lands on the portal
2. All 10 initial services are visible and clickable as portal cards
3. **Tier 1 SSO criterion (deferred):** Clicking LiteLLM, JupyterLab, or Vaultwarden opens the service with no second login prompt — this requires each service's `oauth2_proxy_<name>` container to be provisioned first. This criterion is validated after the provisioning workflow is tested, not at portal launch.
4. Clicking n8n opens it and Vaultwarden autofills credentials (Tier 3)
5. The Add Service wizard completes end-to-end for an internal URL and the card appears in the grid
6. Edit service changes name/category and the card updates immediately
7. Add Category creates a new sidebar filter and the category is selectable in the wizard
