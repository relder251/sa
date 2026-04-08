"""
Portal service-status indicator tests — Task #29 (Order 1)

Tests the live green/red/unknown status dots on portal service cards.
Covers:
  - /api/service-status endpoint returns valid Prometheus data
  - Green dot shown when container is healthy (seen within 90s)
  - Red dot shown when container is absent from Prometheus
  - Amber dot shown when container data is stale (>90s old)
  - Gray dot shown for external services (no terminal field)
  - Status refreshes without page reload (30s interval)
  - Regression: favorites, filters, API badges, card rendering still work

Run against portal container directly (bypasses oauth2_proxy):
    PORTAL_URL=http://172.20.0.9 pytest tests/test_portal_status.py -v
"""

import json
import math
import time
import os
import pytest
from playwright.sync_api import Page, Route, expect


PORTAL_URL = os.environ.get("PORTAL_URL", "http://172.20.0.9")


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def portal_page(page: Page) -> Page:
    """Navigate to portal and wait for cards to render."""
    page.goto(PORTAL_URL, wait_until="networkidle", timeout=15000)
    page.wait_for_selector(".card", timeout=10000)
    return page


def make_prometheus_response(containers: list[dict]) -> dict:
    """Build a Prometheus API v1 vector response for container_last_seen."""
    now = math.floor(time.time())
    return {
        "status": "success",
        "data": {
            "resultType": "vector",
            "result": [
                {
                    "metric": {
                        "__name__": "container_last_seen",
                        "name": c["name"],
                        "instance": "cadvisor:8080",
                        "job": "cadvisor",
                    },
                    "value": [now, str(c.get("last_seen", now))],
                }
                for c in containers
            ],
        },
    }


def mock_status(page: Page, containers: list[dict]) -> None:
    """Intercept /api/service-status and return a controlled response."""
    body = json.dumps(make_prometheus_response(containers))

    def handle(route: Route) -> None:
        route.fulfill(status=200, content_type="application/json", body=body)

    page.route("**/api/service-status", handle)


# ── /api/service-status endpoint tests ───────────────────────────────────────

def test_service_status_endpoint_returns_200(page: Page) -> None:
    """/api/service-status must return HTTP 200 with Prometheus JSON."""
    response = page.request.get(f"{PORTAL_URL}/api/service-status")
    assert response.status == 200, f"Expected 200, got {response.status}"
    data = response.json()
    assert data["status"] == "success"
    assert data["data"]["resultType"] == "vector"


def test_service_status_returns_container_names(page: Page) -> None:
    """Response must include named Docker containers (not just cgroup system slices)."""
    response = page.request.get(f"{PORTAL_URL}/api/service-status")
    data = response.json()
    names = [r["metric"].get("name", "") for r in data["data"]["result"]]
    named = [n for n in names if n]
    assert len(named) > 5, f"Expected >5 named containers, got {named}"


def test_service_status_cache_control(page: Page) -> None:
    """Response must carry Cache-Control: no-store to prevent stale data."""
    response = page.request.get(f"{PORTAL_URL}/api/service-status")
    cc = response.headers.get("cache-control", "")
    assert "no-store" in cc, f"Expected no-store in Cache-Control, got: {cc}"


def test_service_status_includes_known_containers(page: Page) -> None:
    """Containers defined in services.json with terminal field must appear in Prometheus."""
    response = page.request.get(f"{PORTAL_URL}/api/service-status")
    data = response.json()
    names = {r["metric"].get("name", "") for r in data["data"]["result"]}
    # These are stable core services that should always be running
    expected = {"prometheus", "grafana", "n8n", "litellm"}
    missing = expected - names
    assert not missing, f"Expected containers missing from Prometheus: {missing}"


# ── Status dot rendering tests ────────────────────────────────────────────────

def test_green_dot_for_healthy_container(page: Page) -> None:
    """Cards with terminal field and fresh container_last_seen show green (no extra class)."""
    now = math.floor(time.time())
    mock_status(page, [
        {"name": "n8n", "last_seen": now - 10},       # healthy: 10s ago
        {"name": "prometheus", "last_seen": now - 5},
    ])
    page.goto(PORTAL_URL, wait_until="networkidle", timeout=15000)
    page.wait_for_timeout(1500)  # allow updateServiceStatus() to fire

    n8n_card = page.locator('.card[data-id="n8n"]')
    expect(n8n_card).to_be_visible()
    dot = n8n_card.locator(".live-dot")
    # Green: no down/stale/unknown class
    dot_class = dot.get_attribute("class")
    assert "down" not in dot_class, f"n8n dot should not be down, got class: {dot_class}"
    assert "stale" not in dot_class, f"n8n dot should not be stale, got class: {dot_class}"
    assert "unknown" not in dot_class, f"n8n dot should not be unknown, got class: {dot_class}"


