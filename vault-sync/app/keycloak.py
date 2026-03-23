"""
vault-sync/app/keycloak.py
Keycloak admin REST API helpers + CRED-03 bidirectional sync adapter.

Drift detection compares vault user-credentials items against Keycloak users
in the configured realm, reporting missing users in either direction.

Sync pushes vault credential passwords to matching Keycloak users.
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


# ---------------------------------------------------------------------------
# Low-level admin API helpers
# ---------------------------------------------------------------------------

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


def list_users(token: str) -> list[dict]:
    """Return all users in the realm (pages through if needed)."""
    users: list[dict] = []
    first = 0
    batch = 100
    while True:
        url = (
            f"{KEYCLOAK_ADMIN_URL}/admin/realms/{KEYCLOAK_REALM}/users"
            f"?first={first}&max={batch}"
        )
        req = urllib.request.Request(url)
        req.add_header("Authorization", f"Bearer {token}")
        with urllib.request.urlopen(req, timeout=15) as resp:
            page = json.loads(resp.read())
        users.extend(page)
        if len(page) < batch:
            break
        first += batch
    return users


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


# ---------------------------------------------------------------------------
# CRED-03 — Bidirectional sync adapter
# ---------------------------------------------------------------------------

def _kc_user_key(user: dict) -> str:
    """Canonical lookup key: prefer email, fall back to username."""
    return (user.get("email") or user.get("username", "")).lower()


def drift_report(vault_items: list[dict]) -> dict:
    """
    Compare vault user-credentials items against live Keycloak users.

    Returns:
      {
        "vault_only":     [{"name": ..., "vault_id": ...}, ...],   # in vault, not in Keycloak
        "keycloak_only":  [{"username": ..., "email": ..., "user_id": ...}, ...],  # in KC, not in vault
        "matched":        [{"name": ..., "vault_id": ..., "keycloak_user_id": ...}, ...],
        "keycloak_total": int,
        "vault_total":    int,
      }
    """
    token = admin_token()
    kc_users = list_users(token)

    # Build lookup: email/username → kc user dict
    kc_index: dict[str, dict] = {}
    for u in kc_users:
        for key in (u.get("email", "").lower(), u.get("username", "").lower()):
            if key:
                kc_index[key] = u

    # Extract vault user items (collection == user-credentials)
    from models import FIELD_COLLECTION
    vault_user_items = [
        i for i in vault_items
        if any(
            f.get("name") == FIELD_COLLECTION and f.get("value") == "user-credentials"
            for f in (i.get("fields") or [])
        )
    ]

    matched, vault_only = [], []
    matched_kc_ids: set[str] = set()

    for item in vault_user_items:
        name = item.get("name", "")
        vault_id = item.get("id", "")
        # Try matching by item name (or login username) against KC email/username
        login_username = (item.get("login") or {}).get("username", "")
        candidates = {name.lower(), login_username.lower()} - {""}
        kc_user = None
        for candidate in candidates:
            if candidate in kc_index:
                kc_user = kc_index[candidate]
                break

        if kc_user:
            matched.append({
                "name":             name,
                "vault_id":         vault_id,
                "keycloak_user_id": kc_user["id"],
                "username":         kc_user.get("username", ""),
                "email":            kc_user.get("email", ""),
            })
            matched_kc_ids.add(kc_user["id"])
        else:
            vault_only.append({"name": name, "vault_id": vault_id})

    # KC users not matched to any vault item (exclude service accounts: no email, ends with -service)
    keycloak_only = []
    for u in kc_users:
        if u["id"] in matched_kc_ids:
            continue
        username = u.get("username", "")
        if username.endswith("-service") or not u.get("email"):
            continue
        keycloak_only.append({
            "user_id":  u["id"],
            "username": username,
            "email":    u.get("email", ""),
        })

    return {
        "vault_only":      vault_only,
        "keycloak_only":   keycloak_only,
        "matched":         matched,
        "keycloak_total":  len(kc_users),
        "vault_total":     len(vault_user_items),
    }


def sync_all(vault_items: list[dict]) -> dict:
    """
    Push vault user-credential passwords to all matched Keycloak users.

    Returns:
      {"synced": [...], "skipped": [...], "errors": [...]}
    """
    token = admin_token()
    kc_users = list_users(token)

    kc_index: dict[str, dict] = {}
    for u in kc_users:
        for key in (u.get("email", "").lower(), u.get("username", "").lower()):
            if key:
                kc_index[key] = u

    from models import FIELD_COLLECTION
    vault_user_items = [
        i for i in vault_items
        if any(
            f.get("name") == FIELD_COLLECTION and f.get("value") == "user-credentials"
            for f in (i.get("fields") or [])
        )
    ]

    synced, skipped, errors = [], [], []

    for item in vault_user_items:
        name = item.get("name", "")
        login = item.get("login") or {}
        password = login.get("password")
        login_username = login.get("username", "")

        if not password:
            skipped.append({"name": name, "reason": "no password in vault"})
            continue

        candidates = {name.lower(), login_username.lower()} - {""}
        kc_user = None
        for candidate in candidates:
            if candidate in kc_index:
                kc_user = kc_index[candidate]
                break

        if not kc_user:
            skipped.append({"name": name, "reason": "no matching Keycloak user"})
            continue

        try:
            reset_password(token, kc_user["id"], password)
            synced.append({
                "name":     name,
                "user_id":  kc_user["id"],
                "username": kc_user.get("username", ""),
            })
        except Exception as exc:
            errors.append({"name": name, "error": str(exc)})

    return {"synced": synced, "skipped": skipped, "errors": errors}
