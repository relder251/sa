"""
vault-sync/app.py
Flask HTTP API wrapping the Bitwarden CLI (bw) for programmatic Vaultwarden updates.

Environment variables required:
  BW_SERVER       — Vaultwarden URL, e.g. https://vault.private.sovereignadvisory.ai
  BW_CLIENTID     — Vaultwarden Settings → My Account → API Key → client_id
  BW_CLIENTSECRET — Vaultwarden Settings → My Account → API Key → client_secret
  BW_MASTER_PASS  — vault master password (used to unlock after API-key login)

Optional Keycloak sync variables (enables /update-keycloak and atomic sync on /update):
  KEYCLOAK_ADMIN_URL   — e.g. https://kc.sovereignadvisory.ai
  KEYCLOAK_ADMIN_USER  — Keycloak admin username (default: admin)
  KEYCLOAK_ADMIN_PASS  — Keycloak admin password
  KEYCLOAK_REALM       — realm to manage (default: agentic-sdlc)
  KEYCLOAK_SYNC_ITEMS  — comma-separated vault item names that should trigger
                         automatic Keycloak password sync when updated via /update
                         e.g. "Keycloak SSO"
  KEYCLOAK_SYNC_INTERVAL — polling interval in seconds for automatic Keycloak sync
                           when any user-credentials item changes (default: 300, 0 = disabled)
"""

import hashlib
import json
import os
import subprocess
import logging
import urllib.request
import urllib.parse
from flask import Flask, request, jsonify

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# Sentry / GlitchTip error monitoring (optional — only active when SENTRY_DSN is set)
_SENTRY_DSN = os.environ.get("SENTRY_DSN", "")
if _SENTRY_DSN:
    try:
        import sentry_sdk
        from sentry_sdk.integrations.flask import FlaskIntegration
        sentry_sdk.init(
            dsn=_SENTRY_DSN,
            integrations=[FlaskIntegration()],
            traces_sample_rate=0.2,
            environment=os.environ.get("ENVIRONMENT", "production"),
        )
        log.info("Sentry SDK initialised (GlitchTip)")
    except ImportError:
        log.warning("sentry-sdk not installed; error monitoring disabled")

# Module-level session token cache; None means we need to (re-)authenticate.
_BW_SESSION = None

BW_SERVER      = os.environ.get("BW_SERVER", "https://vault.private.sovereignadvisory.ai")
BW_CLIENTID    = os.environ.get("BW_CLIENTID", "")
BW_CLIENTSECRET = os.environ.get("BW_CLIENTSECRET", "")
BW_MASTER_PASS = os.environ.get("BW_MASTER_PASS", "")

# Keycloak sync config (optional)
KEYCLOAK_ADMIN_URL  = os.environ.get("KEYCLOAK_ADMIN_URL", "").rstrip("/")
KEYCLOAK_ADMIN_USER = os.environ.get("KEYCLOAK_ADMIN_USER", "admin")
KEYCLOAK_ADMIN_PASS = os.environ.get("KEYCLOAK_ADMIN_PASS", "")
KEYCLOAK_REALM      = os.environ.get("KEYCLOAK_REALM", "agentic-sdlc")
KEYCLOAK_SYNC_ITEMS = {
    s.strip().lower()
    for s in os.environ.get("KEYCLOAK_SYNC_ITEMS", "").split(",")
    if s.strip()
}
KEYCLOAK_SYNC_INTERVAL = int(os.environ.get("KEYCLOAK_SYNC_INTERVAL", "300"))  # 0 = disabled


def _credential_hash(items: list) -> str:
    """Stable hash of all user-credentials items for change detection."""
    parts = []
    for item in items:
        fields = item.get("fields") or []
        is_user_cred = any(
            f.get("name") == "collection" and f.get("value") == "user-credentials"
            for f in fields
        )
        if is_user_cred:
            login = item.get("login") or {}
            parts.append(f"{item.get('name','')}:{login.get('username','')}:{login.get('password','')}")
    parts.sort()
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()


def _run(args, input_text=None, check=True):
    """Run a bw CLI command, injecting the current session token when available."""
    env = os.environ.copy()
    env["BW_SERVER"] = BW_SERVER
    if _BW_SESSION:
        env["BW_SESSION"] = _BW_SESSION

    result = subprocess.run(
        ["bw"] + args,
        input=input_text,
        capture_output=True,
        text=True,
        env=env,
        check=check,
    )
    return result


