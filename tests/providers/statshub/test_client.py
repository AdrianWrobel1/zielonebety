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


if __name__ == "__main__":
    unittest.main()
