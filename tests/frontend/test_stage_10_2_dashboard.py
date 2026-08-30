"""
Stage 10.2 — Dashboard Production Integration Tests

Focused test suite verifying that the dashboard is driven by real backend data
and that no fake/mock/hardcoded values leak into the production UI.
"""

import json
import os
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Dict, Any, List, Optional
from unittest.mock import MagicMock, patch

import pytest

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from normalization.selection_identity import CanonicalSelectionKey
from normalization.market_identity import CanonicalMarketKey

from orchestration.models import (
    ScanCycleResult,
    CycleStatus,
    StageTiming,
    ResourceMetrics,
    ScanConfig,
)
from api.services import (
    PlatformAPIService,
    _serialize_scan_cycle_result,
    serialize_opportunity_summary,
)


# ────────────────────────────────────────────────────────────
# Fixtures
# ────────────────────────────────────────────────────────────

def _make_provider_result(name: str, status: str = "COMPLETED", discovered: int = 30, parsed: int = 15,
                          duration: float = 1.5, errors: list = None, warnings: list = None):
    """Create a mock provider result object with the expected interface."""
    result = MagicMock()
    result.provider_name = name
    result.status = MagicMock()
    result.status.name = status
    result.status.value = status
    result.execution_duration = duration
    result.discovered_objects = [MagicMock()] * discovered
    result.parsed_objects = [MagicMock()] * parsed
    result.errors = errors or []
    result.warnings = warnings or []
    return result


def _make_scan_cycle_result(
    status: CycleStatus = CycleStatus.SUCCESS,
    discovered_events: int = 80,
    selected_events: int = 20,
    matched_events: int = 10,
    markets_evaluated: int = 40,
    detected_opportunities: int = 0,
    valuebets_qualified: int = 0,
    duration: float = 5.0,
    providers: dict = None,
    errors: list = None,
    warnings: list = None,
    nearest_opportunity: dict = None,
    detection_result=None,
    valuebet_result=None,
) -> ScanCycleResult:
    """Build a ScanCycleResult with realistic field values for testing."""
    result = ScanCycleResult(
        execution_id=f"scan_{uuid.uuid4().hex[:12]}",
        cycle_status=status,
        started_at=datetime.now(timezone.utc).isoformat(),
        completed_at=datetime.now(timezone.utc).isoformat(),
        duration_seconds=duration,
        stage_timings=StageTiming(
            acquisition_seconds=2.0,
            normalization_seconds=0.5,
            matching_seconds=0.3,
            detection_seconds=0.2,
            lifecycle_seconds=0.1,
            dispatch_seconds=0.05,
            reconciliation_seconds=0.0,
            total_duration_seconds=duration,
        ),
        resource_metrics=ResourceMetrics(
            total_http_requests=25,
            detail_http_requests=20,
            events_discovered=discovered_events,
            events_selected=selected_events,
            events_parsed=selected_events,
            normalized_graphs=selected_events,
            matched_events=matched_events,
            markets_evaluated=markets_evaluated,
            selections_evaluated=markets_evaluated * 3,
            peak_memory_mb=35.0,
        ),
        errors=errors or [],
        warnings=warnings or [],
    )

    # Set count fields
    result.discovered_events_count = discovered_events
    result.parsed_events_count = selected_events
    result.normalized_graphs_count = selected_events
    result.normalization_failed_count = 0
    result.matched_events_count = matched_events
    result.unmatched_events_count = selected_events - matched_events
    result.markets_evaluated_count = markets_evaluated
    result.detected_opportunities_count = detected_opportunities
    result.new_opportunities_count = detected_opportunities
    result.updated_opportunities_count = 0
    result.suppressed_opportunities_count = 0
    result.expired_opportunities_count = 0
    result.dispatched_count = detected_opportunities
    result.delivered_count = detected_opportunities
    result.failed_delivery_count = 0
    result.skipped_delivery_count = 0

    # Provider results
    if providers:
        result.provider_results = providers
    else:
        result.provider_results = {
            "superbet": _make_provider_result("superbet", "COMPLETED", 40, 20, 1.2),
            "betclic": _make_provider_result("betclic", "COMPLETED", 40, 15, 1.0),
        }

    # Detection + nearest
    result.detection_result = detection_result
    result.valuebet_result = valuebet_result
    result.nearest_opportunity = nearest_opportunity
    result.diagnostics = {}
    if nearest_opportunity:
        result.diagnostics["nearest_opportunity"] = nearest_opportunity

    return result


