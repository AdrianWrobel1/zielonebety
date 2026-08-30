"""
Tier 2 Full Market Detail Replay Test for Superbet (Stage 2.2)
"""

from pathlib import Path
from providers.base.recording.replay_engine import ReplayEngine
from providers.superbet.parser.parser import SuperbetParser
from providers.superbet.validation.validator import SuperbetValidator


def test_superbet_detail_live_replay():
    fixture_dir = Path("tests/fixtures/recordings/superbet/detail_manifest")
    engine = ReplayEngine(session_dir=fixture_dir, strict=True)

    resp = engine.get_response("https://production-superbet-offer-pl.freetls.fastly.net/v2/pl-PL/events/13222121", "GET")
    assert resp.status_code == 200

    raw_payload = resp.json()
    assert isinstance(raw_payload, dict)
    assert "data" in raw_payload
    assert len(raw_payload["data"]) == 1

    parser = SuperbetParser()
    events = parser.parse_payloads([raw_payload])
    assert len(events) == 1

    ev = events[0]
    assert ev.event_id == "13222121"
    assert ev.home_team == "Chicago Fire"
    assert ev.away_team == "Portland Timbers"
    assert ev.betradar_id is not None
    assert len(ev.markets) > 100, f"Expected > 100 markets, got {len(ev.markets)}"

    # Check distinct market instances (e.g. Over 1.5 vs Over 2.5)
    total_selections = sum(len(m.selections) for m in ev.markets)
    assert total_selections > 500, f"Expected > 500 selections, got {total_selections}"

    # Validate
    validator = SuperbetValidator()
    val_report = validator.validate_events(events)
    assert val_report.is_valid is True
    assert val_report.valid_objects == 1
    assert val_report.invalid_objects == 0
