"""
Unit Tests for Superbet Fetcher Module (Task 037 / Stage 2.1)
"""

from unittest.mock import MagicMock
import pytest
from providers.superbet.config import SuperbetConfig
from providers.superbet.models import SuperbetDiscoveredItem
from providers.superbet.fetch.fetcher import SuperbetFetcher
from providers.superbet.exceptions import SuperbetFetchError
from providers.base.response_interceptor import InterceptedResponse


def test_superbet_fetcher_with_mock_provider():
    config = SuperbetConfig()
    fetcher = SuperbetFetcher(config=config)

    items = [
        SuperbetDiscoveredItem(event_id="ev_101", match_name="Team A vs Team B", competition_name="Comp A")
    ]

    def mock_provider(item):
        return {
            "eventId": item.event_id,
            "matchName": item.match_name,
            "competitionName": item.competition_name,
            "odds": [{"marketName": "1X2", "selectionName": "Team A", "price": 1.95}],
        }

    responses = fetcher.fetch_event_data(items, mock_data_provider=mock_provider)
    assert len(responses) == 1
    assert responses[0]["eventId"] == "ev_101"
    assert responses[0]["odds"][0]["price"] == 1.95


def test_superbet_fetcher_with_raw_metadata():
    config = SuperbetConfig()
    fetcher = SuperbetFetcher(config=config)

    raw_event = {
        "event_id": 9999,
        "fixture": {"event_name": "Team X·Team Y"},
        "markets": [{"id": 1, "name": "Mecz", "odds": []}],
    }
    items = [
        SuperbetDiscoveredItem(
            event_id="9999",
            match_name="Team X·Team Y",
            metadata={"raw": raw_event},
        )
    ]

    responses = fetcher.fetch_event_data(items)
    assert len(responses) == 1
    assert responses[0]["event_id"] == 9999
    assert responses[0]["fixture"]["event_name"] == "Team X·Team Y"


def test_superbet_fetcher_single_event_http():
    mock_session = MagicMock()
    mock_session.get.return_value = InterceptedResponse(
        url="https://production-superbet-offer-pl.freetls.fastly.net/v3/pl-PL/events/123",
        status_code=200,
        body=b'{"event_id": 123, "fixture": {"event_name": "A vs B"}}',
    )

    config = SuperbetConfig(max_retries=0)
    fetcher = SuperbetFetcher(config=config, session_manager=mock_session)

    items = [SuperbetDiscoveredItem(event_id="123", match_name="A vs B")]
    responses = fetcher.fetch_event_data(items)

    assert len(responses) == 1
    assert responses[0]["event_id"] == 123


def test_superbet_fetcher_http_failure():
    mock_session = MagicMock()
    mock_session.get.return_value = InterceptedResponse(
        url="https://production-superbet-offer-pl.freetls.fastly.net/v3/pl-PL/events/err_01",
        status_code=500,
        body=b"Internal Error",
    )

    config = SuperbetConfig(max_retries=0)
    fetcher = SuperbetFetcher(config=config, session_manager=mock_session)

    items = [SuperbetDiscoveredItem(event_id="err_01", match_name="Faulty Match")]

    with pytest.raises(SuperbetFetchError, match="Superbet HTTP fetch failed with status 500"):
        fetcher.fetch_event_data(items)


def test_superbet_fetcher_exception_handling():
    config = SuperbetConfig()
    fetcher = SuperbetFetcher(config=config)

    items = [SuperbetDiscoveredItem(event_id="err_01", match_name="Faulty Match")]

    def faulty_provider(item):
        raise RuntimeError("Connection dropped")

    with pytest.raises(SuperbetFetchError, match="Failed to fetch Superbet event 'err_01'"):
        fetcher.fetch_event_data(items, mock_data_provider=faulty_provider)
