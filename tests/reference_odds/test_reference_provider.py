"""
Unit Tests for Reference Odds Provider Interface & The-Odds-API Parser
"""

from decimal import Decimal
import unittest

from normalization.market_identity import CanonicalMarketType
from normalization.selection_identity import CanonicalSelectionType
from reference_odds.provider import (
    MockReferenceOddsProvider,
    TheOddsApiReferenceProvider,
)
from tests.fixtures.reference_odds_fixtures import (
    create_reference_event,
    fixture_valid_1x2_reference_market,
)


class TestReferenceOddsProvider(unittest.TestCase):
    """Verifies provider abstractions, quota accounting, and payload parsing."""

    def test_mock_provider_contract(self):
        ev = create_reference_event(markets=[fixture_valid_1x2_reference_market()])
        provider = MockReferenceOddsProvider(events=[ev])

        events = provider.fetch_reference_events(sport="football")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].home_team, "Arsenal")

        metrics = provider.get_quota_metrics()
        self.assertEqual(metrics.total_requests, 1)
        self.assertEqual(metrics.cache_hits, 1)

    def test_the_odds_api_payload_parsing_multi_market(self):
        """Tests conversion of The-Odds-API raw JSON to canonical ReferenceEvent."""
        raw_payload = [
            {
                "id": "e305e5520a221f75e243983273e97017",
                "sport_key": "soccer_epl",
                "sport_title": "EPL",
                "commence_time": "2026-08-20T19:00:00Z",
                "home_team": "Arsenal",
                "away_team": "Chelsea",
                "bookmakers": [
                    {
                        "key": "pinnacle",
                        "title": "Pinnacle",
                        "last_update": "2026-08-17T14:30:00Z",
                        "markets": [
                            {
                                "key": "h2h",
                                "outcomes": [
                                    {"name": "Arsenal", "price": 2.05},
                                    {"name": "Draw", "price": 3.50},
                                    {"name": "Chelsea", "price": 3.80},
                                ],
                            },
                            {
                                "key": "btts",
                                "outcomes": [
                                    {"name": "Yes", "price": 1.75},
                                    {"name": "No", "price": 2.10},
                                ],
                            },
                            {
                                "key": "totals",
                                "outcomes": [
                                    {"name": "Over", "price": 1.90, "point": 2.5},
                                    {"name": "Under", "price": 1.95, "point": 2.5},
                                ],
                            },
                        ],
                    }
                ],
            }
        ]

        provider = TheOddsApiReferenceProvider(api_key="dummy_test_key")
        parsed = provider._parse_the_odds_api_response(raw_payload)

        self.assertEqual(len(parsed), 1)
        event = parsed[0]
        self.assertEqual(event.home_team, "Arsenal")
        self.assertEqual(event.away_team, "Chelsea")
        self.assertEqual(len(event.markets), 3)

        # 1. Check 1X2 Market
        mkt_1x2 = next((m for m in event.markets if m.market_type == CanonicalMarketType.ONE_X_TWO.value), None)
        self.assertIsNotNone(mkt_1x2)
        self.assertEqual(mkt_1x2.selections[CanonicalSelectionType.HOME.value].odds, Decimal("2.05"))
        self.assertEqual(mkt_1x2.selections[CanonicalSelectionType.DRAW.value].odds, Decimal("3.50"))
        self.assertEqual(mkt_1x2.selections[CanonicalSelectionType.AWAY.value].odds, Decimal("3.80"))

        # 2. Check BTTS Market
        mkt_btts = next((m for m in event.markets if m.market_type == CanonicalMarketType.BTTS.value), None)
        self.assertIsNotNone(mkt_btts)
        self.assertEqual(mkt_btts.selections[CanonicalSelectionType.YES.value].odds, Decimal("1.75"))
        self.assertEqual(mkt_btts.selections[CanonicalSelectionType.NO.value].odds, Decimal("2.10"))

        # 3. Check Totals Market
        mkt_totals = next((m for m in event.markets if m.market_type == CanonicalMarketType.TOTALS.value), None)
        self.assertIsNotNone(mkt_totals)
        self.assertEqual(mkt_totals.line, Decimal("2.5"))
        self.assertEqual(mkt_totals.selections[CanonicalSelectionType.OVER.value].odds, Decimal("1.90"))
        self.assertEqual(mkt_totals.selections[CanonicalSelectionType.UNDER.value].odds, Decimal("1.95"))

    def test_bookmaker_priority_selection(self):
        """Proves that preferred bookmakers (e.g. Pinnacle) are chosen when multiple are present."""
        raw_payload = [
            {
                "id": "match_01",
                "home_team": "Liverpool",
                "away_team": "Everton",
                "bookmakers": [
                    {
                        "key": "betfair_sb_uk",
                        "title": "Betfair Sportsbook",
                        "markets": [
                            {"key": "h2h", "outcomes": [{"name": "Liverpool", "price": 1.40}, {"name": "Draw", "price": 5.00}, {"name": "Everton", "price": 8.00}]}
                        ],
                    },
                    {
                        "key": "pinnacle",
                        "title": "Pinnacle",
                        "markets": [
                            {"key": "h2h", "outcomes": [{"name": "Liverpool", "price": 1.45}, {"name": "Draw", "price": 5.10}, {"name": "Everton", "price": 8.50}]}
                        ],
                    },
                ],
            }
        ]

        provider = TheOddsApiReferenceProvider(api_key="dummy_key", preferred_bookmakers=["pinnacle", "betfair_sb_uk"])
        parsed = provider._parse_the_odds_api_response(raw_payload)

        self.assertEqual(len(parsed), 1)
        # Should pick Pinnacle
        self.assertEqual(parsed[0].markets[0].bookmaker_name, "pinnacle")
        self.assertEqual(parsed[0].markets[0].selections[CanonicalSelectionType.HOME.value].odds, Decimal("1.45"))


if __name__ == "__main__":
    unittest.main()
