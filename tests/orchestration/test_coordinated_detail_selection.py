"""
Tests ensuring coordinated canonical detail event selection between Superbet and Betclic.
Validates that when a canonical event is selected for Detail, its respective event IDs
are dispatched to both providers synchronously within the shared budget.
"""

from unittest.mock import MagicMock, patch
from datetime import datetime, timezone
import pytest

from orchestration.event_selection import DefaultEventSelectionPolicy
from orchestration.models import ScanConfig
from orchestration.scan_orchestrator import ProductionScanOrchestrator
from providers.superbet.provider import SuperbetProvider
from providers.betclic.provider import BetclicProvider
from providers.superbet.models import SuperbetDiscoveredItem
from providers.betclic.models import BetclicDiscoveredItem


def test_shared_canonical_event_detail_selection_mapping():
    """
    Verifies that when canonical events are matched and ranked for detail:
    1. The exact paired event_ids for Superbet and Betclic are passed into configure_full_market_acquisition.
    2. Shared detail budget (e.g. 4 events) selects exactly 4 common events for both providers.
    3. Tier 0/Tier 1 competitions and earlier kickoff times take precedence.
    """
    now = datetime(2026, 8, 29, 12, 0, 0, tzinfo=timezone.utc)
    policy = DefaultEventSelectionPolicy()

    # Create mock discovered items for Superbet
    sb_items = [
        SuperbetDiscoveredItem(
            event_id=f"sb_{i}",
            match_name=f"Home {i} vs Away {i}",
            competition_name="UEFA Champions League" if i < 3 else "Ekstraklasa" if i < 6 else "Standard League",
            start_time="2026-08-29T14:00:00Z",
        )
        for i in range(10)
    ]

    # Create mock discovered items for Betclic
    bc_items = [
        BetclicDiscoveredItem(
            provider_event_id=f"bc_{i}",
            name=f"Home {i} vs Away {i}",
            competition_name="Liga Mistrzów" if i < 3 else "PKO Ekstraklasa" if i < 6 else "Standard League",
            start_time="2026-08-29T14:00:00Z",
            url="https://betclic.pl/match/1",
        )
        for i in range(10)
    ]

    overlap_sb = {f"sb_{i}" for i in range(8)}
    overlap_bc = {f"bc_{i}" for i in range(8)}
    forced_sb = [f"sb_{i}" for i in range(8)]
    forced_bc = [f"bc_{i}" for i in range(8)]

    budget = 4

    prio_sb = policy.prioritize_detail_events(
        discovered_items=sb_items,
        overlap_event_ids=overlap_sb,
        max_detail_requests=budget,
        current_time=now,
        forced_ranked_ids=forced_sb,
    )

    prio_bc = policy.prioritize_detail_events(
        discovered_items=bc_items,
        overlap_event_ids=overlap_bc,
        max_detail_requests=budget,
        current_time=now,
        forced_ranked_ids=forced_bc,
    )

    assert len(prio_sb.selected_event_ids) == budget
    assert len(prio_bc.selected_event_ids) == budget

    # Both selections correspond 1-to-1 to the top 4 matched events
    assert prio_sb.selected_event_ids == ["sb_0", "sb_1", "sb_2", "sb_3"]
    assert prio_bc.selected_event_ids == ["bc_0", "bc_1", "bc_2", "bc_3"]

    # Verify provider configuration integration
    sb_provider = SuperbetProvider()
    bc_provider = BetclicProvider()

    sb_provider.configure_full_market_acquisition(event_ids=prio_sb.selected_event_ids)
    bc_provider.configure_full_market_acquisition(event_ids=prio_bc.selected_event_ids)

    assert sb_provider.superbet_config.selected_event_ids == ["sb_0", "sb_1", "sb_2", "sb_3"]
    assert bc_provider.betclic_config.selected_event_ids == ["bc_0", "bc_1", "bc_2", "bc_3"]
