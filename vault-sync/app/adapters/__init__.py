"""
vault-sync/app/adapters/__init__.py
Adapter registry: maps service names to rotate() callables.
"""

from adapters import litellm, n8n, postgres, jupyter, glitchtip, vaultwarden

ADAPTERS: dict[str, object] = {
    "litellm":     litellm,
    "n8n":         n8n,
    "postgres":    postgres,
    "jupyter":     jupyter,
    "glitchtip":   glitchtip,
    "vaultwarden": vaultwarden,
}

ROTATABLE_SERVICES: set[str] = set(ADAPTERS.keys())
