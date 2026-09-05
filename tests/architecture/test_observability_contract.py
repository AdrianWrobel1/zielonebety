"""
Phase 3 Architecture Verification: Observability Contract & Telemetry Mode Integration
"""

import pytest
from orchestration.models import ScanConfig, TelemetryMode, ScanCycleResult, CycleStatus
from orchestration.detail_planning import CoordinatedDetailSelectionPlanner, DetailAcquisitionPlan
from orchestration.event_selection import DefaultEventSelectionPolicy


def test_scan_config_telemetry_modes():
    """Verifies that ScanConfig cleanly accepts all TelemetryMode values."""
    cfg_std = ScanConfig()
    assert cfg_std.telemetry_mode == "STANDARD"

    cfg_min = ScanConfig(telemetry_mode="MINIMAL")
    assert cfg_min.telemetry_mode == "MINIMAL"

    cfg_dbg = ScanConfig(telemetry_mode=TelemetryMode.FULL_DEBUG.value)
    assert cfg_dbg.telemetry_mode == "FULL_DEBUG"


def test_observability_detail_planning_telemetry():
    """Verifies that DetailAcquisitionPlan produces complete required Observability Contract diagnostics."""
    planner = CoordinatedDetailSelectionPlanner(event_selection_policy=DefaultEventSelectionPolicy())
    config = ScanConfig(scan_mode="NORMAL", max_detail_requests=10)

    plan = planner.create_plan(
        sb_discovered=[],
        bc_discovered=[],
        sb_parser=None,
        bc_parser=None,
        config=config,
    )

    diag = plan.diagnostics
    assert "scan_mode" in diag
    assert "detail_budget" in diag
    assert "matched_events_eligible" in diag
    assert "matched_events_selected" in diag
    assert "overview_only_matched_events" in diag
    assert "full_detail_matched_events" in diag
    assert "tier_0_available" in diag
    assert "tier_1_available" in diag
    assert "tier_2_available" in diag
    assert "overlap_selection_rate" in diag


def test_scan_cycle_result_cardinality_funnel_fields():
    """Verifies that ScanCycleResult exposes the full cardinality funnel across all stages."""
    result = ScanCycleResult(
        execution_id="test_exec_001",
        cycle_status=CycleStatus.SUCCESS,
        started_at="2026-08-30T10:00:00Z",
        completed_at="2026-08-30T10:00:05Z",
        duration_seconds=5.0,
    )

    # Core funnel counters
    assert hasattr(result, "discovered_events_count")
    assert hasattr(result, "parsed_events_count")
    assert hasattr(result, "normalized_graphs_count")
    assert hasattr(result, "markets_discovered_count")
    assert hasattr(result, "markets_normalized_count")
    assert hasattr(result, "markets_matched_count")
    assert hasattr(result, "markets_evaluated_count")
    assert hasattr(result, "matched_events_count")
    assert hasattr(result, "unmatched_events_count")
    assert hasattr(result, "cross_bookmaker_overlap_rate")
    assert hasattr(result, "detected_opportunities_count")
    assert hasattr(result, "dispatched_count")
    assert hasattr(result, "delivered_count")