def test_green_dot_text_shows_live(page: Page) -> None:
    """Status text beside green dot must read 'live'."""
    now = math.floor(time.time())
    mock_status(page, [{"name": "n8n", "last_seen": now - 10}])
    page.goto(PORTAL_URL, wait_until="networkidle", timeout=15000)
    page.wait_for_timeout(1500)

    status_span = page.locator('.card[data-id="n8n"] .card-status')
    expect(status_span).to_contain_text("live")


def test_red_dot_for_absent_container(page: Page) -> None:
    """Cards whose container name is absent from Prometheus show red (down class)."""
    # Only return prometheus — n8n is absent
    now = math.floor(time.time())
    mock_status(page, [{"name": "prometheus", "last_seen": now - 5}])
    page.goto(PORTAL_URL, wait_until="networkidle", timeout=15000)
    page.wait_for_timeout(1500)

    n8n_card = page.locator('.card[data-id="n8n"]')
    dot = n8n_card.locator(".live-dot")
    dot_class = dot.get_attribute("class")
    assert "down" in dot_class, f"Absent container n8n dot should have 'down' class, got: {dot_class}"


def test_red_dot_text_shows_down(page: Page) -> None:
    """Status text for absent container must read 'down'."""
    now = math.floor(time.time())
    mock_status(page, [{"name": "prometheus", "last_seen": now - 5}])
    page.goto(PORTAL_URL, wait_until="networkidle", timeout=15000)
    page.wait_for_timeout(1500)

    status_span = page.locator('.card[data-id="n8n"] .card-status')
    expect(status_span).to_contain_text("down")


def test_amber_dot_for_stale_container(page: Page) -> None:
    """Container seen >90s ago shows amber (stale class)."""
    now = math.floor(time.time())
    mock_status(page, [{"name": "n8n", "last_seen": now - 120}])  # 120s ago > threshold
    page.goto(PORTAL_URL, wait_until="networkidle", timeout=15000)
    page.wait_for_timeout(1500)

    dot = page.locator('.card[data-id="n8n"] .live-dot')
    dot_class = dot.get_attribute("class")
    assert "stale" in dot_class, f"Stale container dot should have 'stale' class, got: {dot_class}"


def test_amber_dot_text_shows_stale(page: Page) -> None:
    """Status text for stale container must read 'stale'."""
    now = math.floor(time.time())
    mock_status(page, [{"name": "n8n", "last_seen": now - 120}])
    page.goto(PORTAL_URL, wait_until="networkidle", timeout=15000)
    page.wait_for_timeout(1500)

    status_span = page.locator('.card[data-id="n8n"] .card-status')
    expect(status_span).to_contain_text("stale")


def test_gray_dot_for_external_service(page: Page) -> None:
    """Services with no terminal field (external) show gray (unknown class)."""
    now = math.floor(time.time())
    # Return an empty container list — all would be down if they had terminal
    mock_status(page, [{"name": "prometheus", "last_seen": now - 5}])
    page.goto(PORTAL_URL, wait_until="networkidle", timeout=15000)
    page.wait_for_timeout(1500)

    # claude, chatgpt, gemini, grok, notion, cloudflare, twingate, hostinger have no terminal
    claude_card = page.locator('.card[data-id="claude"]')
    expect(claude_card).to_be_visible()
    dot = claude_card.locator(".live-dot")
    dot_class = dot.get_attribute("class")
    assert "unknown" in dot_class, f"External service dot should have 'unknown' class, got: {dot_class}"


def test_gray_dot_text_shows_external(page: Page) -> None:
    """Status text for external service must read 'external'."""
    now = math.floor(time.time())
    mock_status(page, [{"name": "prometheus", "last_seen": now - 5}])
    page.goto(PORTAL_URL, wait_until="networkidle", timeout=15000)
    page.wait_for_timeout(1500)

    status_span = page.locator('.card[data-id="claude"] .card-status')
    expect(status_span).to_contain_text("external")


def test_staleness_boundary_at_89s(page: Page) -> None:
    """Container seen exactly 89s ago (< 90s threshold) shows green, not stale."""
    now = math.floor(time.time())
    mock_status(page, [{"name": "n8n", "last_seen": now - 89}])
    page.goto(PORTAL_URL, wait_until="networkidle", timeout=15000)
    page.wait_for_timeout(1500)

    dot = page.locator('.card[data-id="n8n"] .live-dot')
    dot_class = dot.get_attribute("class")
    assert "stale" not in dot_class, f"89s-old should still be live, got: {dot_class}"
    assert "down" not in dot_class


