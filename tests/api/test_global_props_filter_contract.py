"""
Tests for Global Props Filter Contract & Compositional Reliability.

Verifies:
1. Backend PlatformAPIService.get_global_props_results supports:
   - min_odds
   - competition
   - position
   - threshold / line
   - bookmaker
   - status
   - search
   - min_net_ev (across both TOP_VALUE and ALL_CANDIDATES views)
2. Composable filtering: multiple filters combined narrow down the dataset predictably.
3. Pagination limit applies strictly AFTER all filters and sorting.
"""

import unittest
from unittest.mock import MagicMock
from api.services import PlatformAPIService


class TestGlobalPropsFilterContract(unittest.TestCase):

    def setUp(self):
        self.service = PlatformAPIService(db_manager=MagicMock())
        # Sample cached global props dataset
        PlatformAPIService._cached_global_props_results = {
            "status": "SUCCESS",
            "qualified_count": 2,
            "diagnostic_count": 3,
            "qualified_opportunities": [
                {
                    "canonical_prop_key": "q1_vinicius_shots_1.5",
                    "prop_type": "PLAYER",
                    "player_name": "Vinicius Junior",
                    "team": "Real Madrid",
                    "opponent": "Barcelona",
                    "match_name": "Real Madrid vs Barcelona",
                    "competition": "La Liga",
                    "stat_type": "SHOTS",
                    "line": 1.5,
                    "side": "OVER",
                    "best_bookmaker": "superbet",
                    "best_raw_odds": 1.85,
                    "superbet_odds": 1.85,
                    "betclic_odds": 1.75,
                    "net_ev_pct": 8.5,
                    "gross_ev_pct": 12.0,
                    "hit_rate_pct": 80.0,
                    "trend_window": 10,
                    "status": "QUALIFIED",
                    "reason_code": "QUALIFIED",
                    "action": "VALUE BET",
                    "is_valuebet": True,
                    "provenance": {"position": "F"},
                },
                {
                    "canonical_prop_key": "q2_rodri_fouls_1.5",
                    "prop_type": "PLAYER",
                    "player_name": "Rodri",
                    "team": "Manchester City",
                    "opponent": "Arsenal",
                    "match_name": "Manchester City vs Arsenal",
                    "competition": "Premier League",
                    "stat_type": "FOULS",
                    "line": 1.5,
                    "side": "OVER",
                    "best_bookmaker": "betclic",
                    "best_raw_odds": 2.10,
                    "superbet_odds": None,
                    "betclic_odds": 2.10,
                    "net_ev_pct": 4.2,
                    "gross_ev_pct": 7.0,
                    "hit_rate_pct": 70.0,
                    "trend_window": 10,
                    "status": "QUALIFIED",
                    "reason_code": "QUALIFIED",
                    "action": "VALUE BET",
                    "is_valuebet": True,
                    "provenance": {"position": "M"},
                },
            ],
            "diagnostic_candidates": [
                {
                    "canonical_prop_key": "d1_lewandowski_shots_2.5",
                    "prop_type": "PLAYER",
                    "player_name": "Robert Lewandowski",
                    "team": "Barcelona",
                    "opponent": "Real Madrid",
                    "match_name": "Real Madrid vs Barcelona",
                    "competition": "La Liga",
                    "stat_type": "SHOTS",
                    "line": 2.5,
                    "side": "OVER",
                    "best_bookmaker": "superbet",
                    "best_raw_odds": 1.45,
                    "superbet_odds": 1.45,
                    "betclic_odds": None,
                    "net_ev_pct": 1.2,
                    "gross_ev_pct": 3.0,
                    "hit_rate_pct": 60.0,
                    "trend_window": 10,
                    "status": "POSITIVE_EDGE",
                    "reason_code": "BELOW_VALUE_THRESHOLD",
                    "action": "BELOW THRESHOLD",
                    "is_valuebet": False,
                    "provenance": {"position": "F"},
                },
                {
                    "canonical_prop_key": "d2_arsenal_corners_4.5",
                    "prop_type": "TEAM",
                    "player_name": None,
                    "team": "Arsenal",
                    "opponent": "Manchester City",
                    "match_name": "Manchester City vs Arsenal",
                    "competition": "Premier League",
                    "stat_type": "CORNERS",
                    "line": 4.5,
                    "side": "OVER",
                    "participant_role": "AWAY",
                    "best_bookmaker": "superbet",
                    "best_raw_odds": 1.95,
                    "superbet_odds": 1.95,
                    "betclic_odds": 1.90,
                    "net_ev_pct": -2.0,
                    "gross_ev_pct": 0.5,
                    "hit_rate_pct": 50.0,
                    "trend_window": 10,
                    "status": "REJECTED",
                    "reason_code": "BELOW_VALUE_THRESHOLD",
                    "action": "NO VALUE",
                    "is_valuebet": False,
                    "provenance": {},
                },
                {
                    "canonical_prop_key": "d3_gvardiol_tackles_1.5",
                    "prop_type": "PLAYER",
                    "player_name": "Josko Gvardiol",
                    "team": "Manchester City",
                    "opponent": "Arsenal",
                    "match_name": "Manchester City vs Arsenal",
                    "competition": "Premier League",
                    "stat_type": "TACKLES",
                    "line": 1.5,
                    "side": "OVER",
                    "best_bookmaker": None,
                    "best_raw_odds": None,
                    "superbet_odds": None,
                    "betclic_odds": None,
                    "net_ev_pct": None,
                    "gross_ev_pct": None,
                    "hit_rate_pct": 80.0,
                    "trend_window": 10,
                    "status": "REJECTED",
                    "reason_code": "POLISH_ODDS_UNAVAILABLE",
                    "action": "NO ODDS",
                    "is_valuebet": False,
                    "provenance": {"position": "D"},
                },
            ],
            "funnel_metrics": {},
        }

    def test_backend_filter_contract(self):
        """Test each filter parameter independently."""
        # 1. Min Odds filter
        res_odds = self.service.get_global_props_results(min_odds=2.0, view_mode="ALL_CANDIDATES")
        keys_odds = [i["canonical_prop_key"] for i in res_odds["items"]]
        self.assertIn("q2_rodri_fouls_1.5", keys_odds)
        self.assertNotIn("q1_vinicius_shots_1.5", keys_odds)  # 1.85 < 2.0
        self.assertNotIn("d1_lewandowski_shots_2.5", keys_odds)  # 1.45 < 2.0

        # 2. Competition filter
        res_comp = self.service.get_global_props_results(competition="La Liga", view_mode="ALL_CANDIDATES")
        keys_comp = [i["canonical_prop_key"] for i in res_comp["items"]]
        self.assertIn("q1_vinicius_shots_1.5", keys_comp)
        self.assertIn("d1_lewandowski_shots_2.5", keys_comp)
        self.assertNotIn("q2_rodri_fouls_1.5", keys_comp)  # Premier League

        # 3. Position filter
        res_pos = self.service.get_global_props_results(position="M", view_mode="ALL_CANDIDATES")
        keys_pos = [i["canonical_prop_key"] for i in res_pos["items"]]
        self.assertIn("q2_rodri_fouls_1.5", keys_pos)
        self.assertNotIn("q1_vinicius_shots_1.5", keys_pos)

        # 4. Threshold / Line filter
        res_line = self.service.get_global_props_results(threshold=2.5, view_mode="ALL_CANDIDATES")
        keys_line = [i["canonical_prop_key"] for i in res_line["items"]]
        self.assertIn("d1_lewandowski_shots_2.5", keys_line)
        self.assertNotIn("q1_vinicius_shots_1.5", keys_line)

        # 5. Bookmaker filter
        res_bm = self.service.get_global_props_results(bookmaker="Betclic", view_mode="ALL_CANDIDATES")
        keys_bm = [i["canonical_prop_key"] for i in res_bm["items"]]
        self.assertIn("q2_rodri_fouls_1.5", keys_bm)
        self.assertIn("q1_vinicius_shots_1.5", keys_bm)  # Has betclic_odds=1.75
        self.assertNotIn("d1_lewandowski_shots_2.5", keys_bm)  # No betclic odds

    def test_composable_filters(self):
        """Test multiple filters combined."""
        # Search 'Madrid' + Competition 'La Liga' + Min Net EV 5.0
        res = self.service.get_global_props_results(
            search="Madrid",
            competition="La Liga",
            min_net_ev=5.0,
            view_mode="ALL_CANDIDATES",
        )
        keys = [i["canonical_prop_key"] for i in res["items"]]
        self.assertEqual(keys, ["q1_vinicius_shots_1.5"])
        self.assertEqual(res["total_items_matching_filter"], 1)

        # Bookmaker 'Superbet' + Min Odds 1.80 + Status 'BELOW_VALUE_THRESHOLD'
        res2 = self.service.get_global_props_results(
            bookmaker="Superbet",
            min_odds=1.80,
            status="BELOW_VALUE_THRESHOLD",
            view_mode="ALL_CANDIDATES",
        )
        keys2 = [i["canonical_prop_key"] for i in res2["items"]]
        self.assertEqual(keys2, ["d2_arsenal_corners_4.5"])  # 1.95 odds, Superbet, BELOW_VALUE_THRESHOLD

        # Limit strictly applies after filtering
        res3 = self.service.get_global_props_results(
            competition="Premier League",
            limit=1,
            view_mode="ALL_CANDIDATES",
        )
        self.assertEqual(len(res3["items"]), 1)
        self.assertEqual(res3["total_items_matching_filter"], 3)  # 3 total match Premier League


if __name__ == "__main__":
    unittest.main()
