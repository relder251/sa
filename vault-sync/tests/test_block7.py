"""
CRED-07T — Block 7 tests: post-rotation credential validation (CRED-VALIDATE).

Runs against the live vault-sync container AND the live services.
Set VAULT_SYNC_URL to override vault-sync base (default: http://localhost:8777).
Set N8N_BASE_URL to override n8n base (default: http://localhost:5678).
Set LITELLM_BASE_URL to override LiteLLM base (default: http://localhost:4000).

Strategy by service:
  - n8n        — n8n 2.12.x has NO public REST API for API key management.
                 The n8n adapter attempts live rotation, falls back to vault-only
                 (restart_required=True). Tests verify the vault round-trip only.
                 Live key validation is gated behind N8N_VALIDATE_LIVE=1.
  - litellm    — vault-only (restart_required=True): cannot validate against live service
                 until restarted; verify vault round-trip only (non-empty from /inject).
  - jupyter    — vault-only (restart_required=True): same as litellm.
  - glitchtip  — vault-only (restart_required=True): same.
  - vaultwarden— vault-only (restart_required=True): same.

NOTE: Tests perform REAL rotations against the live vault.
Run in a controlled maintenance window.
"""

import os
import pytest
import httpx

BASE        = os.environ.get("VAULT_SYNC_URL", "http://localhost:8777")
N8N_URL     = os.environ.get("N8N_BASE_URL",   "http://localhost:5678")
LITELLM_URL = os.environ.get("LITELLM_BASE_URL", "http://localhost:4000")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def rotate(service: str) -> dict:
    r = httpx.post(f"{BASE}/rotate/{service}", timeout=60)
    assert r.status_code == 200, f"rotate/{service} HTTP {r.status_code}: {r.text}"
    data = r.json()
    assert data["rotated"] is True, f"{service}: rotation reported failure: {data}"
    return data


def inject(service: str) -> dict:
    r = httpx.get(f"{BASE}/inject/{service}", timeout=30)
    assert r.status_code == 200, f"inject/{service} HTTP {r.status_code}: {r.text}"
    return r.json()["credentials"]


# ---------------------------------------------------------------------------
# n8n — vault-only (n8n 2.12.x has no REST API for API key management)
# ---------------------------------------------------------------------------

class TestN8nValidation:
    """
    n8n 2.12.x does not expose a REST API for API key management.
    The adapter attempts live rotation, falls back to vault-only (restart_required=True).
    Tests verify the vault round-trip and optionally validate against the live service
    when N8N_VALIDATE_LIVE=1 is set (operator must have restarted n8n first).
    """

    def test_rotate_n8n_vault_writes_nonempty_key(self):
        """After rotation, vault holds a non-empty N8N_API_KEY."""
        rotate("n8n")
        creds = inject("n8n")
        assert creds.get("N8N_API_KEY", ""), "N8N_API_KEY is empty in vault after rotation"

    def test_rotate_n8n_key_changes(self):
        """Two consecutive rotations produce different vault values."""
        rotate("n8n")
        first = inject("n8n").get("N8N_API_KEY", "")
        rotate("n8n")
        second = inject("n8n").get("N8N_API_KEY", "")
        assert first and second
        assert first != second, "n8n rotation produced the same key twice"

    @pytest.mark.skipif(
        os.environ.get("N8N_VALIDATE_LIVE") != "1",
        reason=(
            "n8n 2.12.x has no REST API for API key management; "
            "set N8N_VALIDATE_LIVE=1 only after manually regenerating the key "
            "in n8n UI and restarting n8n with the new key"
        ),
    )
    def test_n8n_new_key_authenticates(self):
        """Live key validation — only run after n8n restart with rotated key."""
        creds = inject("n8n")
        new_key = creds.get("N8N_API_KEY", "")
        assert new_key, "N8N_API_KEY is empty in vault"
        r = httpx.get(
            f"{N8N_URL}/api/v1/workflows",
            headers={"X-N8N-API-KEY": new_key},
            timeout=15,
        )
        assert r.status_code == 200, (
            f"n8n key from vault returned HTTP {r.status_code}; "
            f"ensure n8n was restarted after rotation. Response: {r.text[:200]}"
        )


# ---------------------------------------------------------------------------
# Vault-only services — verify credential is stored and readable
# ---------------------------------------------------------------------------

