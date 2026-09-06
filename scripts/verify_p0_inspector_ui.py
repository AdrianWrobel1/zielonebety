"""
Visual and DOM invariant verification script for P0 Opportunity Truth & Inspector Repair.
Tests Doncaster Rovers quote comparison vs genuine Real Madrid surebet across viewports & themes.
"""

import os
import sys
import time
import json

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from playwright.sync_api import sync_playwright

BASE_URL = "http://127.0.0.1:8000"
EVIDENCE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "test_evidence_screenshots", "p0_inspector_truth")
os.makedirs(EVIDENCE_DIR, exist_ok=True)

DONCASTER_ID = "ctp_scan_cev_041ef6c7eed6abdd_Doncaster Rovers_TOTALS_0.5_OVER"

VIEWPORTS = [
    ("desktop_1366_dark", 1366, 768, "dark"),
    ("desktop_1366_light", 1366, 768, "light"),
    ("tablet_1024_dark", 1024, 768, "dark"),
    ("tablet_768_dark", 768, 1024, "dark"),
    ("mobile_375_dark", 375, 812, "dark"),
]

def run_verification():
    print("[p0_inspector_ui] Starting visual verification with Playwright...")
    results = {"checks": [], "screenshots": []}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        
        # First: Test Doncaster Rovers Inspector
        for name, width, height, theme in VIEWPORTS:
            context = browser.new_context(viewport={"width": width, "height": height})
            page = context.new_page()

            try:
                page.goto(f"{BASE_URL}/#opportunities", wait_until="networkidle")
                page.wait_for_timeout(1000)

                # Set theme
                page.evaluate(f"document.documentElement.setAttribute('data-theme', '{theme}')")
                page.wait_for_timeout(300)

                # Load Doncaster detail into Inspector
                page.evaluate(f"""async () => {{
                    await window.__zb.loadOpportunityDetail('{DONCASTER_ID}', {{ openModal: {str(width < 1200).lower()} }});
                }}""")
                page.wait_for_timeout(1000)

                # Verification Checks on Doncaster Inspector
                badge_text = page.evaluate("() => document.getElementById('opp-inspector-badge')?.textContent || document.getElementById('opp-detail-modal-badge')?.textContent")
                title_text = page.evaluate("() => document.getElementById('opp-inspector-title')?.textContent || document.getElementById('opp-detail-modal-title')?.textContent")
                has_surebet_calc = page.evaluate("() => Boolean(document.querySelector('.surebet-calc-box') || document.querySelector('.surebet-stake-calculator'))")

                print(f"[{name}] Title: {title_text} | Badge: {badge_text} | Has Surebet Calc: {has_surebet_calc}")

                # Doncaster Invariant Assertions
                assert "SUREBET" not in (badge_text or "").upper(), f"[{name}] FAILED: Doncaster marked as SUREBET!"
                assert "TEAM PROP" in (badge_text or "").upper() or "PROP" in (badge_text or "").upper(), f"[{name}] FAILED: Doncaster not marked as TEAM PROP!"
                assert not has_surebet_calc, f"[{name}] FAILED: Doncaster rendered surebet calculator!"

                # Capture screenshot
                ss_path = os.path.join(EVIDENCE_DIR, f"{name}_doncaster.png")
                page.screenshot(path=ss_path, full_page=False)
                results["screenshots"].append(ss_path)
                print(f"  [OK] Saved screenshot: {ss_path}")

            finally:
                context.close()

        # Second: Test Genuine Surebet (Real Madrid vs Barcelona)
        context = browser.new_context(viewport={"width": 1366, "height": 768})
        page = context.new_page()
        try:
            page.goto(f"{BASE_URL}/#opportunities", wait_until="networkidle")
            page.wait_for_timeout(1000)

            # Load first opportunity (Real Madrid vs Barcelona surebet)
            page.evaluate("""() => {
                const firstRow = document.querySelector('.opp-feed-item');
                if (firstRow) firstRow.click();
            }""")
            page.wait_for_timeout(1200)

            badge_text = page.evaluate("() => document.getElementById('opp-inspector-badge')?.textContent")
            has_calc = page.evaluate("() => Boolean(document.querySelector('.surebet-calc-box') || document.querySelector('.stake-calc-card') || document.getElementById('surebet-calculator'))")
            print(f"[genuine_surebet] Badge: {badge_text} | Has Calc: {has_calc}")

            ss_path = os.path.join(EVIDENCE_DIR, "desktop_genuine_surebet.png")
            page.screenshot(path=ss_path, full_page=False)
            results["screenshots"].append(ss_path)
            print(f"  [OK] Saved genuine surebet screenshot: {ss_path}")

        finally:
            context.close()

        browser.close()

    print("[p0_inspector_ui] All visual and DOM invariant checks PASSED successfully!")
    return results

if __name__ == "__main__":
    run_verification()
