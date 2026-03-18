import os
import pytest
from playwright.sync_api import Browser


BASE_URL = os.environ.get("BASE_URL", "http://localhost")
LEAD_REVIEW_PASSWORD = os.environ.get("LEAD_REVIEW_PASSWORD", "")

# Per-service URL overrides — default to BASE_URL:PORT pattern.
# Set these when services run on separate Docker hostnames (e.g. prod).
N8N_URL = os.environ.get("N8N_URL", f"{BASE_URL.rstrip('/')}:5678")
WEBUI_URL = os.environ.get("WEBUI_URL", f"{BASE_URL.rstrip('/')}:3000")
LEAD_REVIEW_URL = os.environ.get("LEAD_REVIEW_URL", f"{BASE_URL.rstrip('/')}:5003")


@pytest.fixture(scope="session")
def base_url() -> str:
    return BASE_URL.rstrip("/")


@pytest.fixture(scope="session")
def n8n_url() -> str:
    return N8N_URL.rstrip("/")


@pytest.fixture(scope="session")
def webui_url() -> str:
    return WEBUI_URL.rstrip("/")


@pytest.fixture(scope="session")
def lead_review_url() -> str:
    return LEAD_REVIEW_URL.rstrip("/")


@pytest.fixture(scope="session")
def lead_review_password() -> str:
    return LEAD_REVIEW_PASSWORD


@pytest.fixture(scope="function")
def page(browser: Browser):
    """Fresh browser context per test — prevents sessionStorage/cookie bleed between tests."""
    context = browser.new_context()
    pg = context.new_page()
    yield pg
    context.close()
