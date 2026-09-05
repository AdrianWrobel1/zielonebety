"""
Unit Tests for Player Props API Router & Service Handlers (Stage 15)
"""

import unittest
from unittest.mock import patch, MagicMock

from api.routes import APIRouter
from api.services import PlatformAPIService
from database.connection import DatabaseManager
from database.config import DatabaseConfig
from providers.base.provider_registry import ProviderRegistry
from providers.statshub.provider import StatsHubProvider


class TestPlayerPropsAPI(unittest.TestCase):

    def setUp(self):
        ProviderRegistry.clear()
        ProviderRegistry.register("statshub", StatsHubProvider)

        db_mgr = DatabaseManager(DatabaseConfig.default_sqlite_in_memory())
        db_mgr.create_tables()

        self.service = PlatformAPIService(db_manager=db_mgr)
        self.router = APIRouter(service=self.service)

        # Clear cache before each test (both the cross-stat union and the
        # per-stat partitions — partitions otherwise leak across tests now
        # that the global cache is their union, not the last scan alone).
        PlatformAPIService._cached_props_results = []
        PlatformAPIService._cached_props_by_stat = {}
        PlatformAPIService._last_props_scan_metadata = {}
        PlatformAPIService._last_props_scan_metadata_by_stat = {}

        self._patch_sb = patch("providers.superbet.provider.SuperbetProvider.discover", return_value=[])
        self._patch_bc = patch("providers.betclic.provider.BetclicProvider.discover", return_value=[])
        self._patch_sb.start()
        self._patch_bc.start()

    def tearDown(self):
        self._patch_sb.stop()
        self._patch_bc.stop()
        ProviderRegistry.clear()

    def test_get_props_health(self):
        res = self.router.handle_get_props_health()
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["provider"], "statshub")
        self.assertIn(res.data["status"], ["HEALTHY", "DISABLED"])

    @patch("providers.statshub.client.StatsHubClient.fetch_props")
    def test_scan_props_endpoint_success(self, mock_fetch):
        mock_fetch.return_value = {
            "players": [
                {
                    "playerName": "Vinicius Junior",
                    "teamName": "Real Madrid",
                    "opponentName": "Getafe",
                    "fixtureId": "fix-rma-get",
                    "stat": "shots",
                    "position": "F",
                    "average": 3.5,
                    "hitRateCount": 9,
                    "sampleSize": 10,
                    "hitRatePct": 90.0,
                    "bookmakerOdds": [
                        {"bookmaker": "Bet365", "line": 0.5, "side": "over", "decimalOdds": 1.65},
                        {"bookmaker": "Paddy Power", "line": 0.5, "side": "over", "decimalOdds": 1.70},
                    ],
                }
            ],
            "pagination": {"page": 1, "limit": 50, "total": 1, "totalPages": 1},
        }

        res = self.router.handle_post_scan_props({"stat": "shots", "min_odds": 1.5})
        self.assertEqual(res.status_code, 200)
        self.assertIn("items", res.data)
        self.assertEqual(len(res.data["items"]), 1)

        item = res.data["items"][0]
        self.assertEqual(item["player_name"], "Vinicius Junior")
        self.assertEqual(item["best_odds"], 1.70)
        self.assertEqual(item["best_bookmaker"], "Paddy Power")
        self.assertEqual(item["hit_rate_display"], "9/10")
        self.assertIn("score", item)
        self.assertIn("raw_edge", item)
        self.assertIn("odds_comparison", item)

    @patch("providers.statshub.client.StatsHubClient.fetch_props")
    def test_multi_page_scan_and_backend_search_beyond_50_props(self, mock_fetch):
        # Generate 120 mock players across 3 pages (50 on page 1, 50 on page 2, 20 on page 3)
        def side_effect_fetch(cfg):
            page = cfg.page
            if page == 1:
                return {
                    "players": [
                        {
                            "playerName": f"Page1_Player_{i}",
                            "teamName": f"Team_P1_{i}",
                            "opponentName": "Opponent",
                            "fixtureId": f"fix-p1-{i}",
                            "stat": "shots",
                            "hitRatePct": 70.0,
                            "bookmakerOdds": [{"bookmaker": "Bet365", "line": 0.5, "side": "over", "decimalOdds": 1.80}],
                        }
                        for i in range(1, 51)
                    ],
                    "pagination": {"page": 1, "limit": 50, "total": 120, "totalPages": 3},
                }
            elif page == 2:
                players_p2 = [
                    {
                        "playerName": f"Page2_Player_{i}",
                        "teamName": f"Team_P2_{i}",
                        "opponentName": "Opponent",
                        "fixtureId": f"fix-p2-{i}",
                        "stat": "shots",
                        "hitRatePct": 85.0,
                        "sampleSize": 10,
                        "average": 2.5,
                        "bookmakerOdds": [{"bookmaker": "Bet365", "line": 0.5, "side": "over", "decimalOdds": 2.10}],
                    }
                    for i in range(1, 51)
                ]
                # Insert Mbappe on page 2 (item index 75)
                players_p2[24]["playerName"] = "Kylian Mbappe"
                players_p2[24]["teamName"] = "Real Madrid"
                players_p2[24]["sampleSize"] = 10
                players_p2[24]["average"] = 3.0
                return {
                    "players": players_p2,
                    "pagination": {"page": 2, "limit": 50, "total": 120, "totalPages": 3},
                }
            elif page == 3:
                return {
                    "players": [
                        {
                            "playerName": f"Page3_Player_{i}",
                            "teamName": f"Team_P3_{i}",
                            "opponentName": "Opponent",
                            "fixtureId": f"fix-p3-{i}",
                            "stat": "shots",
                            "hitRatePct": 60.0,
                        }
                        for i in range(1, 21)
                    ],
                    "pagination": {"page": 3, "limit": 50, "total": 120, "totalPages": 3},
                }
            return {}

        mock_fetch.side_effect = side_effect_fetch

        # 1. Execute full multi-page scan
        scan_res = self.router.handle_post_scan_props({"auto_paginate": True})
        self.assertEqual(scan_res.status_code, 200)
        self.assertEqual(len(scan_res.data["items"]), 120)

        meta = scan_res.data["metadata"]
        self.assertEqual(meta["source_total"], 120)
        self.assertEqual(meta["pages_fetched"], 3)
        self.assertEqual(meta["final_count"], 120)
        self.assertEqual(meta["props_with_odds"], 100)  # 50 on p1, 50 on p2

        # 2. Search for Kylian Mbappe (located on page 2, outside first 50 records)
        search_res = self.router.handle_get_props_results(search="Mbappe")
        self.assertEqual(search_res.status_code, 200)
        self.assertEqual(search_res.data["total"], 1)
        self.assertEqual(search_res.data["items"][0]["player_name"], "Kylian Mbappe")
        self.assertEqual(search_res.data["items"][0]["team"], "Real Madrid")
        self.assertEqual(search_res.data["items"][0]["best_odds"], 2.10)
        self.assertEqual(search_res.data["items"][0]["classification"], "OPPORTUNITY")

        # 3. Test category filtering (opportunities vs all)
        opps_res = self.router.handle_get_props_results(category="opportunities")
        self.assertEqual(opps_res.status_code, 200)
        self.assertGreater(opps_res.data["total"], 0)
        for it in opps_res.data["items"]:
            self.assertEqual(it["classification"], "OPPORTUNITY")

        # 4. Test category counts dictionary
        counts = search_res.data["counts"]
        self.assertEqual(counts["total_scanned"], 120)
        self.assertEqual(counts["total_with_odds"], 100)

    @patch("providers.statshub.client.StatsHubClient.fetch_props")
    def test_filter_consistency_and_position_handling(self, mock_fetch):
        mock_fetch.return_value = {
            "players": [
                {
                    "playerName": "Forward Player",
                    "teamName": "Team F",
                    "position": "F",
                    "stat": "shots",
                    "hitRatePct": 80.0,
                    "bookmakerOdds": [{"bookmaker": "Bet365", "line": 0.5, "side": "over", "decimalOdds": 1.90}],
                },
                {
                    "playerName": "Midfielder Player",
                    "teamName": "Team M",
                    "position": "M",
                    "stat": "shots",
                    "hitRatePct": 70.0,
                    "bookmakerOdds": [{"bookmaker": "Bet365", "line": 1.5, "side": "over", "decimalOdds": 2.50}],
                },
                {
                    "playerName": "Defender Player",
                    "teamName": "Team D",
                    "position": "D",
                    "stat": "shots",
                    "hitRatePct": 50.0,
                    "bookmakerOdds": [{"bookmaker": "Bet365", "line": 0.5, "side": "over", "decimalOdds": 3.00}],
                },
            ],
            "pagination": {"page": 1, "limit": 50, "total": 3, "totalPages": 1},
        }

        # Run scan
        self.router.handle_post_scan_props({})

        # 1. Test position "D,M,F" matches all 3
        res_all_pos = self.router.handle_get_props_results(position="D,M,F")
        self.assertEqual(res_all_pos.data["total"], 3)
        self.assertEqual(res_all_pos.data["counts"]["total_scanned"], 3)

        # 2. Test position "F" matches 1
        res_f = self.router.handle_get_props_results(position="F")
        self.assertEqual(res_f.data["total"], 1)
        self.assertEqual(res_f.data["items"][0]["player_name"], "Forward Player")
        # Global counts must remain 3 despite filter
        self.assertEqual(res_f.data["counts"]["total_scanned"], 3)
        self.assertEqual(res_f.data["metadata"]["final_count"], 3)

        # 3. Test line filter 1.5 matches Midfielder only
        res_line = self.router.handle_get_props_results(line=1.5)
        self.assertEqual(res_line.data["total"], 1)
        self.assertEqual(res_line.data["items"][0]["player_name"], "Midfielder Player")
        self.assertEqual(res_line.data["counts"]["total_scanned"], 3)

        # 4. Test zero-result filter returns total=0 while preserving global counts
        res_zero = self.router.handle_get_props_results(search="NonExistentName")
        self.assertEqual(res_zero.data["total"], 0)
        self.assertEqual(len(res_zero.data["items"]), 0)
        self.assertEqual(res_zero.data["counts"]["total_scanned"], 3)
        self.assertEqual(res_zero.data["metadata"]["has_scanned"], True)

    def test_empty_state_before_scan(self):
        res = self.router.handle_get_props_results()
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["total"], 0)
        self.assertEqual(res.data["counts"]["total_scanned"], 0)
        self.assertEqual(res.data["metadata"]["has_scanned"], False)

    @patch("providers.statshub.client.StatsHubClient.fetch_props")
    def test_prop_detail_endpoint(self, mock_fetch):
        mock_fetch.return_value = {
            "players": [
                {
                    "playerName": "Cole Palmer",
                    "teamName": "Chelsea",
                    "opponentName": "Arsenal",
                    "fixtureId": "fix-chelsea-arsenal",
                    "stat": "shots",
                    "hitRatePct": 75.0,
                    "hitRateCount": 6,
                    "sampleSize": 8,
                    "average": 3.1,
                    "last5Avg": 3.4,
                    "last10Avg": 3.1,
                    "bookmakerOdds": [{"bookmaker": "Bet365", "line": 0.5, "side": "over", "decimalOdds": 1.90}],
                    "historicalMatches": [
                        {"opponent": "Arsenal", "date": "2026-02-15", "minutesPlayed": 90, "statValue": 3, "homeAway": "H", "started": True}
                    ],
                }
            ],
            "pagination": {"page": 1, "limit": 50, "total": 1, "totalPages": 1},
        }

        scan_res = self.router.handle_post_scan_props({})
        prop_id = scan_res.data["items"][0]["prop_id"]

        detail_res = self.router.handle_get_prop_detail(prop_id)
        self.assertEqual(detail_res.status_code, 200)
        data = detail_res.data

        # Explicit top-level & nested contract validation
        self.assertEqual(data["player_name"], "Cole Palmer")
        self.assertEqual(data["prop"]["player_name"], "Cole Palmer")
        self.assertEqual(data["prop"]["team"], "Chelsea")
        self.assertEqual(data["prop"]["opponent"], "Arsenal")
        self.assertIn("score", data)
        self.assertIn("statistics", data)
        self.assertEqual(data["statistics"]["sample_size"], 8)
        self.assertEqual(data["statistics"]["hits"], 6)
        self.assertIn("edges", data)
        self.assertIn("statistical", data["edges"])
        self.assertIn("decision", data)
        self.assertIn("score", data["decision"])
        self.assertIn("reasons", data["decision"])
        self.assertIn("warnings", data["decision"])
        self.assertIn("execution", data)
        self.assertIn("status", data["execution"])
        self.assertIn("recent_matches", data)
        self.assertEqual(len(data["recent_matches"]), 1)
        self.assertEqual(data["recent_matches"][0]["opponent"], "Arsenal")

        # Test not found
        not_found_res = self.router.handle_get_prop_detail("non_existent_prop_id")
        self.assertEqual(not_found_res.status_code, 404)
        self.assertIsNone(not_found_res.data)
        self.assertTrue(len(not_found_res.errors) > 0)

    @patch("providers.betclic.provider.BetclicProvider.discover", return_value=[])
    @patch("providers.superbet.provider.SuperbetProvider.discover", return_value=[])
    @patch("providers.statshub.client.StatsHubClient.fetch_props")
    def test_decision_engine_consistency_tai_abed_contract(self, mock_fetch, _mock_sb, _mock_bc):
        """Verify Tai Abed-like prop maintains exact single-source-of-truth Decision Engine consistency."""
        mock_fetch.return_value = {
            "players": [
                {
                    "playerName": "Tai Abed",
                    "teamName": "PSV",
                    "opponentName": "Ajax",
                    "fixtureId": "fix-psv-ajax",
                    "stat": "shots",
                    "hitRatePct": 100.0,
                    "hitRateCount": 10,
                    "sampleSize": 10,
                    "average": 3.3,
                    "last5Avg": 3.4,
                    "last10Avg": 3.3,
                    "bookmakerOdds": [{"bookmaker": "Bet365", "line": 0.5, "side": "over", "decimalOdds": 1.44}],
                }
            ],
            "pagination": {"page": 1, "limit": 50, "total": 1, "totalPages": 1},
        }

        scan_res = self.router.handle_post_scan_props({})
        items = scan_res.data["items"]
        self.assertEqual(len(items), 1)
        prop_summary = items[0]
        prop_id = prop_summary["prop_id"]

        detail_res = self.router.handle_get_prop_detail(prop_id)
        self.assertEqual(detail_res.status_code, 200)
        detail = detail_res.data

        # 1. Decision object is authoritative single source of truth
        self.assertIn("decision", detail)
        dec = detail["decision"]
        self.assertEqual(dec["score"], detail["score"])
        self.assertEqual(dec["classification"], detail["classification"])
        self.assertEqual(dec["raw_edge_pct"], detail["raw_edge_pct"])
        self.assertEqual(dec["historical_probability"], detail["historical_probability"])
        self.assertEqual(dec["reference_market_probability"], detail["reference_market_probability"])
        self.assertAlmostEqual(dec["score"], 92.0, delta=0.5)
        self.assertAlmostEqual(dec["raw_edge_pct"], 30.6, delta=1.0)
        self.assertEqual(dec["historical_probability"], 1.0)
        self.assertAlmostEqual(dec["reference_market_probability"], 1.0 / 1.44, delta=0.01)

        # 2. Reference-only prop guarantees
        self.assertEqual(detail["execution_status"], "REFERENCE_ONLY")
        self.assertIsNone(dec["execution_edge"])
        self.assertIsNone(dec["execution_edge_pct"])
        self.assertIsNone(dec["execution_market_probability"])
        self.assertIsNone(detail["execution_edge"])
        self.assertIsNone(detail["execution_edge_pct"])

        # 3. Table summary vs Detail consistency
        self.assertEqual(prop_summary["score"], detail["score"])
        self.assertEqual(prop_summary["classification"], detail["classification"])
        self.assertEqual(prop_summary["raw_edge_pct"], detail["raw_edge_pct"])
        self.assertEqual(prop_summary["execution_status"], detail["execution_status"])
        self.assertIn("data_quality_flags", detail)

    @patch("providers.betclic.provider.BetclicProvider.discover", return_value=[])
    @patch("providers.superbet.provider.SuperbetProvider.discover", return_value=[])
    @patch("providers.statshub.client.StatsHubClient.fetch_props")
    def test_stage18_decision_workspace_filtering_sorting_and_telemetry(self, mock_fetch, _mock_sb, _mock_bc):
        """Test complete filtering across categories, bookmakers, and sorting dimensions."""
        mock_fetch.return_value = {
            "players": [
                {
                    "playerName": "Abed Player",
                    "teamName": "PSV",
                    "opponentName": "Ajax",
                    "fixtureId": "fix-1",
                    "stat": "shots",
                    "hitRatePct": 100.0,
                    "hitRateCount": 10,
                    "sampleSize": 10,
                    "average": 3.3,
                    "last5Avg": 3.5,
                    "last10Avg": 3.3,
                    "bookmakerOdds": [{"bookmaker": "Bet365", "line": 0.5, "side": "over", "decimalOdds": 1.44}],
                },
                {
                    "playerName": "Bettable Player",
                    "teamName": "Legia",
                    "opponentName": "Lech",
                    "fixtureId": "fix-2",
                    "stat": "shots",
                    "hitRatePct": 80.0,
                    "hitRateCount": 8,
                    "sampleSize": 10,
                    "average": 2.2,
                    "last5Avg": 2.5,
                    "last10Avg": 2.2,
                    "bookmakerOdds": [{"bookmaker": "Bet365", "line": 0.5, "side": "over", "decimalOdds": 1.80}],
                },
            ],
            "pagination": {"page": 1, "limit": 50, "total": 2, "totalPages": 1},
        }

        # Seed scan
        self.router.handle_post_scan_props({})
        # Inject realistic simulated execution quote for Bettable Player
        PlatformAPIService._cached_props_results[1]["execution_status"] = "BETTABLE"
        PlatformAPIService._cached_props_results[1]["best_execution_odds"] = 1.95
        PlatformAPIService._cached_props_results[1]["best_execution_bookmaker"] = "Superbet"
        PlatformAPIService._cached_props_results[1]["execution_odds"] = {
            "Superbet": {"status": "AVAILABLE", "decimal_odds": 1.95, "line": 0.5},
            "Betclic": {"status": "AVAILABLE", "decimal_odds": 1.90, "line": 0.5},
        }
        PlatformAPIService._cached_props_results[1]["execution_edge"] = 0.2872
        PlatformAPIService._cached_props_results[1]["execution_edge_pct"] = 28.7

        # 1. Query with category "all" returns both
        res_all = self.router.handle_get_props_results(category="all")
        self.assertEqual(res_all.data["total"], 2)
        self.assertEqual(res_all.data["counts"]["total_scanned"], 2)
        self.assertEqual(res_all.data["counts"]["total_bettable"], 1)
        self.assertEqual(res_all.data["counts"]["total_polish_odds"], 1)

        # 2. Query with category "bettable" returns only bettable item
        res_bettable = self.router.handle_get_props_results(category="bettable")
        self.assertEqual(res_bettable.data["total"], 1)
        self.assertEqual(res_bettable.data["items"][0]["player_name"], "Bettable Player")

        # 3. Query with category "reference_only" returns Abed Player
        res_ref = self.router.handle_get_props_results(category="reference_only")
        self.assertEqual(res_ref.data["total"], 1)
        self.assertEqual(res_ref.data["items"][0]["player_name"], "Abed Player")

        # 4. Filter by bookmaker "superbet"
        res_superbet = self.router.handle_get_props_results(bookmaker="superbet")
        self.assertEqual(res_superbet.data["total"], 1)
        self.assertEqual(res_superbet.data["items"][0]["player_name"], "Bettable Player")

        # 5. Sort by Execution Edge
        res_sorted_exec = self.router.handle_get_props_results(sort_by="exec_edge")
        self.assertEqual(res_sorted_exec.data["items"][0]["player_name"], "Bettable Player")

        # 6. Sort by Hit Rate
        res_sorted_hr = self.router.handle_get_props_results(sort_by="hit_rate")
        self.assertEqual(res_sorted_hr.data["items"][0]["player_name"], "Abed Player")

        # 7. Sort by Average
        res_sorted_avg = self.router.handle_get_props_results(sort_by="avg")
        self.assertEqual(res_sorted_avg.data["items"][0]["player_name"], "Abed Player")

    @patch("providers.statshub.client.StatsHubClient.fetch_props")
    def test_stage18_2_stat_type_market_filter_integrity(self, mock_fetch):
        """Regression test suite for Stage 18.2 stat-type selection and cache integrity."""
        # 1. Mock responses for shots vs fouls vs cards vs shotsOnTarget
        def mock_fetch_props_side_effect(cfg=None, *args, **kwargs):
            if hasattr(cfg, "stat"):
                stat_str = cfg.stat
            elif isinstance(cfg, str):
                stat_str = cfg
            else:
                stat_str = kwargs.get("stat") or "shots"

            norm_stat = str(stat_str).lower()
            if "foul" in norm_stat:
                return {
                    "players": [
                        {
                            "playerName": "Fouls Player",
                            "teamName": "Team A",
                            "position": "M",
                            "stat": "fouls",
                            "hitRatePct": 85.0,
                            "hitRateCount": 17,
                            "sampleSize": 20,
                            "average": 2.4,
                            "bookmakerOdds": [{"bookmaker": "Bet365", "line": 1.5, "side": "over", "decimalOdds": 1.75}],
                        }
                    ],
                    "pagination": {"page": 1, "limit": 50, "total": 1, "totalPages": 1},
                }
            elif "card" in norm_stat:
                return {
                    "players": [
                        {
                            "playerName": "Cards Player",
                            "teamName": "Team B",
                            "position": "D",
                            "stat": "cards",
                            "hitRatePct": 60.0,
                            "hitRateCount": 6,
                            "sampleSize": 10,
                            "average": 0.6,
                            "bookmakerOdds": [{"bookmaker": "Bet365", "line": 0.5, "side": "over", "decimalOdds": 3.40}],
                        }
                    ],
                    "pagination": {"page": 1, "limit": 50, "total": 1, "totalPages": 1},
                }
            elif "target" in norm_stat:
                return {
                    "players": [
                        {
                            "playerName": "SOT Player",
                            "teamName": "Team C",
                            "position": "F",
                            "stat": "shotsOnTarget",
                            "hitRatePct": 90.0,
                            "hitRateCount": 9,
                            "sampleSize": 10,
                            "average": 1.8,
                            "bookmakerOdds": [{"bookmaker": "Bet365", "line": 0.5, "side": "over", "decimalOdds": 1.50}],
                        }
                    ],
                    "pagination": {"page": 1, "limit": 50, "total": 1, "totalPages": 1},
                }
            else:
                return {
                    "players": [
                        {
                            "playerName": "Shots Player",
                            "teamName": "Team D",
                            "position": "F",
                            "stat": "shots",
                            "hitRatePct": 75.0,
                            "hitRateCount": 15,
                            "sampleSize": 20,
                            "average": 3.1,
                            "bookmakerOdds": [{"bookmaker": "Bet365", "line": 1.5, "side": "over", "decimalOdds": 1.85}],
                        }
                    ],
                    "pagination": {"page": 1, "limit": 50, "total": 1, "totalPages": 1},
                }

        mock_fetch.side_effect = mock_fetch_props_side_effect

        # Step 1: Scan Shots
        shots_scan = self.router.handle_post_scan_props({"stat": "shots"})
        self.assertEqual(shots_scan.status_code, 200)
        self.assertEqual(len(shots_scan.data["items"]), 1)
        self.assertEqual(shots_scan.data["items"][0]["stat"], "shots")
        self.assertEqual(shots_scan.data["items"][0]["stat_type"], "SHOTS")
        self.assertEqual(shots_scan.data["items"][0]["market"], "Over 1.5 Shots")

        # Step 2: Scan Fouls
        fouls_scan = self.router.handle_post_scan_props({"stat": "fouls"})
        self.assertEqual(fouls_scan.status_code, 200)
        self.assertEqual(len(fouls_scan.data["items"]), 1)
        self.assertEqual(fouls_scan.data["items"][0]["stat"], "fouls")
        self.assertEqual(fouls_scan.data["items"][0]["stat_type"], "FOULS")
        self.assertEqual(fouls_scan.data["items"][0]["market"], "Over 1.5 Fouls")
        self.assertEqual(fouls_scan.data["items"][0]["player_name"], "Fouls Player")

        # Step 3: Cache partition verification: query Fouls returns Fouls, query Shots returns Shots
        fouls_res = self.router.handle_get_props_results(stat="fouls")
        self.assertEqual(fouls_res.data["total"], 1)
        self.assertEqual(fouls_res.data["items"][0]["stat"], "fouls")
        self.assertEqual(fouls_res.data["items"][0]["player_name"], "Fouls Player")
        self.assertEqual(fouls_res.data["items"][0]["market"], "Over 1.5 Fouls")

        shots_res = self.router.handle_get_props_results(stat="shots")
        self.assertEqual(shots_res.data["total"], 1)
        self.assertEqual(shots_res.data["items"][0]["stat"], "shots")
        self.assertEqual(shots_res.data["items"][0]["player_name"], "Shots Player")
        self.assertEqual(shots_res.data["items"][0]["market"], "Over 1.5 Shots")

        # Step 4: Scan Cards & Shots on Target
        cards_scan = self.router.handle_post_scan_props({"stat": "cards"})
        self.assertEqual(cards_scan.data["items"][0]["stat"], "cards")
        self.assertEqual(cards_scan.data["items"][0]["stat_type"], "CARDS")
        self.assertEqual(cards_scan.data["items"][0]["market"], "Over 0.5 Cards")

        sot_scan = self.router.handle_post_scan_props({"stat": "shotsOnTarget"})
        self.assertEqual(sot_scan.data["items"][0]["stat"], "shots_on_target")
        self.assertEqual(sot_scan.data["items"][0]["stat_type"], "SHOTS_ON_TARGET")
        self.assertEqual(sot_scan.data["items"][0]["market"], "Over 0.5 Shots on Target")



if __name__ == "__main__":
    unittest.main()

