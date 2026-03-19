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

```
Browser → Twingate (*.private.sovereignadvisory.ai)
        → Nginx (sa-nginx-private)
        → oauth2-proxy (portal)
        → Keycloak (agentic-sdlc realm)
        → Portal static files (Nginx)
```

The portal itself is a **static HTML/CSS/JS application** served by Nginx. Dynamic state (service registry, categories) is stored in a `services.json` file that the portal fetches on load. All mutations to `services.json` go through an **n8n provisioning webhook** — the portal never writes config directly.

### Components

| Component | Role |
|---|---|
| `portal/index.html` | Main application (sidebar + grid UI) |
| `portal/services.json` | Source of truth for registered services and categories |
| `portal/assets/` | CSS, fonts, icons |
| `nginx/conf.d/portal.conf` | Nginx vhost: serves portal, proxies `/api/` to n8n |
| `docker-compose.yml` | `portal` service (Nginx), `oauth2_proxy_portal` service |
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

| Service | Category | URL |
|---|---|---|
| n8n | Automation | `https://n8n.private.sovereignadvisory.ai` |
| Pipeline Board | Automation | `https://pipeline.private.sovereignadvisory.ai` |
| LiteLLM | AI / Models | `https://litellm.private.sovereignadvisory.ai` |
| JupyterLab | AI / Models | `https://jupyter.private.sovereignadvisory.ai` |
| Vaultwarden | Security | `https://vault.private.sovereignadvisory.ai` |
| Keycloak | Security | `https://kc.sovereignadvisory.ai` |
| Twingate | Infrastructure | `https://www.twingate.com/dashboard` |
| Cloudflare | Infrastructure | `https://dash.cloudflare.com` |
| Hostinger | Infrastructure | `https://hpanel.hostinger.com` |
| Notion | Productivity | `https://notion.so` |

---

## Authentication & SSO Passthrough

Authentication to the portal is handled by **oauth2-proxy + Keycloak** (agentic-sdlc realm). After a single login, cards open services directly based on one of three tiers:

### Tier 1 — Full SSO (internal services behind oauth2-proxy)
Services provisioned through the Add Service wizard get their own `oauth2-proxy` container configured against the same Keycloak realm. The Keycloak session cookie is valid across all `*.private.sovereignadvisory.ai` subdomains. Clicking the card opens the service with no second authentication prompt.

**Applies to:** n8n (once re-provisioned), LiteLLM, JupyterLab, Vaultwarden (web UI), Grafana, and any future internal service added via the wizard.

### Tier 2 — Header injection (token-accepting services)
For services that accept `Authorization: Bearer` or `X-Auth-Request-User` / `X-Auth-Request-Email` headers, Nginx injects these from the oauth2-proxy session using `auth_request`. The user gets transparent access without a second prompt.

**Applies to:** JupyterLab (token-based), LiteLLM admin UI.

### Tier 3 — Vaultwarden autofill assist
For services that have their own login UI and cannot federate with Keycloak (community edition limitations or external SaaS), the portal opens the service URL in a new tab and the Vaultwarden browser extension detects the login form and autofills credentials.

**Applies to:**
- **n8n community edition** — enterprise OIDC not included; autofill via Vaultwarden
- **Keycloak admin console** — is the IdP itself; cannot proxy its own auth
- **Cloudflare, Hostinger, Notion** — external SaaS with independent auth

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
1. Keycloak OIDC Client — create client, set redirect URIs, assign roles
2. oauth2-proxy container — generate config, add to docker-compose, restart
3. Nginx reverse proxy rule — add upstream + location block for new subdomain
4. Twingate resource — register private DNS entry
5. Portal card — service appears in selected category with live status

**External:**
1. Portal card — link card added to the portal
2. Vaultwarden entry (optional) — credential record linked from the card

### Step 4 — Deploy
The portal POSTs to the n8n provisioning webhook (`/webhook/portal-provision`). The step shows an animated progress bar advancing through each provisioning task. On completion, displays service name + "Service is live and accessible via SSO." The modal closes and the new card appears in the grid.

The n8n workflow is responsible for all side-effects (Keycloak API, Docker, Nginx, Twingate API, `services.json` update). The portal is stateless — it only fires the webhook and reflects the result.

---

## Edit Service

