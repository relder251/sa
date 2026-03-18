# Keycloak SSO + Vaultwarden Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Unified SSO via Keycloak for all internal tools (n8n, pipeline WebUI, LiteLLM, JupyterLab, lead-review) plus a self-hosted Vaultwarden password vault for SaaS credentials, all authenticated through a single Keycloak identity.

**Architecture:** Keycloak (`kc.sovereignadvisory.ai`) is the single IdP. Tools that support OIDC natively (n8n) use it directly. Tools that don't (WebUI, LiteLLM, JupyterLab) go through oauth2-proxy sidecars (already defined in docker-compose.yml). Lead-review has OIDC already coded — just needs enabling. Vaultwarden runs at `vault.private.sovereignadvisory.ai` with `SSO_ONLY=false` (Keycloak is the primary login; master-password fallback kept for admin recovery). Password rotation policy (30-day, configurable) is set in the Keycloak Realm — no code changes required to adjust it.

**Tech Stack:** Keycloak 24.0.5 (already running), oauth2-proxy v7.6.0 (already in docker-compose.yml), Vaultwarden 1.32+ (new), nginx-private (existing), Playwright for smoke tests.

**Stack topology note:** The prod VPS runs two compose files in parallel:
- `docker-compose.yml` — full application stack (n8n, Keycloak, LiteLLM, WebUI, etc.)
- `docker-compose.prod.yml` — nginx/certbot only (prod-specific port bindings and paths)

---

## Role Design (multi-user ready)

| Role | Access |
|------|--------|
| `admin` | All tools, Keycloak admin, Vaultwarden admin |
| `user` | WebUI, lead-review (read), Vaultwarden (own vault) |

Groups: `admins` → role `admin` + `user`. `users` → role `user` only.

---

## File Map

| Action | File | Responsibility |
|--------|------|----------------|
| Create | `keycloak/realm-export.json` | GitOps realm snapshot (exported after bootstrap) |
| Create | `scripts/keycloak_bootstrap.py` | One-time realm/client/user setup via Admin API |
| Create | `scripts/keycloak_export_realm.sh` | Exports realm to keycloak/ for GitOps |
| Modify | `docker-compose.yml` | Keycloak production mode; oauth2-proxy profile removal; enable SSO env vars; add oauth2-proxy for WebUI; add Vaultwarden |
| Modify | `docker-compose.prod.yml` | Mirror oauth2-proxy/Vaultwarden additions |
| Modify | `nginx-private/conf.d/private.conf` | Route webui/litellm/jupyter through oauth2-proxy; add vault block |
| Modify | `tests/conftest.py` | Add `litellm_url`, `jupyter_url`, `vault_url` fixtures |
| Modify | `tests/test_post_deploy.py` | Add SSO redirect smoke tests |
| Modify | `.env.example` | Add Vaultwarden + new OIDC secret vars |

---

## Task 0: Switch Keycloak to Production Mode

Keycloak is currently running `start-dev` on prod. This suppresses strict validation and
would cause `400 invalid_redirect_uri` in SSO flows because Keycloak won't trust the proxy's
forwarded headers. Must fix before any SSO wiring.

**Files:**
- Modify: `docker-compose.yml` (keycloak service command + env)

- [ ] **Step 1: Update Keycloak command and add proxy headers env**

In `docker-compose.yml`, change the keycloak service:
```yaml
keycloak:
  image: quay.io/keycloak/keycloak:24.0.5
  container_name: keycloak
  command: start                          # was: start-dev
  environment:
    - KEYCLOAK_ADMIN=admin
    - KEYCLOAK_ADMIN_PASSWORD=${KEYCLOAK_ADMIN_PASSWORD}
    - KC_DB=postgres
    - KC_DB_URL=jdbc:postgresql://postgres:5432/keycloak
    - KC_DB_USERNAME=${LITELLM_USER:-litellm}
    - KC_DB_PASSWORD=${LITELLM_PASSWORD:-litellm_password}
    - KC_HOSTNAME=kc.sovereignadvisory.ai
    - KC_HOSTNAME_STRICT=true
    - KC_PROXY=edge                       # trust X-Forwarded-Proto from nginx
    - KC_HTTP_ENABLED=true               # allow HTTP inside the cluster (nginx handles TLS)
    - KC_HEALTH_ENABLED=true
```

- [ ] **Step 2: Redeploy Keycloak on prod and verify**

```bash
# On prod
cd /opt/agentic-sdlc && git pull
docker compose up -d keycloak
sleep 20
docker exec keycloak /opt/keycloak/bin/kc.sh show-config 2>/dev/null | grep -i "proxy\|hostname" || \
  docker logs keycloak --tail=10
```

Expected: Keycloak starts without errors. Admin UI reachable at `https://kc.sovereignadvisory.ai/admin`.

- [ ] **Step 3: Confirm admin UI login still works**

Browse to `https://kc.sovereignadvisory.ai/admin` and log in. If login fails, check
`docker logs keycloak --tail=30` for configuration errors.

- [ ] **Step 4: Commit**

```bash
git add docker-compose.yml
git commit -m "fix: switch Keycloak to production mode with KC_PROXY=edge"
git push origin master
```

