"""
Stage 24B Benchmark Test Suite: Detail Acquisition Capacity & Market Coverage Evaluation

Benchmarks detail acquisition budgets (max_detail_requests = 15, 30, 50, 85) across:
- Total events discovered
- Overlap events
- Detail requests attempted & successful
- Events with detail data
- Total markets after detail & markets per event
- Provider detail coverage (Betclic & Superbet)
- Final matched events & evaluated markets
- Surebets detected
- Prioritization ordering analysis
"""

from datetime import datetime, timezone, timedelta
from decimal import Decimal
import time
from typing import Any, Dict, List, Set, Tuple
import pytest

from domain.models import Competition, Event
from normalization.engine import NormalizationEngine
from normalization.validation_pipeline import CrossBookmakerValidationPipeline
from orchestration.event_selection import DefaultEventSelectionPolicy
from orchestration.models import ScanConfig, CycleStatus
from orchestration.scan_orchestrator import ProductionScanOrchestrator
from providers.betclic.models import (
    BetclicDiscoveredItem,
    BetclicEvent,
    BetclicMarket,
    BetclicOdds,
    BetclicSelection,
)
from providers.betclic.provider import BetclicProvider
from providers.superbet.models import (
    SuperbetDiscoveredItem,
    SuperbetEvent,
    SuperbetMarket,
    SuperbetOdds,
    SuperbetSelection,
)
from providers.superbet.provider import SuperbetProvider


def _build_overview_market_bc(eid: str) -> Dict[str, Any]:
    return {
        "id": f"bc_m_{eid}_1x2",
        "name": "Wynik meczu",
        "code": "1X2",
        "is_open": True,
        "mainSelections": [
            {"id": f"bc_s_{eid}_1", "name": "1", "code": "HOME", "odds": 2.10, "status": 1},
            {"id": f"bc_s_{eid}_x", "name": "X", "code": "DRAW", "odds": 3.40, "status": 1},
            {"id": f"bc_s_{eid}_2", "name": "2", "code": "AWAY", "odds": 3.50, "status": 1},
        ],
    }


def _build_detail_markets_bc(eid: str) -> List[Dict[str, Any]]:
    return [
        _build_overview_market_bc(eid),
        # BTTS
        {
            "id": f"bc_m_{eid}_btts",
            "name": "Oba zespoły strzelą gola",
            "code": "BTTS",
            "is_open": True,
            "selectionMatrix": [
                {
                    "selections": [
                        {"selectionOneof": {"selection": {"id": f"bc_s_{eid}_btts_y", "name": "Tak", "odds": 1.75, "status": 1}}},
                        {"selectionOneof": {"selection": {"id": f"bc_s_{eid}_btts_n", "name": "Nie", "odds": 2.05, "status": 1}}},
                    ]
                }
            ],
        },
        # TOTALS 2.5
        {
            "id": f"bc_m_{eid}_tot25",
            "name": "Gole Powyżej/Poniżej",
            "code": "TOTALS",
            "is_open": True,
            "selectionMatrix": [
                {
                    "selections": [
                        {"selectionOneof": {"selection": {"id": f"bc_s_{eid}_t25_o", "name": "Powyżej 2,5", "odds": 1.85, "status": 1}}},
                        {"selectionOneof": {"selection": {"id": f"bc_s_{eid}_t25_u", "name": "Poniżej 2,5", "odds": 1.95, "status": 1}}},
                    ]
                },
                {
                    "selections": [
                        {"selectionOneof": {"selection": {"id": f"bc_s_{eid}_t15_o", "name": "Powyżej 1,5", "odds": 1.25, "status": 1}}},
                        {"selectionOneof": {"selection": {"id": f"bc_s_{eid}_t15_u", "name": "Poniżej 1,5", "odds": 3.80, "status": 1}}},
                    ]
                },
            ],
        },
        # DOUBLE CHANCE
        {
            "id": f"bc_m_{eid}_dc",
            "name": "Podwójna Szansa",
            "code": "DOUBLE_CHANCE",
            "is_open": True,
            "selectionMatrix": [
                {
                    "selections": [
                        {"selectionOneof": {"selection": {"id": f"bc_s_{eid}_dc_1x", "name": "1X", "odds": 1.30, "status": 1}}},
                        {"selectionOneof": {"selection": {"id": f"bc_s_{eid}_dc_12", "name": "12", "odds": 1.28, "status": 1}}},
                        {"selectionOneof": {"selection": {"id": f"bc_s_{eid}_dc_x2", "name": "X2", "odds": 1.72, "status": 1}}},
                    ]
                }
            ],
        },
        # DRAW NO BET
        {
            "id": f"bc_m_{eid}_dnb",
            "name": "Remis - Nie Ma Zakładu",
            "code": "DRAW_NO_BET",
            "is_open": True,
            "mainSelections": [
                {"id": f"bc_s_{eid}_dnb_1", "name": "1", "odds": 1.50, "status": 1},
                {"id": f"bc_s_{eid}_dnb_2", "name": "2", "odds": 2.45, "status": 1},
            ],
        },
        # HALF TIME RESULT
        {
            "id": f"bc_m_{eid}_ht",
            "name": "Wynik 1. połowy",
            "code": "HALF_TIME_RESULT",
            "is_open": True,
            "mainSelections": [
                {"id": f"bc_s_{eid}_ht_1", "name": "1", "odds": 2.70, "status": 1},
                {"id": f"bc_s_{eid}_ht_x", "name": "X", "odds": 2.15, "status": 1},
                {"id": f"bc_s_{eid}_ht_2", "name": "2", "odds": 3.80, "status": 1},
            ],
        },
    ]