def test_staleness_boundary_at_91s(page: Page) -> None:
    """Container seen exactly 91s ago (> 90s threshold) shows stale."""
    now = math.floor(time.time())
    mock_status(page, [{"name": "n8n", "last_seen": now - 91}])
    page.goto(PORTAL_URL, wait_until="networkidle", timeout=15000)
    page.wait_for_timeout(1500)

    dot = page.locator('.card[data-id="n8n"] .live-dot')
    dot_class = dot.get_attribute("class")
    assert "stale" in dot_class, f"91s-old should be stale, got: {dot_class}"


def test_all_cards_have_status_dot(portal_page: Page) -> None:
    """Every service card must have a .live-dot element."""
    cards = portal_page.locator(".card[data-id]")
    count = cards.count()
    assert count > 0

    for i in range(count):
        card = cards.nth(i)
        dot = card.locator(".live-dot")
        assert dot.count() == 1, f"Card {i} missing .live-dot"


def test_all_cards_have_card_status_span(portal_page: Page) -> None:
    """Every service card must have a .card-status span."""
    cards = portal_page.locator(".card[data-id]")
    for i in range(cards.count()):
        span = cards.nth(i).locator(".card-status")
        assert span.count() == 1, f"Card {i} missing .card-status"


# ── Auto-refresh without page reload ─────────────────────────────────────────

def test_status_updates_without_page_reload(page: Page) -> None:
    """Simulates status flip from live → down without navigating; dot updates in-place."""
    now = math.floor(time.time())
    call_count = [0]

    def dynamic_handler(route: Route) -> None:
        call_count[0] += 1
        if call_count[0] == 1:
            # First call: n8n is healthy
            body = json.dumps(make_prometheus_response([{"name": "n8n", "last_seen": now - 5}]))
        else:
            # Subsequent calls: n8n is gone (down)
            body = json.dumps(make_prometheus_response([]))
        route.fulfill(status=200, content_type="application/json", body=body)

    page.route("**/api/service-status", dynamic_handler)
    page.goto(PORTAL_URL, wait_until="networkidle", timeout=15000)
    page.wait_for_timeout(1500)

    # First state: should be live
    dot = page.locator('.card[data-id="n8n"] .live-dot')
    initial_class = dot.get_attribute("class")
    assert "down" not in initial_class, f"Should start live, got: {initial_class}"

    # Trigger another status check by evaluating the JS function directly
    page.evaluate("updateServiceStatus()")
    page.wait_for_timeout(500)

    updated_class = dot.get_attribute("class")
    assert "down" in updated_class, f"After status flip, dot should be down, got: {updated_class}"


def test_status_endpoint_failure_does_not_crash_portal(page: Page) -> None:
    """If /api/service-status returns 502, portal renders normally (no JS exception)."""
    def error_handler(route: Route) -> None:
        route.fulfill(status=502, body="Bad Gateway")

    page.route("**/api/service-status", error_handler)
    errors = []
    page.on("pageerror", lambda err: errors.append(str(err)))

    page.goto(PORTAL_URL, wait_until="networkidle", timeout=15000)
    page.wait_for_timeout(1500)

    # Portal should still render cards
    assert page.locator(".card").count() > 0
    # No unhandled JS errors from our new code
    critical = [e for e in errors if "service-status" in e.lower() or "updateServiceStatus" in e]
    assert not critical, f"Unexpected JS errors: {critical}"


# ── Regression tests — existing functionality ─────────────────────────────────

def test_portal_page_loads(page: Page) -> None:
    """Portal index renders without error."""
    response = page.goto(PORTAL_URL, wait_until="networkidle", timeout=15000)
    assert response is not None and response.status == 200


def test_portal_title(page: Page) -> None:
    """Page title matches expected portal title."""
    page.goto(PORTAL_URL, wait_until="networkidle", timeout=15000)
    assert "SA Portal" in page.title() or "Portal" in page.title()


def test_services_json_loads(page: Page) -> None:
    """/services.json must return 200 and valid JSON with services array."""
    response = page.request.get(f"{PORTAL_URL}/services.json")
    assert response.status == 200
    data = response.json()
    assert "services" in data
    assert len(data["services"]) > 0


def test_cards_render_from_services_json(portal_page: Page) -> None:
    """Card count matches number of services in services.json."""
    response = portal_page.request.get(f"{PORTAL_URL}/services.json")
    services = response.json()["services"]
    cards = portal_page.locator(".card[data-id]")
    assert cards.count() == len(services), \
        f"Expected {len(services)} cards, got {cards.count()}"


def test_card_has_name_icon_and_category_tag(portal_page: Page) -> None:
    """Each card renders icon, name, and category tag."""
    n8n_card = portal_page.locator('.card[data-id="n8n"]')
    expect(n8n_card.locator(".card-icon")).to_be_visible()
    expect(n8n_card.locator(".card-name")).to_be_visible()
    expect(n8n_card.locator(".card-tag")).to_be_visible()


