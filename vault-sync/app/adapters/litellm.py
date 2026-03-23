"""
vault-sync/app/adapters/litellm.py
LiteLLM master API key rotation adapter.

Rotation strategy:
  1. Generate new sk-... key
  2. Update vault item "LiteLLM Master API Key"
  3. Flag restart_required=True — container picks up new key on next deploy

LiteLLM's master key is set via LITELLM_API_KEY env var; live rotation via
the proxy management API requires the current key. We update the vault here;
the operator restarts litellm to apply.
"""

import logging
import vault
from adapters.base import RotationResult, generate_token

log = logging.getLogger(__name__)

VAULT_ITEM = "LiteLLM Master API Key"


def rotate() -> RotationResult:
    new_key = f"sk-{generate_token(32)}"
    try:
        vault.update_item(VAULT_ITEM, username=None, password=new_key)
        log.info("LiteLLM master API key rotated in vault")
        return RotationResult(
            service="litellm",
            rotated=True,
            restart_required=True,
            detail="New key written to vault; restart litellm container to apply",
        )
    except Exception as exc:
        log.error("litellm rotation failed: %s", exc)
        return RotationResult(service="litellm", rotated=False, error=str(exc))