---

## Task 1: Bootstrap Keycloak Realm via Admin API

**Goal:** Create the `agentic-sdlc` realm, password policy, roles, groups, clients, and admin user programmatically. Idempotent — safe to re-run.

**Files:**
- Create: `scripts/keycloak_bootstrap.py`

- [ ] **Step 1: Write the bootstrap script**

```python
#!/usr/bin/env python3
"""
keycloak_bootstrap.py — one-time Keycloak realm setup.
Usage: python scripts/keycloak_bootstrap.py
Requires: KEYCLOAK_ADMIN_PASSWORD env var (loaded from .env if present).
Idempotent: checks for existing resources before creating.
"""
import os, sys, json, requests
from pathlib import Path

# Load .env if present
env_path = Path(__file__).parent.parent / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

KC_BASE      = os.environ.get("KEYCLOAK_INTERNAL_URL", "http://localhost:8080")
ADMIN_USER   = "admin"
ADMIN_PASS   = os.environ["KEYCLOAK_ADMIN_PASSWORD"]
REALM        = "agentic-sdlc"
SMTP_HOST    = os.environ.get("NEO_SMTP_HOST", "")
SMTP_PORT    = os.environ.get("NEO_SMTP_PORT", "465")
SMTP_USER    = os.environ.get("NEO_SMTP_USER", "")
SMTP_PASS    = os.environ.get("NEO_SMTP_PASS", "")
NOTIFY_EMAIL = os.environ.get("NOTIFY_EMAIL", "")


def get_admin_token():
    r = requests.post(
        f"{KC_BASE}/realms/master/protocol/openid-connect/token",
        data={"grant_type": "password", "client_id": "admin-cli",
              "username": ADMIN_USER, "password": ADMIN_PASS},
    )
    r.raise_for_status()
    return r.json()["access_token"]


def headers(token):
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def create_realm(token):
    r = requests.get(f"{KC_BASE}/admin/realms/{REALM}", headers=headers(token))
    if r.status_code == 200:
        print(f"  realm '{REALM}' already exists — skipping create")
        return
    realm_config = {
        "realm": REALM,
        "enabled": True,
        "displayName": "Sovereign Advisory",
        "registrationAllowed": False,
        "loginWithEmailAllowed": True,
        "duplicateEmailsAllowed": False,
        "resetPasswordAllowed": True,
        "editUsernameAllowed": False,
        # 30-day rotation, 12-char minimum, complexity, no reuse of last 5
        "passwordPolicy": (
            "forceExpiredPasswordChange(30) and length(12) and "
            "upperCase(1) and lowerCase(1) and digits(1) and "
            "specialChars(1) and passwordHistory(5)"
        ),
        "bruteForceProtected": True,
        "failureFactor": 5,
        "waitIncrementSeconds": 60,
        "maxFailureWaitSeconds": 900,
        "smtpServer": {
            "host": SMTP_HOST, "port": SMTP_PORT,
            "fromDisplayName": "Sovereign Advisory",
            "from": NOTIFY_EMAIL,
            "ssl": "true", "auth": "true",
            "user": SMTP_USER, "password": SMTP_PASS,
        } if SMTP_HOST else {},
        "accessTokenLifespan": 300,
        "ssoSessionIdleTimeout": 86400,
        "ssoSessionMaxLifespan": 604800,
    }
    r = requests.post(f"{KC_BASE}/admin/realms",
                      headers=headers(token), data=json.dumps(realm_config))
    r.raise_for_status()
    print(f"  realm '{REALM}' created")


def create_role(token, role_name, description=""):
    r = requests.get(f"{KC_BASE}/admin/realms/{REALM}/roles/{role_name}",
                     headers=headers(token))
    if r.status_code == 200:
        print(f"  role '{role_name}' already exists")
        return r.json()["id"]
    requests.post(f"{KC_BASE}/admin/realms/{REALM}/roles",
                  headers=headers(token),
                  data=json.dumps({"name": role_name, "description": description})).raise_for_status()
    r2 = requests.get(f"{KC_BASE}/admin/realms/{REALM}/roles/{role_name}", headers=headers(token))
    print(f"  role '{role_name}' created")
    return r2.json()["id"]


def create_group(token, group_name, role_ids):
    r = requests.get(f"{KC_BASE}/admin/realms/{REALM}/groups", headers=headers(token))
    existing = {g["name"]: g["id"] for g in r.json()}
    if group_name in existing:
        print(f"  group '{group_name}' already exists")
        return existing[group_name]
    requests.post(f"{KC_BASE}/admin/realms/{REALM}/groups",
                  headers=headers(token),
                  data=json.dumps({"name": group_name})).raise_for_status()
    r2 = requests.get(f"{KC_BASE}/admin/realms/{REALM}/groups", headers=headers(token))
    gid = next(g["id"] for g in r2.json() if g["name"] == group_name)
    roles_payload = []
    for rid in role_ids:
        r3 = requests.get(f"{KC_BASE}/admin/realms/{REALM}/roles-by-id/{rid}",
                          headers=headers(token))
        roles_payload.append(r3.json())
    requests.post(f"{KC_BASE}/admin/realms/{REALM}/groups/{gid}/role-mappings/realm",
                  headers=headers(token), data=json.dumps(roles_payload))
    print(f"  group '{group_name}' created")
    return gid


def create_client(token, client_id, redirect_uris, web_origins=["*"]):
    """Create confidential OIDC client. Returns client secret."""
    r = requests.get(f"{KC_BASE}/admin/realms/{REALM}/clients",
                     headers=headers(token), params={"clientId": client_id})
    existing = r.json()
    if existing:
        cid = existing[0]["id"]
        print(f"  client '{client_id}' already exists — fetching secret")
    else:
        payload = {
            "clientId": client_id, "enabled": True,
            "protocol": "openid-connect",
            "publicClient": False,
            "standardFlowEnabled": True,
            "directAccessGrantsEnabled": False,   # no password grant — forces browser SSO
            "redirectUris": redirect_uris,
            "webOrigins": web_origins,
            "defaultClientScopes": ["openid", "profile", "email", "roles"],
        }
        requests.post(f"{KC_BASE}/admin/realms/{REALM}/clients",
                      headers=headers(token), data=json.dumps(payload)).raise_for_status()
        r2 = requests.get(f"{KC_BASE}/admin/realms/{REALM}/clients",
                          headers=headers(token), params={"clientId": client_id})
        cid = r2.json()[0]["id"]
        print(f"  client '{client_id}' created")
    r4 = requests.get(f"{KC_BASE}/admin/realms/{REALM}/clients/{cid}/client-secret",
                      headers=headers(token))
    return r4.json().get("value", "")


def create_user(token, username, email, password, group_id, first="Robert", last="Elder"):
    r = requests.get(f"{KC_BASE}/admin/realms/{REALM}/users",
                     headers=headers(token), params={"username": username})
    if r.json():
        print(f"  user '{username}' already exists")
        return
    requests.post(f"{KC_BASE}/admin/realms/{REALM}/users",
                  headers=headers(token), data=json.dumps({
                      "username": username, "email": email,
                      "firstName": first, "lastName": last,
                      "enabled": True, "emailVerified": True,
                      "credentials": [{"type": "password", "value": password, "temporary": True}],
                  })).raise_for_status()
    r2 = requests.get(f"{KC_BASE}/admin/realms/{REALM}/users",
                      headers=headers(token), params={"username": username})
    uid = r2.json()[0]["id"]
    requests.put(f"{KC_BASE}/admin/realms/{REALM}/users/{uid}/groups/{group_id}",
                 headers=headers(token))
    print(f"  user '{username}' created (temporary password — must change on first login)")


def main():
    print("=== Keycloak Bootstrap ===")
    token = get_admin_token()
    print("✓ admin token obtained")

    print("\n[realm]")
    create_realm(token)
    token = get_admin_token()  # refresh after realm creation

    print("\n[roles]")
    admin_role_id = create_role(token, "admin", "Full platform access")
    user_role_id  = create_role(token, "user",  "Standard user access")

    print("\n[groups]")
    admins_gid = create_group(token, "admins", [admin_role_id, user_role_id])
    _users_gid = create_group(token, "users",  [user_role_id])

    print("\n[clients]")
    secrets = {}
    secrets["n8n"] = create_client(
        token, "n8n",
        redirect_uris=["https://n8n.private.sovereignadvisory.ai/*"],
    )
    secrets["webui"] = create_client(
        token, "webui",
        redirect_uris=["https://webui.private.sovereignadvisory.ai/*"],
    )
    secrets["litellm"] = create_client(
        token, "litellm",
        redirect_uris=["https://litellm.private.sovereignadvisory.ai/*"],
    )
    secrets["jupyter"] = create_client(
        token, "jupyter",
        redirect_uris=["https://jupyter.private.sovereignadvisory.ai/*"],
    )
    secrets["lead-review"] = create_client(
        token, "lead-review",
        redirect_uris=["https://sovereignadvisory.ai/auth/*",
                       "https://sovereignadvisory.ai/review/*"],
    )
    secrets["vaultwarden"] = create_client(
        token, "vaultwarden",
        redirect_uris=["https://vault.private.sovereignadvisory.ai/*"],
    )

    print("\n[admin user]")
    initial_password = (os.environ.get("KC_INITIAL_ADMIN_PASSWORD") or
                        input("Initial password for 'relder' (temporary): "))
    create_user(token, "relder", NOTIFY_EMAIL, initial_password, admins_gid)

    print("\n=== Client secrets — add ALL of these to .env ===")
    for name, secret in secrets.items():
        env_key = name.upper().replace("-", "_") + "_OIDC_CLIENT_SECRET"
        print(f"  {env_key}={secret}")

    print("\n=== Bootstrap complete ===")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run bootstrap on prod**

```bash
# On prod (Keycloak must be healthy first — see Task 0)
cd /opt/agentic-sdlc
source .env
KC_INITIAL_ADMIN_PASSWORD="<strong-temp-password>" \
KEYCLOAK_INTERNAL_URL=http://localhost:8080 \
python3 scripts/keycloak_bootstrap.py
```

Expected: all resources created; client secrets printed.

- [ ] **Step 3: Add ALL printed secrets to prod .env**

For every line printed (`*_OIDC_CLIENT_SECRET=<value>`), append to `/opt/agentic-sdlc/.env`.
**Confirm these specific keys are present** (they will be needed in later tasks):
- `N8N_OIDC_CLIENT_SECRET`
- `WEBUI_OIDC_CLIENT_SECRET`
- `LITELLM_OIDC_CLIENT_SECRET`
- `JUPYTER_OIDC_CLIENT_SECRET`
- `LEAD_REVIEW_OIDC_CLIENT_SECRET`
- `VAULTWARDEN_OIDC_CLIENT_SECRET`

Also update `.env.example` with empty placeholders for each.

- [ ] **Step 4: Verify realm and OIDC discovery**

```bash
# From prod VPS
curl -s http://localhost:8080/realms/agentic-sdlc/.well-known/openid-configuration \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print('issuer:', d['issuer'])"
```

Expected: `issuer: https://kc.sovereignadvisory.ai/realms/agentic-sdlc`
(Note: `KC_HOSTNAME` causes Keycloak to report the external hostname, not localhost.)

