"""
Unit Tests for Superbet Discovery Module (Task 036 / Stage 2.1)
"""

from unittest.mock import MagicMock
import pytest
from providers.superbet.config import SuperbetConfig
from providers.superbet.discovery.discovery import SuperbetDiscovery
from providers.superbet.exceptions import SuperbetDiscoveryError
from providers.base.response_interceptor import InterceptedResponse


def test_superbet_discovery_success_legacy():
    config = SuperbetConfig()
    discovery = SuperbetDiscovery(config=config)

    raw_payload = [
        {
            "competitionId": "comp_101",
            "competitionName": "Ekstraklasa",
            "events": [
                {
                    "eventId": "ev_001",
                    "matchName": "Legia Warszawa vs Lech Poznan",
                    "matchDate": "2026-08-10T18:00:00Z",
                },
                {
                    "eventId": "ev_002",
                    "matchName": "Rakow Czestochowa vs Pogon Szczecin",
                    "matchDate": "2026-08-10T20:30:00Z",
                },
            ],
        }
    ]

    discovered = discovery.discover_events(raw_payload)
    assert len(discovered) == 2
    assert discovered[0].event_id == "ev_001"
    assert discovered[0].match_name == "Legia Warszawa vs Lech Poznan"
    assert discovered[0].competition_name == "Ekstraklasa"
    assert discovered[1].event_id == "ev_002"


def test_superbet_discovery_success_native_fastly():
    config = SuperbetConfig()
    discovery = SuperbetDiscovery(config=config)

    raw_payload = {
        "events": [
            {
                "event_id": 13207040,
                "fixture": {
                    "event_name": "America MG·Athletic Club MG",
                    "utc_date": "2026-08-16T21:30:00Z",
                    "tournament_id": 1697,
                },
                "markets": [
                    {
                        "id": 547,
                        "name": "Mecz",
                        "odds": [
                            {"uuid": "uuid-1", "price": 2.35, "status": 1, "metadata": {"name": "1"}}
                        ],
                    }
                ],
            }
        ]
    }

    discovered = discovery.discover_events(raw_payload)
    assert len(discovered) == 1
    assert discovered[0].event_id == "13207040"
    assert discovered[0].match_name == "America MG·Athletic Club MG"
    assert discovered[0].start_time == "2026-08-16T21:30:00Z"
    assert discovered[0].competition_id == "1697"
    assert "raw" in discovered[0].metadata


def test_superbet_discovery_deduplication():
    config = SuperbetConfig()
    discovery = SuperbetDiscovery(config=config)

    raw_payload = [
        {
            "competitionName": "League A",
            "events": [
                {"eventId": "dup_01", "matchName": "Team A vs Team B"},
                {"eventId": "dup_01", "matchName": "Team A vs Team B"},  # Duplicate
            ],
        }
    ]

    discovered = discovery.discover_events(raw_payload)
    assert len(discovered) == 1
    assert discovered[0].event_id == "dup_01"


def test_superbet_discovery_invalid_payload_type():
    config = SuperbetConfig()
    discovery = SuperbetDiscovery(config=config)

    with pytest.raises(SuperbetDiscoveryError, match="Raw discovery payload must be a list or dict"):
        discovery.discover_events(12345)  # Invalid type integer


def test_superbet_discovery_http_failure():
    mock_session = MagicMock()
    mock_session.get.return_value = InterceptedResponse(
        url="https://production-superbet-offer-pl.freetls.fastly.net/v3/pl-PL/events",
        status_code=500,
        body=b"Server Error",
    )

    config = SuperbetConfig(max_retries=0)
    discovery = SuperbetDiscovery(config=config, session_manager=mock_session)

    with pytest.raises(SuperbetDiscoveryError, match="Superbet discovery HTTP request failed"):
        discovery.discover_events()
