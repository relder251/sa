"""
CRED-01T — Block 1 tests: FastAPI foundation, vault auth, health/status endpoints.

Runs against the live vault-sync container on VPS (or locally with env vars set).
Set VAULT_SYNC_URL to override (default: http://localhost:8777).
"""

import os
import pytest
import httpx

BASE = os.environ.get("VAULT_SYNC_URL", "http://localhost:8777")

# ---------------------------------------------------------------------------
# Health endpoint
# ---------------------------------------------------------------------------

def test_health_returns_200():
    r = httpx.get(f"{BASE}/health", timeout=10)
    assert r.status_code == 200


def test_health_body():
    r = httpx.get(f"{BASE}/health", timeout=10)
    assert r.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# Status endpoint (requires live vault connection)
# ---------------------------------------------------------------------------

def test_status_returns_200():
    r = httpx.get(f"{BASE}/status", timeout=30)
    assert r.status_code == 200


def test_status_has_vault_key():
    r = httpx.get(f"{BASE}/status", timeout=30)
    data = r.json()
    assert "status" in data


# ---------------------------------------------------------------------------
# Sync endpoint
# ---------------------------------------------------------------------------

def test_sync_returns_ok():
    r = httpx.post(f"{BASE}/sync", timeout=60)
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


# ---------------------------------------------------------------------------
# Items listing (requires live vault)
# ---------------------------------------------------------------------------

def test_list_items_returns_list():
    r = httpx.get(f"{BASE}/items", timeout=30)
    assert r.status_code == 200
    data = r.json()
    assert "items" in data
    assert isinstance(data["items"], list)
    assert data["count"] == len(data["items"])


# ---------------------------------------------------------------------------
# Update endpoint — validation
# ---------------------------------------------------------------------------

def test_update_missing_name_returns_422():
    r = httpx.post(f"{BASE}/update", json={"password": "x"}, timeout=10)
    assert r.status_code == 422


def test_update_missing_password_returns_422():
    r = httpx.post(f"{BASE}/update", json={"name": "x"}, timeout=10)
    assert r.status_code == 422


def test_update_nonexistent_item_returns_404():
    r = httpx.post(
        f"{BASE}/update",
        json={"name": "__cred01t_nonexistent__", "password": "test123"},
        timeout=30,
    )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# OpenAPI docs are served (confirms FastAPI is running, not Flask)
# ---------------------------------------------------------------------------

def test_openapi_docs_available():
    r = httpx.get(f"{BASE}/docs", timeout=10)
    assert r.status_code == 200
    assert "swagger" in r.text.lower() or "openapi" in r.text.lower()
