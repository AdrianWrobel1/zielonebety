"""
Live Fixture Replay Tests for SuperbetProvider (Task 042 / Stage 2.1)
"""

from pathlib import Path
from providers.base.recording.replay_engine import ReplayEngine
from providers.superbet.discovery.discovery import SuperbetDiscovery
from providers.superbet.parser.parser import SuperbetParser
from providers.superbet.validation.validator import SuperbetValidator
from providers.superbet.config import SuperbetConfig


def test_superbet_live_replay():
    fixture_dir = Path("tests/fixtures/recordings/superbet/live_manifest")
    engine = ReplayEngine(session_dir=fixture_dir, strict=True)

    resp = engine.get_response("https://production-superbet-offer-pl.freetls.fastly.net/v3/pl-PL/events", "GET")
    assert resp.status_code == 200

    raw_payload = resp.json()
    assert isinstance(raw_payload, dict)
    assert "events" in raw_payload
    assert len(raw_payload["events"]) > 0

    # Discovery from replayed payload
    config = SuperbetConfig()
    discovery = SuperbetDiscovery(config=config)
    discovered = discovery.discover_events(raw_payload)
    assert len(discovered) == len(raw_payload["events"])
    assert discovered[0].event_id is not None
    assert discovered[0].match_name is not None

    # Parse discovered raw payload items
    raw_events = [d.metadata["raw"] for d in discovered if d.metadata and "raw" in d.metadata]
    parser = SuperbetParser()
    events = parser.parse_payloads(raw_events)
    assert len(events) == len(discovered)

    ev0 = events[0]
    assert ev0.event_id is not None
    assert ev0.name is not None
    assert ev0.home_team is not None
    assert ev0.away_team is not None
    assert len(ev0.markets) > 0

    # Validate parsed domain models
    validator = SuperbetValidator()
    val_report = validator.validate_events(events)
    assert val_report.is_valid is True
    assert val_report.valid_objects == len(events)
    assert val_report.invalid_objects == 0
