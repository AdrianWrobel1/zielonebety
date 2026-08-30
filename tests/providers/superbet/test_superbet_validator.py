"""
Unit Tests for Superbet Validator Module (Task 039 / Stage 2.1)
"""

from providers.superbet.models import (
    SuperbetEvent,
    SuperbetMarket,
    SuperbetSelection,
    SuperbetOdds,
)
from providers.superbet.validation.validator import SuperbetValidator


def test_superbet_validator_valid_event():
    validator = SuperbetValidator()

    odds = SuperbetOdds(decimal_odds=1.85)
    sel = SuperbetSelection(selection_id="s1", name="Home", odds=odds)
    mkt = SuperbetMarket(market_id="m1", name="1X2", selections=[sel])
    event = SuperbetEvent(
        event_id="ev_valid",
        name="Team A vs Team B",
        home_team="Team A",
        away_team="Team B",
        markets=[mkt],
    )

    report = validator.validate_events([event])
    assert report.is_valid is True
    assert report.total_objects == 1
    assert report.valid_objects == 1
    assert report.invalid_objects == 0


def test_superbet_validator_invalid_odds():
    validator = SuperbetValidator()

    odds = SuperbetOdds(decimal_odds=0.95)  # Invalid <= 1.0
    sel = SuperbetSelection(selection_id="s1", name="Home", odds=odds)
    mkt = SuperbetMarket(market_id="m1", name="1X2", selections=[sel])
    event = SuperbetEvent(
        event_id="ev_invalid",
        name="Team A vs Team B",
        home_team="Team A",
        away_team="Team B",
        markets=[mkt],
    )

    report = validator.validate_events([event])
    assert report.is_valid is False
    assert report.invalid_objects == 1
    assert "Invalid decimal odds" in report.rejection_reasons[0]["reasons"][0]


def test_superbet_validator_missing_ids_and_names():
    validator = SuperbetValidator()

    odds = SuperbetOdds(decimal_odds=2.0)
    sel_no_id = SuperbetSelection(selection_id="", name="", odds=odds)
    mkt_no_id = SuperbetMarket(market_id="", name="1X2", selections=[sel_no_id])
    event = SuperbetEvent(
        event_id="",
        name="",
        home_team="",
        away_team="",
        markets=[mkt_no_id],
    )

    report = validator.validate_events([event])
    assert report.is_valid is False
    assert report.invalid_objects == 1
    reasons = report.rejection_reasons[0]["reasons"]
    assert "Missing event_id" in reasons
    assert "Missing event name" in reasons
    assert "Missing home_team" in reasons
    assert "Missing away_team" in reasons
