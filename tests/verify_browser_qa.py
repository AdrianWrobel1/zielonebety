import json
import os
import sys
import time
from playwright.sync_api import sync_playwright

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

SCREENSHOT_DIR = r"C:\Users\Adrian\.gemini\antigravity\brain\df0acbee-c79e-40e1-8d83-00a027f3f120\qa_screenshots"
os.makedirs(SCREENSHOT_DIR, exist_ok=True)

def run_qa():
    print("[QA] Starting Playwright Browser QA Suite for Actionable Discrepancies...")
    results = {}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1366, "height": 850})
        page = context.new_page()

        def trigger_and_wait(action_fn):
            with page.expect_response(lambda r: "/opportunities/explorer" in r.url and r.status == 200, timeout=10000):
                action_fn()
            page.wait_for_timeout(400)

        # Helper: check horizontal overflow
        def check_overflow():
            return page.evaluate("""() => {
                const doc = document.documentElement;
                return {
                    scrollWidth: doc.scrollWidth,
                    clientWidth: doc.clientWidth,
                    hasOverflow: doc.scrollWidth > doc.clientWidth
                };
            }""")

        # -------------------------------------------------------------
        # SCENARIO 1: Baseline Discrepancy View without Actionable Filter
        # -------------------------------------------------------------
        print("\n--- Scenario 1: Baseline Discrepancy View ---")
        page.goto("http://127.0.0.1:8000/#opportunities")
        page.wait_for_timeout(1000)

        # Click Quote Discrepancy category tab
        trigger_and_wait(lambda: page.click("#tab-opp-discrepancy"))

        s1_data = page.evaluate("""() => {
            const btn = document.getElementById('btn-filter-actionable-disc');
            const items = Array.from(document.querySelectorAll('.opp-feed-item')).map(el => {
                const title = el.querySelector('.opp-feed-primary-title')?.innerText?.trim();
                const fixture = el.querySelector('.opp-feed-sub-fixture')?.innerText?.trim();
                const discText = el.querySelector('.opp-feed-ev-value')?.innerText?.trim();
                const refPill = el.querySelector('.ref-pill')?.innerText?.trim();
                return { title, fixture, discText, refPill };
            });
            return {
                btnPressed: btn?.getAttribute('aria-pressed'),
                btnActive: btn?.classList.contains('active'),
                totalCountText: document.getElementById('opp-feed-total-count')?.innerText?.trim(),
                itemCount: items.length,
                sampleItems: items.slice(0, 5)
            };
        }""")

        print(f"Scenario 1 Data: {json.dumps(s1_data, indent=2)}")
        assert s1_data["btnPressed"] == "false", "Actionable button should be inactive"
        assert s1_data["btnActive"] is False, "Actionable button should not have active class"
        assert s1_data["itemCount"] > 0, "Feed should contain discrepancy items"

        page.screenshot(path=os.path.join(SCREENSHOT_DIR, "01_baseline_discrepancy_view.png"))
        results["scenario_1"] = "PASSED"

        # -------------------------------------------------------------
        # SCENARIO 2: Activate "Actionable" Filter
        # -------------------------------------------------------------
        print("\n--- Scenario 2: Activate Actionable Filter ---")
        trigger_and_wait(lambda: page.click("#btn-filter-actionable-disc"))

        s2_data = page.evaluate("""() => {
            const btn = document.getElementById('btn-filter-actionable-disc');
            const tabDisc = document.getElementById('tab-opp-discrepancy');
            const sortSelect = document.getElementById('filter-sort');
            const minDiscInput = document.getElementById('filter-min-discrepancy');
            const maxLowerInput = document.getElementById('filter-max-lower-odds');
            const chips = Array.from(document.querySelectorAll('.opp-chip')).map(c => c.innerText.trim());
            return {
                btnPressed: btn?.getAttribute('aria-pressed'),
                btnActive: btn?.classList.contains('active'),
                tabSelected: tabDisc?.classList.contains('active'),
                sortVal: sortSelect?.value,
                minDiscVal: minDiscInput?.value,
                maxLowerVal: maxLowerInput?.value,
                chips: chips
            };
        }""")

        print(f"Scenario 2 Data: {json.dumps(s2_data, indent=2)}")
        assert s2_data["btnPressed"] == "true", "Actionable button should have aria-pressed='true'"
        assert s2_data["btnActive"] is True, "Actionable button should have 'active' class"
        assert s2_data["tabSelected"] is True, "Quote Discrepancy tab should be active"
        assert s2_data["sortVal"] == "actionability", "Sort should be 'actionability'"
        assert float(s2_data["minDiscVal"]) == 10.0, "Min Discrepancy should be set to 10"
        assert float(s2_data["maxLowerVal"]) == 2.50, "Max Lower Odds should be set to 2.50"

        page.screenshot(path=os.path.join(SCREENSHOT_DIR, "02_actionable_activated.png"))
        results["scenario_2"] = "PASSED"

        # -------------------------------------------------------------
        # SCENARIO 3: Feed Verification with Actionable Active
        # -------------------------------------------------------------
        print("\n--- Scenario 3: Feed Verification with Actionable Active ---")
        s3_data = page.evaluate("""() => {
            const items = Array.from(document.querySelectorAll('.opp-feed-item')).map(el => {
                const title = el.querySelector('.opp-feed-primary-title')?.innerText?.trim();
                const fixture = el.querySelector('.opp-feed-sub-fixture')?.innerText?.trim();
                const discText = el.querySelector('.opp-feed-ev-value')?.innerText?.trim();
                const refPill = el.querySelector('.ref-pill')?.innerText?.trim();
                const lowerOdds = parseFloat(el.querySelector('.ref-pill .ref-v')?.innerText?.trim());
                const hasWarningPill = Boolean(el.querySelector('.ref-pill .text-warning'));
                return { title, fixture, discText, refPill, lowerOdds, hasWarningPill };
            });
            return {
                totalCount: document.getElementById('opp-feed-total-count')?.innerText?.trim(),
                count: items.length,
                items: items
            };
        }""")

        print(f"Scenario 3 Count: {s3_data['count']}, Total: {s3_data['totalCount']}")
        print(f"Top 5 items: {json.dumps(s3_data['items'][:5], indent=2)}")

        # Every item must satisfy disc >= 10% and lower_odds <= 2.50
        for it in s3_data["items"]:
            val_str = it["discText"].replace("+", "").replace("%", "")
            disc_val = float(val_str)
            assert disc_val >= 10.0, f"Item {it['title']} has discrepancy {disc_val}% < 10.0%"
            assert it["lowerOdds"] <= 2.50, f"Item {it['title']} has lower odds {it['lowerOdds']} > 2.50"
            assert it["hasWarningPill"] is True, "Pill must use text-warning styling"

        titles = [it["title"] for it in s3_data["items"]]
        print(f"Sample titles found: {titles[:5]}")
        assert "Jude Bellingham" in titles, "Jude Bellingham (7.00 vs 1.83) must be in actionable feed"
        assert "Bernardo Silva" not in titles, "Bernardo Silva (15.00 vs 5.00) must NOT be in actionable feed"

        page.screenshot(path=os.path.join(SCREENSHOT_DIR, "03_actionable_feed_verified.png"))
        results["scenario_3"] = "PASSED"

        # -------------------------------------------------------------
        # SCENARIO 4: Adjust Max Lower Odds Dynamically
        # -------------------------------------------------------------
        print("\n--- Scenario 4: Adjust Max Lower Odds Dynamically ---")
        tray_expanded = page.evaluate("() => document.getElementById('opp-advanced-filters-panel').style.display !== 'none'")
        if not tray_expanded:
            page.click("#btn-toggle-opp-advanced-filters")
            page.wait_for_timeout(300)

        # Set Max Lower Odds to 2.00
        def apply_max_lower_200():
            page.fill("#filter-max-lower-odds", "2.00")
            page.dispatch_event("#filter-max-lower-odds", "change")

        trigger_and_wait(apply_max_lower_200)

        s4_200 = page.evaluate("""() => {
            const items = Array.from(document.querySelectorAll('.opp-feed-item')).map(el => {
                const title = el.querySelector('.opp-feed-primary-title')?.innerText?.trim();
                const lowerOdds = parseFloat(el.querySelector('.ref-pill .ref-v')?.innerText?.trim());
                return { title, lowerOdds };
            });
            return {
                totalCount: document.getElementById('opp-feed-total-count')?.innerText?.trim(),
                items: items
            };
        }""")
        print(f"Max Lower Odds = 2.00 -> Count: {len(s4_200['items'])}, Total: {s4_200['totalCount']}")
        for it in s4_200["items"]:
            assert it["lowerOdds"] <= 2.00, f"Item {it['title']} has lower odds {it['lowerOdds']} > 2.00"

        # Set Max Lower Odds to 1.50
        def apply_max_lower_150():
            page.fill("#filter-max-lower-odds", "1.50")
            page.dispatch_event("#filter-max-lower-odds", "change")

        trigger_and_wait(apply_max_lower_150)

        s4_150 = page.evaluate("""() => {
            const items = Array.from(document.querySelectorAll('.opp-feed-item')).map(el => {
                const title = el.querySelector('.opp-feed-primary-title')?.innerText?.trim();
                const lowerOdds = parseFloat(el.querySelector('.ref-pill .ref-v')?.innerText?.trim());
                return { title, lowerOdds };
            });
            return {
                totalCount: document.getElementById('opp-feed-total-count')?.innerText?.trim(),
                items: items
            };
        }""")
        print(f"Max Lower Odds = 1.50 -> Count: {len(s4_150['items'])}, Total: {s4_150['totalCount']}")
        for it in s4_150["items"]:
            assert it["lowerOdds"] <= 1.50, f"Item {it['title']} has lower odds {it['lowerOdds']} > 1.50"

        page.screenshot(path=os.path.join(SCREENSHOT_DIR, "04_adjusted_max_lower_odds.png"))
        results["scenario_4"] = "PASSED"

        # -------------------------------------------------------------
        # SCENARIO 5: Adjust Min Discrepancy % Dynamically
        # -------------------------------------------------------------
        print("\n--- Scenario 5: Adjust Min Discrepancy % Dynamically ---")
        # Reset Max Lower Odds to 2.50
        trigger_and_wait(lambda: (
            page.fill("#filter-max-lower-odds", "2.50"),
            page.dispatch_event("#filter-max-lower-odds", "change")
        ))

        # Set Min Discrepancy to 25%
        trigger_and_wait(lambda: (
            page.fill("#filter-min-discrepancy", "25.0"),
            page.dispatch_event("#filter-min-discrepancy", "change")
        ))

        s5_25 = page.evaluate("""() => {
            const items = Array.from(document.querySelectorAll('.opp-feed-item')).map(el => {
                const discText = el.querySelector('.opp-feed-ev-value')?.innerText?.trim();
                return parseFloat(discText.replace('+', '').replace('%', ''));
            });
            return {
                totalCount: document.getElementById('opp-feed-total-count')?.innerText?.trim(),
                count: items.length,
                minFound: Math.min(...items)
            };
        }""")
        print(f"Min Discrepancy = 25% -> Count: {s5_25['count']}, Min Found: {s5_25['minFound']}%")
        assert s5_25["minFound"] >= 25.0, f"Minimum discrepancy found {s5_25['minFound']} < 25.0%"

        # Set Min Discrepancy to 50%
        trigger_and_wait(lambda: (
            page.fill("#filter-min-discrepancy", "50.0"),
            page.dispatch_event("#filter-min-discrepancy", "change")
        ))

        s5_50 = page.evaluate("""() => {
            const items = Array.from(document.querySelectorAll('.opp-feed-item')).map(el => {
                const discText = el.querySelector('.opp-feed-ev-value')?.innerText?.trim();
                return parseFloat(discText.replace('+', '').replace('%', ''));
            });
            return {
                totalCount: document.getElementById('opp-feed-total-count')?.innerText?.trim(),
                count: items.length,
                minFound: Math.min(...items)
            };
        }""")
        print(f"Min Discrepancy = 50% -> Count: {s5_50['count']}, Min Found: {s5_50['minFound']}%")
        assert s5_50["minFound"] >= 50.0, f"Minimum discrepancy found {s5_50['minFound']} < 50.0%"

        page.screenshot(path=os.path.join(SCREENSHOT_DIR, "05_adjusted_min_discrepancy.png"))
        results["scenario_5"] = "PASSED"

        # -------------------------------------------------------------
        # SCENARIO 6: Active Filter Chips & Manual Threshold Clearance
        # -------------------------------------------------------------
        print("\n--- Scenario 6: Active Filter Chips & Manual Threshold Clearance ---")
        chips = page.evaluate("() => Array.from(document.querySelectorAll('.opp-chip')).map(c => c.innerText.trim())")
        print(f"Active filter chips before clearing: {len(chips)} chips found")
        assert any("Min Discrepancy" in c for c in chips), "Min Discrepancy chip should be visible"
        assert any("Max Lower Odds" in c for c in chips), "Max Lower Odds chip should be visible"

        trigger_and_wait(lambda: page.click("#btn-clear-thresholds"))

        s6_data = page.evaluate("""() => {
            const btn = document.getElementById('btn-filter-actionable-disc');
            const minDisc = document.getElementById('filter-min-discrepancy')?.value;
            const maxLower = document.getElementById('filter-max-lower-odds')?.value;
            return {
                btnPressed: btn?.getAttribute('aria-pressed'),
                btnActive: btn?.classList.contains('active'),
                minDisc: minDisc,
                maxLower: maxLower
            };
        }""")
        print(f"Scenario 6 After Clear: {json.dumps(s6_data, indent=2)}")
        assert s6_data["btnPressed"] == "false", "Actionable button must deactivate when thresholds are cleared"
        assert s6_data["btnActive"] is False, "Actionable button must not have active class"
        assert s6_data["minDisc"] == "", "Min Discrepancy input must be cleared"
        assert s6_data["maxLower"] == "", "Max Lower Odds input must be cleared"

        page.screenshot(path=os.path.join(SCREENSHOT_DIR, "06_thresholds_cleared.png"))
        results["scenario_6"] = "PASSED"

        # -------------------------------------------------------------
        # SCENARIO 7: Deactivate Actionable Button Directly (Toggle Off)
        # -------------------------------------------------------------
        print("\n--- Scenario 7: Deactivate Actionable Button Directly ---")
        # Click to turn ON
        trigger_and_wait(lambda: page.click("#btn-filter-actionable-disc"))
        assert page.evaluate("() => document.getElementById('btn-filter-actionable-disc').getAttribute('aria-pressed')") == "true"

        # Click again to turn OFF
        trigger_and_wait(lambda: page.click("#btn-filter-actionable-disc"))

        s7_data = page.evaluate("""() => {
            const btn = document.getElementById('btn-filter-actionable-disc');
            const minDisc = document.getElementById('filter-min-discrepancy')?.value;
            const maxLower = document.getElementById('filter-max-lower-odds')?.value;
            const items = Array.from(document.querySelectorAll('.opp-feed-item')).map(el => {
                return parseFloat(el.querySelector('.ref-pill .ref-v')?.innerText?.trim());
            });
            const hasHighOdds = items.some(o => o > 2.50);
            return {
                btnPressed: btn?.getAttribute('aria-pressed'),
                btnActive: btn?.classList.contains('active'),
                minDisc: minDisc,
                maxLower: maxLower,
                hasHighOddsReturned: hasHighOdds,
                itemCount: items.length
            };
        }""")
        print(f"Scenario 7 Data: {json.dumps(s7_data, indent=2)}")
        assert s7_data["btnPressed"] == "false", "Button should be toggled OFF"
        assert s7_data["minDisc"] == "", "Min Discrepancy should be reset"
        assert s7_data["maxLower"] == "", "Max Lower Odds should be reset"
        assert s7_data["hasHighOddsReturned"] is True, "High odds items (> 2.50) must return to feed"

        page.screenshot(path=os.path.join(SCREENSHOT_DIR, "07_actionable_toggled_off.png"))
        results["scenario_7"] = "PASSED"

        # -------------------------------------------------------------
        # SCENARIO 8: Inspector Verification
        # -------------------------------------------------------------
        print("\n--- Scenario 8: Inspector Verification ---")
        trigger_and_wait(lambda: page.click("#btn-filter-actionable-disc"))

        # Click the first actionable item to inspect
        page.click(".opp-feed-item")
        page.wait_for_timeout(500)

        s8_data = page.evaluate("""() => {
            const cards = Array.from(document.querySelectorAll('.insp-price-card'));
            let card1Label = null;
            let card2Label = null;
            let card2Val = null;
            if (cards.length >= 2) {
                card1Label = cards[0].querySelector('.price-label')?.innerText?.trim();
                card2Label = cards[1].querySelector('.price-label')?.innerText?.trim();
                card2Val = cards[1].querySelector('.price-val')?.innerText?.trim();
            }
            const explanation = document.querySelector('.insp-explanation-card, .insp-market-subtitle')?.innerText?.trim();
            return {
                cardsCount: cards.length,
                card1Label: card1Label,
                card2Label: card2Label,
                card2Val: card2Val,
                explanation: explanation ? explanation.substring(0, 200) : null
            };
        }""")

        print(f"Scenario 8 Data: {json.dumps(s8_data, indent=2)}")
        assert s8_data["card2Label"] == "LOWER EXECUTABLE ODDS", f"Expected 'LOWER EXECUTABLE ODDS', got '{s8_data['card2Label']}'"
        assert s8_data["card2Val"] and s8_data["card2Val"] != "—", "Card 2 must display the lower executable odds value"
        assert float(s8_data["card2Val"]) <= 2.50, f"Inspected item lower odds {s8_data['card2Val']} must be <= 2.50"

        page.screenshot(path=os.path.join(SCREENSHOT_DIR, "08_inspector_discrepancy_details.png"))
        results["scenario_8"] = "PASSED"

        # -------------------------------------------------------------
        # SCENARIO 9: Sort Switching with Actionable Active
        # -------------------------------------------------------------
        print("\n--- Scenario 9: Sort Switching ---")
        # Switch to Discrepancy % sort
        trigger_and_wait(lambda: page.select_option("#filter-sort", "discrepancy"))

        s9_disc = page.evaluate("""() => {
            const items = Array.from(document.querySelectorAll('.opp-feed-item')).map(el => {
                const text = el.querySelector('.opp-feed-ev-value')?.innerText?.trim();
                return parseFloat(text.replace('+', '').replace('%', ''));
            });
            const isDesc = items.slice(0, 10).every((v, i, a) => i === 0 || a[i - 1] >= v);
            return { isDesc, sample: items.slice(0, 5) };
        }""")
        print(f"Sorted by Discrepancy DESC: {s9_disc}")
        assert s9_disc["isDesc"] is True, "Discrepancy sort must be descending"

        # Switch to Odds sort
        trigger_and_wait(lambda: page.select_option("#filter-sort", "odds"))

        s9_odds = page.evaluate("""() => {
            const items = Array.from(document.querySelectorAll('.opp-feed-item')).map(el => {
                const text = el.querySelector('.bm-odds')?.innerText?.trim();
                return parseFloat(text);
            });
            const isDesc = items.slice(0, 10).every((v, i, a) => i === 0 || a[i - 1] >= v);
            return { isDesc, sample: items.slice(0, 5) };
        }""")
        print(f"Sorted by Odds DESC: {s9_odds}")
        assert s9_odds["isDesc"] is True, "Odds sort must be descending"

        # Restore Actionability sort
        trigger_and_wait(lambda: page.select_option("#filter-sort", "actionability"))
        assert page.evaluate("() => document.getElementById('filter-sort').value") == "actionability"

        page.screenshot(path=os.path.join(SCREENSHOT_DIR, "09_sort_switching.png"))
        results["scenario_9"] = "PASSED"

        # -------------------------------------------------------------
        # SCENARIO 10: Complete Discrepancy Universe Preservation
        # -------------------------------------------------------------
        print("\n--- Scenario 10: Universe Preservation ---")
        trigger_and_wait(lambda: page.click("#btn-filter-actionable-disc"))

        s10_data = page.evaluate("""() => {
            const countText = document.getElementById('opp-feed-total-count')?.innerText?.trim();
            const tabCount = document.getElementById('tab-count-discrepancy')?.innerText?.trim();
            return { totalCount: countText, tabCount: tabCount };
        }""")
        print(f"Scenario 10 Data: {s10_data}")
        assert int(s10_data["tabCount"]) >= 4000, f"Discrepancy universe count {s10_data['tabCount']} must be fully preserved (>= 4000)"

        page.screenshot(path=os.path.join(SCREENSHOT_DIR, "10_universe_preservation.png"))
        results["scenario_10"] = "PASSED"

        # -------------------------------------------------------------
        # SCENARIO 11: Top 5 European Leagues Toggle Composition
        # -------------------------------------------------------------
        print("\n--- Scenario 11: Top 5 European Leagues Toggle Composition ---")
        trigger_and_wait(lambda: page.click("#btn-filter-actionable-disc"))
        trigger_and_wait(lambda: page.click("#btn-filter-top5"))

        s11_data = page.evaluate("""() => {
            const btnTop5 = document.getElementById('btn-filter-top5');
            const btnActionable = document.getElementById('btn-filter-actionable-disc');
            const totalCount = document.getElementById('opp-feed-total-count')?.innerText?.trim();
            const items = Array.from(document.querySelectorAll('.opp-feed-item')).map(el => {
                const title = el.querySelector('.opp-feed-primary-title')?.innerText?.trim();
                const fixture = el.querySelector('.opp-feed-sub-fixture')?.innerText?.trim();
                const lowerOdds = parseFloat(el.querySelector('.ref-pill .ref-v')?.innerText?.trim());
                const discText = el.querySelector('.opp-feed-ev-value')?.innerText?.trim();
                const discVal = parseFloat(discText.replace('+', '').replace('%', ''));
                return { title, fixture, lowerOdds, discVal };
            });
            return {
                top5Pressed: btnTop5?.getAttribute('aria-pressed'),
                actionablePressed: btnActionable?.getAttribute('aria-pressed'),
                totalCount: totalCount,
                itemCount: items.length,
                sample: items.slice(0, 5)
            };
        }""")
        print(f"Scenario 11 Data: {json.dumps(s11_data, indent=2)}")
        assert s11_data["top5Pressed"] == "true", "Top 5 button must be pressed"
        assert s11_data["actionablePressed"] == "true", "Actionable button must be pressed"
        for it in s11_data["sample"]:
            assert it["lowerOdds"] <= 2.50, f"Lower odds {it['lowerOdds']} must be <= 2.50"
            assert it["discVal"] >= 10.0, f"Discrepancy {it['discVal']}% must be >= 10.0%"

        page.screenshot(path=os.path.join(SCREENSHOT_DIR, "11_top5_composition.png"))
        results["scenario_11"] = "PASSED"

        # Turn Top 5 off
        trigger_and_wait(lambda: page.click("#btn-filter-top5"))

        # -------------------------------------------------------------
        # RESPONSIVE TESTING (1366, 1024, 768, 375) & ZERO HORIZONTAL OVERFLOW
        # -------------------------------------------------------------
        print("\n--- Responsive Testing & Viewports ---")
        viewports = [
            (1366, 850, "1366_desktop"),
            (1024, 768, "1024_small_desktop"),
            (768, 1024, "768_tablet"),
            (375, 812, "375_mobile")
        ]

        for w, h, name in viewports:
            page.set_viewport_size({"width": w, "height": h})
            page.wait_for_timeout(500)
            overflow = check_overflow()
            print(f"Viewport {w}x{h} ({name}) -> scrollWidth: {overflow['scrollWidth']}, clientWidth: {overflow['clientWidth']}, hasOverflow: {overflow['hasOverflow']}")
            assert not overflow["hasOverflow"], f"Horizontal overflow detected at viewport {w}x{h}!"
            page.screenshot(path=os.path.join(SCREENSHOT_DIR, f"responsive_{name}.png"))
            results[f"responsive_{name}"] = "PASSED"

        # -------------------------------------------------------------
        # THEME TOGGLE (DARK & LIGHT)
        # -------------------------------------------------------------
        print("\n--- Theme Testing (Dark & Light) ---")
        page.set_viewport_size({"width": 1366, "height": 850})
        page.screenshot(path=os.path.join(SCREENSHOT_DIR, "theme_dark.png"))

        page.click("#theme-toggle-btn")
        page.wait_for_timeout(500)
        theme = page.evaluate("() => document.documentElement.getAttribute('data-theme')")
        print(f"Theme switched to: {theme}")
        page.screenshot(path=os.path.join(SCREENSHOT_DIR, "theme_light.png"))

        page.click("#theme-toggle-btn")
        page.wait_for_timeout(300)

        browser.close()

    print("\n" + "="*50)
    print("ALL BROWSER QA SCENARIOS PASSED PERFECTLY!")
    print("="*50)
    print(json.dumps(results, indent=2))

if __name__ == "__main__":
    run_qa()
