"""
Deep Runtime QA Verification for Position / Role and Stat Line / Threshold.
Executes against running application http://127.0.0.1:8000/playerprops.
Verifies real DOM table rows, position badges, line labels, filter requests, and captures screenshots.
"""

import os
import time
from playwright.sync_api import sync_playwright


def test_runtime_qa_position_and_threshold_deep():
    screenshots_dir = os.path.join(os.getcwd(), "test_evidence_screenshots")
    os.makedirs(screenshots_dir, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1366, "height": 768})
        page = context.new_page()

        # Step 1: Navigate to /playerprops
        print("[Step 1] Loading /playerprops...")
        page.goto("http://127.0.0.1:8000/playerprops")
        page.wait_for_selector("#view-playerprops")
        time.sleep(2)

        # Open advanced filters panel
        panel = page.locator("#props-advanced-filters-panel")
        if not panel.is_visible():
            page.click("#btn-toggle-advanced-filters")
            panel.wait_for(state="visible", timeout=3000)

        # Switch to diagnostics tab to view all candidates
        page.click("#props-tab-diagnostics")
        time.sleep(2)

        # Verify initial default state rows
        rows = page.locator("#props-table-body tr")
        initial_count = rows.count()
        print(f"[Step 2] Initial candidate rows count: {initial_count}")
        assert initial_count == 50, f"Expected 50 candidate rows, got {initial_count}"
        page.screenshot(path=os.path.join(screenshots_dir, "01_initial_default_state.png"))

        # Step 3: Select Position = Forwards (F)
        print("[Step 3] Selecting Position = Forwards (F)...")
        page.select_option("#props-filter-position", "F")
        time.sleep(2)

        f_rows = page.locator("#props-table-body tr")
        f_count = f_rows.count()
        print(f"[Step 3] Forwards rows count: {f_count}")
        assert f_count == 26, f"Expected 26 forward rows, got {f_count}"
        for i in range(min(f_count, 5)):
            row_text = f_rows.nth(i).text_content()
            assert "PLAYER" in row_text, f"Row {i} must be a player prop!"
            assert "F" in row_text, f"Row {i} must show forward position badge!"
        page.screenshot(path=os.path.join(screenshots_dir, "02_position_forwards.png"))

        # Step 4: Select Position = Midfielders (M)
        print("[Step 4] Selecting Position = Midfielders (M)...")
        page.select_option("#props-filter-position", "M")
        time.sleep(2)

        m_rows = page.locator("#props-table-body tr")
        m_count = m_rows.count()
        print(f"[Step 4] Midfielders rows count: {m_count}")
        assert m_count == 19, f"Expected 19 midfielder rows, got {m_count}"
        for i in range(min(m_count, 5)):
            row_text = m_rows.nth(i).text_content()
            assert "PLAYER" in row_text, f"Row {i} must be a player prop!"
            assert "M" in row_text, f"Row {i} must show midfielder position badge!"
        page.screenshot(path=os.path.join(screenshots_dir, "03_position_midfielders.png"))

        # Step 5: Select Position = Defenders (D)
        print("[Step 5] Selecting Position = Defenders (D)...")
        page.select_option("#props-filter-position", "D")
        time.sleep(2)

        d_rows = page.locator("#props-table-body tr")
        d_count = d_rows.count()
        print(f"[Step 5] Defenders rows count: {d_count}")
        assert d_count == 5, f"Expected 5 defender rows, got {d_count}"
        for i in range(min(d_count, 5)):
            row_text = d_rows.nth(i).text_content()
            assert "PLAYER" in row_text, f"Row {i} must be a player prop!"
            assert "D" in row_text, f"Row {i} must show defender position badge!"
        page.screenshot(path=os.path.join(screenshots_dir, "04_position_defenders.png"))

        # Step 6: Reset Position to All Positions
        print("[Step 6] Resetting Position to All Positions...")
        page.select_option("#props-filter-position", "D,M,F")
        time.sleep(2)

        all_pos_rows = page.locator("#props-table-body tr").count()
        print(f"[Step 6] Returned to All Positions count: {all_pos_rows}")
        assert all_pos_rows == 50, f"Expected 50 rows on return to All Positions, got {all_pos_rows}"

        # Step 7: Select Stat Line / Threshold = Over 0.5
        print("[Step 7] Selecting Stat Line / Threshold = 0.5...")
        page.select_option("#props-filter-threshold", "0.5")
        time.sleep(2)

        t_rows = page.locator("#props-table-body tr")
        t_count = t_rows.count()
        print(f"[Step 7] Threshold 0.5 count: {t_count}")
        assert t_count == 50, f"Expected 50 rows for line 0.5, got {t_count}"
        for i in range(min(t_count, 5)):
            row_text = t_rows.nth(i).text_content()
            assert "0.5" in row_text, f"Row {i} must have line 0.5!"
        page.screenshot(path=os.path.join(screenshots_dir, "05_threshold_0_5.png"))

        # Step 8: Composable Filter (Position = Forwards + Threshold = 0.5)
        print("[Step 8] Combining Position = Forwards + Threshold = 0.5...")
        page.select_option("#props-filter-position", "F")
        time.sleep(2)

        comp_rows = page.locator("#props-table-body tr")
        comp_count = comp_rows.count()
        print(f"[Step 8] Composable Forwards + 0.5 count: {comp_count}")
        assert comp_count == 26, f"Expected 26 rows for Forwards + 0.5, got {comp_count}"
        for i in range(min(comp_count, 5)):
            row_text = comp_rows.nth(i).text_content()
            assert "0.5" in row_text, f"Row {i} must have line 0.5!"
            assert "F" in row_text, f"Row {i} must be a forward!"
        page.screenshot(path=os.path.join(screenshots_dir, "06_composable_position_and_threshold.png"))

        # Step 9: Reset Filters Button
        print("[Step 9] Clicking Reset Filters Button...")
        page.click("#btn-reset-props-filters")
        time.sleep(2)

        reset_pos = page.locator("#props-filter-position").input_value()
        reset_thresh = page.locator("#props-filter-threshold").input_value()
        print(f"[Step 9] Reset values: position='{reset_pos}', threshold='{reset_thresh}'")
        assert reset_pos == "D,M,F", "Position must reset to D,M,F!"
        assert reset_thresh in ("", "0"), "Threshold must reset to empty or 0!"
        page.screenshot(path=os.path.join(screenshots_dir, "07_after_reset_filters.png"))

        browser.close()
        print("ALL DEEP RUNTIME QA STEPS PASSED PERFECTLY!")


if __name__ == "__main__":
    test_runtime_qa_position_and_threshold_deep()
