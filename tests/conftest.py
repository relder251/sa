import os
import pytest


BASE_URL = os.environ.get("BASE_URL", "http://localhost")
LEAD_REVIEW_PASSWORD = os.environ.get("LEAD_REVIEW_PASSWORD", "")


@pytest.fixture(scope="session")
def base_url() -> str:
    return BASE_URL.rstrip("/")


@pytest.fixture(scope="session")
def lead_review_password() -> str:
    return LEAD_REVIEW_PASSWORD