- [ ] **Step 5: Commit**

```bash
git add scripts/keycloak_bootstrap.py .env.example
git commit -m "feat: add Keycloak realm bootstrap script"
git push origin master
```

---

## Task 2: Add URL fixtures to conftest.py

Needed before writing SSO tests for private-network services.

**Files:**
- Modify: `tests/conftest.py`

- [ ] **Step 1: Add fixtures**

In `tests/conftest.py`, add after the existing `lead_review_url` fixture:

```python
LITELLM_URL = os.environ.get("LITELLM_URL", "https://litellm.private.sovereignadvisory.ai")
JUPYTER_URL = os.environ.get("JUPYTER_URL", "https://jupyter.private.sovereignadvisory.ai")
VAULT_URL   = os.environ.get("VAULT_URL",   "https://vault.private.sovereignadvisory.ai")


@pytest.fixture(scope="session")
def litellm_url() -> str:
    return LITELLM_URL.rstrip("/")


@pytest.fixture(scope="session")
def jupyter_url() -> str:
    return JUPYTER_URL.rstrip("/")


@pytest.fixture(scope="session")
def vault_url() -> str:
    return VAULT_URL.rstrip("/")
```

- [ ] **Step 2: Commit**

```bash
git add tests/conftest.py
git commit -m "test: add litellm_url, jupyter_url, vault_url fixtures"
```

