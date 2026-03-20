# Refactor: nginx-private/

**Date:** 2026-03-20
**Refactored by:** Claude Sonnet 4.6

---

## Directory Overview

| Property | Value |
|---|---|
| **Path** | `nginx-private/` |
| **Purpose** | Private nginx — Twingate-gated reverse proxy for all internal services |
| **Container** | `sa_nginx_private` (running on homelab) |
| **Domains** | `*.private.sovereignadvisory.ai`, `vault.private.sovereignadvisory.ai`, `kc.private.sovereignadvisory.ai` |

### Services proxied

| Subdomain | Upstream |
|---|---|
| `n8n.private.*` | `oauth2_proxy_n8n:5679` |
| `webui.private.*` | `oauth2_proxy_webui:3001` |
| `litellm.private.*` | `oauth2_proxy_litellm:4001` |
| `jupyter.private.*` | `oauth2_proxy_jupyter:8889` |
| `ollama.private.*` | `ollama:11434` (direct, no oauth2 proxy) |
| `vault.private.*` | `vaultwarden:80` + WebSocket |
| `kc.private.*` | `keycloak:8080` (admin console) |
| `home.private.*` | `oauth2_proxy_portal:4185` |
| `sovereignadvisory.ai` | `sa_lead_review:5003` + n8n webhooks (Twingate path) |

---

## Gaps Found

| # | Gap | Severity | Description |
|---|---|---|---|
| 1 | `ollama` block: bare `proxy_pass http://ollama:11434` | **Medium** | No `resolver` + `set $upstream` pattern. nginx resolves `ollama` at config load time, not request time. If Ollama is restarting, nginx fails to reload. All other blocks use late binding. |
| 2 | `kc.private.sovereignadvisory.ai` block: bare `proxy_pass http://keycloak:8080` | **Medium** | Same issue — if Keycloak restarts, nginx reload/restart fails until Keycloak is up. |
| 3 | Both blocks missing `resolver` directive entirely | **Medium** | Required for `set $upstream` late binding; also needed if Docker assigns a new IP to a restarted container. |

---

## Changes Made

| Change | File | Before | After | Reason |
|---|---|---|---|---|
| Add resolver + late-binding to ollama block | `conf.d/private.conf` | `proxy_pass http://ollama:11434` directly | `resolver 127.0.0.11 valid=30s; resolver_timeout 5s;` + `set $up_ollama http://ollama:11434; proxy_pass $up_ollama;` | Consistent with all other proxy blocks; nginx no longer fails to reload if Ollama is temporarily down |
| Add resolver + late-binding to kc.private block | `conf.d/private.conf` | `proxy_pass http://keycloak:8080` directly | Same pattern with `$up_kc` | Keycloak restarts during realm import or config changes; nginx must survive this |

---

## Test Results

| Check | Result |
|---|---|
| nginx -t inside `sa_nginx_private` container | ✅ `nginx: configuration file /etc/nginx/nginx.conf test is successful` |
| All existing proxy blocks preserved | ✅ No functional changes to any other server block |

---

## Deferred Items

| Item | Notes |
|---|---|
| Missing `ssl_stapling` on private servers | Private certs (Twingate-internal) — OCSP stapling has limited value for private TLS; deferred |
| `sovereignadvisory.ai` block `/n8n/webhook/` bare proxy_pass | Pre-existing inconsistency with the block's own `set $upstream` pattern. Low risk — same issue as nginx-public. |