def _build_overview_market_sb(eid: str) -> Dict[str, Any]:
    return {
        "id": f"sb_m_{eid}_1x2",
        "marketName": "1X2",
        "name": "Wynik meczu",
        "marketId": f"sb_m_{eid}_1x2",
        "outcomes": [
            {"id": f"sb_s_{eid}_1", "name": "1", "outcomeName": "1", "code": "HOME", "price": 2.15, "odds": 2.15},
            {"id": f"sb_s_{eid}_x", "name": "X", "outcomeName": "X", "code": "DRAW", "price": 3.45, "odds": 3.45},
            {"id": f"sb_s_{eid}_2", "name": "2", "outcomeName": "2", "code": "AWAY", "price": 3.45, "odds": 3.45},
        ],
    }


def _build_detail_markets_sb(eid: str) -> List[Dict[str, Any]]:
    return [
        _build_overview_market_sb(eid),
        # BTTS
        {
            "id": f"sb_m_{eid}_btts",
            "marketName": "Obie drużyny strzelą",
            "name": "Obie drużyny strzelą",
            "outcomes": [
                {"id": f"sb_s_{eid}_btts_y", "name": "Tak", "outcomeName": "Tak", "price": 1.78, "odds": 1.78},
                {"id": f"sb_s_{eid}_btts_n", "name": "Nie", "outcomeName": "Nie", "price": 2.08, "odds": 2.08},
            ],
        },
        # TOTALS 2.5
        {
            "id": f"sb_m_{eid}_tot25",
            "marketName": "Liczba goli",
            "name": "Liczba goli",
            "line": 2.5,
            "outcomes": [
                {"id": f"sb_s_{eid}_t25_o", "name": "Powyżej 2.5", "outcomeName": "Powyżej 2.5", "price": 1.90, "odds": 1.90},
                {"id": f"sb_s_{eid}_t25_u", "name": "Poniżej 2.5", "outcomeName": "Poniżej 2.5", "price": 1.92, "odds": 1.92},
            ],
        },
        # TOTALS 1.5
        {
            "id": f"sb_m_{eid}_tot15",
            "marketName": "Liczba goli",
            "name": "Liczba goli",
            "line": 1.5,
            "outcomes": [
                {"id": f"sb_s_{eid}_t15_o", "name": "Powyżej 1.5", "outcomeName": "Powyżej 1.5", "price": 1.28, "odds": 1.28},
                {"id": f"sb_s_{eid}_t15_u", "name": "Poniżej 1.5", "outcomeName": "Poniżej 1.5", "price": 3.70, "odds": 3.70},
            ],
        },
        # DOUBLE CHANCE
        {
            "id": f"sb_m_{eid}_dc",
            "marketName": "Podwójna szansa",
            "name": "Podwójna szansa",
            "outcomes": [
                {"id": f"sb_s_{eid}_dc_1x", "name": "1X", "outcomeName": "1X", "price": 1.32, "odds": 1.32},
                {"id": f"sb_s_{eid}_dc_12", "name": "12", "outcomeName": "12", "price": 1.26, "odds": 1.26},
                {"id": f"sb_s_{eid}_dc_x2", "name": "X2", "outcomeName": "X2", "price": 1.70, "odds": 1.70},
            ],
        },
        # DRAW NO BET
        {
            "id": f"sb_m_{eid}_dnb",
            "marketName": "Zakład bez remisu",
            "name": "Zakład bez remisu",
            "outcomes": [
                {"id": f"sb_s_{eid}_dnb_1", "name": "1", "outcomeName": "1", "price": 1.52, "odds": 1.52},
                {"id": f"sb_s_{eid}_dnb_2", "name": "2", "outcomeName": "2", "price": 2.40, "odds": 2.40},
            ],
        },
        # HALF TIME RESULT
        {
            "id": f"sb_m_{eid}_ht",
            "marketName": "1. połowa - wynik",
            "name": "1. połowa - wynik",
            "outcomes": [
                {"id": f"sb_s_{eid}_ht_1", "name": "1", "outcomeName": "1", "price": 2.65, "odds": 2.65},
                {"id": f"sb_s_{eid}_ht_x", "name": "X", "outcomeName": "X", "price": 2.20, "odds": 2.20},
                {"id": f"sb_s_{eid}_ht_2", "name": "2", "outcomeName": "2", "price": 3.75, "odds": 3.75},
            ],
        },
    ]


