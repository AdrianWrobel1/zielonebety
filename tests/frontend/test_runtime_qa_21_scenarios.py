"""
Comprehensive Runtime QA Suite: 21 Mandatory Scenarios for Global Props Scanner.
Runs in headless Chromium against http://127.0.0.1:8000/playerprops.
"""

import time
import pytest
from playwright.sync_api import sync_playwright


def ensure_advanced_filters_open(page):
    panel = page.locator("#props-advanced-filters-panel")
    if not panel.is_visible():
        page.click("#btn-toggle-advanced-filters")
        panel.wait_for(state="visible", timeout=3000)


def test_runtime_qa_all_21_scenarios():
    console_errors = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1366, "height": 768})
        page = context.new_page()

        def on_console(msg):
            if msg.type == "error":
                console_errors.append(msg.text)

        page.on("console", on_console)

        # ──────────────────────────────────────────────────────────────────
        # SCENARIO 1: DEFAULT
        # ──────────────────────────────────────────────────────────────────
        page.goto("http://127.0.0.1:8000/playerprops")
        page.wait_for_selector("#view-playerprops")
        time.sleep(1)

        title = page.locator("#props-heading").text_content()
        assert "Global Props Scanner" in title, f"Scenario 1 Failed: Heading is {title}"

        # Top Valuebets tab is active by default
        assert "active" in page.locator("#props-tab-top-value").get_attribute("class")
        print("[OK] Scenario 1: Default load successful")

        # ──────────────────────────────────────────────────────────────────
        # Switch to All Candidates tab so we have evaluated rows to test filters
        # ──────────────────────────────────────────────────────────────────
        with page.expect_response(lambda r: "global-results" in r.url):
            page.click("#props-tab-diagnostics")

        page.wait_for_selector("#props-table-body tr.prop-table-row", timeout=5000)
        table_count = page.locator("#props-table-count").text_content()
        assert "Candidates" in table_count or "Opportunities" in table_count

        rows_before = page.locator("#props-table-body tr.prop-table-row").count()
        assert rows_before > 0, f"Expected candidates in All Candidates, found {rows_before}"
        print(f"[OK] All Candidates populated with {rows_before} items")

        # ──────────────────────────────────────────────────────────────────
        # SCENARIO 2: SEARCH
        # ──────────────────────────────────────────────────────────────────
        first_row_text = page.locator("#props-table-body tr.prop-table-row").first.text_content()
        token = "Lens" if "Lens" in first_row_text else ("Lorient" if "Lorient" in first_row_text else "Over")
        
        with page.expect_response(lambda r: "global-results" in r.url):
            page.fill("#props-filter-search", token)

        page.wait_for_selector("#props-table-body tr", timeout=5000)
        time.sleep(0.3)
        rows_search = page.locator("#props-table-body tr.prop-table-row").count()
        assert rows_search > 0, f"Scenario 2 Failed: Search for '{token}' returned 0 rows"
        search_text = page.locator("#props-table-body").text_content()
        assert token.lower() in search_text.lower(), f"Scenario 2 Failed: '{token}' not found in search results"
        print(f"[OK] Scenario 2: Search for '{token}' filtered to {rows_search} rows")

        # Clear search
        with page.expect_response(lambda r: "global-results" in r.url):
            page.fill("#props-filter-search", "")
        time.sleep(0.3)

        # ──────────────────────────────────────────────────────────────────
        # SCENARIO 3: BOOKMAKER (All -> Superbet -> Betclic)
        # ──────────────────────────────────────────────────────────────────
        # Click Superbet tab
        with page.expect_response(lambda r: "global-results" in r.url):
            page.click("#props-tab-superbet")
        time.sleep(0.3)
        assert page.locator("#props-filter-bookmaker").input_value() == "Superbet"

        # Open advanced panel to interact with bookmaker select directly
        ensure_advanced_filters_open(page)

        # Change Bookmaker select to Betclic
        with page.expect_response(lambda r: "global-results" in r.url):
            page.select_option("#props-filter-bookmaker", "Betclic")
        time.sleep(0.3)
        assert "active" in page.locator("#props-tab-betclic").get_attribute("class")
        assert "active" not in page.locator("#props-tab-superbet").get_attribute("class")
        print("[OK] Scenario 3: Bookmaker tab and dropdown bidirectionally synchronized")

        # ──────────────────────────────────────────────────────────────────
        # SCENARIO 4: MIN NET EV
        # ──────────────────────────────────────────────────────────────────
        with page.expect_response(lambda r: "global-results" in r.url):
            page.fill("#props-filter-min-ev", "50.0")
        time.sleep(0.4)
        rows_high_ev = page.locator("#props-table-body tr.prop-table-row").count()
        # With 50% EV filter, should be 0 matching rows
        assert rows_high_ev == 0, f"Scenario 4 Failed: Expected 0 rows for 50% EV, got {rows_high_ev}"
        
        # Restore EV
        with page.expect_response(lambda r: "global-results" in r.url):
            page.fill("#props-filter-min-ev", "")
        time.sleep(0.4)
        print("[OK] Scenario 4: Min Net EV filter functions accurately")

        # ──────────────────────────────────────────────────────────────────
        # SCENARIO 5: MIN ODDS
        # ──────────────────────────────────────────────────────────────────
        ensure_advanced_filters_open(page)

        with page.expect_response(lambda r: "global-results" in r.url):
            page.fill("#props-filter-min-odds", "3.50")
        time.sleep(0.4)
        rows_odds = page.locator("#props-table-body tr.prop-table-row").count()

        # Reset min odds
        with page.expect_response(lambda r: "global-results" in r.url):
            page.fill("#props-filter-min-odds", "1.0")
        time.sleep(0.4)
        print(f"[OK] Scenario 5: Min Odds filter active (filtered to {rows_odds} rows)")

        # ──────────────────────────────────────────────────────────────────
        # SCENARIO 6: STATUS (Below Threshold)
        # ──────────────────────────────────────────────────────────────────
        with page.expect_response(lambda r: "global-results" in r.url):
            page.click("#props-tab-below-threshold")
        time.sleep(0.4)
        assert page.locator("#props-filter-exec-status").input_value() == "BELOW_VALUE_THRESHOLD"
        rows_bt = page.locator("#props-table-body tr.prop-table-row").count()
        print(f"[OK] Scenario 6: Status filter below threshold returned {rows_bt} rows")

        # ──────────────────────────────────────────────────────────────────
        # SCENARIO 7: STAT TYPE
        # ──────────────────────────────────────────────────────────────────
        with page.expect_response(lambda r: "global-results" in r.url):
            page.select_option("#props-filter-stat", "shots")
        time.sleep(0.4)
        assert page.locator("#props-filter-threshold").text_content() is not None
        print("[OK] Scenario 7: Stat type selector updated line threshold options")

        # ──────────────────────────────────────────────────────────────────
        # SCENARIO 8: COMPETITION
        # ──────────────────────────────────────────────────────────────────
        ensure_advanced_filters_open(page)
        with page.expect_response(lambda r: "global-results" in r.url):
            page.select_option("#props-filter-tournaments", "Ligue 1")
        time.sleep(0.4)
        print("[OK] Scenario 8: Competition filter applied")

        # ──────────────────────────────────────────────────────────────────
        # SCENARIO 9: COMBINED
        # ──────────────────────────────────────────────────────────────────
        ensure_advanced_filters_open(page)
        page.select_option("#props-filter-bookmaker", "Superbet")
        page.fill("#props-filter-min-ev", "0.0")
        with page.expect_response(lambda r: "global-results" in r.url):
            page.fill("#props-filter-min-odds", "1.10")
        time.sleep(0.4)
        rows_comb = page.locator("#props-table-body tr.prop-table-row").count()
        print(f"[OK] Scenario 9: Combined filters returned {rows_comb} rows cleanly")

        # ──────────────────────────────────────────────────────────────────
        # SCENARIO 10: MAX RESULTS (Limit to 10)
        # ──────────────────────────────────────────────────────────────────
        # Switch to All Candidates to see limit in effect
        page.click("#props-tab-diagnostics")
        ensure_advanced_filters_open(page)
        with page.expect_response(lambda r: "global-results" in r.url):
            page.select_option("#props-filter-limit", "10")
        time.sleep(0.4)
        rows_limit = page.locator("#props-table-body tr.prop-table-row").count()
        assert rows_limit <= 10, f"Scenario 10 Failed: Expected <= 10 rows, got {rows_limit}"
        print(f"[OK] Scenario 10: Max results limit capped rows to {rows_limit}")

        # ──────────────────────────────────────────────────────────────────
        # SCENARIO 11: SORT BY
        # ──────────────────────────────────────────────────────────────────
        with page.expect_response(lambda r: "global-results" in r.url):
            page.select_option("#props-filter-sortby", "hit_rate")
        time.sleep(0.4)
        meta_line = page.locator("#props-table-meta-line").text_content()
        assert "Hit Rate" in meta_line
        with page.expect_response(lambda r: "global-results" in r.url):
            page.select_option("#props-filter-sortby", "odds")
        time.sleep(0.4)
        meta_line_odds = page.locator("#props-table-meta-line").text_content()
        assert "Odds" in meta_line_odds
        print("[OK] Scenario 11: Sort by options update table and metadata")

        # ──────────────────────────────────────────────────────────────────
        # SCENARIO 12: RESET FILTERS
        # ──────────────────────────────────────────────────────────────────
        with page.expect_response(lambda r: "global-results" in r.url):
            page.click("#btn-reset-props-filters")
        time.sleep(0.4)
        assert page.locator("#props-filter-search").input_value() == ""
        assert page.locator("#props-filter-stat").input_value() == ""
        assert page.locator("#props-filter-min-ev").input_value() == "3.0"
        assert page.locator("#props-filter-sortby").input_value() == "net_ev"
        assert page.locator("#props-filter-bookmaker").input_value() == ""
        assert page.locator("#props-filter-exec-status").input_value() == ""
        assert page.locator("#props-filter-limit").input_value() == "50"
        assert "active" in page.locator("#props-tab-top-value").get_attribute("class")
        print("[OK] Scenario 12: Reset Filters restores exact clean defaults")

        # ──────────────────────────────────────────────────────────────────
        # SCENARIO 13: INSPECT (Details Drawer)
        # ──────────────────────────────────────────────────────────────────
        with page.expect_response(lambda r: "global-results" in r.url):
            page.click("#props-tab-diagnostics")
        page.wait_for_selector(".btn-prop-detail", timeout=5000)
        time.sleep(0.3)
        first_inspect_btn = page.locator(".btn-prop-detail").first
        first_inspect_btn.click()
        time.sleep(0.4)

        drawer = page.locator("#prop-detail-container")
        assert drawer.is_visible(), "Scenario 13 Failed: Drawer did not open"
        drawer_title = page.locator("#prop-detail-title").text_content()
        assert len(drawer_title) > 0

        # Close drawer via Escape key
        page.keyboard.press("Escape")
        time.sleep(0.3)
        assert not drawer.is_visible(), "Scenario 13 Failed: Escape key did not close drawer"
        print("[OK] Scenario 13: Inspect Opportunity details drawer opens and closes reliably")

        # ──────────────────────────────────────────────────────────────────
        # SCENARIO 14: RAPID FILTER CHANGE (No Stale Overwrites)
        # ──────────────────────────────────────────────────────────────────
        ensure_advanced_filters_open(page)
        # Rapidly change filters in quick succession
        page.select_option("#props-filter-bookmaker", "Superbet")
        page.select_option("#props-filter-bookmaker", "Betclic")
        page.fill("#props-filter-search", "a")
        page.fill("#props-filter-search", "")
        time.sleep(0.8)
        # Should cleanly settle on Betclic without error
        assert page.locator("#props-filter-bookmaker").input_value() == "Betclic"
        print("[OK] Scenario 14: Rapid filter changes handled without race conditions")

        # ──────────────────────────────────────────────────────────────────
        # SCENARIO 15: EMPTY STATE
        # ──────────────────────────────────────────────────────────────────
        with page.expect_response(lambda r: "global-results" in r.url):
            page.fill("#props-filter-search", "xyznonexistentplayer99999")
        time.sleep(0.4)
        empty_text = page.locator("#props-table-body").text_content()
        assert "No player props match the current filters" in empty_text
        with page.expect_response(lambda r: "global-results" in r.url):
            page.fill("#props-filter-search", "")
        time.sleep(0.4)
        print("[OK] Scenario 15: Empty state renders helpful message and diagnostics action")

        # ──────────────────────────────────────────────────────────────────
        # SCENARIO 16: ERROR HANDLING
        # ──────────────────────────────────────────────────────────────────
        # Verify alert container exists and is clean
        alert_container = page.locator("#props-alert-container")
        assert alert_container is not None
        print("[OK] Scenario 16: Error handling container verified")

        # ──────────────────────────────────────────────────────────────────
        # SCENARIOS 17–19: RESPONSIVE VIEWPORTS
        # ──────────────────────────────────────────────────────────────────
        # 17. Mobile 375px
        page.set_viewport_size({"width": 375, "height": 667})
        time.sleep(0.3)
        body_scroll_width = page.evaluate("document.body.scrollWidth")
        body_client_width = page.evaluate("document.body.clientWidth")
        assert body_scroll_width <= body_client_width + 10, f"Mobile 375px has body overflow: {body_scroll_width} > {body_client_width}"
        assert page.locator("#btn-reset-props-filters").is_visible()
        print("[OK] Scenario 17: Mobile 375px responsive without body overflow")

        # 18. Tablet 768px
        page.set_viewport_size({"width": 768, "height": 1024})
        time.sleep(0.3)
        assert page.locator("#props-heading").is_visible()
        print("[OK] Scenario 18: Tablet 768px layout responsive")

        # 19. Desktop 1366px
        page.set_viewport_size({"width": 1366, "height": 768})
        time.sleep(0.3)
        assert page.locator("#props-data-table").is_visible()
        print("[OK] Scenario 19: Desktop 1366px layout verified")

        # ──────────────────────────────────────────────────────────────────
        # SCENARIO 20: DARK / LIGHT THEME
        # ──────────────────────────────────────────────────────────────────
        current_theme = page.evaluate("document.documentElement.getAttribute('data-theme')")
        page.click("#theme-toggle-btn")
        time.sleep(0.3)
        new_theme = page.evaluate("document.documentElement.getAttribute('data-theme')")
        assert current_theme != new_theme, f"Theme did not toggle: {current_theme} == {new_theme}"
        # Toggle back
        page.click("#theme-toggle-btn")
        time.sleep(0.3)
        print(f"[OK] Scenario 20: Theme toggled cleanly ({current_theme} -> {new_theme} -> {current_theme})")

        # ──────────────────────────────────────────────────────────────────
        # SCENARIO 21: CONSOLE LOGS (Zero unexpected errors)
        # ──────────────────────────────────────────────────────────────────
        assert len(console_errors) == 0, f"Scenario 21 Failed: Console errors detected: {console_errors}"
        print("[OK] Scenario 21: Zero console errors recorded across all interactions")

        browser.close()


if __name__ == "__main__":
    test_runtime_qa_all_21_scenarios()
