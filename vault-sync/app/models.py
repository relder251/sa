"""
vault-sync/app/models.py
Credential data models. Placeholder extended in CRED-02 with full taxonomy.
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class CredItem:
    """A single credential item stored in Vaultwarden."""
    name: str
    username: Optional[str] = None
    password: Optional[str] = None
    notes: Optional[str] = None
    collection: Optional[str] = None          # user-credentials / system-credentials / provider-credentials
    service_tags: list[str] = field(default_factory=list)
    custom_fields: dict = field(default_factory=dict)
    vault_id: Optional[str] = None            # Vaultwarden item UUID, populated after fetch
