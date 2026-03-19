# Internal Access Portal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a branded internal access portal at `home.private.sovereignadvisory.ai` that gives authenticated users a single launchpad for all Sovereign Advisory services, with SSO passthrough, Vaultwarden autofill assist, LiteLLM API status badges, and an Add Service wizard backed by n8n provisioning webhooks.

**Architecture:** Static HTML/CSS/JS portal served by a new `portal` Nginx container, gated by `oauth2_proxy_portal` (Keycloak agentic-sdlc realm), routed through the existing `sa_nginx_private` edge proxy. Service registry lives in `portal/services.json`; all mutations go through n8n webhooks. A separate `providers` category holds AI provider cards with live API status badges fetched from LiteLLM.

**Tech Stack:** Vanilla HTML/CSS/JS (no build step), nginx:alpine, oauth2-proxy v7.6.0, Keycloak (existing), n8n (existing), LiteLLM (existing), Docker Compose, Python 3 (Keycloak bootstrap script).

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `portal/index.html` | **Create** | Full portal UI — sidebar, grid, cards, wizard, edit modal, badge logic |
| `portal/services.json` | **Create** | Initial registry — 14 services across 6 categories |
| `nginx/conf.d/portal.conf` | **Create** | Portal container vhost — serves static files, proxies `/api/` to n8n and `/api/litellm-health` to LiteLLM |
| `nginx-private/conf.d/private.conf` | **Modify** | Append `home.private.sovereignadvisory.ai` server block |
| `docker-compose.yml` | **Modify** | Add `portal` and `oauth2-proxy-portal` services; patch n8n volumes |
| `docker-compose.override.yml` | **Create** | Initial `services: {}` — Docker Compose auto-merges this |
| `.env` | **Modify** | Add `PORTAL_OIDC_CLIENT_SECRET` |
| `scripts/portal_docker_up.sh` | **Create** | Used by n8n: `docker run` a new oauth2-proxy container |
| `scripts/portal_nginx_reload.sh` | **Create** | Used by n8n: `docker exec sa_nginx_private nginx -s reload` |
| `scripts/keycloak_portal_bootstrap.py` | **Create** | One-time: creates `portal` OIDC client in Keycloak |

n8n workflows are created through the n8n UI (not as JSON files) — each task specifies the node sequence.

---

## Task 1: Initial `services.json`

**Files:**
- Create: `portal/services.json`

- [ ] **Step 1.1: Create the portal directory and services.json**

```bash
mkdir -p portal
```

Create `portal/services.json`:

```json
{
  "categories": [
    { "key": "automation", "label": "Automation", "icon": "⚡", "color": "rgba(74,222,128,0.08)", "text": "#6ee7a0", "border": "rgba(74,222,128,0.15)" },
    { "key": "ai", "label": "AI / Models", "icon": "🤖", "color": "rgba(165,180,252,0.08)", "text": "#a5b4fc", "border": "rgba(165,180,252,0.15)" },
    { "key": "providers", "label": "AI Providers", "icon": "🧠", "color": "rgba(252,211,77,0.08)", "text": "#fcd34d", "border": "rgba(252,211,77,0.15)" },
    { "key": "security", "label": "Security", "icon": "🔐", "color": "rgba(248,113,113,0.08)", "text": "#fca5a5", "border": "rgba(248,113,113,0.15)" },
    { "key": "productivity", "label": "Productivity", "icon": "📝", "color": "rgba(196,181,253,0.08)", "text": "#c4b5fd", "border": "rgba(196,181,253,0.15)" },
    { "key": "infra", "label": "Infrastructure", "icon": "🌐", "color": "rgba(125,211,252,0.08)", "text": "#7dd3fc", "border": "rgba(125,211,252,0.15)" }
  ],
  "services": [
    { "id": "n8n", "name": "n8n", "url": "https://n8n.private.sovereignadvisory.ai", "description": "Workflow automation", "icon": "⚡", "category": "automation", "favorite": false, "ssoTier": 1 },
    { "id": "pipeline", "name": "Pipeline Board", "url": "https://pipeline.private.sovereignadvisory.ai", "description": "Lead pipeline management", "icon": "🎯", "category": "automation", "favorite": false, "ssoTier": 3 },
    { "id": "litellm", "name": "LiteLLM", "url": "https://litellm.private.sovereignadvisory.ai", "description": "AI proxy · 4 model tiers", "icon": "🤖", "category": "ai", "favorite": false, "ssoTier": 1 },
    { "id": "jupyter", "name": "JupyterLab", "url": "https://jupyter.private.sovereignadvisory.ai", "description": "Interactive dev notebooks", "icon": "📓", "category": "ai", "favorite": false, "ssoTier": 1 },
    { "id": "claude", "name": "Claude", "url": "https://claude.ai", "description": "Anthropic AI assistant", "icon": "🧠", "category": "providers", "favorite": false, "ssoTier": 3, "credentialHint": "https://vault.private.sovereignadvisory.ai/#search/anthropic", "apiProvider": "anthropic" },
    { "id": "chatgpt", "name": "ChatGPT", "url": "https://chat.openai.com", "description": "OpenAI chat interface", "icon": "💬", "category": "providers", "favorite": false, "ssoTier": 3, "credentialHint": "https://vault.private.sovereignadvisory.ai/#search/openai", "apiProvider": "openai" },
    { "id": "gemini", "name": "Gemini", "url": "https://gemini.google.com", "description": "Google AI assistant", "icon": "✨", "category": "providers", "favorite": false, "ssoTier": 3, "credentialHint": "https://vault.private.sovereignadvisory.ai/#search/gemini", "apiProvider": "gemini" },
    { "id": "grok", "name": "Grok", "url": "https://grok.x.ai", "description": "xAI assistant", "icon": "⚡", "category": "providers", "favorite": false, "ssoTier": 3, "credentialHint": "https://vault.private.sovereignadvisory.ai/#search/xai", "apiProvider": "xai" },
    { "id": "vaultwarden", "name": "Vaultwarden", "url": "https://vault.private.sovereignadvisory.ai", "description": "Credentials & secrets vault", "icon": "🔐", "category": "security", "favorite": false, "ssoTier": 1 },
    { "id": "keycloak", "name": "Keycloak", "url": "https://kc.sovereignadvisory.ai", "description": "Identity & SSO admin", "icon": "🔑", "category": "security", "favorite": false, "ssoTier": 3, "credentialHint": "https://vault.private.sovereignadvisory.ai/#search/keycloak" },
    { "id": "notion", "name": "Notion", "url": "https://notion.so", "description": "Workspace & docs", "icon": "📝", "category": "productivity", "favorite": false, "ssoTier": 3, "credentialHint": "https://vault.private.sovereignadvisory.ai/#search/notion" },
    { "id": "twingate", "name": "Twingate", "url": "https://www.twingate.com/dashboard", "description": "Private network access", "icon": "🛡️", "category": "infra", "favorite": false, "ssoTier": 3, "credentialHint": "https://vault.private.sovereignadvisory.ai/#search/twingate" },
    { "id": "cloudflare", "name": "Cloudflare", "url": "https://dash.cloudflare.com", "description": "DNS & CDN management", "icon": "🌐", "category": "infra", "favorite": false, "ssoTier": 3, "credentialHint": "https://vault.private.sovereignadvisory.ai/#search/cloudflare" },
    { "id": "hostinger", "name": "Hostinger", "url": "https://hpanel.hostinger.com", "description": "Domain & hosting", "icon": "🖥️", "category": "infra", "favorite": false, "ssoTier": 3, "credentialHint": "https://vault.private.sovereignadvisory.ai/#search/hostinger" }
  ]
}
```

