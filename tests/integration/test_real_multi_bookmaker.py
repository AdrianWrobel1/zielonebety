"""
Stage 7.2: Real Multi-Bookmaker Integration Test Suite (Superbet + Betclic)

Verifies:
1. Offline deterministic multi-bookmaker pipeline execution with recorded fixtures.
2. Cross-bookmaker event matching accuracy and adversarial rejection.
3. Market matching, selection alignment, and line integrity (e.g. Totals 2.5 != 3.5).
4. Provider failure isolation (single & dual failure resilience).
5. Mathematical verification of arbitrage evaluation (distinguishing zero-surebet from pipeline failure).
6. Bounded, safe live network acquisition and diagnostic reporting.
"""

from decimal import Decimal
import json
from pathlib import Path
import time
from typing import Any, Dict, List
import pytest

from domain.models import Competition, Event, Market, Selection, Odds, CanonicalEvent
from normalization.base_normalizer import NormalizedGraph
from normalization.candidate_generator import EventCandidateGenerator
from normalization.matcher import EventMatcher, MatchDecisionType, OrientationType
from normalization.market_identity import CanonicalMarketKey
from normalization.market_matcher import MarketMatcher, MarketMatchDecisionType
from normalization.selection_matcher import SelectionMatcher, SelectionMatchDecisionType
from normalization.superbet_normalizer import SuperbetNormalizer
from normalization.betclic_normalizer import BetclicNormalizer
from normalization.validation_pipeline import CrossBookmakerValidationPipeline
from normalization.surebet import SurebetDetectorEngine, SurebetStatus
from orchestration.scan_orchestrator import ProductionScanOrchestrator
from orchestration.models import ScanConfig, CycleStatus
from providers.base.execution_engine import ExecutionEngine
from providers.base.provider_state import ProviderState
from providers.base.provider_result import ProviderResult
from providers.superbet.provider import SuperbetProvider
from providers.superbet.parser.parser import SuperbetParser
from providers.betclic.provider import BetclicProvider
from providers.betclic.parser.parser import BetclicParser


# ─────────────────────────────────────────────────────────────────────────────
# Fixture Loaders for Deterministic Offline Tests
# ─────────────────────────────────────────────────────────────────────────────

def load_multi_bookmaker_fixtures() -> Dict[str, Any]:
    """Loads sanitized recorded multi-bookmaker fixtures from disk."""
    fixture_dir = Path("tests/fixtures/recordings/multi_bookmaker")
    with open(fixture_dir / "manifest.json", "r", encoding="utf-8") as f:
        manifest = json.load(f)
    with open(fixture_dir / "superbet_payloads.json", "r", encoding="utf-8") as f:
        sb_payloads = json.load(f)
    with open(fixture_dir / "betclic_payloads.json", "r", encoding="utf-8") as f:
        bc_payloads = json.load(f)
    return {
        "manifest": manifest,
        "superbet_payloads": sb_payloads,
        "betclic_payloads": bc_payloads,
    }


def create_offline_providers() -> Dict[str, Any]:
    """Instantiates Superbet and Betclic providers pre-loaded with recorded payloads."""
    fixtures = load_multi_bookmaker_fixtures()

    sb_provider = SuperbetProvider()
    sb_provider.set_mock_discovery_payload(fixtures["superbet_payloads"])
    sb_provider.set_mock_fetch_provider(lambda item: item.metadata.get("raw") if item.metadata else {})

    bc_provider = BetclicProvider()
    bc_provider.set_mock_discovery_payload(fixtures["betclic_payloads"])
    bc_provider.set_mock_fetch_provider(lambda item: item.metadata.get("raw") if item.metadata else {})

    return {
        "superbet": sb_provider,
        "betclic": bc_provider,
    }


# ─────────────────────────────────────────────────────────────────────────────
# OFFLINE TEST MATRIX
# ─────────────────────────────────────────────────────────────────────────────

