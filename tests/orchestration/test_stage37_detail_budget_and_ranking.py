"""
Unit tests for Stage 37: Intelligent Detail Budget & Acquisition Optimization
"""

from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List
import pytest

from orchestration.models import ScanConfig, ResourceMetrics, ScanCycleResult, CycleStatus
from orchestration.event_selection import DefaultEventSelectionPolicy, DetailPrioritizationResult
from providers.betclic.config import BetclicConfig
from providers.betclic.models import BetclicDiscoveredItem
from providers.betclic.fetch.fetcher import BetclicFetcher
from providers.superbet.config import SuperbetConfig


def test_configurable_detail_budget_resolution():
    """Verifies that NORMAL and DEEP detail budgets are resolved accurately and can be customized."""
    # 1. Default NORMAL and DEEP budgets
    cfg_normal = ScanConfig(scan_mode="NORMAL")
    assert cfg_normal.normal_detail_budget == 40
    assert cfg_normal.effective_max_detail_requests == 40
    assert cfg_normal.detail_workers == 6

    cfg_deep = ScanConfig(scan_mode="DEEP")
    assert cfg_deep.deep_detail_budget == 100
    assert cfg_deep.effective_max_detail_requests == 100

    # 2. Configurable overrides
    cfg_custom_normal = ScanConfig(scan_mode="NORMAL", normal_detail_budget=35)
    assert cfg_custom_normal.effective_max_detail_requests == 35

    cfg_custom_deep = ScanConfig(scan_mode="DEEP", deep_detail_budget=125)
    assert cfg_custom_deep.effective_max_detail_requests == 125

    # 3. Explicit max_detail_requests takes precedence
    cfg_explicit = ScanConfig(scan_mode="NORMAL", max_detail_requests=50)
    assert cfg_explicit.effective_max_detail_requests == 50


def test_priority_ranking_tier0_uefa_over_tier1_and_tier2():
    """Verifies that Tier 0 UEFA tournaments outrank Tier 1 top leagues and Tier 2 minor leagues."""
    policy = DefaultEventSelectionPolicy()
    now = datetime(2026, 8, 28, 12, 0, 0, tzinfo=timezone.utc)

    items = [
        {
            "event_id": "tier2_match",
            "name": "Finnish Cup Match",
            "competition": "Suomen Cup",
            "start_time": now + timedelta(hours=2),
        },
        {
            "event_id": "tier1_match",
            "name": "Arsenal vs Chelsea",
            "competition": "Premier League",
            "start_time": now + timedelta(hours=24),
        },
        {
            "event_id": "tier0_match",
            "name": "Real Madrid vs Manchester City",
            "competition": "UEFA Champions League",
            "start_time": now + timedelta(hours=48),
        },
        {
            "event_id": "tier0_qual_match",
            "name": "Bodø/Glimt vs Crvena Zvezda",
            "competition": "UEFA Champions League (Qualifiers)",
            "start_time": now + timedelta(hours=36),
        },
        {
            "event_id": "tier1_ekstraklasa",
            "name": "Legia Warszawa vs Lech Poznan",
            "competition": "PKO BP Ekstraklasa",
            "start_time": now + timedelta(hours=12),
        },
    ]

    overlap_ids = {it["event_id"] for it in items}

    res = policy.prioritize_detail_events(
        discovered_items=items,
        overlap_event_ids=overlap_ids,
        max_detail_requests=3,
        current_time=now,
    )

    # Top 3 selected should contain the 2 UEFA Tier 0 matches first, followed by Tier 1 Ekstraklasa (earlier kickoff)
    assert len(res.selected_event_ids) == 3
    assert res.selected_event_ids[0] in ("tier0_match", "tier0_qual_match")
    assert res.selected_event_ids[1] in ("tier0_match", "tier0_qual_match")
    assert res.selected_event_ids[2] in ("tier1_ekstraklasa", "tier1_match")
    assert "tier2_match" not in res.selected_event_ids


