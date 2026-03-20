# Runbook: Keycloak Client Secret Rotation

**When to use:**
- After a fresh Keycloak import (fresh volume or manual `--import-realm`) — all client secrets are regenerated
- When manually rotating a secret in the Keycloak Admin UI
- When a secret is suspected to be compromised

---

## Background

Keycloak client secrets are **not included in realm exports**. Every fresh Keycloak import regenerates all client secrets. Services that hold stale secrets in `.env` will fail OIDC token exchange silently — they reach the login page, redirect to Keycloak, and Keycloak rejects the client authentication with no user-visible error.

---

## Client → Service Mapping

| Keycloak Client ID | `.env` Variable | Service(s) |
|---|---|---|
| `n8n` | `N8N_OIDC_CLIENT_SECRET` | `oauth2-proxy-n8n` |
| `litellm` | `LITELLM_OIDC_CLIENT_SECRET` | `oauth2-proxy-litellm` |
| `jupyter` | `JUPYTER_OIDC_CLIENT_SECRET` | `oauth2-proxy-jupyter` |
| `webui` | `WEBUI_OIDC_CLIENT_SECRET` | `oauth2-proxy-webui` |
| `portal` | `PORTAL_OIDC_CLIENT_SECRET` | `oauth2-proxy-portal` |
| `lead-review` | `LEAD_REVIEW_OIDC_CLIENT_SECRET` | `lead-review` |
| `vaultwarden` | `VAULTWARDEN_OIDC_CLIENT_SECRET` | `vaultwarden` |

---

## Procedure: After a Fresh Keycloak Import

### Step 1 — Get the new secret for each client

In the Keycloak Admin UI (`https://kc.sovereignadvisory.ai` for prod, `http://localhost:8080` for homelab):

1. Go to **Realm: agentic-sdlc** → **Clients**
2. Click the client (e.g., `n8n`)
3. Go to **Credentials** tab
4. Copy the **Client secret** value

Repeat for every client in the table above.

Or via Keycloak Admin API (requires admin token):
```bash
# Get admin token
TOKEN=$(curl -s -X POST "http://localhost:8080/realms/master/protocol/openid-connect/token" \
  -d "client_id=admin-cli&username=admin&password=${KEYCLOAK_ADMIN_PASSWORD}&grant_type=password" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

# Get secret for a specific client (replace CLIENT_ID)
curl -s -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8080/admin/realms/agentic-sdlc/clients?clientId=n8n" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(d[0]['secret'])"
```

### Step 2 — Update `.env`

Edit `.env` on the host and update each `*_OIDC_CLIENT_SECRET` variable with the new values.

### Step 3 — Restart affected services

**Homelab:**
```bash
docker compose up -d --no-deps \
  oauth2-proxy-n8n oauth2-proxy-litellm oauth2-proxy-jupyter \
  oauth2-proxy-webui oauth2-proxy-portal lead-review vaultwarden
```

**VPS (production):**
```bash
docker compose -f docker-compose.prod.yml up -d --no-deps \
  oauth2-proxy-n8n oauth2-proxy-litellm oauth2-proxy-jupyter \
  oauth2-proxy-webui oauth2-proxy-portal lead-review vaultwarden
```

### Step 4 — Verify SSO login works

Test each protected service:
- n8n: `https://n8n.private.sovereignadvisory.ai/` — should redirect to Keycloak, then back
- LiteLLM: `https://litellm.private.sovereignadvisory.ai/` — same
- JupyterLab: `https://jupyter.private.sovereignadvisory.ai/` — same
- WebUI: `https://webui.private.sovereignadvisory.ai/` — same
- Portal: `https://home.private.sovereignadvisory.ai/` — same

Or run the post-deploy test suite:
```bash
pip install -r tests/requirements.txt && playwright install chromium
pytest tests/test_post_deploy.py -v -k "keycloak"
```

---

## Procedure: Rotating a Single Secret

If only one service's secret needs rotation (manual rotation or suspected compromise):

1. In Keycloak Admin: **Clients** → select client → **Credentials** → **Regenerate**
2. Copy the new secret
3. Update the corresponding variable in `.env`
4. Restart only the affected service:
   ```bash
   docker compose up -d --no-deps oauth2-proxy-n8n  # example
   ```
5. Verify login works for that service

---

## Symptoms of a Stale Secret

- Login redirects to Keycloak then immediately back to a 500 or "invalid_client" error
- oauth2-proxy logs show: `error redeeming code: token exchange failed`
- Keycloak logs show: `Client not found` or `Invalid client credentials`

Check oauth2-proxy logs:
```bash
docker compose logs oauth2-proxy-n8n --tail=50
```
