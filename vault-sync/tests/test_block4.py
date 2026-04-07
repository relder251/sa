"""
CRED-04T — Block 4 tests: credential injection endpoint.

Runs against the live vault-sync container.
Set VAULT_SYNC_URL to override (default: http://localhost:8777).
"""

import os
import pytest
import httpx

BASE = os.environ.get("VAULT_SYNC_URL", "http://localhost:8777")

KNOWN_SERVICES = ["litellm", "n8n", "jupyter", "glitchtip", "vaultwarden", "keycloak", "postgres"]


# ---------------------------------------------------------------------------
# /inject — service listing
# ---------------------------------------------------------------------------

def test_list_services_returns_200():
    r = httpx.get(f"{BASE}/inject", timeout=10)
    assert r.status_code == 200


def test_list_services_response_shape():
    r = httpx.get(f"{BASE}/inject", timeout=10)
    data = r.json()
    assert data["status"] == "ok"
    assert isinstance(data["services"], list)
    assert len(data["services"]) > 0


def test_list_services_contains_known_services():
    r = httpx.get(f"{BASE}/inject", timeout=10)
    listed = set(r.json()["services"])
    for svc in KNOWN_SERVICES:
        assert svc in listed, f"Expected {svc!r} in service list"


# ---------------------------------------------------------------------------
# /inject/{service} — JSON format (default)
# ---------------------------------------------------------------------------

def test_inject_unknown_service_returns_404():
    r = httpx.get(f"{BASE}/inject/__no_such_service__", timeout=10)
    assert r.status_code == 404


def test_inject_invalid_format_returns_400():
    r = httpx.get(f"{BASE}/inject/litellm?format=xml", timeout=10)
    assert r.status_code == 400


def test_inject_litellm_returns_200():
    r = httpx.get(f"{BASE}/inject/litellm", timeout=30)
    assert r.status_code == 200


def test_inject_json_response_shape():
    r = httpx.get(f"{BASE}/inject/litellm", timeout=30)
    data = r.json()
    assert data["status"] == "ok"
    assert data["service"] == "litellm"
    assert isinstance(data["credentials"], dict)
    assert "LITELLM_API_KEY" in data["credentials"]


def test_inject_litellm_api_key_nonempty():
    r = httpx.get(f"{BASE}/inject/litellm", timeout=30)
    creds = r.json()["credentials"]
    assert creds.get("LITELLM_API_KEY"), "LITELLM_API_KEY should not be empty"


def test_inject_n8n_api_key_present():
    r = httpx.get(f"{BASE}/inject/n8n", timeout=30)
    assert r.status_code == 200
    creds = r.json()["credentials"]
    assert "N8N_API_KEY" in creds


def test_inject_postgres_returns_both_fields():
    r = httpx.get(f"{BASE}/inject/postgres", timeout=30)
    assert r.status_code == 200
    creds = r.json()["credentials"]
    assert "POSTGRES_PASSWORD" in creds
    assert "POSTGRES_USER" in creds


# ---------------------------------------------------------------------------
# /inject/{service}?format=shell
# ---------------------------------------------------------------------------

def test_inject_shell_format_content_type():
    r = httpx.get(f"{BASE}/inject/litellm?format=shell", timeout=30)
    assert r.status_code == 200
    assert "text/plain" in r.headers.get("content-type", "")


def test_inject_shell_format_contains_export():
    r = httpx.get(f"{BASE}/inject/litellm?format=shell", timeout=30)
    assert "export LITELLM_API_KEY=" in r.text


# ---------------------------------------------------------------------------
# /inject/{service}?format=dotenv
# ---------------------------------------------------------------------------

def test_inject_dotenv_format_no_export():
    r = httpx.get(f"{BASE}/inject/litellm?format=dotenv", timeout=30)
    assert r.status_code == 200
    assert "export" not in r.text
    assert "LITELLM_API_KEY=" in r.text
