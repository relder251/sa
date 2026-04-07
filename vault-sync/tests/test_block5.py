"""
CRED-05T — Block 5 tests: container migration validation.

Verifies that:
1. All CRED-05 services are healthy after vault_sync dependency is added
2. The vault-inject entrypoint script is reachable in injected containers
3. Credential injection (format=shell) produces valid shell output
4. Inject endpoint is idempotent (safe to call multiple times)

Set VAULT_SYNC_URL to override (default: http://vault-sync:8777).
Docker socket access required for container health checks.
"""

import os
import subprocess
import pytest
import httpx

BASE = os.environ.get("VAULT_SYNC_URL", "http://localhost:8777")

# Services that depend on vault_sync per CRED-05
INJECTED_SERVICES = ["free-model-sync", "test-runner"]
VAULT_DEPENDENT = [
    "n8n", "litellm", "free_model_sync", "test_runner",
    "pipeline_server", "webui", "jupyter", "portal",
]


# ---------------------------------------------------------------------------
# vault-sync itself is healthy (prerequisite)
# ---------------------------------------------------------------------------

def test_vault_sync_health():
    r = httpx.get(f"{BASE}/health", timeout=10)
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


# ---------------------------------------------------------------------------
# Dependent containers are all running and healthy
# ---------------------------------------------------------------------------

def _container_status(name: str) -> str:
    """Return docker inspect .State.Health.Status or .State.Status."""
    try:
        result = subprocess.run(
            ["docker", "inspect", "--format",
             "{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}",
             name],
            capture_output=True, text=True, check=True,
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError:
        return "not_found"


@pytest.mark.parametrize("container", VAULT_DEPENDENT)
def test_container_is_healthy(container):
    status = _container_status(container)
    assert status in ("healthy", "running"), (
        f"Container {container!r} is not healthy: {status!r}"
    )


# ---------------------------------------------------------------------------
# Inject endpoint works for all registry services
# ---------------------------------------------------------------------------

def test_inject_all_services_return_200():
    r = httpx.get(f"{BASE}/inject", timeout=10)
    services = r.json()["services"]
    for svc in services:
        resp = httpx.get(f"{BASE}/inject/{svc}", timeout=30)
        assert resp.status_code == 200, f"inject/{svc} returned {resp.status_code}: {resp.text}"


def test_inject_shell_format_is_valid_shell():
    """Shell output should be parseable export statements."""
    r = httpx.get(f"{BASE}/inject/litellm?format=shell", timeout=30)
    assert r.status_code == 200
    for line in r.text.strip().splitlines():
        assert line.startswith("export "), f"Unexpected line in shell output: {line!r}"
        assert "=" in line


def test_inject_dotenv_format_is_valid():
    """Dotenv output should be KEY=value lines."""
    r = httpx.get(f"{BASE}/inject/litellm?format=dotenv", timeout=30)
    assert r.status_code == 200
    for line in r.text.strip().splitlines():
        assert "=" in line
        assert not line.startswith("export ")


def test_inject_idempotent():
    """Two consecutive inject calls return identical credential sets."""
    r1 = httpx.get(f"{BASE}/inject/litellm", timeout=30).json()["credentials"]
    r2 = httpx.get(f"{BASE}/inject/litellm", timeout=30).json()["credentials"]
    assert r1 == r2


# ---------------------------------------------------------------------------
# Entrypoint script is present in injected containers
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("container", ["free_model_sync", "test_runner"])
def test_entrypoint_script_exists_in_container(container):
    result = subprocess.run(
        ["docker", "exec", container, "test", "-f", "/vault-entrypoint.sh"],
        capture_output=True,
    )
    assert result.returncode == 0, f"/vault-entrypoint.sh not found in {container}"


@pytest.mark.parametrize("container", ["free_model_sync", "test_runner"])
def test_entrypoint_env_vars_injected(container):
    """VAULT_INJECT_SERVICE should be set inside the container."""
    result = subprocess.run(
        ["docker", "exec", container, "sh", "-c", "echo $VAULT_INJECT_SERVICE"],
        capture_output=True, text=True,
    )
    assert result.stdout.strip() == "litellm", (
        f"VAULT_INJECT_SERVICE not set in {container}: {result.stdout!r}"
    )
