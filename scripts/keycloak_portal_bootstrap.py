#!/usr/bin/env python3
"""Create the 'portal' OIDC client in Keycloak agentic-sdlc realm.

Usage:
  KEYCLOAK_URL=https://kc.sovereignadvisory.ai \
  KEYCLOAK_ADMIN=admin \
  KEYCLOAK_ADMIN_PASSWORD=<password> \
  PORTAL_OIDC_CLIENT_SECRET=<secret> \
  python3 scripts/keycloak_portal_bootstrap.py
"""
import os, sys, json
from urllib.parse import urlencode
from urllib.request import urlopen, Request
from urllib.error import HTTPError

KC_URL = os.environ["KEYCLOAK_URL"].rstrip("/")
ADMIN = os.environ["KEYCLOAK_ADMIN"]
PASSWORD = os.environ["KEYCLOAK_ADMIN_PASSWORD"]
CLIENT_SECRET = os.environ["PORTAL_OIDC_CLIENT_SECRET"]
REALM = "agentic-sdlc"


def api(url, method="GET", data=None, token=None, form=False):
    if form:
        body = urlencode(data).encode()
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
    else:
        body = json.dumps(data).encode() if data else None
        headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    r = Request(url, data=body, headers=headers, method=method)
    try:
        with urlopen(r) as resp:
            return resp.status, resp.read()
    except HTTPError as e:
        return e.code, e.read()


# 1. Get admin token
status, body = api(
    f"{KC_URL}/realms/master/protocol/openid-connect/token",
    method="POST",
    data={"grant_type": "password", "client_id": "admin-cli",
          "username": ADMIN, "password": PASSWORD},
    form=True,
)
if status != 200:
    print(f"✗ Failed to get admin token: HTTP {status} — {body.decode()}")
    sys.exit(1)
token = json.loads(body)["access_token"]
print("✓ Got admin token")

# 2. Check if client already exists
status, body = api(f"{KC_URL}/admin/realms/{REALM}/clients?clientId=portal", token=token)
clients = json.loads(body)
if clients:
    print("✓ 'portal' client already exists — updating secret")
    client_id = clients[0]["id"]
    api(f"{KC_URL}/admin/realms/{REALM}/clients/{client_id}/client-secret",
        method="PUT",
        data={"type": "secret", "value": CLIENT_SECRET},
        token=token)
    print("✓ Secret updated")
    sys.exit(0)

# 3. Create client
client_def = {
    "clientId": "portal",
    "name": "Internal Access Portal",
    "enabled": True,
    "protocol": "openid-connect",
    "publicClient": False,
    "secret": CLIENT_SECRET,
    "redirectUris": ["https://home.private.sovereignadvisory.ai/oauth2/callback"],
    "webOrigins": ["https://home.private.sovereignadvisory.ai"],
    "standardFlowEnabled": True,
    "directAccessGrantsEnabled": False,
    "attributes": {"pkce.code.challenge.method": "S256"},
    "defaultClientScopes": ["openid", "email", "profile"],
}
status, body = api(f"{KC_URL}/admin/realms/{REALM}/clients",
                   method="POST", data=client_def, token=token)
if status == 201:
    print("✓ 'portal' client created successfully")
else:
    print(f"✗ Failed: HTTP {status} — {body.decode()}")
    sys.exit(1)
