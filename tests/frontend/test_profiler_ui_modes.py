import os
import time
from playwright.sync_api import sync_playwright

def test_profiler_ui_multi_mode_navigation():
    evidence_dir = os.path.join(os.getcwd(), "test_evidence_screenshots")
    os.makedirs(evidence_dir, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1366, "height": 768})
        page = context.new_page()

        console_errors = []
        page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)

        print("[Step 1] Navigating directly to http://127.0.0.1:8000/#profiler...")
        page.goto("http://127.0.0.1:8000/#profiler")
        page.wait_for_selector("#view-profiler", state="visible", timeout=10000)
        time.sleep(2)

        # 1. Main Scan is default
        heading = page.locator("#profiler-heading").inner_text()
        print(f"[Step 2] Initial heading: {heading}")
        assert "Main Scan" in heading, f"Expected Main Scan heading, got: {heading}"

        main_btn = page.locator("#prof-mode-main")
        assert "active" in (main_btn.get_attribute("class") or ""), "Main Scan button should be active by default"
        page.screenshot(path=os.path.join(evidence_dir, "profiler_1_main_scan.png"))

        # 2. Switch to Team Props
        print("[Step 3] Clicking Team Props mode button...")
        page.click("#prof-mode-team")
        time.sleep(2)

        team_btn = page.locator("#prof-mode-team")
        assert "active" in (team_btn.get_attribute("class") or ""), "Team Props button should be active"
        heading_team = page.locator("#profiler-heading").inner_text()
        print(f"[Step 4] Team Props heading: {heading_team}")
        assert "Team Props" in heading_team, f"Expected Team Props heading, got: {heading_team}"
        page.screenshot(path=os.path.join(evidence_dir, "profiler_2_team_props.png"))

        # 3. Switch to Player Props
        print("[Step 5] Clicking Player Props mode button...")
        page.click("#prof-mode-player")
        time.sleep(2)

        player_btn = page.locator("#prof-mode-player")
        assert "active" in (player_btn.get_attribute("class") or ""), "Player Props button should be active"
        heading_player = page.locator("#profiler-heading").inner_text()
        print(f"[Step 6] Player Props heading: {heading_player}")
        assert "Player Props" in heading_player, f"Expected Player Props heading, got: {heading_player}"
        page.screenshot(path=os.path.join(evidence_dir, "profiler_3_player_props.png"))

        # 4. Switch back to Main Scan
        print("[Step 7] Switching back to Main Scan...")
        page.click("#prof-mode-main")
        time.sleep(2)

        assert "active" in (main_btn.get_attribute("class") or ""), "Main Scan button should be active after returning"
        heading_back = page.locator("#profiler-heading").inner_text()
        print(f"[Step 8] Returned heading: {heading_back}")
        assert "Main Scan" in heading_back, f"Expected Main Scan heading, got: {heading_back}"
        page.screenshot(path=os.path.join(evidence_dir, "profiler_4_main_returned.png"))

        # Check console errors
        print(f"[Step 9] Console errors count: {len(console_errors)}")
        if console_errors:
            print(f"Console errors: {console_errors}")

        browser.close()
        print("UI VALIDATION COMPLETED SUCCESSFULLY!")

if __name__ == "__main__":
    test_profiler_ui_multi_mode_navigation()
