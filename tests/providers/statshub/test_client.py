"""
Unit tests for StatsHub HTTP Client
"""

import unittest
from unittest.mock import MagicMock, patch
import requests

from providers.statshub.config import StatsHubConfig
from providers.statshub.client import (
    StatsHubClient,
    StatsHubHTTPError,
    StatsHubTimeoutError,
    StatsHubMalformedResponseError,
    StatsHubClientError,
)


class TestStatsHubClient(unittest.TestCase):

    def setUp(self):
        self.config = StatsHubConfig(
            stat="shots",
            start_of_day=1787529600,
            end_of_day=1787615999,
            tournaments="83,36,40",
            positions="D,M,F",
            last_games=10,
            hit_rate_threshold=50,
            stat_threshold=1,
            max_retries=2,
            request_timeout=5.0,
        )

    def test_query_builder(self):
        params = self.config.build_query_params()
        self.assertEqual(params["stat"], "shots")
        self.assertEqual(params["positions"], "D,M,F")
        self.assertEqual(params["lastGames"], "10")
        self.assertEqual(params["hitRateThreshold"], "50")
        self.assertEqual(params["statThreshold"], "1")
        self.assertEqual(params["page"], "1")
        self.assertEqual(params["limit"], "50")

    @patch("requests.Session.get")
    def test_successful_fetch(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = '{"data": [{"playerName": "Kylian Mbappe", "stat": "shots"}]}'
        mock_resp.json.return_value = {"data": [{"playerName": "Kylian Mbappe", "stat": "shots"}]}
        mock_get.return_value = mock_resp

        client = StatsHubClient(config=self.config)
        data = client.fetch_props()

        self.assertIn("data", data)
        self.assertEqual(data["data"][0]["playerName"], "Kylian Mbappe")
        mock_get.assert_called_once()

    @patch("requests.Session.get")
    def test_empty_response(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = ""
        mock_get.return_value = mock_resp

        client = StatsHubClient(config=self.config)
        data = client.fetch_props()
        self.assertEqual(data, {})

    @patch("requests.Session.get")
    def test_malformed_json(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "INVALID_NOT_JSON"
        mock_resp.json.side_effect = ValueError("Invalid JSON")
        mock_get.return_value = mock_resp

        client = StatsHubClient(config=self.config)
        with self.assertRaises(StatsHubMalformedResponseError):
            client.fetch_props()

    @patch("requests.Session.get")
    def test_http_403_error(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 403
        mock_resp.text = "Forbidden"
        mock_get.return_value = mock_resp

        client = StatsHubClient(config=self.config)
        with self.assertRaises(StatsHubHTTPError) as ctx:
            client.fetch_props()
        self.assertEqual(ctx.exception.status_code, 403)

    @patch("requests.Session.get")
    def test_http_429_retry_exhaustion(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 429
        mock_resp.text = "Too Many Requests"
        mock_get.return_value = mock_resp

        client = StatsHubClient(config=self.config)
        with self.assertRaises(StatsHubHTTPError) as ctx:
            client.fetch_props()
        self.assertEqual(ctx.exception.status_code, 429)
        self.assertEqual(mock_get.call_count, 2)

    @patch("requests.Session.get")
    def test_http_500_server_error(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "Internal Server Error"
        mock_get.return_value = mock_resp

        client = StatsHubClient(config=self.config)
        with self.assertRaises(StatsHubHTTPError) as ctx:
            client.fetch_props()
        self.assertEqual(ctx.exception.status_code, 500)

    @patch("requests.Session.get")
    def test_timeout_handling(self, mock_get):
        mock_get.side_effect = requests.Timeout("Connection timed out")

        client = StatsHubClient(config=self.config)
        with self.assertRaises(StatsHubTimeoutError):
            client.fetch_props()
        self.assertEqual(mock_get.call_count, 2)

    def test_disabled_config_returns_empty(self):
        cfg = StatsHubConfig(enabled=False)
        client = StatsHubClient(config=cfg)
        res = client.fetch_props()
        self.assertEqual(res, {})

    @patch("requests.Session.get")
    def test_fetch_player_trends_success(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = '{"data": [{"playerId": 101, "statType": "shots", "line": 2.5, "trendHits": 8, "trendWindow": 10}]}'
        mock_resp.json.return_value = {"data": [{"playerId": 101, "statType": "shots", "line": 2.5, "trendHits": 8, "trendWindow": 10}]}
        mock_get.return_value = mock_resp

        client = StatsHubClient(config=self.config)
        res = client.fetch_player_trends(games="16416308", stat_type="shots")

        self.assertIn("data", res)
        self.assertEqual(res["data"][0]["trendHits"], 8)
        mock_get.assert_called_once()
        call_url = mock_get.call_args[0][0]
        call_params = mock_get.call_args[1]["params"]
        self.assertIn("/api/props/player-trends", call_url)
        self.assertEqual(call_params.get("games"), "16416308")

    def test_fetch_player_trends_validation(self):
        client = StatsHubClient(config=StatsHubConfig(games="", player_id=None, unique_tournament_id=None))
        with self.assertRaises(StatsHubClientError):
            client.fetch_player_trends()

    @patch("requests.Session.get")
    def test_fetch_team_trends_success(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = '{"data": [{"teamId": 501, "statType": "corners", "line": 4.5, "trendHits": 7}]}'
        mock_resp.json.return_value = {"data": [{"teamId": 501, "statType": "corners", "line": 4.5, "trendHits": 7}]}
        mock_get.return_value = mock_resp

        client = StatsHubClient(config=self.config)
        res = client.fetch_team_trends(games=["16416308", "16416309"], stat_type="corners")

        self.assertIn("data", res)
        self.assertEqual(res["data"][0]["trendHits"], 7)
        mock_get.assert_called_once()
        call_url = mock_get.call_args[0][0]
        call_params = mock_get.call_args[1]["params"]
        self.assertIn("/api/props/team-trends", call_url)
        self.assertEqual(call_params.get("games"), "16416308,16416309")

    def test_fetch_team_trends_validation(self):
        client = StatsHubClient(config=StatsHubConfig(games=""))
        with self.assertRaises(StatsHubClientError):
            client.fetch_team_trends()

    def test_cache_keys_isolation(self):
        cfg_hunter = StatsHubConfig(mode="hunter", stat="shots")
        cfg_ptrends = StatsHubConfig(mode="player_trends", games="16416308", stat="shots")
        cfg_ttrends = StatsHubConfig(mode="team_trends", games="16416308", stat="shots")

        key1 = cfg_hunter.build_cache_key()
        key2 = cfg_ptrends.build_cache_key()
        key3 = cfg_ttrends.build_cache_key()

        self.assertNotEqual(key1, key2)
        self.assertNotEqual(key2, key3)
        self.assertNotEqual(key1, key3)
        self.assertTrue(key1.startswith("statshub:global:hunter:"))
        self.assertTrue(key2.startswith("statshub:event:player_trends:"))
        self.assertTrue(key3.startswith("statshub:event:team_trends:"))


if __name__ == "__main__":
    unittest.main()