# ────────────────────────────────────────────────────────────
# Test 1: Initial NOT_RUN state
# ────────────────────────────────────────────────────────────

class TestInitialNotRunState:
    """Verify that when no scan has run, the API returns appropriate NOT_RUN indicators."""

    def test_scan_status_not_run(self):
        """get_scan_status should show NOT_RUN when no scan has been executed."""
        with patch.object(PlatformAPIService, '__init__', lambda self, **kw: None):
            svc = PlatformAPIService.__new__(PlatformAPIService)
            svc._last_scan_result = None
            svc._scan_history = []
            svc._is_scanning = False
            svc._scanner_status = "READY"

            status = svc.get_scan_status()
            assert status["has_run"] is False
            assert status["last_cycle_status"] == "NOT_RUN"
            assert status["last_scan_id"] is None
            assert status["last_scan_time"] is None

    def test_latest_scan_none_when_not_run(self):
        """get_latest_scan should return None when no scan has been executed."""
        with patch.object(PlatformAPIService, '__init__', lambda self, **kw: None):
            svc = PlatformAPIService.__new__(PlatformAPIService)
            svc._last_scan_result = None

            assert svc.get_latest_scan() is None

    def test_scan_history_empty_when_not_run(self):
        """get_scan_history should return empty list when no scan has been executed."""
        with patch.object(PlatformAPIService, '__init__', lambda self, **kw: None):
            svc = PlatformAPIService.__new__(PlatformAPIService)
            svc._scan_history = []

            assert svc.get_scan_history() == []


# ────────────────────────────────────────────────────────────
# Test 2: Real SUCCESS scan serialization
# ────────────────────────────────────────────────────────────

class TestSuccessScanSerialization:
    """Verify that a successful scan is correctly serialized."""

    def test_success_scan_has_all_required_fields(self):
        result = _make_scan_cycle_result(
            status=CycleStatus.SUCCESS,
            discovered_events=120,
            selected_events=50,
            matched_events=25,
            markets_evaluated=100,
        )
        serialized = _serialize_scan_cycle_result(result)

        assert serialized["cycle_status"] == "SUCCESS"
        assert serialized["execution_id"].startswith("scan_")
        assert "started_at" in serialized
        assert "completed_at" in serialized
        assert isinstance(serialized["duration_seconds"], float)

        # Counts
        counts = serialized["counts"]
        assert counts["discovered_events"] == 120
        assert counts["selected_events"] == 50
        assert counts["matched_events"] == 25
        assert counts["markets_evaluated"] == 100

        # Provider results
        assert "superbet" in serialized["provider_results"]
        assert "betclic" in serialized["provider_results"]
        assert serialized["provider_results"]["superbet"]["discovered_count"] == 40
        assert serialized["provider_results"]["betclic"]["parsed_count"] == 15

    def test_stage_timings_serialized(self):
        result = _make_scan_cycle_result()
        serialized = _serialize_scan_cycle_result(result)

        timings = serialized["stage_timings"]
        assert timings["acquisition_seconds"] == 2.0
        assert timings["normalization_seconds"] == 0.5
        assert timings["matching_seconds"] == 0.3
        assert timings["detection_seconds"] == 0.2

    def test_resource_metrics_serialized(self):
        result = _make_scan_cycle_result()
        serialized = _serialize_scan_cycle_result(result)

        metrics = serialized["resource_metrics"]
        assert metrics["total_http_requests"] == 25
        assert metrics["detail_http_requests"] == 20
        assert metrics["peak_memory_mb"] == 35.0


# ────────────────────────────────────────────────────────────
# Test 3: SUCCESS with zero opportunities
# ────────────────────────────────────────────────────────────