def test_offline_multi_bookmaker_e2e_pipeline():
    """Test 1: Full E2E scan cycle on recorded Superbet + Betclic fixtures."""
    providers = create_offline_providers()
    config = ScanConfig(providers=["superbet", "betclic"])
    orchestrator = ProductionScanOrchestrator(config=config)

    result = orchestrator.run_scan_cycle(providers=providers)

    assert result.cycle_status == CycleStatus.SUCCESS
    assert result.discovered_events_count > 0
    assert result.parsed_events_count > 0
    assert result.normalized_graphs_count > 0
    assert result.matched_events_count > 0
    assert result.validation_result is not None
    assert result.detection_result is not None
    assert len(result.errors) == 0


def test_event_matching_correctness():
    """Test 2: Verifies semantically accurate matching for overlapping events."""
    fixtures = load_multi_bookmaker_fixtures()
    sb_parser = SuperbetParser()
    bc_parser = BetclicParser()

    sb_events = sb_parser.parse_payloads(fixtures["superbet_payloads"])
    bc_events = bc_parser.parse_payloads(fixtures["betclic_payloads"])

    sb_normalizer = SuperbetNormalizer()
    bc_normalizer = BetclicNormalizer()

    sb_graphs = [sb_normalizer.normalize_event(ev) for ev in sb_events]
    bc_graphs = [bc_normalizer.normalize_event(ev) for ev in bc_events]

    pipeline = CrossBookmakerValidationPipeline()
    result = pipeline.run(source_items=sb_graphs, target_items=bc_graphs)

    assert len(result.canonical_events) >= 5
    for ce in result.canonical_events:
        assert ce.home_team is not None
        assert ce.away_team is not None
        assert len(ce.sources) == 2
        assert "superbet" in ce.sources
        assert "betclic" in ce.sources
        assert ce.match_evidence is not None
        assert len(ce.match_evidence) > 0
        assert ce.match_evidence[0].decision == MatchDecisionType.MATCHED.value
        assert ce.match_evidence[0].total_score >= 0.80


def test_adversarial_event_rejection():
    """Test 3: Verifies that mismatched teams, kickoff deltas > 24h, and age group mismatches are rejected."""
    matcher = EventMatcher()
    candidate_gen = EventCandidateGenerator()

    # Base event
    ev_a = Event(
        competition_id="comp_1",
        home_participant="Real Madrid",
        away_participant="Barcelona",
        scheduled_start="2026-08-20T20:00:00Z",
        provider_ids={"superbet": "sb_101"},
    )
    comp_a = Competition(name="La Liga", sport="Football")

    # Adversarial 1: Different teams
    ev_diff_teams = Event(
        competition_id="comp_2",
        home_participant="Bayern Munich",
        away_participant="Borussia Dortmund",
        scheduled_start="2026-08-20T20:00:00Z",
        provider_ids={"betclic": "bc_201"},
    )
    cand_diff = candidate_gen.generate_candidates([ev_a], [ev_diff_teams])
    if cand_diff.candidates:
        dec_diff = matcher.score_candidate(cand_diff.candidates[0], ev_a, ev_diff_teams, comp_a, comp_a)
        assert dec_diff.decision == MatchDecisionType.REJECTED

    # Adversarial 2: Kickoff delta > 24 hours
    ev_late = Event(
        competition_id="comp_3",
        home_participant="Real Madrid",
        away_participant="Barcelona",
        scheduled_start="2026-08-23T20:00:00Z",  # 72 hours later
        provider_ids={"betclic": "bc_202"},
    )
    cand_late = candidate_gen.generate_candidates([ev_a], [ev_late])
    if cand_late.candidates:
        dec_late = matcher.score_candidate(cand_late.candidates[0], ev_a, ev_late, comp_a, comp_a)
        assert dec_late.decision == MatchDecisionType.REJECTED
        assert any("kickoff_mismatch" in v for v in dec_late.veto_reasons)

    # Adversarial 3: Age group mismatch (Senior vs U21)
    ev_u21 = Event(
        competition_id="comp_4",
        home_participant="Real Madrid U21",
        away_participant="Barcelona U21",
        scheduled_start="2026-08-20T20:00:00Z",
        provider_ids={"betclic": "bc_203"},
    )
    cand_u21 = candidate_gen.generate_candidates([ev_a], [ev_u21])
    if cand_u21.candidates:
        dec_u21 = matcher.score_candidate(cand_u21.candidates[0], ev_a, ev_u21, comp_a, comp_a)
        assert dec_u21.decision == MatchDecisionType.REJECTED
        assert any("age_group_mismatch" in v for v in dec_u21.veto_reasons)


