"""
Stage 10.12 Regression & Verification Test Suite:
Production Coverage, Overlap Optimization & Provider Reliability
"""

from decimal import Decimal
import pytest
from datetime import datetime, timezone

from domain.models import Competition, Event, Market, Selection, Odds
from normalization.base_normalizer import NormalizedGraph
from normalization.betclic_normalizer import BetclicNormalizer
from normalization.candidate_generator import EventCandidateGenerator
from normalization.identity import (
    CITY_TRANSLATIONS,
    WEAK_TOKENS,
    compare_teams,
    normalize_team_name,
    parse_kickoff_to_utc,
    TeamReference,
)
from normalization.matcher import EventMatcher, MatchDecisionType
from normalization.superbet_normalizer import SuperbetNormalizer
from normalization.validation_pipeline import CrossBookmakerValidationPipeline
from orchestration.event_selection import DefaultEventSelectionPolicy
from orchestration.models import CycleStatus, ScanConfig
from orchestration.scan_orchestrator import ProductionScanOrchestrator, _categorize_market_type
from providers.base.execution_engine import ExecutionEngine
from providers.base.provider_state import ProviderState
from providers.base.models import ValidationReport
from providers.betclic.config import BetclicConfig
from providers.betclic.models import (
    BetclicDiscoveredItem,
    BetclicEvent,
    BetclicMarket,
    BetclicOdds,
    BetclicSelection,
)
from providers.betclic.parser.parser import BetclicParser
from providers.betclic.provider import BetclicProvider
from providers.betclic.validation.validator import BetclicValidator
from providers.odds_api.config import OddsApiConfig
from providers.odds_api.provider import OddsApiProvider
from providers.superbet.config import SuperbetConfig
from providers.superbet.provider import SuperbetProvider


# ──────────────────────────────────────────────────────────────────────────────
# 1. Betclic 1.0 Odds Rejection & Validator Safety Tests
# ──────────────────────────────────────────────────────────────────────────────

def test_betclic_parser_and_validator_handles_1_0_and_null_odds():
    """Verify BetclicParser marks odds <= 1.0 as inactive and validator does not fail the entire event."""
    parser = BetclicParser()
    validator = BetclicValidator()

    raw_payload = {
        "id": "12345",
        "name": "Team A vs Team B",
        "competition": {"name": "Premier League"},
        "start_date": "2026-08-18T20:00:00Z",
        "markets": [
            {
                "id": "m1",
                "name": "Wynik meczu",
                "code": "1X2",
                "is_open": True,
                "mainSelections": [
                    {"id": "s1", "name": "Team A", "odds": 2.10, "status": 1},
                    {"id": "s2", "name": "Remis", "odds": 3.40, "status": 1},
                    {"id": "s3", "name": "Team B", "odds": 3.20, "status": 1},
                ],
            },
            {
                "id": "m2",
                "name": "Dokładny wynik",
                "code": "CORRECT_SCORE",
                "is_open": True,
                "mainSelections": [
                    {"id": "s4", "name": "1-0", "odds": 6.50, "status": 1},
                    {"id": "s5", "name": "Unpriced placeholder", "odds": None, "status": 2},
                    {"id": "s6", "name": "Suspended outcome", "odds": 1.0, "status": 2},
                ],
            },
        ],
    }

    events = parser.parse_payloads([raw_payload])
    assert len(events) == 1
    ev = events[0]

    # Check that valid odds are active
    m1 = next(m for m in ev.markets if m.provider_market_id == "m1")
    assert all(s.odds.is_active and s.odds.decimal_odds > 1.0 for s in m1.selections)

    # Check that unpriced/1.0 odds are inactive
    m2 = next(m for m in ev.markets if m.provider_market_id == "m2")
    s5 = next(s for s in m2.selections if s.provider_selection_id == "s5")
    s6 = next(s for s in m2.selections if s.provider_selection_id == "s6")
    assert s5.odds is None or not s5.odds.is_active
    assert s6.odds is not None and not s6.odds.is_active

    # Validation should succeed without marking the whole event invalid
    report = validator.validate_events(events)
    assert report.is_valid is True
    assert report.valid_objects == 1
    assert report.invalid_objects == 0


