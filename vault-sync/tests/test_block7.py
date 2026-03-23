"""
CRED-07T — Block 7 tests: post-rotation credential validation (CRED-VALIDATE).

Runs against the live vault-sync container AND the live services.
Set VAULT_SYNC_URL to override vault-sync base (default: http://localhost:8777).
Set N8N_BASE_URL to override n8n base (default: http://localhost:5678).
Set LITELLM_BASE_URL to override LiteLLM base (default: http://localhost:4000).

Strategy by service:
  - n8n        — live-rotation (restart_required=False): new key is immediately active;
                 validate against real n8n API (/api/v1/workflows).
  - litellm    — vault-only (restart_required=True): cannot validate against live service
                 until restarted; verify vault round-trip only (non-empty from /inject).
  - jupyter    — vault-only (restart_required=True): same as litellm.
  - glitchtip  — vault-only (restart_required=True): same.
  - vaultwarden— vault-only (restart_required=True): same.

NOTE: Tests perform REAL rotations against the live vault and, for n8n, against the
live n8n service.  Run in a controlled maintenance window.
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
# n8n — live rotation; new key must authenticate immediately
# ---------------------------------------------------------------------------

class TestN8nValidation:
    """n8n rotates live (restart_required=False); key is active immediately."""

    def test_rotate_n8n_live(self):
        data = rotate("n8n")
        assert data["restart_required"] is False, (
            "n8n adapter unexpectedly set restart_required=True; "
            "live validation cannot proceed until n8n is restarted"
        )

    def test_n8n_new_key_authenticates(self):
        rotate("n8n")
        creds = inject("n8n")
        new_key = creds.get("N8N_API_KEY", "")
        assert new_key, "N8N_API_KEY is empty after rotation"

        # Verify new key works against live n8n
        r = httpx.get(
            f"{N8N_URL}/api/v1/workflows",
            headers={"X-N8N-API-KEY": new_key},
            timeout=15,
        )
        assert r.status_code == 200, (
            f"New n8n API key returned HTTP {r.status_code}; key may not be active yet. "
            f"Response: {r.text[:200]}"
        )

    def test_n8n_old_key_rejected_after_rotate(self):
        """
        Fetch the current key, rotate, then confirm the OLD key no longer works.
        This test is best-effort: if n8n's DELETE /api/v1/user/api-key succeeds,
        the old key should be revoked.  If it 401s, old key is gone (pass).
        If it 200s, n8n may not have revoked it (note but don't hard-fail).
        """
        # Capture key before rotation
        pre_creds = inject("n8n")
        old_key = pre_creds.get("N8N_API_KEY", "")
        assert old_key, "Could not retrieve N8N_API_KEY before rotation"

        rotate("n8n")
        new_creds = inject("n8n")
        new_key = new_creds.get("N8N_API_KEY", "")

        if old_key == new_key:
            pytest.skip("Old and new key are identical (n8n may have reused the key)")

        r = httpx.get(
            f"{N8N_URL}/api/v1/workflows",
            headers={"X-N8N-API-KEY": old_key},
            timeout=15,
        )
        assert r.status_code in (401, 403), (
            f"Old n8n key still accepted after rotation (HTTP {r.status_code}). "
            "Key revocation may not be working."
        )


# ---------------------------------------------------------------------------
# Vault-only services — verify credential is stored and readable
# ---------------------------------------------------------------------------

VAULT_ONLY = [
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

    def test_n8n_inject_matches_rotation_result(self):
        """
        /inject/n8n must return the key that n8n is now accepting.
        (This also cross-validates the n8n vault-write and inject pipeline.)
        """
        rotate("n8n")
        creds = inject("n8n")
        key = creds.get("N8N_API_KEY", "")
        assert key, "N8N_API_KEY empty from /inject after rotation"

        r = httpx.get(
            f"{N8N_URL}/api/v1/workflows",
            headers={"X-N8N-API-KEY": key},
            timeout=15,
        )
        assert r.status_code == 200, (
            f"/inject key does not authenticate against n8n (HTTP {r.status_code}); "
            "vault write or inject pipeline may have a staleness bug"
        )
