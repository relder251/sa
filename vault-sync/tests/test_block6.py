"""
CRED-06T — Block 6 tests: credential rotation adapters.

Runs against the live vault-sync container.
Set VAULT_SYNC_URL to override (default: http://localhost:8777).

NOTE: These tests perform REAL credential rotations against the live vault.
Vault items will have their passwords changed.  Run in a controlled window.
The tests verify rotation completes successfully — not that the new credential
works in the target service (that is covered by CRED-VALIDATE).
"""

import os
import pytest
import httpx

BASE = os.environ.get("VAULT_SYNC_URL", "http://localhost:8777")

ROTATABLE = ["litellm", "n8n", "jupyter", "glitchtip", "vaultwarden"]
# postgres excluded by default — psql not available in vault-sync container


# ---------------------------------------------------------------------------
# GET /rotate — service listing
# ---------------------------------------------------------------------------

def test_list_rotatable_returns_200():
    r = httpx.get(f"{BASE}/rotate", timeout=10)
    assert r.status_code == 200


def test_list_rotatable_response_shape():
    r = httpx.get(f"{BASE}/rotate", timeout=10)
    data = r.json()
    assert data["status"] == "ok"
    assert isinstance(data["services"], list)


def test_list_rotatable_contains_expected_services():
    r = httpx.get(f"{BASE}/rotate", timeout=10)
    listed = set(r.json()["services"])
    for svc in ROTATABLE:
        assert svc in listed, f"Expected {svc!r} in rotatable list"


# ---------------------------------------------------------------------------
# POST /rotate/{service} — invalid service
# ---------------------------------------------------------------------------

def test_rotate_unknown_service_returns_404():
    r = httpx.post(f"{BASE}/rotate/__no_such_service__", timeout=10)
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# POST /rotate/{service} — response contract
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("service", ROTATABLE)
def test_rotate_returns_200(service):
    r = httpx.post(f"{BASE}/rotate/{service}", timeout=60)
    assert r.status_code == 200, f"rotate/{service} returned {r.status_code}: {r.text}"


@pytest.mark.parametrize("service", ROTATABLE)
def test_rotate_response_has_required_keys(service):
    r = httpx.post(f"{BASE}/rotate/{service}", timeout=60)
    data = r.json()
    assert "status" in data
    assert "service" in data
    assert "rotated" in data
    assert "restart_required" in data
    assert data["service"] == service


@pytest.mark.parametrize("service", ROTATABLE)
def test_rotate_reports_success(service):
    r = httpx.post(f"{BASE}/rotate/{service}", timeout=60)
    data = r.json()
    assert data["status"] == "ok", f"{service}: {data}"
    assert data["rotated"] is True, f"{service}: rotated=False, error={data.get('error')}"


# ---------------------------------------------------------------------------
# Rotation idempotency — rotating twice both succeed
# ---------------------------------------------------------------------------

def test_rotate_litellm_twice_both_succeed():
    r1 = httpx.post(f"{BASE}/rotate/litellm", timeout=60)
    r2 = httpx.post(f"{BASE}/rotate/litellm", timeout=60)
    assert r1.json()["rotated"] is True
    assert r2.json()["rotated"] is True


# ---------------------------------------------------------------------------
# After rotation, inject returns a non-empty credential
# ---------------------------------------------------------------------------

def test_litellm_inject_after_rotate_nonempty():
    httpx.post(f"{BASE}/rotate/litellm", timeout=60)
    r = httpx.get(f"{BASE}/inject/litellm", timeout=30)
    assert r.json()["credentials"]["LITELLM_API_KEY"]


def test_n8n_inject_after_rotate_nonempty():
    httpx.post(f"{BASE}/rotate/n8n", timeout=60)
    r = httpx.get(f"{BASE}/inject/n8n", timeout=30)
    assert r.json()["credentials"]["N8N_API_KEY"]


def test_jupyter_inject_after_rotate_nonempty():
    httpx.post(f"{BASE}/rotate/jupyter", timeout=60)
    r = httpx.get(f"{BASE}/inject/jupyter", timeout=30)
    assert r.json()["credentials"]["JUPYTER_TOKEN"]
