"""
vault-sync/app/main.py
FastAPI credential management service.

Bootstrap environment variables (kept in .env — the only on-disk secrets):
  BW_SERVER       — Vaultwarden URL
  BW_CLIENTID     — Vaultwarden API key client_id
  BW_CLIENTSECRET — Vaultwarden API key client_secret
  BW_MASTER_PASS  — vault master password

Optional Keycloak sync (enables /update-keycloak and atomic sync on /update):
  KEYCLOAK_ADMIN_URL   — e.g. https://kc.sovereignadvisory.ai
  KEYCLOAK_ADMIN_USER  — default: admin
  KEYCLOAK_ADMIN_PASS  — Keycloak admin password
  KEYCLOAK_REALM       — default: agentic-sdlc
  KEYCLOAK_SYNC_ITEMS  — comma-separated vault item names to auto-sync
"""

import logging
import os

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import vault
import keycloak as kc

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Sentry / GlitchTip (optional)
# ---------------------------------------------------------------------------
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
app = FastAPI(title="vault-sync", version="2.0.0")


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
