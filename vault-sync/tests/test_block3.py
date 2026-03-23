"""
CRED-03T — Block 3 tests: Keycloak sync adapter.

Runs against the live vault-sync container with Keycloak available.
Set VAULT_SYNC_URL to override (default: http://localhost:8777).

Tests verify endpoint availability and contract shape.  Full password-sync
verification (confirming the new password actually works) requires a
Keycloak login, which is covered in the E2E gate (CRED-VALIDATE).
"""

import os
import pytest
import httpx

BASE = os.environ.get("VAULT_SYNC_URL", "http://localhost:8777")


# ---------------------------------------------------------------------------
# /drift/keycloak
# ---------------------------------------------------------------------------

def test_drift_endpoint_returns_200():
    r = httpx.get(f"{BASE}/drift/keycloak", timeout=30)
    assert r.status_code == 200, r.text


def test_drift_response_shape():
    r = httpx.get(f"{BASE}/drift/keycloak", timeout=30)
    data = r.json()
    assert data["status"] == "ok"
    assert "drifted" in data
    assert isinstance(data["matched"], list)
    assert isinstance(data["vault_only"], list)
    assert isinstance(data["keycloak_only"], list)
    assert isinstance(data["keycloak_total"], int)
    assert isinstance(data["vault_total"], int)


def test_drift_matched_items_have_required_keys():
    r = httpx.get(f"{BASE}/drift/keycloak", timeout=30)
    matched = r.json()["matched"]
    for item in matched:
        assert "name" in item
        assert "vault_id" in item
        assert "keycloak_user_id" in item


def test_drift_keycloak_sso_is_matched():
    """Keycloak SSO vault item should match a Keycloak user."""
    r = httpx.get(f"{BASE}/drift/keycloak", timeout=30)
    data = r.json()
    matched_names = [m["name"] for m in data["matched"]]
    # Keycloak SSO is in ITEM_TAXONOMY under user-credentials — it must match
    assert len(matched_names) > 0, "Expected at least one matched user-credentials item"


def test_drift_drifted_count_is_consistent():
    r = httpx.get(f"{BASE}/drift/keycloak", timeout=30)
    data = r.json()
    expected = len(data["vault_only"]) + len(data["keycloak_only"])
    assert data["drifted"] == expected


# ---------------------------------------------------------------------------
# /sync/keycloak
# ---------------------------------------------------------------------------

def test_sync_endpoint_returns_200():
    r = httpx.post(f"{BASE}/sync/keycloak", timeout=60)
    assert r.status_code == 200, r.text


def test_sync_response_shape():
    r = httpx.post(f"{BASE}/sync/keycloak", timeout=60)
    data = r.json()
    assert data["status"] == "ok"
    assert isinstance(data["synced"], list)
    assert isinstance(data["skipped"], list)
    assert isinstance(data["errors"], list)


def test_sync_no_errors():
    r = httpx.post(f"{BASE}/sync/keycloak", timeout=60)
    data = r.json()
    assert data["errors"] == [], f"Sync errors: {data['errors']}"


def test_sync_synced_items_have_required_keys():
    r = httpx.post(f"{BASE}/sync/keycloak", timeout=60)
    for item in r.json()["synced"]:
        assert "name" in item
        assert "user_id" in item
        assert "username" in item


def test_sync_then_drift_shows_no_new_vault_only():
    """After sync, vault_only should not grow (idempotent)."""
    r1 = httpx.get(f"{BASE}/drift/keycloak", timeout=30)
    vault_only_before = len(r1.json()["vault_only"])

    httpx.post(f"{BASE}/sync/keycloak", timeout=60)

    r2 = httpx.get(f"{BASE}/drift/keycloak", timeout=30)
    vault_only_after = len(r2.json()["vault_only"])

    assert vault_only_after == vault_only_before, (
        f"vault_only grew after sync: {vault_only_before} → {vault_only_after}"
    )