def test_priority_ranking_overlap_strictly_prioritized():
    """Verifies that cross-bookmaker overlapping events are strictly prioritized before non-overlapping events."""
    policy = DefaultEventSelectionPolicy()
    now = datetime(2026, 8, 28, 12, 0, 0, tzinfo=timezone.utc)

    items = [
        {
            "event_id": "ucl_non_overlap",
            "name": "PSG vs Bayern",
            "competition": "UEFA Champions League",
            "start_time": now + timedelta(hours=2),
        },
        {
            "event_id": "epl_overlap",
            "name": "Liverpool vs Man United",
            "competition": "Premier League",
            "start_time": now + timedelta(hours=24),
        },
    ]

    res = policy.prioritize_detail_events(
        discovered_items=items,
        overlap_event_ids={"epl_overlap"},
        max_detail_requests=1,
        current_time=now,
    )

    assert res.selected_event_ids == ["epl_overlap"]
    assert res.events_overlap_selected == 1


def test_selective_grpc_categories_betclic():
    """Verifies that Betclic gRPC categories omit ca_ftb_top and select props categories conditionally."""
    cfg = BetclicConfig(selective_categories=True)
    assert "ca_ftb_top" not in cfg.grpc_categories
    assert "ca_ftb_prp" in cfg.grpc_categories
    assert "ca_ftb_gsc" in cfg.grpc_categories
    assert cfg.detail_workers == 6

    fetcher = BetclicFetcher(config=cfg)

    # 1. Tier 0 / Tier 1 event (Premier League) -> Full categories including props
    top_item = BetclicDiscoveredItem(
        provider_event_id="101",
        name="Arsenal vs Chelsea",
        competition_name="Premier League",
        url="https://www.betclic.pl/event/101",
        start_time="2026-08-28T18:00:00Z",
    )
    cats_top = fetcher._resolve_categories_for_item(top_item)
    assert "ca_ftb_prp" in cats_top
    assert "ca_ftb_gsc" in cats_top

    # 2. Tier 2 event (minor league) -> Tier 2 categories without props
    minor_item = BetclicDiscoveredItem(
        provider_event_id="102",
        name="Team A vs Team B",
        competition_name="Finland Kolmonen",
        url="https://www.betclic.pl/event/102",
        start_time="2026-08-28T18:00:00Z",
    )
    cats_minor = fetcher._resolve_categories_for_item(minor_item)
    assert "ca_ftb_prp" not in cats_minor
    assert "ca_ftb_gsc" not in cats_minor
    assert "ca_ftb_rslt" in cats_minor
    assert "ca_ftb_goa" in cats_minor


def test_telemetry_and_audit_report():
    """Verifies that ResourceMetrics and ScanCycleResult track and format detail telemetry properly."""
    metrics = ResourceMetrics(
        events_discovered=500,
        markets_discovered=4500,
        detail_events_selected=40,
        detail_events_overlap_selected=38,
        detail_overlap_selection_rate=0.95,
        detail_budget_allocated=40,
        scan_mode="NORMAL",
        detail_acquisition_seconds=12.5,
        markets_per_selected_event=112.5,
    )

    result = ScanCycleResult(
        execution_id="test_scan_123",
        cycle_status=CycleStatus.SUCCESS,
        started_at="2026-08-28T12:00:00Z",
        completed_at="2026-08-28T12:00:15Z",
        duration_seconds=15.0,
        resource_metrics=metrics,
        detail_events_selected=40,
        detail_events_overlap_selected=38,
        detail_overlap_selection_rate=0.95,
        detail_budget_allocated=40,
        scan_mode="NORMAL",
        detail_acquisition_seconds=12.5,
        markets_per_selected_event=112.5,
    )

    report = result.generate_audit_report()
    assert "Detail Budget: Mode=NORMAL (Allocated=40)" in report
    assert "Selected=40 (38 overlap, 95.0%)" in report
    assert "Acq Time=12.50s" in report
    assert "Avg Mkts/Event=112.5" in report