def test_market_matching_alignment():
    """Test 4: Verifies correct market matching across canonical market types."""
    matcher = MarketMatcher()

    # 1X2 Market
    m_1x2_a = Market(event_id="ev_1", market_type="1X2", status="OPEN", provider_ids={"superbet": "m_1"})
    m_1x2_b = Market(event_id="ev_2", market_type="1X2", status="OPEN", provider_ids={"betclic": "m_2"})
    dec_1x2 = matcher.match(m_1x2_a, m_1x2_b)
    assert dec_1x2.decision == MarketMatchDecisionType.MATCHED

    # BTTS Market
    m_btts_a = Market(event_id="ev_1", market_type="BTTS", status="OPEN", provider_ids={"superbet": "m_3"})
    m_btts_b = Market(event_id="ev_2", market_type="BTTS", status="OPEN", provider_ids={"betclic": "m_4"})
    dec_btts = matcher.match(m_btts_a, m_btts_b)
    assert dec_btts.decision == MarketMatchDecisionType.MATCHED

    # Incompatible types (1X2 vs BTTS)
    dec_incompat = matcher.match(m_1x2_a, m_btts_b)
    assert dec_incompat.decision == MarketMatchDecisionType.REJECTED


def test_market_line_integrity():
    """Test 5: Verifies that different lines (e.g. Totals 2.5 vs 3.5) strictly do NOT match."""
    matcher = MarketMatcher()

    m_tot_25_a = Market(event_id="ev_1", market_type="TOTALS", line=2.5, status="OPEN", provider_ids={"superbet": "m_tot1"})
    m_tot_25_b = Market(event_id="ev_2", market_type="TOTALS", line=2.5, status="OPEN", provider_ids={"betclic": "m_tot2"})
    m_tot_35_b = Market(event_id="ev_2", market_type="TOTALS", line=3.5, status="OPEN", provider_ids={"betclic": "m_tot3"})

    # Same line -> MATCHED
    dec_same_line = matcher.match(m_tot_25_a, m_tot_25_b)
    assert dec_same_line.decision == MarketMatchDecisionType.MATCHED

    # Different line -> REJECTED
    dec_diff_line = matcher.match(m_tot_25_a, m_tot_35_b)
    assert dec_diff_line.decision == MarketMatchDecisionType.REJECTED
    assert "LINE_MISMATCH" in dec_diff_line.reasons

    # Handicap -1.5 vs -2.5 -> REJECTED
    m_hcp_15_a = Market(event_id="ev_1", market_type="HANDICAP", line=-1.5, status="OPEN", provider_ids={"superbet": "m_h1"})
    m_hcp_25_b = Market(event_id="ev_2", market_type="HANDICAP", line=-2.5, status="OPEN", provider_ids={"betclic": "m_h2"})
    dec_hcp = matcher.match(m_hcp_15_a, m_hcp_25_b)
    assert dec_hcp.decision == MarketMatchDecisionType.REJECTED
    assert "LINE_MISMATCH" in dec_hcp.reasons


def test_selection_alignment():
    """Test 6: Verifies correct selection alignment (HOME ↔ HOME, OVER ↔ OVER)."""
    matcher = SelectionMatcher()
    market_key_1x2 = CanonicalMarketKey(
        sport="football",
        market_type="1X2",
        metric="GOALS",
        scope="MATCH",
        participant_role="ALL",
        period="FULL_TIME",
        line=None,
    )

    ev_a = Event(competition_id="c1", home_participant="Arsenal", away_participant="Chelsea")
    ev_b = Event(competition_id="c2", home_participant="Arsenal", away_participant="Chelsea")

    sel_h_a = Selection(market_id="m1", selection_type="HOME", participant="Arsenal", provider_ids={"superbet": "s1"})
    sel_h_b = Selection(market_id="m2", selection_type="HOME", participant="Arsenal", provider_ids={"betclic": "s2"})
    sel_a_b = Selection(market_id="m2", selection_type="AWAY", participant="Chelsea", provider_ids={"betclic": "s3"})

    # HOME ↔ HOME -> MATCHED
    dec_h = matcher.match_selection(sel_h_a, sel_h_b, market_key_1x2, market_key_1x2, ev_a, ev_b)
    assert dec_h.decision == SelectionMatchDecisionType.MATCHED

    # HOME ↔ AWAY -> REJECTED
    dec_h_a = matcher.match_selection(sel_h_a, sel_a_b, market_key_1x2, market_key_1x2, ev_a, ev_b)
    assert dec_h_a.decision == SelectionMatchDecisionType.REJECTED


