"""
vault-sync/app/vault.py
Thin wrapper around the bw CLI for Vaultwarden cipher (Password Manager) operations.

NOTE on bitwarden-sdk:
  bitwarden-sdk 2.0.0 covers Secrets Manager (Projects/Secrets) only — it has no
  API for Password Manager ciphers (login items, collections).  We use the bw CLI
  standalone binary here until the SDK gains cipher support.  The standalone binary
  is downloaded at image build time; Node.js is not required.

NOTE on bw unlock --raw:
  The --raw flag works in TTY mode only.  In headless Docker (no TTY) the standalone
  binary returns an empty string.  We use the standard unlock output and extract the
  session token via regex instead.
"""

import json
import logging
import os
import re
import subprocess

log = logging.getLogger(__name__)

BW_SERVER       = os.environ.get("BW_SERVER", "")
BW_CLIENTID     = os.environ.get("BW_CLIENTID", "")
BW_CLIENTSECRET = os.environ.get("BW_CLIENTSECRET", "")
BW_MASTER_PASS  = os.environ.get("BW_MASTER_PASS", "")

# Module-level session cache; None = not yet authenticated.
_session: str | None = None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _run(args: list[str], input_text: str | None = None, check: bool = True) -> subprocess.CompletedProcess:
    """Run a bw CLI command with the current session token injected."""
    env = os.environ.copy()
    env["BW_SERVER"] = BW_SERVER
    if _session:
        env["BW_SESSION"] = _session
    return subprocess.run(
        ["bw"] + args,
        input=input_text,
        capture_output=True,
        text=True,
        env=env,
        check=check,
    )


def _authenticate() -> None:
    """Configure server, log in with API key, unlock with master password."""
    global _session

    env = os.environ.copy()
    env.update({
        "BW_SERVER":       BW_SERVER,
        "BW_CLIENTID":     BW_CLIENTID,
        "BW_CLIENTSECRET": BW_CLIENTSECRET,
    })

    log.info("Configuring bw server: %s", BW_SERVER)
    subprocess.run(["bw", "config", "server", BW_SERVER], capture_output=True, env=env)

    log.info("Logging in with API key...")
    subprocess.run(
        ["bw", "login", "--apikey"],
        capture_output=True, text=True, env=env, check=False,
    )

    log.info("Unlocking vault...")
    result = subprocess.run(
        ["bw", "unlock", "--passwordenv", "BW_MASTER_PASS"],
        capture_output=True, text=True,
        env={**env, "BW_MASTER_PASS": BW_MASTER_PASS},
        check=False,
    )
    # --raw outputs the session token only in TTY mode; the standalone binary returns
    # empty in headless Docker.  Parse the token from the unlock message instead.
    match = re.search(r'BW_SESSION="([^"]+)"', result.stdout)
    if not match:
        match = re.search(r'--session\s+(\S+)', result.stdout)
    session = match.group(1) if match else ""
    if not session:
        raise RuntimeError(
            f"bw unlock failed to return a session token — "
            f"check BW_MASTER_PASS and server connectivity.\n"
            f"stdout: {result.stdout[:200]!r}\nstderr: {result.stderr[:200]!r}"
        )

    _session = session
    log.info("Vault unlocked; session cached.")

    log.info("Syncing vault...")
    _run(["sync"])
    log.info("Vault sync complete.")


def _ensure_session() -> None:
    if not _session:
        _authenticate()


def _with_reauth(fn):
    """Call fn(); on failure, re-authenticate once and retry."""
    global _session
    try:
        return fn()
    except (subprocess.CalledProcessError, OSError) as exc:
        log.warning("bw command failed (%s); re-authenticating...", exc)
        _session = None
        _authenticate()
        return fn()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def status() -> dict:
    """Return bw status (locked/unlocked, server URL)."""
    _ensure_session()
    result = _run(["status", "--raw"], check=False)
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"raw": result.stdout.strip(), "stderr": result.stderr.strip()}


def sync() -> None:
    """Force a vault sync from the server."""
    _ensure_session()
    _with_reauth(lambda: _run(["sync"]))


def get_item(name: str) -> dict:
    """
    Find a vault item by name.  Returns the first exact-name match (case-insensitive),
    falling back to the first search result.  Raises ValueError if not found.
    """
    _ensure_session()

    def _do():
        result = _run(["list", "items", "--search", name, "--raw"])
        items = json.loads(result.stdout)
        if not items:
            raise ValueError(f"No vault item found with name: {name!r}")
        return next(
            (i for i in items if i.get("name", "").lower() == name.lower()),
            items[0],
        )

    return _with_reauth(_do)


def update_item(name: str, username: str | None, password: str) -> dict:
    """
    Update the username and/or password of a vault login item.
    Returns the updated item dict.
    """
    _ensure_session()

    def _do():
        item = get_item(name)
        item_id = item["id"]

        if "login" not in item:
            item["login"] = {}
        if username:
            item["login"]["username"] = username
        item["login"]["password"] = password

        encoded = subprocess.run(
            ["bw", "encode"],
            input=json.dumps(item),
            capture_output=True, text=True, check=True,
        ).stdout.strip()

        _run(["edit", "item", item_id, encoded])
        _run(["sync"])
        return item

    return _with_reauth(_do)


def create_item(name: str, username: str | None, password: str, notes: str | None = None) -> dict:
    """Create a new login item in the vault."""
    _ensure_session()

    def _do():
        new_item = {
            "type": 1,  # Login
            "name": name,
            "login": {
                "username": username or "",
                "password": password,
            },
        }
        if notes:
            new_item["notes"] = notes

        encoded = subprocess.run(
            ["bw", "encode"],
            input=json.dumps(new_item),
            capture_output=True, text=True, check=True,
        ).stdout.strip()

        result = _run(["create", "item", encoded])
        return json.loads(result.stdout)

    return _with_reauth(_do)


def delete_item(name: str) -> None:
    """Permanently delete a vault item by name."""
    _ensure_session()

    def _do():
        item = get_item(name)
        _run(["delete", "item", item["id"], "--permanent"])
        _run(["sync"])

    _with_reauth(_do)


def list_items(search: str | None = None) -> list[dict]:
    """List vault items, optionally filtered by a search term."""
    _ensure_session()

    def _do():
        args = ["list", "items", "--raw"]
        if search:
            args += ["--search", search]
        result = _run(args)
        return json.loads(result.stdout)

    return _with_reauth(_do)
