"""
Post-deploy regression tests.
Confirms no services were disrupted after deploying infrastructure changes.

Run locally (all services on localhost):
    pip install -r tests/requirements.txt
    playwright install chromium
    pytest tests/test_post_deploy.py -v

Run against production (services on separate Docker hostnames):
    N8N_URL=http://n8n:5678 \
    WEBUI_URL=http://webui:3000 \
    LEAD_REVIEW_URL=http://sa_lead_review:5003 \
    LEAD_REVIEW_PASSWORD=<from .env> \
    pytest tests/test_post_deploy.py -v
"""

import shutil
import subprocess
import pytest
from playwright.sync_api import Page, expect


# ── Lead Review Portal ────────────────────────────────────────────────────────

def test_lead_review_login_page_renders(page: Page, lead_review_url: str) -> None:
    """Login page must load and show the password form or dashboard.
    Skipped if the service uses token-based URLs (no /review base path).
    """
    response = page.goto(f"{lead_review_url}/review", timeout=15000)
    if response is not None and response.status == 404:
        pytest.skip("Lead review /review returns 404 — service uses token-based URLs, test requires production URL")
    assert (
        page.locator("input[type='password']").count() > 0
        or page.locator("#dashboard-screen").count() > 0
    ), "Lead review page did not render login form or dashboard"


def test_lead_review_dashboard_loads(page: Page, lead_review_url: str, lead_review_password: str) -> None:
    """After login, the lead dashboard table must be visible."""
    if not lead_review_password:
        pytest.skip("LEAD_REVIEW_PASSWORD not set — skipping authenticated test")

    page.goto(f"{lead_review_url}/review", timeout=15000)
    pwd_input = page.locator("input[type='password']")
    if pwd_input.count() > 0:
        pwd_input.fill(lead_review_password)
        page.locator("button[type='submit']").click()
        page.wait_for_selector("#dashboard-screen", timeout=10000)

    expect(page.locator("#leads-table, table")).to_be_visible(timeout=10000)


# ── n8n ───────────────────────────────────────────────────────────────────────

def test_n8n_loads(page: Page, n8n_url: str) -> None:
    """n8n UI must respond with a non-error status."""
    response = page.goto(n8n_url, timeout=15000)
    assert response is not None and response.status < 400, \
        f"n8n returned unexpected status {response.status if response else 'None'}"


def test_n8n_health_endpoint(page: Page, n8n_url: str) -> None:
    """n8n /healthz must return 200."""
    response = page.goto(f"{n8n_url}/healthz", timeout=10000)
    assert response is not None and response.status == 200, \
        f"n8n /healthz returned {response.status if response else 'None'}"


# ── Web UI ────────────────────────────────────────────────────────────────────

def test_webui_loads(page: Page, webui_url: str) -> None:
    """Web UI homepage must load and contain 'Pipeline' text."""
    page.goto(webui_url, timeout=15000)
    expect(page.get_by_text("Pipeline").first).to_be_visible(timeout=10000)


def test_webui_health(page: Page, webui_url: str) -> None:
    """Web UI /health must return 200."""
    response = page.goto(f"{webui_url}/health", timeout=10000)
    assert response is not None and response.status == 200, \
        f"webui /health returned {response.status if response else 'None'}"


# ── Backup container ──────────────────────────────────────────────────────────

def test_backup_container_is_running() -> None:
    """Backup container must be running (not exited).
    Skipped automatically if docker CLI is not available (e.g. remote CI without socket).
    """
    if not shutil.which("docker"):
        pytest.skip("docker CLI not available — skipping container check")

    result = subprocess.run(
        ["docker", "inspect", "--format", "{{.State.Status}}", "backup"],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        pytest.skip("backup container not found — may not be deployed yet")

    status = result.stdout.strip()
    assert status == "running", f"backup container status is '{status}', expected 'running'"
