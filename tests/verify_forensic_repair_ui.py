import json
import os
import sys
import time
from playwright.sync_api import sync_playwright

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

SCREENSHOT_DIR = r"C:\Users\Adrian\.gemini\antigravity\brain\a58079d0-a1ac-4910-bf43-fb19d6f4a00f\qa_screenshots"
os.makedirs(SCREENSHOT_DIR, exist_ok=True)

def run_forensic_qa():
    print("[FORENSIC QA] Starting Playwright Browser Verification...")
    results = {}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()
        page.on("console", lambda msg: print(f"  [CONSOLE {msg.type}] {msg.text}"))
        page.on("pageerror", lambda err: print(f"  [PAGE ERROR] {err}"))

        # -------------------------------------------------------------
        # 1. COMMAND CENTER (DASHBOARD)
        # -------------------------------------------------------------
        print("\n--- 1. Verifying Command Center (Dashboard) ---")
        page.goto("http://127.0.0.1:8000/#dashboard")
        page.wait_for_selector(".dash-radar-row", timeout=10000)

        # Telemetry pills
        telemetry = page.evaluate("""() => {
            return {
                systemBadge: document.getElementById('dash-scanner-status-badge')?.innerText?.trim(),
                lastScan: document.getElementById('dash-last-scan-time')?.innerText?.trim(),
                duration: document.getElementById('dash-scan-duration')?.innerText?.trim(),
                mode: document.getElementById('dash-scan-mode-val')?.innerText?.trim(),
                source: document.getElementById('dash-scan-source-val')?.innerText?.trim()
            };
        }""")
        print(f"Telemetry: {json.dumps(telemetry, indent=2)}")
        assert telemetry["mode"] in ["NORMAL", "ULTRA", "DEEP", "—"], f"Unexpected mode: {telemetry['mode']}"
        assert any(k in telemetry["source"] for k in ["MANUAL", "AUTOMATED", "SCHEDULED", "CACHED", "ULTRA", "NORMAL", "—"]), f"Unexpected source: {telemetry['source']}"

        # Radar Top-5 Rows
        radar_data = page.evaluate("""() => {
            const rows = Array.from(document.querySelectorAll('.dash-radar-row')).map(el => {
                const rank = el.querySelector('.dash-radar-row-rank')?.innerText?.trim();
                const ev = el.querySelector('.dash-radar-row-edge')?.innerText?.trim();
                const type = el.querySelector('.dash-radar-row-type')?.innerText?.trim();
                const fixture = el.querySelector('.dash-radar-row-event')?.innerText?.trim();
                const market = el.querySelector('.dash-radar-row-market')?.innerText?.trim();
                const best = el.querySelector('.dash-radar-price-exec')?.innerText?.trim();
                const ref = el.querySelector('.dash-radar-price-ref')?.innerText?.trim();
                return { rank, ev, type, fixture, market, best, ref };
            });
            const subtitle = document.getElementById('dash-opp-badge')?.innerText?.trim();
            const footerText = document.querySelector('.dash-radar-preview-footer')?.innerText?.trim();
            const footerLink = document.querySelector('.dash-radar-preview-footer a')?.innerText?.trim();
            return {
                rowCount: rows.length,
                rows: rows,
                subtitle: subtitle,
                footerText: footerText,
                footerLink: footerLink
            };
        }""")
        print(f"Radar Data: {json.dumps(radar_data, indent=2)}")
        assert radar_data["rowCount"] <= 5, f"Radar must show at most 5 items, got {radar_data['rowCount']}"
        assert radar_data["rowCount"] > 0, "Radar should display opportunities"
        assert "Top 5" in radar_data["footerText"] or "Showing Top" in radar_data["subtitle"]
        page.screenshot(path=os.path.join(SCREENSHOT_DIR, "01_command_center_radar.png"))
        results["command_center_radar"] = "PASSED"

        # -------------------------------------------------------------
        # 2. TEST DASHBOARD REFRESH BUTTON
        # -------------------------------------------------------------
        print("\n--- 2. Testing Dashboard Refresh Button ---")
        page.on("request", lambda req: print(f"  [REQ] {req.url}"))
        page.on("response", lambda res: print(f"  [RES] {res.status} {res.url}"))
        refresh_btn_state_before = page.evaluate("() => document.getElementById('btn-refresh-dashboard')?.innerText?.trim()")
        
        # Click refresh and wait for network idle
        page.click("#btn-refresh-dashboard")
        page.wait_for_timeout(1500)
        refresh_btn_state_after = page.evaluate("() => document.getElementById('btn-refresh-dashboard')?.innerText?.trim()")
        print(f"Dashboard Refresh text before: '{refresh_btn_state_before}', after: '{refresh_btn_state_after}'")
        assert "Refresh" in refresh_btn_state_after
        page.screenshot(path=os.path.join(SCREENSHOT_DIR, "02_dashboard_refreshed.png"))
        results["dashboard_refresh"] = "PASSED"

        # -------------------------------------------------------------
        # 3. TEST RADAR INSPECT BUTTON
        # -------------------------------------------------------------
        print("\n--- 3. Testing Radar Inspect Button & Modal ---")
        with page.expect_response(lambda r: "/api/v1/opportunities/" in r.url and r.status == 200, timeout=10000):
            page.click(".dash-radar-row .btn-dash-inspect")
        page.wait_for_timeout(600)

        modal_visible = page.evaluate("""() => {
            const modal = document.getElementById('opp-detail-modal');
            const title = modal?.querySelector('.insp-event-title')?.innerText?.trim();
            const badge = document.getElementById('opp-detail-modal-badge')?.innerText?.trim();
            const content = document.getElementById('opp-detail-modal-content')?.innerText?.trim();
            return {
                modalDisplay: modal?.style.display,
                title: title,
                badge: badge,
                hasContent: Boolean(content && content.length > 20)
            };
        }""")
        print(f"Modal Data: {json.dumps(modal_visible, indent=2)}")
        assert modal_visible["modalDisplay"] != "none", "Opportunity detail modal should be open"
        assert modal_visible["title"] is not None and len(modal_visible["title"]) > 0, "Event title in modal must be populated"
        assert modal_visible["hasContent"] is True, "Inspector modal content must be rendered"
        page.screenshot(path=os.path.join(SCREENSHOT_DIR, "03_radar_inspect_modal.png"))

        # Close modal
        page.click("#btn-close-opp-detail")
        page.wait_for_timeout(300)
        results["radar_inspect_modal"] = "PASSED"

        # -------------------------------------------------------------
        # 4. OPPORTUNITY EXPLORER VIEW & REFRESH
        # -------------------------------------------------------------
        print("\n--- 4. Verifying Opportunity Explorer ---")
        page.goto("http://127.0.0.1:8000/#opportunities")
        page.wait_for_selector(".opp-feed-item", timeout=10000)

        explorer_data = page.evaluate("""() => {
            const totalCount = document.getElementById('opp-feed-total-count')?.innerText?.trim();
            const items = Array.from(document.querySelectorAll('.opp-feed-item')).map(el => {
                const title = el.querySelector('.opp-feed-primary-title')?.innerText?.trim();
                const ev = el.querySelector('.opp-feed-ev-value')?.innerText?.trim();
                return { title, ev };
            });
            return { totalCount, itemCount: items.length, sample: items.slice(0, 3) };
        }""")
        print(f"Explorer Data: {json.dumps(explorer_data, indent=2)}")
        assert explorer_data["itemCount"] > 0, "Opportunity Explorer should display items"

        # Test Explorer Refresh with filter preservation
        print("\n--- Testing Explorer Refresh Button ---")
        # Change a filter first (e.g. min EV / ROI)
        page.fill("#filter-min-roi", "3.0")
        page.dispatch_event("#filter-min-roi", "change")
        page.wait_for_timeout(500)

        with page.expect_response(lambda r: "/opportunities/explorer" in r.url and "refresh=true" in r.url and r.status == 200, timeout=10000):
            page.click("#btn-refresh-opps")
        page.wait_for_timeout(500)

        filter_val_after = page.evaluate("() => document.getElementById('filter-min-roi')?.value")
        print(f"Filter min ROI preserved after refresh: {filter_val_after}")
        assert filter_val_after == "3.0", "Filter value must be preserved after refresh"

        page.screenshot(path=os.path.join(SCREENSHOT_DIR, "04_explorer_refreshed.png"))
        results["explorer_refresh"] = "PASSED"

        # -------------------------------------------------------------
        # 5. GLOBAL PROPS SCANNER VIEW & NON-CONTRADICTORY COUNTERS
        # -------------------------------------------------------------
        print("\n--- 5. Verifying Global Props Scanner ---")
        page.goto("http://127.0.0.1:8000/#playerprops")
        page.wait_for_selector("#btn-refresh-props", timeout=10000)
        page.wait_for_timeout(800)

        props_counters = page.evaluate("""() => {
            return {
                scanned: document.getElementById('props-stat-scanned')?.innerText?.trim(),
                unique: document.getElementById('props-stat-unique')?.innerText?.trim(),
                matchedBoth: document.getElementById('props-stat-matched-both')?.innerText?.trim(),
                bettable: document.getElementById('props-stat-bettable')?.innerText?.trim(),
                discrepancy: document.getElementById('props-stat-discrepancy')?.innerText?.trim(),
                tabTop: document.getElementById('tab-count-props-top')?.innerText?.trim(),
                tabDiscrepancies: document.getElementById('tab-count-props-discrepancies')?.innerText?.trim(),
                tabAll: document.getElementById('tab-count-props-all')?.innerText?.trim(),
                tabDiagnostics: document.getElementById('tab-count-props-diagnostics')?.innerText?.trim(),
                hasRefreshBtn: Boolean(document.getElementById('btn-refresh-props'))
            };
        }""")
        print(f"Props Counters: {json.dumps(props_counters, indent=2)}")
        assert props_counters["hasRefreshBtn"] is True, "Global Props must have #btn-refresh-props"
        
        # Verify non-contradictory logic:
        # ALL tab count must be >= TOP VALUEBETS count
        tab_top = int(props_counters["tabTop"]) if props_counters["tabTop"].isdigit() else 0
        tab_all = int(props_counters["tabAll"]) if props_counters["tabAll"].isdigit() else 0
        print(f"Props Tabs -> TOP: {tab_top}, ALL: {tab_all}")
        assert tab_all >= tab_top, f"ALL count ({tab_all}) cannot be less than TOP count ({tab_top})"

        # Test Global Props Refresh Button
        print("\n--- Testing Global Props Refresh Button ---")
        with page.expect_response(lambda r: "/props/global-results" in r.url and r.status == 200, timeout=10000):
            page.click("#btn-refresh-props")
        page.wait_for_timeout(600)
        page.screenshot(path=os.path.join(SCREENSHOT_DIR, "05_props_scanner_refreshed.png"))
        results["props_refresh"] = "PASSED"

        # -------------------------------------------------------------
        # 6. SWITCH MODES: NORMAL TO ULTRA AND BACK
        # -------------------------------------------------------------
        print("\n--- 6. Testing Mode Switching: NORMAL <-> ULTRA ---")
        page.click("#props-mode-ultra")
        page.wait_for_timeout(1000)

        ultra_active = page.evaluate("""() => {
            const btnUltra = document.getElementById('props-mode-ultra');
            const notice = document.getElementById('props-ultra-mode-notice');
            return {
                ultraActive: btnUltra?.classList.contains('active'),
                noticeVisible: notice ? window.getComputedStyle(notice).display !== 'none' : false,
                tabAllCount: document.getElementById('tab-count-props-all')?.innerText?.trim()
            };
        }""")
        print(f"ULTRA Switch: {json.dumps(ultra_active, indent=2)}")
        assert ultra_active["ultraActive"] is True, "ULTRA button must be active"
        assert ultra_active["noticeVisible"] is True, "ULTRA mode notice banner must be visible"

        page.screenshot(path=os.path.join(SCREENSHOT_DIR, "06_props_ultra_mode.png"))

        # Switch back to NORMAL
        page.click("#props-mode-normal")
        page.wait_for_timeout(800)
        normal_active = page.evaluate("""() => {
            const btnNormal = document.getElementById('props-mode-normal');
            const notice = document.getElementById('props-ultra-mode-notice');
            return {
                normalActive: btnNormal?.classList.contains('active'),
                noticeVisible: notice ? window.getComputedStyle(notice).display !== 'none' : false
            };
        }""")
        print(f"NORMAL Switch: {json.dumps(normal_active, indent=2)}")
        assert normal_active["normalActive"] is True, "NORMAL button must be active"
        assert normal_active["noticeVisible"] is False, "ULTRA mode notice banner must be hidden in NORMAL"
        results["props_mode_switching"] = "PASSED"

        # -------------------------------------------------------------
        # 7. RESPONSIVE LAYOUT VERIFICATION
        # -------------------------------------------------------------
        print("\n--- 7. Responsive Layout Verification ---")
        for width, name in [(1440, "desktop_1440"), (1024, "laptop_1024"), (768, "tablet_768"), (375, "mobile_375")]:
            page.set_viewport_size({"width": width, "height": 800})
            page.wait_for_timeout(400)
            page.screenshot(path=os.path.join(SCREENSHOT_DIR, f"07_responsive_{name}.png"))

        browser.close()

    print("\n" + "="*60)
    print("ALL FORENSIC BROWSER QA TESTS COMPLETED SUCCESSFULLY!")
    print("="*60)
    print(json.dumps(results, indent=2))

if __name__ == "__main__":
    run_forensic_qa()