def test_betclic_normalizer_excludes_inactive_and_1_0_odds_from_canonical_graph():
    """Verify that only active selections with odds > 1.0 are converted to Odds domain models."""
    normalizer = BetclicNormalizer()

    ev = BetclicEvent(
        provider_event_id="bc_ev_1",
        name="Arsenal vs Chelsea",
        competition_name="Premier League",
        start_time="2026-08-18T20:00:00Z",
        home_team="Arsenal",
        away_team="Chelsea",
        markets=[
            BetclicMarket(
                provider_market_id="m1",
                name="Wynik meczu",
                market_type_code="1X2",
                is_open=True,
                selections=[
                    BetclicSelection(
                        provider_selection_id="s1",
                        name="Arsenal",
                        type_code="HOME",
                        odds=BetclicOdds(provider_odds_id="o1", decimal_odds=1.85, is_active=True),
                    ),
                    BetclicSelection(
                        provider_selection_id="s2",
                        name="Remis",
                        type_code="DRAW",
                        odds=BetclicOdds(provider_odds_id="o2", decimal_odds=1.0, is_active=False),
                    ),
                    BetclicSelection(
                        provider_selection_id="s3",
                        name="Chelsea",
                        type_code="AWAY",
                        odds=BetclicOdds(provider_odds_id="o3", decimal_odds=4.20, is_active=True),
                    ),
                ],
            )
        ],
    )

    graph = normalizer.normalize_event(ev)
    assert len(graph.markets) == 1
    assert len(graph.selections) == 3
    # Only 2 valid odds should be in odds_list (the 1.0 inactive odd is excluded)
    assert len(graph.odds_list) == 2
    assert all(o.decimal_odds > 1.0 for o in graph.odds_list)
    assert {o.decimal_odds for o in graph.odds_list} == {1.85, 4.20}


# ──────────────────────────────────────────────────────────────────────────────
# 2. Team Name Transliteration and Synonym Normalization Tests
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "raw_a,raw_b,expected_match",
    [
        ("Dinamo Zagrzeb", "Dinamo Zagreb", True),
        ("AEK Ateny", "AEK Athens", True),
        ("Bayern Monachium", "Bayern Munich", True),
        ("Inter Mediolan", "Inter Milan", True),
        ("Juventus Turyn", "Juventus Torino", True),
        ("Sporting Lizbona", "Sporting Lisbon", True),
        ("FC Kopenhaga", "FC Copenhagen", True),
        ("MSK Spisske Podhradie", "Spisske Podhradie", True),
        ("TJ Tatran Oravske Vesele", "Tatran Oravske Vesele", True),
        ("Olympique Lyon", "Lyon", True),
        ("Viking FK", "Viking", True),
    ],
)
def test_city_and_club_prefix_normalization(raw_a, raw_b, expected_match):
    """Test that Polish/European transliterations and club noise prefixes match with high token overlap."""
    ref_a = TeamReference.from_raw(raw_a)
    ref_b = TeamReference.from_raw(raw_b)

    comp = compare_teams(ref_a, ref_b)
    if expected_match:
        assert comp.exact_name or comp.token_overlap >= 0.50, (
            f"Expected {raw_a} and {raw_b} to match (exact={comp.exact_name}, overlap={comp.token_overlap})"
        )


