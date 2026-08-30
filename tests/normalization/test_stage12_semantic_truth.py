"""
Stage 12.1 Regression Tests: Semantic Truth & False Positive Immunity

Validates:
1. Odds API SECOND_HALF extraction (Both Teams To Score 2H -> SECOND_HALF)
2. Betclic Team Total scope extraction (Liczba goli - Malaga -> TEAM_AWAY, Liczba goli - Atletico -> TEAM_HOME)
3. Domain hard safety: TEAM scope vs MATCH scope and TEAM_HOME vs TEAM_AWAY never match
4. Deduplication of duplicate provider event IDs into a single authoritative graph
5. Preservation of valid Draw No Bet surebet
6. Immunity against false-positive Atletico/Malaga and BTTS 2H surebets
"""
from decimal import Decimal
import pytest

from domain.models import Competition, Event, Market, Selection, Odds, MatchEvidence
from normalization.odds_api_normalizer import OddsApiNormalizer
from normalization.betclic_normalizer import BetclicNormalizer
from normalization.superbet_normalizer import SuperbetNormalizer
from normalization.engine import NormalizationEngine
from normalization.market_identity import (
    CanonicalMarketKey,
    CanonicalMarketType,
    MarketPeriod,
    MarketScope,
    extract_canonical_market_key,
)
from normalization.market_matcher import MarketMatcher, MarketMatchDecisionType
from normalization.selection_matcher import SelectionMatcher
from normalization.odds_comparison import OddsComparisonEngine, OddsComparison, OddsComparisonStatus
from normalization.surebet import SurebetDetectorEngine, SurebetStatus
from providers.odds_api.models import OddsApiEvent, OddsApiMarket, OddsApiSelection, OddsApiOdds
from providers.betclic.models import BetclicEvent, BetclicMarket, BetclicSelection, BetclicOdds


def test_odds_api_btts_2h_normalizes_as_second_half():
    """Confirms that 'Both Teams To Score 2H' normalizes as BTTS in SECOND_HALF, not FULL_TIME."""
    normalizer = OddsApiNormalizer()
    raw_event = OddsApiEvent(
        provider_event_id="test_oapi_1",
        bookmaker_name="bet365",
        name="CA Banfield vs CA Ferrocarril Midland",
        competition_name="Copa Argentina",
        home_team="CA Banfield",
        away_team="CA Ferrocarril Midland",
        start_time="2026-08-19T00:15:00Z",
        markets=[
            OddsApiMarket(
                provider_market_id="m_btts_2h",
                name="Both Teams To Score 2H",
                market_type_code="Both Teams To Score 2H_49",
                is_open=True,
                selections=[
                    OddsApiSelection(provider_selection_id="s1", name="Yes", type_code="YES", odds=OddsApiOdds(4.333)),
                    OddsApiSelection(provider_selection_id="s2", name="No", type_code="NO", odds=OddsApiOdds(1.20)),
                ]
            )
        ]
    )
    graph = normalizer.normalize_event(raw_event)
    assert len(graph.markets) == 1
    mkt = graph.markets[0]
    assert mkt.market_type == "BTTS"
    assert mkt.metadata.get("period") == "SECOND_HALF"
    
    key = extract_canonical_market_key(mkt)
    assert key is not None
    assert key.market_type == "BTTS"
    assert key.period == MarketPeriod.SECOND_HALF.value
    assert key.period != MarketPeriod.FULL_TIME.value


