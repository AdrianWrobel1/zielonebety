"""
Targeted Regression Test Suite:
1. Fallback Selection: Popular competitions take priority, but when fewer popular events exist than the budget,
   secondary tier fixtures (including Championship) fill all remaining slots without being skipped or displaced
   by distant matches.
2. General Tier Hierarchy: Works generally across tiers (Tier 0 -> Tier 1 -> Tier 2) without hardcoded special cases.
3. Full Market Coverage for Secondary / Championship fixtures:
   Detail fetch on Championship fixtures acquires full market depth across multiple market families
   (1X2, TOTALS, BTTS, HANDICAP, CORNERS, CARDS, FOULS, SHOTS, PLAYER PROPS), not limited to 1X2 overview.
"""

from datetime import datetime, timezone, timedelta
import pytest
from typing import List, Dict, Any

from domain.models import Competition, Event, Market, Selection, Odds
from normalization.base_normalizer import NormalizedGraph
from normalization.competitions import resolve_canonical_competition
from orchestration.event_selection import DefaultEventSelectionPolicy, SECONDARY_TIER_COMPETITIONS
from orchestration.detail_planning import CoordinatedDetailSelectionPlanner
from orchestration.models import ScanConfig
from providers.superbet.models import SuperbetDiscoveredItem
from providers.betclic.models import BetclicDiscoveredItem
from providers.superbet.parser.parser import SuperbetParser
from providers.betclic.parser.parser import BetclicParser
from normalization.engine import NormalizationEngine
from normalization.validation_pipeline import CrossBookmakerValidationPipeline
from api.services import _serialize_events_from_scan_result
from orchestration.scan_orchestrator import ScanCycleResult
from orchestration.models import CycleStatus


def test_championship_competition_resolution_and_tiering():
    """Test 1: Championship competition resolves to canonical 'Championship' with Tier 1 from team names."""
    policy = DefaultEventSelectionPolicy()

    # 1. Superbet overview item with generic name and Championship teams
    item = SuperbetDiscoveredItem(
        event_id="sb_champ_01",
        match_name="Portsmouth·Derby County",
        competition_id="215",
        competition_name="Superbet Football",
        start_time="2026-09-05T14:00:00Z",
    )

    resolved_comp = policy._extract_item_competition(item)
    assert resolved_comp == "Championship", f"Expected 'Championship', got '{resolved_comp}'"
    tier = policy.calculate_competition_tier(resolved_comp)
    assert tier == 1, f"Expected Tier 1 for Championship, got {tier}"

    # 2. Direct canonical resolution check
    comp_res = resolve_canonical_competition(
        raw_name="Superbet Football",
        home_team="Stoke City",
        away_team="Norwich City",
    )
    assert comp_res.canonical_name == "Championship"
    assert comp_res.tier == 1