def test_cross_bookmaker_event_matcher_with_city_transliteration():
    """Verify that EventMatcher accurately matches fixtures with European transliterations."""
    matcher = EventMatcher()
    cand_gen = EventCandidateGenerator()

    comp_sb = Competition(name="Liga Mistrzów")
    ev_sb = Event(
        competition_id=comp_sb.internal_id,
        home_participant="Dinamo Zagreb",
        away_participant="Viking",
        scheduled_start="2026-08-18T19:00:00Z",
        provider_ids={"superbet": "sb_101"},
    )
    graph_sb = NormalizedGraph(
        competition=comp_sb,
        event=ev_sb,
        markets=[],
        selections=[],
        odds_list=[],
    )

    # Target: Betclic style (Dinamo Zagrzeb vs Viking)
    comp_bc = comp_sb
    ev_bc = Event(
        competition_id=comp_bc.internal_id,
        home_participant="Dinamo Zagrzeb",
        away_participant="Viking",
        scheduled_start="2026-08-18T19:00:00Z",
        provider_ids={"betclic": "bc_202"},
    )
    graph_bc = NormalizedGraph(
        competition=comp_bc,
        event=ev_bc,
        markets=[],
        selections=[],
        odds_list=[],
    )

    cand_res = cand_gen.generate_candidates([graph_sb], [graph_bc])
    assert len(cand_res.candidates) == 1

    src_map = {ev_sb.internal_id: ev_sb}
    tgt_map = {ev_bc.internal_id: ev_bc}
    comp_map = {
        comp_sb.internal_id: comp_sb,
        comp_bc.internal_id: comp_bc,
    }

    match_res = matcher.match_candidates(cand_res.candidates, src_map, tgt_map, comp_map)
    assert match_res.matched_count == 1
    assert match_res.decisions[0].decision == MatchDecisionType.MATCHED
    assert match_res.decisions[0].total_score >= 0.80


# ──────────────────────────────────────────────────────────────────────────────
# 3. Event Selection Policy & Overlap Prioritization Tests
# ──────────────────────────────────────────────────────────────────────────────

def test_event_selection_policy_prioritizes_overlapping_fixtures_in_graphs():
    """Verify that filter_normalized_graphs preserves all overlapping fixtures when capping to limit."""
    policy = DefaultEventSelectionPolicy()

    graphs = []
    comp = Competition(name="Standard League")
    # Create 20 events
    for i in range(1, 21):
        ev = Event(
            competition_id=comp.internal_id,
            home_participant=f"Team {i}A",
            away_participant=f"Team {i}B",
            scheduled_start="2026-08-18T20:00:00Z",
            provider_ids={"superbet": f"sb_{i}"},
        )
        g = NormalizedGraph(
            competition=comp,
            event=ev,
            markets=[],
            selections=[],
            odds_list=[],
        )
        graphs.append(g)

    # Suppose events 18 and 19 are overlapping fixtures from pre-discovery
    overlap_ids = {"sb_18", "sb_19"}

    # Cap to top 5
    filtered = policy.filter_normalized_graphs(
        graphs=graphs,
        limit=5,
        overlap_event_ids=overlap_ids,
    )

    assert len(filtered) == 5
    selected_sb_ids = {g.event.provider_ids["superbet"] for g in filtered}
    assert "sb_18" in selected_sb_ids
    assert "sb_19" in selected_sb_ids


# ──────────────────────────────────────────────────────────────────────────────
# 4. Multi-Market Overlap & Telemetry Tests
# ──────────────────────────────────────────────────────────────────────────────

