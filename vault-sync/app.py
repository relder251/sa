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
"""

import json
import os
import subprocess
import logging
import urllib.request
import urllib.parse
from flask import Flask, request, jsonify

app = Flask(__name__)
logging.basicConfig(level=logging.DEBUG, format="%(asctime)s %(levelname)s %(message)s")
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


def _run(args, input_text=None, check=True):
    """Run a bw CLI command, injecting the current session token when available."""
    env = os.environ.copy()
    env["BW_SERVER"] = BW_SERVER
    if _BW_SESSION:
        env["BW_SESSION"] = _BW_SESSION

    log.debug("_run %s session_len=%d", args[0:2], len(_BW_SESSION or ""))
    result = subprocess.run(
        ["bw"] + args,
        input=input_text,
        capture_output=True,
        text=True,
        env=env,
        check=check,
    )
    log.debug("_run %s rc=%d stdout_len=%d stderr=%s",
              args[0:2], result.returncode, len(result.stdout), result.stderr[:80])
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

    # Attempt unlock without a full re-login first. bw login --apikey deletes
    # its own data.json internally in CLI 2026.x and then fails to re-initialise,
    # producing an empty session token. By trying unlock first we avoid that bug
    # when a valid login state already exists.
    log.info("Attempting vault unlock...")
    result = subprocess.run(
        ["bw", "unlock", "--passwordenv", "BW_MASTER_PASS", "--raw"],
        capture_output=True,
        text=True,
        env={**env, "BW_MASTER_PASS": BW_MASTER_PASS},
        check=False,
    )
    session = result.stdout.strip()

    if not session:
        # Unlock failed — not logged in yet. Clear state and do a full login.
        log.info("Unlock returned empty session; performing full API-key login...")
        data_json = os.path.expanduser("~/.config/Bitwarden CLI/data.json")
        if os.path.exists(data_json):
            os.remove(data_json)
            log.info("Cleared stale bw data.json")
        subprocess.run(["bw", "config", "server", BW_SERVER], capture_output=True, env=env)
        subprocess.run(
            ["bw", "login", "--apikey"],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        result = subprocess.run(
            ["bw", "unlock", "--passwordenv", "BW_MASTER_PASS", "--raw"],
            capture_output=True,
            text=True,
            env={**env, "BW_MASTER_PASS": BW_MASTER_PASS},
            check=True,
        )
        session = result.stdout.strip()

    _BW_SESSION = session
    log.info("Vault unlocked; session token cached.")

    log.info("Syncing vault to populate local cache...")
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


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8777)
