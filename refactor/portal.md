# Refactor: portal/

**Date:** 2026-03-20
**Refactored by:** Claude Sonnet 4.6

---

## Directory Overview

| Property | Value |
|---|---|
| **Path** | `portal/` |
| **Purpose** | Internal access portal — single-page app served by portal nginx at `home.private.sovereignadvisory.ai` |
| **Files** | `index.html` (57KB — full SPA), `services.json` (service catalog) |
| **Auth** | Protected by `oauth2_proxy_portal` (Keycloak SSO) in nginx-private |

### Architecture

```
Browser → nginx-private → oauth2_proxy_portal → portal nginx → portal/index.html
                                                              → /api/litellm-health  → litellm:4000
                                                              → /api/portal-*        → n8n webhooks
```

`index.html` fetches `services.json` at runtime and renders the service catalog. API badges call `/api/litellm-health` (proxied by nginx) to show which AI providers are reachable.

---

## Gaps Found

| # | Gap | Severity | Description |
|---|---|---|---|
| 1 | None in `services.json` | — | Clean, well-structured service catalog |
| 2 | None in `index.html` | — | Self-contained SPA, no external runtime deps except Google Fonts and Mermaid (via CDN in diagrams). Logic, styling, and data loading all correct. |

---

## Changes Made

No changes made to `portal/`. Files are clean.

---

## Notes

- `services.json` is the live service catalog — categories and service entries are managed via the n8n portal provisioning workflow (`/api/portal-provision`), not by direct file edit.
- `index.html` contains hardcoded `home.private.sovereignadvisory.ai` in a few places for the portal's own URL — intentional, this is a fixed internal subdomain.
- Portal uses `localStorage` for favorites — no backend persistence required.
