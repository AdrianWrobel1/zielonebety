"""
Stage 20B Tests — Team Props Integration & Isolation

Verifies:
1. TEAM vs MATCH scope cannot match.
2. TEAM vs PLAYER scope cannot match.
3. HOME team prop cannot match AWAY team prop.
4. Different team-prop lines cannot match.
5. Same TEAM prop + same team + same line can match across bookmakers.
6. End-to-end Team Props surebet opportunity detection between Superbet and Betclic.
"""

from decimal import Decimal
import pytest

from domain.models import Competition, Event, Market, Selection, Odds
from normalization.base_normalizer import NormalizedGraph
from normalization.market_identity import extract_canonical_market_key, CanonicalMarketKey
from normalization.market_matcher import MarketMatcher, MarketMatchDecisionType
from normalization.selection_identity import extract_canonical_selection_key, CanonicalSelectionKey
from normalization.validation_pipeline import CrossBookmakerValidationPipeline
from normalization.surebet import SurebetDetectorEngine, SurebetStatus


def test_team_vs_match_scope_cannot_match():
    """1. TEAM vs MATCH scope cannot match."""
    m_team = Market(
        event_id="ev_1",
        market_type="TOTALS",
        line=1.5,
        metadata={"metric": "GOALS", "scope": "TEAM", "participant_role": "HOME"}
    )
    m_match = Market(
        event_id="ev_1",
        market_type="TOTALS",
        line=1.5,
        metadata={"metric": "GOALS", "scope": "MATCH"}
    )

    matcher = MarketMatcher()
    dec = matcher.match(m_team, m_match)
    assert dec.decision == MarketMatchDecisionType.REJECTED
    assert "SCOPE_MISMATCH" in dec.reasons
    assert "PARTICIPANT_MISMATCH" in dec.reasons


def test_team_vs_player_scope_cannot_match():
    """2. TEAM vs PLAYER scope cannot match."""
    m_team = Market(
        event_id="ev_1",
        market_type="TOTALS",
        line=0.5,
        metadata={"metric": "SHOTS", "scope": "TEAM", "participant_role": "HOME"}
    )
    m_player = Market(
        event_id="ev_1",
        market_type="PLAYER_SHOTS",
        line=0.5,
        metadata={"player_name": "bukayo saka"}
    )

    matcher = MarketMatcher()
    dec = matcher.match(m_team, m_player)
    assert dec.decision == MarketMatchDecisionType.REJECTED
    assert "MARKET_TYPE_MISMATCH" in dec.reasons or "SCOPE_MISMATCH" in dec.reasons


def test_home_team_prop_cannot_match_away_team_prop():
    """3. HOME team prop cannot match AWAY team prop."""
    m_home = Market(
        event_id="ev_1",
        market_type="TOTALS",
        line=1.5,
        metadata={"metric": "GOALS", "scope": "TEAM", "participant_role": "HOME"}
    )
    m_away = Market(
        event_id="ev_1",
        market_type="TOTALS",
        line=1.5,
        metadata={"metric": "GOALS", "scope": "TEAM", "participant_role": "AWAY"}
    )

    matcher = MarketMatcher()
    dec = matcher.match(m_home, m_away)
    assert dec.decision == MarketMatchDecisionType.REJECTED
    assert "PARTICIPANT_MISMATCH" in dec.reasons


def test_different_team_prop_lines_cannot_match():
    """4. Different team-prop lines cannot match."""
    m_line1 = Market(
        event_id="ev_1",
        market_type="TOTALS",
        line=1.5,
        metadata={"metric": "GOALS", "scope": "TEAM", "participant_role": "HOME"}
    )
    m_line2 = Market(
        event_id="ev_1",
        market_type="TOTALS",
        line=2.5,
        metadata={"metric": "GOALS", "scope": "TEAM", "participant_role": "HOME"}
    )

    matcher = MarketMatcher()
    dec = matcher.match(m_line1, m_line2)
    assert dec.decision == MarketMatchDecisionType.REJECTED
    assert "LINE_MISMATCH" in dec.reasons


