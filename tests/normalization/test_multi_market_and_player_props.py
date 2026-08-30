"""
Comprehensive Multi-Market & Player Props Matching and Evaluation Tests (Stage 10.11)
"""

import pytest
from normalization.superbet_normalizer import SuperbetNormalizer
from normalization.betclic_normalizer import BetclicNormalizer
from normalization.odds_api_normalizer import OddsApiNormalizer
from normalization.market_matcher import MarketMatcher, MarketMatchDecisionType
from normalization.selection_matcher import SelectionMatcher
from normalization.validation_pipeline import CrossBookmakerValidationPipeline
from normalization.surebet import SurebetDetectorEngine
from providers.superbet.models import (
    SuperbetEvent,
    SuperbetMarket,
    SuperbetSelection,
    SuperbetOdds,
)
from providers.betclic.models import (
    BetclicEvent,
    BetclicMarket,
    BetclicSelection,
    BetclicOdds,
)
from providers.odds_api.models import (
    OddsApiEvent,
    OddsApiMarket,
    OddsApiSelection,
    OddsApiOdds,
)


def test_cross_provider_multi_market_matching_and_surebet():
    """Verify that TOTALS, BTTS, and DOUBLE CHANCE match and evaluate across Superbet and Betclic."""
    # 1. Superbet Event with 1X2, BTTS, and Totals 2.5
    sb_event = SuperbetEvent(
        event_id="sb_100",
        name="Real Madrid vs Barcelona",
        home_team="Real Madrid",
        away_team="Barcelona",
        competition_name="La Liga",
        start_time="2026-08-25T20:00:00Z",
        markets=[
            SuperbetMarket(
                market_id="sb_m_1x2",
                name="Wynik meczu",
                is_active=True,
                selections=[
                    SuperbetSelection(selection_id="s1", name="1", odds=SuperbetOdds(decimal_odds=2.10)),
                    SuperbetSelection(selection_id="s2", name="X", odds=SuperbetOdds(decimal_odds=3.60)),
                    SuperbetSelection(selection_id="s3", name="2", odds=SuperbetOdds(decimal_odds=3.40)),
                ],
            ),
            SuperbetMarket(
                market_id="sb_m_btts",
                name="Obie drużyny strzelą",
                is_active=True,
                selections=[
                    SuperbetSelection(selection_id="sb1", name="Tak", odds=SuperbetOdds(decimal_odds=1.70)),
                    SuperbetSelection(selection_id="sb2", name="Nie", odds=SuperbetOdds(decimal_odds=2.15)),
                ],
            ),
            SuperbetMarket(
                market_id="sb_m_tot25",
                name="Liczba goli 2.5",
                is_active=True,
                specifiers={"total": "2.5"},
                selections=[
                    SuperbetSelection(selection_id="st1", name="Powyżej 2.5", odds=SuperbetOdds(decimal_odds=1.85)),
                    SuperbetSelection(selection_id="st2", name="Poniżej 2.5", odds=SuperbetOdds(decimal_odds=1.95)),
                ],
            ),
        ],
    )

    # 2. Betclic Event with 1X2, BTTS, and Totals 2.5
    bc_event = BetclicEvent(
        provider_event_id="bc_200",
        name="Real Madrid - Barcelona",
        home_team="Real Madrid",
        away_team="Barcelona",
        competition_name="La Liga",
        start_time="2026-08-25T20:00:00Z",
        markets=[
            BetclicMarket(
                provider_market_id="bc_m_1x2",
                name="Wynik meczu",
                market_type_code="1X2",
                is_open=True,
                selections=[
                    BetclicSelection(provider_selection_id="bcs1", name="Real Madrid", type_code="HOME", odds=BetclicOdds(provider_odds_id="o1", decimal_odds=2.15)),
                    BetclicSelection(provider_selection_id="bcs2", name="Remis", type_code="DRAW", odds=BetclicOdds(provider_odds_id="o2", decimal_odds=3.50)),
                    BetclicSelection(provider_selection_id="bcs3", name="Barcelona", type_code="AWAY", odds=BetclicOdds(provider_odds_id="o3", decimal_odds=3.30)),
                ],
            ),
            BetclicMarket(
                provider_market_id="bc_m_btts",
                name="Obie drużyny strzelą gola",
                market_type_code="BTTS",
                is_open=True,
                selections=[
                    BetclicSelection(provider_selection_id="bcb1", name="Tak", type_code="YES", odds=BetclicOdds(provider_odds_id="o4", decimal_odds=1.75)),
                    BetclicSelection(provider_selection_id="bcb2", name="Nie", type_code="NO", odds=BetclicOdds(provider_odds_id="o5", decimal_odds=2.05)),
                ],
            ),
            BetclicMarket(
                provider_market_id="bc_m_tot25",
                name="Liczba goli - 2.5",
                market_type_code="TOTALS",
                is_open=True,
                selections=[
                    BetclicSelection(provider_selection_id="bct1", name="Powyżej 2.5", type_code="OVER", handicap=2.5, odds=BetclicOdds(provider_odds_id="o6", decimal_odds=1.80)),
                    BetclicSelection(provider_selection_id="bct2", name="Poniżej 2.5", type_code="UNDER", handicap=2.5, odds=BetclicOdds(provider_odds_id="o7", decimal_odds=2.00)),
                ],
            ),
        ],
    )

    sb_graph = SuperbetNormalizer().normalize_event(sb_event)
    bc_graph = BetclicNormalizer().normalize_event(bc_event)

    pipeline = CrossBookmakerValidationPipeline()
    val_result = pipeline.run(source_items=[sb_graph], target_items=[bc_graph])

    assert len(val_result.canonical_events) == 1
    rec = val_result.event_validation_records[0]

    # Must match all 3 markets: 1X2, BTTS, TOTALS
    matched_mkts = {m.canonical_market_key.market_type for m in rec.matched_markets}
    assert matched_mkts == {"1X2", "BTTS", "TOTALS"}

    # Run Surebet Detector
    detector = SurebetDetectorEngine()
    det_result = detector.detect(val_result)
    assert len(det_result.evaluations) == 3
    assert all(e.status.value in ("SUREBET", "NO_SUREBET") for e in det_result.evaluations)


