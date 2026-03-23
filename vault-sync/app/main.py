"""
vault-sync/app/main.py
FastAPI credential management service.

Bootstrap environment variables (kept in .env — the only on-disk secrets):
  BW_SERVER       — Vaultwarden URL
  BW_CLIENTID     — Vaultwarden API key client_id
  BW_CLIENTSECRET — Vaultwarden API key client_secret
  BW_MASTER_PASS  — vault master password

Optional Keycloak sync (enables /update-keycloak and atomic sync on /update):
  KEYCLOAK_ADMIN_URL      — e.g. https://kc.sovereignadvisory.ai
  KEYCLOAK_ADMIN_USER     — default: admin
  KEYCLOAK_ADMIN_PASS     — Keycloak admin password
  KEYCLOAK_REALM          — default: agentic-sdlc
  KEYCLOAK_SYNC_ITEMS     — comma-separated vault item names to auto-sync
  KEYCLOAK_SYNC_INTERVAL  — polling interval in seconds for background watcher
                            (default: 300, 0 = disabled)
"""

import hashlib
import logging
import os
import threading
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import shlex

import vault
import keycloak as kc
from models import VALID_COLLECTIONS, ITEM_TAXONOMY, item_to_cred
from registry import SERVICE_REGISTRY, VALID_SERVICES
from adapters import ADAPTERS, ROTATABLE_SERVICES

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Sentry / GlitchTip (optional)
# ---------------------------------------------------------------------------
KEYCLOAK_SYNC_INTERVAL = int(os.environ.get("KEYCLOAK_SYNC_INTERVAL", "300"))
_last_kc_hash: str = ""


def _credential_hash(items: list) -> str:
    """Stable hash of all user-credentials vault items (username + password)."""
    from models import FIELD_COLLECTION
    parts = []
    for item in items:
        fields = item.get("fields") or []
        if not any(f.get("name") == FIELD_COLLECTION and f.get("value") == "user-credentials" for f in fields):
            continue
        login = item.get("login") or {}
        parts.append(f"{item.get('name', '')}:{login.get('username', '')}:{login.get('password', '')}")
    parts.sort()
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()


