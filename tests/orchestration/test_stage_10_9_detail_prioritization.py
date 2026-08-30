"""
Stage 10.9: Matched-Event Detail Prioritization Test Suite

Verifies:
1. Overlap events outrank non-overlap events
2. Preferred competition ranking remains deterministic
3. Kickoff proximity affects ordering
4. max_detail_requests is enforced
5. No duplicate event IDs are selected
6. Deterministic ordering across shuffled input
7. Non-overlap fallback works when fewer overlap candidates exist
8. Provider failure does not crash orchestration
9. Existing safety vetoes remain intact
10. Existing Stage 10.8 behavior remains compatible
"""

import random
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List
import pytest

from domain.models import Competition, Event
from normalization.base_normalizer import NormalizedGraph
from orchestration.event_selection import (
    DefaultEventSelectionPolicy,
    DetailPrioritizationResult,
)
from orchestration.models import ScanConfig, CycleStatus
from orchestration.scan_orchestrator import ProductionScanOrchestrator
from providers.base.provider_state import ProviderState
from providers.betclic.models import (
    BetclicDiscoveredItem,
    BetclicEvent,
    BetclicMarket,
    BetclicSelection,
    BetclicOdds,
)
from providers.betclic.provider import BetclicProvider
from providers.superbet.models import (
    SuperbetDiscoveredItem,
    SuperbetEvent,
    SuperbetMarket,
    SuperbetSelection,
    SuperbetOdds,
)
from providers.superbet.provider import SuperbetProvider


def _make_bc_item(
    event_id: str,
    name: str,
    comp_name: str,
    kickoff: str,
) -> BetclicDiscoveredItem:
    return BetclicDiscoveredItem(
        provider_event_id=event_id,
        name=name,
        competition_name=comp_name,
        url=f"https://www.betclic.pl/events/{event_id}",
        start_time=kickoff,
        metadata={
            "relative_url": f"/events/{event_id}",
            "raw": {
                "id": event_id,
                "name": name,
                "competition": comp_name,
                "start_date": kickoff,
                "markets": [
                    {
                        "id": f"{event_id}_m1",
                        "name": "Wynik meczu",
                        "code": "1X2",
                        "mainSelections": [
                            {"id": f"{event_id}_s1", "name": "1", "code": "HOME", "odds": 2.10, "status": 1},
                            {"id": f"{event_id}_s2", "name": "X", "code": "DRAW", "odds": 3.40, "status": 1},
                            {"id": f"{event_id}_s3", "name": "2", "code": "AWAY", "odds": 3.50, "status": 1},
                        ],
                    }
                ],
            },
        },
    )


def _make_sb_item(
    event_id: str,
    name: str,
    comp_name: str,
    kickoff: str,
) -> SuperbetDiscoveredItem:
    return SuperbetDiscoveredItem(
        provider_event_id=event_id,
        name=name,
        competition_name=comp_name,
        url=f"https://superbet.pl/event/{event_id}",
        start_time=kickoff,
        metadata={
            "raw": {
                "id": event_id,
                "name": name,
                "competition": comp_name,
                "start_date": kickoff,
            }
        },
    )


def test_1_overlap_events_outrank_non_overlap_events():
    """Test 1: Overlap events outrank non-overlap events regardless of standard tier."""
    policy = DefaultEventSelectionPolicy()
    now_dt = datetime.now(timezone.utc)

    it1 = _make_bc_item("bc_1", "Arsenal vs Chelsea", "Premier League", (now_dt + timedelta(hours=2)).isoformat())
    it2 = _make_bc_item("bc_2", "Lechia vs Radomiak", "Polska 2 Liga", (now_dt + timedelta(hours=5)).isoformat())
    it3 = _make_bc_item("bc_3", "Wieczysta vs Hutnik", "Polska 3 Liga", (now_dt + timedelta(hours=1)).isoformat())

    overlap_ids = {"bc_2"}

    res = policy.prioritize_detail_events(
        discovered_items=[it1, it2, it3],
        overlap_event_ids=overlap_ids,
        max_detail_requests=2,
    )

    assert res.events_selected == 2
    assert res.events_overlap_selected == 1
    assert res.selected_event_ids[0] == "bc_2"
    assert res.selected_event_ids[1] == "bc_1"


