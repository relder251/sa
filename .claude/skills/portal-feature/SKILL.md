---
name: portal-feature
description: Use when the user invokes /portal-feature, asks to add a portal capability, add a service card, add a UI feature, add a backend API route, or extend the SA Portal (home.private.sovereignadvisory.ai). This skill guides the user through scoping and implementing new portal features against the live codebase.
---

# Portal Feature Development

A guided workflow for adding new capabilities to the SA Portal. Always reads the actual source files fresh before generating an implementation plan, since the portal evolves continuously.

## Step 1 — Load Live Context

Before doing anything else, read the three canonical source files:

```
portal/index.html          — full HTML/CSS/JS single-page app
portal/services.json       — services registry and category definitions
nginx/conf.d/portal.conf.template  — nginx API routing
```

Use the Read tool on each file. Extract:

1. **CSS design tokens** from `:root` block in `index.html`
2. **JS globals** (top-level `let`/`const`/`function` declarations in the `<script>` block)
3. **All `fetch('/api/...')` calls** — these are the existing backend routes
4. **All function names** — to avoid naming conflicts and to reuse existing helpers
5. **Category keys** from `services.json` — valid values for the `category` field
6. **ssoTier valid values** from existing service entries

Then present the extracted context summary to the user before proceeding.

## Step 2 — Architecture Reference (read before proposing anything)

### Design System

The portal uses a **copper-on-dark** aesthetic. Key CSS custom properties:

| Token | Value | Usage |
|-------|-------|-------|
| `--bg` | `#0b0f1c` | Page background |
| `--bg-card` | `#0e1324` | Card backgrounds |
| `--bg-hover` | `#121830` | Hover state background |
| `--bg-sidebar` | `#090d19` | Topbar and sidebar |
| `--copper` | `#d4924a` | Primary accent (active states) |
| `--copper-light` | `#f0a535` | Brighter copper (hover accents) |
| `--copper-pale` | `rgba(240,165,53,0.10)` | Subtle copper fill |
| `--copper-border` | `rgba(240,165,53,0.28)` | Copper borders |
| `--text` | `#f0ece4` | Primary text |
| `--text-2` | `#ddd5c8` | Secondary text |
| `--text-3` | `#a09aaf` | Muted/label text |
| `--border` | `rgba(240,165,53,0.18)` | Main borders |
| `--border-subtle` | `rgba(255,255,255,0.06)` | Subtle dividers |
| `--font-d` | `'Cormorant Garamond', Georgia, serif` | Display / heading font |
| `--font-b` | `'Raleway', sans-serif` | Body / UI font |

All new UI must use these CSS variables — never hardcode hex values.

### JS Architecture

The portal is a **single-page vanilla JS app** (no build step, no framework). Key architectural patterns:

**State globals:**
- `currentFilter` — active sidebar filter key (`'all'`, `'favorites'`, `'recent'`, or a category key)
- `currentServices` — array of all service objects from the last successful `loadPortal()` call
- `recentOrder` — array of `{id, ts}` objects for recently-used tracking (persisted to localStorage)
- `editingCard` — the service object currently open in the edit modal (`null` when closed)
- `wizardStep` — current step number in the add-service wizard (1–4)
- `selectedCat` — selected category key in the wizard
- `sshGateway` — SSH gateway hostname from `services.json.sshGateway`

**Key functions to know before adding new features:**
- `loadPortal()` — async, fetches `/api/portal-services`, populates `currentServices`, calls `renderCards()` and `buildSidebarCategories()`
- `renderCards(services)` — renders service card grid; call after any mutation to `currentServices`
- `setFilter(cat, btn)` — changes active sidebar filter and re-renders
- `applyFilter(cat)` — applies `.hidden` CSS class to cards based on filter; call after `setFilter`
- `updateCounts()` — updates badge counts in sidebar; call after any service add/remove
- `refreshSidebarCategories()` — rebuilds sidebar category list from `currentServices`
- `openEdit(e, card)` — opens the edit modal for a service card
- `loadUser()` — fetches `/oauth2/userinfo` to display authenticated user in topbar
- `loadApiStatus()` — polls `/api/litellm-health` to update the status indicator dot
- `saveEdit()` — async, PATCHes `/api/portal-update` with edited service data
- `deleteService()` — async, DELETEs via `/api/portal-delete`
- `startDeploy()` — async, POSTs to `/api/portal-provision` for full service provisioning workflow

**Pattern for adding a new API call:**
```js
async function myNewApiCall(payload) {
  const res = await fetch('/api/my-new-route', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}
```

**Pattern for adding a new sidebar button:**
```html
<button class="filter-btn" id="btn-myfeature" onclick="setFilter('myfeature', this)">
  <span class="filter-icon">emoji</span>
  <span class="filter-label">My Feature</span>
  <span class="filter-count" id="count-myfeature">0</span>
</button>
```

**Pattern for adding a new card action button** (small button in card top-right area):
```html
<button class="card-ssh" onclick="myAction(event, '${svc.id}')" title="My Action">ACT</button>
```

### Service Schema (services.json)

