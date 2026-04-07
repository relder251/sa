# Refactor: certbot/

**Date:** 2026-03-20
**Refactored by:** Claude Sonnet 4.6

---

## Files Reviewed

| File | Outcome |
|---|---|
| `certbot/cloudflare.ini` | Clean — template file; real token written at runtime by `entrypoint.sh` |
| `certbot/entrypoint.sh` | Clean — no changes |
| `certbot/nginx-reload.py` | Clean — no changes |

---

## Gaps Found

None.

---

## Notes

- `cloudflare.ini` is a **template** committed intentionally — `entrypoint.sh` overwrites `dns_cloudflare_api_token = PLACEHOLDER` with the real `CF_API_TOKEN` value at container startup via `printf`. File has `chmod 600` applied.
- `nginx-reload.py` is a certbot deploy-hook: sends `SIGHUP` to `sa_nginx_private` via the Docker socket (`/var/run/docker.sock`) to reload nginx after cert renewal. Uses stdlib only (no external deps).
- `entrypoint.sh` runs certbot in a renew loop (every 12h), passing `--deploy-hook python3 /nginx-reload.py`. Failures are swallowed (`|| true`) to prevent the container from crashing on a transient network error.
- The `CF_API_TOKEN` warning that appeared in `docker compose config` on local dev was addressed in `docker-compose.yml` by changing `${CF_API_TOKEN}` to `${CF_API_TOKEN:-}` (empty default suppresses the warning without affecting VPS behaviour).

---

## Changes Made

None in this directory.

---

## Test Results

| Check | Result |
|---|---|
| `cloudflare.ini` contains PLACEHOLDER (not a real token) | ✅ |
| `nginx-reload.py` uses stdlib only | ✅ |
| `CF_API_TOKEN` warning addressed in `docker-compose.yml` | ✅ (see root-files.md) |

---

## Deferred Items

None.