def _create_benchmark_dataset(
    num_overlapping: int = 85,
    num_bc_extra: int = 15,
    num_sb_extra: int = 100,
) -> Tuple[List[BetclicDiscoveredItem], List[SuperbetDiscoveredItem], Dict[str, Any], Dict[str, Any]]:
    """Creates a deterministic benchmark dataset matching the real scan population."""
    bc_items: List[BetclicDiscoveredItem] = []
    sb_items: List[SuperbetDiscoveredItem] = []
    bc_detail_map: Dict[str, Any] = {}
    sb_detail_map: Dict[str, Any] = {}

    competitions = [
        ("Premier League", "Tier 0"),
        ("LaLiga", "Tier 0"),
        ("Serie A", "Tier 0"),
        ("Bundesliga", "Tier 0"),
        ("Ekstraklasa", "Tier 0"),
        ("Champions League", "Tier 0"),
        ("Championship", "Tier 1"),
        ("Segunda Division", "Tier 1"),
        ("ME U21", "Tier 2"),
        ("Brazylia Serie A", "Tier 2"),
    ]

    now_utc = datetime.now(timezone.utc)
    base_time = int(now_utc.timestamp()) + 7200  # Start 2 hours from now

    for i in range(num_overlapping):
        comp_name, tier = competitions[i % len(competitions)]
        kickoff_ts = base_time + (i * 1800)  # Each match starts 30 mins later
        kickoff_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(kickoff_ts))
        team_h = f"Team H{i}"
        team_a = f"Team A{i}"
        match_name = f"{team_h} vs {team_a}"
        eid = f"fix_{i:03d}"

        # Betclic Discovered Item (Overview payload)
        bc_item = BetclicDiscoveredItem(
            provider_event_id=f"bc_{eid}",
            name=match_name,
            competition_name=comp_name,
            url=f"https://www.betclic.pl/events/bc_{eid}",
            start_time=kickoff_iso,
            metadata={
                "raw": {
                    "id": f"bc_{eid}",
                    "name": match_name,
                    "competition": comp_name,
                    "start_date": kickoff_iso,
                    "markets": [_build_overview_market_bc(f"bc_{eid}")],
                }
            },
        )
        bc_items.append(bc_item)
        bc_detail_map[f"bc_{eid}"] = {
            "id": f"bc_{eid}",
            "name": match_name,
            "competition": {"id": "1", "name": comp_name},
            "contestants": [{"name": team_h}, {"name": team_a}],
            "matchDateUtc": kickoff_iso,
            "subCategories": [{"name": "Główne", "markets": _build_detail_markets_bc(f"bc_{eid}")}],
        }

        # Superbet Discovered Item (Overview payload)
        sb_item = SuperbetDiscoveredItem(
            event_id=f"sb_{eid}",
            match_name=match_name,
            competition_name=comp_name,
            url=f"https://superbet.pl/event/sb_{eid}",
            start_time=kickoff_iso,
            metadata={
                "raw": {
                    "id": f"sb_{eid}",
                    "name": match_name,
                    "competitionName": comp_name,
                    "matchDate": kickoff_iso,
                    "markets": [_build_overview_market_sb(f"sb_{eid}")],
                }
            },
        )
        sb_items.append(sb_item)
        sb_detail_map[f"sb_{eid}"] = {
            "id": f"sb_{eid}",
            "name": match_name,
            "competitionName": comp_name,
            "matchDate": kickoff_iso,
            "markets": _build_detail_markets_sb(f"sb_{eid}"),
        }

    # Non-overlapping items
    future_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(base_time + 86400))
    for j in range(num_bc_extra):
        eid = f"bc_extra_{j:03d}"
        bc_items.append(
            BetclicDiscoveredItem(
                provider_event_id=eid,
                name=f"BC NonOver {j} vs Other",
                competition_name="Ekstraklasa",
                url=f"https://www.betclic.pl/events/{eid}",
                start_time=future_iso,
                metadata={"raw": {"id": eid, "name": f"BC NonOver {j} vs Other", "competition": "Ekstraklasa", "start_date": future_iso, "markets": [_build_overview_market_bc(eid)]}},
            )
        )

    for k in range(num_sb_extra):
        eid = f"sb_extra_{k:03d}"
        sb_items.append(
            SuperbetDiscoveredItem(
                event_id=eid,
                match_name=f"SB NonOver {k} vs Other",
                competition_name="LaLiga",
                url=f"https://superbet.pl/event/{eid}",
                start_time=future_iso,
                metadata={"raw": {"id": eid, "name": f"SB NonOver {k} vs Other", "competitionName": "LaLiga", "matchDate": future_iso, "markets": [_build_overview_market_sb(eid)]}},
            )
        )

    return bc_items, sb_items, bc_detail_map, sb_detail_map