- [ ] **Step 1.2: Validate JSON**

```bash
python3 -c "import json; d=json.load(open('portal/services.json')); print(f'{len(d[\"categories\"])} categories, {len(d[\"services\"])} services')"
```

Expected: `6 categories, 14 services`

- [ ] **Step 1.3: Commit**

```bash
git add portal/services.json
git commit -m "feat: add portal/services.json with 14 initial services across 6 categories"
```

---

## Task 2: Portal HTML — Core Layout and Dynamic Data

**Files:**
- Create: `portal/index.html`

The existing brainstorm mockup at `.superpowers/brainstorm/1995122-1773927454/portal-hybrid-v2.html` (1256 lines) is a fully-functional interactive prototype with hardcoded service cards. This task adapts it to load data dynamically from `services.json`.

**Key changes from the mockup:**
1. Remove all hardcoded `<div class="card">` elements from the grid
2. Add `async function loadPortal()` that `fetch('./services.json')` and renders cards + sidebar
3. Keep all JS logic (filter, favorites, recently used, category management) — it works correctly
4. Add API badge rendering (Task 3)
5. Wire wizard/edit modal `fetch` calls to `/api/` endpoints (Task 3)

- [ ] **Step 2.1: Serve the mockup locally to confirm baseline**

```bash
cd .superpowers/brainstorm/1995122-1773927454
python3 -m http.server 54400
```

Open `http://localhost:54400/portal-hybrid-v2.html`. Verify all 14 cards visible, sidebar filters work, wizard opens. This is the target — preserve all behaviour.

Stop the server (`Ctrl+C`), return to project root.

- [ ] **Step 2.2: Create `portal/index.html` — structure and styles**

Copy the mockup as the starting point:

```bash
cp .superpowers/brainstorm/1995122-1773927454/portal-hybrid-v2.html portal/index.html
```

- [ ] **Step 2.3: Replace hardcoded card HTML with dynamic rendering**

In `portal/index.html`, find the `<div class="grid" id="grid">` element and remove all hardcoded `.card` children — leave only `<div class="grid" id="grid"></div>`.

Then add a `loadPortal()` function that:
1. Fetches `./services.json`
2. Calls existing `initCategories(data.categories)` to populate the JS `categories` object and rebuild the sidebar
3. Calls `renderCards(data.services)` to inject cards into the grid

Add `renderCards(services)` function:

```js
function renderCards(services) {
  const grid = document.getElementById('grid');
  grid.innerHTML = '';
  services.forEach(svc => {
    const cat = categories[svc.category] || { text: '#a09aaf', color: 'rgba(160,154,175,0.08)', border: 'rgba(160,154,175,0.15)' };
    const card = document.createElement('a');
    card.className = 'card';
    card.href = svc.url;
    card.target = '_blank';
    card.rel = 'noopener noreferrer';
    card.dataset.id = svc.id;
    card.dataset.category = svc.category;
    card.dataset.ssoTier = svc.ssoTier;
    card.dataset.apiProvider = svc.apiProvider || '';
    card.dataset.url = svc.url;
    card.dataset.name = svc.name;
    card.dataset.description = svc.description || '';
    card.dataset.icon = svc.icon;
    if (svc.favorite) card.dataset.favorite = 'true';
    card.innerHTML = `
      ${svc.apiProvider ? '<span class="api-badge" data-provider="' + svc.apiProvider + '">API —</span>' : ''}
      <button class="card-star${svc.favorite ? ' active' : ''}" onclick="toggleFavorite(event,this)">★</button>
      <div class="card-icon">${svc.icon}</div>
      <div class="card-name">${svc.name}</div>
      ${svc.description ? '<div class="card-desc">' + svc.description + '</div>' : ''}
      <div class="card-footer">
        <span class="card-tag" style="background:${cat.color};color:${cat.text};border:1px solid ${cat.border}">${cat.label || svc.category}</span>
        <span class="card-status"><span class="live-dot"></span> live</span>
      </div>
      <button class="card-edit" onclick="openEdit(event,this.closest('.card'))">✎ edit</button>
    `;
    grid.appendChild(card);
  });
  applyFilter(currentFilter);
  updateCounts();
}
```

Replace the existing `initCategories` call (if hardcoded) with one driven by data from `services.json`.

Call `loadPortal()` from the `DOMContentLoaded` handler instead of the existing hardcoded init.

- [ ] **Step 2.4: Local smoke test with mock server**

```bash
cd portal
python3 -m http.server 8080
```

Open `http://localhost:8080`. Verify:
- All 14 service cards appear
- 6 categories in sidebar with correct counts
- Sidebar filter buttons work (click "AI Providers" → only Claude, ChatGPT, Gemini, Grok visible)
- Favorites star toggles (copper colour when active)
- Recently Used updates on card click (check sidebar Quick Access section)