VAULT_ONLY = [
    ("n8n",         "N8N_API_KEY"),
    ("litellm",     "LITELLM_API_KEY"),
    ("jupyter",     "JUPYTER_TOKEN"),
    ("glitchtip",   "SENTRY_DSN"),
    ("vaultwarden", "VAULTWARDEN_ADMIN_TOKEN"),
]


class TestVaultRoundTrip:
    """
    For services where rotation writes to the vault only (restart_required=True),
    verify that the new credential is non-empty and readable via /inject.
    Live service validation requires a service restart and is out of scope here.
    """

    @pytest.mark.parametrize("service,env_var", VAULT_ONLY)
    def test_rotate_returns_restart_required(self, service, env_var):
        # n8n attempts live rotation and falls back to vault-only; restart_required
        # is always True for the fallback path and for all other vault-only adapters.
        data = rotate(service)
        assert data["restart_required"] is True, (
            f"{service}: expected restart_required=True for vault-only adapter"
        )

    @pytest.mark.parametrize("service,env_var", VAULT_ONLY)
    def test_rotated_credential_nonempty_in_vault(self, service, env_var):
        rotate(service)
        creds = inject(service)
        value = creds.get(env_var, "")
        assert value, (
            f"{env_var} is empty after rotating {service}; "
            "credential was not written to vault correctly"
        )

    @pytest.mark.parametrize("service,env_var", VAULT_ONLY)
    def test_rotated_credential_changes(self, service, env_var):
        """Rotating twice should produce two different values."""
        rotate(service)
        first = inject(service).get(env_var, "")

        rotate(service)
        second = inject(service).get(env_var, "")

        assert first and second, f"{env_var} empty on one of the two rotations"
        assert first != second, (
            f"{service}: two consecutive rotations produced the same {env_var} value "
            f"({first!r}); token generator may not be producing unique values"
        )


# ---------------------------------------------------------------------------
# LiteLLM — vault-only rotation + optional live check after restart
# ---------------------------------------------------------------------------

class TestLiteLLMValidation:
    """
    LiteLLM is vault-only.  The live key check is skipped unless
    LITELLM_VALIDATE_LIVE=1 is set (operator must restart litellm first).
    """

    def test_litellm_key_format(self):
        rotate("litellm")
        creds = inject("litellm")
        key = creds.get("LITELLM_API_KEY", "")
        assert key.startswith("sk-"), (
            f"LITELLM_API_KEY does not start with 'sk-': {key!r}"
        )

    @pytest.mark.skipif(
        os.environ.get("LITELLM_VALIDATE_LIVE") != "1",
        reason="Set LITELLM_VALIDATE_LIVE=1 to run after restarting litellm",
    )
    def test_litellm_new_key_authenticates(self):
        rotate("litellm")
        creds = inject("litellm")
        key = creds.get("LITELLM_API_KEY", "")
        assert key, "LITELLM_API_KEY empty after rotation"

        r = httpx.get(
            f"{LITELLM_URL}/health/liveliness",
            headers={"Authorization": f"Bearer {key}"},
            timeout=15,
        )
        assert r.status_code == 200, (
            f"LiteLLM health check with new key returned {r.status_code}: {r.text[:200]}"
        )


# ---------------------------------------------------------------------------
# Cross-service: /inject reflects latest vault state after rotation
# ---------------------------------------------------------------------------

class TestInjectConsistency:
    """
    After rotation, /inject must return the credential that was just written —
    not a stale cached value.
    """

    def test_n8n_inject_reflects_rotation(self):
        """
        /inject/n8n must return a non-empty key after rotation and it must
        differ from the pre-rotation value (vault was actually updated).
        """
        pre = inject("n8n").get("N8N_API_KEY", "")
        rotate("n8n")
        post = inject("n8n").get("N8N_API_KEY", "")
        assert post, "N8N_API_KEY empty from /inject after rotation"
        assert pre != post, (
            "/inject/n8n returned the same key before and after rotation; "
            "vault was not updated or /inject is serving stale data"
        )

    def test_litellm_inject_reflects_rotation(self):
        """Same staleness check for litellm."""
        pre = inject("litellm").get("LITELLM_API_KEY", "")
        rotate("litellm")
        post = inject("litellm").get("LITELLM_API_KEY", "")
        assert post, "LITELLM_API_KEY empty from /inject after rotation"
        assert pre != post, "LITELLM_API_KEY unchanged after rotation"