def test_2_preferred_competition_ranking_remains_deterministic():
    """Test 2: Within overlapping events, preferred competitions outrank standard competitions."""
    policy = DefaultEventSelectionPolicy()
    now_dt = datetime.now(timezone.utc)

    it_pl = _make_bc_item("bc_pl", "Liverpool vs Everton", "Premier League", (now_dt + timedelta(hours=3)).isoformat())
    it_ll = _make_bc_item("bc_ll", "Real Madrid vs Barcelona", "LaLiga", (now_dt + timedelta(hours=4)).isoformat())
    it_std = _make_bc_item("bc_std", "Wisla vs Cracovia", "Polska 2 Liga", (now_dt + timedelta(hours=1)).isoformat())

    overlap_ids = {"bc_pl", "bc_ll", "bc_std"}

    res = policy.prioritize_detail_events(
        discovered_items=[it_std, it_pl, it_ll],
        overlap_event_ids=overlap_ids,
        max_detail_requests=3,
        preferred_competitions=("Premier League", "LaLiga"),
    )

    assert res.selected_event_ids[:2] == ["bc_pl", "bc_ll"]
    assert res.selected_event_ids[2] == "bc_std"


def test_3_kickoff_proximity_affects_ordering():
    """Test 3: Within same tier and overlap status, earlier kickoffs are ranked higher."""
    policy = DefaultEventSelectionPolicy()
    now_dt = datetime.now(timezone.utc)

    it_later = _make_bc_item("bc_later", "Man City vs Spurs", "Premier League", (now_dt + timedelta(hours=6)).isoformat())
    it_earlier = _make_bc_item("bc_earlier", "Arsenal vs Chelsea", "Premier League", (now_dt + timedelta(hours=2)).isoformat())

    overlap_ids = {"bc_later", "bc_earlier"}

    res = policy.prioritize_detail_events(
        discovered_items=[it_later, it_earlier],
        overlap_event_ids=overlap_ids,
        max_detail_requests=2,
    )

    assert res.selected_event_ids == ["bc_earlier", "bc_later"]


def test_4_max_detail_requests_is_enforced():
    """Test 4: max_detail_requests strictly caps the selected IDs."""
    policy = DefaultEventSelectionPolicy()
    now_dt = datetime.now(timezone.utc)

    items = [
        _make_bc_item(f"bc_{i}", f"Team A{i} vs Team B{i}", "Premier League", (now_dt + timedelta(hours=i)).isoformat())
        for i in range(1, 15)
    ]
    overlap_ids = {f"bc_{i}" for i in range(1, 15)}

    res = policy.prioritize_detail_events(
        discovered_items=items,
        overlap_event_ids=overlap_ids,
        max_detail_requests=5,
    )

    assert res.events_selected == 5
    assert len(res.selected_event_ids) == 5
    assert res.candidates_available == 14
    assert res.candidates_overlap == 14
    assert res.events_overlap_selected == 5
    assert res.overlap_selection_rate == 1.0


def test_5_no_duplicate_event_ids_are_selected():
    """Test 5: Duplicate event IDs in input are deduplicated."""
    policy = DefaultEventSelectionPolicy()
    now_dt = datetime.now(timezone.utc)

    it1 = _make_bc_item("bc_1", "Arsenal vs Chelsea", "Premier League", (now_dt + timedelta(hours=2)).isoformat())
    it1_dup = _make_bc_item("bc_1", "Arsenal vs Chelsea", "Premier League", (now_dt + timedelta(hours=2)).isoformat())
    it2 = _make_bc_item("bc_2", "Liverpool vs Everton", "Premier League", (now_dt + timedelta(hours=3)).isoformat())

    res = policy.prioritize_detail_events(
        discovered_items=[it1, it1_dup, it2],
        overlap_event_ids={"bc_1", "bc_2"},
        max_detail_requests=10,
    )

    assert len(res.selected_event_ids) == 2
    assert res.selected_event_ids == ["bc_1", "bc_2"]


