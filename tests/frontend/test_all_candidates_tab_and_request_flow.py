import unittest
from unittest.mock import MagicMock
import json
import re

from api.services import PlatformAPIService


class TestAllCandidatesTabAndRequestFlow(unittest.TestCase):
    """Verifies that ALL_CANDIDATES tab requests view_mode=ALL_CANDIDATES and renders all candidates."""

    def setUp(self):
        with open("web/app.js", "r", encoding="utf-8") as f:
            self.app_js = f.read()

    def test_1_frontend_builds_request_with_view_mode_all_candidates_and_min_net_ev(self):
        """TEST 1: When ALL_CANDIDATES / diagnostics is active, frontend builds params with view_mode=ALL_CANDIDATES and min_net_ev."""
        # 1. Verify app.js maps diagnostics tab to ALL_CANDIDATES view_mode
        self.assertIn("viewMode = isTopValueTab ? 'TOP_VALUE' : 'ALL_CANDIDATES';", self.app_js)
        self.assertIn("params.min_net_ev = minEvVal", self.app_js)
        self.assertIn("data-category=\"diagnostics\"", open("web/index.html", "r", encoding="utf-8").read())

    def test_2_all_three_diagnostic_categories_preserved_and_returned_for_all_candidates(self):
        """TEST 2: Response with +2.93% EV, -3.2% EV, and null EV are all preserved in ALL_CANDIDATES view."""
        service = PlatformAPIService(db_manager=MagicMock())
        PlatformAPIService._cached_global_props_results = {
            "status": "SUCCESS",
            "qualified_count": 0,
            "diagnostic_count": 3,
            "qualified_opportunities": [],
            "diagnostic_candidates": [
                {
                    "canonical_prop_key": "cand_a",
                    "player_name": "Ollie Tanner",
                    "net_ev_pct": 2.93,
                    "status": "EVALUATED",
                    "reason_code": "BELOW_VALUE_THRESHOLD",
                },
                {
                    "canonical_prop_key": "cand_b",
                    "player_name": "Riley McGree",
                    "net_ev_pct": -3.20,
                    "status": "EVALUATED",
                    "reason_code": "BELOW_VALUE_THRESHOLD",
                },
                {
                    "canonical_prop_key": "cand_c",
                    "player_name": "Adama Sidibeh",
                    "net_ev_pct": None,
                    "status": "REJECTED",
                    "reason_code": "INSUFFICIENT_REFERENCE_SOURCES",
                },
            ],
            "funnel_metrics": {},
        }

        # Query with view_mode=ALL_CANDIDATES and min_net_ev=3.0
        res = service.get_global_props_results(
            view_mode="ALL_CANDIDATES",
            min_net_ev=3.0,
        )
        items = res.get("items", [])
        self.assertEqual(len(items), 3, "All 3 candidates must be returned in ALL_CANDIDATES view")
        keys = [i["canonical_prop_key"] for i in items]
        self.assertIn("cand_a", keys)
        self.assertIn("cand_b", keys)
        self.assertIn("cand_c", keys)


if __name__ == "__main__":
    unittest.main()