def _authenticate():
    """Configure server, log in with API key, unlock with master password, cache session."""
    global _BW_SESSION

    env = os.environ.copy()
    env["BW_SERVER"] = BW_SERVER
    env["BW_CLIENTID"] = BW_CLIENTID
    env["BW_CLIENTSECRET"] = BW_CLIENTSECRET

    log.info("Configuring bw server: %s", BW_SERVER)
    subprocess.run(["bw", "config", "server", BW_SERVER], capture_output=True, env=env)

    log.info("Logging in with API key...")
    subprocess.run(
        ["bw", "login", "--apikey"],
        capture_output=True, text=True, env=env, check=False,
    )

    log.info("Unlocking vault...")
    result = subprocess.run(
        ["bw", "unlock", "--passwordenv", "BW_MASTER_PASS", "--raw"],
        capture_output=True,
        text=True,
        env={**env, "BW_MASTER_PASS": BW_MASTER_PASS},
        check=False,
    )
    session = result.stdout.strip()

    if not session:
        raise RuntimeError(
            "bw unlock returned empty session — check BW_MASTER_PASS and server connectivity"
        )

    _BW_SESSION = session
    log.info("Vault unlocked; session token cached.")

    log.info("Syncing vault...")
    _run(["sync"])
    log.info("Vault sync complete.")


def _ensure_session():
    """Authenticate if no session token is cached."""
    global _BW_SESSION
    if not _BW_SESSION:
        _authenticate()


def _with_reauth(fn):
    """Call fn(); on subprocess/OSError, re-authenticate once and retry."""
    global _BW_SESSION
    try:
        return fn()
    except (subprocess.CalledProcessError, OSError) as exc:
        log.warning("bw command failed (%s); re-authenticating and retrying...", exc)
        _BW_SESSION = None
        _authenticate()
        return fn()


# ---------------------------------------------------------------------------
# Keycloak helpers
# ---------------------------------------------------------------------------

def _keycloak_admin_token():
    """Obtain a Keycloak admin-cli access token. Raises RuntimeError on failure."""
    if not KEYCLOAK_ADMIN_URL or not KEYCLOAK_ADMIN_PASS:
        raise RuntimeError(
            "KEYCLOAK_ADMIN_URL and KEYCLOAK_ADMIN_PASS must be set for Keycloak sync"
        )
    token_url = f"{KEYCLOAK_ADMIN_URL}/realms/master/protocol/openid-connect/token"
    payload = urllib.parse.urlencode({
        "client_id": "admin-cli",
        "grant_type": "password",
        "username": KEYCLOAK_ADMIN_USER,
        "password": KEYCLOAK_ADMIN_PASS,
    }).encode()
    req = urllib.request.Request(token_url, data=payload, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read())
    token = data.get("access_token")
    if not token:
        raise RuntimeError(f"Failed to get Keycloak admin token: {data}")
    return token


def _keycloak_find_user(admin_token, identifier):
    """
    Find a user in KEYCLOAK_REALM by email or username.
    Returns the user dict, or raises ValueError if not found.
    """
    # Try by email first, then by username
    for param in (f"email={urllib.parse.quote(identifier)}",
                  f"username={urllib.parse.quote(identifier)}",
                  f"search={urllib.parse.quote(identifier)}"):
        url = f"{KEYCLOAK_ADMIN_URL}/admin/realms/{KEYCLOAK_REALM}/users?{param}&max=10"
        req = urllib.request.Request(url)
        req.add_header("Authorization", f"Bearer {admin_token}")
        with urllib.request.urlopen(req, timeout=15) as resp:
            users = json.loads(resp.read())
        if users:
            return users[0]
    raise ValueError(f"No Keycloak user found matching: {identifier!r}")


def _keycloak_reset_password(admin_token, user_id, new_password):
    """Reset a Keycloak user's password. Raises RuntimeError on non-204 response."""
    url = f"{KEYCLOAK_ADMIN_URL}/admin/realms/{KEYCLOAK_REALM}/users/{user_id}/reset-password"
    payload = json.dumps({"type": "password", "value": new_password, "temporary": False}).encode()
    req = urllib.request.Request(url, data=payload, method="PUT")
    req.add_header("Authorization", f"Bearer {admin_token}")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=15) as resp:
        status = resp.status
    if status != 204:
        raise RuntimeError(f"Keycloak reset-password returned HTTP {status}")
    log.info("Keycloak password reset for user_id=%s", user_id)


def _sync_to_keycloak(username, password):
    """
    High-level helper: get admin token, find user, reset password.
    Returns dict with 'user_id' on success; raises on failure.
    """
    token = _keycloak_admin_token()
    user = _keycloak_find_user(token, username)
    user_id = user["id"]
    _keycloak_reset_password(token, user_id, password)
    return {"user_id": user_id, "username": user.get("username", ""), "email": user.get("email", "")}


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


