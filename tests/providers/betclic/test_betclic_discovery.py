"""
Unit Tests for BetclicDiscovery (Task 027)
"""

import pytest
from providers.betclic.config import BetclicConfig
from providers.betclic.discovery.discovery import BetclicDiscovery
from providers.betclic.exceptions import BetclicDiscoveryError


def test_betclic_discovery_success():
    config = BetclicConfig()
    discovery = BetclicDiscovery(config)

    raw_payload = [
        {
            "name": "Ekstraklasa",
            "events": [
                {"id": "ev_101", "name": "Legia vs Lech", "start_date": "2026-08-10T18:00:00Z"},
                {"id": "ev_102", "name": "Wisla vs Cracovia", "start_date": "2026-08-10T20:00:00Z"},
                {"id": "ev_101", "name": "Legia vs Lech (Dupe)", "start_date": "2026-08-10T18:00:00Z"},  # Duplicate
            ]
        }
    ]

    items = discovery.discover_events(raw_payload)
    assert len(items) == 2
    assert items[0].provider_event_id == "ev_101"
    assert items[0].name == "Legia vs Lech"
    assert items[0].competition_name == "Ekstraklasa"
    assert items[1].provider_event_id == "ev_102"


def test_betclic_discovery_invalid_payload():
    config = BetclicConfig()
    discovery = BetclicDiscovery(config)

    with pytest.raises(BetclicDiscoveryError, match="must be a list"):
        discovery.discover_events("invalid string payload")


def test_betclic_discovery_filtering():
    config = BetclicConfig()
    discovery = BetclicDiscovery(config)

    raw_payload = [
        {
            "name": "Premier League",
            "events": [
                {"id": "", "name": "No ID Match"},
                {"id": "ev_201", "name": ""},  # No name
                {"id": "ev_202", "name": "Arsenal vs Chelsea"},
            ]
        }
    ]

    items = discovery.discover_events(raw_payload)
    assert len(items) == 1
    assert items[0].provider_event_id == "ev_202"
