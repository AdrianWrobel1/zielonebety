"""
Unit Tests for BetclicValidator (Task 030)
"""

import pytest
from providers.betclic.models import (
    BetclicEvent,
    BetclicMarket,
    BetclicSelection,
    BetclicOdds,
)
from providers.betclic.validation.validator import BetclicValidator


def test_betclic_validator_valid_event():
    validator = BetclicValidator()
    event = BetclicEvent(
        provider_event_id="ev_101",
        name="Real Madrid vs Barcelona",
        competition_name="La Liga",
        markets=[
            BetclicMarket(
                provider_market_id="m_1",
                name="Match Result",
                market_type_code="1X2",
                is_open=True,
                selections=[
                    BetclicSelection(
                        provider_selection_id="s_1",
                        name="Real Madrid",
                        type_code="1",
                        odds=BetclicOdds(provider_odds_id="o_1", decimal_odds=2.10)
                    )
                ]
            )
        ]
    )

    report = validator.validate_events([event])
    assert report.is_valid is True
    assert report.valid_objects == 1
    assert report.invalid_objects == 0


def test_betclic_validator_invalid_odds():
    validator = BetclicValidator()
    event = BetclicEvent(
        provider_event_id="ev_102",
        name="Invalid Odds Match",
        competition_name="La Liga",
        markets=[
            BetclicMarket(
                provider_market_id="m_2",
                name="Match Result",
                market_type_code="1X2",
                is_open=True,
                selections=[
                    BetclicSelection(
                        provider_selection_id="s_2",
                        name="Team",
                        type_code="1",
                        odds=BetclicOdds(provider_odds_id="o_2", decimal_odds=0.5)  # Invalid <= 1.0
                    )
                ]
            )
        ]
    )

    report = validator.validate_events([event])
    assert report.is_valid is False
    assert report.invalid_objects == 1
    assert "Invalid decimal odds" in report.rejection_reasons[0]["reasons"][0]