class TestZeroOpportunityState:
    """Verify zero-surebet state is informative, not an error."""

    def test_zero_surebets_is_success(self):
        result = _make_scan_cycle_result(
            status=CycleStatus.SUCCESS,
            detected_opportunities=0,
        )
        serialized = _serialize_scan_cycle_result(result)

        assert serialized["cycle_status"] == "SUCCESS"
        assert serialized["counts"]["detected_opportunities"] == 0
        assert serialized["opportunities"] == []

    def test_nearest_opportunity_telemetry(self):
        nearest = {
            "canonical_event_id": "cev_test123",
            "canonical_market_key": "1X2:FULL_TIME:MATCH:no_line",
            "implied_probability_sum": 1.012,
            "distance_to_surebet": 0.012,
            "best_legs": [
                {"selection_type": "HOME", "provider": "superbet", "odds": 2.45},
                {"selection_type": "DRAW", "provider": "betclic", "odds": 3.40},
            ],
        }
        result = _make_scan_cycle_result(
            detected_opportunities=0,
            nearest_opportunity=nearest,
        )
        serialized = _serialize_scan_cycle_result(result)

        assert serialized["nearest_opportunity"] is not None
        assert serialized["nearest_opportunity"]["canonical_event_id"] == "cev_test123"
        assert serialized["nearest_opportunity"]["implied_probability_sum"] == 1.012


# ────────────────────────────────────────────────────────────
# Test 4: SUCCESS with real opportunity
# ────────────────────────────────────────────────────────────

class TestRealOpportunity:
    """Verify opportunities from the pipeline are serialized correctly."""

    def test_surebet_opportunity_serialized(self):
        from normalization.surebet import SurebetOpportunity, SurebetLeg
        mkt_key = CanonicalMarketKey(market_type="1X2", period="FULL_TIME", scope="MATCH")
        opp = SurebetOpportunity(
            opportunity_id="opp_test_abc123",
            canonical_event_id="cev_abc123",
            canonical_market_key=mkt_key,
            implied_probability_sum=Decimal("0.9850"),
            arbitrage_margin=Decimal("0.0152"),
            legs=[
                SurebetLeg(
                    canonical_selection_key=CanonicalSelectionKey(market_key=mkt_key, selection_type="HOME"),
                    selection_type="HOME",
                    provider="superbet",
                    odds=Decimal("2.50"),
                    source_selection_id="sel_sb_home",
                    implied_probability=Decimal("0.4000"),
                ),
                SurebetLeg(
                    canonical_selection_key=CanonicalSelectionKey(market_key=mkt_key, selection_type="DRAW"),
                    selection_type="DRAW",
                    provider="betclic",
                    odds=Decimal("3.60"),
                    source_selection_id="sel_bc_draw",
                    implied_probability=Decimal("0.2778"),
                ),
                SurebetLeg(
                    canonical_selection_key=CanonicalSelectionKey(market_key=mkt_key, selection_type="AWAY"),
                    selection_type="AWAY",
                    provider="superbet",
                    odds=Decimal("3.25"),
                    source_selection_id="sel_sb_away",
                    implied_probability=Decimal("0.3077"),
                ),
            ],
        )

        summary = serialize_opportunity_summary(opp)
        assert summary["opportunity_type"] == "SUREBET"
        assert summary["canonical_event_id"] == "cev_abc123"
        assert "legs" in summary
        assert len(summary["legs"]) == 3
        # Must not contain undefined values
        for key in ["opportunity_type", "canonical_event_id", "canonical_market_key"]:
            assert summary[key] is not None


# ────────────────────────────────────────────────────────────
# Test 5: PARTIAL scan
# ────────────────────────────────────────────────────────────

class TestPartialScan:
    """Verify PARTIAL scan status is correctly represented."""

    def test_partial_scan_with_provider_failure(self):
        providers = {
            "superbet": _make_provider_result("superbet", "COMPLETED", 40, 20, 1.2),
            "betclic": _make_provider_result("betclic", "FAILED", 0, 0, 0.5, errors=["Connection timeout"]),
        }
        result = _make_scan_cycle_result(
            status=CycleStatus.PARTIAL,
            providers=providers,
            warnings=["betclic provider failed — scan completed with degradation"],
        )
        serialized = _serialize_scan_cycle_result(result)

        assert serialized["cycle_status"] == "PARTIAL"
        assert serialized["provider_results"]["betclic"]["status"] == "FAILED"
        assert len(serialized["provider_results"]["betclic"]["errors"]) == 1
        assert len(serialized["warnings"]) > 0