---

## Task 3: Enable n8n SSO

**Files:**
- Modify: `docker-compose.yml` (n8n OIDC block)
- Modify: `docker-compose.prod.yml`
- Test: `tests/test_post_deploy.py`

> **n8n SSO callback URL:** n8n's SSO login callback is `/rest/sso/oidc/callback`, not `/rest/oauth2-credential/callback` (the latter is for credential-type OAuth2 flows). Verify against your exact n8n version if login redirects fail after enabling.

- [ ] **Step 1: Write the failing smoke test**

Add to `tests/test_post_deploy.py`:
```python
def test_n8n_redirects_to_keycloak_for_login(page, n8n_url):
    """Accessing n8n without a session must redirect to Keycloak login."""
    page.goto(f"{n8n_url}/")
    page.wait_for_url("**/realms/agentic-sdlc/**", timeout=10_000)
    assert "agentic-sdlc" in page.url
```

- [ ] **Step 2: Confirm test fails**

```bash
pytest tests/test_post_deploy.py::test_n8n_redirects_to_keycloak_for_login -v
```

Expected: FAIL — n8n shows its own login page.

- [ ] **Step 3: Enable OIDC in docker-compose.yml**

```yaml
# n8n service env block
- N8N_SSO_OIDC_ENABLED=true
- N8N_SSO_OIDC_ISSUER_URL=http://keycloak:8080/realms/agentic-sdlc
- N8N_SSO_OIDC_CLIENT_ID=n8n
- N8N_SSO_OIDC_CLIENT_SECRET=${N8N_OIDC_CLIENT_SECRET}
- N8N_SSO_OIDC_REDIRECT_URL=https://n8n.private.sovereignadvisory.ai/rest/sso/oidc/callback
```

Mirror in `docker-compose.prod.yml`.

- [ ] **Step 4: Redeploy n8n on prod**

```bash
cd /opt/agentic-sdlc && git pull
docker compose up -d n8n
sleep 5
```

- [ ] **Step 5: Run test**

```bash
pytest tests/test_post_deploy.py::test_n8n_redirects_to_keycloak_for_login -v
```

Expected: PASS.

If login redirects to Keycloak but returns to n8n with an error about the callback URL, check n8n logs (`docker logs n8n --tail=20`) — the callback path may differ by n8n version. Adjust `N8N_SSO_OIDC_REDIRECT_URL` and the Keycloak client redirect URI accordingly.

- [ ] **Step 6: Log in and verify**

Browse to `https://n8n.private.sovereignadvisory.ai`, complete Keycloak login, change temporary password. Confirm n8n loads.

