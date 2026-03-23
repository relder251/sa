"""
vault-sync/app/registry.py
Service credential registry — maps service names to vault items and env vars.

Each service entry is a list of credential mappings:
  vault_item  — exact vault item name (must match bw item name)
  env_var     — environment variable to export
  field       — which part of the vault item: "password", "username", or "notes"
"""

# ---------------------------------------------------------------------------
# Service registry
# ---------------------------------------------------------------------------

SERVICE_REGISTRY: dict[str, list[dict]] = {
    "litellm": [
        {
            "vault_item": "LiteLLM Master API Key",
            "env_var":    "LITELLM_API_KEY",
            "field":      "password",
        },
    ],
    "n8n": [
        {
            "vault_item": "n8n API Key (JWT)",
            "env_var":    "N8N_API_KEY",
            "field":      "password",
        },
    ],
    "jupyter": [
        {
            "vault_item": "JupyterLab Token",
            "env_var":    "JUPYTER_TOKEN",
            "field":      "password",
        },
    ],
    "glitchtip": [
        {
            "vault_item": "GlitchTip (Sentry)",
            "env_var":    "SENTRY_DSN",
            "field":      "password",
        },
    ],
    "vaultwarden": [
        {
            "vault_item": "Vaultwarden Admin Panel",
            "env_var":    "VAULTWARDEN_ADMIN_TOKEN",
            "field":      "password",
        },
    ],
    "keycloak": [
        {
            "vault_item": "Keycloak Admin Console",
            "env_var":    "KEYCLOAK_ADMIN_PASS",
            "field":      "password",
        },
    ],
    "postgres": [
        {
            "vault_item": "PostgreSQL \u2014 Connection Info",
            "env_var":    "POSTGRES_PASSWORD",
            "field":      "password",
        },
        {
            "vault_item": "PostgreSQL \u2014 Connection Info",
            "env_var":    "POSTGRES_USER",
            "field":      "username",
        },
    ],
}

VALID_SERVICES: set[str] = set(SERVICE_REGISTRY.keys())