def _keycloak_watcher():
    """Background thread: polls vault every KEYCLOAK_SYNC_INTERVAL seconds.
    Syncs user-credentials to Keycloak only when a change is detected."""
    global _last_kc_hash
    log.info("Keycloak watcher started (interval=%ds)", KEYCLOAK_SYNC_INTERVAL)
    while True:
        time.sleep(KEYCLOAK_SYNC_INTERVAL)
        if not kc.KEYCLOAK_ADMIN_URL or not kc.KEYCLOAK_ADMIN_PASS:
            continue
        try:
            vault.sync()
            items = vault.list_items()
            current_hash = _credential_hash(items)
            if current_hash == _last_kc_hash:
                log.debug("Keycloak watcher: no credential changes")
                continue
            log.info("Keycloak watcher: credential change detected, syncing")
            _last_kc_hash = current_hash
            result = kc.sync_all(items)
            log.info(
                "Keycloak watcher: synced=%d skipped=%d errors=%d",
                len(result["synced"]), len(result["skipped"]), len(result["errors"]),
            )
        except Exception as exc:
            log.error("Keycloak watcher: poll cycle error: %s", exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    if KEYCLOAK_SYNC_INTERVAL > 0 and kc.KEYCLOAK_ADMIN_URL:
        t = threading.Thread(target=_keycloak_watcher, daemon=True, name="keycloak-watcher")
        t.start()
    yield
_SENTRY_DSN = os.environ.get("SENTRY_DSN", "")
if _SENTRY_DSN:
    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        sentry_sdk.init(
            dsn=_SENTRY_DSN,
            integrations=[FastApiIntegration()],
            traces_sample_rate=0.2,
            environment=os.environ.get("ENVIRONMENT", "production"),
        )
        log.info("Sentry SDK initialised (GlitchTip)")
    except ImportError:
        log.warning("sentry-sdk not installed; error monitoring disabled")

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(title="vault-sync", version="2.0.0", lifespan=lifespan)


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------
class UpdateRequest(BaseModel):
    name: str
    username: str = ""
    password: str


class UpdateKeycloakRequest(BaseModel):
    username: str
    password: str


class CreateRequest(BaseModel):
    name: str
    username: str = ""
    password: str
    notes: str = ""


class DeleteRequest(BaseModel):
    name: str


class CredCreateRequest(BaseModel):
    name: str
    username: str = ""
    password: str
    notes: str = ""
    service_tags: list[str] = []


class CredUpdateRequest(BaseModel):
    username: str = ""
    password: str
    service_tags: list[str] = []


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/status")
def status():
    try:
        data = vault.status()
        return {"status": "ok", "vault": data}
    except Exception as exc:
        log.error("status failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/sync")
def sync():
    try:
        vault.sync()
        return {"status": "ok"}
    except Exception as exc:
        log.error("sync failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/update")
def update(req: UpdateRequest):
    """
    Update a Vaultwarden login item by name.
    If the item name matches KEYCLOAK_SYNC_ITEMS, also updates Keycloak atomically.
    """
    if not req.name:
        raise HTTPException(status_code=400, detail="field 'name' is required")
    if not req.password:
        raise HTTPException(status_code=400, detail="field 'password' is required")

    try:
        vault.update_item(req.name, req.username or None, req.password)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        log.error("update failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))

    # Atomic Keycloak sync
    keycloak_synced = False
    kc_detail = None
    if req.name.lower() in kc.KEYCLOAK_SYNC_ITEMS and kc.KEYCLOAK_ADMIN_URL:
        sync_user = req.username or req.name
        try:
            kc_detail = kc.sync_password(sync_user, req.password)
            keycloak_synced = True
            log.info("Atomic Keycloak sync completed for %r (user: %s)", req.name, sync_user)
        except Exception as kc_exc:
            log.error("Vaultwarden updated but Keycloak sync failed for %r: %s", req.name, kc_exc)
            resp = {
                "status":          "partial",
                "item":            req.name,
                "keycloak_synced": False,
                "keycloak_error":  str(kc_exc),
                "warning":         "Vaultwarden updated but Keycloak was NOT updated — credentials are out of sync",
            }
            return JSONResponse(content=resp, status_code=207)

    resp = {"status": "ok", "item": req.name, "keycloak_synced": keycloak_synced}
    if kc_detail:
        resp["keycloak_user"] = kc_detail
    return resp


@app.post("/update-keycloak")
def update_keycloak(req: UpdateKeycloakRequest):
    """Update a Keycloak user's password directly (without touching Vaultwarden)."""
    if not req.username:
        raise HTTPException(status_code=400, detail="field 'username' is required")
    if not req.password:
        raise HTTPException(status_code=400, detail="field 'password' is required")
    if not kc.KEYCLOAK_ADMIN_URL or not kc.KEYCLOAK_ADMIN_PASS:
        raise HTTPException(
            status_code=503,
            detail="KEYCLOAK_ADMIN_URL and KEYCLOAK_ADMIN_PASS are not configured",
        )
    try:
        kc_detail = kc.sync_password(req.username, req.password)
        log.info("/update-keycloak: password updated for %s", req.username)
        return {"status": "ok", "keycloak_user": kc_detail}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        log.error("update-keycloak failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/create")
def create(req: CreateRequest):
    """Create a new vault login item."""
    if not req.name:
        raise HTTPException(status_code=400, detail="field 'name' is required")
    if not req.password:
        raise HTTPException(status_code=400, detail="field 'password' is required")
    try:
        item = vault.create_item(req.name, req.username or None, req.password, req.notes or None)
        return {"status": "ok", "item": item.get("name"), "id": item.get("id")}
    except Exception as exc:
        log.error("create failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.delete("/delete")
def delete(req: DeleteRequest):
    """Permanently delete a vault item by name."""
    if not req.name:
        raise HTTPException(status_code=400, detail="field 'name' is required")
    try:
        vault.delete_item(req.name)
        return {"status": "ok", "item": req.name}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        log.error("delete failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/items")
def list_items(search: str = ""):
    """List vault items, optionally filtered by search term."""
    try:
        items = vault.list_items(search or None)
        return {"status": "ok", "count": len(items), "items": [
            {"id": i.get("id"), "name": i.get("name")} for i in items
        ]}
    except Exception as exc:
        log.error("list_items failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


# ---------------------------------------------------------------------------
# Credential taxonomy endpoints (CRED-02)
# ---------------------------------------------------------------------------

def _validate_collection(collection: str) -> None:
    if collection not in VALID_COLLECTIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid collection {collection!r}. Valid: {sorted(VALID_COLLECTIONS)}",
        )


@app.post("/credentials/migrate")
def migrate_taxonomy():
    """
    Tag all known vault items with their collection and service_tags
    per the ITEM_TAXONOMY map in models.py.  Safe to run multiple times.
    Performs a single vault sync at the end for efficiency.
    """
    try:
        results = vault.tag_items_batch(ITEM_TAXONOMY)
        log.info("Migration complete: %d tagged, %d skipped, %d errors",
                 len(results["tagged"]), len(results["skipped"]), len(results["errors"]))
        return {"status": "ok", **results}
    except Exception as exc:
        log.error("migrate_taxonomy failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/credentials/{collection}")
def list_credentials(collection: str):
    """List all vault items tagged with the given collection tier."""
    _validate_collection(collection)
    try:
        items = vault.list_by_collection(collection)
        creds = [item_to_cred(i) for i in items]
        return {
            "status": "ok",
            "collection": collection,
            "count": len(creds),
            "items": [
                {"id": c.vault_id, "name": c.name, "service_tags": c.service_tags}
                for c in creds
            ],
        }
    except Exception as exc:
        log.error("list_credentials(%s) failed: %s", collection, exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/credentials/{collection}")
def create_credential(collection: str, req: CredCreateRequest):
    """Create a new vault item tagged with the given collection tier."""
    _validate_collection(collection)
    try:
        item = vault.create_item(
            req.name,
            req.username or None,
            req.password,
            notes=req.notes or None,
            collection=collection,
            service_tags=req.service_tags or None,
        )
        return {"status": "ok", "collection": collection, "item": item.get("name"), "id": item.get("id")}
    except Exception as exc:
        log.error("create_credential(%s) failed: %s", collection, exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.put("/credentials/{collection}/{name}")
def update_credential(collection: str, name: str, req: CredUpdateRequest):
    """Update credentials for a vault item in the given collection."""
    _validate_collection(collection)
    try:
        item = vault.update_item(
            name,
            req.username or None,
            req.password,
            collection=collection,
            service_tags=req.service_tags or None,
        )
        return {"status": "ok", "collection": collection, "item": item.get("name")}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        log.error("update_credential(%s/%s) failed: %s", collection, name, exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.delete("/credentials/{collection}/{name}")
def delete_credential(collection: str, name: str):
    """Delete a vault item from the given collection."""
    _validate_collection(collection)
    try:
        vault.delete_item(name)
        return {"status": "ok", "collection": collection, "item": name}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        log.error("delete_credential(%s/%s) failed: %s", collection, name, exc)
        raise HTTPException(status_code=500, detail=str(exc))


# ---------------------------------------------------------------------------
# CRED-03 — Keycloak sync adapter endpoints
# ---------------------------------------------------------------------------

@app.get("/drift/keycloak")
def drift_keycloak():
    """
    Compare vault user-credentials items against live Keycloak users.
    Returns matched, vault-only, and Keycloak-only user sets.
    """
    if not kc.KEYCLOAK_ADMIN_URL or not kc.KEYCLOAK_ADMIN_PASS:
        raise HTTPException(
            status_code=503,
            detail="KEYCLOAK_ADMIN_URL and KEYCLOAK_ADMIN_PASS are not configured",
        )
    try:
        items = vault.list_items()
        report = kc.drift_report(items)
        drifted = len(report["vault_only"]) + len(report["keycloak_only"])
        return {
            "status": "ok",
            "drifted": drifted,
            **report,
        }
    except Exception as exc:
        log.error("drift_keycloak failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/sync/keycloak")
def sync_keycloak():
    """
    Push vault user-credential passwords to all matched Keycloak users.
    Returns counts of synced, skipped, and errored items.
    """
    if not kc.KEYCLOAK_ADMIN_URL or not kc.KEYCLOAK_ADMIN_PASS:
        raise HTTPException(
            status_code=503,
            detail="KEYCLOAK_ADMIN_URL and KEYCLOAK_ADMIN_PASS are not configured",
        )
    try:
        items = vault.list_items()
        result = kc.sync_all(items)
        log.info(
            "Keycloak sync complete: %d synced, %d skipped, %d errors",
            len(result["synced"]), len(result["skipped"]), len(result["errors"]),
        )
        return {"status": "ok", **result}
    except Exception as exc:
        log.error("sync_keycloak failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


# ---------------------------------------------------------------------------
# CRED-04 — Credential injection endpoint
# ---------------------------------------------------------------------------

def _extract_field(item: dict, field: str) -> str:
    """Extract password, username, or notes from a raw vault item dict."""
    login = item.get("login") or {}
    if field == "password":
        return login.get("password") or ""
    if field == "username":
        return login.get("username") or ""
    if field == "notes":
        return item.get("notes") or ""
    return ""


def _resolve_service(service: str) -> dict[str, str]:
    """
    Fetch all credentials for a service from the vault.
    Returns {ENV_VAR: value, ...}.  Raises ValueError if service unknown.
    """
    mappings = SERVICE_REGISTRY.get(service)
    if mappings is None:
        raise ValueError(
            f"Unknown service {service!r}. Valid: {sorted(VALID_SERVICES)}"
        )

    # Batch-fetch only the vault items we need (de-duplicated)
    needed_items = {m["vault_item"] for m in mappings}
    item_cache: dict[str, dict] = {}
    for name in needed_items:
        try:
            item_cache[name] = vault.get_item(name)
        except ValueError:
            item_cache[name] = {}

    result: dict[str, str] = {}
    for m in mappings:
        raw = item_cache.get(m["vault_item"], {})
        value = _extract_field(raw, m["field"])
        result[m["env_var"]] = value

    return result


@app.get("/inject/{service}")
def inject(service: str, format: str = "json"):
    """
    Return credentials for a service as env-var key/value pairs.

    Query params:
      format=json   (default) — {"ENV_VAR": "value", ...}
      format=shell  — export ENV_VAR="value"\\n... (shell-sourceable)
      format=dotenv — ENV_VAR=value\\n... (.env file format)
    """
    if service not in VALID_SERVICES:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown service {service!r}. Valid: {sorted(VALID_SERVICES)}",
        )
    if format not in ("json", "shell", "dotenv"):
        raise HTTPException(
            status_code=400,
            detail="format must be one of: json, shell, dotenv",
        )
    try:
        creds = _resolve_service(service)
    except Exception as exc:
        log.error("inject(%s) failed: %s", service, exc)
        raise HTTPException(status_code=500, detail=str(exc))

    if format == "shell":
        from fastapi.responses import PlainTextResponse
        lines = [f"export {k}={shlex.quote(v)}" for k, v in creds.items()]
        return PlainTextResponse("\n".join(lines) + "\n")

    if format == "dotenv":
        from fastapi.responses import PlainTextResponse
        lines = [f"{k}={v}" for k, v in creds.items()]
        return PlainTextResponse("\n".join(lines) + "\n")

    return {"status": "ok", "service": service, "credentials": creds}


@app.get("/inject")
def list_services():
    """List all registered services available for credential injection."""
    return {
        "status":   "ok",
        "services": sorted(VALID_SERVICES),
    }


# ---------------------------------------------------------------------------
# CRED-06 — Credential rotation endpoints
# ---------------------------------------------------------------------------

@app.get("/rotate")
def list_rotatable():
    """List all services that support credential rotation."""
    return {
        "status":   "ok",
        "services": sorted(ROTATABLE_SERVICES),
    }


@app.post("/rotate/{service}")
def rotate_credential(service: str):
    """
    Rotate credentials for the named service.

    Generates a new secret, updates the external service (where possible),
    and writes the new value to the vault.

    Returns:
      {
        "status": "ok" | "error",
        "service": str,
        "rotated": bool,
        "restart_required": bool,  # true if service restart needed to apply
        "detail": str,             # human-readable outcome
        "error": str,              # only present on failure
      }
    """
    if service not in ROTATABLE_SERVICES:
        raise HTTPException(
            status_code=404,
            detail=f"No rotation adapter for {service!r}. Rotatable: {sorted(ROTATABLE_SERVICES)}",
        )
    adapter = ADAPTERS[service]
    try:
        result = adapter.rotate()
        status = "ok" if result.rotated else "error"
        code = 200 if result.rotated else 500
        return JSONResponse(content={"status": status, **result.to_dict()}, status_code=code)
    except Exception as exc:
        log.error("rotate(%s) unhandled exception: %s", service, exc)
        raise HTTPException(status_code=500, detail=str(exc))