# ────────────────────────────────────────────────────────────
# Test 6: FAILED scan
# ────────────────────────────────────────────────────────────

class TestFailedScan:
    """Verify FAILED scan status is correctly represented."""

    def test_failed_scan_has_errors(self):
        result = _make_scan_cycle_result(
            status=CycleStatus.FAILED,
            errors=["Critical pipeline failure: all providers unreachable"],
        )
        serialized = _serialize_scan_cycle_result(result)

        assert serialized["cycle_status"] == "FAILED"
        assert len(serialized["errors"]) > 0
        assert "Critical pipeline failure" in serialized["errors"][0]


# ────────────────────────────────────────────────────────────
# Test 7: Scheduler state
# ────────────────────────────────────────────────────────────

class TestSchedulerState:
    """Verify scheduler status is properly exposed."""

    def test_scheduler_disabled_state(self):
        with patch.object(PlatformAPIService, '__init__', lambda self, **kw: None):
            svc = PlatformAPIService.__new__(PlatformAPIService)
            mock_scheduler = MagicMock()
            mock_scheduler.get_status.return_value = {
                "enabled": False,
                "interval_minutes": 15,
                "scan_scope": "POPULAR",
                "hours_ahead": 24,
                "event_limit": 50,
                "next_scan_at": None,
                "last_scan_at": None,
                "is_running": False,
            }
            svc.scheduler = mock_scheduler

            status = svc.get_scheduler_status()
            assert status["enabled"] is False
            assert status["next_scan_at"] is None

    def test_scheduler_enabled_state(self):
        with patch.object(PlatformAPIService, '__init__', lambda self, **kw: None):
            svc = PlatformAPIService.__new__(PlatformAPIService)
            mock_scheduler = MagicMock()
            mock_scheduler.get_status.return_value = {
                "enabled": True,
                "interval_minutes": 10,
                "scan_scope": "POPULAR",
                "hours_ahead": 12,
                "event_limit": 30,
                "next_scan_at": "2026-08-17T19:00:00Z",
                "last_scan_at": "2026-08-17T18:50:00Z",
                "is_running": True,
            }
            svc.scheduler = mock_scheduler

            status = svc.get_scheduler_status()
            assert status["enabled"] is True
            assert status["next_scan_at"] is not None
            assert status["interval_minutes"] == 10


# ────────────────────────────────────────────────────────────
# Test 8: Provider status rendering
# ────────────────────────────────────────────────────────────

class TestProviderStatus:
    """Verify provider status comes from real data, not hardcoded values."""

    def test_provider_results_serialized(self):
        result = _make_scan_cycle_result()
        serialized = _serialize_scan_cycle_result(result)

        for prov_name in ["superbet", "betclic"]:
            prov = serialized["provider_results"][prov_name]
            assert "name" in prov
            assert "status" in prov
            assert "execution_duration" in prov
            assert "discovered_count" in prov
            assert "parsed_count" in prov
            assert "errors" in prov
            assert "warnings" in prov
            # Values must be real numbers, not hardcoded 216
            assert isinstance(prov["discovered_count"], int)
            assert isinstance(prov["parsed_count"], int)

    def test_provider_health_enrichment(self):
        """get_providers should include last scan metrics when available."""
        with patch.object(PlatformAPIService, '__init__', lambda self, **kw: None):
            svc = PlatformAPIService.__new__(PlatformAPIService)
            mock_pm = MagicMock()
            mock_pm.check_health.return_value = {
                "overall_status": "HEALTHY",
                "provider_health": {
                    "superbet": {"status": "HEALTHY", "circuit_breaker": "CLOSED"},
                    "betclic": {"status": "HEALTHY", "circuit_breaker": "CLOSED"},
                },
                "registered_providers": ["superbet", "betclic"],
            }
            svc.provider_manager = mock_pm
            svc._last_scan_result = {
                "provider_results": {
                    "superbet": {"discovered_count": 45, "parsed_count": 22, "execution_duration": 1.5, "status": "COMPLETED", "errors": [], "warnings": []},
                    "betclic": {"discovered_count": 38, "parsed_count": 18, "execution_duration": 1.1, "status": "COMPLETED", "errors": [], "warnings": []},
                },
            }

            health = svc.get_providers()
            ph = health["provider_health"]
            assert ph["superbet"]["last_scan_discovered"] == 45
            assert ph["betclic"]["last_scan_parsed"] == 18