Stop server, return to project root.

- [ ] **Step 2.5: Commit**

```bash
git add portal/index.html
git commit -m "feat: portal index.html — dynamic services.json loading, card rendering, sidebar"
```

---

## Task 3: Portal HTML — API Badge Logic and Webhook Wiring

**Files:**
- Modify: `portal/index.html`

- [ ] **Step 3.1: Add API badge CSS**

In `portal/index.html` `<style>` block, add:

```css
.api-badge {
  position: absolute;
  top: 10px; left: 10px;
  font-size: 9px;
  font-weight: 600;
  padding: 2px 6px;
  border-radius: 999px;
  letter-spacing: 0.04em;
  background: rgba(160,154,175,0.15);
  color: var(--text-3);
  pointer-events: none;
}
.api-badge.healthy {
  background: rgba(74,222,128,0.15);
  color: #6ee7a0;
}
.api-badge.unhealthy {
  background: rgba(248,113,113,0.12);
  color: #fca5a5;
}
```

- [ ] **Step 3.2: Add `loadApiStatus()` function**

Add after `renderCards`:

```js
async function loadApiStatus() {
  try {
    const res = await fetch('/api/litellm-health');
    if (!res.ok) return; // silently skip if LiteLLM unreachable
    const data = await res.json();
    const healthy = new Set(data.healthy_providers || []);
    const unhealthy = new Set(data.unhealthy_providers || []);
    document.querySelectorAll('.api-badge[data-provider]').forEach(badge => {
      const p = badge.dataset.provider;
      if (healthy.has(p)) {
        badge.textContent = 'API ✓';
        badge.classList.add('healthy');
      } else if (unhealthy.has(p)) {
        badge.textContent = 'API ✗';
        badge.classList.add('unhealthy');
      }
      // else leave as 'API —' (not configured)
    });
  } catch (e) {
    // LiteLLM down — hide all badges rather than show stale data
    document.querySelectorAll('.api-badge').forEach(b => b.style.display = 'none');
  }
}
```

Call `loadApiStatus()` after `renderCards()` inside `loadPortal()`.

- [ ] **Step 3.3: Wire wizard Step 4 to real `/api/` endpoint**

In the wizard's `finishWizard()` function (or equivalent), replace any mock/console.log with:

```js
async function submitProvision(payload) {
  const res = await fetch('/api/portal-provision', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}
```

On success, call `renderCards([...currentServices, response.card])` and close the modal.
On error, show the error message in Step 4 and a "Try again" button.

- [ ] **Step 3.4: Wire edit/delete modals to real endpoints**

In `saveEdit()` (Edit modal Save Changes):

```js
async function saveEdit(id, fields) {
  await fetch('/api/portal-update', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ id, fields })
  });
  // re-fetch services.json to get authoritative state
  await loadPortal();
}
```

In `deleteService(id)` (Edit modal Remove):

```js
async function deleteService(id) {
  await fetch('/api/portal-delete', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ id })
  });
  await loadPortal();
}
```

- [ ] **Step 3.5: Wire Add Category to `/api/portal-update-categories`**

In `addCategory()` (after creating new category object):

```js
async function persistCategories() {
  await fetch('/api/portal-update-categories', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ categories: Object.values(categories) })
  });
}
```

Call `persistCategories()` after updating the `categories` object.

- [ ] **Step 3.6: Local test with mock API responses**

Test badge rendering without a live LiteLLM — create `portal/api-mock.json`:

```json
{ "healthy_providers": ["anthropic", "gemini"], "unhealthy_providers": ["openai"] }
```

Temporarily change `loadApiStatus()` fetch URL to `./api-mock.json` and verify:
- Claude badge shows `API ✓` (green)
- Gemini badge shows `API ✓` (green)
- ChatGPT badge shows `API ✗` (red)
- Grok badge shows `API —` (grey)

Revert fetch URL back to `/api/litellm-health` and delete `portal/api-mock.json`.

- [ ] **Step 3.7: Commit**

```bash
git add portal/index.html
git commit -m "feat: portal — API status badges, webhook wiring for provision/edit/delete/categories"
```

---

## Task 4: Nginx Configuration

**Files:**
- Create: `nginx/conf.d/portal.conf`
- Modify: `nginx-private/conf.d/private.conf`

- [ ] **Step 4.1: Create `nginx/conf.d/portal.conf`**

```nginx
server {
    listen 80;
    server_name home.private.sovereignadvisory.ai;

    root /usr/share/nginx/html;

    location / {
        try_files $uri $uri/ /index.html;
    }

    # n8n provisioning webhooks
    location /api/ {
        proxy_pass http://n8n:5678/webhook/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_read_timeout 120s;
    }

    # LiteLLM provider health — used by portal for API access badges
    location /api/litellm-health {
        proxy_pass http://litellm:4000/health/services;
        proxy_set_header Host $host;
        proxy_set_header Authorization "Bearer $LITELLM_API_KEY";
    }
}
```

