"""
vault-sync/app/adapters/base.py
Base credential rotation adapter protocol + registry.
"""

import secrets
import string
from typing import Protocol


def generate_token(length: int = 32) -> str:
    """Generate a URL-safe random token."""
    return secrets.token_urlsafe(length)


def generate_password(length: int = 32) -> str:
    """Generate a random alphanumeric+symbol password."""
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
    return "".join(secrets.choice(alphabet) for _ in range(length))


class RotationResult:
    """Structured result from a rotation operation."""

    def __init__(
        self,
        service: str,
        rotated: bool,
        restart_required: bool = False,
        detail: str = "",
        error: str = "",
    ):
        self.service          = service
        self.rotated          = rotated
        self.restart_required = restart_required
        self.detail           = detail
        self.error            = error

    def to_dict(self) -> dict:
        d: dict = {
            "service":          self.service,
            "rotated":          self.rotated,
            "restart_required": self.restart_required,
        }
        if self.detail:
            d["detail"] = self.detail
        if self.error:
            d["error"] = self.error
        return d
