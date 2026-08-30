"""
Unit Tests for Odds API.io Provider (Bet365 and Unibet)
"""

import pytest
from providers.base.provider_factory import ProviderFactory
from providers.odds_api.provider import OddsApiProvider
from providers.odds_api.config import OddsApiConfig
from providers.odds_api.models import OddsApiDiscoveredItem, OddsApiEvent


def test_odds_api_provider_factory_instantiation():
    provider = ProviderFactory.create_provider("odds_api")
    assert isinstance(provider, OddsApiProvider)
    assert provider.metadata.name == "odds_api"
    assert provider.metadata.code == "oapi"


def test_odds_api_discovery_parsing():
    config = OddsApiConfig(api_key="test_key")
    provider = OddsApiProvider(config=config)

    mock_raw_events = [
        {
            "id": "ev_101",
            "home": "Arsenal",
            "away": "Chelsea",
            "date": "2026-08-20T19:00:00Z",
            "league": {"name": "Premier League", "slug": "premier-league"},
        },
        {
            "id": "ev_102",
            "home": "Barcelona",
            "away": "Real Madrid",
            "date": "2026-08-21T20:00:00Z",
            "league": {"name": "La Liga", "slug": "la-liga"},
        },
    ]

    provider.set_mock_discovery_payload(mock_raw_events)
    items = provider.discover()
    assert len(items) == 2
    assert items[0].home_team == "Arsenal"
    assert items[0].competition_name == "Premier League"


def test_odds_api_parser_bet365_and_unibet():
    provider = OddsApiProvider(config=OddsApiConfig(api_key="test_key"))

    raw_data = [
        {
            "id": "ev_101",
            "home": "Arsenal",
            "away": "Chelsea",
            "date": "2026-08-20T19:00:00Z",
            "league": {"name": "Premier League"},
            "bookmakers": {
                "Bet365": [
                    {
                        "name": "ML",
                        "odds": [{"home": "2.10", "draw": "3.50", "away": "3.20"}],
                    },
                    {
                        "name": "Both Teams To Score",
                        "odds": [{"yes": "1.75", "no": "2.05"}],
                    },
                    {
                        "name": "Totals",
                        "odds": [
                            {"hdp": 2.5, "over": "1.85", "under": "1.95"},
                            {"hdp": 3.5, "over": "3.10", "under": "1.35"},
                        ],
                    },
                ],
                "Unibet": [
                    {
                        "name": "ML",
                        "odds": [{"home": "2.15", "draw": "3.45", "away": "3.15"}],
                    },
                    {
                        "name": "Double Chance",
                        "odds": [{"1X": "1.33", "12": "1.28", "X2": "1.65"}],
                    },
                ],
            },
        }
    ]

    parsed_events = provider.parse(raw_data)
    assert len(parsed_events) == 2  # One event for Bet365, one for Unibet

    b365_ev = next(e for e in parsed_events if e.bookmaker_name == "bet365")
    unibet_ev = next(e for e in parsed_events if e.bookmaker_name == "unibet")

    assert len(b365_ev.markets) == 4  # ML + BTTS + Totals 2.5 + Totals 3.5
    assert len(unibet_ev.markets) == 2  # ML + Double Chance

    # Validation check
    val_report = provider.validate(parsed_events)
    assert val_report.is_valid
    assert val_report.valid_objects == 2


def test_odds_api_config_event_limit_resolution():
    """Verify event_limit defaults to 50 when None, invalid, or zero, and preserves positive integers."""
    # None -> 50
    cfg_none = OddsApiConfig(event_limit=None)
    assert cfg_none.event_limit == 50

    # Explicit 25 -> 25
    cfg_25 = OddsApiConfig(event_limit=25)
    assert cfg_25.event_limit == 25

    # Invalid values -> 50
    assert OddsApiConfig(event_limit=0).event_limit == 50
    assert OddsApiConfig(event_limit=-10).event_limit == 50
    assert OddsApiConfig(event_limit="invalid").event_limit == 50

    # from_dict parsing
    assert OddsApiConfig.from_dict({"event_limit": None}).event_limit == 50
    assert OddsApiConfig.from_dict({"event_limit": 25}).event_limit == 25
    assert OddsApiConfig.from_dict({"event_limit": 0}).event_limit == 50
    assert OddsApiConfig.from_dict({}).event_limit == 50


def test_odds_api_discovery_url_params_contain_valid_limit():
    """Verify generated discovery parameters contain limit='50' or configured value, never 'None'."""
    from unittest.mock import MagicMock
    from providers.odds_api.discovery.discovery import OddsApiDiscovery, clear_persistent_discovery_cache

    clear_persistent_discovery_cache()

    # Case 1: event_limit=None
    cfg = OddsApiConfig(api_key="test_key_123", event_limit=None)
    discovery = OddsApiDiscovery(config=cfg)

    mock_session = MagicMock()
    mock_resp = MagicMock()
    mock_resp.is_success = True
    mock_resp.status_code = 200
    mock_resp.json.return_value = []
    mock_session.get.return_value = mock_resp
    discovery._session_manager = mock_session

    discovery.discover_events()
    assert mock_session.get.call_count == 1
    call_kwargs = mock_session.get.call_args[1]
    params = call_kwargs.get("params", {})
    assert params.get("limit") == "50"
    assert params.get("limit") != "None"

    # Case 2: explicitly configured limit=25
    clear_persistent_discovery_cache()
    cfg_25 = OddsApiConfig(api_key="test_key_123", event_limit=25)
    discovery_25 = OddsApiDiscovery(config=cfg_25)
    discovery_25._session_manager = mock_session
    discovery_25.discover_events()
    call_kwargs_25 = mock_session.get.call_args[1]
    assert call_kwargs_25.get("params", {}).get("limit") == "25"


def test_odds_api_discovery_http_400_not_retried():
    """Verify HTTP 400 client error is classified as non-retryable and not retried."""
    from unittest.mock import MagicMock
    from providers.odds_api.discovery.discovery import OddsApiDiscovery, clear_persistent_discovery_cache
    from providers.base.execution_engine import ExecutionEngine
    from providers.base.exceptions import NonRetryableError

    clear_persistent_discovery_cache()

    cfg = OddsApiConfig(api_key="test_key_123", max_retries=3)
    provider = OddsApiProvider(config=cfg)

    mock_session = MagicMock()
    mock_resp = MagicMock()
    mock_resp.is_success = False
    mock_resp.status_code = 400
    mock_resp.text.return_value = "Bad Request: Invalid limit"
    mock_session.get.return_value = mock_resp
    provider.discovery._session_manager = mock_session

    engine = ExecutionEngine()
    result = engine.execute(provider)

    # Provider should fail on discovery stage with exactly 1 attempt (0 retries)
    from providers.base.provider_state import ProviderState
    assert result.status == ProviderState.FAILED
    assert mock_session.get.call_count == 1
    assert len(result.errors) > 0
    assert "400" in result.errors[0]

