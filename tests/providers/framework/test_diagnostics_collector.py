"""
Unit Tests for DiagnosticsCollector (Task 018)
"""

from pathlib import Path
import pytest
from providers.base.models import RetryAttempt
from providers.base.observability.diagnostics import DiagnosticsCollector


def test_diagnostics_collector_init():
    dc = DiagnosticsCollector(execution_id="exec_101", provider_name="betclic")
    assert dc.execution_id == "exec_101"
    assert dc._sealed is False


def test_diagnostics_collector_stage_timing():
    dc = DiagnosticsCollector(execution_id="exec_102", provider_name="betclic")
    timing = dc.start_stage("fetch")
    assert timing.stage == "fetch"

    dc.finish_stage(timing, succeeded=True)
    assert len(dc.bundle.stage_timings) == 1
    assert dc.bundle.stage_timings[0].succeeded is True


def test_diagnostics_collector_record_events():
    dc = DiagnosticsCollector(execution_id="exec_103", provider_name="betclic")

    dc.record_console_log(level="error", text="JS syntax error", url="https://example.com/js")
    dc.record_failed_request(url="https://example.com/api", method="GET", error_message="HTTP 500", status_code=500)
    dc.record_retry(RetryAttempt(stage="fetch", attempt_number=1, error_type="FetchError", error_message="Timeout", delay_seconds=1.0))
    dc.warn("Low memory warning")
    dc.error("Page crash error")

    assert len(dc.bundle.console_logs) == 1
    assert len(dc.bundle.failed_requests) == 1
    assert len(dc.bundle.retry_attempts) == 1
    assert len(dc.bundle.warnings) == 1
    assert len(dc.bundle.errors) == 1


def test_diagnostics_collector_seal():
    dc = DiagnosticsCollector(execution_id="exec_104", provider_name="betclic")
    report = dc.seal()

    assert report.execution_id == "exec_104"
    assert dc._sealed is True

    # Post-seal writes must raise RuntimeError
    with pytest.raises(RuntimeError, match="already sealed"):
        dc.warn("Post seal warning")


def test_diagnostics_collector_export_summary():
    dc = DiagnosticsCollector(execution_id="exec_105", provider_name="betclic")
    dc.warn("Warning 1")
    dc.error("Error 1")

    summary = dc.export_summary()
    assert summary["execution_id"] == "exec_105"
    assert summary["provider_name"] == "betclic"
    assert summary["warnings_count"] == 1
    assert summary["errors_count"] == 1
