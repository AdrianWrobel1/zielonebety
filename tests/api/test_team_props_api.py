"""
API & Opportunity Explorer Tests for Team Props (Stage 30).
"""

import unittest
from api.routes import APIRouter
from api.services import PlatformAPIService
from database.connection import DatabaseManager
from database.config import DatabaseConfig
from core.opportunity_explorer import OpportunityExplorerAdapter, OpportunityType


class TestTeamPropsAPI(unittest.TestCase):

    def setUp(self):
        db_mgr = DatabaseManager(DatabaseConfig.default_sqlite_in_memory())
        db_mgr.create_tables()

        self.service = PlatformAPIService(db_manager=db_mgr)
        self.router = APIRouter(service=self.service)

        PlatformAPIService._cached_team_props_results = []
        PlatformAPIService._cached_team_props_by_stat = {}
        PlatformAPIService._last_team_props_scan_metadata = {}

    def test_team_props_health_endpoint(self):
        res = self.router.handle_get_team_props_health()
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["provider"], "statshub_team")
        self.assertIn("total_props_cached", res.data)

    def test_team_props_results_and_explorer_integration(self):
        fake_team_prop = {
            "prop_id": "ctp_arsenal_corners_4_5",
            "canonical_prop_key": "team_prop:arsenal:chelsea:HOME:CORNERS:OVER:FULL_TIME:4.5",
            "team": "Arsenal",
            "team_name": "Arsenal",
            "opponent": "Chelsea",
            "opponent_name": "Chelsea",
            "participant_role": "HOME",
            "match_name": "Arsenal vs Chelsea",
            "fixture": "Arsenal vs Chelsea",
            "competition": "Premier League",
            "kickoff": "2026-08-30T15:00:00Z",
            "stat": "corners",
            "stat_type": "CORNERS",
            "market": "Arsenal (HOME) Over 4.5 Corners",
            "line": 4.5,
            "side": "OVER",
            "best_odds": 1.55,
            "best_bookmaker": "Bet365",
            "best_execution_odds": 1.70,
            "best_execution_bookmaker": "Superbet",
            "execution_status": "BETTABLE",
            "hit_rate_pct": 80.0,
            "hit_rate_display": "8/10",
            "sample_size": 10,
            "average": 5.8,
            "historical_probability": 0.80,
            "model_probability": 0.80,
            "model_probability_pct": 80.0,
            "fair_odds": 1.25,
            "execution_market_probability": 0.5882,
            "execution_edge_pct": 21.2,
            "value_edge_pp": 21.18,
            "execution_ev_pct": 36.0,
            "is_valuebet": True,
            "score": 88.5,
            "classification": "OPPORTUNITY",
            "actionability": "BETTABLE",
            "status": "VALUEBET",
            "data_quality_flags": ["VALUEBET_POSITIVE_EV"],
            "recent_matches": [],
        }

        PlatformAPIService._cached_team_props_results = [fake_team_prop]
        PlatformAPIService._cached_team_props_by_stat["corners"] = [fake_team_prop]

        # 1. Test GET /api/v1/team-props
        res = self.router.handle_get_team_props_results(stat="corners")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(res.data["results"]), 1)
        self.assertEqual(res.data["results"][0]["team"], "Arsenal")
        self.assertTrue(res.data["results"][0]["is_valuebet"])

        # 2. Test GET /api/v1/team-props/{prop_id}
        res_det = self.router.handle_get_team_prop_detail("ctp_arsenal_corners_4_5")
        self.assertEqual(res_det.status_code, 200)
        self.assertEqual(res_det.data["team"], "Arsenal")

        # 3. Test Adapter directly
        dto = OpportunityExplorerAdapter.from_team_prop(fake_team_prop)
        self.assertEqual(dto.type, OpportunityType.TEAM_PROP.value)
        self.assertEqual(dto.team, "Arsenal")
        self.assertTrue(dto.is_valuebet)
        self.assertEqual(dto.status, "VALUEBET")
        self.assertEqual(dto.fair_odds, 1.25)
        self.assertEqual(dto.execution_odds, 1.70)

        # 4. Test Opportunity Explorer endpoint with type=TEAM_PROP
        exp_res = self.service.get_unified_explorer_opportunities(opp_type="TEAM_PROP")
        self.assertGreaterEqual(exp_res["total"], 1)
        found = any(item["team"] == "Arsenal" for item in exp_res["items"])
        self.assertTrue(found)

        # 5. Test Opportunity Explorer endpoint with type=ALL
        all_res = self.service.get_unified_explorer_opportunities(opp_type="ALL")
        found_all = any(item["team"] == "Arsenal" and item["type"] == "TEAM_PROP" for item in all_res["items"])
        self.assertTrue(found_all)