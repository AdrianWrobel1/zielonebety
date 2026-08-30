"""
Unit Tests for BetclicParser (Task 029)
"""

import pytest
from providers.betclic.parser.parser import BetclicParser
from providers.betclic.exceptions import BetclicParsingError


def test_betclic_parser_success():
    parser = BetclicParser()
    raw_payloads = [
        {
            "id": "1001",
            "name": "Arsenal vs Chelsea",
            "competition": "Premier League",
            "home_team": "Arsenal",
            "away_team": "Chelsea",
            "start_date": "2026-08-15T15:00:00Z",
            "markets": [
                {
                    "id": "m_1",
                    "name": "Match Result",
                    "code": "1X2",
                    "is_open": True,
                    "selections": [
                        {"id": "s_1", "name": "Arsenal", "code": "1", "odds": 1.85},
                        {"id": "s_2", "name": "Draw", "code": "X", "odds": 3.60},
                        {"id": "s_3", "name": "Chelsea", "code": "2", "odds": 4.20},
                    ]
                }
            ]
        }
    ]

    events = parser.parse_payloads(raw_payloads)
    assert len(events) == 1

    ev = events[0]
    assert ev.provider_event_id == "1001"
    assert ev.home_team == "Arsenal"
    assert ev.away_team == "Chelsea"
    assert len(ev.markets) == 1

    m = ev.markets[0]
    assert m.name == "Match Result"
    assert len(m.selections) == 3

    assert m.selections[0].odds.decimal_odds == 1.85


def test_betclic_parser_missing_mandatory_fields():
    parser = BetclicParser()
    raw_payloads = [{"id": "", "name": "Missing ID Event"}]

    with pytest.raises(BetclicParsingError, match="Missing mandatory event fields"):
        parser.parse_payloads(raw_payloads)