# ────────────────────────────────────────────────────────────
# Test 9: Missing optional metrics (no undefined/NaN)
# ────────────────────────────────────────────────────────────

class TestMissingOptionalMetrics:
    """Ensure missing optional fields don't produce undefined or NaN."""

    def test_serialization_with_no_valuebets(self):
        result = _make_scan_cycle_result()
        serialized = _serialize_scan_cycle_result(result)

        counts = serialized["counts"]
        # Valuebet counts should be 0 (not undefined)
        assert counts["valuebets_qualified"] == 0
        assert counts["valuebet_candidates"] == 0

    def test_serialization_with_no_nearest_opportunity(self):
        result = _make_scan_cycle_result(nearest_opportunity=None)
        serialized = _serialize_scan_cycle_result(result)

        # nearest_opportunity should be None, not undefined
        assert serialized["nearest_opportunity"] is None

    def test_all_numeric_counts_are_integers(self):
        result = _make_scan_cycle_result()
        serialized = _serialize_scan_cycle_result(result)

        counts = serialized["counts"]
        for key, value in counts.items():
            if key in ("cross_bookmaker_overlap_rate", "cross_bookmaker_overlap_rate_pct"):
                assert isinstance(value, float), f"counts.{key} should be float, got {type(value)}: {value}"
            else:
                assert isinstance(value, int), f"counts.{key} should be int, got {type(value)}: {value}"


# ────────────────────────────────────────────────────────────
# Test 10: undefined/NaN prevention
# ────────────────────────────────────────────────────────────

class TestNoUndefinedNaN:
    """Verify no None values appear in critical serialized fields."""

    def test_no_none_in_critical_fields(self):
        result = _make_scan_cycle_result()
        serialized = _serialize_scan_cycle_result(result)

        assert serialized["execution_id"] is not None
        assert serialized["cycle_status"] is not None
        assert serialized["started_at"] is not None
        assert serialized["duration_seconds"] is not None

    def test_provider_results_no_none_fields(self):
        result = _make_scan_cycle_result()
        serialized = _serialize_scan_cycle_result(result)

        for prov_name, prov_data in serialized["provider_results"].items():
            assert prov_data["name"] is not None, f"{prov_name}.name is None"
            assert prov_data["status"] is not None, f"{prov_name}.status is None"
            assert prov_data["execution_duration"] is not None, f"{prov_name}.execution_duration is None"


# ────────────────────────────────────────────────────────────
# Test 11: Run Scan flow
# ────────────────────────────────────────────────────────────

class TestRunScanFlow:
    """Verify the run_scan method properly executes and returns serialized results."""

    def test_run_scan_returns_serialized_result(self):
        with patch.object(PlatformAPIService, '__init__', lambda self, **kw: None):
            svc = PlatformAPIService.__new__(PlatformAPIService)
            svc._scan_lock = threading.Lock()
            svc._is_scanning = False
            svc._scanner_status = "READY"
            svc._last_scan_result = None
            svc._scan_history = []
            svc.db_manager = None

            mock_result = _make_scan_cycle_result()
            mock_orchestrator = MagicMock()
            mock_orchestrator.run_scan_cycle.return_value = mock_result
            svc.scan_orchestrator = mock_orchestrator

            result = svc.run_scan()
            assert result["cycle_status"] == "SUCCESS"
            assert "execution_id" in result
            assert "provider_results" in result
            assert svc._last_scan_result is not None
            assert len(svc._scan_history) == 1


# ────────────────────────────────────────────────────────────
# Test 12: Concurrent scan protection
# ────────────────────────────────────────────────────────────

