"""
Unit Tests for ReplayEngine (Task 021)
"""

from pathlib import Path
import pytest
from providers.base.exceptions import ReplayNotFoundError
from providers.base.recording.response_recorder import ResponseRecorder
from providers.base.recording.replay_engine import ReplayEngine


def test_replay_engine_success(tmp_path: Path):
    recorder = ResponseRecorder(fixture_dir=tmp_path)
    recordings = [
        {
            "url": "https://example.com/api/v1/events",
            "method": "GET",
            "headers": {"Content-Type": "application/json"},
            "body": [{"id": "ev_1", "name": "Team A vs Team B"}],
        }
    ]

    session_dir = recorder.record_session("exec_301", "betclic", recordings)
    engine = ReplayEngine(session_dir=session_dir, strict=True)

    resp = engine.get_response("https://example.com/api/v1/events", "GET")
    assert resp.status_code == 200
    assert resp.url == "https://example.com/api/v1/events"

    data = resp.json()
    assert data[0]["id"] == "ev_1"


def test_replay_engine_strict_missing_url(tmp_path: Path):
    recorder = ResponseRecorder(fixture_dir=tmp_path)
    session_dir = recorder.record_session("exec_302", "betclic", [])
    engine = ReplayEngine(session_dir=session_dir, strict=True)

    with pytest.raises(ReplayNotFoundError, match="No recorded response found"):
        engine.get_response("https://example.com/missing", "GET")


def test_replay_engine_non_strict_fallback(tmp_path: Path):
    recorder = ResponseRecorder(fixture_dir=tmp_path)
    session_dir = recorder.record_session("exec_303", "betclic", [])
    engine = ReplayEngine(session_dir=session_dir, strict=False)

    resp = engine.get_response("https://example.com/missing", "GET")
    assert resp.status_code == 404