Each service object has these fields:

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `id` | string | yes | Unique slug, kebab-case |
| `name` | string | yes | Display name |
| `url` | string | yes | Full URL the card opens |
| `description` | string | yes | Short subtitle shown in card |
| `icon` | string | yes | Emoji or short icon name |
| `category` | string | yes | Must match a key in `categories` array |
| `favorite` | boolean | yes | Default: `false` |
| `ssoTier` | number | yes | 1=SSO protected, 2=SSO+admin role, 3=external/no-SSO |
| `sshTarget` | string | no | Target name for SSH terminal proxy |
| `credentialHint` | string | no | Vault URL for credentials |
| `apiProvider` | string | no | For cloud AI providers: `anthropic`, `openai`, `gemini`, `xai` |

**Valid `category` keys** (read from live file each time): `automation`, `ai`, `providers`, `security`, `productivity`, `infra`, `testing`

**ssoTier meanings:**
- `1` — Protected by oauth2-proxy SSO (internal services on `.private.sovereignadvisory.ai`)
- `2` — SSO but requires admin role (e.g., Keycloak admin)
- `3` — External service, no SSO protection (cloud providers, external tools)

### nginx API Layer (portal.conf.template)

All `/api/*` routes are proxied by nginx to n8n webhooks or other internal services:

| Route | Target | Method | Timeout |
|-------|--------|--------|---------|
| `/api/portal-services` | n8n webhook (GET portal data) | GET | 10s |
| `/api/litellm-health` | LiteLLM `/health?model=cloud/smart` | GET | 20s |
| `/api/portal-provision` | n8n webhook (full provisioning) | POST | 120s |
| `/api/portal-update` | n8n webhook (edit service) | POST | 30s |
| `/api/portal-delete` | n8n webhook (delete service) | POST | 30s |
| `/api/portal-update-categories` | n8n webhook (edit categories) | POST | 30s |
| `/api/portal-track-recent` | n8n webhook (track click) | POST | 10s |
| `/oauth2/*` | oauth2_proxy_portal:4185 | any | default |
| `/terminal/*` | shell_gateway:7681 (WebSocket) | any | 3600s |

**To add a new API route**, add a `location /api/my-new-route` block in `portal.conf.template` following the existing pattern. The nginx container reads this template at startup via `envsubst` — use `${VAR_NAME}` syntax for environment variable substitution, not `$var`.

### Three-Layer Feature Model

Classify every feature request as one of:

| Layer | Type Key | What changes | Examples |
|-------|----------|--------------|---------|
| Data only | `service-card` | `portal/services.json` only | Add/edit/remove a service entry |
| UI only | `ui-feature` | `portal/index.html` only | New button, keyboard shortcut, filter, modal, visual indicator |
| Full-stack | `backend-capability` | `index.html` + `portal.conf.template` + new n8n workflow | New API endpoint with UI that calls it |

## Step 3 — Clarify the Feature Request

After loading live context, if the request is ambiguous, present this template to the user:

```
Type: service-card | ui-feature | backend-capability
What it does: [one sentence]
Trigger: [how the user activates it — button click, keyboard shortcut, page load, etc.]
Data needed: [what data the feature reads or writes]
Success looks like: [observable outcome — what the user sees when it works]
```

If the user's original request already answers all five fields unambiguously, skip asking and instead confirm the interpretation before proceeding.

## Step 4 — Generate Implementation Plan

Once the request is scoped, produce a complete implementation plan:

1. **Classify the layer** — state the type key explicitly
2. **List every file to change** — only files that must change, nothing else
3. **For each file**, specify:
   - Exact location (function name, line range, HTML element, or CSS selector)
   - The specific change with actual code (not pseudocode)
4. **Blast radius**: what else could break, and why it won't (or what to test)
5. **Verification steps**: exact steps to confirm the feature works

### Implementation checklist:
- [ ] Identify feature layer (service-card / ui-feature / backend-capability)
- [ ] Read relevant file sections for current context
- [ ] Draft implementation scoped to minimum files
- [ ] List all files to change (1 for service-card, 1 for ui-feature, 3+ for backend-capability)
- [ ] Estimate blast radius
- [ ] Write verification steps

## Example: Keyboard Shortcut (ui-feature)

For the request "add keyboard shortcut `/` to focus the search/filter bar":

**Layer**: `ui-feature` — `portal/index.html` only

**What to locate in index.html before writing code**:
- Any existing `keydown` event listener (search for `keydown`)
- The filter/search input element's `id` (search for `<input` in the HTML)
- Whether filtering is done via an input or via sidebar buttons only

**Implementation**:
- In the `DOMContentLoaded` handler (near the bottom of the script), add:
  ```js
  document.addEventListener('keydown', e => {
    if (e.key === '/' && !['INPUT','TEXTAREA','SELECT'].includes(document.activeElement.tagName)) {
      e.preventDefault();
      const fi = document.getElementById('filter-input'); // confirm actual id from read
      if (fi) fi.focus();
    }
  });
  ```
- Files changed: `portal/index.html` only (1 file)
- Blast radius: minimal — event listener is additive and guarded by activeElement check
- Verify: open portal, press `/`, confirm input receives focus; press `/` while typing in an input, confirm no interference

## Important Notes

- The portal has **no build step** — changes to `index.html` take effect on next browser reload after the file is served
- nginx serves static files from the `portal/` directory (mapped to `/usr/share/nginx/html` in the container)
- `services.json` is served via the `/api/portal-services` n8n webhook, not directly by nginx as a static file. The file on disk is the source of truth that n8n reads.
- To apply nginx config changes after editing `portal.conf.template`: `docker compose restart portal` (nginx service)
- The `portal.conf.template` is processed by `envsubst` at container start — `${LITELLM_API_KEY}` and similar patterns are substituted from environment variables