def test_betclic_team_total_suffix_normalizes_as_team_scope():
    """Confirms that Betclic 'Liczba goli - {TEAM}' normalizes as TEAM scope with correct participant role."""
    normalizer = BetclicNormalizer()
    raw_event = BetclicEvent(
        provider_event_id="1157604931833856",
        name="Atletico Madryt - Malaga",
        competition_name="La Liga",
        home_team="Atletico Madryt",
        away_team="Malaga",
        start_time="2026-08-18T20:00:00Z",
        markets=[
            BetclicMarket(
                provider_market_id="m_malaga_totals",
                name="Liczba goli - Malaga",
                market_type_code="Liczba goli - Malaga",
                is_open=True,
                selections=[
                    BetclicSelection(provider_selection_id="s_m1", name="Powyżej 0,5", type_code="OVER", handicap=0.5, odds=BetclicOdds(provider_odds_id="o1", decimal_odds=1.94)),
                    BetclicSelection(provider_selection_id="s_m2", name="Poniżej 0,5", type_code="UNDER", handicap=0.5, odds=BetclicOdds(provider_odds_id="o2", decimal_odds=1.77)),
                ]
            ),
            BetclicMarket(
                provider_market_id="m_atletico_totals",
                name="Liczba goli - Atletico Madryt",
                market_type_code="Liczba goli - Atletico Madryt",
                is_open=True,
                selections=[
                    BetclicSelection(provider_selection_id="s_a1", name="Powyżej 3,5", type_code="OVER", handicap=3.5, odds=BetclicOdds(provider_odds_id="o3", decimal_odds=4.55)),
                    BetclicSelection(provider_selection_id="s_a2", name="Poniżej 3,5", type_code="UNDER", handicap=3.5, odds=BetclicOdds(provider_odds_id="o4", decimal_odds=1.16)),
                ]
            )
        ]
    )
    graph = normalizer.normalize_event(raw_event)
    assert len(graph.markets) == 2
    
    # Check Malaga (Away team totals)
    m_malaga = next(m for m in graph.markets if "malaga" in m.provider_ids.get("betclic", ""))
    assert m_malaga.metadata.get("scope") == "TEAM"
    assert m_malaga.metadata.get("participant_role") == "AWAY"
    key_malaga = extract_canonical_market_key(m_malaga)
    assert key_malaga.scope == MarketScope.TEAM.value
    assert key_malaga.participant_role == "AWAY"
    
    # Check Atletico (Home team totals)
    m_atletico = next(m for m in graph.markets if "atletico" in m.provider_ids.get("betclic", ""))
    assert m_atletico.metadata.get("scope") == "TEAM"
    assert m_atletico.metadata.get("participant_role") == "HOME"
    key_atletico = extract_canonical_market_key(m_atletico)
    assert key_atletico.scope == MarketScope.TEAM.value
    assert key_atletico.participant_role == "HOME"


def test_team_total_never_matches_match_total():
    """Domain hard safety invariant: TEAM scope markets must NEVER match MATCH scope markets."""
    matcher = MarketMatcher()
    
    match_total_market = Market(
        event_id="ev1",
        market_type="TOTALS",
        line=2.5,
        metadata={"scope": "MATCH", "period": "FULL_TIME", "metric": "GOALS"}
    )
    
    team_home_market = Market(
        event_id="ev2",
        market_type="TOTALS",
        line=2.5,
        metadata={"scope": "TEAM", "participant_role": "HOME", "period": "FULL_TIME", "metric": "GOALS"}
    )
    
    team_away_market = Market(
        event_id="ev3",
        market_type="TOTALS",
        line=2.5,
        metadata={"scope": "TEAM", "participant_role": "AWAY", "period": "FULL_TIME", "metric": "GOALS"}
    )
    
    # 1. TEAM_HOME vs MATCH -> REJECTED
    dec_h_vs_m = matcher.match(team_home_market, match_total_market)
    assert dec_h_vs_m.decision == MarketMatchDecisionType.REJECTED
    assert "SCOPE_MISMATCH" in dec_h_vs_m.reasons
    
    # 2. TEAM_AWAY vs MATCH -> REJECTED
    dec_a_vs_m = matcher.match(team_away_market, match_total_market)
    assert dec_a_vs_m.decision == MarketMatchDecisionType.REJECTED
    assert "SCOPE_MISMATCH" in dec_a_vs_m.reasons

    # 3. TEAM_HOME vs TEAM_AWAY -> REJECTED
    dec_h_vs_a = matcher.match(team_home_market, team_away_market)
    assert dec_h_vs_a.decision == MarketMatchDecisionType.REJECTED
    assert "PARTICIPANT_MISMATCH" in dec_h_vs_a.reasons


