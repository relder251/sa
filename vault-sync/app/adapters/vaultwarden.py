"""
vault-sync/app/adapters/vaultwarden.py
Vaultwarden admin token rotation adapter.

The Vaultwarden admin panel is protected by VAULTWARDEN_ADMIN_TOKEN (bcrypt hash
of the admin password, or raw token in legacy mode).

Rotation strategy:
  1. Generate new random token
  2. Update vault item "Vaultwarden Admin Panel"
  3. Flag restart_required=True — Vaultwarden reads VAULTWARDEN_ADMIN_TOKEN at startup

Note: Vaultwarden admin tokens can also be updated live via the admin API at
/admin/config, but that requires the current token. We update the vault and
prompt for restart to keep the adapter simple.
"""

import logging
import vault
from adapters.base import RotationResult, generate_token

log = logging.getLogger(__name__)

VAULT_ITEM = "Vaultwarden Admin Panel"


def rotate() -> RotationResult:
    new_token = generate_token(32)
    try:
        vault.update_item(VAULT_ITEM, username=None, password=new_token)
        log.info("Vaultwarden admin token rotated in vault")
        return RotationResult(
            service="vaultwarden",
            rotated=True,
            restart_required=True,
            detail="New admin token written to vault; restart vaultwarden to apply",
        )
    except Exception as exc:
        log.error("vaultwarden rotation failed: %s", exc)
        return RotationResult(service="vaultwarden", rotated=False, error=str(exc))
