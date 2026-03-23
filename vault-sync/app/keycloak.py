"""
vault-sync/app/keycloak.py
Keycloak admin REST API helpers for user management.
Full bidirectional sync adapter is implemented in CRED-03.
"""

import json
import logging
import os
import urllib.parse
import urllib.request

log = logging.getLogger(__name__)

KEYCLOAK_ADMIN_URL  = os.environ.get("KEYCLOAK_ADMIN_URL", "").rstrip("/")
KEYCLOAK_ADMIN_USER = os.environ.get("KEYCLOAK_ADMIN_USER", "admin")
KEYCLOAK_ADMIN_PASS = os.environ.get("KEYCLOAK_ADMIN_PASS", "")
KEYCLOAK_REALM      = os.environ.get("KEYCLOAK_REALM", "agentic-sdlc")
KEYCLOAK_SYNC_ITEMS = {
    s.strip().lower()
    for s in os.environ.get("KEYCLOAK_SYNC_ITEMS", "").split(",")
    if s.strip()
}


def admin_token() -> str:
    """Obtain a Keycloak admin-cli access token."""
    if not KEYCLOAK_ADMIN_URL or not KEYCLOAK_ADMIN_PASS:
        raise RuntimeError(
            "KEYCLOAK_ADMIN_URL and KEYCLOAK_ADMIN_PASS must be set for Keycloak sync"
        )
    token_url = f"{KEYCLOAK_ADMIN_URL}/realms/master/protocol/openid-connect/token"
    payload = urllib.parse.urlencode({
        "client_id":  "admin-cli",
        "grant_type": "password",
        "username":   KEYCLOAK_ADMIN_USER,
        "password":   KEYCLOAK_ADMIN_PASS,
    }).encode()
    req = urllib.request.Request(token_url, data=payload, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read())
    token = data.get("access_token")
    if not token:
        raise RuntimeError(f"Failed to get Keycloak admin token: {data}")
    return token


def find_user(token: str, identifier: str) -> dict:
    """
    Find a Keycloak user by email or username.
    Returns the user dict; raises ValueError if not found.
    """
    for param in (
        f"email={urllib.parse.quote(identifier)}",
        f"username={urllib.parse.quote(identifier)}",
        f"search={urllib.parse.quote(identifier)}",
    ):
        url = f"{KEYCLOAK_ADMIN_URL}/admin/realms/{KEYCLOAK_REALM}/users?{param}&max=10"
        req = urllib.request.Request(url)
        req.add_header("Authorization", f"Bearer {token}")
        with urllib.request.urlopen(req, timeout=15) as resp:
            users = json.loads(resp.read())
        if users:
            return users[0]
    raise ValueError(f"No Keycloak user found matching: {identifier!r}")


def reset_password(token: str, user_id: str, new_password: str) -> None:
    """Reset a Keycloak user's password."""
    url = f"{KEYCLOAK_ADMIN_URL}/admin/realms/{KEYCLOAK_REALM}/users/{user_id}/reset-password"
    payload = json.dumps({
        "type": "password", "value": new_password, "temporary": False
    }).encode()
    req = urllib.request.Request(url, data=payload, method="PUT")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=15) as resp:
        if resp.status != 204:
            raise RuntimeError(f"Keycloak reset-password returned HTTP {resp.status}")
    log.info("Keycloak password reset for user_id=%s", user_id)


def sync_password(username: str, password: str) -> dict:
    """
    High-level: get admin token, find user, reset password.
    Returns dict with user_id, username, email on success.
    """
    token = admin_token()
    user = find_user(token, username)
    reset_password(token, user["id"], password)
    return {
        "user_id":  user["id"],
        "username": user.get("username", ""),
        "email":    user.get("email", ""),
    }
