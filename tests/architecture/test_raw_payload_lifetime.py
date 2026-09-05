"""
Phase 3 Architecture Verification: Raw Payload Lifetime & Memory Invariant Safety
"""

import pytest
from differential.runner import DifferentialRunner
from orchestration.models import ScanConfig
from providers.superbet.provider import SuperbetProvider
from providers.betclic.provider import BetclicProvider
from domain.models import Event, Market, Selection, Odds


def test_raw_payload_not_retained_in_canonical_domain_entities():
    """Verifies Phase 0 / Phase 3 Invariant: Domain models never store raw JSON dictionaries."""
    ev = Event(
        competition_id="comp_001",
        home_participant="Arsenal",
        away_participant="Chelsea",
        scheduled_start="2026-08-17T19:00:00Z",
    )
    assert not hasattr(ev, "raw_data")
    assert not hasattr(ev, "raw_response")
    assert not hasattr(ev, "raw_payload")

    mkt = Market(
        event_id="ev_001",
        market_type="1X2",
    )
    assert not hasattr(mkt, "raw_data")
    assert not hasattr(mkt, "raw_response")

    sel = Selection(
        market_id="mkt_001",
        selection_type="HOME",
    )
    assert not hasattr(sel, "raw_data")

    odds = Odds(
        selection_id="sel_001",
        bookmaker="superbet",
        decimal_odds=2.10,
    )
    assert not hasattr(odds, "raw_data")


def test_provider_discovery_cache_explicit_contract():
    """Verifies that BaseProvider discovery cache methods function explicitly and release cleanly."""
    sb = SuperbetProvider()
    assert sb.get_discovered_items() is None

    mock_items = [{"id": "1", "name": "Event 1"}, {"id": "2", "name": "Event 2"}]
    sb.set_discovered_items(mock_items)
    assert sb.get_discovered_items() == mock_items

    # Discover hook returns cached items without network calls
    assert sb.discover() == mock_items

    sb.clear_discovered_cache()
    assert sb.get_discovered_items() is None


def test_scan_cycle_result_does_not_leak_raw_payloads_in_serialization():
    """Verifies that ScanCycleResult serialization to dict does not include raw JSON payload blobs."""
    runner = DifferentialRunner()
    snapshot = runner.run_pipeline("multi_bookmaker_v1")

    # Snapshot and card funnels are lightweight and structured
    assert snapshot.checksum is not None
    assert "discovery" in snapshot.stages
    assert "final" in snapshot.stages

    # Verify no 'raw' payload dict is in snapshot records
    for stage_name, stage_snap in snapshot.stages.items():
        for rec_key, rec_val in stage_snap.records.items():
            if isinstance(rec_val, dict):
                assert "raw" not in rec_val
                assert "raw_response" not in rec_val