def test_6_deterministic_ordering_across_shuffled_input():
    """Test 6: Shuffling input order produces identical ranking and selection output."""
    policy = DefaultEventSelectionPolicy()
    now_dt = datetime.now(timezone.utc)

    items = [
        _make_bc_item(f"bc_{i}", f"Team A{i} vs Team B{i}", "Premier League" if i % 2 == 0 else "Serie A", (now_dt + timedelta(hours=i)).isoformat())
        for i in range(1, 12)
    ]
    overlap_ids = {"bc_2", "bc_5", "bc_8"}

    res_orig = policy.prioritize_detail_events(
        discovered_items=items,
        overlap_event_ids=overlap_ids,
        max_detail_requests=5,
    )

    for seed in (42, 123, 999):
        shuffled = list(items)
        random.Random(seed).shuffle(shuffled)
        res_shuffled = policy.prioritize_detail_events(
            discovered_items=shuffled,
            overlap_event_ids=overlap_ids,
            max_detail_requests=5,
        )
        assert res_shuffled.selected_event_ids == res_orig.selected_event_ids
        assert res_shuffled.ranked_event_ids == res_orig.ranked_event_ids


def test_7_non_overlap_fallback_works_when_fewer_overlap_candidates_exist():
    """Test 7: If overlap candidates < max_detail_requests, remaining slots are filled by fallback."""
    policy = DefaultEventSelectionPolicy()
    now_dt = datetime.now(timezone.utc)

    items = [
        _make_bc_item("bc_1", "Match 1", "Premier League", (now_dt + timedelta(hours=1)).isoformat()),
        _make_bc_item("bc_2", "Match 2", "Premier League", (now_dt + timedelta(hours=2)).isoformat()),
        _make_bc_item("bc_3", "Match 3", "LaLiga", (now_dt + timedelta(hours=3)).isoformat()),
        _make_bc_item("bc_4", "Match 4", "Serie A", (now_dt + timedelta(hours=4)).isoformat()),
        _make_bc_item("bc_5", "Match 5", "Bundesliga", (now_dt + timedelta(hours=5)).isoformat()),
    ]
    overlap_ids = {"bc_4", "bc_5"}

    res = policy.prioritize_detail_events(
        discovered_items=items,
        overlap_event_ids=overlap_ids,
        max_detail_requests=4,
    )

    assert res.events_selected == 4
    assert res.events_overlap_selected == 2
    assert res.overlap_selection_rate == 0.5
    assert res.selected_event_ids[:2] == ["bc_4", "bc_5"]
    assert res.selected_event_ids[2:] == ["bc_1", "bc_2"]


def test_8_provider_failure_does_not_crash_orchestration():
    """Test 8: Failure of one provider during discovery or fetch degrades gracefully."""
    config = ScanConfig(
        providers=("superbet", "betclic"),
        selection_mode="SELECTED",
        max_detail_requests=5,
    )
    orchestrator = ProductionScanOrchestrator(config=config)

    bc = BetclicProvider()
    def failing_discover():
        raise RuntimeError("Simulated network timeout during Betclic discovery")
    bc.discover = failing_discover

    sb = SuperbetProvider()
    sb.set_mock_discovery_payload([
        {"id": "sb_1", "name": "Arsenal vs Chelsea", "competition": "Premier League", "start_date": "2026-08-18T20:00:00Z"}
    ])
    sb.set_mock_fetch_provider(lambda item: {
        "id": "sb_1",
        "name": "Arsenal vs Chelsea",
        "competition": "Premier League",
        "start_date": "2026-08-18T20:00:00Z",
        "markets": [],
    })

    result = orchestrator.run_scan_cycle(providers={"superbet": sb, "betclic": bc})

    assert result.cycle_status in (CycleStatus.PARTIAL, CycleStatus.FAILED)
    assert result.provider_results["betclic"].status == ProviderState.FAILED
    assert result.provider_results["superbet"].status == ProviderState.COMPLETED
    assert len(result.errors) > 0


