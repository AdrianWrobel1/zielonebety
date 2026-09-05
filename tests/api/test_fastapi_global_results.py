"""
FastAPI / Router Integration Tests for GET /api/v1/props/global-results

Reproduces and validates:
1. Exact frontend request GET /api/v1/props/global-results?limit=50&offset=0&min_net_ev=3 does not return 404.
2. Empty cache returns status='NOT_RUN' without triggering a background scan.
3. Populated cache returns cached opportunities with limit, offset, min_net_ev, stat, and search filtering.
"""

import unittest
from unittest.mock import patch

from api.routes import APIRouter
from api.services import PlatformAPIService
from database.config import DatabaseConfig
from database.connection import DatabaseManager


class TestFastAPIGlobalResultsEndpoint(unittest.TestCase):

    def setUp(self):
        PlatformAPIService._cached_global_props_results = None
        self.db_manager = DatabaseManager(DatabaseConfig.default_sqlite_in_memory())
        self.service = PlatformAPIService(db_manager=self.db_manager)
        if hasattr(self.service, "scheduler") and self.service.scheduler is not None:
            self.service.scheduler.stop()
        self.router = APIRouter(service=self.service)

    def tearDown(self):
        PlatformAPIService._cached_global_props_results = None
        if hasattr(self.service, "scheduler") and self.service.scheduler is not None:
            self.service.scheduler.stop()
        if hasattr(self.db_manager, "dispose"):
            self.db_manager.dispose()

    def test_routing_precedence_and_registration(self):
        """Validates that global-results does not trigger prop_id 404 handler."""
        detail_res = self.router.handle_get_prop_detail("global-results")
        self.assertEqual(detail_res.status_code, 404)

        global_res = self.router.handle_get_global_props_results()
        self.assertEqual(global_res.status_code, 200)

    def test_frontend_exact_get_request_empty_cache_contract(self):
        """Reproduces exact frontend request: GET /api/v1/props/global-results?limit=50&offset=0&min_net_ev=3."""
        with patch.object(self.service, "scan_global_props") as mock_scan:
            res = self.router.handle_get_global_props_results(
                limit=50,
                offset=0,
                min_net_ev=3.0,
            )
            # Empty cache GET must not trigger a scan
            mock_scan.assert_not_called()

            self.assertEqual(res.status_code, 200)
            data = res.data
            self.assertEqual(data.get("status"), "NOT_RUN")
            self.assertEqual(data.get("qualified_count"), 0)
            self.assertEqual(data.get("qualified_opportunities"), [])
            self.assertEqual(data.get("limit"), 50)
            self.assertEqual(data.get("offset"), 0)

    def test_frontend_get_with_populated_cache_and_filtering(self):
        """Validates that GET /api/v1/props/global-results filters cached results correctly."""
        PlatformAPIService._cached_global_props_results = {
            "status": "SUCCESS",
            "scope": {"props_scope": "ALL"},
            "budget": {},
            "funnel_metrics": {
                "fixtures_discovered": 5,
                "fixtures_selected": 3,
                "trends_discovered": 20,
                "trends_deduplicated": 15,
                "matched_props": 5,
                "match_uncertain": 0,
                "evaluated_count": 15,
                "qualified_count": 3,
                "rejected_count": 12,
                "rejection_breakdown": {},
                "execution_time_ms": 100.0,
            },
            "qualified_opportunities": [
                {
                    "canonical_prop_key": "prop:vinicius:shots_on_target:1.5:OVER",
                    "prop_type": "PLAYER",
                    "player_name": "Vinicius Junior",
                    "team": "Real Madrid",
                    "opponent": "Barcelona",
                    "match_name": "Real Madrid vs Barcelona",
                    "fixture_id": "fix-100",
                    "stat_type": "SHOTS_ON_TARGET",
                    "line": 1.5,
                    "side": "OVER",
                    "net_ev_pct": 8.5,
                    "best_bookmaker": "Superbet",
                    "best_raw_odds": 2.40,
                    "status": "QUALIFIED",
                },
                {
                    "canonical_prop_key": "prop:mbappe:shots:2.5:OVER",
                    "prop_type": "PLAYER",
                    "player_name": "Kylian Mbappe",
                    "team": "Real Madrid",
                    "opponent": "Barcelona",
                    "match_name": "Real Madrid vs Barcelona",
                    "fixture_id": "fix-100",
                    "stat_type": "SHOTS",
                    "line": 2.5,
                    "side": "OVER",
                    "net_ev_pct": 2.1,
                    "best_bookmaker": "Superbet",
                    "best_raw_odds": 1.95,
                    "status": "QUALIFIED",
                },
                {
                    "canonical_prop_key": "team_prop:liverpool:corners:5.5:OVER:HOME",
                    "prop_type": "TEAM",
                    "player_name": None,
                    "team": "Liverpool",
                    "opponent": "Manchester City",
                    "match_name": "Liverpool vs Manchester City",
                    "fixture_id": "fix-300",
                    "stat_type": "CORNERS",
                    "line": 5.5,
                    "side": "OVER",
                    "net_ev_pct": 12.0,
                    "best_bookmaker": "Betclic",
                    "best_raw_odds": 2.15,
                    "status": "QUALIFIED",
                },
            ],
            "diagnostic_candidates": [],
            "duration_ms": 100.0,
            "scanned_at": "2026-08-31T20:00:00Z",
        }

        # 1. Fetch with min_net_ev=3.0 (Mbappe at 2.1 should be filtered out)
        res = self.router.handle_get_global_props_results(
            limit=50,
            offset=0,
            min_net_ev=3.0,
        )
        self.assertEqual(res.status_code, 200)
        data = res.data
        self.assertEqual(data["status"], "SUCCESS")
        self.assertEqual(len(data["qualified_opportunities"]), 2)
        self.assertEqual(data["total_qualified_matching_filter"], 2)

        # 2. Filter by search="Vinicius"
        res = self.router.handle_get_global_props_results(
            search="Vinicius",
        )
        self.assertEqual(res.status_code, 200)
        data = res.data
        self.assertEqual(len(data["qualified_opportunities"]), 1)
        self.assertEqual(data["qualified_opportunities"][0]["player_name"], "Vinicius Junior")

        # 3. Filter by props_scope="TEAM"
        res = self.router.handle_get_global_props_results(
            props_scope="TEAM",
        )
        self.assertEqual(res.status_code, 200)
        data = res.data
        self.assertEqual(len(data["qualified_opportunities"]), 1)
        self.assertEqual(data["qualified_opportunities"][0]["team"], "Liverpool")

    def test_taxonomy_and_coverage_routes(self):
        """Validates that router exposes taxonomy and coverage endpoints."""
        tax_res = self.router.handle_get_props_taxonomy()
        self.assertEqual(tax_res.status_code, 200)
        self.assertIn("groups", tax_res.data)
        self.assertEqual(len(tax_res.data["groups"]), 2)
        group_scopes = [g["scope"] for g in tax_res.data["groups"]]
        self.assertIn("PLAYER", group_scopes)
        self.assertIn("TEAM", group_scopes)

        cov_res = self.router.handle_get_props_coverage()
        self.assertEqual(cov_res.status_code, 200)
        self.assertEqual(cov_res.data["total_categories"], 15)
        self.assertEqual(cov_res.data["player_categories_count"], 8)
        self.assertEqual(cov_res.data["team_categories_count"], 7)


if __name__ == "__main__":
    unittest.main()
