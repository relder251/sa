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
import time

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


def _unlock(env: dict) -> str:
    """Run bw unlock and return the session token, or '' on failure."""
    result = subprocess.run(
        ["bw", "unlock", "--passwordenv", "BW_MASTER_PASS"],
        capture_output=True, text=True,
        env={**env, "BW_MASTER_PASS": BW_MASTER_PASS},
        check=False,
    )
    # --raw outputs the token only in TTY mode; parse the token from the message.
    match = re.search(r'BW_SESSION="([^"]+)"', result.stdout)
    if not match:
        match = re.search(r'--session\s+(\S+)', result.stdout)
    token = match.group(1) if match else ""
    if not token:
        log.debug("bw unlock stdout=%r stderr=%r", result.stdout[:200], result.stderr[:200])
    return token


def _authenticate() -> None:
    """Configure server, log in with API key, unlock with master password.

    Strategy (fast path first):
      1. Try bw unlock directly — works when bw is logged in but session expired.
      2. If unlock says "not logged in", do the full logout → config → login → unlock.
    """
    global _session

    env = os.environ.copy()
    env.update({
        "BW_SERVER":       BW_SERVER,
        "BW_CLIENTID":     BW_CLIENTID,
        "BW_CLIENTSECRET": BW_CLIENTSECRET,
    })

    # Fast path: bw may already be logged in (session expired but login state intact).
    log.info("Attempting fast unlock (session may have expired)...")
    token = _unlock(env)
    if token:
        _session = token
        log.info("Vault re-unlocked (fast path); session refreshed.")
        log.info("Syncing vault...")
        _run(["sync"])
        log.info("Vault sync complete.")
        return

    # Slow path: bw login state is gone — full re-auth.
    log.info("Fast unlock failed; performing full re-auth (logout → login → unlock)...")

    # Always start from a clean state.  bw refuses to change the server config
    # while logged in, and the container may have residual state from a previous
    # session or from env-var-based auto-auth.
    subprocess.run(["bw", "logout"], capture_output=True, env=env, check=False)

    log.info("Configuring bw server: %s", BW_SERVER)
    subprocess.run(["bw", "config", "server", BW_SERVER], capture_output=True, env=env)

    for attempt in range(1, 4):
        log.info("Logging in with API key (attempt %d/3)...", attempt)
        login_result = subprocess.run(
            ["bw", "login", "--apikey"],
            capture_output=True, text=True, env=env, check=False,
        )
        if login_result.returncode != 0:
            if "already logged in" not in (login_result.stdout + login_result.stderr).lower():
                log.warning("bw login attempt %d returned %d: stdout=%r stderr=%r",
                            attempt, login_result.returncode,
                            login_result.stdout[:200], login_result.stderr[:200])

        # Allow the CLI state to settle before unlock
        time.sleep(3)

        log.info("Unlocking vault (attempt %d/3)...", attempt)
        token = _unlock(env)
        if token:
            break
        log.warning("Unlock attempt %d failed; retrying login...", attempt)
        subprocess.run(["bw", "logout"], capture_output=True, env=env, check=False)

    if not token:
        raise RuntimeError(
            "bw unlock failed after 3 login attempts — "
            "check BW_MASTER_PASS, BW_CLIENTID, BW_CLIENTSECRET and server connectivity."
        )

    _session = token
    log.info("Vault unlocked; session cached.")

    log.info("Syncing vault...")
    try:
        _run(["sync"])
        log.info("Vault sync complete.")
    except subprocess.CalledProcessError as exc:
        log.warning("bw sync failed (non-fatal): %s", exc)


def _ensure_session() -> None:
    """Authenticate if no session token is cached."""
    if _session:
        return
    _authenticate()  # _authenticate() has its own 3-attempt retry internally


def _with_reauth(fn):
    """Call fn(); on failure, re-authenticate once and retry."""
    global _session
    try:
        return fn()
    except (subprocess.CalledProcessError, OSError) as exc:
        log.warning("bw command failed (%s); re-authenticating...", exc)
        _session = None
        _ensure_session()   # use retry logic instead of bare _authenticate()
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