@app.route("/sync", methods=["POST"])
def sync():
    """Force bw sync to pull latest vault state."""
    try:
        _ensure_session()

        def _do_sync():
            _run(["sync"])

        _with_reauth(_do_sync)
        return jsonify({"status": "ok"})
    except Exception as exc:
        log.error("sync failed: %s", exc)
        return jsonify({"status": "error", "error": str(exc)}), 500


@app.route("/update", methods=["POST"])
def update():
    """
    Update a Vaultwarden login item by name.
    If the item name matches KEYCLOAK_SYNC_ITEMS, also updates Keycloak atomically.

    Request body JSON:
      { "name": "<item name>", "username": "<new username>", "password": "<new password>" }

    Returns:
      { "status": "ok", "item": "<name>", "keycloak_synced": true|false }
      or  { "status": "error", "error": "..." }
    """
    data = request.get_json(force=True, silent=True) or {}
    name     = data.get("name", "").strip()
    username = data.get("username", "").strip()
    password = data.get("password", "").strip()

    if not name:
        return jsonify({"status": "error", "error": "field 'name' is required"}), 400
    if not password:
        return jsonify({"status": "error", "error": "field 'password' is required"}), 400

    try:
        _ensure_session()

        def _do_update():
            # Search for the item by name
            result = _run(["list", "items", "--search", name, "--raw"])
            items = json.loads(result.stdout)
            if not items:
                raise ValueError(f"No vault item found with name: {name!r}")

            # Pick the first exact-name match, or fall back to the first result
            item = next((i for i in items if i.get("name", "").lower() == name.lower()), items[0])
            item_id = item["id"]

            # Patch username and password fields
            if "login" not in item:
                item["login"] = {}
            if username:
                item["login"]["username"] = username
            item["login"]["password"] = password

            # Encode the updated item
            encoded_result = subprocess.run(
                ["bw", "encode"],
                input=json.dumps(item),
                capture_output=True,
                text=True,
                check=True,
            )
            encoded = encoded_result.stdout.strip()

            # Write the updated item back
            _run(["edit", "item", item_id, encoded])

            # Push the change to the server immediately
            _run(["sync"])

        _with_reauth(_do_update)

        # Atomic Keycloak sync: if this item is in the sync list, update Keycloak too
        keycloak_synced = False
        kc_detail = None
        if name.lower() in KEYCLOAK_SYNC_ITEMS and KEYCLOAK_ADMIN_URL:
            sync_username = username or name  # use provided username or item name as fallback
            try:
                kc_detail = _sync_to_keycloak(sync_username, password)
                keycloak_synced = True
                log.info("Atomic Keycloak sync completed for item %r (user: %s)", name, sync_username)
            except Exception as kc_exc:
                log.error("Vaultwarden updated but Keycloak sync failed for %r: %s", name, kc_exc)
                return jsonify({
                    "status": "partial",
                    "item": name,
                    "keycloak_synced": False,
                    "keycloak_error": str(kc_exc),
                    "warning": "Vaultwarden updated but Keycloak was NOT updated — credentials are out of sync",
                }), 207

        resp = {"status": "ok", "item": name, "keycloak_synced": keycloak_synced}
        if kc_detail:
            resp["keycloak_user"] = kc_detail
        return jsonify(resp)

    except ValueError as exc:
        log.warning("update rejected: %s", exc)
        return jsonify({"status": "error", "error": str(exc)}), 404
    except Exception as exc:
        log.error("update failed: %s", exc)
        return jsonify({"status": "error", "error": str(exc)}), 500


@app.route("/update-keycloak", methods=["POST"])
def update_keycloak():
    """
    Update a user's password in Keycloak directly (without touching Vaultwarden).
    Useful for one-off fixes or when called independently.

    Request body JSON:
      { "username": "<email or username>", "password": "<new password>" }

    Returns:
      { "status": "ok", "keycloak_user": { "user_id": "...", "email": "..." } }
      or { "status": "error", "error": "..." }
    """
    data = request.get_json(force=True, silent=True) or {}
    username = data.get("username", "").strip()
    password = data.get("password", "").strip()

    if not username:
        return jsonify({"status": "error", "error": "field 'username' is required"}), 400
    if not password:
        return jsonify({"status": "error", "error": "field 'password' is required"}), 400

    if not KEYCLOAK_ADMIN_URL or not KEYCLOAK_ADMIN_PASS:
        return jsonify({
            "status": "error",
            "error": "KEYCLOAK_ADMIN_URL and KEYCLOAK_ADMIN_PASS are not configured",
        }), 503

    try:
        kc_detail = _sync_to_keycloak(username, password)
        log.info("/update-keycloak: password updated for %s", username)
        return jsonify({"status": "ok", "keycloak_user": kc_detail})
    except ValueError as exc:
        log.warning("update-keycloak rejected: %s", exc)
        return jsonify({"status": "error", "error": str(exc)}), 404
    except Exception as exc:
        log.error("update-keycloak failed: %s", exc)
        return jsonify({"status": "error", "error": str(exc)}), 500


