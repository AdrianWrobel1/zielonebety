"""
Betclic Discovery Rebuild Tests (Stage 21A Root-Cause Verification)
Strict limit: exactly 7 targeted tests.
"""

import pytest
from unittest.mock import MagicMock
from providers.betclic.config import BetclicConfig
from providers.betclic.discovery.discovery import BetclicDiscovery
from providers.betclic.discovery.acquisition import BetclicDiscoveryAcquisition
from providers.betclic.discovery.discovery_parser import BetclicDiscoveryParser
from providers.betclic.exceptions import BetclicAccessDeniedError, BetclicDiscoveryError
from providers.betclic.provider import BetclicProvider
from normalization.betclic_normalizer import BetclicNormalizer


SAMPLE_HTML_PAGE = """
<!DOCTYPE html>
<html>
<head>
<script type="application/json">
{
  "page_state": {
    "response": {
      "payload": {
        "matches": [
          {
            "matchId": "1001",
            "name": "Arsenal - Chelsea",
            "matchDateUtc": "2026-08-29T19:00:00Z",
            "competition": {"id": "11", "name": "Premier League"},
            "market": {
              "id": "mkt_1",
              "name": "Wynik meczu",
              "mainSelections": [
                {"id": "s1", "name": "Arsenal", "odds": 2.10, "status": 1},
                {"id": "s2", "name": "Remis", "odds": 3.40, "status": 1},
                {"id": "s3", "name": "Chelsea", "odds": 3.20, "status": 1}
              ]
            }
          }
        ]
      }
    }
  }
}
</script>
</head>
<body>
  <a href="/pilka-nozna-sfootball/anglia-premier-league-c11/arsenal-chelsea-m1001">Arsenal vs Chelsea</a>
</body>
</html>
"""


def test_1_session_and_acquisition_initialization():
    """Test 1: Verifies discovery acquisition initializes and reuses a shared session manager."""
    config = BetclicConfig()
    mock_session = MagicMock()
    mock_session.session = MagicMock()

    discovery = BetclicDiscovery(config=config, session_manager=mock_session)
    assert discovery._session_manager is mock_session
    assert discovery.acquisition.session_manager is mock_session


def test_2_discovery_403_access_denied_classification_and_bailout():
    """Test 2: Verifies 403 triggers BetclicAccessDeniedError immediately without continuing across all URLs."""
    config = BetclicConfig(discovery_urls=["https://url1.com", "https://url2.com", "https://url3.com"], discovery_workers=1)
    mock_session = MagicMock()

    mock_resp = MagicMock()
    mock_resp.is_success = False
    mock_resp.status_code = 403
    mock_session.get.return_value = mock_resp

    discovery = BetclicDiscovery(config=config, session_manager=mock_session)

    with pytest.raises(BetclicAccessDeniedError):
        discovery.fetch_discovery_payload()

    # Fast bailout verified: only 1 URL attempted instead of hitting all 3
    assert mock_session.get.call_count == 1
    assert discovery.acquisition.last_diagnostics["last_error_reason"] == "ACCESS_DENIED"


def test_3_discovery_parser_pure_extraction():
    """Test 3: Pure parser extracts match records and relative URL mapping correctly."""
    matches = BetclicDiscoveryParser.parse_html_page(SAMPLE_HTML_PAGE)
    assert len(matches) == 1
    assert matches[0]["matchId"] == "1001"
    assert matches[0]["relative_url"] == "/pilka-nozna-sfootball/anglia-premier-league-c11/arsenal-chelsea-m1001"


def test_4_discovery_success_path_fixture():
    """Test 4: Full discovery pipeline succeeds on fixture response and produces BetclicDiscoveredItem."""
    config = BetclicConfig(discovery_urls=["https://www.betclic.pl/pilka-nozna-sfootball"])
    mock_session = MagicMock()
    mock_resp = MagicMock()
    mock_resp.is_success = True
    mock_resp.status_code = 200
    mock_resp.text.return_value = SAMPLE_HTML_PAGE
    mock_session.get.return_value = mock_resp

    discovery = BetclicDiscovery(config=config, session_manager=mock_session)
    items = discovery.discover_events()

    assert len(items) == 1
    assert items[0].provider_event_id == "1001"
    assert items[0].name == "Arsenal - Chelsea"
    assert items[0].competition_name == "Premier League"
    assert discovery.stats["events_discovered"] == 1
    assert discovery.stats["events_parsed"] == 1


def test_5_discovery_preserves_event_models():
    """Test 5: Discovery items match required fields for downstream fetcher/parser."""
    config = BetclicConfig()
    discovery = BetclicDiscovery(config=config)
    mock_payload = [{
        "id": "999",
        "name": "Liverpool - Everton",
        "competition": {"name": "Premier League"},
        "start_date": "2026-08-25T15:00:00Z"
    }]
    items = discovery.discover_events(mock_payload)
    assert len(items) == 1
    item = items[0]
    assert item.provider_event_id == "999"
    assert item.name == "Liverpool - Everton"
    assert item.competition_name == "Premier League"
    assert item.url.endswith("/match-m999")


def test_6_provider_failure_does_not_fake_data():
    """Test 6: Provider error raises properly and does not pretend fresh data was discovered."""
    config = BetclicConfig(discovery_urls=["https://url1.com"])
    mock_session = MagicMock()
    mock_resp = MagicMock()
    mock_resp.is_success = False
    mock_resp.status_code = 500
    mock_session.get.return_value = mock_resp

    discovery = BetclicDiscovery(config=config, session_manager=mock_session)
    with pytest.raises(BetclicDiscoveryError):
        discovery.discover_events()

    assert discovery.stats["events_discovered"] == 0


def test_7_integration_discovery_to_canonical_events():
    """Test 7: Full integration from Discovery -> Provider -> Normalizer -> Canonical Graph."""
    provider = BetclicProvider()
    mock_discovery_item = {
        "id": "1001",
        "name": "Arsenal - Chelsea",
        "matchDateUtc": "2026-08-25T19:00:00Z",
        "competition": {"id": "11", "name": "Premier League"},
        "market": {
            "id": "mkt_1",
            "name": "Wynik meczu",
            "mainSelections": [
                {"id": "s1", "name": "Arsenal", "odds": 2.10, "status": 1},
                {"id": "s2", "name": "Remis", "odds": 3.40, "status": 1},
                {"id": "s3", "name": "Chelsea", "odds": 3.20, "status": 1}
            ]
        }
    }
    provider.set_mock_discovery_payload([mock_discovery_item])
    discovered = provider.discover()
    assert len(discovered) == 1

    raw = provider.fetch(discovered)
    parsed = provider.parse(raw)
    validation = provider.validate(parsed)
    assert validation.is_valid

    normalizer = BetclicNormalizer()
    graph = normalizer.normalize_event(parsed[0])

    assert graph.event.home_participant == "Arsenal"
    assert graph.event.away_participant == "Chelsea"
    assert len(graph.markets) >= 1
    assert len(graph.odds_list) == 3