def update_item(name: str, username: str | None, password: str,
                collection: str | None = None, service_tags: list[str] | None = None) -> dict:
    """
    Update a vault login item's credentials and optionally its collection/service_tags.
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

        # Update collection/service_tags custom fields if provided
        if collection is not None or service_tags is not None:
            existing = [f for f in (item.get("fields") or [])
                        if f.get("name") not in ("collection", "service_tags")]
            if collection:
                existing.append({"name": "collection", "value": collection, "type": 0})
            if service_tags is not None:
                existing.append({"name": "service_tags", "value": ",".join(service_tags), "type": 0})
            item["fields"] = existing

        encoded = subprocess.run(
            ["bw", "encode"],
            input=json.dumps(item),
            capture_output=True, text=True, check=True,
        ).stdout.strip()

        _run(["edit", "item", item_id, encoded])
        _run(["sync"])
        return item

    return _with_reauth(_do)


def tag_item(name: str, collection: str, service_tags: list[str] | None = None) -> dict:
    """
    Tag an existing vault item with a collection and optional service_tags.
    Does not change credentials.  Returns the updated item dict.
    """
    _ensure_session()

    def _do():
        item = get_item(name)
        item_id = item["id"]

        # Preserve non-taxonomy fields, replace taxonomy ones
        existing = [f for f in (item.get("fields") or [])
                    if f.get("name") not in ("collection", "service_tags")]
        existing.append({"name": "collection", "value": collection, "type": 0})
        if service_tags:
            existing.append({"name": "service_tags", "value": ",".join(service_tags), "type": 0})
        item["fields"] = existing

        encoded = subprocess.run(
            ["bw", "encode"],
            input=json.dumps(item),
            capture_output=True, text=True, check=True,
        ).stdout.strip()

        _run(["edit", "item", item_id, encoded])
        _run(["sync"])
        return item

    return _with_reauth(_do)


def create_item(name: str, username: str | None, password: str,
                notes: str | None = None,
                collection: str | None = None,
                service_tags: list[str] | None = None) -> dict:
    """Create a new login item in the vault with optional taxonomy tags."""
    _ensure_session()

    def _do():
        new_item: dict = {
            "type": 1,  # Login
            "name": name,
            "login": {
                "username": username or "",
                "password": password,
            },
        }
        if notes:
            new_item["notes"] = notes
        fields = []
        if collection:
            fields.append({"name": "collection", "value": collection, "type": 0})
        if service_tags:
            fields.append({"name": "service_tags", "value": ",".join(service_tags), "type": 0})
        if fields:
            new_item["fields"] = fields

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


def tag_items_batch(taxonomy: dict) -> dict:
    """
    Tag multiple vault items with collection + service_tags in a single session.
    Syncs only once at the end.  taxonomy = {item_name: (collection, service_tags)}.
    Returns {"tagged": [...], "skipped": [...], "errors": [...]}.
    """
    _ensure_session()
    tagged, skipped, errors = [], [], []

    def _do():
        all_items = list_items()
        item_map = {i["name"]: i for i in all_items}

        for item_name, (collection, service_tags) in taxonomy.items():
            item = item_map.get(item_name)
            if not item:
                skipped.append(item_name)
                continue
            try:
                item_id = item["id"]
                existing = [f for f in (item.get("fields") or [])
                            if f.get("name") not in ("collection", "service_tags")]
                existing.append({"name": "collection", "value": collection, "type": 0})
                if service_tags:
                    existing.append({"name": "service_tags",
                                     "value": ",".join(service_tags), "type": 0})
                item["fields"] = existing

                encoded = subprocess.run(
                    ["bw", "encode"],
                    input=json.dumps(item),
                    capture_output=True, text=True, check=True,
                ).stdout.strip()
                _run(["edit", "item", item_id, encoded])
                tagged.append(item_name)
            except Exception as exc:
                errors.append({"item": item_name, "error": str(exc)})

        if tagged:
            _run(["sync"])

    _with_reauth(_do)
    return {"tagged": tagged, "skipped": skipped, "errors": errors}


def list_by_collection(collection: str) -> list[dict]:
    """Return all vault items tagged with the given collection custom field."""
    items = list_items()
    result = []
    for item in items:
        fields = {f["name"]: f["value"] for f in (item.get("fields") or [])}
        if fields.get("collection") == collection:
            result.append(item)
    return result