def test_player_props_matching_same_player():
    """Verify player prop market for same player matches across providers."""
    sb_event = SuperbetEvent(
        event_id="sb_p1",
        name="Poland vs Germany",
        home_team="Poland",
        away_team="Germany",
        competition_name="UEFA Nations League",
        markets=[
            SuperbetMarket(
                market_id="sb_m_lew_goal",
                name="Zawodnik - strzeli gola",
                is_active=True,
                specifiers={"player": "Lewandowski, Robert"},
                selections=[
                    SuperbetSelection(
                        selection_id="sb_s_lew",
                        name="Lewandowski, Robert",
                        odds=SuperbetOdds(decimal_odds=2.20),
                    )
                ],
            )
        ],
    )

    bc_event = BetclicEvent(
        provider_event_id="bc_p1",
        name="Poland - Germany",
        home_team="Poland",
        away_team="Germany",
        competition_name="UEFA Nations League",
        markets=[
            BetclicMarket(
                provider_market_id="bc_m_scorer",
                name="Strzelec gola",
                market_type_code="PLAYER_GOALS",
                is_open=True,
                selections=[
                    BetclicSelection(
                        provider_selection_id="bc_s_lew",
                        name="Robert Lewandowski",
                        type_code="YES",
                        odds=BetclicOdds(provider_odds_id="o1", decimal_odds=2.35),
                    ),
                    BetclicSelection(
                        provider_selection_id="bc_s_ziel",
                        name="Piotr Zieliński",
                        type_code="YES",
                        odds=BetclicOdds(provider_odds_id="o1b", decimal_odds=4.50),
                    ),
                    BetclicSelection(
                        provider_selection_id="bc_s_swid",
                        name="Karol Świderski",
                        type_code="YES",
                        odds=BetclicOdds(provider_odds_id="o1c", decimal_odds=3.20),
                    ),
                ],
            )
        ],
    )

    sb_graph = SuperbetNormalizer().normalize_event(sb_event)
    bc_graph = BetclicNormalizer().normalize_event(bc_event)

    matcher = MarketMatcher()
    lew_bc_mkt = next(m for m in bc_graph.markets if m.metadata.get("player_name") == "Robert Lewandowski")
    decision = matcher.match(
        source_market=sb_graph.markets[0],
        target_market=lew_bc_mkt,
    )

    assert decision.decision == MarketMatchDecisionType.MATCHED
    assert decision.canonical_market_key.market_type == "PLAYER_GOALS"
    assert decision.canonical_market_key.player_name == "robert lewandowski"


def test_player_props_rejection_different_player():
    """Verify player prop market for DIFFERENT players is strictly rejected."""
    sb_event = SuperbetEvent(
        event_id="sb_p1",
        name="Poland vs Germany",
        home_team="Poland",
        away_team="Germany",
        competition_name="UEFA Nations League",
        markets=[
            SuperbetMarket(
                market_id="sb_m_lew_goal",
                name="Zawodnik - strzeli gola",
                is_active=True,
                specifiers={"player": "Lewandowski, Robert"},
                selections=[
                    SuperbetSelection(
                        selection_id="sb_s_lew",
                        name="Lewandowski, Robert",
                        odds=SuperbetOdds(decimal_odds=2.20),
                    )
                ],
            )
        ],
    )

    bc_event = BetclicEvent(
        provider_event_id="bc_p1",
        name="Poland - Germany",
        home_team="Poland",
        away_team="Germany",
        competition_name="UEFA Nations League",
        markets=[
            BetclicMarket(
                provider_market_id="bc_m_scorer",
                name="Strzelec gola",
                market_type_code="PLAYER_GOALS",
                is_open=True,
                selections=[
                    BetclicSelection(
                        provider_selection_id="bc_s_muller",
                        name="Thomas Müller",
                        type_code="YES",
                        odds=BetclicOdds(provider_odds_id="o2", decimal_odds=3.10),
                    ),
                    BetclicSelection(
                        provider_selection_id="bc_s_sane",
                        name="Leroy Sané",
                        type_code="YES",
                        odds=BetclicOdds(provider_odds_id="o3", decimal_odds=2.80),
                    ),
                    BetclicSelection(
                        provider_selection_id="bc_s_musiala",
                        name="Jamal Musiala",
                        type_code="YES",
                        odds=BetclicOdds(provider_odds_id="o4", decimal_odds=2.50),
                    ),
                ],
            )
        ],
    )

    sb_graph = SuperbetNormalizer().normalize_event(sb_event)
    bc_graph = BetclicNormalizer().normalize_event(bc_event)

    matcher = MarketMatcher()
    muller_bc_mkt = next(m for m in bc_graph.markets if m.metadata.get("player_name") == "Thomas Müller")
    decision = matcher.match(
        source_market=sb_graph.markets[0],
        target_market=muller_bc_mkt,
    )

    assert decision.decision == MarketMatchDecisionType.REJECTED
    assert "PLAYER_MISMATCH" in decision.reasons