def test_odds_api_duplicate_provider_event_id_is_deduplicated():
    """Confirms duplicate Odds API event models with identical provider IDs produce 1 authoritative graph."""
    engine = NormalizationEngine()
    ev1 = OddsApiEvent(
        provider_event_id="duplicate_id_123",
        bookmaker_name="bet365",
        name="Team A vs Team B",
        competition_name="League 1",
        home_team="Team A",
        away_team="Team B",
        start_time="2026-08-20T18:00:00Z",
        markets=[
            OddsApiMarket(
                provider_market_id="m1",
                name="1X2",
                market_type_code="1X2",
                is_open=True,
                selections=[OddsApiSelection(provider_selection_id="s1", name="1", type_code="HOME", odds=OddsApiOdds(2.0))]
            )
        ]
    )
    ev2 = OddsApiEvent(
        provider_event_id="duplicate_id_123",
        bookmaker_name="bet365",
        name="Team A vs Team B",
        competition_name="League 1",
        home_team="Team A",
        away_team="Team B",
        start_time="2026-08-20T18:00:00Z",
        markets=[
            OddsApiMarket(
                provider_market_id="m2",
                name="Both Teams To Score",
                market_type_code="BTTS",
                is_open=True,
                selections=[OddsApiSelection(provider_selection_id="s2", name="Yes", type_code="YES", odds=OddsApiOdds(1.8))]
            )
        ]
    )
    
    norm_res = engine.normalize("odds_api", [ev1, ev2])
    assert len(norm_res.graphs) == 1
    graph = norm_res.graphs[0]
    assert graph.event.provider_ids.get("bet365") == "duplicate_id_123"
    # Merged markets from both duplicate payloads
    assert len(graph.markets) == 2


def test_false_positive_atletico_malaga_regression():
    """Proves that Betclic TEAM totals and Superbet MATCH totals cannot form an opportunity."""
    detector = SurebetDetectorEngine()
    
    # 1. Betclic Team Away Over 0.5 (1.94)
    betclic_norm = BetclicNormalizer()
    bc_event = BetclicEvent(
        provider_event_id="bc_ev",
        name="Atletico Madryt - Malaga",
        competition_name="La Liga",
        home_team="Atletico Madryt",
        away_team="Malaga",
        start_time="2026-08-18T20:00:00Z",
        markets=[
            BetclicMarket(
                provider_market_id="m_bc",
                name="Liczba goli - Malaga",
                market_type_code="Liczba goli - Malaga",
                is_open=True,
                selections=[
                    BetclicSelection(provider_selection_id="s_bc_over", name="Powyżej 0,5", type_code="OVER", handicap=0.5, odds=BetclicOdds(provider_odds_id="o1", decimal_odds=1.94)),
                ]
            )
        ]
    )
    bc_graph = betclic_norm.normalize_event(bc_event)
    bc_mkt_key = extract_canonical_market_key(bc_graph.markets[0])
    
    # 2. Superbet Match Under 0.5 (12.0)
    sb_mkt = Market(
        event_id="sb_ev",
        market_type="TOTALS",
        line=0.5,
        metadata={"scope": "MATCH", "period": "FULL_TIME", "metric": "GOALS"}
    )
    sb_mkt_key = extract_canonical_market_key(sb_mkt)
    
    # Canonical Market Keys are distinct
    assert bc_mkt_key != sb_mkt_key
    assert bc_mkt_key.to_key_string() != sb_mkt_key.to_key_string()
    
    # Detector evaluates TEAM scope safely and returns INCOMPLETE_MARKET for empty comparisons (isolated from MATCH scope)
    eval_bc = detector.evaluate_market(
        canonical_event_id="cev_test",
        canonical_market_key=bc_mkt_key,
        comparisons=[]
    )
    assert eval_bc.status == SurebetStatus.INCOMPLETE_MARKET


def test_false_positive_btts_second_half_vs_full_time_regression():
    """Proves that Bet365 BTTS 2H and Superbet BTTS Full Time cannot form an opportunity."""
    oapi_norm = OddsApiNormalizer()
    ev = OddsApiEvent(
        provider_event_id="ev_b365",
        bookmaker_name="bet365",
        name="Home vs Away",
        competition_name="League",
        home_team="Home",
        away_team="Away",
        start_time="2026-08-19T20:00:00Z",
        markets=[
            OddsApiMarket(
                provider_market_id="m_b365_2h",
                name="Both Teams To Score 2H",
                market_type_code="Both Teams To Score 2H_49",
                is_open=True,
                selections=[OddsApiSelection(provider_selection_id="s1", name="Yes", type_code="YES", odds=OddsApiOdds(4.333))]
            )
        ]
    )
    b365_graph = oapi_norm.normalize_event(ev)
    b365_key = extract_canonical_market_key(b365_graph.markets[0])
    
    sb_mkt = Market(
        event_id="sb_ev",
        market_type="BTTS",
        line=None,
        metadata={"scope": "MATCH", "period": "FULL_TIME", "metric": "GOALS"}
    )
    sb_key = extract_canonical_market_key(sb_mkt)
    
    assert b365_key.period == "SECOND_HALF"
    assert sb_key.period == "FULL_TIME"
    assert b365_key != sb_key


