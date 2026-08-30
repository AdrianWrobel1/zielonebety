"""
Unit Tests for Superbet Config and Models (Task 035)
"""

from providers.superbet.config import SuperbetConfig
from providers.superbet.constants import SUPERBET_PROVIDER_NAME, SUPERBET_PROVIDER_CODE
from providers.superbet.models import (
    SuperbetDiscoveredItem,
    SuperbetEvent,
    SuperbetMarket,
    SuperbetSelection,
    SuperbetOdds,
)
from providers.superbet.exceptions import SuperbetError, SuperbetParsingError


def test_superbet_config_defaults():
    config = SuperbetConfig()
    assert config.base_url == "https://production-superbet-offer-pl.freetls.fastly.net/v3/pl-PL"
    assert config.sport_id == 5
    assert config.active_index == "active-prematch"
    assert config.hours_ahead == 168
    assert config.request_timeout == 15.0
    assert config.max_retries == 3
    assert "User-Agent" in config.headers
    assert config.rate_limit_per_sec == 5.0


def test_superbet_constants():
    assert SUPERBET_PROVIDER_NAME == "superbet"
    assert SUPERBET_PROVIDER_CODE == "supr"


def test_superbet_domain_models():
    item = SuperbetDiscoveredItem(event_id="sb100", match_name="Legia vs Lech")
    assert item.event_id == "sb100"
    assert item.match_name == "Legia vs Lech"

    odds = SuperbetOdds(decimal_odds=2.15)
    sel = SuperbetSelection(selection_id="s1", name="Legia", odds=odds)
    mkt = SuperbetMarket(market_id="m1", name="1X2", selections=[sel])
    event = SuperbetEvent(
        event_id="sb100",
        name="Legia vs Lech",
        home_team="Legia Warszawa",
        away_team="Lech Poznan",
        markets=[mkt],
    )

    assert event.event_id == "sb100"
    assert event.markets[0].selections[0].odds.decimal_odds == 2.15


def test_superbet_exceptions():
    err = SuperbetParsingError("Missing field")
    assert isinstance(err, SuperbetError)
    assert str(err) == "Missing field"
