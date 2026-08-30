"""
Unit Tests for BetclicFetcher (Task 028)
"""

import pytest
from providers.betclic.config import BetclicConfig
from providers.betclic.fetch.fetcher import BetclicFetcher
from providers.betclic.models import BetclicDiscoveredItem
from providers.betclic.exceptions import BetclicFetchError


def test_betclic_fetcher_with_mock_provider():
    config = BetclicConfig()
    fetcher = BetclicFetcher(config)

    items = [
        BetclicDiscoveredItem(
            provider_event_id="ev_101",
            name="Legia vs Lech",
            competition_name="Ekstraklasa",
            url="https://api.betclic.pl/v2/events/ev_101",
            start_time="2026-08-10T18:00:00Z"
        )
    ]

    def mock_provider(item):
        return {"id": item.provider_event_id, "fetched": True, "markets": [{"id": "m1"}]}

    responses = fetcher.fetch_event_data(items, mock_data_provider=mock_provider)
    assert len(responses) == 1
    assert responses[0]["id"] == "ev_101"
    assert responses[0]["fetched"] is True


def test_betclic_fetcher_exception_handling():
    config = BetclicConfig()
    fetcher = BetclicFetcher(config)

    items = [
        BetclicDiscoveredItem(
            provider_event_id="ev_err",
            name="Error Event",
            competition_name="Ekstraklasa",
            url="https://api.betclic.pl/v2/events/ev_err",
            start_time="2026-08-10T18:00:00Z"
        )
    ]

    def failing_provider(item):
        raise ConnectionError("Network dropped")

    with pytest.raises(BetclicFetchError, match="Failed to fetch.*event"):
        fetcher.fetch_event_data(items, mock_data_provider=failing_provider)
