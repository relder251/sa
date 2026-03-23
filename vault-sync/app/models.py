"""
vault-sync/app/models.py
Credential data models and collection taxonomy.

Three-tier credential taxonomy (stored as custom fields on vault items):

  user-credentials      Keycloak SSO logins, portal accounts
  system-credentials    Internal service secrets (n8n, LiteLLM, PostgreSQL, etc.)
  provider-credentials  External provider keys (Anthropic, Hostinger, etc.) — Phase 2
"""

from dataclasses import dataclass, field
from typing import Optional

VALID_COLLECTIONS = {"user-credentials", "system-credentials", "provider-credentials"}

# Mapping: vault item name → (collection, service_tags)
# Used by the migration step (CRED-02) to tag existing items.
ITEM_TAXONOMY: dict[str, tuple[str, list[str]]] = {
    "Keycloak SSO":            ("user-credentials",    ["keycloak"]),
    "Keycloak Admin Console":  ("user-credentials",    ["keycloak-admin"]),
    "n8n Login":               ("user-credentials",    ["n8n"]),
    "GlitchTip (Sentry)":     ("system-credentials",  ["glitchtip"]),
    "JupyterLab Token":        ("system-credentials",  ["jupyter"]),
    "LiteLLM Master API Key":  ("system-credentials",  ["litellm"]),
    "n8n API Key (JWT)":       ("system-credentials",  ["n8n"]),
    "PostgreSQL \u2014 Connection Info": ("system-credentials", ["postgres"]),
    "Vaultwarden Admin Panel": ("system-credentials",  ["vaultwarden-admin"]),
    "accounts.google.com":     ("provider-credentials", []),
    "Direct Email - Neo":      ("provider-credentials", ["email"]),
    "GitHub SSH Key (Claude Code)": ("provider-credentials", ["github"]),
    "Default SSH Key (local)": ("provider-credentials", ["ssh"]),
    "Production VPS \u2014 Access Info": ("provider-credentials", ["vps"]),
    "Production VPS SSH Key":  ("provider-credentials", ["vps"]),
    "Twingate \u2014 Network Access": ("provider-credentials", ["twingate"]),
}

# Custom field names used on vault items
FIELD_COLLECTION   = "collection"
FIELD_SERVICE_TAGS = "service_tags"


@dataclass
class CredItem:
    """A credential item with collection taxonomy metadata."""
    name: str
    username: Optional[str] = None
    password: Optional[str] = None
    notes: Optional[str] = None
    collection: Optional[str] = None            # one of VALID_COLLECTIONS
    service_tags: list[str] = field(default_factory=list)
    custom_fields: dict = field(default_factory=dict)
    vault_id: Optional[str] = None

    def validate_collection(self) -> None:
        if self.collection and self.collection not in VALID_COLLECTIONS:
            raise ValueError(
                f"Invalid collection {self.collection!r}. "
                f"Must be one of: {sorted(VALID_COLLECTIONS)}"
            )


def item_to_cred(vault_item: dict) -> CredItem:
    """Convert a raw bw vault item dict to a CredItem."""
    fields = {f["name"]: f["value"] for f in (vault_item.get("fields") or [])}
    login = vault_item.get("login") or {}

    service_tags_raw = fields.get(FIELD_SERVICE_TAGS, "")
    service_tags = [t.strip() for t in service_tags_raw.split(",") if t.strip()]

    custom = {k: v for k, v in fields.items()
              if k not in (FIELD_COLLECTION, FIELD_SERVICE_TAGS)}

    return CredItem(
        name=vault_item.get("name", ""),
        username=login.get("username"),
        password=None,           # never returned in API responses
        notes=vault_item.get("notes"),
        collection=fields.get(FIELD_COLLECTION),
        service_tags=service_tags,
        custom_fields=custom,
        vault_id=vault_item.get("id"),
    )


def build_fields(collection: Optional[str], service_tags: list[str],
                 extra_fields: list[dict]) -> list[dict]:
    """Build the bw fields array with collection + service_tags embedded."""
    fields = list(extra_fields)
    if collection:
        fields.append({"name": FIELD_COLLECTION, "value": collection, "type": 0})
    if service_tags:
        fields.append({"name": FIELD_SERVICE_TAGS, "value": ",".join(service_tags), "type": 0})
    return fields