- [ ] **Step 7: Commit**

```bash
git add docker-compose.yml docker-compose.prod.yml tests/test_post_deploy.py
git commit -m "feat: enable Keycloak SSO for n8n"
git push origin master
```

---

## Task 4: Wire Pipeline WebUI through oauth2-proxy

The `webui` service (port 3000) is the custom pipeline dashboard — it has no built-in OIDC. Protect it with an oauth2-proxy sidecar, same pattern as LiteLLM/JupyterLab.

**Files:**
- Modify: `docker-compose.yml` (add oauth2-proxy-webui service)
- Modify: `docker-compose.prod.yml`
- Modify: `nginx-private/conf.d/private.conf`
- Test: `tests/test_post_deploy.py`

- [ ] **Step 1: Write failing test**

```python
def test_webui_redirects_to_keycloak_for_login(page, webui_url):
    """Pipeline WebUI must redirect unauthenticated requests to Keycloak."""
    page.goto(f"{webui_url}/")
    page.wait_for_url("**/realms/agentic-sdlc/**", timeout=10_000)
    assert "agentic-sdlc" in page.url
```

- [ ] **Step 2: Add oauth2-proxy-webui to docker-compose.yml**

```yaml
  oauth2-proxy-webui:
    image: quay.io/oauth2-proxy/oauth2-proxy:v7.6.0
    container_name: oauth2_proxy_webui
    command:
      - --provider=oidc
      - --oidc-issuer-url=http://keycloak:8080/realms/agentic-sdlc
      - --client-id=webui
      - --client-secret=${WEBUI_OIDC_CLIENT_SECRET}
      - --redirect-url=https://webui.private.sovereignadvisory.ai/oauth2/callback
      - --upstream=http://webui:3000
      - --http-address=0.0.0.0:3001
      - --email-domain=*
      - --cookie-secret=${OAUTH2_PROXY_COOKIE_SECRET_WEBUI}
      - --cookie-secure=true
      - --skip-provider-button=true
      - --allowed-role=agentic-sdlc:admin
      - --allowed-role=agentic-sdlc:user
    networks:
      - vibe_net
    depends_on:
      keycloak:
        condition: service_healthy
    restart: unless-stopped
```

Note: `OAUTH2_PROXY_COOKIE_SECRET_WEBUI` is a distinct secret from the litellm/jupyter proxies. Generate with `openssl rand -base64 32`. Add to `.env.example`.

- [ ] **Step 3: Update nginx-private webui server block to route through proxy**

In `nginx-private/conf.d/private.conf`, change the webui block:
```nginx
server {
    listen 443 ssl;
    http2 on;
    server_name webui.private.sovereignadvisory.ai;
    ...
    location / {
        proxy_pass http://oauth2_proxy_webui:3001;    # was: webui:3000
        ...
    }
}
```

- [ ] **Step 4: Remove `profiles` guard from oauth2-proxy-litellm and oauth2-proxy-jupyter in docker-compose.yml**

Find and delete the `profiles:` blocks from both existing oauth2-proxy services so they start with the normal stack (they were gated behind `--profile oidc` which means they never started). These services are now always-on.

- [ ] **Step 5: Generate new cookie secret and add to prod .env**

```bash
echo "OAUTH2_PROXY_COOKIE_SECRET_WEBUI=$(openssl rand -base64 32)" >> /opt/agentic-sdlc/.env
```

- [ ] **Step 6: Redeploy on prod**

```bash
cd /opt/agentic-sdlc && git pull
docker compose up -d oauth2-proxy-webui oauth2-proxy-litellm oauth2-proxy-jupyter
docker compose -f docker-compose.prod.yml up -d nginx-private
```

- [ ] **Step 7: Run test**

```bash
pytest tests/test_post_deploy.py::test_webui_redirects_to_keycloak_for_login -v
```

- [ ] **Step 8: Commit**

```bash
git add docker-compose.yml docker-compose.prod.yml \
        nginx-private/conf.d/private.conf tests/test_post_deploy.py .env.example
git commit -m "feat: protect pipeline WebUI with Keycloak oauth2-proxy"
git push origin master
```

---

## Task 5: Wire LiteLLM UI through oauth2-proxy

`oauth2-proxy-litellm` is already in docker-compose.yml (profiles guard removed in Task 4). nginx-private bypasses it — fix the route.

**Files:**
- Modify: `docker-compose.yml` (fix redirect URL and remove profiles — done in Task 4)
- Modify: `nginx-private/conf.d/private.conf` (litellm server block)
- Test: `tests/test_post_deploy.py`

- [ ] **Step 1: Write failing test**

```python
def test_litellm_redirects_to_keycloak_for_login(page, litellm_url):
    """LiteLLM UI must redirect unauthenticated requests to Keycloak."""
    page.goto(f"{litellm_url}/")
    page.wait_for_url("**/realms/agentic-sdlc/**", timeout=10_000)
    assert "agentic-sdlc" in page.url
```

- [ ] **Step 2: Fix oauth2-proxy-litellm redirect URL in docker-compose.yml**

