"""
Unit Tests for ResponseRecorder & Recording Harness (Task 020)
"""

import json
from pathlib import Path
import pytest
from providers.base.recording.response_recorder import ResponseRecorder


def test_response_recorder_record_payload(tmp_path: Path):
    recorder = ResponseRecorder(fixture_dir=tmp_path)
    data = {"events": [{"id": 1}], "token": "secret_abc"}

    filepath = recorder.record_payload("test_fixture", data)
    assert filepath.exists()

    loaded = json.loads(filepath.read_text(encoding="utf-8"))
    assert loaded["token"] == "[REDACTED]"
    assert loaded["events"] == [{"id": 1}]


def test_response_recorder_record_session(tmp_path: Path):
    recorder = ResponseRecorder(fixture_dir=tmp_path)
    recordings = [
        {
            "url": "https://example.com/api/v1/sports",
            "method": "GET",
            "headers": {"Authorization": "Bearer secret", "Accept": "application/json"},
            "body": {"sports": ["football", "tennis"]},
        }
    ]

    session_dir = recorder.record_session("exec_201", "betclic", recordings)
    manifest_file = session_dir / "manifest.json"
    assert manifest_file.exists()

    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    assert manifest["execution_id"] == "exec_201"
    assert manifest["provider_name"] == "betclic"
    assert manifest["recordings"][0]["headers"]["Authorization"] == "[REDACTED]"

    payload_file = session_dir / manifest["recordings"][0]["payload_file"]
    assert payload_file.exists()


def test_response_recorder_load_payload(tmp_path: Path):
    recorder = ResponseRecorder(fixture_dir=tmp_path)
    recorder.record_payload("saved_data", {"ok": True})

    loaded = recorder.load_payload("saved_data")
    assert loaded == {"ok": True}