def test_fallback_selection_fills_remaining_slots_with_secondary_fixtures():
    """Test 2: When popular events (Tier 0) are fewer than budget, secondary fixtures (Tier 1, e.g. Championship) fill remaining slots."""
    policy = DefaultEventSelectionPolicy()
    planner = CoordinatedDetailSelectionPlanner(
        event_selection_policy=policy,
        normalization_engine=NormalizationEngine(),
        validation_pipeline=CrossBookmakerValidationPipeline(),
    )

    # 2 Popular fixtures (Premier League, La Liga)
    # 2 Secondary fixtures (Championship: Swansea vs Watford, Portsmouth vs Derby)
    # 1 Distant Minor fixture (Tier 2) with high mock confidence
    now = datetime.now(timezone.utc)

    sb_disc = [
        SuperbetDiscoveredItem(
            event_id="sb_pl_1",
            match_name="Arsenal·Chelsea",
            competition_name="Premier League",
            start_time=(now + timedelta(hours=2)).isoformat(),
            metadata={"raw": {"id": "sb_pl_1", "matchName": "Arsenal·Chelsea", "competitionName": "Premier League", "markets": []}},
        ),
        SuperbetDiscoveredItem(
            event_id="sb_la_1",
            match_name="Real Madrid·Barcelona",
            competition_name="LaLiga",
            start_time=(now + timedelta(hours=3)).isoformat(),
            metadata={"raw": {"id": "sb_la_1", "matchName": "Real Madrid·Barcelona", "competitionName": "LaLiga", "markets": []}},
        ),
        SuperbetDiscoveredItem(
            event_id="sb_champ_1",
            match_name="Swansea City·Watford",
            competition_name="Superbet Football",
            start_time=(now + timedelta(hours=4)).isoformat(),
            metadata={"raw": {"id": "sb_champ_1", "matchName": "Swansea City·Watford", "competitionName": "Superbet Football", "markets": []}},
        ),
        SuperbetDiscoveredItem(
            event_id="sb_champ_2",
            match_name="Portsmouth·Derby County",
            competition_name="Superbet Football",
            start_time=(now + timedelta(hours=5)).isoformat(),
            metadata={"raw": {"id": "sb_champ_2", "matchName": "Portsmouth·Derby County", "competitionName": "Superbet Football", "markets": []}},
        ),
        SuperbetDiscoveredItem(
            event_id="sb_minor_1",
            match_name="Minor Team A·Minor Team B",
            competition_name="Superbet Football",
            start_time=(now + timedelta(hours=10)).isoformat(),
            metadata={"raw": {"id": "sb_minor_1", "matchName": "Minor Team A·Minor Team B", "competitionName": "Superbet Football", "markets": []}},
        ),
    ]

    bc_disc = [
        BetclicDiscoveredItem(
            provider_event_id="bc_pl_1",
            name="Arsenal - Chelsea",
            competition_name="Anglia Premier League",
            start_time=(now + timedelta(hours=2)).isoformat(),
            url="https://www.betclic.pl/match/bc_pl_1",
            metadata={"raw": {"id": "bc_pl_1", "name": "Arsenal - Chelsea", "competition": "Anglia Premier League", "markets": []}},
        ),
        BetclicDiscoveredItem(
            provider_event_id="bc_la_1",
            name="Real Madrid - Barcelona",
            competition_name="LaLiga",
            start_time=(now + timedelta(hours=3)).isoformat(),
            url="https://www.betclic.pl/match/bc_la_1",
            metadata={"raw": {"id": "bc_la_1", "name": "Real Madrid - Barcelona", "competition": "LaLiga", "markets": []}},
        ),
        BetclicDiscoveredItem(
            provider_event_id="bc_champ_1",
            name="Swansea - Watford",
            competition_name="Anglia Championship",
            start_time=(now + timedelta(hours=4)).isoformat(),
            url="https://www.betclic.pl/match/bc_champ_1",
            metadata={"raw": {"id": "bc_champ_1", "name": "Swansea - Watford", "competition": "Anglia Championship", "markets": []}},
        ),
        BetclicDiscoveredItem(
            provider_event_id="bc_champ_2",
            name="Portsmouth - Derby",
            competition_name="Anglia Championship",
            start_time=(now + timedelta(hours=5)).isoformat(),
            url="https://www.betclic.pl/match/bc_champ_2",
            metadata={"raw": {"id": "bc_champ_2", "name": "Portsmouth - Derby", "competition": "Anglia Championship", "markets": []}},
        ),
        BetclicDiscoveredItem(
            provider_event_id="bc_minor_1",
            name="Minor Team A - Minor Team B",
            competition_name="Minor Cup",
            start_time=(now + timedelta(hours=10)).isoformat(),
            url="https://www.betclic.pl/match/bc_minor_1",
            metadata={"raw": {"id": "bc_minor_1", "name": "Minor Team A - Minor Team B", "competition": "Minor Cup", "markets": []}},
        ),
    ]

    # Detail budget of 4:
    # Must select 2 Tier 0 events (PL, LaLiga) + 2 Tier 1 events (Championship: sb_champ_1, sb_champ_2).
    # Minor fixture must NOT displace Championship!
    config = ScanConfig(
        providers=("superbet", "betclic"),
        hours_ahead=72,
        max_detail_requests=4,
        scan_mode="NORMAL",
    )

    plan = planner.create_plan(
        sb_discovered=sb_disc,
        bc_discovered=bc_disc,
        sb_parser=SuperbetParser(),
        bc_parser=BetclicParser(),
        config=config,
    )

    assert len(plan.selected_event_ids_superbet) == 4
    assert len(plan.selected_event_ids_betclic) == 4

    # Both popular events selected
    assert "sb_pl_1" in plan.selected_event_ids_superbet
    assert "sb_la_1" in plan.selected_event_ids_superbet
    # Both Championship events selected
    assert "sb_champ_1" in plan.selected_event_ids_superbet
    assert "sb_champ_2" in plan.selected_event_ids_superbet
    # Minor event NOT selected
    assert "sb_minor_1" not in plan.selected_event_ids_superbet
    assert "bc_minor_1" not in plan.selected_event_ids_betclic


