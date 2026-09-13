"""
Targeted Playwright Mobile Smoke Test for Scan Diagnostic Mode (502 Diagnosis).
Verifies:
1. iPhone mobile viewport rendering (390x844).
2. Interception of POST /api/v1/scan/run returning an immediate 502 HTML page.
3. Rendering of SCAN DEBUG panel with Category C, raw HTML body, safe headers, and request details.
4. Dismissal of debug panel on Clear Debug button.
5. Screenshot evidence capturing the mobile display.
"""

import os
import pytest
from playwright.sync_api import sync_playwright


def test_mobile_scan_debug_502_rendering():
    """Verify that an immediate 502 Bad Gateway exposes the real request and response in mobile SCAN DEBUG."""
    html_path = os.path.abspath("web/index.html")
    screenshot_dir = os.path.abspath("test_evidence_screenshots")
    os.makedirs(screenshot_dir, exist_ok=True)
    screenshot_path = os.path.join(screenshot_dir, "mobile_scan_debug_502.png")

    with sync_playwright() as p:
        # Emulate iPhone 14 viewport
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 390, "height": 844},
            user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 16_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/114.0.5735.124 Mobile/15E148 Safari/604.1"
        )
        page = context.new_page()

        # Mock background endpoints to keep the console clean
        page.route("**/api/v1/health**", lambda route: route.fulfill(status=200, content_type="application/json", body='{"data":{"status":"HEALTHY"}}'))
        page.route("**/api/v1/scan/status**", lambda route: route.fulfill(status=200, content_type="application/json", body='{"data":{"status":"READY"}}'))
        page.route("**/api/v1/scan/latest**", lambda route: route.fulfill(status=200, content_type="application/json", body='{"data":null}'))
        page.route("**/api/v1/scan/history**", lambda route: route.fulfill(status=200, content_type="application/json", body='{"data":[]}'))
        page.route("**/api/v1/providers**", lambda route: route.fulfill(status=200, content_type="application/json", body='{"data":[]}'))
        page.route("**/api/v1/events**", lambda route: route.fulfill(status=200, content_type="application/json", body='{"data":[]}'))
        page.route("**/api/v1/opportunities**", lambda route: route.fulfill(status=200, content_type="application/json", body='{"data":[]}'))
        page.route("**/api/v1/notifications**", lambda route: route.fulfill(status=200, content_type="application/json", body='{"data":[]}'))

        # Mock the exact 502 Gateway Failure on Run Scan
        gateway_html_body = "<html><head><title>502 Bad Gateway</title></head><body><center><h1>502 Bad Gateway</h1></center><hr><center>cloudflare-nginx</center><p>Host connection refused on upstream:8000</p></body></html>"
        page.route("**/api/v1/scan/run", lambda route: route.fulfill(
            status=502,
            headers={
                "server": "cloudflare",
                "cf-ray": "8c1234567890-WAW",
                "content-type": "text/html; charset=UTF-8",
                "content-length": str(len(gateway_html_body)),
            },
            body=gateway_html_body,
        ))

        # Navigate to local web index
        page.goto(f"file:///{html_path.replace(os.sep, '/')}")
        page.wait_for_selector("#btn-run-scan")

        # Initial state: Debug panel must NOT be visible
        debug_panel = page.locator("#dash-scan-debug-panel")
        assert not debug_panel.is_visible(), "Diagnostic panel should be hidden initially"

        # Trigger Run Scan
        page.click("#btn-run-scan")

        # Wait for diagnostic panel to appear
        debug_panel.wait_for(state="visible", timeout=5000)

        # Verify alert banner
        alert_box = page.locator("#dash-alert-container")
        assert alert_box.is_visible()
        alert_text = alert_box.inner_text()
        assert "502" in alert_text

        # Verify Diagnostic Panel content
        panel_text = debug_panel.inner_text()
        assert "SCAN DEBUG" in panel_text
        assert "CATEGORY C" in panel_text
        assert "Proxy / Gateway Failure" in panel_text
        assert "POST" in panel_text
        assert "502" in panel_text
        assert "cloudflare-nginx" in panel_text or "cloudflare" in panel_text
        assert "Host connection refused" in panel_text

        # Verify buttons exist
        btn_copy = page.locator("#btn-copy-scan-debug")
        btn_clear = page.locator("#btn-clear-scan-debug")
        assert btn_copy.is_visible()
        assert btn_clear.is_visible()

        # Capture evidence screenshot of the mobile screen
        page.screenshot(path=screenshot_path, full_page=False)
        print(f"Captured mobile screenshot at {screenshot_path}")

        # Also capture screenshot scrolled into the debug drawers
        debug_panel.scroll_into_view_if_needed()
        screenshot_detail_path = os.path.join(screenshot_dir, "mobile_scan_debug_details.png")
        page.screenshot(path=screenshot_detail_path, full_page=False)
        print(f"Captured mobile detail screenshot at {screenshot_detail_path}")

        # Test Clear Debug action
        btn_clear.click()
        page.wait_for_timeout(200)
        assert not debug_panel.is_visible(), "Diagnostic panel should be hidden after Clear Debug"
        assert alert_box.inner_text().strip() == "", "Alert box should be cleared after Clear Debug"

        context.close()
        browser.close()


