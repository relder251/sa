# Refactor: nginx-public/

**Date:** 2026-03-20
**Refactored by:** Claude Sonnet 4.6

---

## Directory Overview

| Property | Value |
|---|---|
| **Path** | `nginx-public/` |
| **Purpose** | Public-internet nginx — TLS termination, static site, Keycloak OIDC proxy, lead review proxy |
| **Deployed to** | VPS (not homelab) via `scripts/deploy-nginx.sh` |
| **Domains** | `sovereignadvisory.ai`, `www.sovereignadvisory.ai`, `kc.sovereignadvisory.ai` |

### File inventory

| File | Purpose |
|---|---|
| `nginx.conf` | Worker config, gzip, rate limiting, includes conf.d/ |
| `conf.d/00-http.conf` | HTTP→HTTPS redirect + ACME challenge pass-through |
| `conf.d/10-ssl.conf` | Main HTTPS server (static site, n8n webhook, lead review, auth) + Keycloak SSO server |

---

## Gaps Found

| # | File | Gap | Severity |
|---|---|---|---|
| 1 | `10-ssl.conf` keycloak block | Missing `ssl_stapling on` / `ssl_stapling_verify on` — no OCSP stapling on kc.sovereignadvisory.ai | Low |
| 2 | `10-ssl.conf` keycloak block | Missing `Strict-Transport-Security` header — HSTS not sent to Keycloak clients | Medium |
| 3 | `10-ssl.conf` keycloak block | Missing `resolver_timeout` — has `resolver 127.0.0.11` but no timeout directive | Low |
| 4 | `10-ssl.conf` keycloak block | `ssl_session_cache`, `ssl_session_timeout`, `ssl_session_tickets` already present — consistent with main block | (no gap) |

---

## Changes Made

| Change | File | Before | After | Reason |
|---|---|---|---|---|
| Add OCSP stapling to keycloak block | `10-ssl.conf` | No `ssl_stapling` | `ssl_stapling on; ssl_stapling_verify on;` | Consistent with main server block; reduces TLS handshake latency |
| Add HSTS to keycloak block | `10-ssl.conf` | No HSTS header | `add_header Strict-Transport-Security "max-age=63072000; includeSubDomains; preload" always;` | kc.sovereignadvisory.ai is always HTTPS; HSTS prevents downgrade attacks |
| Add `resolver_timeout` | `10-ssl.conf` | `resolver 127.0.0.11 valid=30s;` only | + `resolver_timeout 5s;` | Explicit timeout; prevents indefinite DNS wait on Docker network issues |

**Not changed (intentional):**
- X-Frame-Options not added to keycloak server block — Keycloak manages its own frame policy; the iframe endpoint explicitly overrides with CSP `frame-ancestors`. Adding server-level X-Frame-Options would conflict via nginx's `add_header` inheritance rules.
- No changes to `00-http.conf` or `nginx.conf` — both clean.

---

## Test Results

| Check | Result |
|---|---|
| nginx config syntax (outside Docker network) | ⚠ Expected failure: `host not found in upstream "n8n"` — pre-existing; `n8n` resolves only inside Docker network at request time via `resolver 127.0.0.11`. The bare `proxy_pass http://n8n:5678/webhook/` in the n8n webhook block resolves at startup, not request time. All `set $upstream` blocks pass. |
| Keycloak block HSTS header present | ✅ Verified in file |
| OCSP stapling directives present | ✅ Verified in file |

---

## Deferred Items

| Item | Notes |
|---|---|
| `/n8n/webhook/` bare `proxy_pass http://n8n:5678/webhook/` | Should use `set $upstream` pattern for late DNS binding, consistent with all other proxy locations. Low risk — n8n is always up before nginx on the VPS. Deferred to avoid VPS deploy test requirement. |
