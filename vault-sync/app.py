"""
vault-sync/app.py
Flask HTTP API wrapping the Bitwarden CLI (bw) for programmatic Vaultwarden updates.

Environment variables required:
  BW_SERVER       — Vaultwarden URL, e.g. https://vault.private.sovereignadvisory.ai
  BW_CLIENTID     — Vaultwarden Settings → My Account → API Key → client_id
  BW_CLIENTSECRET — Vaultwarden Settings → My Account → API Key → client_secret
  BW_MASTER_PASS  — vault master password (used to unlock after API-key login)
"""

import json
import os
import subprocess
import logging
from flask import Flask, request, jsonify

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# Module-level session token cache; None means we need to (re-)authenticate.
_BW_SESSION = None

BW_SERVER      = os.environ.get("BW_SERVER", "https://vault.private.sovereignadvisory.ai")
BW_CLIENTID    = os.environ.get("BW_CLIENTID", "")
BW_CLIENTSECRET = os.environ.get("BW_CLIENTSECRET", "")
BW_MASTER_PASS = os.environ.get("BW_MASTER_PASS", "")


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

    log.info("Configuring bw server: %s", BW_SERVER)
    _run(["config", "server", BW_SERVER])

    log.info("Logging in with API key...")
    env = os.environ.copy()
    env["BW_SERVER"] = BW_SERVER
    env["BW_CLIENTID"] = BW_CLIENTID
    env["BW_CLIENTSECRET"] = BW_CLIENTSECRET
    subprocess.run(
        ["bw", "login", "--apikey"],
        capture_output=True,
        text=True,
        env=env,
        check=False,  # may fail if already logged in; that is fine
    )

    log.info("Unlocking vault...")
    result = subprocess.run(
        ["bw", "unlock", "--passwordenv", "BW_MASTER_PASS", "--raw"],
        capture_output=True,
        text=True,
        env={**env, "BW_MASTER_PASS": BW_MASTER_PASS},
        check=True,
    )
    _BW_SESSION = result.stdout.strip()
    log.info("Vault unlocked; session token cached.")


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

    Request body JSON:
      { "name": "<item name>", "username": "<new username>", "password": "<new password>" }

    Returns:
      { "status": "ok", "item": "<name>" }  or  { "status": "error", "error": "..." }
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
        return jsonify({"status": "ok", "item": name})

    except ValueError as exc:
        log.warning("update rejected: %s", exc)
        return jsonify({"status": "error", "error": str(exc)}), 404
    except Exception as exc:
        log.error("update failed: %s", exc)
        return jsonify({"status": "error", "error": str(exc)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8777)