Change:
```yaml
- --redirect-url=http://localhost:4001/oauth2/callback
```
To:
```yaml
- --redirect-url=https://litellm.private.sovereignadvisory.ai/oauth2/callback
```

- [ ] **Step 3: Update nginx-private litellm server block**

```nginx
location / {
    proxy_pass http://oauth2_proxy_litellm:4001;    # was: litellm:4000
    ...
}
```

- [ ] **Step 4: Redeploy and test**

```bash
cd /opt/agentic-sdlc && git pull
docker compose up -d oauth2-proxy-litellm
docker compose -f docker-compose.prod.yml up -d nginx-private
pytest tests/test_post_deploy.py::test_litellm_redirects_to_keycloak_for_login -v
```

- [ ] **Step 5: Commit**

```bash
git add docker-compose.yml nginx-private/conf.d/private.conf tests/test_post_deploy.py
git commit -m "feat: route LiteLLM UI through Keycloak oauth2-proxy"
git push origin master
```

---

## Task 6: Wire JupyterLab through oauth2-proxy

Same pattern as Task 5.

**Files:**
- Modify: `docker-compose.yml` (fix redirect URL — profiles already removed in Task 4)
- Modify: `nginx-private/conf.d/private.conf`
- Test: `tests/test_post_deploy.py`

- [ ] **Step 1: Write failing test**

```python
def test_jupyter_redirects_to_keycloak_for_login(page, jupyter_url):
    """JupyterLab must redirect unauthenticated requests to Keycloak."""
    page.goto(f"{jupyter_url}/")
    page.wait_for_url("**/realms/agentic-sdlc/**", timeout=10_000)
    assert "agentic-sdlc" in page.url
```

- [ ] **Step 2: Fix oauth2-proxy-jupyter redirect URL**

```yaml
- --redirect-url=https://jupyter.private.sovereignadvisory.ai/oauth2/callback
# was: http://localhost:8889/oauth2/callback
```

- [ ] **Step 3: Update nginx-private jupyter server block**

```nginx
location / {
    proxy_pass http://oauth2_proxy_jupyter:8889;    # was: jupyter:8888
    ...
}
```

- [ ] **Step 4: Redeploy and test**

```bash
cd /opt/agentic-sdlc && git pull
docker compose up -d oauth2-proxy-jupyter
docker compose -f docker-compose.prod.yml up -d nginx-private
pytest tests/test_post_deploy.py::test_jupyter_redirects_to_keycloak_for_login -v
```

- [ ] **Step 5: Commit**

```bash
git add docker-compose.yml nginx-private/conf.d/private.conf tests/test_post_deploy.py
git commit -m "feat: route JupyterLab through Keycloak oauth2-proxy"
git push origin master
```

---

## Task 7: Enable lead-review OIDC

The OIDC code is already in `lead_review_server.py`. Flip the switch and confirm the redirect to Keycloak works end-to-end.

**Files:**
- Modify: `docker-compose.prod.yml` (set OIDC_ENABLED=true)
- Prod `.env`: confirm secrets present
- Test: `tests/test_post_deploy.py`

- [ ] **Step 1: Write failing test**

```python
def test_lead_review_auth_redirects_to_keycloak(page, lead_review_url):
    """Lead review /auth/login must return 302 → Keycloak when OIDC enabled."""
    r = page.request.get(f"{lead_review_url}/auth/login",
                         max_redirects=0)
    assert r.status == 302, f"Expected 302 redirect, got {r.status}"
    location = r.headers.get("location", "")
    assert "kc.sovereignadvisory.ai" in location or "agentic-sdlc" in location, \
        f"Redirect target is not Keycloak: {location}"
```

- [ ] **Step 2: Confirm `LEAD_REVIEW_OIDC_CLIENT_SECRET` is set in prod .env**

```bash
grep LEAD_REVIEW_OIDC_CLIENT_SECRET /opt/agentic-sdlc/.env
```

Expected: non-empty value (copied from Task 1, Step 3 output). If missing, re-run
`python3 scripts/keycloak_bootstrap.py` and copy the printed secret.

- [ ] **Step 3: Enable OIDC in docker-compose.prod.yml**

Add/update in the lead-review service env:
```yaml
- OIDC_ENABLED=true
- LEAD_REVIEW_PUBLIC_URL=https://sovereignadvisory.ai
- KEYCLOAK_EXTERNAL_URL=https://kc.sovereignadvisory.ai
```

In `docker-compose.yml`, leave `OIDC_ENABLED=${OIDC_ENABLED:-false}` as the default for local dev.

- [ ] **Step 4: Redeploy and test**

```bash
cd /opt/agentic-sdlc && git pull
docker compose -f docker-compose.prod.yml up -d sa_lead_review
pytest tests/test_post_deploy.py::test_lead_review_auth_redirects_to_keycloak -v
```

- [ ] **Step 5: Commit**

```bash
git add docker-compose.yml docker-compose.prod.yml tests/test_post_deploy.py
git commit -m "feat: enable Keycloak OIDC for lead-review portal"
git push origin master
```

---

## Task 8: Export Realm to Git (GitOps Snapshot)

