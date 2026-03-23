"""
vault-sync/app/adapters/jupyter.py
JupyterLab token rotation adapter.

Rotation strategy:
  1. Generate new URL-safe token
  2. Update vault item "JupyterLab Token"
  3. Flag restart_required=True — jupyter reads token from env var at startup
"""

import logging
import vault
from adapters.base import RotationResult, generate_token

log = logging.getLogger(__name__)

VAULT_ITEM = "JupyterLab Token"


def rotate() -> RotationResult:
    new_token = generate_token(32)
    try:
        vault.update_item(VAULT_ITEM, username=None, password=new_token)
        log.info("JupyterLab token rotated in vault")
        return RotationResult(
            service="jupyter",
            rotated=True,
            restart_required=True,
            detail="New token written to vault; restart jupyter container to apply",
        )
    except Exception as exc:
        log.error("jupyter rotation failed: %s", exc)
        return RotationResult(service="jupyter", rotated=False, error=str(exc))
