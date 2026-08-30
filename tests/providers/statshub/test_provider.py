"""
Unit tests for StatsHubProvider lifecycle & BaseProvider compliance
"""

import unittest
from unittest.mock import patch, MagicMock

from providers.statshub.provider import StatsHubProvider
from providers.statshub.config import StatsHubConfig
from providers.base.provider_context import ProviderContext
from providers.base.models import ProviderMetadata
from providers.base.provider_state import ProviderState
from providers.base.provider_registry import ProviderRegistry


class TestStatsHubProvider(unittest.TestCase):

    def test_provider_registration(self):
        self.assertTrue(ProviderRegistry.is_registered("statshub"))
        cls = ProviderRegistry.get("statshub")
        self.assertEqual(cls, StatsHubProvider)

    def test_provider_initialization(self):
        ctx = ProviderContext(provider_name="statshub")
        meta = ProviderMetadata(name="statshub", code="SH")
        provider = StatsHubProvider(context=ctx, metadata=meta)
        provider.initialize()
        self.assertEqual(provider.state, ProviderState.READY)

    def test_disabled_provider_run(self):
        ctx = ProviderContext(provider_name="statshub")
        meta = ProviderMetadata(name="statshub", code="SH", enabled=False)
        provider = StatsHubProvider(context=ctx, metadata=meta)
        result = provider.run()
        self.assertEqual(result.status, ProviderState.CANCELLED)

    def test_mock_payload_full_run(self):
        sample_payload = {
            "data": [
                {
                    "playerName": "Bukayo Saka",
                    "teamName": "Arsenal",
                    "opponentName": "Chelsea",
                    "fixtureId": "fix-ars-che",
                    "stat": "shots",
                    "position": "F",
                    "average": 3.2,
                    "hitRateCount": 8,
                    "sampleSize": 10,
                    "hitRatePct": 80.0,
                    "bookmakerOdds": [
                        {"bookmaker": "Bet365", "line": 0.5, "side": "over", "decimalOdds": 1.72},
                        {"bookmaker": "Paddy Power", "line": 0.5, "side": "over", "decimalOdds": 1.80},
                    ],
                }
            ]
        }

        ctx = ProviderContext(provider_name="statshub")
        meta = ProviderMetadata(name="statshub", code="SH")
        cfg = StatsHubConfig(stat="shots")
        provider = StatsHubProvider(context=ctx, metadata=meta, config=cfg)
        provider.set_mock_payload(sample_payload)

        result = provider.run()

        self.assertEqual(result.status, ProviderState.COMPLETED)
        self.assertEqual(len(result.parsed_objects), 1)
        self.assertTrue(result.validation_report.is_valid)
        self.assertEqual(provider.acquisition_metrics["players_found"], 1)
        self.assertEqual(provider.acquisition_metrics["odds_found"], 2)


if __name__ == "__main__":
    unittest.main()
