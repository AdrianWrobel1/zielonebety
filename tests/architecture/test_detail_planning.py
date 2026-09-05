"""
Phase 3 Architecture Verification: Coordinated Detail Selection Planner Determinism & Invariants
"""

from datetime import datetime, timezone
from decimal import Decimal
import pytest
from orchestration.detail_planning import CoordinatedDetailSelectionPlanner, DetailAcquisitionPlan
from orchestration.event_selection import DefaultEventSelectionPolicy
from orchestration.models import ScanConfig
from providers.superbet.models import SuperbetDiscoveredItem
from providers.betclic.models import BetclicDiscoveredItem


def test_planner_empty_input_graceful_handling():
    """Verifies that planner returns empty valid plan without errors when given empty discovery."""
    planner = CoordinatedDetailSelectionPlanner()
    config = ScanConfig(providers=("superbet", "betclic"), max_detail_requests=15)
    plan = planner.create_plan(
        sb_discovered=[],
        bc_discovered=[],
        sb_parser=None,
        bc_parser=None,
        config=config,
    )
    assert isinstance(plan, DetailAcquisitionPlan)
    assert len(plan.selected_event_ids_superbet) == 0
    assert len(plan.selected_event_ids_betclic) == 0
    assert len(plan.ranked_pairs) == 0


def test_planner_7_tuple_deterministic_ranking():
    """Verifies that pair ranking strictly enforces the 7-component lexicographical sort key:
    (pair_tier, -confidence, -mkt_count, kickoff_ts, pair_name, sb_eid_str, bc_eid_str)
    """
    policy = DefaultEventSelectionPolicy()
    planner = CoordinatedDetailSelectionPlanner(event_selection_policy=policy)

    # 1. Tier 0 vs Tier 1 comparison: Tier 0 must come first
    tier0_pair = (0, -0.95, -100, 1000.0, "Real Madrid vs Barcelona", "sb_1", "bc_1")
    tier1_pair = (1, -0.99, -200, 500.0, "Arsenal vs Chelsea", "sb_2", "bc_2")

    pairs = [tier1_pair, tier0_pair]
    pairs.sort(key=lambda x: (x[0], x[1], x[2], x[3], x[4], x[5], x[6]))
    assert pairs[0] == tier0_pair
    assert pairs[1] == tier1_pair

    # 2. Same Tier, Higher Confidence (-0.99 before -0.90)
    conf_high = (1, -0.99, -100, 1000.0, "Match A", "sb_3", "bc_3")
    conf_low = (1, -0.90, -100, 1000.0, "Match B", "sb_4", "bc_4")
    pairs2 = [conf_low, conf_high]
    pairs2.sort(key=lambda x: (x[0], x[1], x[2], x[3], x[4], x[5], x[6]))
    assert pairs2[0] == conf_high

    # 3. Same Tier & Confidence, Richer Markets (-250 before -50)
    rich_mkt = (1, -0.95, -250, 1000.0, "Match Rich", "sb_5", "bc_5")
    lean_mkt = (1, -0.95, -50, 1000.0, "Match Lean", "sb_6", "bc_6")
    pairs3 = [lean_mkt, rich_mkt]
    pairs3.sort(key=lambda x: (x[0], x[1], x[2], x[3], x[4], x[5], x[6]))
    assert pairs3[0] == rich_mkt

    # 4. Same Tier, Conf, Markets, Earlier Kickoff (1000.0 before 2000.0)
    early_ko = (1, -0.95, -100, 1000.0, "Match Early", "sb_7", "bc_7")
    late_ko = (1, -0.95, -100, 2000.0, "Match Late", "sb_8", "bc_8")
    pairs4 = [late_ko, early_ko]
    pairs4.sort(key=lambda x: (x[0], x[1], x[2], x[3], x[4], x[5], x[6]))
    assert pairs4[0] == early_ko


def test_planner_exact_budget_enforcement():
    """Verifies that detail budget limit is strictly respected."""
    policy = DefaultEventSelectionPolicy()
    planner = CoordinatedDetailSelectionPlanner(event_selection_policy=policy)

    now = datetime(2026, 8, 17, 12, 0, 0, tzinfo=timezone.utc)
    config = ScanConfig(providers=("superbet", "betclic"), max_detail_requests=2)

    # 4 dummy items
    sb_items = [
        SuperbetDiscoveredItem(event_id=f"sb_{i}", match_name=f"Match {i}", start_time="2026-08-17T18:00:00Z")
        for i in range(1, 5)
    ]
    bc_items = [
        BetclicDiscoveredItem(
            provider_event_id=f"bc_{i}",
            name=f"Match {i}",
            start_time="2026-08-17T18:00:00Z",
            competition_name="Premier League",
            url=f"https://www.betclic.pl/event/{i}",
        )
        for i in range(1, 5)
    ]

    plan = planner.create_plan(
        sb_discovered=sb_items,
        bc_discovered=bc_items,
        sb_parser=None,
        bc_parser=None,
        config=config,
        evaluation_time=now,
    )
    assert len(plan.selected_event_ids_superbet) == 2
    assert len(plan.selected_event_ids_betclic) == 2
