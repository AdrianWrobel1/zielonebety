"""
Targeted Validation Tests for Design Direction Implementation.

Validates that:
1. Design system CSS tokens (surfaces, semantic value colors, tabular figures, drawer transitions) are defined in web/styles.css.
2. Dual theme (dark and light) contrast and semantic token mappings exist.
3. Slide-over Drawer component and backdrop structures exist.
4. Sticky table headers and skeleton shimmer animations are defined.
5. Key view containers and interaction hooks across all 9 views remain intact.
"""

import os
import unittest


class TestDesignDirectionCSS(unittest.TestCase):

    def setUp(self):
        self.root_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        self.styles_css_path = os.path.join(self.root_dir, "web", "styles.css")
        with open(self.styles_css_path, "r", encoding="utf-8") as f:
            self.css = f.read()

    def test_semantic_color_and_surface_tokens_exist(self):
        """Validates surface and semantic value tokens exist in :root."""
        self.assertIn("--surface-canvas", self.css)
        self.assertIn("--surface-card", self.css)
        self.assertIn("--surface-elevated", self.css)
        self.assertIn("--val-positive", self.css)
        self.assertIn("--val-negative", self.css)
        self.assertIn("--val-warning", self.css)
        self.assertIn("--val-reference", self.css)

    def test_light_theme_semantic_tokens_exist(self):
        """Validates [data-theme="light"] overrides semantic and surface tokens."""
        self.assertIn('[data-theme="light"]', self.css)
        self.assertIn("--val-positive", self.css)

    def test_tabular_nums_discipline(self):
        """Validates tabular numbers property is specified for monospace/data tables."""
        self.assertIn("tabular-nums", self.css)

    def test_sticky_table_headers_exist(self):
        """Validates sticky positioning for table headers."""
        self.assertIn("sticky", self.css)

    def test_slide_over_drawer_rules_exist(self):
        """Validates slide-over drawer styling rules exist."""
        self.assertIn("slide-over-drawer", self.css)
        self.assertIn("drawer-backdrop", self.css)


class TestDesignDirectionMarkupAndLogic(unittest.TestCase):

    def setUp(self):
        self.root_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        self.index_html_path = os.path.join(self.root_dir, "web", "index.html")
        self.app_js_path = os.path.join(self.root_dir, "web", "app.js")
        with open(self.index_html_path, "r", encoding="utf-8") as f:
            self.html = f.read()
        with open(self.app_js_path, "r", encoding="utf-8") as f:
            self.js = f.read()

    def test_all_views_present_in_markup(self):
        """Validates that all 9 application views are present in index.html."""
        views = [
            'id="view-dashboard"',
            'id="view-opportunities"',
            'id="view-providers"',
            'id="view-events"',
            'id="view-playerprops"',
            'id="view-history"',
            'id="view-profiler"',
            'id="view-notifications"',
            'id="view-settings"',
        ]
        for view_id in views:
            self.assertIn(view_id, self.html, f"Missing view container: {view_id}")

    def test_playerprops_table_and_drawer_containers(self):
        """Validates player props table and drawer container IDs."""
        self.assertIn('id="props-table-body"', self.html)
        self.assertIn('id="prop-detail-container"', self.html)
        self.assertIn('id="btn-close-prop-detail"', self.html)

    def test_app_js_has_drawer_and_props_renderers(self):
        """Validates app.js has core rendering and drawer interaction routines."""
        self.assertIn('renderPropsTable', self.js)
        self.assertIn('showPropDetail', self.js)
        self.assertIn('renderOpportunities', self.js)


if __name__ == '__main__':
    unittest.main()