def _run_benchmark_cycle(max_detail_requests: int) -> Dict[str, Any]:
    """Runs a complete isolated orchestration scan cycle with mock providers at a given detail budget."""
    bc_items, sb_items, bc_detail_map, sb_detail_map = _create_benchmark_dataset(
        num_overlapping=85,
        num_bc_extra=15,
        num_sb_extra=100,
    )

    bc_provider = BetclicProvider()
    bc_provider.set_mock_discovery_payload([it.metadata["raw"] for it in bc_items])

    sb_provider = SuperbetProvider()
    sb_provider.set_mock_discovery_payload([it.metadata["raw"] for it in sb_items])

    def _mock_bc_fetch(item):
        if bc_provider.fetcher._is_event_selected_for_detail(item) or (item.provider_event_id in bc_provider.betclic_config.selected_event_ids):
            bc_provider.fetcher.stats["detail_requests_attempted"] += 1
            bc_provider.fetcher.stats["detail_requests_successful"] += 1
            bc_provider.fetcher.stats["events_selected_for_detail"] += 1
            return bc_detail_map.get(item.provider_event_id, item.metadata.get("raw"))
        else:
            bc_provider.fetcher.stats["overview_payloads_used"] += 1
            return item.metadata.get("raw")

    def _mock_sb_fetch(item):
        if sb_provider.fetcher._is_event_selected_for_detail(item) or (item.event_id in sb_provider.superbet_config.selected_event_ids):
            sb_provider.fetcher.stats["detail_requests_attempted"] += 1
            sb_provider.fetcher.stats["detail_requests_successful"] += 1
            sb_provider.fetcher.stats["events_selected_for_detail"] += 1
            return sb_detail_map.get(item.event_id, item.metadata.get("raw"))
        else:
            sb_provider.fetcher.stats["overview_payloads_used"] += 1
            return item.metadata.get("raw")

    bc_provider.set_mock_fetch_provider(_mock_bc_fetch)
    sb_provider.set_mock_fetch_provider(_mock_sb_fetch)

    config = ScanConfig(
        providers=("superbet", "betclic"),
        max_detail_requests=max_detail_requests,
        enable_reconciliation=False,
    )

    orchestrator = ProductionScanOrchestrator(config=config)

    t0 = time.perf_counter()
    result = orchestrator.run_scan_cycle(
        providers={"betclic": bc_provider, "superbet": sb_provider}
    )
    duration = time.perf_counter() - t0

    # Extract metrics
    bc_metrics = bc_provider.acquisition_metrics
    sb_metrics = sb_provider.acquisition_metrics

    bc_detail_attempted = bc_metrics.get("detail_requests_attempted", 0)
    bc_detail_success = bc_metrics.get("detail_requests_successful", 0)
    sb_detail_attempted = sb_metrics.get("detail_requests_attempted", 0)
    sb_detail_success = sb_metrics.get("detail_requests_successful", 0)

    # Markets after detail
    bc_markets = bc_metrics.get("markets_acquired", 0)
    sb_markets = sb_metrics.get("markets_acquired", 0)
    total_markets_acquired = bc_markets + sb_markets

    # Matched and evaluated
    matched_events = result.matched_events_count
    evaluated_markets = result.evaluated_markets_total or result.markets_evaluated_count
    surebets = result.detected_opportunities_count

    bc_coverage_pct = round((bc_detail_success / 85) * 100.0, 1) if 85 > 0 else 0.0
    sb_coverage_pct = round((sb_detail_success / 85) * 100.0, 1) if 85 > 0 else 0.0

    return {
        "budget_limit": max_detail_requests,
        "total_events_discovered": len(bc_items) + len(sb_items),
        "overlap_events": 85,
        "detail_requests_attempted_bc": bc_detail_attempted,
        "detail_requests_successful_bc": bc_detail_success,
        "detail_requests_attempted_sb": sb_detail_attempted,
        "detail_requests_successful_sb": sb_detail_success,
        "total_detail_requests": bc_detail_attempted + sb_detail_attempted,
        "events_with_detail_data": bc_detail_success,
        "total_markets_after_detail": total_markets_acquired,
        "markets_per_overlap_event": round(total_markets_acquired / 85, 1),
        "scan_duration_sec": round(duration, 3),
        "http_requests_estimate": (len(bc_items) + len(sb_items) > 0) + bc_detail_attempted + sb_detail_attempted,
        "betclic_detail_coverage_pct": bc_coverage_pct,
        "superbet_detail_coverage_pct": sb_coverage_pct,
        "final_matched_events": matched_events,
        "final_evaluated_markets": evaluated_markets,
        "surebets_found": surebets,
    }