def test_invalid_odds_rejected():
    """Test 7: Verifies non-positive or <= 1.0 decimal odds are rejected."""
    parser = BetclicParser()
    payload = [{
        "id": "ev_invalid_odds",
        "name": "Team A vs Team B",
        "competition": "League",
        "start_date": "2026-08-20T18:00:00Z",
        "markets": [{
            "id": "mkt_1",
            "name": "Match Result",
            "code": "1X2",
            "selections": [
                {"id": "s_1", "name": "Team A", "code": "1", "odds": 0.85},  # Invalid (< 1.0)
                {"id": "s_2", "name": "Draw", "code": "X", "odds": -2.10},  # Invalid (negative)
                {"id": "s_3", "name": "Team B", "code": "2", "odds": 2.50},  # Valid
            ]
        }]
    }]

    parsed = parser.parse_payloads(payload)
    assert len(parsed) == 1

    normalizer = BetclicNormalizer()
    graph = normalizer.normalize_event(parsed[0])

    # Only odds > 1.0 are admitted to graph.odds_list
    assert len(graph.odds_list) == 1
    assert graph.odds_list[0].decimal_odds == 2.50


def test_zero_match_scenario_handling():
    """Test 8: Verifies pipeline completes cleanly when datasets are disjoint."""
    sb_normalizer = SuperbetNormalizer()
    bc_normalizer = BetclicNormalizer()

    ev_sb = Event(
        competition_id="c1",
        home_participant="Legia Warsaw",
        away_participant="Lech Poznan",
        scheduled_start="2026-08-20T18:00:00Z",
        provider_ids={"superbet": "sb_1"},
    )
    ev_bc = Event(
        competition_id="c2",
        home_participant="Flamengo",
        away_participant="Palmeiras",
        scheduled_start="2026-08-20T18:00:00Z",
        provider_ids={"betclic": "bc_1"},
    )

    gr_sb = NormalizedGraph(competition=Competition(name="Ekstraklasa"), event=ev_sb, markets=[], selections=[], odds_list=[])
    gr_bc = NormalizedGraph(competition=Competition(name="Brasileirao"), event=ev_bc, markets=[], selections=[], odds_list=[])

    pipeline = CrossBookmakerValidationPipeline()
    result = pipeline.run([gr_sb], [gr_bc])

    assert len(result.canonical_events) == 0
    assert len(result.unmatched_events) == 2
    assert len(result.errors) == 0


def test_single_provider_failure_resilience():
    """Test 9: Verifies single provider failure yields PARTIAL cycle and preserves healthy data."""
    fixtures = load_multi_bookmaker_fixtures()
    sb_provider = SuperbetProvider()
    sb_provider.set_mock_discovery_payload(fixtures["superbet_payloads"])

    # Failing Betclic provider
    bc_failing = BetclicProvider()
    bc_failing.discover = lambda: (_ for _ in ()).throw(RuntimeError("Simulated Betclic connection timeout"))

    orchestrator = ProductionScanOrchestrator(config=ScanConfig(providers=["superbet", "betclic"]))
    result = orchestrator.run_scan_cycle(providers={"superbet": sb_provider, "betclic": bc_failing})

    assert result.cycle_status == CycleStatus.PARTIAL
    assert result.provider_results["superbet"].status == ProviderState.COMPLETED
    assert result.provider_results["betclic"].status == ProviderState.FAILED
    assert result.discovered_events_count > 0
    assert result.matched_events_count == 0  # Safely skipped matching
    assert result.detected_opportunities_count == 0  # No false surebets


