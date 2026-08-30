"""
Frontend structure and regression tests for Player Props section (Stage 15)
"""

import os
import unittest


class TestPlayerPropsFrontendStructure(unittest.TestCase):

    def setUp(self):
        self.root_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        self.index_html_path = os.path.join(self.root_dir, "web", "index.html")
        self.app_js_path = os.path.join(self.root_dir, "web", "app.js")
        self.styles_css_path = os.path.join(self.root_dir, "web", "styles.css")

    def test_sidebar_nav_link_exists(self):
        with open(self.index_html_path, "r", encoding="utf-8") as f:
            content = f.read()

        self.assertIn('data-view="playerprops"', content)
        self.assertIn('Player Props', content)
        self.assertIn('id="nav-props-count"', content)

    def test_view_playerprops_section_exists(self):
        with open(self.index_html_path, "r", encoding="utf-8") as f:
            content = f.read()

        self.assertIn('id="view-playerprops"', content)
        self.assertIn('id="btn-scan-props"', content)
        self.assertIn('id="props-filter-stat"', content)
        self.assertIn('id="props-filter-position"', content)
        self.assertIn('id="props-filter-lastgames"', content)
        self.assertIn('id="props-filter-min-hitrate"', content)
        self.assertIn('id="props-filter-min-odds"', content)
        self.assertIn('id="props-filter-threshold"', content)
        self.assertIn('id="props-filter-bookmaker"', content)
        self.assertIn('id="props-filter-exec-status"', content)
        self.assertIn('id="props-filter-min-exec-edge"', content)
        self.assertIn('id="props-filter-min-stat-edge"', content)
        self.assertIn('id="props-filter-sortby"', content)
        self.assertIn('id="props-filter-search"', content)
        self.assertIn('id="props-stat-scanned"', content)
        self.assertIn('id="props-stat-polish-odds"', content)
        self.assertIn('id="props-stat-bettable"', content)
        self.assertIn('id="props-stat-ref-only"', content)
        self.assertIn('id="props-stat-uncertain"', content)
        self.assertIn('id="props-diagnostics-panel"', content)
        self.assertIn('id="props-data-table"', content)
        self.assertIn('id="prop-detail-container"', content)

    def test_javascript_props_controller_exists(self):
        with open(self.app_js_path, "r", encoding="utf-8") as f:
            content = f.read()

        self.assertIn('playerProps:', content)
        self.assertIn('scanProps', content)
        self.assertIn('fetchPropsResults', content)
        self.assertIn('fetchPropDetail', content)
        self.assertIn('fetchPropsHealth', content)
        self.assertIn("viewName === 'playerprops'", content)
        self.assertIn('loadPlayerPropsData', content)
        self.assertIn('handlePropsScan', content)
        self.assertIn('renderScanDiagnostics', content)
        self.assertIn('renderPropsTable', content)
        self.assertIn('showPropDetail', content)
        self.assertIn('updatePropsSummaryMetrics', content)

    def test_rendering_contract_and_empty_states(self):
        with open(self.app_js_path, "r", encoding="utf-8") as f:
            content = f.read()

        # Ensures distinct empty states
        self.assertIn('No player props scanned yet', content)
        self.assertIn('No player props match the current filters', content)

        # Ensures 3-state inspector (LOADING, ERROR, SUCCESS)
        self.assertIn('Loading intelligence breakdown...', content)
        self.assertIn('Unable to load prop details', content)
        self.assertIn('Edge Comparison & Math', content)

        # Ensures single source of truth Decision Engine object reading
        self.assertIn('const decision = item.decision || {};', content)
        self.assertIn('decision.score !== undefined', content)
        self.assertIn('decision.raw_edge_pct !== undefined', content)

        # Ensures single source of truth & event guard
        self.assertIn('_eventsInitialized', content)

    def test_stat_switching_and_dynamic_line_selector(self):
        with open(self.app_js_path, "r", encoding="utf-8") as f:
            content = f.read()

        # Ensures dynamic line selector function and stat synchronization
        self.assertIn('function updateLineSelectorOptions', content)
        self.assertIn('getStatDisplayName', content)
        self.assertIn('_activePropsRequestId', content)
        self.assertIn('document.getElementById(\'props-filter-stat\')', content)
        self.assertIn('handlePropsScan()', content)


if __name__ == "__main__":
    unittest.main()

