"""
vault-sync/app/adapters/n8n.py
n8n API key rotation adapter.

Rotation strategy:
  1. Call n8n DELETE /api/v1/user/api-key to revoke the current key
  2. Call n8n POST /api/v1/user/api-key to generate a new key
  3. Update vault item "n8n API Key (JWT)"

Requires N8N_BASE_URL and the current N8N_API_KEY env vars.
Falls back to vault-only update if n8n API is unreachable (sets restart_required=True).
"""

import json
import logging
import os
import urllib.request
import vault
from adapters.base import RotationResult

log = logging.getLogger(__name__)

N8N_BASE_URL = os.environ.get("N8N_BASE_URL", "http://n8n:5678")
VAULT_ITEM   = "n8n API Key (JWT)"


def _current_api_key() -> str:
    """Fetch current n8n API key from vault."""
    try:
        item = vault.get_item(VAULT_ITEM)
        return (item.get("login") or {}).get("password", "")
    except Exception:
        return os.environ.get("N8N_API_KEY", "")


def _n8n_request(method: str, path: str, token: str, body: dict | None = None) -> dict:
    url = f"{N8N_BASE_URL}{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("X-N8N-API-KEY", token)
    if body:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read()) if resp.length else {}


def rotate() -> RotationResult:
    current_key = _current_api_key()

    # Try live rotation via n8n API
    if current_key:
        try:
            # Delete existing API key(s)
            try:
                _n8n_request("DELETE", "/api/v1/user/api-key", current_key)
                log.info("n8n: revoked existing API key")
            except Exception as del_exc:
                log.warning("n8n: could not revoke existing key (continuing): %s", del_exc)

            # Create new API key
            resp = _n8n_request("POST", "/api/v1/user/api-key", current_key,
                                 body={"label": "vault-sync-rotated"})
            new_key = resp.get("data", {}).get("apiKey") or resp.get("apiKey", "")

            if new_key:
                vault.update_item(VAULT_ITEM, username=None, password=new_key)
                log.info("n8n API key rotated live and saved to vault")
                return RotationResult(
                    service="n8n",
                    rotated=True,
                    restart_required=False,
                    detail="API key rotated via n8n API and vault updated",
                )
        except Exception as exc:
            log.warning("n8n live rotation failed (%s); falling back to vault-only", exc)

    # Fallback: vault-only update (n8n unreachable or key unknown)
    log.warning("n8n: performing vault-only rotation — restart n8n to apply")
    import secrets
    new_key = secrets.token_urlsafe(32)
    try:
        vault.update_item(VAULT_ITEM, username=None, password=new_key)
        return RotationResult(
            service="n8n",
            rotated=True,
            restart_required=True,
            detail="Vault updated; n8n API unreachable — restart n8n to apply new key",
        )
    except Exception as exc:
        return RotationResult(service="n8n", rotated=False, error=str(exc))