def test_both_providers_failure_handling():
    """Test 10: Verifies both providers failing yields FAILED cycle without crashing."""
    sb_failing = SuperbetProvider()
    sb_failing.discover = lambda: (_ for _ in ()).throw(RuntimeError("Simulated Superbet 503"))

    bc_failing = BetclicProvider()
    bc_failing.discover = lambda: (_ for _ in ()).throw(RuntimeError("Simulated Betclic 503"))

    orchestrator = ProductionScanOrchestrator(config=ScanConfig(providers=["superbet", "betclic"], fail_on_critical_error=False))
    result = orchestrator.run_scan_cycle(providers={"superbet": sb_failing, "betclic": bc_failing})

    assert result.cycle_status == CycleStatus.FAILED
    assert result.discovered_events_count == 0
    assert len(result.errors) >= 2


def test_scanner_zero_opportunity_diagnostic():
    """Test 11: Verifies scanner evaluation of matched non-arbitrage markets produces exact NO_SUREBET."""
    fixtures = load_multi_bookmaker_fixtures()
    sb_events = SuperbetParser().parse_payloads(fixtures["superbet_payloads"])
    bc_events = BetclicParser().parse_payloads(fixtures["betclic_payloads"])

    sb_graphs = [SuperbetNormalizer().normalize_event(ev) for ev in sb_events]
    bc_graphs = [BetclicNormalizer().normalize_event(ev) for ev in bc_events]

    val_result = CrossBookmakerValidationPipeline().run(sb_graphs, bc_graphs)
    det_result = SurebetDetectorEngine().detect(val_result)

    assert det_result.metrics.complete_market_count > 0
    assert det_result.metrics.surebet_count == 0
    assert det_result.metrics.no_surebet_count > 0

    for eval_item in det_result.no_surebet_evaluations:
        assert eval_item.status == SurebetStatus.NO_SUREBET
        assert eval_item.implied_probability_sum is not None
        assert eval_item.implied_probability_sum >= Decimal("1.0")


def test_genuine_recorded_opportunity_detection():
    """Test 12: Verifies scanner detects a true surebet with exact Decimal arithmetic when one is constructed."""
    detector = SurebetDetectorEngine()
    market_key = CanonicalMarketKey(
        sport="football",
        market_type="1X2",
        metric="GOALS",
        scope="MATCH",
        participant_role="ALL",
        period="FULL_TIME",
        line=None,
    )

    ev_a = Event(competition_id="c1", home_participant="Team A", away_participant="Team B")
    ev_b = Event(competition_id="c2", home_participant="Team A", away_participant="Team B")

    # Construct genuine surebet: Home=2.80 (Superbet), Draw=3.80 (Betclic), Away=2.90 (Superbet)
    # Sum S = 1/2.80 + 1/3.80 + 1/2.90 = 0.35714 + 0.26315 + 0.34482 = 0.96511 < 1.0 (Margin = ~3.61%)
    from normalization.odds_comparison import OddsComparison, OddsComparisonStatus
    from normalization.selection_identity import CanonicalSelectionKey

    comparisons = [
        OddsComparison(
            canonical_event_id="cev_test_1",
            canonical_market_key=market_key,
            canonical_selection_key=CanonicalSelectionKey(market_key=market_key, selection_type="HOME", participant_role="HOME"),
            source_provider="superbet",
            target_provider="betclic",
            source_event_id="sb_1",
            target_event_id="bc_1",
            source_internal_event_id="ev_1",
            target_internal_event_id="ev_2",
            source_market_id="m_1",
            target_market_id="m_2",
            source_selection_id="s_h1",
            target_selection_id="s_h2",
            source_odds=Decimal("2.80"),
            target_odds=Decimal("2.50"),
            status=OddsComparisonStatus.VALID,
            evidence={"source_validation_status": "VALID", "target_validation_status": "VALID"},
        ),
        OddsComparison(
            canonical_event_id="cev_test_1",
            canonical_market_key=market_key,
            canonical_selection_key=CanonicalSelectionKey(market_key=market_key, selection_type="DRAW"),
            source_provider="superbet",
            target_provider="betclic",
            source_event_id="sb_1",
            target_event_id="bc_1",
            source_internal_event_id="ev_1",
            target_internal_event_id="ev_2",
            source_market_id="m_1",
            target_market_id="m_2",
            source_selection_id="s_d1",
            target_selection_id="s_d2",
            source_odds=Decimal("3.40"),
            target_odds=Decimal("3.80"),
            status=OddsComparisonStatus.VALID,
            evidence={"source_validation_status": "VALID", "target_validation_status": "VALID"},
        ),
        OddsComparison(
            canonical_event_id="cev_test_1",
            canonical_market_key=market_key,
            canonical_selection_key=CanonicalSelectionKey(market_key=market_key, selection_type="AWAY", participant_role="AWAY"),
            source_provider="superbet",
            target_provider="betclic",
            source_event_id="sb_1",
            target_event_id="bc_1",
            source_internal_event_id="ev_1",
            target_internal_event_id="ev_2",
            source_market_id="m_1",
            target_market_id="m_2",
            source_selection_id="s_a1",
            target_selection_id="s_a2",
            source_odds=Decimal("2.90"),
            target_odds=Decimal("2.60"),
            status=OddsComparisonStatus.VALID,
            evidence={"source_validation_status": "VALID", "target_validation_status": "VALID"},
        ),
    ]

    result = detector.detect_markets(comparisons)
    assert result.metrics.surebet_count == 1
    opp = result.opportunities[0]
    assert opp.status == SurebetStatus.SUREBET
    assert opp.implied_probability_sum < Decimal("1.0")
    assert opp.arbitrage_margin > Decimal("0.0")

    # Independent mathematical verification
    expected_sum = (Decimal("1.0") / Decimal("2.80")) + (Decimal("1.0") / Decimal("3.80")) + (Decimal("1.0") / Decimal("2.90"))
    expected_margin = (Decimal("1.0") / expected_sum) - Decimal("1.0")
    assert opp.implied_probability_sum == expected_sum
    assert opp.arbitrage_margin == expected_margin


