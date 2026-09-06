"""
Targeted tests for Global Props Scanner ULTRA SCAN mode, NORMAL SCAN preservation,
exhaustive pagination, cache mode isolation, and truthful telemetry.
"""

import unittest
from unittest.mock import MagicMock, patch
from typing import Dict, Any, List

from scanner.global_props_scanner import (
    GlobalPropsScanner,
    GlobalScanScope,
    GlobalScanBudget,
    GlobalScanFunnelMetrics,
    GlobalScanResult,
)
from providers.statshub.config import StatsHubConfig
from providers.statshub.provider import StatsHubProvider
from providers.statshub.team_provider import StatsHubTeamPropsProvider
from api.services import PlatformAPIService
from api.routes import APIRouter


class TestGlobalPropsUltraScan(unittest.TestCase):

    def setUp(self):
        # Reset cached states before each test
        PlatformAPIService._cached_global_props_results = None
        PlatformAPIService._cached_global_props_ultra_results = None

    def test_normal_scan_profile_defaults_and_budget(self):
        """Verify NORMAL scan budget preserves exact baseline limits."""
        normal_b = GlobalScanBudget.normal()
        self.assertEqual(normal_b.scan_mode, "NORMAL")
        self.assertEqual(normal_b.max_fixtures, 30)
        self.assertEqual(normal_b.max_trends_requests, 20)
        self.assertEqual(normal_b.max_execution_events, 20)
        self.assertFalse(normal_b.auto_paginate_statshub)
        self.assertEqual(normal_b.max_prop_results_per_stat, 500)

        scope = GlobalScanScope()
        self.assertEqual(scope.scan_mode, "NORMAL")
        self.assertEqual(scope.max_results, 50)

    def test_ultra_scan_profile_defaults_and_budget(self):
        """Verify ULTRA scan budget expands search space ceilings."""
        ultra_b = GlobalScanBudget.ultra()
        self.assertEqual(ultra_b.scan_mode, "ULTRA")
        self.assertEqual(ultra_b.max_fixtures, 60)
        self.assertEqual(ultra_b.max_trends_requests, 60)
        self.assertEqual(ultra_b.max_execution_events, 60)
        self.assertTrue(ultra_b.auto_paginate_statshub)
        self.assertEqual(ultra_b.max_prop_results_per_stat, 2000)

        scope = GlobalScanScope(scan_mode="ULTRA")
        self.assertEqual(scope.scan_mode, "ULTRA")

    @patch("providers.statshub.client.StatsHubClient.fetch_player_trends")
    def test_statshub_player_trends_multi_page_pagination(self, mock_fetch):
        """Verify StatsHubProvider exhaustively paginates player trends when auto_paginate=True."""
        page1 = {
            "data": [{"playerName": "Player 1", "stat": "shots", "line": 0.5}],
            "pagination": {"page": 1, "totalPages": 3, "total": 3},
        }
        page2 = {
            "data": [{"playerName": "Player 2", "stat": "shots", "line": 1.5}],
            "pagination": {"page": 2, "totalPages": 3, "total": 3},
        }
        page3 = {
            "data": [{"playerName": "Player 3", "stat": "shots", "line": 2.5}],
            "pagination": {"page": 3, "totalPages": 3, "total": 3},
        }
        mock_fetch.side_effect = [page1, page2, page3]

        cfg = StatsHubConfig(mode="player_trends", games="12345", stat="shots", auto_paginate=True, max_pages=10)
        provider = StatsHubProvider(config=cfg)
        payloads = provider.fetch()

        self.assertEqual(len(payloads), 3)
        self.assertEqual(provider.acquisition_metrics["pages_fetched"], 3)
        self.assertEqual(provider.acquisition_metrics["source_total"], 3)
        self.assertFalse(provider.acquisition_metrics["truncated"])
        self.assertEqual(mock_fetch.call_count, 3)

    @patch("providers.statshub.client.StatsHubClient.fetch_player_trends")
    def test_statshub_player_trends_single_page_when_auto_paginate_false(self, mock_fetch):
        """Verify StatsHubProvider preserves single-page fetch when auto_paginate=False (NORMAL scan)."""
        page1 = {
            "data": [{"playerName": "Player 1", "stat": "shots", "line": 0.5}],
            "pagination": {"page": 1, "totalPages": 5, "total": 5},
        }
        mock_fetch.return_value = page1

        cfg = StatsHubConfig(mode="player_trends", games="12345", stat="shots", auto_paginate=False)
        provider = StatsHubProvider(config=cfg)
        payloads = provider.fetch()

        self.assertEqual(len(payloads), 1)
        self.assertEqual(mock_fetch.call_count, 1)

    @patch("providers.statshub.client.StatsHubClient.fetch_team_trends")
    def test_statshub_team_trends_multi_page_pagination(self, mock_fetch):
        """Verify StatsHubTeamPropsProvider exhaustively paginates team trends when auto_paginate=True."""
        page1 = {
            "data": [{"teamName": "Arsenal", "stat": "shots", "line": 4.5}],
            "pagination": {"page": 1, "totalPages": 2, "total": 2},
        }
        page2 = {
            "data": [{"teamName": "Chelsea", "stat": "shots", "line": 3.5}],
            "pagination": {"page": 2, "totalPages": 2, "total": 2},
        }
        mock_fetch.side_effect = [page1, page2]

        cfg = StatsHubConfig(mode="team_trends", games="12345", stat="shots", auto_paginate=True, max_pages=10)
        provider = StatsHubTeamPropsProvider(config=cfg)
        payloads = provider.fetch()

        self.assertEqual(len(payloads), 2)
        self.assertEqual(provider.acquisition_metrics["pages_fetched"], 2)
        self.assertEqual(mock_fetch.call_count, 2)

    def test_cache_mode_isolation_normal_vs_ultra(self):
        """Verify Normal and Ultra scan results are cached in strictly separate stores."""
        service = PlatformAPIService(db_manager=None)

        normal_data = {
            "status": "SUCCESS",
            "scan_mode": "NORMAL",
            "qualified_opportunities": [{"prop_id": "p_norm_1", "net_ev_pct": 5.0}],
            "diagnostic_candidates": [],
        }
        ultra_data = {
            "status": "SUCCESS",
            "scan_mode": "ULTRA",
            "qualified_opportunities": [
                {"prop_id": "p_ultra_1", "net_ev_pct": 7.0},
                {"prop_id": "p_ultra_2", "net_ev_pct": 4.5},
            ],
            "diagnostic_candidates": [],
        }

        PlatformAPIService._cached_global_props_results = normal_data
        PlatformAPIService._cached_global_props_ultra_results = ultra_data

        # Query Normal
        res_normal = service.get_global_props_results(scan_mode="NORMAL")
        self.assertEqual(res_normal["scan_mode"], "NORMAL")
        self.assertEqual(len(res_normal["qualified_opportunities"]), 1)
        self.assertEqual(res_normal["qualified_opportunities"][0]["prop_id"], "p_norm_1")

        # Query Ultra
        res_ultra = service.get_global_props_results(scan_mode="ULTRA")
        self.assertEqual(res_ultra["scan_mode"], "ULTRA")
        self.assertEqual(len(res_ultra["qualified_opportunities"]), 2)
        self.assertEqual(res_ultra["qualified_opportunities"][0]["prop_id"], "p_ultra_1")

        # Default query without scan_mode must resolve to NORMAL
        res_default = service.get_global_props_results()
        self.assertEqual(res_default["scan_mode"], "NORMAL")
        self.assertEqual(len(res_default["qualified_opportunities"]), 1)

    def test_api_router_forwards_scan_mode(self):
        """Verify APIRouter properly forwards scan_mode to service."""
        service = PlatformAPIService(db_manager=None)
        router = APIRouter(service=service)

        PlatformAPIService._cached_global_props_ultra_results = {
            "status": "SUCCESS",
            "scan_mode": "ULTRA",
            "qualified_opportunities": [{"prop_id": "u1", "net_ev_pct": 6.0}],
            "diagnostic_candidates": [],
        }

        api_res = router.handle_get_global_props_results(scan_mode="ULTRA")
        self.assertEqual(api_res.status_code, 200)
        self.assertEqual(api_res.data["scan_mode"], "ULTRA")
        self.assertEqual(len(api_res.data["qualified_opportunities"]), 1)

    def test_max_results_slicing_occurs_after_filtering_and_sorting(self):
        """Verify max_results does not truncate the evaluated candidate pool before category filtering."""
        service = PlatformAPIService(db_manager=None)

        # 60 items: 40 player props and 20 team props
        sample_items = []
        for i in range(40):
            sample_items.append({
                "prop_id": f"player_{i}",
                "prop_type": "PLAYER",
                "net_ev_pct": float(10.0 - i * 0.1),
                "status": "QUALIFIED",
                "is_valuebet": True,
            })
        for i in range(20):
            sample_items.append({
                "prop_id": f"team_{i}",
                "prop_type": "TEAM",
                "net_ev_pct": float(15.0 - i * 0.1),
                "status": "QUALIFIED",
                "is_valuebet": True,
            })

        PlatformAPIService._cached_global_props_results = {
            "status": "SUCCESS",
            "scan_mode": "NORMAL",
            "qualified_opportunities": sample_items,
            "diagnostic_candidates": [],
        }

        # Request player props with limit=15
        res = service.get_global_props_results(props_scope="PLAYER", limit=15, offset=0)
        self.assertEqual(res["total_qualified_matching_filter"], 40)
        self.assertEqual(res["total_items_matching_filter"], 40)
        self.assertEqual(len(res["items"]), 15)
        self.assertTrue(all(it["prop_type"] == "PLAYER" for it in res["items"]))

        # Request team props with limit=10
        res_team = service.get_global_props_results(props_scope="TEAM", limit=10, offset=0)
        self.assertEqual(res_team["total_qualified_matching_filter"], 20)
        self.assertEqual(res_team["total_items_matching_filter"], 20)
        self.assertEqual(len(res_team["items"]), 10)
        self.assertTrue(all(it["prop_type"] == "TEAM" for it in res_team["items"]))

    def test_ultra_funnel_metrics_telemetry_consistency(self):
        """Verify funnel metrics telemetry serialization and consistency."""
        funnel = GlobalScanFunnelMetrics(
            scan_mode="ULTRA",
            fixtures_discovered=50,
            fixtures_selected=40,
            trends_discovered=1200,
            trends_scoped_to_selected=950,
            trends_deduplicated=420,
            matched_props=210,
            evaluated_count=420,
            qualified_count=35,
            rejected_count=385,
            pages_requested=25,
            pages_successful=24,
            pages_failed=1,
            player_props_evaluated=300,
            team_props_evaluated=120,
            matched_both_bookmakers=85,
            quote_discrepancies=28,
            valuebets_qualified=35,
        )

        d = funnel.to_dict()
        self.assertEqual(d["scan_mode"], "ULTRA")
        self.assertEqual(d["fixtures_discovered"], 50)
        self.assertEqual(d["trends_discovered"], 1200)
        self.assertEqual(d["trends_deduplicated"], 420)
        self.assertEqual(d["pages_requested"], 25)
        self.assertEqual(d["pages_failed"], 1)
        self.assertEqual(d["matched_both_bookmakers"], 85)
        self.assertEqual(d["quote_discrepancies"], 28)
        self.assertEqual(d["valuebets_qualified"], 35)


if __name__ == "__main__":
    unittest.main()
