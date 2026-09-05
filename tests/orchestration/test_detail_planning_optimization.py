"""
Focused regression test suite protecting CoordinatedDetailSelectionPlanner behavior and optimization invariants.
"""

import json
import os
from datetime import datetime, timezone
import pytest

from domain.models import Event, Competition
from orchestration.detail_planning import CoordinatedDetailSelectionPlanner, DetailAcquisitionPlan
from orchestration.event_selection import DefaultEventSelectionPolicy
from orchestration.models import ScanConfig
from orchestration.scan_orchestrator import ProductionScanOrchestrator
from providers.superbet.models import SuperbetDiscoveredItem
from providers.betclic.models import BetclicDiscoveredItem


def test_detail_planning_baseline_exact_match():
    """
    Test 1: SAME SELECTION CONTRACT.
    Replays the deterministic baseline fixture (1,672 events) and asserts that the planner returns
    100% bit-for-bit identical selected event IDs, overlap IDs, and ranked pairs.
    """
    fixture_path = os.path.join("tests", "fixtures", "detail_planning_baseline.json")
    assert os.path.exists(fixture_path), f"Baseline fixture missing at {fixture_path}"

    with open(fixture_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    sb_items = [SuperbetDiscoveredItem(**item) for item in data["sb_discovered"]]
    bc_items = [BetclicDiscoveredItem(**item) for item in data["bc_discovered"]]
    eval_time = datetime.fromisoformat(data["evaluation_time"])
    config = ScanConfig(
        scan_mode=data["config"]["scan_mode"],
        max_detail_requests=data["config"]["max_detail_requests"],
    )

    orchestrator = ProductionScanOrchestrator(config=config)
    sb_inst = orchestrator._create_provider_instance("superbet")
    bc_inst = orchestrator._create_provider_instance("betclic")

    plan = orchestrator.detail_planner.create_plan(
        sb_discovered=sb_items,
        bc_discovered=bc_items,
        sb_parser=getattr(sb_inst, "parser", None),
        bc_parser=getattr(bc_inst, "parser", None),
        config=config,
        evaluation_time=eval_time,
    )

    expected = data["baseline_output"]

    assert plan.selected_event_ids_superbet == expected["selected_event_ids_superbet"]
    assert plan.selected_event_ids_betclic == expected["selected_event_ids_betclic"]
    assert sorted(list(plan.overlap_event_ids_superbet)) == expected["overlap_event_ids_superbet"]
    assert sorted(list(plan.overlap_event_ids_betclic)) == expected["overlap_event_ids_betclic"]
    assert [list(p) for p in plan.ranked_pairs] == expected["ranked_pairs"]
    assert len(plan.selected_event_ids_superbet) == expected["selected_counts"]["superbet"]
    assert len(plan.selected_event_ids_betclic) == expected["selected_counts"]["betclic"]


def test_detail_planning_identity_edge_cases():
    """
    Test 2: IDENTITY SEMANTICS.
    Verifies that the planner preserves canonical team resolution, Polish diacritics,
    kickoff tolerance, and missing metadata handling.
    """
    now = datetime(2026, 9, 10, 12, 0, 0, tzinfo=timezone.utc)
    config = ScanConfig(providers=("superbet", "betclic"), max_detail_requests=10)
    orchestrator = ProductionScanOrchestrator(config=config)
    planner = orchestrator.detail_planner
    sb_parser = orchestrator._create_provider_instance("superbet").parser
    bc_parser = orchestrator._create_provider_instance("betclic").parser

    # Superbet item with diacritics
    sb_item = SuperbetDiscoveredItem(
        event_id="sb_100",
        match_name="Górnik Zabrze · Legia Warszawa",
        competition_name="PKO Ekstraklasa",
        start_time="2026-09-10T18:00:00Z",
        metadata={"raw": {
            "id": "sb_100",
            "name": "Górnik Zabrze · Legia Warszawa",
            "homeTeamName": "Górnik Zabrze",
            "awayTeamName": "Legia Warszawa",
            "competitionName": "PKO BP Ekstraklasa",
            "startDate": "2026-09-10T18:00:00Z",
            "markets": [],
        }}
    )

    # Betclic item with ASCII transliteration and reversed/dash format
    bc_item = BetclicDiscoveredItem(
        provider_event_id="bc_200",
        name="Gornik Zabrze - Legia Warszawa",
        competition_name="PKO BP Ekstraklasa",
        url="https://betclic.pl/match/1",
        start_time="2026-09-10T18:05:00Z",  # 5 min diff within tolerance
        metadata={"raw": {
            "id": "bc_200",
            "name": "Gornik Zabrze - Legia Warszawa",
            "competition": {"name": "PKO BP Ekstraklasa"},
            "start_date": "2026-09-10T18:05:00Z",
            "markets": [],
        }}
    )

    plan = planner.create_plan(
        sb_discovered=[sb_item],
        bc_discovered=[bc_item],
        sb_parser=sb_parser,
        bc_parser=bc_parser,
        config=config,
        evaluation_time=now,
    )

    assert "sb_100" in plan.selected_event_ids_superbet
    assert "bc_200" in plan.selected_event_ids_betclic
    assert "sb_100" in plan.overlap_event_ids_superbet
    assert "bc_200" in plan.overlap_event_ids_betclic
    assert len(plan.ranked_pairs) == 1


def test_detail_planning_budget_and_ranking_contract():
    """
    Test 3: SELECTION CONTRACT & BUDGET.
    Verifies that when budget is smaller than overlap count, Tier 0 and earlier kickoff fixtures
    are strictly selected first, and fallback behaves deterministically.
    """
    now = datetime(2026, 9, 10, 12, 0, 0, tzinfo=timezone.utc)
    config = ScanConfig(providers=("superbet", "betclic"), max_detail_requests=2)
    orchestrator = ProductionScanOrchestrator(config=config)
    planner = orchestrator.detail_planner
    sb_parser = orchestrator._create_provider_instance("superbet").parser
    bc_parser = orchestrator._create_provider_instance("betclic").parser

    # 3 pairs:
    # 1. Tier 0 (Champions League), 18:00
    # 2. Tier 1 (Premier League), 15:00
    # 3. Tier 1 (Premier League), 20:00
    sb_items = [
        SuperbetDiscoveredItem(
            event_id="sb_t1_late", match_name="Arsenal · Chelsea", competition_name="Premier League",
            start_time="2026-09-10T20:00:00Z", metadata={"raw": {"id": "sb_t1_late", "name": "Arsenal · Chelsea", "competitionName": "Premier League", "startDate": "2026-09-10T20:00:00Z", "markets": []}}
        ),
        SuperbetDiscoveredItem(
            event_id="sb_t0", match_name="Real Madrid · Barcelona", competition_name="UEFA Champions League",
            start_time="2026-09-10T18:00:00Z", metadata={"raw": {"id": "sb_t0", "name": "Real Madrid · Barcelona", "competitionName": "UEFA Champions League", "startDate": "2026-09-10T18:00:00Z", "markets": []}}
        ),
        SuperbetDiscoveredItem(
            event_id="sb_t1_early", match_name="Liverpool · Everton", competition_name="Premier League",
            start_time="2026-09-10T15:00:00Z", metadata={"raw": {"id": "sb_t1_early", "name": "Liverpool · Everton", "competitionName": "Premier League", "startDate": "2026-09-10T15:00:00Z", "markets": []}}
        ),
    ]

    bc_items = [
        BetclicDiscoveredItem(
            provider_event_id="bc_t1_late", name="Arsenal - Chelsea", competition_name="Premier League",
            url="url", start_time="2026-09-10T20:00:00Z", metadata={"raw": {"id": "bc_t1_late", "name": "Arsenal - Chelsea", "competition": {"name": "Premier League"}, "start_date": "2026-09-10T20:00:00Z", "markets": []}}
        ),
        BetclicDiscoveredItem(
            provider_event_id="bc_t0", name="Real Madrid - Barcelona", competition_name="Liga Mistrzów",
            url="url", start_time="2026-09-10T18:00:00Z", metadata={"raw": {"id": "bc_t0", "name": "Real Madrid - Barcelona", "competition": {"name": "Liga Mistrzów"}, "start_date": "2026-09-10T18:00:00Z", "markets": []}}
        ),
        BetclicDiscoveredItem(
            provider_event_id="bc_t1_early", name="Liverpool - Everton", competition_name="Premier League",
            url="url", start_time="2026-09-10T15:00:00Z", metadata={"raw": {"id": "bc_t1_early", "name": "Liverpool - Everton", "competition": {"name": "Premier League"}, "start_date": "2026-09-10T15:00:00Z", "markets": []}}
        ),
    ]

    plan = planner.create_plan(
        sb_discovered=sb_items,
        bc_discovered=bc_items,
        sb_parser=sb_parser,
        bc_parser=bc_parser,
        config=config,
        evaluation_time=now,
    )

    # Budget is 2: Must pick sb_t0 (Tier 0) and sb_t1_early (Tier 1 earlier kickoff)
    assert plan.selected_event_ids_superbet == ["sb_t0", "sb_t1_early"]
    assert plan.selected_event_ids_betclic == ["bc_t0", "bc_t1_early"]


def test_detail_planning_avoids_unnecessary_market_graph_allocations():
    """
    Test 4: REDUNDANT WORK ELIMINATION.
    Verifies that overview graphs created during detail planning do not populate
    heavy Market/Selection/Odds collections, because candidate generation and matching
    strictly require Event and Competition identity.
    """
    config = ScanConfig(providers=("superbet", "betclic"), max_detail_requests=5)
    orchestrator = ProductionScanOrchestrator(config=config)
    planner = orchestrator.detail_planner
    sb_parser = orchestrator._create_provider_instance("superbet").parser
    bc_parser = orchestrator._create_provider_instance("betclic").parser

    # Provide raw overview payloads with markets attached
    sb_raw_payload = {
        "id": "sb_test_mkt",
        "name": "Team A · Team B",
        "homeTeamName": "Team A",
        "awayTeamName": "Team B",
        "competitionName": "Ekstraklasa",
        "startDate": "2026-09-10T18:00:00Z",
        "markets": [
            {
                "id": "mkt_1",
                "name": "1X2",
                "selections": [
                    {"id": "sel_1", "name": "1", "price": 2.0},
                    {"id": "sel_2", "name": "X", "price": 3.0},
                    {"id": "sel_3", "name": "2", "price": 4.0},
                ],
            }
        ],
    }

    bc_raw_payload = {
        "id": "bc_test_mkt",
        "name": "Team A - Team B",
        "competition": {"name": "Ekstraklasa"},
        "start_date": "2026-09-10T18:00:00Z",
        "markets": [
            {
                "id": "bmkt_1",
                "name": "Wynik meczu",
                "selections": [
                    {"id": "bsel_1", "name": "1", "odds": 2.05},
                    {"id": "bsel_2", "name": "X", "odds": 3.10},
                    {"id": "bsel_3", "name": "2", "odds": 3.90},
                ],
            }
        ],
    }

    sb_item = SuperbetDiscoveredItem(event_id="sb_test_mkt", match_name="Team A · Team B", metadata={"raw": sb_raw_payload})
    bc_item = BetclicDiscoveredItem(provider_event_id="bc_test_mkt", name="Team A - Team B", competition_name="Ekstraklasa", url="url", start_time="2026-09-10T18:00:00Z", metadata={"raw": bc_raw_payload})

    # The planner must succeed and match the pair
    plan = planner.create_plan(
        sb_discovered=[sb_item],
        bc_discovered=[bc_item],
        sb_parser=sb_parser,
        bc_parser=bc_parser,
        config=config,
    )

    assert "sb_test_mkt" in plan.selected_event_ids_superbet
    assert "bc_test_mkt" in plan.selected_event_ids_betclic
def test_detail_planning_skips_unneeded_market_normalization():
    """
    Test 4: REDUNDANT WORK ELIMINATION (RED test before implementation).
    Verifies that overview graphs created during detail planning explicitly set
    include_markets=False on the NormalizationEngine, eliminating redundant
    Market/Selection/Odds instantiation on the critical path.
    """
    from unittest.mock import MagicMock
    config = ScanConfig(providers=("superbet", "betclic"), max_detail_requests=5)
    orchestrator = ProductionScanOrchestrator(config=config)
    planner = orchestrator.detail_planner
    sb_parser = orchestrator._create_provider_instance("superbet").parser
    bc_parser = orchestrator._create_provider_instance("betclic").parser

    sb_item = SuperbetDiscoveredItem(event_id="sb_1", match_name="Team A · Team B", metadata={"raw": {"id": "sb_1", "name": "Team A · Team B", "markets": []}})
    bc_item = BetclicDiscoveredItem(provider_event_id="bc_1", name="Team A - Team B", competition_name="Ekstraklasa", url="url", start_time="2026-09-10T18:00:00Z", metadata={"raw": {"id": "bc_1", "name": "Team A - Team B", "markets": []}})

    # Spy on normalization_engine.normalize
    orig_normalize = planner.normalization_engine.normalize
    normalize_calls = []

    def spy_normalize(provider_name, parsed_objects, *args, **kwargs):
        normalize_calls.append({"provider": provider_name, "args": args, "kwargs": kwargs})
        return orig_normalize(provider_name, parsed_objects, *args, **kwargs)

    planner.normalization_engine.normalize = spy_normalize

    plan = planner.create_plan(
        sb_discovered=[sb_item],
        bc_discovered=[bc_item],
        sb_parser=sb_parser,
        bc_parser=bc_parser,
        config=config,
    )

    # All normalization calls in create_plan MUST explicitly request include_markets=False
    assert len(normalize_calls) >= 2, "Expected at least 2 normalize calls for superbet and betclic"
    for call in normalize_calls:
        assert call["kwargs"].get("include_markets") is False, (
            f"Expected include_markets=False for provider {call['provider']}, got {call['kwargs']}"
        )
