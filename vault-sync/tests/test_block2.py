"""
CRED-02T — Block 2 tests: credential taxonomy, collection CRUD, item migration.

Runs against the live vault-sync container.
Set VAULT_SYNC_URL to override (default: http://localhost:8777).
"""

import os
import pytest
import httpx

BASE = os.environ.get("VAULT_SYNC_URL", "http://localhost:8777")
TEST_ITEM = "__cred02t_test_item__"


# ---------------------------------------------------------------------------
# Taxonomy validation
# ---------------------------------------------------------------------------

def test_invalid_collection_returns_400():
    r = httpx.get(f"{BASE}/credentials/invalid-collection", timeout=10)
    assert r.status_code == 400


def test_valid_collections_return_200():
    for col in ("user-credentials", "system-credentials", "provider-credentials"):
        r = httpx.get(f"{BASE}/credentials/{col}", timeout=30)
        assert r.status_code == 200, f"Failed for {col}: {r.text}"
        data = r.json()
        assert data["collection"] == col
        assert isinstance(data["items"], list)


# ---------------------------------------------------------------------------
# Collection CRUD
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def cleanup():
    """Remove the test item before and after each test."""
    httpx.delete(f"{BASE}/credentials/system-credentials/{TEST_ITEM}", timeout=30)
    yield
    httpx.delete(f"{BASE}/credentials/system-credentials/{TEST_ITEM}", timeout=30)


def test_create_in_collection():
    r = httpx.post(
        f"{BASE}/credentials/system-credentials",
        json={"name": TEST_ITEM, "password": "test-pass-123", "service_tags": ["test"]},
        timeout=30,
    )
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert data["collection"] == "system-credentials"


def test_item_appears_in_collection_list():
    httpx.post(
        f"{BASE}/credentials/system-credentials",
        json={"name": TEST_ITEM, "password": "test-pass-123", "service_tags": ["test"]},
        timeout=30,
    )
    r = httpx.get(f"{BASE}/credentials/system-credentials", timeout=30)
    names = [i["name"] for i in r.json()["items"]]
    assert TEST_ITEM in names


def test_item_absent_from_other_collections():
    httpx.post(
        f"{BASE}/credentials/system-credentials",
        json={"name": TEST_ITEM, "password": "test-pass-123"},
        timeout=30,
    )
    for col in ("user-credentials", "provider-credentials"):
        r = httpx.get(f"{BASE}/credentials/{col}", timeout=30)
        names = [i["name"] for i in r.json()["items"]]
        assert TEST_ITEM not in names, f"Item unexpectedly in {col}"


def test_update_credential_in_collection():
    httpx.post(
        f"{BASE}/credentials/system-credentials",
        json={"name": TEST_ITEM, "password": "test-pass-123"},
        timeout=30,
    )
    r = httpx.put(
        f"{BASE}/credentials/system-credentials/{TEST_ITEM}",
        json={"password": "updated-pass-456"},
        timeout=30,
    )
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_delete_credential_from_collection():
    httpx.post(
        f"{BASE}/credentials/system-credentials",
        json={"name": TEST_ITEM, "password": "test-pass-123"},
        timeout=30,
    )
    r = httpx.delete(f"{BASE}/credentials/system-credentials/{TEST_ITEM}", timeout=30)
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_delete_nonexistent_returns_404():
    r = httpx.delete(
        f"{BASE}/credentials/system-credentials/__nonexistent_xyz__",
        timeout=30,
    )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Item migration
# ---------------------------------------------------------------------------

def test_migrate_taxonomy_runs_ok():
    r = httpx.post(f"{BASE}/credentials/migrate", timeout=120)
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert len(data["errors"]) == 0, f"Migration errors: {data['errors']}"


def test_known_items_tagged_after_migration():
    httpx.post(f"{BASE}/credentials/migrate", timeout=120)

    # Verify some known items appear in the right collection
    expected = {
        "system-credentials": ["LiteLLM Master API Key", "JupyterLab Token"],
        "user-credentials":   ["Keycloak SSO"],
    }
    for col, names in expected.items():
        r = httpx.get(f"{BASE}/credentials/{col}", timeout=30)
        tagged = [i["name"] for i in r.json()["items"]]
        for name in names:
            assert name in tagged, f"Expected {name!r} in {col}, got: {tagged}"