def test_stage24b_benchmark_all_budgets():
    """Runs and asserts telemetry across all 4 production detail budgets: 15, 30, 50, 85."""
    budgets = [15, 30, 50, 85]
    results = {}

    for b in budgets:
        res = _run_benchmark_cycle(max_detail_requests=b)
        results[b] = res
        assert res["detail_requests_successful_bc"] == b
        assert res["detail_requests_successful_sb"] == b
        assert res["final_matched_events"] == 85

    # Verification: higher detail budget strictly scales acquired and parsed markets
    assert results[30]["total_markets_after_detail"] > results[15]["total_markets_after_detail"]
    assert results[50]["total_markets_after_detail"] > results[30]["total_markets_after_detail"]
    assert results[85]["total_markets_after_detail"] > results[50]["total_markets_after_detail"]

    import json
    print("\n=== STAGE 24B BENCHMARK RESULTS ===")
    print(json.dumps(results, indent=2))

    # Coverage progression
    assert results[15]["betclic_detail_coverage_pct"] == pytest.approx(17.6, abs=0.5)
    assert results[30]["betclic_detail_coverage_pct"] == pytest.approx(35.3, abs=0.5)
    assert results[50]["betclic_detail_coverage_pct"] == pytest.approx(58.8, abs=0.5)
    assert results[85]["betclic_detail_coverage_pct"] == pytest.approx(100.0, abs=0.5)