**Files:**
- Create: `scripts/keycloak_export_realm.sh`
- Create: `keycloak/realm-export.json`

- [ ] **Step 1: Write export script**

```bash
#!/usr/bin/env bash
# keycloak_export_realm.sh — export agentic-sdlc realm for GitOps.
# Usage: bash scripts/keycloak_export_realm.sh
# Run from repo root on a machine with Docker access to the Keycloak container.
# Uses --users skip: user records (including hashed passwords) are NOT included.
# Client secrets are never included in Keycloak exports — safe to commit.
set -euo pipefail

REALM="agentic-sdlc"
OUTPUT="keycloak/realm-export.json"
KC_CONTAINER="keycloak"

echo "Exporting realm '$REALM' (users skipped for security)..."
mkdir -p keycloak

docker exec "$KC_CONTAINER" \
  /opt/keycloak/bin/kc.sh export \
  --dir /tmp/kc-export \
  --realm "$REALM" \
  --users skip 2>/dev/null || true   # suppress verbose startup noise

docker cp "$KC_CONTAINER:/tmp/kc-export/${REALM}-realm.json" "$OUTPUT"

# Sanity check: client secrets must be absent
python3 -c "
import json, sys
with open('$OUTPUT') as f:
    realm = json.load(f)
for c in realm.get('clients', []):
    secret = c.get('secret', '')
    if secret and len(secret) > 5:
        print(f'WARNING: client {c[\"clientId\"]} has embedded secret — remove before commit')
        sys.exit(1)
print('Secret check passed — safe to commit')
"
echo "Realm exported to $OUTPUT"
```

- [ ] **Step 2: Run export on prod and copy locally**

```bash
# On prod
cd /opt/agentic-sdlc
bash scripts/keycloak_export_realm.sh

# Locally
scp root@sovereignadvisory.ai:/opt/agentic-sdlc/keycloak/realm-export.json \
    keycloak/realm-export.json
```

- [ ] **Step 3: Commit**

```bash
git add keycloak/ scripts/keycloak_export_realm.sh
git commit -m "feat: add Keycloak realm GitOps snapshot and export script"
git push origin master
```

---

## Task 9: Deploy Vaultwarden

Self-hosted Bitwarden-compatible vault at `vault.private.sovereignadvisory.ai`. Accessible via browser extension / mobile app wherever Twingate is running.

> **Vaultwarden SSO note:** SSO support (`SSO_ENABLED`, `SSO_AUTHORITY`, `SSO_CLIENT_ID`, `SSO_CLIENT_SECRET`) requires Vaultwarden 1.32+. Use `vaultwarden/server:latest` and verify SSO vars are recognized in startup logs. If not, pin to a specific 1.32+ tag.
>
> **SSO_AUTHORITY uses external Keycloak URL:** Unlike other services that use `http://keycloak:8080` internally, Vaultwarden's SSO discovery must use the external URL (`https://kc.sovereignadvisory.ai/...`) because the same URL is used for both server-side JWKS fetching and browser-side redirects. This requires the container to resolve `kc.sovereignadvisory.ai` and trust its TLS cert. Both work by default since the container has outbound internet access and uses the system CA bundle.
>
> **`SSO_ONLY=false`:** Vaultwarden allows a master-password fallback in addition to Keycloak SSO. This is intentional for admin account recovery. Day-to-day login uses the Keycloak button.

**Files:**
- Modify: `docker-compose.yml` (add vaultwarden service + volume)
- Modify: `docker-compose.prod.yml` (if vaultwarden needs prod-specific settings)
- Modify: `nginx-private/conf.d/private.conf` (add vault server block)
- Modify: `.env.example`
- Test: `tests/test_post_deploy.py`

- [ ] **Step 1: Add Vaultwarden service to docker-compose.yml**

```yaml
  vaultwarden:
    image: vaultwarden/server:latest
    container_name: vaultwarden
    restart: unless-stopped
    environment:
      - DOMAIN=https://vault.private.sovereignadvisory.ai
      - SIGNUPS_ALLOWED=false
      - INVITATIONS_ALLOWED=true
      - ADMIN_TOKEN=${VAULTWARDEN_ADMIN_TOKEN}
      # Keycloak OIDC SSO (requires Vaultwarden 1.32+)
      - SSO_ENABLED=true
      - SSO_ONLY=false                   # keeps master-password for admin recovery
      - SSO_AUTHORITY=https://kc.sovereignadvisory.ai/realms/agentic-sdlc
      - SSO_CLIENT_ID=vaultwarden
      - SSO_CLIENT_SECRET=${VAULTWARDEN_OIDC_CLIENT_SECRET}
      # Email
      - SMTP_HOST=${NEO_SMTP_HOST}
      - SMTP_PORT=${NEO_SMTP_PORT}
      - SMTP_SECURITY=force_tls
      - SMTP_USERNAME=${NEO_SMTP_USER}
      - SMTP_PASSWORD=${NEO_SMTP_PASS}
      - SMTP_FROM=${NOTIFY_EMAIL}
      - SMTP_FROM_NAME=Sovereign Advisory Vault
    volumes:
      - vaultwarden_data:/data
    networks:
      - vibe_net
    labels:
      com.sovereignadvisory.service: "vaultwarden"
```