@app.route("/sync-all-keycloak", methods=["POST"])
def sync_all_keycloak():
    """
    Sync ALL user-credentials vault items to Keycloak.
    Pulls latest vault state first, then resets Keycloak passwords for all matched users.
    Returns {"status":"ok","synced":[...],"skipped":[...],"errors":[...]}
    """
    if not KEYCLOAK_ADMIN_URL or not KEYCLOAK_ADMIN_PASS:
        return jsonify({"status": "error", "error": "Keycloak not configured"}), 503
    try:
        def _do():
            _run(["sync"])
            items_json = _run(["list", "items"]).stdout
            return json.loads(items_json)
        items = _with_reauth(_do)
    except Exception as exc:
        log.error("sync-all-keycloak: vault sync failed: %s", exc)
        return jsonify({"status": "error", "error": str(exc)}), 500

    synced, skipped, errors = [], [], []
    for item in items:
        fields = item.get("fields") or []
        is_user_cred = any(
            f.get("name") == "collection" and f.get("value") == "user-credentials"
            for f in fields
        )
        if not is_user_cred:
            continue
        name = item.get("name", "")
        login = item.get("login") or {}
        username = login.get("username", "")
        password = login.get("password", "")
        if not username or not password:
            skipped.append({"name": name, "reason": "no username or password"})
            continue
        try:
            kc = _sync_to_keycloak(username, password)
            synced.append({"name": name, "user_id": kc.get("user_id"), "username": kc.get("username")})
            log.info("sync-all-keycloak: synced %s → Keycloak user %s", name, kc.get("username"))
        except ValueError as exc:
            skipped.append({"name": name, "reason": str(exc)})
        except Exception as exc:
            errors.append({"name": name, "error": str(exc)})
            log.error("sync-all-keycloak: failed for %s: %s", name, exc)

    return jsonify({"status": "ok", "synced": synced, "skipped": skipped, "errors": errors})


# ── Background Keycloak sync watcher ─────────────────────────────────────────

_last_kc_hash: str = ""


def _keycloak_watcher():
    """
    Background thread: polls Vaultwarden every KEYCLOAK_SYNC_INTERVAL seconds.
    Syncs user-credentials to Keycloak when a change is detected.
    """
    global _last_kc_hash
    import time
    log.info("Keycloak watcher started (interval=%ds)", KEYCLOAK_SYNC_INTERVAL)
    while True:
        time.sleep(KEYCLOAK_SYNC_INTERVAL)
        if not KEYCLOAK_ADMIN_URL or not KEYCLOAK_ADMIN_PASS:
            continue
        try:
            def _poll():
                _run(["sync"])
                return json.loads(_run(["list", "items"]).stdout)
            items = _with_reauth(_poll)
            current_hash = _credential_hash(items)
            if current_hash == _last_kc_hash:
                log.debug("Keycloak watcher: no credential changes detected")
                continue
            log.info("Keycloak watcher: credential change detected, syncing to Keycloak")
            _last_kc_hash = current_hash
            # Reuse sync-all logic inline
            for item in items:
                fields = item.get("fields") or []
                if not any(f.get("name") == "collection" and f.get("value") == "user-credentials" for f in fields):
                    continue
                login = item.get("login") or {}
                username = login.get("username", "")
                password = login.get("password", "")
                if not username or not password:
                    continue
                try:
                    _sync_to_keycloak(username, password)
                    log.info("Keycloak watcher: synced %s", username)
                except Exception as exc:
                    log.warning("Keycloak watcher: skipped %s: %s", username, exc)
        except Exception as exc:
            log.error("Keycloak watcher: error during poll cycle: %s", exc)


if KEYCLOAK_SYNC_INTERVAL > 0 and KEYCLOAK_ADMIN_URL:
    import threading
    _watcher_thread = threading.Thread(target=_keycloak_watcher, daemon=True, name="keycloak-watcher")
    _watcher_thread.start()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8777)