def test_9_existing_safety_vetoes_remain_intact():
    """Test 9: Cross-bookmaker vetoes (e.g. youth / reserve / women) reject candidates during pre-matching."""
    orchestrator = ProductionScanOrchestrator(
        config=ScanConfig(selection_mode="SELECTED", max_detail_requests=5)
    )

    sb = SuperbetProvider()
    sb.set_mock_discovery_payload([
        {"id": "sb_youth", "name": "Arsenal U19 vs Chelsea U19", "competition": "UEFA Youth League", "start_date": "2026-08-18T14:00:00Z"}
    ])
    sb.set_mock_fetch_provider(lambda item: {
        "id": "sb_youth",
        "name": "Arsenal U19 vs Chelsea U19",
        "competition": "UEFA Youth League",
        "start_date": "2026-08-18T14:00:00Z",
        "markets": [
            {
                "id": "m_sb_1",
                "name": "Wynik meczu",
                "code": "1X2",
                "selections": [
                    {"id": "s1", "name": "1", "code": "HOME", "odds": 2.0},
                    {"id": "s2", "name": "X", "code": "DRAW", "odds": 3.5},
                    {"id": "s3", "name": "2", "code": "AWAY", "odds": 3.5},
                ],
            }
        ],
    })

    bc = BetclicProvider()
    bc.set_mock_discovery_payload([
        {
            "id": "bc_senior",
            "name": "Arsenal - Chelsea",
            "competition": "Premier League",
            "start_date": "2026-08-18T14:00:00Z",
            "metadata": {
                "raw": {
                    "id": "bc_senior",
                    "name": "Arsenal - Chelsea",
                    "competition": "Premier League",
                    "start_date": "2026-08-18T14:00:00Z",
                    "market": {
                        "id": "bc_m1",
                        "name": "Wynik meczu",
                        "code": "1X2",
                        "mainSelections": [
                            {"id": "bcs1", "name": "1", "code": "HOME", "odds": 2.0, "status": 1},
                            {"id": "bcs2", "name": "X", "code": "DRAW", "odds": 3.5, "status": 1},
                            {"id": "bcs3", "name": "2", "code": "AWAY", "odds": 3.5, "status": 1},
                        ],
                    },
                }
            },
        }
    ])
    bc.set_mock_fetch_provider(lambda item: {
        "id": "bc_senior",
        "name": "Arsenal - Chelsea",
        "competition": "Premier League",
        "start_date": "2026-08-18T14:00:00Z",
        "markets": [],
    })

    result = orchestrator.run_scan_cycle(providers={"superbet": sb, "betclic": bc})

    assert result.resource_metrics.detail_candidates_overlap == 0
    assert result.matched_events_count == 0