def test_same_team_prop_matches_across_bookmakers():
    """5. Same TEAM prop + same team + same line can match across bookmakers."""
    m_sb = Market(
        event_id="ev_sb",
        market_type="TOTALS",
        line=1.5,
        metadata={"metric": "CORNERS", "scope": "TEAM", "participant_role": "AWAY"}
    )
    m_bc = Market(
        event_id="ev_bc",
        market_type="TOTALS",
        line=1.5,
        metadata={"metric": "CORNERS", "scope": "TEAM", "participant_role": "AWAY"}
    )

    matcher = MarketMatcher()
    dec = matcher.match(m_sb, m_bc)
    assert dec.decision == MarketMatchDecisionType.MATCHED
    assert dec.canonical_market_key is not None
    assert dec.canonical_market_key.scope == "TEAM"
    assert dec.canonical_market_key.participant_role == "AWAY"
    assert dec.canonical_market_key.metric == "CORNERS"
    assert dec.canonical_market_key.line == Decimal("1.5")


def test_end_to_end_team_props_surebet_detection():
    """6. One end-to-end Team Props opportunity/surebet fixture using Superbet + Betclic."""
    ev_sb = Event(
        competition_id="c1",
        home_participant="Arsenal",
        away_participant="Chelsea",
        scheduled_start="2026-08-25T20:00:00Z",
        provider_ids={"superbet": "sb_1"}
    )
    m_sb = Market(
        event_id=ev_sb.internal_id,
        market_type="TOTALS",
        line=1.5,
        status="OPEN",
        provider_ids={"superbet": "m_sb"},
        metadata={"metric": "GOALS", "scope": "TEAM", "participant_role": "HOME"}
    )
    s_o_sb = Selection(market_id=m_sb.internal_id, selection_type="OVER", line=1.5, participant="Arsenal", provider_ids={"superbet": "s_o_sb"})
    s_u_sb = Selection(market_id=m_sb.internal_id, selection_type="UNDER", line=1.5, participant="Arsenal", provider_ids={"superbet": "s_u_sb"})

    gr_sb = NormalizedGraph(
        competition=Competition(name="Premier League"),
        event=ev_sb,
        markets=[m_sb],
        selections=[s_o_sb, s_u_sb],
        odds_list=[
            Odds(selection_id=s_o_sb.internal_id, bookmaker="superbet", decimal_odds=2.10),
            Odds(selection_id=s_u_sb.internal_id, bookmaker="superbet", decimal_odds=1.75),
        ]
    )

    ev_bc = Event(
        competition_id="c1",
        home_participant="Arsenal",
        away_participant="Chelsea",
        scheduled_start="2026-08-25T20:00:00Z",
        provider_ids={"betclic": "bc_1"}
    )
    m_bc = Market(
        event_id=ev_bc.internal_id,
        market_type="TOTALS",
        line=1.5,
        status="OPEN",
        provider_ids={"betclic": "m_bc"},
        metadata={"metric": "GOALS", "scope": "TEAM", "participant_role": "HOME"}
    )
    s_o_bc = Selection(market_id=m_bc.internal_id, selection_type="OVER", line=1.5, participant="Arsenal", provider_ids={"betclic": "s_o_bc"})
    s_u_bc = Selection(market_id=m_bc.internal_id, selection_type="UNDER", line=1.5, participant="Arsenal", provider_ids={"betclic": "s_u_bc"})

    gr_bc = NormalizedGraph(
        competition=Competition(name="Premier League"),
        event=ev_bc,
        markets=[m_bc],
        selections=[s_o_bc, s_u_bc],
        odds_list=[
            Odds(selection_id=s_o_bc.internal_id, bookmaker="betclic", decimal_odds=1.85),
            Odds(selection_id=s_u_bc.internal_id, bookmaker="betclic", decimal_odds=2.05),
        ]
    )

    pipeline = CrossBookmakerValidationPipeline()
    val_result = pipeline.run(source_items=[gr_sb], target_items=[gr_bc])

    assert len(val_result.canonical_events) == 1
    assert len(val_result.comparable_selections) == 2

    # Best odds: Superbet Over 1.5 = 2.10, Betclic Under 1.5 = 2.05
    # S = 1/2.10 + 1/2.05 = 0.47619 + 0.48780 = 0.9640 < 1.0 (Surebet!)
    detector = SurebetDetectorEngine()
    det_res = detector.detect(val_result)

    assert len(det_res.opportunities) == 1
    opp = det_res.opportunities[0]
    assert opp.status == SurebetStatus.SUREBET
    assert opp.canonical_market_key.scope == "TEAM"
    assert opp.canonical_market_key.participant_role == "HOME"
    assert opp.implied_probability_sum < Decimal("1.0")
    assert opp.arbitrage_margin > Decimal("0.0")
    assert set(opp.bookmakers) == {"betclic", "superbet"}
