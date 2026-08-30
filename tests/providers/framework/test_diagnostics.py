"""
DiagnosticsCollector Unit Tests
"""

from providers.base.observability.diagnostics import DiagnosticsCollector


def test_diagnostics_collector():
    collector = DiagnosticsCollector(execution_id="exec123", provider_name="test_p")
    stage_t = collector.start_stage("discovery")
    collector.warn("Low count warning")
    collector.finish_stage(stage_t, succeeded=True)

    report = collector.seal()
    assert report.execution_id == "exec123"
    assert report.provider_name == "test_p"
    assert len(report.warnings) == 1
    assert report.warnings[0] == "Low count warning"
    assert len(report.stage_events) == 2
