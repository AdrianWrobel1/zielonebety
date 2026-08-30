"""
Hotfix regression verification suite for Player Props.
Tests strictly:
1. Valid StatsHub payload -> parser produces >0 props
2. Stat mapping for fouls / shotsOnTarget / shots_on_target
3. scan -> cache -> GET /props/results preserves found props
"""

import unittest
from unittest.mock import patch

from providers.statshub.parser import StatsHubParser
from api.routes import APIRouter
from api.services import PlatformAPIService
from database.connection import DatabaseManager
from database.config import DatabaseConfig
from providers.base.provider_registry import ProviderRegistry
from providers.statshub.provider import StatsHubProvider


class TestPlayerPropsHotfixRegression(unittest.TestCase):

    def setUp(self):
        ProviderRegistry.clear()
        ProviderRegistry.register("statshub", StatsHubProvider)

        db_mgr = DatabaseManager(DatabaseConfig.default_sqlite_in_memory())
        db_mgr.create_tables()

        self.service = PlatformAPIService(db_manager=db_mgr)
        self.router = APIRouter(service=self.service)

        # Clear cache before each test
        PlatformAPIService._cached_props_results = []
        PlatformAPIService._cached_props_by_stat = {}
        PlatformAPIService._last_props_scan_metadata = {}
        PlatformAPIService._last_props_scan_metadata_by_stat = {}

    def tearDown(self):
        ProviderRegistry.clear()

    def test_1_statshub_payload_produces_props(self):
        """Test 1: Valid StatsHub payload with players array produces > 0 parsed props."""
        parser = StatsHubParser()
        raw_payload = {
            "players": [
                {
                    "id": 101,
                    "name": "Rodrigo Riquelme",
                    "teamName": "Real Betis",
                    "matchup": {"opponentTeamName": "Celta Vigo", "isHome": True},
                    "fixtureInfo": {"id": 15001, "homeTeamName": "Real Betis", "awayTeamName": "Celta Vigo", "tournamentName": "La Liga"},
                    "position": "M",
                    "stats": {"fouls": 12},
                    "averages": {"fouls": 1.71},
                    "hitRates": {"fouls": 85.7},
                    "count": 7,
                    "recentGames": [
                        {"event": {"homeTeamName": "Real Betis", "awayTeamName": "Getafe"}, "playerStats": {"fouls": 2, "minutesPlayed": 80}}
                    ],
                    "oddsByLine": {
                        "0.5": {"over": [{"bookmakerName": "Bet365", "oddsValue": 1.45}], "under": []},
                        "1.5": {"over": [{"bookmakerName": "Bet365", "oddsValue": 2.80}], "under": []},
                    }
                }
            ]
        }

        results = parser.parse_payload(raw_payload)
        self.assertGreater(len(results), 0, "Parser must produce > 0 props for valid payload")
        self.assertEqual(results[0].player_stat.player_name, "Rodrigo Riquelme")
        self.assertEqual(results[0].player_stat.stat_type, "fouls")
        self.assertIn(0.5, results[0].available_lines)
        self.assertIn(1.5, results[0].available_lines)

    def test_2_stat_mapping_fouls_and_shotsontarget(self):
        """Test 2: Stat mapping correctly normalizes fouls, shotsOnTarget, and shots_on_target."""
        # Check service normalization
        self.assertEqual(PlatformAPIService.normalize_stat_key("fouls"), "fouls")
        self.assertEqual(PlatformAPIService.normalize_stat_key("shotsOnTarget"), "shots_on_target")
        self.assertEqual(PlatformAPIService.normalize_stat_key("shots_on_target"), "shots_on_target")
        self.assertEqual(PlatformAPIService.normalize_stat_key("shots"), "shots")
        self.assertEqual(PlatformAPIService.normalize_stat_key("cards"), "cards")

        # Check market display formatting
        self.assertEqual(PlatformAPIService.format_market_display("FOULS", 1.5), "Over 1.5 Fouls")
        self.assertEqual(PlatformAPIService.format_market_display("SHOTS_ON_TARGET", 0.5), "Over 0.5 Shots on Target")

        # Check parser stat detection for both stat types
        parser = StatsHubParser()
        sot_item = {
            "name": "Kylian Mbappé",
            "teamName": "Real Madrid",
            "stat": "shotsOnTarget",
            "fixtureInfo": {"id": 15002, "homeTeamName": "Real Madrid", "awayTeamName": "Espanyol"},
            "averages": {"shotsOnTarget": 2.4},
            "hitRates": {"shotsOnTarget": 90.0},
        }
        res_sot = parser._parse_single_prop_item(sot_item, {})
        self.assertIsNotNone(res_sot)
        self.assertEqual(res_sot.player_stat.stat_type, "shots_on_target")

        foul_item = {
            "name": "Casemiro",
            "teamName": "Man United",
            "stat": "fouls",
            "fixtureInfo": {"id": 15003, "homeTeamName": "Man United", "awayTeamName": "Arsenal"},
            "averages": {"fouls": 2.1},
            "hitRates": {"fouls": 80.0},
        }
        res_foul = parser._parse_single_prop_item(foul_item, {})
        self.assertIsNotNone(res_foul)
        self.assertEqual(res_foul.player_stat.stat_type, "fouls")

    @patch("providers.statshub.client.StatsHubClient.fetch_props")
    def test_3_scan_cache_get_results_flow(self, mock_fetch):
        """Test 3: POST /props/scan -> cache -> GET /props/results preserves found props."""
        mock_fetch.return_value = {
            "players": [
                {
                    "id": 201,
                    "playerName": "Bukayo Saka",
                    "teamName": "Arsenal",
                    "opponentName": "Chelsea",
                    "fixtureId": "fix-ars-che",
                    "stat": "shotsOnTarget",
                    "position": "F",
                    "average": 1.8,
                    "hitRateCount": 8,
                    "sampleSize": 10,
                    "hitRatePct": 80.0,
                    "bookmakerOdds": [
                        {"bookmaker": "Bet365", "line": 0.5, "side": "over", "decimalOdds": 1.85},
                        {"bookmaker": "Superbet", "line": 0.5, "side": "over", "decimalOdds": 1.90},
                    ],
                }
            ],
            "pagination": {"page": 1, "limit": 50, "total": 1, "totalPages": 1},
        }

        # 1. Trigger scan for shotsOnTarget
        scan_res = self.router.handle_post_scan_props({"stat": "shotsOnTarget", "min_odds": 1.0, "line": 0.5})
        self.assertEqual(scan_res.status_code, 200)
        self.assertIn("items", scan_res.data)
        self.assertEqual(len(scan_res.data["items"]), 1)
        self.assertEqual(scan_res.data["items"][0]["player_name"], "Bukayo Saka")

        # 2. Retrieve results via GET handler
        get_res = self.router.handle_get_props_results(stat="shotsOnTarget", line=0.5)
        self.assertEqual(get_res.status_code, 200)
        self.assertIn("items", get_res.data)
        self.assertEqual(len(get_res.data["items"]), 1, "GET /props/results must preserve scanned props from cache")
        self.assertEqual(get_res.data["items"][0]["player_name"], "Bukayo Saka")
        self.assertEqual(get_res.data["items"][0]["stat"], "shots_on_target")


    @patch("providers.betclic.provider.BetclicProvider.discover")
    @patch("providers.superbet.provider.SuperbetProvider.discover")
    @patch("providers.statshub.client.StatsHubClient.fetch_props")
    def test_4_statshub_props_isolated_from_betclic_403(self, mock_fetch, mock_sb_disc, mock_bc_disc):
        """Test 4: Betclic throwing HTTP 403 or network error does NOT zero StatsHub props."""
        # StatsHub returns valid props data
        mock_fetch.return_value = {
            "players": [
                {
                    "id": 301,
                    "name": "Tjaronn Chery",
                    "teamName": "NEC Nijmegen",
                    "matchup": {"opponentTeamName": "PEC Zwolle", "isHome": True},
                    "fixtureInfo": {"id": 16001, "homeTeamName": "NEC Nijmegen", "awayTeamName": "PEC Zwolle", "tournamentName": "Eredivisie"},
                    "position": "M",
                    "stats": {"shots": 10},
                    "averages": {"shots": 2.5},
                    "hitRates": {"shots": 100.0},
                    "count": 10,
                    "oddsByLine": {
                        "0.5": {"over": [{"bookmakerName": "Bet365", "oddsValue": 1.40}], "under": []}
                    }
                }
            ],
            "pagination": {"page": 1, "limit": 50, "total": 1, "totalPages": 1},
        }

        # Superbet discovery succeeds
        mock_sb_disc.return_value = []

        # Betclic discovery throws HTTP 403 Forbidden
        from providers.betclic.exceptions import BetclicAccessDeniedError
        mock_bc_disc.side_effect = BetclicAccessDeniedError("Betclic HTTP 403 Forbidden")

        # Trigger scan
        scan_res = self.router.handle_post_scan_props({"stat": "shots", "min_odds": 1.0})
        self.assertEqual(scan_res.status_code, 200)
        self.assertIn("items", scan_res.data)
        self.assertEqual(len(scan_res.data["items"]), 1, "StatsHub props must NOT be zeroed when Betclic returns 403")

        item = scan_res.data["items"][0]
        self.assertEqual(item["player_name"], "Tjaronn Chery")
        self.assertEqual(item["team"], "NEC Nijmegen")
        # Execution status defaults gracefully to REFERENCE_ONLY
        self.assertEqual(item["execution_status"], "REFERENCE_ONLY")


if __name__ == "__main__":
    unittest.main()