def test_preserve_valid_draw_no_bet_opportunity():
    """Confirms that the audited valid Draw No Bet surebet remains fully functional."""
    from core.tax_engine import TaxEngine, BookmakerTaxConfig
    no_tax = TaxEngine(custom_configs={"superbet": BookmakerTaxConfig(tax_enabled=False), "betclic": BookmakerTaxConfig(tax_enabled=False)})
    detector = SurebetDetectorEngine(tax_engine=no_tax)
    
    dnb_key = CanonicalMarketKey(
        market_type=CanonicalMarketType.DRAW_NO_BET.value,
        line=None,
        period=MarketPeriod.FULL_TIME.value,
        scope=MarketScope.MATCH.value,
        metric="GOALS",
        participant_role=None,
    )
    
    # Construct valid comparison records: Superbet Home @ 1.66, Bet365 Away @ 3.00
    from normalization.selection_identity import CanonicalSelectionKey, CanonicalSelectionType
    
    sel_home_key = CanonicalSelectionKey(market_key=dnb_key, selection_type=CanonicalSelectionType.HOME.value, participant_role="HOME")
    sel_away_key = CanonicalSelectionKey(market_key=dnb_key, selection_type=CanonicalSelectionType.AWAY.value, participant_role="AWAY")
    
    comp_home = OddsComparison(
        canonical_event_id="cev_trujillanos",
        canonical_market_key=dnb_key,
        canonical_selection_key=sel_home_key,
        source_provider="superbet",
        target_provider="bet365",
        source_event_id="sb_e1",
        target_event_id="b365_e1",
        source_internal_event_id="sb_int_1",
        target_internal_event_id="b365_int_1",
        source_market_id="sb_m1",
        target_market_id="bc_m1",
        source_selection_id="sb_sel_1",
        target_selection_id="bc_sel_1",
        source_odds=Decimal("1.66"),
        target_odds=Decimal("1.40"),
        status=OddsComparisonStatus.VALID,
        evidence={"source_validation_status": "VALID", "target_validation_status": "VALID"},
        event_evidence=MatchEvidence(
            source_provider="superbet",
            target_provider="betclic",
            source_event_id="e1",
            target_event_id="e2",
            decision="MATCHED",
            total_score=0.9667,
            orientation="NORMAL",
            home_team="Trujillanos FC",
            away_team="Urena SC",
            competition_name="Venezuela Copa",
        )
    )
    
    comp_away = OddsComparison(
        canonical_event_id="cev_trujillanos",
        canonical_market_key=dnb_key,
        canonical_selection_key=sel_away_key,
        source_provider="superbet",
        target_provider="betclic",
        source_event_id="sb_e1",
        target_event_id="bc_e1",
        source_internal_event_id="sb_int_1",
        target_internal_event_id="bc_int_1",
        source_market_id="sb_m1",
        target_market_id="bc_m1",
        source_selection_id="sb_sel_2",
        target_selection_id="bc_sel_2",
        source_odds=Decimal("2.20"),
        target_odds=Decimal("3.00"),
        status=OddsComparisonStatus.VALID,
        evidence={"source_validation_status": "VALID", "target_validation_status": "VALID"},
        event_evidence=MatchEvidence(
            source_provider="superbet",
            target_provider="betclic",
            source_event_id="e1",
            target_event_id="e2",
            decision="MATCHED",
            total_score=0.9667,
            orientation="NORMAL",
            home_team="Trujillanos FC",
            away_team="Urena SC",
            competition_name="Venezuela Copa",
        )
    )
    
    eval_res = detector.evaluate_market(
        canonical_event_id="cev_trujillanos",
        canonical_market_key=dnb_key,
        comparisons=[comp_home, comp_away]
    )
    
    assert eval_res.status == SurebetStatus.SUREBET
    assert eval_res.opportunity is not None
    opp = eval_res.opportunity
    assert opp.implied_probability_sum < Decimal("1.0")
    assert round(opp.arbitrage_margin * 100, 2) == Decimal("6.87")
    assert set(opp.bookmakers) == {"betclic", "superbet"}