def test_betclic_opta_player_props_decomposition():
    """Verify Betclic OPTA multi-player market decomposes into canonical player prop entities."""
    bc_event = BetclicEvent(
        provider_event_id="bc_opta_1",
        name="Gil Vicente - Casa Pia Atletico",
        home_team="Gil Vicente",
        away_team="Casa Pia Atletico",
        competition_name="Liga Portugal",
        start_time="2026-08-25T19:15:00Z",
        markets=[
            BetclicMarket(
                provider_market_id="bc_m_shots",
                name="Liczba strzałów zawodnika (OPTA)",
                market_type_code="PLAYER_SHOTS",
                is_open=True,
                selections=[
                    BetclicSelection(
                        provider_selection_id="s1",
                        name="Tidjany Touré Powyżej 2,5",
                        type_code="OVER",
                        odds=BetclicOdds(provider_odds_id="o1", decimal_odds=1.28),
                    ),
                    BetclicSelection(
                        provider_selection_id="s2",
                        name="Tidjany Touré Powyżej 3,5",
                        type_code="OVER",
                        odds=BetclicOdds(provider_odds_id="o2", decimal_odds=1.72),
                    ),
                    BetclicSelection(
                        provider_selection_id="s3",
                        name="Murilo Costa Powyżej 1,5",
                        type_code="OVER",
                        odds=BetclicOdds(provider_odds_id="o3", decimal_odds=1.15),
                    ),
                    BetclicSelection(
                        provider_selection_id="s4",
                        name="Carlos Eduardo (10/08/2002) Powyżej 0,5",
                        type_code="OVER",
                        odds=BetclicOdds(provider_odds_id="o4", decimal_odds=1.18),
                    ),
                ],
            )
        ],
    )

    graph = BetclicNormalizer().normalize_event(bc_event)
    assert len(graph.markets) == 4
    assert len(graph.selections) == 4
    assert len(graph.odds_list) == 4

    # Verify Tidjany Toure Over 2.5
    m1 = next(m for m in graph.markets if m.metadata.get("player_name") == "Tidjany Touré" and m.line == 2.5)
    assert m1.market_type == "PLAYER_SHOTS"
    s1 = next(s for s in graph.selections if s.market_id == m1.internal_id)
    assert s1.selection_type == "OVER"
    assert s1.participant == "Tidjany Touré"
    assert s1.line == 2.5

    # Verify Carlos Eduardo date stripping
    m4 = next(m for m in graph.markets if m.metadata.get("player_name") == "Carlos Eduardo")
    assert m4.line == 0.5


def test_execution_market_engine_extract_quotes():
    """Verify ExecutionMarketEngine extracts normalized quotes from graphs."""
    from scanner.execution_providers import ExecutionMarketEngine
    bc_event = BetclicEvent(
        provider_event_id="bc_opta_2",
        name="Gil Vicente - Casa Pia Atletico",
        home_team="Gil Vicente",
        away_team="Casa Pia Atletico",
        competition_name="Liga Portugal",
        start_time="2026-08-25T19:15:00Z",
        markets=[
            BetclicMarket(
                provider_market_id="bc_m_shots",
                name="Liczba strzałów zawodnika (OPTA)",
                market_type_code="PLAYER_SHOTS",
                is_open=True,
                selections=[
                    BetclicSelection(
                        provider_selection_id="s1",
                        name="Tidjany Touré Powyżej 2,5",
                        type_code="OVER",
                        odds=BetclicOdds(provider_odds_id="o1", decimal_odds=1.28),
                    ),
                ],
            )
        ],
    )
    graph = BetclicNormalizer().normalize_event(bc_event)
    engine = ExecutionMarketEngine()
    quotes = engine.extract_quotes_from_graphs([graph], stat_type="SHOTS")

    assert len(quotes) == 1
    assert quotes[0].bookmaker == "Betclic"
    assert quotes[0].player == "Tidjany Touré"
    assert quotes[0].line == 2.5
    assert quotes[0].side == "OVER"
    assert quotes[0].odds == 1.28
    assert quotes[0].active is True

