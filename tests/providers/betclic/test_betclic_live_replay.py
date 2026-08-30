"""
Live Fixture Replay Tests for BetclicProvider (Task 032)
"""

from pathlib import Path
import pytest
from providers.base.recording.replay_engine import ReplayEngine
from providers.betclic.parser.parser import BetclicParser
from providers.betclic.validation.validator import BetclicValidator


def test_betclic_live_replay():
    fixture_dir = Path("tests/fixtures/recordings/betclic/live_manifest")
    engine = ReplayEngine(session_dir=fixture_dir, strict=True)

    resp = engine.get_response("https://api.betclic.pl/v2/sportsbook/football/events", "GET")
    assert resp.status_code == 200

    raw_payloads = resp.json()
    assert isinstance(raw_payloads, list)
    assert len(raw_payloads) == 1

    parser = BetclicParser()
    events = parser.parse_payloads(raw_payloads)
    assert len(events) == 1

    ev = events[0]
    assert ev.provider_event_id == "event_betclic_9901"
    assert ev.home_team == "Barcelona"
    assert ev.away_team == "Real Madrid"
    assert len(ev.markets) == 2

    validator = BetclicValidator()
    val_report = validator.validate_events(events)
    assert val_report.is_valid is True
    assert val_report.valid_objects == 1