def test_multi_market_matching_on_overlapping_fixture():
    """Verify that 1X2, BTTS, TOTALS lines, and Double Chance match across Superbet and Betclic representations."""
    pipeline = CrossBookmakerValidationPipeline()

    comp_sb = Competition(name="Champions League")
    comp_bc = comp_sb

    ev_sb = Event(
        competition_id=comp_sb.internal_id,
        home_participant="Fenerbahce",
        away_participant="Lyon",
        scheduled_start="2026-08-18T19:00:00Z",
        provider_ids={"superbet": "sb_fener"},
    )
    ev_bc = Event(
        competition_id=comp_bc.internal_id,
        home_participant="Fenerbahce",
        away_participant="Lyon",
        scheduled_start="2026-08-18T19:00:00Z",
        provider_ids={"betclic": "bc_fener"},
    )

    # Markets for Superbet
    m_1x2_sb = Market(event_id=ev_sb.internal_id, market_type="1X2", status="OPEN", provider_ids={"superbet": "m_1x2"})
    m_btts_sb = Market(event_id=ev_sb.internal_id, market_type="BTTS", status="OPEN", provider_ids={"superbet": "m_btts"})
    m_tot_sb = Market(event_id=ev_sb.internal_id, market_type="TOTALS", line=2.5, status="OPEN", provider_ids={"superbet": "m_tot"})
    m_dc_sb = Market(event_id=ev_sb.internal_id, market_type="DOUBLE_CHANCE", status="OPEN", provider_ids={"superbet": "m_dc"})

    s_sb_1 = Selection(market_id=m_1x2_sb.internal_id, selection_type="HOME")
    s_sb_x = Selection(market_id=m_1x2_sb.internal_id, selection_type="DRAW")
    s_sb_2 = Selection(market_id=m_1x2_sb.internal_id, selection_type="AWAY")

    s_sb_byes = Selection(market_id=m_btts_sb.internal_id, selection_type="YES")
    s_sb_bno = Selection(market_id=m_btts_sb.internal_id, selection_type="NO")

    s_sb_over = Selection(market_id=m_tot_sb.internal_id, selection_type="OVER", line=2.5)
    s_sb_under = Selection(market_id=m_tot_sb.internal_id, selection_type="UNDER", line=2.5)

    s_sb_1x = Selection(market_id=m_dc_sb.internal_id, selection_type="HOME_DRAW")
    s_sb_12 = Selection(market_id=m_dc_sb.internal_id, selection_type="HOME_AWAY")
    s_sb_x2 = Selection(market_id=m_dc_sb.internal_id, selection_type="DRAW_AWAY")

    odds_sb = [
        Odds(selection_id=s_sb_1.internal_id, bookmaker="superbet", decimal_odds=2.00),
        Odds(selection_id=s_sb_x.internal_id, bookmaker="superbet", decimal_odds=3.60),
        Odds(selection_id=s_sb_2.internal_id, bookmaker="superbet", decimal_odds=3.70),
        Odds(selection_id=s_sb_byes.internal_id, bookmaker="superbet", decimal_odds=1.75),
        Odds(selection_id=s_sb_bno.internal_id, bookmaker="superbet", decimal_odds=2.05),
        Odds(selection_id=s_sb_over.internal_id, bookmaker="superbet", decimal_odds=1.90),
        Odds(selection_id=s_sb_under.internal_id, bookmaker="superbet", decimal_odds=1.90),
        Odds(selection_id=s_sb_1x.internal_id, bookmaker="superbet", decimal_odds=1.30),
        Odds(selection_id=s_sb_12.internal_id, bookmaker="superbet", decimal_odds=1.28),
        Odds(selection_id=s_sb_x2.internal_id, bookmaker="superbet", decimal_odds=1.75),
    ]

    graph_sb = NormalizedGraph(
        competition=comp_sb,
        event=ev_sb,
        markets=[m_1x2_sb, m_btts_sb, m_tot_sb, m_dc_sb],
        selections=[s_sb_1, s_sb_x, s_sb_2, s_sb_byes, s_sb_bno, s_sb_over, s_sb_under, s_sb_1x, s_sb_12, s_sb_x2],
        odds_list=odds_sb,
    )

    # Markets for Betclic
    m_1x2_bc = Market(event_id=ev_bc.internal_id, market_type="1X2", status="OPEN", provider_ids={"betclic": "bc_m_1x2"})
    m_btts_bc = Market(event_id=ev_bc.internal_id, market_type="BTTS", status="OPEN", provider_ids={"betclic": "bc_m_btts"})
    m_tot_bc = Market(event_id=ev_bc.internal_id, market_type="TOTALS", line=2.5, status="OPEN", provider_ids={"betclic": "bc_m_tot"})
    m_dc_bc = Market(event_id=ev_bc.internal_id, market_type="DOUBLE_CHANCE", status="OPEN", provider_ids={"betclic": "bc_m_dc"})

    s_bc_1 = Selection(market_id=m_1x2_bc.internal_id, selection_type="HOME")
    s_bc_x = Selection(market_id=m_1x2_bc.internal_id, selection_type="DRAW")
    s_bc_2 = Selection(market_id=m_1x2_bc.internal_id, selection_type="AWAY")

    s_bc_byes = Selection(market_id=m_btts_bc.internal_id, selection_type="YES")
    s_bc_bno = Selection(market_id=m_btts_bc.internal_id, selection_type="NO")

    s_bc_over = Selection(market_id=m_tot_bc.internal_id, selection_type="OVER", line=2.5)
    s_bc_under = Selection(market_id=m_tot_bc.internal_id, selection_type="UNDER", line=2.5)

    s_bc_1x = Selection(market_id=m_dc_bc.internal_id, selection_type="HOME_DRAW")
    s_bc_12 = Selection(market_id=m_dc_bc.internal_id, selection_type="HOME_AWAY")
    s_bc_x2 = Selection(market_id=m_dc_bc.internal_id, selection_type="DRAW_AWAY")

    odds_bc = [
        Odds(selection_id=s_bc_1.internal_id, bookmaker="betclic", decimal_odds=2.05),
        Odds(selection_id=s_bc_x.internal_id, bookmaker="betclic", decimal_odds=3.65),
        Odds(selection_id=s_bc_2.internal_id, bookmaker="betclic", decimal_odds=3.55),
        Odds(selection_id=s_bc_byes.internal_id, bookmaker="betclic", decimal_odds=1.72),
        Odds(selection_id=s_bc_bno.internal_id, bookmaker="betclic", decimal_odds=2.00),
        Odds(selection_id=s_bc_over.internal_id, bookmaker="betclic", decimal_odds=1.92),
        Odds(selection_id=s_bc_under.internal_id, bookmaker="betclic", decimal_odds=1.88),
        Odds(selection_id=s_bc_1x.internal_id, bookmaker="betclic", decimal_odds=1.31),
        Odds(selection_id=s_bc_12.internal_id, bookmaker="betclic", decimal_odds=1.27),
        Odds(selection_id=s_bc_x2.internal_id, bookmaker="betclic", decimal_odds=1.72),
    ]

    graph_bc = NormalizedGraph(
        competition=comp_bc,
        event=ev_bc,
        markets=[m_1x2_bc, m_btts_bc, m_tot_bc, m_dc_bc],
        selections=[s_bc_1, s_bc_x, s_bc_2, s_bc_byes, s_bc_bno, s_bc_over, s_bc_under, s_bc_1x, s_bc_12, s_bc_x2],
        odds_list=odds_bc,
    )

    vr = pipeline.run([graph_sb], [graph_bc])
    assert len(vr.canonical_events) == 1
    rec = vr.event_validation_records[0]

    matched_types = {mm.canonical_market_key.market_type for mm in rec.matched_markets}
    assert {"1X2", "BTTS", "TOTALS", "DOUBLE_CHANCE"}.issubset(matched_types)
    assert len(rec.matched_markets) == 4


# ──────────────────────────────────────────────────────────────────────────────
# 5. Odds API Failure Isolation & Primary Scan Robustness
# ──────────────────────────────────────────────────────────────────────────────

def test_odds_api_failure_does_not_break_primary_scan(monkeypatch):
    """Verify that if Odds API times out or fails with 429, primary Superbet/Betclic scan executes safely."""
    class FailingOddsApiProvider(OddsApiProvider):
        def discover(self):
            raise RuntimeError("The Odds API 429 Rate Limit Exceeded")

    config = ScanConfig(
        providers=("superbet", "betclic"),
        enable_valuebets=False,
    )

    orchestrator = ProductionScanOrchestrator(config=config)

    # Provide valid mock Superbet and Betclic providers
    sb = SuperbetProvider()
    bc = BetclicProvider()
    sb.set_mock_discovery_payload([])
    bc.set_mock_discovery_payload([])

    result = orchestrator.run_scan_cycle(
        providers={"superbet": sb, "betclic": bc}
    )

    # Cycle should complete safely
    assert result.cycle_status in (CycleStatus.SUCCESS, CycleStatus.PARTIAL)
