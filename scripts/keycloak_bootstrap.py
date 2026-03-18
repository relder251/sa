#!/usr/bin/env python3
"""
keycloak_bootstrap.py — one-time Keycloak realm setup.
Usage: python scripts/keycloak_bootstrap.py
Requires: KEYCLOAK_ADMIN_PASSWORD env var (loaded from .env if present).
Idempotent: checks for existing resources before creating.
"""
import os, sys, json, requests
from pathlib import Path

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
            "directAccessGrantsEnabled": False,
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
    token = get_admin_token()

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