class TestConcurrentScanProtection:
    """Verify that concurrent scans are blocked with 409."""

    def test_concurrent_scan_raises_409(self):
        from api.exceptions import APIError

        with patch.object(PlatformAPIService, '__init__', lambda self, **kw: None):
            svc = PlatformAPIService.__new__(PlatformAPIService)
            svc._scan_lock = threading.Lock()
            svc._is_scanning = False
            svc._scanner_status = "SCANNING"
            svc._last_scan_result = None
            svc._scan_history = []
            svc.db_manager = None

            # Simulate lock being held (scan in progress)
            svc._scan_lock.acquire()

            mock_orchestrator = MagicMock()
            svc.scan_orchestrator = mock_orchestrator

            with pytest.raises(APIError) as exc_info:
                svc.run_scan()
            assert exc_info.value.status_code == 409

            svc._scan_lock.release()


# ────────────────────────────────────────────────────────────
# Test 13: Latest scan refresh
# ────────────────────────────────────────────────────────────

class TestLatestScanRefresh:
    """Verify get_latest_scan returns the most recent scan result."""

    def test_latest_scan_updates_after_run(self):
        with patch.object(PlatformAPIService, '__init__', lambda self, **kw: None):
            svc = PlatformAPIService.__new__(PlatformAPIService)
            svc._scan_lock = threading.Lock()
            svc._is_scanning = False
            svc._scanner_status = "READY"
            svc._last_scan_result = None
            svc._scan_history = []
            svc.db_manager = None

            mock_result = _make_scan_cycle_result(discovered_events=150)
            mock_orchestrator = MagicMock()
            mock_orchestrator.run_scan_cycle.return_value = mock_result
            svc.scan_orchestrator = mock_orchestrator

            svc.run_scan()
            latest = svc.get_latest_scan()
            assert latest is not None
            assert latest["counts"]["discovered_events"] == 150


# ────────────────────────────────────────────────────────────
# Test 14: Real opportunity summary rendering
# ────────────────────────────────────────────────────────────

class TestOpportunitySummaryRendering:
    """Verify opportunity summaries contain all required fields for the dashboard."""

    def test_surebet_summary_fields(self):
        from normalization.surebet import SurebetOpportunity, SurebetLeg
        mkt_key = CanonicalMarketKey(market_type="1X2", period="FULL_TIME", scope="MATCH")
        opp = SurebetOpportunity(
            opportunity_id="opp_test_summary",
            canonical_event_id="cev_test",
            canonical_market_key=mkt_key,
            implied_probability_sum=Decimal("0.975"),
            arbitrage_margin=Decimal("0.0256"),
            legs=[
                SurebetLeg(
                    canonical_selection_key=CanonicalSelectionKey(market_key=mkt_key, selection_type="HOME"),
                    selection_type="HOME",
                    provider="superbet",
                    odds=Decimal("2.50"),
                    source_selection_id="sel_home",
                    implied_probability=Decimal("0.4"),
                ),
                SurebetLeg(
                    canonical_selection_key=CanonicalSelectionKey(market_key=mkt_key, selection_type="AWAY"),
                    selection_type="AWAY",
                    provider="betclic",
                    odds=Decimal("3.20"),
                    source_selection_id="sel_away",
                    implied_probability=Decimal("0.3125"),
                ),
            ],
        )

        summary = serialize_opportunity_summary(opp)
        # All essential fields must be present and not None
        required_fields = [
            "opportunity_type", "canonical_event_id", "canonical_market_key",
            "legs",
        ]
        for field in required_fields:
            assert field in summary, f"Missing field: {field}"
            assert summary[field] is not None, f"Field {field} is None"

        # Legs should have real data
        for leg in summary["legs"]:
            assert "selection_type" in leg
            assert "provider" in leg
            assert "odds" in leg


# ────────────────────────────────────────────────────────────
# Test 15: No mock data in notifications
# ────────────────────────────────────────────────────────────