Add `vaultwarden_data:` under the top-level `volumes:` key.

- [ ] **Step 2: Add vault.private nginx server block**

In `nginx-private/conf.d/private.conf`, append:

```nginx
server {
    listen 443 ssl;
    http2 on;
    server_name vault.private.sovereignadvisory.ai;

    ssl_certificate     /etc/letsencrypt/live/private.sovereignadvisory.ai/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/private.sovereignadvisory.ai/privkey.pem;

    client_max_body_size 10m;

    location / {
        proxy_pass http://vaultwarden:80;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $connection_upgrade;   # use map variable, not "upgrade"
    }

    # WebSocket for live vault sync
    location /notifications/hub {
        proxy_pass http://vaultwarden:3012;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $connection_upgrade;
        proxy_set_header Host $host;
    }
}
```

- [ ] **Step 3: Add env vars to .env.example**

```
VAULTWARDEN_ADMIN_TOKEN=   # generate: openssl rand -base64 48
VAULTWARDEN_OIDC_CLIENT_SECRET=
OAUTH2_PROXY_COOKIE_SECRET_WEBUI=  # generate: openssl rand -base64 32
```

- [ ] **Step 4: Generate secrets and add to prod .env**

```bash
echo "VAULTWARDEN_ADMIN_TOKEN=$(openssl rand -base64 48)" >> /opt/agentic-sdlc/.env
# VAULTWARDEN_OIDC_CLIENT_SECRET was printed in Task 1 Step 3
# Confirm it's present:
grep VAULTWARDEN_OIDC_CLIENT_SECRET /opt/agentic-sdlc/.env
```

- [ ] **Step 5: Write smoke test**

```python
def test_vaultwarden_reachable(page, vault_url):
    """Vaultwarden login page must be reachable via private network."""
    page.goto(f"{vault_url}/")
    page.wait_for_load_state("networkidle")
    # Vaultwarden serves its own login page (SSO button present) or redirects
    assert page.title() != "" or "vault" in page.url.lower(), \
        f"Vaultwarden not reachable at {vault_url}"
```

- [ ] **Step 6: Deploy on prod**

```bash
cd /opt/agentic-sdlc && git pull
docker compose up -d vaultwarden
sleep 10
docker logs vaultwarden --tail=10
# Confirm SSO env vars recognized (no "unknown env var" warnings)
docker compose -f docker-compose.prod.yml up -d nginx-private
```

- [ ] **Step 7: Verify reachability (must be on Twingate)**

```bash
# From a machine with Twingate active:
curl -sI https://vault.private.sovereignadvisory.ai/ | head -3
```

Expected: `HTTP/2 200`.

- [ ] **Step 8: Log in via Keycloak SSO**

Browse to `https://vault.private.sovereignadvisory.ai`, click SSO/Keycloak login, authenticate. Vaultwarden auto-creates the account on first login.

- [ ] **Step 9: Install Bitwarden browser extension**

Extension Settings → Self-hosted → Server URL: `https://vault.private.sovereignadvisory.ai`. Login using Enterprise SSO. Works wherever Twingate is running.

- [ ] **Step 10: Commit**

```bash
git add docker-compose.yml docker-compose.prod.yml \
        nginx-private/conf.d/private.conf \
        tests/test_post_deploy.py .env.example
git commit -m "feat: deploy Vaultwarden with Keycloak SSO at vault.private"
git push origin master
```

---

## Task 10: Final Verification + Re-export Realm

- [ ] **Step 1: Run full SSO test suite**

```bash
pytest tests/test_post_deploy.py -v -k "keycloak or sso or redirect or vaultwarden"
```

Expected: all new SSO tests pass.

- [ ] **Step 2: Re-export realm (captures Vaultwarden client)**

```bash
# On prod
bash scripts/keycloak_export_realm.sh
# Locally
scp root@sovereignadvisory.ai:/opt/agentic-sdlc/keycloak/realm-export.json keycloak/realm-export.json
```

- [ ] **Step 3: Final commit + push**

```bash
git add keycloak/realm-export.json
git commit -m "chore: update Keycloak realm snapshot with all SSO clients"
git push origin master
```

- [ ] **Step 4: Sync prod**

```bash
ssh root@sovereignadvisory.ai "cd /opt/agentic-sdlc && git pull"
```

---

## Password Policy Reference

Configured in Task 1 via `passwordPolicy` in the realm. To adjust anytime:
**Keycloak Admin UI** → Realm Settings → Authentication → Password Policy → no redeployment needed.

Current policy: 30-day forced rotation, 12-char min, upper+lower+digit+special, no reuse of last 5 passwords.

---

## Multi-User Onboarding (future)

1. Create user in Keycloak Admin UI
2. Assign to `admins` or `users` group
3. User gets temporary password email, changes on first login
4. Access to all SSO-protected tools is automatic via group → role mapping
5. `OAUTH_ALLOWED_ROLES` in oauth2-proxy and `OAUTH_ADMIN_ROLES` in any native OIDC apps enforce role-based access — no per-tool config needed when adding users