> **Note:** The `$LITELLM_API_KEY` nginx variable requires `env LITELLM_API_KEY;` in the nginx.conf `main` block, or alternatively use a Lua module. Simpler alternative: hardcode the key value here (it's an internal container-to-container call, not exposed externally). Check `nginx/nginx.conf` to see if env variable passing is already configured; if not, paste the key value directly.

- [ ] **Step 4.2: Check existing nginx.conf for env var support**

```bash
cat nginx/nginx.conf | grep -i "env\|lua\|perl"
```

If no env var support: replace `$LITELLM_API_KEY` in `portal.conf` with the actual key value from `.env`.

- [ ] **Step 4.3: Append home.private server block to `nginx-private/conf.d/private.conf`**

Append to the end of the file:

```nginx
# -----------------------------------------
# Internal Access Portal — home.private.sovereignadvisory.ai
# Routes to oauth2_proxy_portal for Keycloak SSO gating
# -----------------------------------------
server {
    listen 443 ssl;
    http2 on;
    server_name home.private.sovereignadvisory.ai;

    ssl_certificate     /etc/letsencrypt/live/private.sovereignadvisory.ai/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/private.sovereignadvisory.ai/privkey.pem;

    client_max_body_size 10m;

    location / {
        proxy_pass http://oauth2_proxy_portal:4185;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $connection_upgrade;
    }
}
```

- [ ] **Step 4.4: Validate Nginx config syntax**

```bash
docker exec sa_nginx_private nginx -t 2>&1
```

Expected: `syntax is ok` and `test is successful`. If `sa_nginx_private` is not running, verify with:

```bash
docker ps | grep sa_nginx
```

- [ ] **Step 4.5: Commit**

```bash
git add nginx/conf.d/portal.conf nginx-private/conf.d/private.conf
git commit -m "feat: add portal.conf and home.private server block for sa_nginx_private"
```

---

## Task 5: Docker Compose — Portal Services

**Files:**
- Modify: `docker-compose.yml`
- Create: `docker-compose.override.yml`
- Modify: `.env`

- [ ] **Step 5.1: Generate a `PORTAL_OIDC_CLIENT_SECRET`**

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

Copy the output. Add to `.env`:

```bash
echo "PORTAL_OIDC_CLIENT_SECRET=<paste-value-here>" >> .env
```

- [ ] **Step 5.2: Add `portal` and `oauth2-proxy-portal` services to `docker-compose.yml`**

Append before the final `volumes:` top-level key (or at the end of the `services:` block):

```yaml
  # -----------------------------------------
  # Internal Access Portal — home.private.sovereignadvisory.ai
  # Static HTML/JS portal served by Nginx, gated by oauth2_proxy_portal
  # -----------------------------------------
  portal:
    image: nginx:alpine
    container_name: portal
    volumes:
      - ./portal:/usr/share/nginx/html:ro
      - ./nginx/conf.d/portal.conf:/etc/nginx/conf.d/default.conf:ro
    networks:
      - vibe_net
    restart: unless-stopped
    labels:
      - "com.centurylinklabs.watchtower.enable=true"

  # -----------------------------------------
  # oauth2-proxy — Internal Access Portal (port 4185)
  # Authenticates portal access via Keycloak agentic-sdlc realm
  # -----------------------------------------
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

- [ ] **Step 5.3: Patch n8n service — add portal volume and Docker socket**

In `docker-compose.yml`, find the n8n `volumes:` block and add two lines:

```yaml
      - ./portal:/data/portal          # r/w access to services.json and docker-compose.override.yml
      - /var/run/docker.sock:/var/run/docker.sock  # for docker compose up via provisioning scripts
```

The full n8n volumes block should now be:

```yaml
    volumes:
      - n8n_data:/home/node/.n8n
      - ./workflows:/data/workflows
      - ./output:/data/output
      - ./opportunities:/data/opportunities
      - ./scripts:/data/scripts:ro
      - ./portal:/data/portal
      - /var/run/docker.sock:/var/run/docker.sock
```

- [ ] **Step 5.4: Create `docker-compose.override.yml`**

```yaml
# Auto-merged by Docker Compose. Provisioned oauth2-proxy containers are appended here.
services: {}
```

- [ ] **Step 5.5: Validate compose config**

```bash
docker compose config --quiet && echo "compose config valid"
```

Expected: `compose config valid` with no errors.

- [ ] **Step 5.6: Commit**

```bash
git add docker-compose.yml docker-compose.override.yml .env
git commit -m "feat: add portal and oauth2_proxy_portal services; patch n8n with portal volume and docker socket"
```

---

## Task 6: Keycloak — Bootstrap Portal Client

**Files:**
- Create: `scripts/keycloak_portal_bootstrap.py`

The existing `scripts/keycloak_bootstrap.py` handles realm/client creation. We need a focused script that creates only the `portal` OIDC client.

- [ ] **Step 6.1: Create `scripts/keycloak_portal_bootstrap.py`**

```python
#!/usr/bin/env python3
"""Create the 'portal' OIDC client in Keycloak agentic-sdlc realm.

Usage:
  KEYCLOAK_URL=https://kc.sovereignadvisory.ai \
  KEYCLOAK_ADMIN=admin \
  KEYCLOAK_ADMIN_PASSWORD=<password> \
  PORTAL_OIDC_CLIENT_SECRET=<secret> \
  python3 scripts/keycloak_portal_bootstrap.py
"""
import os, sys, json
from urllib.request import Request, urlopen
from urllib.error import HTTPError

KC_URL = os.environ["KEYCLOAK_URL"].rstrip("/")
ADMIN = os.environ["KEYCLOAK_ADMIN"]
PASSWORD = os.environ["KEYCLOAK_ADMIN_PASSWORD"]
CLIENT_SECRET = os.environ["PORTAL_OIDC_CLIENT_SECRET"]
REALM = "agentic-sdlc"

def req(url, method="GET", data=None, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = json.dumps(data).encode() if data else None
    r = Request(url, data=body, headers=headers, method=method)
    try:
        with urlopen(r) as resp:
            return resp.status, resp.read()
    except HTTPError as e:
        return e.code, e.read()

# 1. Get admin token
status, body = req(
    f"{KC_URL}/realms/master/protocol/openid-connect/token",
    method="POST",
    data=None
)
# Use form encoding for token endpoint
from urllib.parse import urlencode
from urllib.request import urlopen, Request as Req
r = Req(
    f"{KC_URL}/realms/master/protocol/openid-connect/token",
    data=urlencode({"grant_type": "password", "client_id": "admin-cli",
                    "username": ADMIN, "password": PASSWORD}).encode(),
    headers={"Content-Type": "application/x-www-form-urlencoded"}
)
with urlopen(r) as resp:
    token = json.loads(resp.read())["access_token"]

print("✓ Got admin token")

# 2. Check if client already exists
status, body = req(f"{KC_URL}/admin/realms/{REALM}/clients?clientId=portal", token=token)
clients = json.loads(body)
if clients:
    print("✓ 'portal' client already exists — updating secret")
    client_id = clients[0]["id"]
    req(f"{KC_URL}/admin/realms/{REALM}/clients/{client_id}/client-secret",
        method="PUT",
        data={"type": "secret", "value": CLIENT_SECRET},
        token=token)
    print("✓ Secret updated")
    sys.exit(0)

# 3. Create client
client_def = {
    "clientId": "portal",
    "name": "Internal Access Portal",
    "enabled": True,
    "protocol": "openid-connect",
    "publicClient": False,
    "secret": CLIENT_SECRET,
    "redirectUris": ["https://home.private.sovereignadvisory.ai/oauth2/callback"],
    "webOrigins": ["https://home.private.sovereignadvisory.ai"],
    "standardFlowEnabled": True,
    "directAccessGrantsEnabled": False,
    "attributes": {"pkce.code.challenge.method": "S256"},
    "defaultClientScopes": ["openid", "email", "profile"]
}
status, body = req(f"{KC_URL}/admin/realms/{REALM}/clients", method="POST",
                   data=client_def, token=token)
if status == 201:
    print("✓ 'portal' client created successfully")
else:
    print(f"✗ Failed: HTTP {status} — {body.decode()}")
    sys.exit(1)
```

- [ ] **Step 6.2: Run the bootstrap script**

```bash
KEYCLOAK_URL=https://kc.sovereignadvisory.ai \
KEYCLOAK_ADMIN=admin \
KEYCLOAK_ADMIN_PASSWORD=$(grep KEYCLOAK_ADMIN_PASSWORD .env | cut -d= -f2) \
PORTAL_OIDC_CLIENT_SECRET=$(grep PORTAL_OIDC_CLIENT_SECRET .env | cut -d= -f2) \
python3 scripts/keycloak_portal_bootstrap.py
```

Expected: `✓ Got admin token` then `✓ 'portal' client created successfully`

If the Keycloak URL differs from what's in `.env`, adjust accordingly.

- [ ] **Step 6.3: Verify in Keycloak admin console**

Open `https://kc.sovereignadvisory.ai` → `agentic-sdlc` realm → Clients → confirm `portal` client exists with redirect URI `https://home.private.sovereignadvisory.ai/oauth2/callback`.

- [ ] **Step 6.4: Commit**

```bash
git add scripts/keycloak_portal_bootstrap.py
git commit -m "feat: add keycloak_portal_bootstrap.py — creates portal OIDC client in agentic-sdlc realm"
```

---

## Task 7: Start Portal and Smoke Test

- [ ] **Step 7.1: Start portal containers**

```bash
docker compose up -d portal oauth2-proxy-portal
```

- [ ] **Step 7.2: Check container health**

```bash
docker compose ps portal oauth2-proxy-portal
docker compose logs --tail=20 portal
docker compose logs --tail=20 oauth2-proxy-portal
```

Expected: both containers running, no errors. `oauth2_proxy_portal` should log that it's listening on 0.0.0.0:4185.

- [ ] **Step 7.3: Test portal container serves files**

```bash
docker exec portal wget -qO- http://localhost:80/ | grep -c "SOVEREIGN ADVISORY"
```

Expected: `1` (the title text appears in the HTML)

- [ ] **Step 7.4: Reload sa_nginx_private with new home.conf block**

```bash
docker exec sa_nginx_private nginx -t && docker exec sa_nginx_private nginx -s reload
```

- [ ] **Step 7.5: Register Twingate resource for home.private**

```bash
TWINGATE_API_KEY=$(grep TWINGATE_API_KEY .env | cut -d= -f2) \
TWINGATE_NETWORK=$(grep TWINGATE_NETWORK .env | cut -d= -f2) \
TWINGATE_REMOTE_NETWORK=$(grep TWINGATE_REMOTE_NETWORK .env | cut -d= -f2) \
python3 scripts/twingate/twingate_add_resource.py \
  --name "Internal Access Portal" \
  --address "home.private.sovereignadvisory.ai"
```

If TWINGATE env vars are not in `.env`, find them in your Twingate dashboard (Settings → API) and add them.

- [ ] **Step 7.6: End-to-end browser test**

With Twingate connected, open `https://home.private.sovereignadvisory.ai`:
- Redirects to Keycloak login ✓
- After login, portal loads with all 14 cards ✓
- Sidebar shows 6 categories with counts ✓
- Clicking a Tier 1 card (n8n, LiteLLM) opens without second login prompt ✓
- AI Provider cards show API badges (grey `API —` if LiteLLM keys not configured, green `API ✓` if they are) ✓

- [ ] **Step 7.7: Commit**

```bash
git commit --allow-empty -m "feat: portal live at home.private.sovereignadvisory.ai — smoke test passed"
```

---

## Task 8: Shell Scripts for n8n Provisioning

**Files:**
- Create: `scripts/portal_docker_up.sh`
- Create: `scripts/portal_nginx_reload.sh`

These scripts are executed by n8n's Execute Command node (mounted read-only at `/data/scripts/`).

- [ ] **Step 8.1: Create `scripts/portal_docker_up.sh`**

```bash
#!/bin/bash
# Usage: portal_docker_up.sh <service_name>
# Runs a new oauth2-proxy container from docker-compose.override.yml
# Called by n8n after appending a new service block to docker-compose.override.yml
set -euo pipefail

SERVICE_NAME="${1:?Usage: portal_docker_up.sh <service_name>}"
CONTAINER_NAME="oauth2_proxy_${SERVICE_NAME}"
COMPOSE_FILE="/data/portal/docker-compose.override.yml"

echo "Starting container: ${CONTAINER_NAME}"
docker compose -f /host-compose/docker-compose.yml -f "${COMPOSE_FILE}" up -d "${CONTAINER_NAME}"
echo "Done: ${CONTAINER_NAME} started"
```

> **Implementation note:** n8n's Execute Command node runs inside the n8n container. The `docker` binary must be available there (it is, since we mounted `/var/run/docker.sock`). However, `docker compose` needs the base `docker-compose.yml` to resolve network names and shared config. Mount the project root into n8n as `/host-compose` — add `- .:/host-compose:ro` to n8n's volumes in `docker-compose.yml`. Alternatively, use `docker run` directly (simpler — see note below).

**Simpler alternative for portal_docker_up.sh** — use `docker run` directly rather than `docker compose`, so there's no dependency on the host compose file path. The n8n provisioning workflow builds the `docker run` command with all required env vars from the Keycloak provisioning step.

Replace the script with:

```bash
#!/bin/bash
# Usage: portal_docker_up.sh <container_name> <image> [docker run args...]
# Starts a container directly via docker run (avoids compose file path dependency)
set -euo pipefail
CONTAINER_NAME="${1:?missing container name}"; shift
IMAGE="${1:?missing image}"; shift
echo "Starting ${CONTAINER_NAME}..."
docker run -d --name "${CONTAINER_NAME}" --network vibe_net --restart unless-stopped "$IMAGE" "$@"
echo "Started: ${CONTAINER_NAME}"
```

- [ ] **Step 8.2: Create `scripts/portal_nginx_reload.sh`**

```bash
#!/bin/bash
# Reloads sa_nginx_private after a new service conf is written
set -euo pipefail
echo "Testing nginx config..."
docker exec sa_nginx_private nginx -t
echo "Reloading nginx..."
docker exec sa_nginx_private nginx -s reload
echo "nginx reloaded"
```

- [ ] **Step 8.3: Make scripts executable**

```bash
chmod +x scripts/portal_docker_up.sh scripts/portal_nginx_reload.sh
```

- [ ] **Step 8.4: Test scripts manually**

Test nginx reload (safe, no-op if config is already valid):

```bash
bash scripts/portal_nginx_reload.sh
```

Expected: `nginx reloaded`

- [ ] **Step 8.5: Commit**

```bash
git add scripts/portal_docker_up.sh scripts/portal_nginx_reload.sh
git commit -m "feat: add portal_docker_up.sh and portal_nginx_reload.sh for n8n provisioning"
```

---

## Task 9: n8n Workflow — `portal-update-categories`

This is the simplest workflow — reads `services.json`, replaces the categories array, writes back. Build and test this first to validate the n8n → file access pattern before tackling more complex workflows.

- [ ] **Step 9.1: Verify n8n can read `portal/services.json`**

In n8n UI (`https://n8n.private.sovereignadvisory.ai`), create a temporary workflow:

1. **Manual Trigger** node
2. **Execute Command** node: `cat /data/portal/services.json`
3. Run it. Verify the file contents appear in the output.

Delete the temporary workflow.

- [ ] **Step 9.2: Create `portal-update-categories` workflow in n8n**

New workflow named `portal-update-categories`. Nodes:

1. **Webhook** node
   - HTTP Method: POST
   - Path: `portal-update-categories`
   - Response Mode: Last Node

2. **Code** node (JavaScript)
   - Name: `Update Categories`
   - Input: `$json.body.categories` (the incoming array)
   ```js
   const fs = require('fs');
   const path = '/data/portal/services.json';
   const data = JSON.parse(fs.readFileSync(path, 'utf8'));
   data.categories = $input.first().json.body.categories;
   fs.writeFileSync(path, JSON.stringify(data, null, 2));
   return [{ json: { success: true, categoriesCount: data.categories.length } }];
   ```

3. **Respond to Webhook** node
   - Response Body: `{{ $json }}`

- [ ] **Step 9.3: Activate and test**

Activate the workflow. Test with curl:

```bash
curl -s -X POST https://n8n.private.sovereignadvisory.ai/webhook/portal-update-categories \
  -H "Content-Type: application/json" \
  -d '{"categories":[{"key":"test","label":"Test","icon":"🧪","color":"rgba(0,0,0,0.1)","text":"#fff","border":"rgba(0,0,0,0.2)"}]}' | jq .
```

Expected: `{"success":true,"categoriesCount":1}`

Verify `portal/services.json` now has the test category. **Restore** the original categories:

```bash
# Re-POST the full categories array to restore
curl -s -X POST https://n8n.private.sovereignadvisory.ai/webhook/portal-update-categories \
  -H "Content-Type: application/json" \
  -d "$(python3 -c "import json; d=json.load(open('portal/services.json')); print(json.dumps({'categories':d['categories']}))")"
```

---

## Task 10: n8n Workflow — `portal-update` and `portal-delete`

- [ ] **Step 10.1: Create `portal-update` workflow**

New workflow named `portal-update`. Nodes:

1. **Webhook** — POST, path: `portal-update`
2. **Code** — `Update Service`
   ```js
   const fs = require('fs');
   const path = '/data/portal/services.json';
   const { id, fields } = $input.first().json.body;
   const data = JSON.parse(fs.readFileSync(path, 'utf8'));
   const idx = data.services.findIndex(s => s.id === id);
   if (idx === -1) throw new Error(`Service '${id}' not found`);
   data.services[idx] = { ...data.services[idx], ...fields };
   fs.writeFileSync(path, JSON.stringify(data, null, 2));
   return [{ json: { success: true, service: data.services[idx] } }];
   ```
3. **Respond to Webhook** — `{{ $json }}`

- [ ] **Step 10.2: Test `portal-update`**

```bash
curl -s -X POST https://n8n.private.sovereignadvisory.ai/webhook/portal-update \
  -H "Content-Type: application/json" \
  -d '{"id":"notion","fields":{"description":"Updated description"}}' | jq .
```

Expected: `{"success":true,"service":{...,"description":"Updated description",...}}`

Check `portal/services.json` to confirm the change. Revert:

```bash
curl -s -X POST https://n8n.private.sovereignadvisory.ai/webhook/portal-update \
  -H "Content-Type: application/json" \
  -d '{"id":"notion","fields":{"description":"Workspace & docs"}}' | jq .
```

- [ ] **Step 10.3: Create `portal-delete` workflow**

New workflow named `portal-delete`. Nodes:

1. **Webhook** — POST, path: `portal-delete`
2. **Code** — `Delete Service`
   ```js
   const fs = require('fs');
   const path = '/data/portal/services.json';
   const { id } = $input.first().json.body;
   const data = JSON.parse(fs.readFileSync(path, 'utf8'));
   const before = data.services.length;
   data.services = data.services.filter(s => s.id !== id);
   if (data.services.length === before) throw new Error(`Service '${id}' not found`);
   fs.writeFileSync(path, JSON.stringify(data, null, 2));
   return [{ json: { success: true, deleted: id } }];
   ```
3. **Respond to Webhook** — `{{ $json }}`

- [ ] **Step 10.4: Test `portal-delete`**

Add a test service first via direct file edit, then delete it:

```bash
# Add test entry directly
python3 -c "
import json
d = json.load(open('portal/services.json'))
d['services'].append({'id':'test-delete','name':'Delete Me','url':'https://example.com','icon':'🗑️','category':'infra','favorite':False,'ssoTier':3})
json.dump(d, open('portal/services.json','w'), indent=2)
"

# Delete via webhook
curl -s -X POST https://n8n.private.sovereignadvisory.ai/webhook/portal-delete \
  -H "Content-Type: application/json" \
  -d '{"id":"test-delete"}' | jq .
```

Expected: `{"success":true,"deleted":"test-delete"}`. Verify entry is gone from `services.json`.

---

## Task 11: n8n Workflow — `portal-provision` (External Services)

Build and test the external path first (no infra changes needed). Internal path in Task 12.

- [ ] **Step 11.1: Create `portal-provision` workflow (external path)**

New workflow named `portal-provision`. Nodes:

1. **Webhook** — POST, path: `portal-provision`, Response Mode: Last Node

2. **Code** — `Route by isInternal`
   ```js
   const body = $input.first().json.body;
   return [{ json: { ...body, _route: body.isInternal ? 'internal' : 'external' } }];
   ```

3. **IF** node — Condition: `{{ $json._route }}` equals `internal`
   - True branch → internal provisioning (Task 12)
   - False branch → external provisioning (continue here)

4. **Code** (false branch) — `Provision External`
   ```js
   const fs = require('fs');
   const path = '/data/portal/services.json';
   const { name, url, description, icon, category, credentialHint, apiProvider } = $input.first().json;
   const id = name.toLowerCase().replace(/[^a-z0-9]/g, '-');
   const data = JSON.parse(fs.readFileSync(path, 'utf8'));
   if (data.services.find(s => s.id === id)) throw new Error(`Service '${id}' already exists`);
   const card = { id, name, url, description: description || '', icon: icon || '🔗', category, favorite: false, ssoTier: 3 };
   if (credentialHint) card.credentialHint = credentialHint;
   if (apiProvider) card.apiProvider = apiProvider;
   data.services.push(card);
   fs.writeFileSync(path, JSON.stringify(data, null, 2));
   return [{ json: { success: true, card } }];
   ```

5. **Respond to Webhook** (false branch) — `{{ $json }}`

- [ ] **Step 11.2: Activate and test external provision**

```bash
curl -s -X POST https://n8n.private.sovereignadvisory.ai/webhook/portal-provision \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Test External",
    "url": "https://example.com",
    "description": "A test service",
    "icon": "🧪",
    "category": "infra",
    "isInternal": false
  }' | jq .
```

Expected: `{"success":true,"card":{"id":"test-external",...}}`

Reload `https://home.private.sovereignadvisory.ai` — new card should appear in Infrastructure.

- [ ] **Step 11.3: Clean up test card**

```bash
curl -s -X POST https://n8n.private.sovereignadvisory.ai/webhook/portal-delete \
  -H "Content-Type: application/json" \
  -d '{"id":"test-external"}' | jq .
```

- [ ] **Step 11.4: Test Add Service wizard in the portal UI**

Open the portal, click `+ Add Service`, fill in an external URL (`https://linear.app`), complete wizard Steps 1-4. Verify new card appears in the grid. Delete via the edit modal.

---

## Task 12: n8n Workflow — `portal-provision` (Internal Services)

This is the most complex task — full infrastructure provisioning. The internal path in the `portal-provision` workflow from Task 11 needs to be extended.

- [ ] **Step 12.1: Verify n8n has Docker access**

In n8n, create a temporary workflow:

1. **Manual Trigger**
2. **Execute Command**: `docker ps --format "table {{.Names}}" | head -5`
3. Run. Verify container names appear in output.

If Docker access fails (permission error), check that `/var/run/docker.sock` is mounted in n8n's container:

```bash
docker exec n8n ls -la /var/run/docker.sock
```

If not present, apply the volume patch from Task 5 and restart n8n: `docker compose restart n8n`.

- [ ] **Step 12.2: Extend `portal-provision` workflow — internal branch**

In the `portal-provision` workflow, add these nodes on the true (internal) branch of the IF node:

**Node A: Create Keycloak Client**
- Type: **HTTP Request**
- Method: POST
- URL: `https://kc.sovereignadvisory.ai/realms/agentic-sdlc/clients`
- Auth: Use a Keycloak credential (create an n8n HTTP Basic auth credential with the admin token endpoint, or use the service account)
- Body:
  ```json
  {
    "clientId": "{{ $json.name.toLowerCase().replace(/[^a-z0-9]/g, '-') }}",
    "enabled": true,
    "protocol": "openid-connect",
    "publicClient": false,
    "secret": "{{ $randomString(32) }}",
    "redirectUris": ["{{ $json.url }}/oauth2/callback"],
    "standardFlowEnabled": true,
    "attributes": { "pkce.code.challenge.method": "S256" }
  }
  ```

> **Keycloak admin token:** The simplest n8n approach is to first call `POST /realms/master/protocol/openid-connect/token` with admin credentials (stored as n8n HTTP Request credential), extract the token, then use it for the client creation call. Chain two HTTP Request nodes.

**Node B: Write nginx conf file**
- Type: **Code** (JavaScript)
  ```js
  const fs = require('fs');
  const { name, url } = $input.first().json;
  const subdomain = new URL(url).hostname;
  const conf = `
  server {
      listen 443 ssl;
      http2 on;
      server_name ${subdomain};
      ssl_certificate     /etc/letsencrypt/live/private.sovereignadvisory.ai/fullchain.pem;
      ssl_certificate_key /etc/letsencrypt/live/private.sovereignadvisory.ai/privkey.pem;
      location / {
          proxy_pass http://oauth2_proxy_${name.toLowerCase().replace(/[^a-z0-9]/g, '_')}:XXXX;
          proxy_set_header Host $host;
          proxy_set_header X-Forwarded-Proto https;
      }
  }`;
  fs.writeFileSync(`/data/portal/nginx-${name}.conf`, conf);
  return $input.all();
  ```

  > **Note:** The nginx conf needs to be written to the `nginx-private/conf.d/` path on the host. Since n8n's portal mount is `./portal:/data/portal`, writing to `/data/portal/nginx-<name>.conf` puts the file in `./portal/` on the host. The provisioning script then copies/moves it to `nginx-private/conf.d/`. Add this step to `portal_nginx_reload.sh` or handle in an Execute Command node.

**Node C: Start oauth2-proxy container**
- Type: **Execute Command**
  ```bash
  docker run -d \
    --name oauth2_proxy_{{ $('Route by isInternal').item.json.name.toLowerCase().replace(/[^a-z0-9]/g, '_') }} \
    --network vibe_net \
    --restart unless-stopped \
    quay.io/oauth2-proxy/oauth2-proxy:v7.6.0 \
    --provider=oidc \
    --oidc-issuer-url=https://kc.sovereignadvisory.ai/realms/agentic-sdlc \
    --client-id={{ $('Create Keycloak Client').item.json.clientId }} \
    --client-secret={{ $('Create Keycloak Client').item.json.secret }} \
    --redirect-url={{ $('Route by isInternal').item.json.url }}/oauth2/callback \
    --upstream=http://{{ $('Route by isInternal').item.json.name.toLowerCase() }}:80 \
    --http-address=0.0.0.0:XXXX \
    --cookie-secret=$(openssl rand -base64 16) \
    --cookie-secure=true \
    --email-domain=* \
    --skip-provider-button=true \
    --code-challenge-method=S256
  ```
  > Port XXXX: n8n must generate an available port. Add a **Code** node before this that reads `docker-compose.override.yml` to find the next available port starting from 4186.

**Node D: Reload nginx**
- Type: **Execute Command**
  ```bash
  bash /data/scripts/portal_nginx_reload.sh
  ```

**Node E: Register Twingate resource**
- Type: **Execute Command**
  ```bash
  TWINGATE_API_KEY=$TWINGATE_API_KEY \
  TWINGATE_NETWORK=$TWINGATE_NETWORK \
  TWINGATE_REMOTE_NETWORK="$TWINGATE_REMOTE_NETWORK" \
  python3 /data/scripts/twingate/twingate_add_resource.py \
    --name "{{ $('Route by isInternal').item.json.name }}" \
    --address "{{ new URL($('Route by isInternal').item.json.url).hostname }}"
  ```
  > TWINGATE env vars must be exposed to n8n. Add `TWINGATE_API_KEY`, `TWINGATE_NETWORK`, `TWINGATE_REMOTE_NETWORK` to the n8n environment block in `docker-compose.yml` (sourced from `.env`).

**Node F: Append to services.json**
- Type: **Code**
  ```js
  const fs = require('fs');
  const path = '/data/portal/services.json';
  const { name, url, description, icon, category } = $('Route by isInternal').item.json;
  const id = name.toLowerCase().replace(/[^a-z0-9]/g, '-');
  const data = JSON.parse(fs.readFileSync(path, 'utf8'));
  const card = { id, name, url, description: description || '', icon: icon || '🔗', category, favorite: false, ssoTier: 1 };
  data.services.push(card);
  fs.writeFileSync(path, JSON.stringify(data, null, 2));
  return [{ json: { success: true, card } }];
  ```

**Node G: Respond to Webhook** — `{{ $json }}`

- [ ] **Step 12.3: Add Twingate env vars to n8n in `docker-compose.yml`**

In n8n `environment:` block, add:

```yaml
      - TWINGATE_API_KEY=${TWINGATE_API_KEY}
      - TWINGATE_NETWORK=${TWINGATE_NETWORK}
      - TWINGATE_REMOTE_NETWORK=${TWINGATE_REMOTE_NETWORK}
```

Add these vars to `.env` (get values from Twingate dashboard → Settings → API).

- [ ] **Step 12.4: Test internal provision end-to-end**

```bash
curl -s -X POST https://n8n.private.sovereignadvisory.ai/webhook/portal-provision \
  -H "Content-Type: application/json" \
  -d '{
    "name": "test-internal",
    "url": "https://test-internal.private.sovereignadvisory.ai",
    "description": "Integration test service",
    "icon": "🧪",
    "category": "infra",
    "isInternal": true
  }' | jq .
```

Expected: `{"success":true,"card":{"id":"test-internal","ssoTier":1,...}}`

Verify:
- Keycloak has new `test-internal` client
- `oauth2_proxy_test_internal` container is running (`docker ps | grep test`)
- `portal/services.json` contains the new entry
- Portal card appears at `https://home.private.sovereignadvisory.ai`

Clean up: `docker stop oauth2_proxy_test_internal && docker rm oauth2_proxy_test_internal`, delete Keycloak client, remove from services.json via delete webhook.

- [ ] **Step 12.5: Commit compose changes**

```bash
git add docker-compose.yml .env scripts/
git commit -m "feat: add Twingate env vars to n8n; portal provisioning workflows operational"
```

---

## Task 13: Final Validation

- [ ] **Step 13.1: Run all success criteria**

| Criterion | Test |
|---|---|
| 1. Unauthenticated redirect | Open portal in incognito → should hit Keycloak login |
| 2. All 14 cards visible | Log in → count cards in grid |
| 3. Tier 1 SSO | Click n8n / LiteLLM → no second login prompt |
| 4. Tier 3 autofill | Click Cloudflare → Vaultwarden extension autofills |
| 5. Add Service wizard (external) | `+ Add Service` → `https://linear.app` → wizard completes → card appears |
| 6. Edit service | Click ✎ on Notion → change description → Save → card updates |
| 7. Add Category | Wizard Step 2 → `＋ New` → "Design" + 🎨 → category appears in sidebar and step 2 |
| 8. API badges | Claude shows `API ✓` or `API —` depending on LiteLLM key; clicking opens claude.ai |

- [ ] **Step 13.2: Final commit**

```bash
git add -A
git commit -m "feat: internal access portal complete — all success criteria validated"
```