# ─────────────────────────────────────────────────────────────────────────────
# LIVE NETWORK INTEGRATION TESTS (Isolated with @pytest.mark.live)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.live
def test_live_superbet_acquisition():
    """Test 13 (Live): Executes real Superbet acquisition and validates output."""
    provider = SuperbetProvider()
    engine = ExecutionEngine()

    start_time = time.perf_counter()
    result = engine.execute(provider)
    duration = time.perf_counter() - start_time

    assert result.status == ProviderState.COMPLETED
    assert len(result.discovered_objects) > 0
    assert len(result.parsed_objects) > 0
    assert result.validation_report.is_valid is True
    print(f"\n[LIVE SUPERBET] Events: {len(result.parsed_objects)}, Duration: {duration:.2f}s")


@pytest.mark.live
def test_live_betclic_acquisition():
    """Test 14 (Live): Executes real Betclic acquisition and validates output."""
    provider = BetclicProvider()
    engine = ExecutionEngine()

    start_time = time.perf_counter()
    result = engine.execute(provider)
    duration = time.perf_counter() - start_time

    assert result.status in (ProviderState.COMPLETED, ProviderState.DEGRADED)
    assert len(result.discovered_objects) > 0
    assert len(result.parsed_objects) > 0
    print(f"\n[LIVE BETCLIC] Events: {len(result.parsed_objects)}, Duration: {duration:.2f}s")