def test_championship_detail_acquisition_pipeline_market_coverage():
    """Test 3: Championship fixture selected for detail produces rich multi-market coverage across all pipeline stages."""
    home = "Swansea City"
    away = "Watford"

    # Simulate Superbet full detail with diverse market families
    sb_markets = [
        {"name": "Mecz", "market_type_code": "1X2", "selections": [{"name": "1", "price": 2.20}, {"name": "X", "price": 3.30}, {"name": "2", "price": 3.10}]},
        {"name": "Obie drużyny strzelą", "market_type_code": "BTTS", "selections": [{"name": "Tak", "price": 1.75}, {"name": "Nie", "price": 2.05}]},
        {"name": "Liczba goli", "market_type_code": "TOTALS", "specifiers": {"ss_total": "2.5"}, "selections": [{"name": "Powyżej 2.5", "price": 1.90}, {"name": "Poniżej 2.5", "price": 1.90}]},
        {"name": "Handicap 1X2", "market_type_code": "HANDICAP", "specifiers": {"ss_hcp": "0:1"}, "selections": [{"name": "Swansea (-1)", "price": 4.20}, {"name": "Remis", "price": 3.80}, {"name": "Watford (+1)", "price": 1.65}]},
        {"name": "Liczba rzutów rożnych", "market_type_code": "CORNERS", "specifiers": {"ss_total": "9.5"}, "selections": [{"name": "Powyżej 9.5", "price": 1.85}, {"name": "Poniżej 9.5", "price": 1.95}]},
        {"name": "Liczba żółtych kartek", "market_type_code": "CARDS", "specifiers": {"ss_total": "3.5"}, "selections": [{"name": "Powyżej 3.5", "price": 1.70}, {"name": "Poniżej 3.5", "price": 2.10}]},
        {"name": "Liczba fauli", "market_type_code": "FOULS", "specifiers": {"ss_total": "21.5"}, "selections": [{"name": "Powyżej 21.5", "price": 1.80}, {"name": "Poniżej 21.5", "price": 1.95}]},
        {"name": "Liczba strzałów", "market_type_code": "SHOTS", "specifiers": {"ss_total": "23.5"}, "selections": [{"name": "Powyżej 23.5", "price": 1.85}, {"name": "Poniżej 23.5", "price": 1.90}]},
        {"name": "Zawodnik - strzeli gola", "market_type_code": "PLAYER_GOALS", "specifiers": {"ss_player": "Liam Cullen"}, "selections": [{"name": "Liam Cullen - Tak", "price": 2.80}]},
        {"name": "Zawodnik - liczba celnych strzałów", "market_type_code": "PLAYER_SHOTS_ON_TARGET", "specifiers": {"ss_player": "Liam Cullen", "ss_total": "1.5"}, "selections": [{"name": "Liam Cullen - powyżej 1.5", "price": 2.50}, {"name": "Liam Cullen - poniżej 1.5", "price": 1.45}]},
    ]
    sb_raw = {
        "id": "sb_champ_live_01",
        "matchName": f"{home}·{away}",
        "competitionName": "Superbet Football",
        "startDate": "2026-09-05T14:00:00Z",
        "markets": sb_markets,
    }

    # Simulate Betclic full detail with diverse market families
    bc_raw = {
        "id": "1209271794548736",
        "name": f"{home} - {away}",
        "competition": {"id": "2", "name": "Anglia Championship"},
        "start_date": "2026-09-05T14:00:00Z",
        "contestants": [{"name": home}, {"name": away}],
        "subCategories": [
            {
                "id": "sc_main",
                "name": "Wynik meczu",
                "markets": [
                    {"id": "bc_m1", "name": "Wynik meczu", "mainSelections": [{"id": "s1", "name": home, "odds": 2.25}, {"id": "s2", "name": "Remis", "odds": 3.35}, {"id": "s3", "name": away, "odds": 3.05}]},
                    {"id": "bc_m2", "name": "Obie drużyny strzelą gola", "mainSelections": [{"id": "s4", "name": "Tak", "odds": 1.78}, {"id": "s5", "name": "Nie", "odds": 2.00}]},
                    {"id": "bc_m3", "name": "Liczba goli: powyżej/poniżej 2.5", "mainSelections": [{"id": "s6", "name": "Powyżej 2.5", "odds": 1.95}, {"id": "s7", "name": "Poniżej 2.5", "odds": 1.85}]},
                ],
            },
            {
                "id": "sc_props",
                "name": "Statystyki i zawodnicy",
                "markets": [
                    {"id": "bc_m4", "name": "Rzuty rożne w meczu: powyżej/poniżej 9.5", "mainSelections": [{"id": "s8", "name": "Powyżej 9.5", "odds": 1.90}, {"id": "s9", "name": "Poniżej 9.5", "odds": 1.90}]},
                    {"id": "bc_m5", "name": "Kartki w meczu: powyżej/poniżej 3.5", "mainSelections": [{"id": "s10", "name": "Powyżej 3.5", "odds": 1.75}, {"id": "s11", "name": "Poniżej 3.5", "odds": 2.05}]},
                    {"id": "bc_m6", "name": "Faule w meczu: powyżej/poniżej 21.5", "mainSelections": [{"id": "s12", "name": "Powyżej 21.5", "odds": 1.82}, {"id": "s13", "name": "Poniżej 21.5", "odds": 1.92}]},
                    {"id": "bc_m7", "name": "Strzały w meczu: powyżej/poniżej 23.5", "mainSelections": [{"id": "s14", "name": "Powyżej 23.5", "odds": 1.88}, {"id": "s15", "name": "Poniżej 23.5", "odds": 1.88}]},
                    {"id": "bc_m8", "name": "Liam Cullen - liczba celnych strzałów", "mainSelections": [{"id": "s16", "name": "Powyżej 1.5", "odds": 2.55}, {"id": "s17", "name": "Poniżej 1.5", "odds": 1.42}]},
                ],
            },
        ],
    }

    # 1. PARSE
    sb_parsed = SuperbetParser().parse_payloads([sb_raw])
    bc_parsed = BetclicParser().parse_payloads([bc_raw])
    assert len(sb_parsed) == 1
    assert len(bc_parsed) == 1
    assert len(sb_parsed[0].markets) >= 10
    assert len(bc_parsed[0].markets) >= 8

    # 2. NORMALIZE
    norm = NormalizationEngine()
    sb_norm = norm.normalize("superbet", sb_parsed)
    bc_norm = norm.normalize("betclic", bc_parsed)
    assert len(sb_norm.graphs) == 1
    assert len(bc_norm.graphs) == 1
    assert len(sb_norm.graphs[0].markets) >= 10
    assert len(bc_norm.graphs[0].markets) >= 8

    # 3. MATCH & VALIDATE
    val_pipe = CrossBookmakerValidationPipeline()
    val_res = val_pipe.run(
        source_items=sb_norm.graphs,
        target_items=bc_norm.graphs,
    )
    assert len(val_res.canonical_events) == 1
    ce = val_res.canonical_events[0]
    rec = val_res.event_validation_records[0]

    # Must match multiple market families, NOT just 1X2!
    matched_types = set(m.canonical_market_key.market_type for m in rec.matched_markets)
    assert "1X2" in matched_types
    assert "BTTS" in matched_types
    assert "TOTALS" in matched_types
    assert len(rec.matched_markets) >= 5

    # 4. API & EVENT BROWSER SERIALIZATION
    scan_res = ScanCycleResult(
        execution_id="test_champ_coverage_scan",
        cycle_status=CycleStatus.SUCCESS,
        duration_seconds=1.0,
        started_at=datetime.now(timezone.utc),
        completed_at=datetime.now(timezone.utc),
        scan_mode="NORMAL",
        validation_result=val_res,
        normalization_results={
            "superbet": sb_norm,
            "betclic": bc_norm,
        },
    )

    events_list, detail_map = _serialize_events_from_scan_result(scan_res)
    assert len(events_list) == 1
    ev_summary = events_list[0]
    assert ev_summary["home_team"] == home
    assert ev_summary["away_team"] == away
    assert ev_summary["matched_markets_count"] >= 5

    ev_detail = detail_map.get(ev_summary["canonical_event_id"])
    assert ev_detail is not None
    assert len(ev_detail["markets"]) >= 5
    detail_types = set(m["market_type"] for m in ev_detail["markets"])
    assert "1X2" in detail_types
    assert "BTTS" in detail_types
    assert "TOTALS" in detail_types
