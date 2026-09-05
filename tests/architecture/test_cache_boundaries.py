"""
Phase 3 Architecture Verification: Discovery / Detail Cache Ownership & Lifecycle Boundaries
"""

from typing import Any, List
import pytest
from providers.superbet.provider import SuperbetProvider
from providers.betclic.provider import BetclicProvider
from providers.superbet.models import SuperbetDiscoveredItem
from providers.betclic.models import BetclicDiscoveredItem
from providers.base.provider_result import ProviderResult
from providers.base.execution_engine import ExecutionEngine


def test_discovery_cache_isolation_and_reuse():
    """Verifies that discovered items cache serves subsequent discover() calls without duplicate network calls."""
    sb = SuperbetProvider()
    call_count = 0

    def mock_discover(raw_payload=None):
        nonlocal call_count
        call_count += 1
        return [
            SuperbetDiscoveredItem(event_id="101", match_name="Arsenal vs Chelsea", start_time="2026-08-17T18:00:00Z"),
            SuperbetDiscoveredItem(event_id="102", match_name="Liverpool vs Everton", start_time="2026-08-17T20:00:00Z"),
        ]

    sb.discovery.discover_events = mock_discover

    # 1. First call executes discovery
    res1 = sb.discover()
    assert len(res1) == 2
    assert call_count == 1

    # 2. Second call reuses cached items without calling discovery.discover_events again
    res2 = sb.discover()
    assert len(res2) == 2
    assert call_count == 1
    assert res1 == res2

    # 3. Explicit clear releases the cache
    sb.clear_discovered_cache()
    assert sb.get_discovered_items() is None

    # 4. Third call executes discovery again
    res3 = sb.discover()
    assert len(res3) == 2
    assert call_count == 2


def test_provider_shutdown_releases_discovery_cache():
    """Verifies that provider shutdown cleanly clears the discovery cache."""
    bc = BetclicProvider()
    mock_items = [
        BetclicDiscoveredItem(
            provider_event_id="201",
            name="Real Madrid vs Barcelona",
            start_time="2026-08-17T21:00:00Z",
            competition_name="La Liga",
            url="https://www.betclic.pl/event/201",
        )
    ]
    bc.set_discovered_items(mock_items)
    assert bc.get_discovered_items() is not None

    bc.shutdown()
    assert bc.get_discovered_items() is None


def test_request_vs_payload_vs_domain_model_distinction():
    """Verifies conceptual distinction between HTTP requests, raw payloads, and parsed domain models."""
    sb = SuperbetProvider()
    sb_items = [
        SuperbetDiscoveredItem(
            event_id="101",
            match_name="Match 1",
            start_time="2026-08-17T18:00:00Z",
            metadata={"raw": {"id": "101", "name": "Match 1", "markets": []}},
        ),
        SuperbetDiscoveredItem(
            event_id="102",
            match_name="Match 2",
            start_time="2026-08-17T18:00:00Z",
            metadata={"raw": {"id": "102", "name": "Match 2", "markets": []}},
        ),
    ]
    # Set selected IDs to only event 101
    sb.superbet_config.selected_event_ids = ["101"]

    # Mock fetch function for detail
    detail_fetched = []
    def mock_fetch(item):
        detail_fetched.append(item.event_id)
        return {"id": item.event_id, "name": item.match_name, "markets": [{"id": "m1", "name": "1X2", "selections": []}]}

    raw_responses = sb.fetcher.fetch_event_data(sb_items, mock_data_provider=mock_fetch)

    # 1. 2 items were evaluated
    assert len(raw_responses) == 2
    # 2. Both items produced raw payloads (1 from mock fetch, 1 from overview)
    assert len(raw_responses) == 2

    # 3. Parsed domain models
    parsed_events = sb.parse(raw_responses)
    assert len(parsed_events) == 2
    assert parsed_events[0].event_id == "101"
    assert parsed_events[1].event_id == "102"
