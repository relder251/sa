"""
vault-sync/app/adapters/glitchtip.py
GlitchTip DSN / secret key rotation adapter.

GlitchTip uses a Django SECRET_KEY set via env var; the DSN stored in the
vault is the Sentry-compatible ingest URL used by other services.

Rotation strategy:
  1. Generate new Django secret key
  2. Update vault item "GlitchTip (Sentry)"
  3. Flag restart_required=True — GlitchTip reads SECRET_KEY at startup

Note: The DSN ingest URL itself doesn't change on key rotation; what changes
is the Django session/signing key.  Services using the DSN continue to work
after key rotation; only active sessions are invalidated.
"""

import logging
import vault
from adapters.base import RotationResult, generate_password

log = logging.getLogger(__name__)

VAULT_ITEM = "GlitchTip (Sentry)"


def rotate() -> RotationResult:
    # Django secret key format: 50 char alphanumeric+symbols
    new_secret = generate_password(50)
    try:
        vault.update_item(VAULT_ITEM, username=None, password=new_secret)
        log.info("GlitchTip secret key rotated in vault")
        return RotationResult(
            service="glitchtip",
            rotated=True,
            restart_required=True,
            detail="New Django SECRET_KEY written to vault; restart glitchtip to apply",
        )
    except Exception as exc:
        log.error("glitchtip rotation failed: %s", exc)
        return RotationResult(service="glitchtip", rotated=False, error=str(exc))
