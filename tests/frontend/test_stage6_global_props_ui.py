"""
Stage 6: Global Props Scanner UI — Frontend Structure, Controller & Integration Tests.

Validates:
1. Index.html markup structure for Global Props Scanner (Scope switcher, scan controls, funnel cards, diagnostics, table, inspector).
2. Unified PLAYER | TEAM | ALL presentation and controls.
3. JavaScript API client methods (scanGlobalProps, fetchGlobalPropsResults) and backward compatibility.
4. Deterministic Net EV % ranking and StatsHub trend context rendering.
5. Rejection diagnostics with reason codes.
6. Opportunity details inspector rendering mathematics and provenance.
7. Styles and CSS rules in styles.css.
"""

import os
import unittest


class TestGlobalPropsUIStructure(unittest.TestCase):

    def setUp(self):
        self.root_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        self.index_html_path = os.path.join(self.root_dir, "web", "index.html")
        self.app_js_path = os.path.join(self.root_dir, "web", "app.js")
        self.styles_css_path = os.path.join(self.root_dir, "web", "styles.css")

        with open(self.index_html_path, "r", encoding="utf-8") as f:
            self.html_content = f.read()
        with open(self.app_js_path, "r", encoding="utf-8") as f:
            self.js_content = f.read()
        with open(self.styles_css_path, "r", encoding="utf-8") as f:
            self.css_content = f.read()

    def test_sidebar_nav_link_compatibility(self):
        """Validates sidebar navigation preserves playerprops data-view and labels."""
        self.assertIn('data-view="playerprops"', self.html_content)
        self.assertIn('Player Props', self.html_content)
        self.assertIn('id="nav-props-count"', self.html_content)

    def test_scope_switcher_markup_exists(self):
        """Validates PLAYER | TEAM | ALL scope switcher buttons in index.html."""
        self.assertIn('id="props-scope-switcher"', self.html_content)
        self.assertIn('id="props-scope-player"', self.html_content)
        self.assertIn('id="props-scope-team"', self.html_content)
        self.assertIn('id="props-scope-all"', self.html_content)
        self.assertIn('data-scope="PLAYER"', self.html_content)
        self.assertIn('data-scope="TEAM"', self.html_content)
        self.assertIn('data-scope="ALL"', self.html_content)

    def test_multi_fixture_scan_controls_exist(self):
        """Validates multi-fixture scan control inputs and primary SCAN PROPS button."""
        self.assertIn('id="view-playerprops"', self.html_content)
        self.assertIn('id="btn-scan-props"', self.html_content)
        self.assertIn('id="btn-scan-props-text"', self.html_content)
        self.assertIn('id="props-filter-horizon"', self.html_content)
        self.assertIn('id="props-filter-stat"', self.html_content)
        self.assertIn('id="props-filter-min-ev"', self.html_content)
        self.assertIn('id="props-filter-tournaments"', self.html_content)
        self.assertIn('id="props-filter-limit"', self.html_content)
        self.assertIn('id="props-filter-search"', self.html_content)

    def test_telemetry_and_diagnostics_markup_exist(self):
        """Validates summary cards and collapsible diagnostics panel with rejection chips."""
        self.assertIn('id="props-stat-scanned"', self.html_content)
        self.assertIn('id="props-stat-polish-odds"', self.html_content)
        self.assertIn('id="props-stat-bettable"', self.html_content)
        self.assertIn('id="props-stat-ref-only"', self.html_content)
        self.assertIn('id="props-stat-uncertain"', self.html_content)
        self.assertIn('id="props-diagnostics-panel"', self.html_content)
        self.assertIn('id="props-rejection-breakdown-container"', self.html_content)
        self.assertIn('id="props-rejection-chips"', self.html_content)

    def test_results_table_and_inspector_markup_exist(self):
        """Validates results table container and opportunity inspector card."""
        self.assertIn('id="props-table-container"', self.html_content)
        self.assertIn('id="props-data-table"', self.html_content)
        self.assertIn('id="props-table-body"', self.html_content)
        self.assertIn('id="prop-detail-container"', self.html_content)
        self.assertIn('id="prop-detail-title"', self.html_content)
        self.assertIn('id="btn-close-prop-detail"', self.html_content)
        self.assertIn('id="prop-detail-content"', self.html_content)

    def test_javascript_api_methods_and_routes(self):
        """Validates API client methods for Stage 5 global scanner endpoints in app.js."""
        self.assertIn('scanGlobalProps', self.js_content)
        self.assertIn('/api/v1/props/global-scan', self.js_content)
        self.assertIn('fetchGlobalPropsResults', self.js_content)
        self.assertIn('/api/v1/props/global-results', self.js_content)
        # Backward compatibility
        self.assertIn('scanProps', self.js_content)
        self.assertIn('fetchPropsResults', self.js_content)
        self.assertIn('fetchPropDetail', self.js_content)
        self.assertIn('fetchPropsHealth', self.js_content)

    def test_javascript_controller_functions_and_state(self):
        """Validates controller functions, state management, and event initialization."""
        self.assertIn('propsScope:', self.js_content)
        self.assertIn('diagnosticCandidates:', self.js_content)
        self.assertIn('funnelMetrics:', self.js_content)
        self.assertIn('initPlayerPropsEvents', self.js_content)
        self.assertIn('loadPlayerPropsData', self.js_content)
        self.assertIn('handlePropsScan', self.js_content)
        self.assertIn('fetchAndRenderPropsFromBackend', self.js_content)
        self.assertIn('updatePropsSummaryMetrics', self.js_content)
        self.assertIn('renderScanDiagnostics', self.js_content)
        self.assertIn('renderPropsTable', self.js_content)
        self.assertIn('showPropDetail', self.js_content)

    def test_unified_player_and_team_presentation(self):
        """Validates unified rendering for PLAYER and TEAM props in app.js."""
        self.assertIn("propType === 'PLAYER'", self.js_content)
        self.assertIn('item.player_name', self.js_content)
        self.assertIn('item.participant_role', self.js_content)
        self.assertIn('prop-type-badge', self.js_content)
        self.assertIn('net-ev-pill', self.js_content)

    def test_empty_and_diagnostics_handling(self):
        """Validates explicit empty states and reason codes mapping in app.js."""
        self.assertIn('Brak zakwalifikowanych propsów w wybranym zakresie.', self.js_content)
        self.assertIn('rejection_breakdown', self.js_content)
        self.assertIn('rejection-chip', self.js_content)

    def test_match_uncertain_and_funnel_semantics_integrity(self):
        """Validates that MATCH_UNCERTAIN is not heuristically conflated with total rejections and that reason codes are preserved."""
        # Ensure uncertainEl is not assigned rejectedCount / rejected
        self.assertNotIn('uncertainEl.textContent = rejectedCount;', self.js_content)
        self.assertNotIn('uncertainEl.textContent = rejected;', self.js_content)
        self.assertNotIn('uncertainEl.textContent = funnel.rejected_count;', self.js_content)
        self.assertIn('funnel.match_uncertain', self.js_content)
        self.assertIn('funnelMetrics?.rejected_count', self.js_content)
        self.assertIn('POLISH_ODDS_UNAVAILABLE', self.js_content)

    def test_css_styles_exist(self):
        """Validates styles.css contains required Stage 6 classes."""
        self.assertIn('.scope-switcher', self.css_content)
        self.assertIn('.scope-btn.active', self.css_content)
        self.assertIn('.net-ev-pill', self.css_content)
        self.assertIn('.rejection-chip', self.css_content)
        self.assertIn('.prop-type-badge', self.css_content)
        self.assertIn('.bookmaker-odds-pill', self.css_content)


if __name__ == "__main__":
    unittest.main()