def test_10_stage_10_8_multi_market_matching_compatible_with_prioritization():
    """Test 10: Prioritized overlapping event fetches multi-market detail and matches markets cleanly."""
    orchestrator = ProductionScanOrchestrator(
        config=ScanConfig(
            selection_mode="SELECTED",
            max_detail_requests=2,
            preferred_competitions=("Premier League",),
        )
    )

    sb = SuperbetProvider()
    sb.set_mock_discovery_payload([
        {"id": "sb_100", "name": "Arsenal - Chelsea", "competition": "Premier League", "start_date": "2026-08-18T20:00:00Z"},
        {"id": "sb_200", "name": "Osasuna - Valencia", "competition": "LaLiga", "start_date": "2026-08-18T21:00:00Z"},
    ])
    sb.set_mock_fetch_provider(lambda item: {
        "id": getattr(item, "event_id", getattr(item, "provider_event_id", "")),
        "name": getattr(item, "match_name", getattr(item, "name", "")),
        "competition": getattr(item, "competition_name", ""),
        "start_date": getattr(item, "start_time", ""),
        "markets": [
            {
                "id": f"{getattr(item, 'event_id', getattr(item, 'provider_event_id', ''))}_m_1x2",
                "name": "Wynik meczu",
                "code": "1X2",
                "selections": [
                    {"id": "sb_s1", "name": "1", "code": "HOME", "odds": 2.10},
                    {"id": "sb_s2", "name": "X", "code": "DRAW", "odds": 3.40},
                    {"id": "sb_s3", "name": "2", "code": "AWAY", "odds": 3.60},
                ],
            },
            {
                "id": f"{getattr(item, 'event_id', getattr(item, 'provider_event_id', ''))}_m_totals",
                "name": "Liczba goli",
                "code": "TOTALS",
                "selections": [
                    {"id": "sb_s4", "name": "Powyżej 2.5", "code": "OVER", "odds": 1.85, "handicap": 2.5},
                    {"id": "sb_s5", "name": "Poniżej 2.5", "code": "UNDER", "odds": 1.95, "handicap": 2.5},
                ],
            },
            {
                "id": f"{getattr(item, 'event_id', getattr(item, 'provider_event_id', ''))}_m_btts",
                "name": "Obie drużyny strzelą",
                "code": "BTTS",
                "selections": [
                    {"id": "sb_s6", "name": "Tak", "code": "YES", "odds": 1.70},
                    {"id": "sb_s7", "name": "Nie", "code": "NO", "odds": 2.10},
                ],
            },
        ],
    })

    bc = BetclicProvider()
    bc.set_mock_discovery_payload([
        {
            "id": "bc_100",
            "name": "Arsenal - Chelsea",
            "competition": "Premier League",
            "start_date": "2026-08-18T20:00:00Z",
            "metadata": {
                "raw": {
                    "id": "bc_100",
                    "name": "Arsenal - Chelsea",
                    "competition": "Premier League",
                    "start_date": "2026-08-18T20:00:00Z",
                    "market": {
                        "id": "bc_m_1x2_ov",
                        "name": "Wynik meczu",
                        "code": "1X2",
                        "mainSelections": [
                            {"id": "bcs1", "name": "1", "code": "HOME", "odds": 2.15, "status": 1},
                            {"id": "bcs2", "name": "X", "code": "DRAW", "odds": 3.35, "status": 1},
                            {"id": "bcs3", "name": "2", "code": "AWAY", "odds": 3.55, "status": 1},
                        ],
                    },
                }
            },
        },
        {
            "id": "bc_psg",
            "name": "PSG - Marseille",
            "competition": "Ligue 1",
            "start_date": "2026-08-18T21:00:00Z",
            "metadata": {
                "raw": {
                    "id": "bc_psg",
                    "name": "PSG - Marseille",
                    "competition": "Ligue 1",
                    "start_date": "2026-08-18T21:00:00Z",
                    "market": {
                        "id": "bc_m_psg_1x2",
                        "name": "Wynik meczu",
                        "code": "1X2",
                        "mainSelections": [
                            {"id": "bcs4", "name": "1", "code": "HOME", "odds": 1.50, "status": 1},
                            {"id": "bcs5", "name": "X", "code": "DRAW", "odds": 4.20, "status": 1},
                            {"id": "bcs6", "name": "2", "code": "AWAY", "odds": 6.00, "status": 1},
                        ],
                    },
                }
            },
        },
    ])

    def bc_fetch(item):
        if item.provider_event_id == "bc_100":
            return {
                "id": "bc_100",
                "name": "Arsenal - Chelsea",
                "competition": "Premier League",
                "start_date": "2026-08-18T20:00:00Z",
                "markets": [
                    {
                        "id": "bc_100_m_1x2",
                        "name": "Wynik meczu",
                        "code": "1X2",
                        "is_open": True,
                        "mainSelections": [
                            {"id": "bc_s1", "name": "1", "code": "HOME", "odds": 2.15, "status": 1},
                            {"id": "bc_s2", "name": "X", "code": "DRAW", "odds": 3.35, "status": 1},
                            {"id": "bc_s3", "name": "2", "code": "AWAY", "odds": 3.55, "status": 1},
                        ],
                    },
                    {
                        "id": "bc_100_m_totals",
                        "name": "Liczba goli",
                        "code": "TOTALS",
                        "is_open": True,
                        "selections": [
                            {"id": "bc_s4", "name": "Powyżej 2.5", "code": "OVER", "odds": 1.90, "handicap": 2.5, "status": 1},
                            {"id": "bc_s5", "name": "Poniżej 2.5", "code": "UNDER", "odds": 1.90, "handicap": 2.5, "status": 1},
                        ],
                    },
                    {
                        "id": "bc_100_m_btts",
                        "name": "Obie drużyny strzelą",
                        "code": "BTTS",
                        "is_open": True,
                        "selections": [
                            {"id": "bc_s6", "name": "Tak", "code": "YES", "odds": 1.75, "status": 1},
                            {"id": "bc_s7", "name": "Nie", "code": "NO", "odds": 2.05, "status": 1},
                        ],
                    },
                ],
            }
        else:
            return item.metadata["raw"]

    bc.set_mock_fetch_provider(bc_fetch)

    eval_time = datetime(2026, 8, 18, 12, 0, 0, tzinfo=timezone.utc)
    result = orchestrator.run_scan_cycle(providers={"superbet": sb, "betclic": bc}, evaluation_time=eval_time)

    assert result.cycle_status == CycleStatus.SUCCESS
    assert result.matched_events_count == 1
    assert result.detail_events_overlap_selected == 1
    assert "bc_100" in result.diagnostics["detail_prioritization"]["selected_event_ids"]

    assert result.market_coverage_breakdown["1X2"]["matched"] >= 1
    assert result.market_coverage_breakdown["TOTALS"]["matched"] >= 1
    assert result.market_coverage_breakdown["BTTS"]["matched"] >= 1
    assert result.markets_matched_count >= 3


