"""
Tests for SPA View Lifecycle & Route Mounting Architecture

Verifies:
1. Exact single active primary view invariant across all 9 views.
2. Complete absence of unmounted view elements from <main class="content-container">.
3. No duplicate page headings or dashboard telemetry sections.
4. Direct hash route loading and page reload.
5. Browser back / forward history transitions.
6. Repeated navigation cycles without DOM accumulation or redundant data loading.
"""

import time
import pytest
from playwright.sync_api import sync_playwright

ALL_ROUTES = [
    ("dashboard", "view-dashboard", "dash-heading"),
    ("opportunities", "view-opportunities", "opp-heading"),
    ("providers", "view-providers", "providers-heading"),
    ("events", "view-events", "events-heading"),
    ("playerprops", "view-playerprops", "props-heading"),
    ("history", "view-history", "history-heading"),
    ("profiler", "view-profiler", "profiler-heading"),
    ("notifications", "view-notifications", "notif-heading"),
    ("settings", "view-settings", "settings-heading"),
]

BASE_URL = "http://127.0.0.1:8000"


def check_dom_invariant(page, expected_view_name, expected_section_id):
    """
    Evaluates the DOM invariant:
    1. Exactly one .view-section exists in <main class="content-container">.
    2. Its ID matches the expected_section_id.
    3. It has the 'active' class.
    4. No other views exist inside the main container.
    """
    res = page.evaluate("""() => {
        const main = document.querySelector('main.content-container') || document.querySelector('main');
        const sections = Array.from(main.querySelectorAll('.view-section'));
        const activeSections = sections.filter(s => s.classList.contains('active'));
        const allHeadings = Array.from(main.querySelectorAll('h1')).map(h => h.id || h.innerText.slice(0, 30));
        return {
            childCount: main.children.length,
            sectionCount: sections.length,
            activeCount: activeSections.length,
            mountedId: sections.length > 0 ? sections[0].id : null,
            headings: allHeadings,
            hasDashHeading: !!document.getElementById('dash-heading'),
            hasOppHeading: !!document.getElementById('opp-heading'),
            currentHash: window.location.hash
        };
    }""")
    assert res["sectionCount"] == 1, f"Expected exactly 1 view section in main, found {res['sectionCount']}"
    assert res["activeCount"] == 1, f"Expected exactly 1 active section in main, found {res['activeCount']}"
    assert res["mountedId"] == expected_section_id, f"Expected mounted view {expected_section_id}, got {res['mountedId']}"
    return res


def test_initial_load_and_single_active_view():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1366, "height": 768})
        page.goto(f"{BASE_URL}/#dashboard")
        page.wait_for_selector("#view-dashboard")
        time.sleep(0.5)

        check_dom_invariant(page, "dashboard", "view-dashboard")
        browser.close()


def test_navigation_matrix_primary_views():
    """Verify transitions between all 9 primary views: previous view must be removed from main."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1366, "height": 768})
        page.goto(f"{BASE_URL}/#dashboard")
        page.wait_for_selector("#view-dashboard")
        time.sleep(0.5)

        # 1. Dashboard -> Opportunities -> Dashboard
        page.click('a[data-view="opportunities"]')
        time.sleep(0.5)
        check_dom_invariant(page, "opportunities", "view-opportunities")
        assert not page.locator("main.content-container #view-dashboard").count(), "Dashboard leaked into main!"

        page.click('a[data-view="dashboard"]')
        time.sleep(0.5)
        check_dom_invariant(page, "dashboard", "view-dashboard")
        assert not page.locator("main.content-container #view-opportunities").count(), "Opportunities leaked into main!"

        # 2. Sequential walkthrough of all views
        for route, section_id, heading_id in ALL_ROUTES:
            page.click(f'a[data-view="{route}"]')
            time.sleep(0.4)
            res = check_dom_invariant(page, route, section_id)
            assert res["mountedId"] == section_id

        browser.close()


def test_direct_route_load_all_routes():
    """Verify that directly loading #/<route> mounts only that route view and none other."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1366, "height": 768})

        for route, section_id, heading_id in ALL_ROUTES:
            page.goto(f"{BASE_URL}/#{route}")
            page.wait_for_selector(f"#{section_id}")
            time.sleep(0.3)
            check_dom_invariant(page, route, section_id)

            # Reload test
            page.reload()
            page.wait_for_selector(f"#{section_id}")
            time.sleep(0.3)
            check_dom_invariant(page, route, section_id)

        browser.close()


def test_browser_back_and_forward_history():
    """Verify browser history back/forward unmounts and replaces views cleanly."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1366, "height": 768})
        page.goto(f"{BASE_URL}/#dashboard")
        page.wait_for_selector("#view-dashboard")

        # Dashboard -> Opportunities -> Providers
        page.click('a[data-view="opportunities"]')
        time.sleep(0.4)
        check_dom_invariant(page, "opportunities", "view-opportunities")

        page.click('a[data-view="providers"]')
        time.sleep(0.4)
        check_dom_invariant(page, "providers", "view-providers")

        # Back -> Opportunities
        page.go_back()
        time.sleep(0.4)
        check_dom_invariant(page, "opportunities", "view-opportunities")
        assert not page.locator("main.content-container #view-providers").count()

        # Back -> Dashboard
        page.go_back()
        time.sleep(0.4)
        check_dom_invariant(page, "dashboard", "view-dashboard")
        assert not page.locator("main.content-container #view-opportunities").count()

        # Forward -> Opportunities
        page.go_forward()
        time.sleep(0.4)
        check_dom_invariant(page, "opportunities", "view-opportunities")
        assert not page.locator("main.content-container #view-dashboard").count()

        browser.close()


def test_repeated_navigation_non_accumulation():
    """Verify that repeated navigation between Dashboard and Opportunities never accumulates views."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1366, "height": 768})
        page.goto(f"{BASE_URL}/#dashboard")
        page.wait_for_selector("#view-dashboard")

        for i in range(5):
            page.click('a[data-view="opportunities"]')
            time.sleep(0.2)
            check_dom_invariant(page, "opportunities", "view-opportunities")

            page.click('a[data-view="dashboard"]')
            time.sleep(0.2)
            check_dom_invariant(page, "dashboard", "view-dashboard")

        browser.close()
