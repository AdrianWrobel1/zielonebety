import unittest
from unittest.mock import MagicMock
from api.services import PlatformAPIService


class TestAllCandidatesMinEvBugfix(unittest.TestCase):
    def setUp(self):
        self.service = PlatformAPIService(db_manager=MagicMock())
        # Populate cached results with realistic candidate data
        PlatformAPIService._cached_global_props_results = {
            "status": "SUCCESS",
            "qualified_count": 1,
            "diagnostic_count": 1,
            "qualified_opportunities": [
                {
                    "canonical_prop_key": "fix1_shots_0.5",
                    "player_name": "Ollie Tanner",
                    "status": "QUALIFIED",
                    "reason_code": "QUALIFIED",
                    "action": "VALUE BET",
                    "net_ev_pct": 2.9,
                    "is_valuebet": True,
                }
            ],
            "diagnostic_candidates": [
                {
                    "canonical_prop_key": "fix2_fouls_0.5",
                    "player_name": "Takefusa Kubo",
                    "status": "REJECTED",
                    "reason_code": "INSUFFICIENT_REFERENCE_SOURCES",
                    "action": "NO VALUE",
                    "net_ev_pct": None,
                    "is_valuebet": False,
                }
            ],
            "funnel_metrics": {},
        }

    def test_1_reproduce_bug_all_candidates_with_net_ev_below_min_ev(self):
        """TEST 1: In ALL_CANDIDATES view with min_ev=3.0, a candidate with net_ev=2.9 must remain."""
        res = self.service.get_global_props_results(
            view_mode="ALL_CANDIDATES",
            min_net_ev=3.0,
        )
        items = res.get("items", [])
        keys = [i.get("canonical_prop_key") for i in items]
        self.assertIn("fix1_shots_0.5", keys, "Candidate with net_ev=2.9 was improperly filtered out in ALL_CANDIDATES view")

    def test_2_diagnostic_with_none_ev_in_all_candidates(self):
        """TEST 2: In ALL_CANDIDATES view with min_ev=3.0, a diagnostic candidate with net_ev=None must remain."""
        res = self.service.get_global_props_results(
            view_mode="ALL_CANDIDATES",
            min_net_ev=3.0,
        )
        items = res.get("items", [])
        keys = [i.get("canonical_prop_key") for i in items]
        self.assertIn("fix2_fouls_0.5", keys, "Candidate with net_ev=None was improperly filtered out in ALL_CANDIDATES view")

    def test_3_top_value_respects_min_ev(self):
        """TEST 3: In TOP_VALUE view with min_ev=3.0, a candidate with net_ev=2.9 must be excluded."""
        res = self.service.get_global_props_results(
            view_mode="TOP_VALUE",
            min_net_ev=3.0,
        )
        items = res.get("items", [])
        keys = [i.get("canonical_prop_key") for i in items]
        self.assertNotIn("fix1_shots_0.5", keys, "Candidate with net_ev=2.9 should not be returned in TOP_VALUE view when min_ev=3.0")


if __name__ == "__main__":
    unittest.main()
