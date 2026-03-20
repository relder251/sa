# Runbook: Keycloak Realm Re-Export

**When to use:** After adding or modifying Keycloak clients, roles, identity providers, or realm settings via the Admin UI. If this step is skipped, fresh deploys will be missing those changes.

---

## When to Re-Export

Re-export after ANY of the following:
- Adding a new OAuth2/OIDC client (e.g., a new service)
- Modifying client scopes, redirect URIs, or allowed origins on an existing client
- Adding or modifying realm roles
- Changing realm-level settings (token lifespans, brute-force protection, etc.)
- Adding or configuring identity providers (SAML, social login)

**Do NOT need to re-export for:**
- Client secret rotation (secrets are stripped from exports — see [keycloak-secret-rotation.md](keycloak-secret-rotation.md))
- User creation or management (users are not included in realm exports)
- Session-level changes

---

## How to Re-Export

### Step 1 — Export via the script

The export script strips client secrets before writing the file (safe to commit):

```bash
bash scripts/keycloak_export_realm.sh
```

This writes to `keycloak/realm-export.json`.

**Prerequisites:** The `keycloak` container must be running and healthy:
```bash
docker compose ps keycloak
```

### Step 2 — Review the diff

```bash
git diff keycloak/realm-export.json
```

Verify the diff contains only the expected changes. Watch for:
- `"secret": ""` entries (expected — secrets are stripped)
- Any unexpected client additions (may indicate a stale UI session added something)

### Step 3 — Commit and push

```bash
git add keycloak/realm-export.json
git commit -m "chore(keycloak): re-export realm after <describe change>"
git push
```

### Step 4 — Deploy to any environment that needs the update

**Homelab:**
```bash
docker compose restart keycloak
# Verify:
docker compose ps keycloak
```

**VPS (production):** The realm import only runs on a fresh volume. For an existing deployment, apply changes directly in the Admin UI on the VPS — or wipe the Keycloak data volume and re-import (destructive: loses all sessions and user data).

---

## Freshdeployment vs. Running Deployment

| Scenario | Behaviour |
|---|---|
| Fresh Keycloak volume | `--import-realm` flag reads `keycloak/realm-export.json` automatically on first start |
| Existing Keycloak volume | Import is skipped — apply changes via Admin UI, then re-export |

---

## Gotchas

- **Client secrets are not exported.** After a fresh import, all client secrets are regenerated. See [keycloak-secret-rotation.md](keycloak-secret-rotation.md) for how to update downstream services.
- **Users are not exported.** Recreate users manually or via `scripts/keycloak_bootstrap.py` after a fresh import.
- **The export includes `"bruteForceProtected": true`** — this was enabled during the 2026-03-20 refactor. Do not revert it.

---

## Script Reference

`scripts/keycloak_export_realm.sh` does:
1. `docker exec keycloak /opt/keycloak/bin/kc.sh export --realm agentic-sdlc --file /tmp/realm-export.json`
2. Copies the file out: `docker cp keycloak:/tmp/realm-export.json keycloak/realm-export.json`
3. Strips `"secret"` fields (via `jq`) so credentials are not committed
