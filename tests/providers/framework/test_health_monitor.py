"""
HealthMonitor Unit Tests
"""

from providers.base.observability.health_monitor import HealthMonitor
from providers.base.models import QualityReport, QualityStatus


def test_health_monitor_tracks_success_and_failure():
    monitor = HealthMonitor()
    q = QualityReport(
        status=QualityStatus.EXCELLENT,
        execution_id="1",
        provider_name="test_p",
        execution_time_seconds=1.0,
        events_found=10,
        markets_found=20,
        odds_found=40,
        warnings=[],
        errors=[],
        coverage_pct=100.0,
        missing_data_fields=[],
        retry_count=0,
        extraction_strategy_used="NETWORK_RESPONSE",
    )

    monitor.record_success("test_p", 1.0, q)
    snap = monitor.get_snapshot("test_p")
    assert snap.is_healthy is True
    assert snap.total_runs == 1
    assert snap.consecutive_successes == 1

    monitor.record_failure("test_p")
    monitor.record_failure("test_p")
    monitor.record_failure("test_p")

    snap = monitor.get_snapshot("test_p")
    assert snap.is_healthy is False
    assert snap.consecutive_failures == 3