A `✎ edit` button appears bottom-left on each card on hover. Opens a single-page modal (no steps) pre-populated with the card's current values:

- Display Name, Service URL, Description, Icon (emoji)
- Category tile picker (same component as wizard step 2, current category pre-selected)
- **Save Changes** — fires `POST /webhook/portal-update` with updated fields, refreshes card in place
- **Remove** (red, left-aligned) — confirms then fires `POST /webhook/portal-delete`, removes card from grid and sidebar counts update

---

## Add Category

Available in two places:
1. Wizard Step 2 — `＋ New` tile
2. Edit modal category picker — same `＋ New` tile

Flow: enter emoji + name → creates entry in `categories` registry → adds sidebar filter button → adds tile in current picker. Category persists to `services.json` via a `POST /webhook/portal-update-categories` call.

Auto-assigns a harmonious tag color from a rotating palette of muted hues.

---

## services.json Schema

```json
{
  "categories": [
    { "key": "automation", "label": "Automation", "icon": "⚡",
      "color": "rgba(74,222,128,0.08)", "text": "#6ee7a0", "border": "rgba(74,222,128,0.15)" }
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
      "ssoTier": 1
    }
  ]
}
```

`ssoTier` (1/2/3) is set by the provisioning workflow and used by the portal to annotate card tooltips indicating auth method.

---

## n8n Provisioning Workflows

### `portal-provision` webhook
**Trigger:** `POST /webhook/portal-provision`
**Payload:** `{ name, url, description, icon, category, isInternal }`

**For internal services, steps:**
1. Call Keycloak Admin API → create OIDC client, set redirect URIs
2. Generate `oauth2-proxy` config from template, append to `docker-compose.override.yml`
3. Run `docker compose up -d oauth2_proxy_<name>`
4. Append Nginx location block to `conf.d/<name>.conf`, reload Nginx
5. Call Twingate API → create resource with private DNS alias
6. Append service entry to `services.json`, set `ssoTier: 1`
7. Return `{ success: true, card: { ... } }`

**For external services, steps:**
1. Append service entry to `services.json`, set `ssoTier: 3`
2. Return `{ success: true, card: { ... } }`

### `portal-update` webhook
**Trigger:** `POST /webhook/portal-update`
**Payload:** `{ id, fields: { name?, url?, description?, icon?, category? } }`
Updates the matching entry in `services.json`.

### `portal-delete` webhook
**Trigger:** `POST /webhook/portal-delete`
**Payload:** `{ id }`
Removes entry from `services.json`. Does **not** automatically tear down infrastructure (oauth2-proxy container, Nginx config, Twingate resource) — those require a separate manual cleanup step to avoid accidental data loss.

### `portal-update-categories` webhook
**Trigger:** `POST /webhook/portal-update-categories`
**Payload:** `{ categories: [...] }`
Replaces the `categories` array in `services.json`.

---

## Docker Compose

New services added to `docker-compose.override.yml` (not the base `docker-compose.yml`) to keep the base file clean. Watchtower label added to oauth2-proxy containers for nightly updates.

The portal container:
```yaml
portal:
  image: nginx:alpine
  volumes:
    - ./portal:/usr/share/nginx/html:ro
    - ./nginx/conf.d/portal.conf:/etc/nginx/conf.d/portal.conf:ro
  labels:
    - "com.centurylinklabs.watchtower.enable=true"

oauth2_proxy_portal:
  image: quay.io/oauth2-proxy/oauth2-proxy:latest
  # standard oauth2-proxy config against agentic-sdlc Keycloak realm
```

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

The portal calls `/api/portal-provision` etc., which Nginx proxies to the n8n webhook paths.

---

## Twingate Resource

A Twingate resource at `home.private.sovereignadvisory.ai` pointing to the portal Nginx container, registered via the existing `twingate_add_resource` script pattern.

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
2. All 10 initial services are visible and clickable
3. Clicking an internal service (LiteLLM, JupyterLab, Vaultwarden) opens it with no second login prompt (Tier 1)
4. Clicking n8n opens it and Vaultwarden autofills credentials (Tier 3)
5. The Add Service wizard completes end-to-end for an internal URL and the card appears in the grid
6. Edit service changes name/category and the card updates immediately
7. Add Category creates a new sidebar filter and the category is selectable in the wizard