def test_11_top_tier_league_outranks_niche_league():
    """Test 11: Top-tier league (UCL / Premier League) strictly outranks lower/niche leagues."""
    policy = DefaultEventSelectionPolicy()
    now_dt = datetime.now(timezone.utc)

    it_ucl = _make_bc_item("bc_ucl", "Real Madrid vs Man City", "UEFA Champions League", (now_dt + timedelta(hours=5)).isoformat())
    it_pl = _make_bc_item("bc_pl", "Arsenal vs Liverpool", "Premier League", (now_dt + timedelta(hours=4)).isoformat())
    it_niche = _make_bc_item("bc_niche", "Banga vs Suduva", "Litwa 1. Liga", (now_dt + timedelta(hours=1)).isoformat())
    it_youth = _make_bc_item("bc_youth", "Chelsea U21 vs Arsenal U21", "Premier League 2", (now_dt + timedelta(hours=1)).isoformat())

    # All non-overlapping to test pure competition tier priority
    res = policy.prioritize_detail_events(
        discovered_items=[it_niche, it_youth, it_pl, it_ucl],
        overlap_event_ids=set(),
        max_detail_requests=2,
    )

    assert res.events_selected == 2
    assert res.selected_event_ids == ["bc_ucl", "bc_pl"]


def test_12_top_event_not_evicted_by_lower_priority_event():
    """Test 12: Top-tier events are never displaced by niche/lower priority events even if the latter kicks off earlier."""
    policy = DefaultEventSelectionPolicy()
    now_dt = datetime.now(timezone.utc)

    it_cl = _make_bc_item("bc_cl", "Bayern vs PSG", "Champions League", (now_dt + timedelta(hours=24)).isoformat())
    it_pl = _make_bc_item("bc_pl", "Chelsea vs Spurs", "Premier League", (now_dt + timedelta(hours=12)).isoformat())
    it_ek = _make_bc_item("bc_ek", "Legia vs Lech", "Ekstraklasa", (now_dt + timedelta(hours=6)).isoformat())
    it_niche1 = _make_bc_item("bc_n1", "Team A vs Team B", "Uganda Premier League", (now_dt + timedelta(hours=1)).isoformat())
    it_niche2 = _make_bc_item("bc_n2", "Team C vs Team D", "Wyspy Owcze 1 Liga", (now_dt + timedelta(hours=2)).isoformat())

    res = policy.prioritize_detail_events(
        discovered_items=[it_niche1, it_niche2, it_cl, it_pl, it_ek],
        overlap_event_ids=set(),
        max_detail_requests=3,
    )

    assert len(res.selected_event_ids) == 3
    # Top tier events must occupy all 3 slots
    assert "bc_cl" in res.selected_event_ids
    assert "bc_pl" in res.selected_event_ids
    assert "bc_ek" in res.selected_event_ids
    assert "bc_n1" not in res.selected_event_ids
    assert "bc_n2" not in res.selected_event_ids