def test_favorite_star_button_present(portal_page: Page) -> None:
    """Each card has a star/favorite toggle button."""
    cards = portal_page.locator(".card[data-id]")
    for i in range(min(3, cards.count())):
        star = cards.nth(i).locator(".star-btn")
        assert star.count() == 1, f"Card {i} missing star button"


def test_favorites_section_exists(portal_page: Page) -> None:
    """Favorites sidebar section exists in the DOM."""
    expect(portal_page.locator("#btn-favorites")).to_be_visible()


def test_filter_buttons_render(portal_page: Page) -> None:
    """Category filter buttons render in sidebar."""
    assert portal_page.locator(".filter-btn").count() > 0


def test_filter_hides_cards_from_other_categories(portal_page: Page) -> None:
    """Clicking a filter shows only cards matching that category."""
    # Click 'automation' category filter
    automation_btn = portal_page.locator('[data-sidebar-cat="automation"]')
    if automation_btn.count() == 0:
        pytest.skip("No automation filter button found")
    automation_btn.click()
    portal_page.wait_for_timeout(300)

    visible_cards = portal_page.locator('.card[data-id]:not(.hidden)')
    count = visible_cards.count()
    for i in range(count):
        cat = visible_cards.nth(i).get_attribute("data-category")
        assert cat == "automation", f"After filter, card {i} has category '{cat}', expected 'automation'"


def test_filter_all_shows_all_cards(portal_page: Page) -> None:
    """'All' filter button restores all cards."""
    # First apply a filter
    automation_btn = portal_page.locator('[data-sidebar-cat="automation"]')
    if automation_btn.count() > 0:
        automation_btn.click()
        portal_page.wait_for_timeout(200)

    all_btn = portal_page.locator('[data-sidebar-cat="all"]')
    if all_btn.count() == 0:
        pytest.skip("No 'all' filter button")
    all_btn.click()
    portal_page.wait_for_timeout(300)

    response = portal_page.request.get(f"{PORTAL_URL}/services.json")
    total = len(response.json()["services"])
    visible = portal_page.locator('.card[data-id]:not(.hidden)').count()
    assert visible == total, f"Expected {total} cards after 'All' filter, got {visible}"


def test_docker_containers_endpoint(page: Page) -> None:
    """/api/docker/containers must return a JSON array of running containers."""
    response = page.request.get(f"{PORTAL_URL}/api/docker/containers")
    assert response.status == 200
    containers = response.json()
    assert isinstance(containers, list)
    assert len(containers) > 0
    # Verify structure
    assert "Names" in containers[0]
    assert "State" in containers[0]


def test_ssh_button_renders_for_terminal_services(portal_page: Page) -> None:
    """Services with terminal field show an SSH quick-launch button."""
    n8n_card = portal_page.locator('.card[data-id="n8n"]')
    ssh_btn = n8n_card.locator(".card-ssh")
    assert ssh_btn.count() == 1, "n8n card missing SSH button"


def test_external_services_have_no_ssh_button(portal_page: Page) -> None:
    """Services without terminal field (external) must NOT have an SSH button."""
    claude_card = portal_page.locator('.card[data-id="claude"]')
    ssh_btn = claude_card.locator(".card-ssh")
    assert ssh_btn.count() == 0, "claude (external) should not have SSH button"


def test_card_links_open_correct_url(portal_page: Page) -> None:
    """Card href must match service URL from services.json."""
    response = portal_page.request.get(f"{PORTAL_URL}/services.json")
    services = {s["id"]: s for s in response.json()["services"]}

    n8n_card = portal_page.locator('.card[data-id="n8n"]')
    href = n8n_card.get_attribute("href")
    assert href == services["n8n"]["url"], f"n8n card href mismatch: {href}"


def test_topbar_status_indicator_visible(portal_page: Page) -> None:
    """Topbar 'All systems operational' status indicator still visible."""
    expect(portal_page.locator(".status-indicator")).to_be_visible()
    expect(portal_page.locator(".status-dot")).to_be_visible()


def test_stack_advisor_chat_button_visible(portal_page: Page) -> None:
    """Stack Advisor FAB button is visible."""
    expect(portal_page.locator("#chat-fab")).to_be_visible()


def test_search_input_filters_cards(portal_page: Page) -> None:
    """Search input filters cards by name."""
    search = portal_page.locator('input[type="search"], input[placeholder*="search"], input[placeholder*="Search"]')
    if search.count() == 0:
        pytest.skip("No search input found")
    search.first.fill("n8n")
    portal_page.wait_for_timeout(400)
    visible = portal_page.locator('.card[data-id]:not(.hidden)')
    assert visible.count() >= 1
    for i in range(visible.count()):
        name = visible.nth(i).locator(".card-name").text_content() or ""
        assert "n8n" in name.lower() or "automation" in name.lower(), \
            f"Search 'n8n' returned unexpected card: {name}"