class TestNoMockNotifications:
    """Verify notifications endpoint no longer returns fabricated data."""

    def test_notifications_empty_when_no_scans(self):
        with patch.object(PlatformAPIService, '__init__', lambda self, **kw: None):
            svc = PlatformAPIService.__new__(PlatformAPIService)
            svc._scan_history = []

            result = svc.list_notifications()
            assert result["total"] == 0
            assert result["notifications"] == []
            # No fabricated "Real Madrid vs Barcelona" notifications
            for notif in result["notifications"]:
                assert "Real Madrid" not in notif.get("message", "")

    def test_notifications_from_real_scans(self):
        with patch.object(PlatformAPIService, '__init__', lambda self, **kw: None):
            svc = PlatformAPIService.__new__(PlatformAPIService)
            svc._scan_history = [
                {"execution_id": "scan_abc", "status": "SUCCESS", "completed_at": "2026-08-17T18:00:00Z", "surebets_count": 0, "scan_source": "MANUAL"},
                {"execution_id": "scan_def", "status": "SUCCESS", "completed_at": "2026-08-17T18:05:00Z", "surebets_count": 2, "scan_source": "AUTOMATED"},
            ]

            result = svc.list_notifications()
            assert result["total"] == 2
            # First notification (from scan with 0 surebets)
            assert result["notifications"][0]["level"] == "INFO"
            # Second notification (from scan with 2 surebets)
            assert result["notifications"][1]["level"] == "CRITICAL"
            assert "2 Surebet" in result["notifications"][1]["title"]


# ────────────────────────────────────────────────────────────
# Test 16: No mock data in odds history
# ────────────────────────────────────────────────────────────

class TestNoMockOddsHistory:
    """Verify odds history returns honest empty state."""

    def test_odds_history_unavailable(self):
        with patch.object(PlatformAPIService, '__init__', lambda self, **kw: None):
            svc = PlatformAPIService.__new__(PlatformAPIService)

            result = svc.get_odds_history()
            assert result["available"] is False
            assert result["series"] == []
            assert result["timestamps"] == []


# ────────────────────────────────────────────────────────────
# Test 17: No mock data in event detail
# ────────────────────────────────────────────────────────────

class TestNoMockEventDetail:
    """Verify event detail no longer returns hardcoded Real Madrid vs Barcelona."""

    def test_event_detail_not_found(self):
        with patch.object(PlatformAPIService, '__init__', lambda self, **kw: None):
            svc = PlatformAPIService.__new__(PlatformAPIService)
            svc._last_scan_result = None
            mock_db = MagicMock()
            mock_session = MagicMock()
            mock_db.get_session.return_value.__enter__ = lambda s: mock_session
            mock_db.get_session.return_value.__exit__ = MagicMock(return_value=False)
            svc.db_manager = mock_db

            # Mock EventRepository to return None
            with patch("api.services.EventRepository") as MockRepo:
                MockRepo.return_value.get_by_id.return_value = None
                MockRepo.return_value.get_event_with_details.return_value = None
                result = svc.get_event_detail("nonexistent-event")

            assert result is None or result.get("status") == "NOT_FOUND"


# ────────────────────────────────────────────────────────────
# Test 18: Scan history serialization
# ────────────────────────────────────────────────────────────

class TestScanHistorySerialization:
    """Verify scan history entries contain correct fields."""

    def test_history_entry_after_scan(self):
        with patch.object(PlatformAPIService, '__init__', lambda self, **kw: None):
            svc = PlatformAPIService.__new__(PlatformAPIService)
            svc._scan_lock = threading.Lock()
            svc._is_scanning = False
            svc._scanner_status = "READY"
            svc._last_scan_result = None
            svc._scan_history = []
            svc.db_manager = None

            mock_result = _make_scan_cycle_result(
                discovered_events=100,
                selected_events=40,
                matched_events=20,
                detected_opportunities=1,
            )
            mock_orchestrator = MagicMock()
            mock_orchestrator.run_scan_cycle.return_value = mock_result
            svc.scan_orchestrator = mock_orchestrator

            svc.run_scan(scan_source="MANUAL")

            history = svc.get_scan_history()
            assert len(history) == 1
            entry = history[0]
            assert entry["status"] == "SUCCESS"
            assert entry["events_discovered"] == 100
            assert entry["events_selected"] == 40
            assert entry["events_matched"] == 20
            assert entry["surebets_count"] == 1
            assert entry["scan_source"] == "MANUAL"
