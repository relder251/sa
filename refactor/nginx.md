# Refactor: nginx/

**Date:** 2026-03-20
**Refactored by:** Claude Sonnet 4.6

---

## Directory Overview

| Property | Value |
|---|---|
| **Path** | `nginx/` |
| **Purpose** | Portal nginx — serves `portal/index.html` + proxies portal API calls to LiteLLM and n8n |
| **Container** | Portal nginx (homelab, internal-only) |
| **Domain** | `home.private.sovereignadvisory.ai` (HTTP only, behind Twingate + nginx-private TLS) |

### File inventory

| File | Purpose |
|---|---|
| `conf.d/portal.conf` | Single server block: static portal + LiteLLM health proxy + n8n webhook routes |

---

## Gaps Found

| # | Gap | Severity | Description |
|---|---|---|---|
| 1 | `proxy_set_header Authorization "Bearer sk-vibe-coding-key-123"` | **Info** | LITELLM_API_KEY hardcoded in config. nginx doesn't natively support env var substitution in headers. Internal-only server (no public TLS), so blast radius is low. |
| 2 | n8n webhook paths contain hardcoded workflow IDs | **Info** | `FrPgMVhkBNi9nE85`, `wgvZCgnHnlIkSEN5`, etc. — n8n v1.x URLs include the workflow ID by design. These need updating if workflows are recreated in n8n. |

---

## Changes Made

| Change | File | Before | After | Reason |
|---|---|---|---|---|
| Document hardcoded API key with rotation instructions | `conf.d/portal.conf` | No comment | Added comment: nginx limitation, internal-only risk, how to rotate | Surfaces the constraint; prevents future confusion about why it's hardcoded |

---

## Deferred Items

| Item | Notes |
|---|---|
| Replace hardcoded API key with envsubst template | Convert to `portal.conf.template` + `${LITELLM_API_KEY}` env var. Tracked in `refactor/deferred.md`. Requires coordinated `docker-compose.yml` change for the portal nginx service. |
| Hardcoded n8n workflow IDs | By design — n8n webhook URLs include the workflow ID. Document a runbook for updating these when workflows are recreated. |
