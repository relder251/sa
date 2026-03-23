"""
vault-sync/app/adapters/postgres.py
PostgreSQL password rotation adapter.

Rotation strategy:
  1. Generate new password
  2. Execute ALTER USER via psql subprocess
  3. Update vault item "PostgreSQL — Connection Info"

Requires POSTGRES_HOST, LITELLM_USER, and current LITELLM_PASSWORD (or
reads from vault as fallback) to connect for ALTER USER.
"""

import logging
import os
import subprocess
import vault
from adapters.base import RotationResult, generate_password

log = logging.getLogger(__name__)

POSTGRES_HOST = os.environ.get("POSTGRES_HOST", "postgres")
POSTGRES_PORT = os.environ.get("POSTGRES_PORT", "5432")
POSTGRES_DB   = os.environ.get("LITELLM_DB", "litellm")
PG_USER       = os.environ.get("LITELLM_USER", "litellm")
PG_PASSWORD   = os.environ.get("LITELLM_PASSWORD", "")
VAULT_ITEM    = "PostgreSQL \u2014 Connection Info"


def _current_password() -> str:
    if PG_PASSWORD:
        return PG_PASSWORD
    try:
        item = vault.get_item(VAULT_ITEM)
        return (item.get("login") or {}).get("password", "")
    except Exception:
        return ""


def rotate() -> RotationResult:
    current_pw = _current_password()
    if not current_pw:
        return RotationResult(
            service="postgres",
            rotated=False,
            error="Cannot determine current PostgreSQL password; set LITELLM_PASSWORD env var",
        )

    new_password = generate_password(24)
    conn_str = f"postgresql://{PG_USER}:{current_pw}@{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB}"

    try:
        result = subprocess.run(
            ["psql", conn_str, "-c",
             f"ALTER USER {PG_USER} WITH PASSWORD '{new_password}'"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            raise RuntimeError(f"psql error: {result.stderr.strip()}")
        log.info("PostgreSQL password changed for user %s", PG_USER)
    except FileNotFoundError:
        return RotationResult(
            service="postgres",
            rotated=False,
            error="psql not available in vault-sync container; cannot rotate postgres password live",
        )
    except Exception as exc:
        return RotationResult(service="postgres", rotated=False, error=str(exc))

    try:
        vault.update_item(VAULT_ITEM, username=PG_USER, password=new_password)
        log.info("PostgreSQL vault item updated")
        return RotationResult(
            service="postgres",
            rotated=True,
            restart_required=True,
            detail=f"Password changed for user {PG_USER}; restart dependent services to apply",
        )
    except Exception as exc:
        return RotationResult(
            service="postgres",
            rotated=False,
            error=f"psql succeeded but vault update failed: {exc}",
        )
