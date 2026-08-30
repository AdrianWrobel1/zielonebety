"""
Unit Tests for RateLimiter on Superbet Tier 2 Fetcher Path (Stage 2.2)
"""

from unittest.mock import MagicMock
from providers.superbet.config import SuperbetConfig, EventSelectionMode
from providers.superbet.models import SuperbetDiscoveredItem
from providers.superbet.fetch.fetcher import SuperbetFetcher
from providers.base.response_interceptor import InterceptedResponse


def test_rate_limiter_invoked_on_detail_fetch():
    mock_session = MagicMock()
    mock_session.get.return_value = InterceptedResponse(
        url="https://production-superbet-offer-pl.freetls.fastly.net/v2/pl-PL/events/101",
        status_code=200,
        body=b'{"data": [{"eventId": 101, "matchName": "Team A vs Team B", "odds": []}]}',
    )

    mock_rate_limiter = MagicMock()
    mock_rate_limiter.acquire.return_value = 0.0

    config = SuperbetConfig(
        selection_mode=EventSelectionMode.SELECTED.value,
        selected_event_ids=["101"],
    )

    fetcher = SuperbetFetcher(
        config=config,
        session_manager=mock_session,
        rate_limiter=mock_rate_limiter,
    )

    items = [
        SuperbetDiscoveredItem(event_id="101", match_name="Team A vs Team B", metadata={"raw": {"event_id": 101}})
    ]

    responses = fetcher.fetch_event_data(items)
    assert len(responses) == 1

    # Verify that RateLimiter.acquire(1) was called before session_manager.get
    assert mock_rate_limiter.acquire.called
    assert mock_rate_limiter.acquire.call_count == 1
    mock_rate_limiter.acquire.assert_called_with(1)
    assert mock_session.get.called


def test_rate_limiter_not_invoked_for_overview_only():
    mock_session = MagicMock()
    mock_rate_limiter = MagicMock()

    config = SuperbetConfig(selection_mode=EventSelectionMode.OVERVIEW_ONLY.value)
    fetcher = SuperbetFetcher(
        config=config,
        session_manager=mock_session,
        rate_limiter=mock_rate_limiter,
    )

    items = [
        SuperbetDiscoveredItem(event_id="101", match_name="Team A vs Team B", metadata={"raw": {"event_id": 101, "fixture": {"event_name": "Team A vs Team B"}}})
    ]

    responses = fetcher.fetch_event_data(items)
    assert len(responses) == 1
    # When overview payload is already present in metadata, no HTTP call and no rate limiter acquire needed
    assert not mock_rate_limiter.acquire.called
    assert not mock_session.get.called