def test_mobile_scan_debug_json_error_with_cycle_safety():
    """Verify that a JSON error response renders cycle-safely without throwing cyclic serialization errors."""
    html_path = os.path.abspath("web/index.html")
    screenshot_dir = os.path.abspath("test_evidence_screenshots")
    os.makedirs(screenshot_dir, exist_ok=True)
    screenshot_path = os.path.join(screenshot_dir, "mobile_scan_debug_json_error.png")

    page_errors = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 390, "height": 844},
            user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 16_5 like Mac OS X) AppleWebKit/605.1.15"
        )
        page = context.new_page()
        page.on("pageerror", lambda err: page_errors.append(str(err)))

        # Mock background endpoints
        page.route("**/api/v1/health**", lambda route: route.fulfill(status=200, content_type="application/json", body='{"data":{"status":"HEALTHY"}}'))
        page.route("**/api/v1/scan/status**", lambda route: route.fulfill(status=200, content_type="application/json", body='{"data":{"status":"READY"}}'))
        page.route("**/api/v1/scan/latest**", lambda route: route.fulfill(status=200, content_type="application/json", body='{"data":null}'))
        page.route("**/api/v1/scan/history**", lambda route: route.fulfill(status=200, content_type="application/json", body='{"data":[]}'))
        page.route("**/api/v1/providers**", lambda route: route.fulfill(status=200, content_type="application/json", body='{"data":[]}'))
        page.route("**/api/v1/events**", lambda route: route.fulfill(status=200, content_type="application/json", body='{"data":[]}'))
        page.route("**/api/v1/opportunities**", lambda route: route.fulfill(status=200, content_type="application/json", body='{"data":[]}'))
        page.route("**/api/v1/notifications**", lambda route: route.fulfill(status=200, content_type="application/json", body='{"data":[]}'))

        # Mock structured JSON backend error
        page.route("**/api/v1/scan/run", lambda route: route.fulfill(
            status=500,
            content_type="application/json",
            body='{"status_code":500,"errors":["Database connection pool exhausted"],"data":{"retries":3}}'
        ))

        page.goto(f"file:///{html_path.replace(os.sep, '/')}")
        page.wait_for_selector("#btn-run-scan")

        # Trigger Run Scan
        page.click("#btn-run-scan")

        debug_panel = page.locator("#dash-scan-debug-panel")
        debug_panel.wait_for(state="visible", timeout=5000)

        # Confirm zero unhandled page errors (no cyclic serialization crash)
        cyclic_errors = [e for e in page_errors if "cyclic" in e.lower() or "circular" in e.lower()]
        assert len(cyclic_errors) == 0, f"Found cyclic structure errors in browser: {cyclic_errors}"

        # Check alert banner preserves real error
        alert_box = page.locator("#dash-alert-container")
        alert_text = alert_box.inner_text()
        assert "Database connection pool exhausted" in alert_text, f"Unexpected alert banner: {alert_text}"

        # Check Diagnostic Panel contains Category D and exact error
        panel_text = debug_panel.inner_text()
        assert "CATEGORY D" in panel_text
        assert "Backend Application HTTP Error" in panel_text
        assert "Database connection pool exhausted" in panel_text
        assert "500" in panel_text

        # Capture screenshot evidence
        page.screenshot(path=screenshot_path, full_page=False)

        context.close()
        browser.close()


if __name__ == "__main__":
    test_mobile_scan_debug_502_rendering()
    test_mobile_scan_debug_json_error_with_cycle_safety()