def test_prioritization_mechanics_and_u21_bias_evaluation():
    """Evaluates how (is_overlap, tier, kickoff_ts, name_str, eid) prioritizes matches."""
    policy = DefaultEventSelectionPolicy()

    # Create 3 Tier 0 matches (Premier League, LaLiga, Serie A) with kickoffs in 48h, 72h, 96h
    # Create 3 Tier 2 matches (ME U21) with kickoffs in 1h, 2h, 3h
    items = [
        # Tier 0 marquee matches (starting later)
        {"id": "pl_1", "competition": "Premier League", "start_time": "2026-08-27T18:00:00Z", "name": "Arsenal vs Chelsea"},
        {"id": "pl_2", "competition": "LaLiga", "start_time": "2026-08-28T20:00:00Z", "name": "Real Madrid vs Barcelona"},
        {"id": "pl_3", "competition": "Serie A", "start_time": "2026-08-29T19:00:00Z", "name": "Juventus vs Inter"},
        # Tier 2 lower matches (starting today / in 2h)
        {"id": "u21_1", "competition": "ME U21", "start_time": "2026-08-25T15:00:00Z", "name": "Polska U21 vs Niemcy U21"},
        {"id": "u21_2", "competition": "ME U21", "start_time": "2026-08-25T16:00:00Z", "name": "Hiszpania U21 vs Wlochy U21"},
        {"id": "u21_3", "competition": "ME U21", "start_time": "2026-08-25T17:00:00Z", "name": "Francja U21 vs Anglia U21"},
    ]

    all_ids = {it["id"] for it in items}

    # Case A: When all 6 are overlapping and budget = 3:
    # Tier 0 (tier=0) MUST strictly beat Tier 2 (tier=2) despite kickoff being 2 days later!
    prio_top = policy.prioritize_detail_events(
        discovered_items=items,
        overlap_event_ids=all_ids,
        max_detail_requests=3,
    )
    assert set(prio_top.selected_event_ids) == {"pl_1", "pl_2", "pl_3"}
    assert "u21_1" not in prio_top.selected_event_ids

    # Case B: Stage 24C Smart Prioritization maps "Anglia 1" to Tier 0, so it outranks U21 (Tier 2):
    items_mapped = [
        {"id": "pl_mapped_1", "competition": "Anglia 1", "start_time": "2026-08-27T18:00:00Z", "name": "Arsenal vs Chelsea"},
        {"id": "u21_1", "competition": "ME U21", "start_time": "2026-08-25T15:00:00Z", "name": "Polska U21 vs Niemcy U21"},
    ]
    prio_mapped = policy.prioritize_detail_events(
        discovered_items=items_mapped,
        overlap_event_ids={"pl_mapped_1", "u21_1"},
        max_detail_requests=1,
    )
    # Stage 24C fix: Tier 0 ("Anglia 1") wins over Tier 2 ("ME U21")
    assert prio_mapped.selected_event_ids == ["pl_mapped_1"]

    # Case C: Truly unmapped lower-tier competitions both fall to Tier 2, where kickoff proximity tie-breaks:
    items_unmapped = [
        {"id": "minor_later", "competition": "Liga Niezdefiniowana XYZ", "start_time": "2026-08-27T18:00:00Z", "name": "Team A vs Team B"},
        {"id": "u21_1", "competition": "ME U21", "start_time": "2026-08-25T15:00:00Z", "name": "Polska U21 vs Niemcy U21"},
    ]
    prio_unmapped = policy.prioritize_detail_events(
        discovered_items=items_unmapped,
        overlap_event_ids={"minor_later", "u21_1"},
        max_detail_requests=1,
    )
    assert prio_unmapped.selected_event_ids == ["u21_1"]