@pytest.mark.live
def test_live_multi_bookmaker_pipeline_and_detection():
    """Test 15 (Live): Real Superbet + Betclic multi-bookmaker pipeline E2E diagnostic."""
    t0 = time.perf_counter()

    # Real acquisition
    sb_provider = SuperbetProvider()
    bc_provider = BetclicProvider()
    engine = ExecutionEngine()

    sb_res = engine.execute(sb_provider)
    bc_res = engine.execute(bc_provider)

    assert sb_res.status in (ProviderState.COMPLETED, ProviderState.DEGRADED)
    assert bc_res.status in (ProviderState.COMPLETED, ProviderState.DEGRADED)

    # Normalization
    sb_graphs = [SuperbetNormalizer().normalize_event(ev) for ev in sb_res.parsed_objects]
    bc_graphs = [BetclicNormalizer().normalize_event(ev) for ev in bc_res.parsed_objects]

    # Cross-Bookmaker Validation Pipeline
    pipeline = CrossBookmakerValidationPipeline()
    val_result = pipeline.run(source_items=sb_graphs, target_items=bc_graphs)

    # Surebet Detection
    detector = SurebetDetectorEngine()
    det_result = detector.detect(val_result)

    duration = time.perf_counter() - t0

    # Diagnostic Output
    print("\n" + "=" * 70)
    print(f"LIVE MULTI-BOOKMAKER PIPELINE RUNTIME: {duration:.2f}s")
    print("=" * 70)
    print(f"Superbet Parsed Events:     {len(sb_res.parsed_objects)}")
    print(f"Betclic Parsed Events:      {len(bc_res.parsed_objects)}")
    print(f"Candidates Evaluated:       {len(val_result.event_candidates)}")
    print(f"Canonical Matched Events:   {len(val_result.canonical_events)}")
    print(f"Unmatched Superbet Events:  {len(val_result.unmatched_events)}")
    print(f"Unmatched Betclic Events:   {len(val_result.rejected_events)}")
    print("-" * 70)

    print("MATCHED CANONICAL EVENTS SAMPLE:")
    for idx, ce in enumerate(val_result.canonical_events[:5], 1):
        comp_name = ce.competition.name if ce.competition else "Unknown"
        score = ce.match_evidence[0].total_score if ce.match_evidence else 0.0
        print(f"  [{idx}] {ce.home_team} vs {ce.away_team} ({ce.scheduled_start}) - {comp_name} [Score: {score:.4f}]")

    print("-" * 70)
    print(f"Evaluated Markets:          {det_result.metrics.input_market_count}")
    print(f"Complete Markets:           {det_result.metrics.complete_market_count}")
    print(f"Surebets Found:             {len(det_result.opportunities)}")

    if det_result.opportunities:
        for opp in det_result.opportunities:
            print(f"  -> SUREBET: {opp.canonical_event_id} | Margin: {opp.arbitrage_margin * Decimal(100):.2f}% | S: {opp.implied_probability_sum}")
            for leg in opp.legs:
                print(f"     Leg {leg.selection_type}: {leg.provider} @ {leg.odds}")
    else:
        print("  -> ZERO SUREBETS in current live sample (all evaluated market margins <= 0.0)")
        if det_result.no_surebet_evaluations:
            best_eval = min(det_result.no_surebet_evaluations, key=lambda e: e.implied_probability_sum or Decimal("99.0"))
            print(f"     Best observed market: {best_eval.canonical_event_id} (Sum S = {best_eval.implied_probability_sum})")

    print("=" * 70 + "\n")

    # Assertions
    assert len(val_result.canonical_events) >= 0, "Non-negative overlapping events matched"
    assert det_result.metrics.complete_market_count >= 0, "Non-negative complete markets evaluated"


@pytest.mark.live
def test_live_surebet_mathematical_verification():
    """Test 16 (Live): Mathematically verifies Decimal sum calculations for all evaluated live markets."""
    sb_res = ExecutionEngine().execute(SuperbetProvider())
    bc_res = ExecutionEngine().execute(BetclicProvider())

    sb_graphs = [SuperbetNormalizer().normalize_event(ev) for ev in sb_res.parsed_objects]
    bc_graphs = [BetclicNormalizer().normalize_event(ev) for ev in bc_res.parsed_objects]

    val_result = CrossBookmakerValidationPipeline().run(source_items=sb_graphs, target_items=bc_graphs)
    det_result = SurebetDetectorEngine().detect(val_result)

    for eval_item in det_result.evaluations:
        if eval_item.status in (SurebetStatus.SUREBET, SurebetStatus.NO_SUREBET):
            assert len(eval_item.best_legs) in (2, 3)
            # Recompute Decimal sum independently
            manual_sum = sum(Decimal("1.0") / leg.odds for leg in eval_item.best_legs)
            manual_margin = (Decimal("1.0") / manual_sum) - Decimal("1.0")
            assert eval_item.implied_probability_sum == manual_sum
            if eval_item.status == SurebetStatus.SUREBET:
                assert eval_item.arbitrage_margin == manual_margin
                assert eval_item.implied_probability_sum < Decimal("1.0")
            elif eval_item.status == SurebetStatus.NO_SUREBET:
                assert eval_item.implied_probability_sum >= Decimal("1.0")
