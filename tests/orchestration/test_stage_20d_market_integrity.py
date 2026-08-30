"""
Stage 20D Regression Tests — Market & Line Integrity Audit

Verifies:
1. MARKET SCOPE: Match Total vs Home Team Total vs Player Total cannot match.
2. PARTICIPANT IDENTITY: Home Team prop cannot match Away Team prop.
3. LINE INTEGRITY: Distinct lines (e.g. 1.5 vs 2.5) are preserved and strictly rejected.
4. SIDE INTEGRITY: OVER vs UNDER selections are strictly isolated.
5. STAT TYPE INTEGRITY: SHOTS vs SHOTS_ON_TARGET vs FOULS are strictly isolated without fallback.
6. CROSS-BOOKMAKER EXACT MATCHING: PropExecutionMatcher requires exact event + player + stat + line + side.
7. AMBIGUOUS MATCHING IS NOT BETTABLE: Ambiguous or mismatched events are flagged with appropriate status.
8. REFERENCE BOOKMAKER REMAINS REFERENCE ONLY: Execution status correctly reflects executable vs reference bookmaker presence.
"""

from decimal import Decimal
import pytest

from domain.models import Event, Market, Selection, Odds
from normalization.market_identity import CanonicalMarketKey, extract_canonical_market_key
from normalization.market_matcher import MarketMatcher, MarketMatchDecisionType
from normalization.selection_identity import extract_canonical_selection_key
from scanner.execution_providers import NormalizedExecutionQuote
from scanner.prop_execution_matcher import PropExecutionMatcher, CanonicalPropKey


def test_market_scope_isolation_match_vs_team_vs_player():
    """1. Match Total vs Home Team Total vs Player Total cannot match across scopes."""
    m_match = Market(
        event_id="ev_1",
        market_type="TOTALS",
        line=2.5,
        metadata={"metric": "GOALS", "scope": "MATCH"}
    )
    m_team = Market(
        event_id="ev_1",
        market_type="TOTALS",
        line=2.5,
        metadata={"metric": "GOALS", "scope": "TEAM", "participant_role": "HOME"}
    )
    m_player = Market(
        event_id="ev_1",
        market_type="PLAYER_SHOTS",
        line=2.5,
        metadata={"player_name": "bukayo saka"}
    )

    matcher = MarketMatcher()

    # MATCH vs TEAM
    dec1 = matcher.match(m_match, m_team)
    assert dec1.decision == MarketMatchDecisionType.REJECTED
    assert "SCOPE_MISMATCH" in dec1.reasons

    # MATCH vs PLAYER
    dec2 = matcher.match(m_match, m_player)
    assert dec2.decision == MarketMatchDecisionType.REJECTED
    assert "SCOPE_MISMATCH" in dec2.reasons or "MARKET_TYPE_MISMATCH" in dec2.reasons

    # TEAM vs PLAYER
    dec3 = matcher.match(m_team, m_player)
    assert dec3.decision == MarketMatchDecisionType.REJECTED
    assert "SCOPE_MISMATCH" in dec3.reasons or "MARKET_TYPE_MISMATCH" in dec3.reasons


def test_participant_identity_home_vs_away():
    """2. Home Team prop cannot match Away Team prop even with identical stat and line."""
    m_home = Market(
        event_id="ev_1",
        market_type="TOTALS",
        line=4.5,
        metadata={"metric": "CORNERS", "scope": "TEAM", "participant_role": "HOME"}
    )
    m_away = Market(
        event_id="ev_1",
        market_type="TOTALS",
        line=4.5,
        metadata={"metric": "CORNERS", "scope": "TEAM", "participant_role": "AWAY"}
    )

    matcher = MarketMatcher()
    dec = matcher.match(m_home, m_away)
    assert dec.decision == MarketMatchDecisionType.REJECTED
    assert "PARTICIPANT_MISMATCH" in dec.reasons


def test_line_integrity_exact_decimal_no_rounding():
    """3. Distinct lines (e.g. 1.5 vs 2.5 vs 1.75) are preserved and strictly rejected."""
    m_15 = Market(
        event_id="ev_1",
        market_type="TOTALS",
        line=1.5,
        metadata={"metric": "GOALS", "scope": "MATCH"}
    )
    m_25 = Market(
        event_id="ev_1",
        market_type="TOTALS",
        line=2.5,
        metadata={"metric": "GOALS", "scope": "MATCH"}
    )

    matcher = MarketMatcher()
    dec = matcher.match(m_15, m_25)
    assert dec.decision == MarketMatchDecisionType.REJECTED
    assert "LINE_MISMATCH" in dec.reasons


def test_side_integrity_over_vs_under():
    """4. OVER vs UNDER selections are strictly isolated."""
    ev = Event(
        competition_id="c1",
        home_participant="Arsenal",
        away_participant="Chelsea",
        scheduled_start="2026-08-25T20:00:00Z"
    )
    mkt = Market(
        event_id=ev.internal_id,
        market_type="TOTALS",
        line=2.5,
        metadata={"metric": "GOALS", "scope": "MATCH"}
    )
    mkt_key = extract_canonical_market_key(mkt)

    sel_over = Selection(market_id=mkt.internal_id, selection_type="OVER", line=2.5)
    sel_under = Selection(market_id=mkt.internal_id, selection_type="UNDER", line=2.5)

    key_over = extract_canonical_selection_key(sel_over, mkt_key, ev)
    key_under = extract_canonical_selection_key(sel_under, mkt_key, ev)

    assert key_over.selection_type == "OVER"
    assert key_under.selection_type == "UNDER"
    assert key_over.to_key_string() != key_under.to_key_string()


def test_stat_family_integrity_shots_vs_shots_on_target():
    """5. SHOTS vs SHOTS_ON_TARGET vs FOULS are strictly isolated without fallback."""
    m_shots = Market(
        event_id="ev_1",
        market_type="PLAYER_SHOTS",
        line=1.5,
        metadata={"player_name": "erling haaland"}
    )
    m_sot = Market(
        event_id="ev_1",
        market_type="PLAYER_SHOTS_ON_TARGET",
        line=1.5,
        metadata={"player_name": "erling haaland"}
    )

    matcher = MarketMatcher()
    dec = matcher.match(m_shots, m_sot)
    assert dec.decision == MarketMatchDecisionType.REJECTED
    assert "MARKET_TYPE_MISMATCH" in dec.reasons or "METRIC_MISMATCH" in dec.reasons


def test_cross_bookmaker_prop_execution_matching():
    """6. PropExecutionMatcher requires exact match of event + player + stat + line + side."""
    quotes = [
        NormalizedExecutionQuote(
            bookmaker="Superbet",
            fixture="Arsenal vs Chelsea",
            player="Bukayo Saka",
            stat_type="SHOTS_ON_TARGET",
            line=1.5,
            side="OVER",
            odds=2.10,
            active=True,
        ),
        NormalizedExecutionQuote(
            bookmaker="Betclic",
            fixture="Arsenal vs Chelsea",
            player="Bukayo Saka",
            stat_type="SHOTS",  # Different stat!
            line=1.5,
            side="OVER",
            odds=1.40,
            active=True,
        ),
    ]

    matcher = PropExecutionMatcher(normalized_quotes=quotes)
    res = matcher.match_execution_odds(
        player_name="Bukayo Saka",
        team="Arsenal",
        opponent="Chelsea",
        stat_type="SHOTS_ON_TARGET",
        line=1.5,
        side="OVER",
    )

    assert res.execution_status == "BETTABLE"
    # Superbet matched exactly on SHOTS_ON_TARGET
    assert res.execution_odds["Superbet"].status == "AVAILABLE"
    assert res.execution_odds["Superbet"].decimal_odds == 2.10
    # Betclic rejected because quote was total SHOTS, not SHOTS_ON_TARGET
    assert res.execution_odds["Betclic"].status == "UNAVAILABLE"


def test_prop_execution_matcher_line_mismatch():
    """7. PropExecutionMatcher rejects quotes with line mismatch."""
    quotes = [
        NormalizedExecutionQuote(
            bookmaker="Superbet",
            fixture="Real Madrid vs Barcelona",
            player="Vinicius Junior",
            stat_type="SHOTS",
            line=2.5,
            side="OVER",
            odds=1.85,
            active=True,
        )
    ]
    matcher = PropExecutionMatcher(normalized_quotes=quotes)

    # Request line 1.5 vs available line 2.5
    res = matcher.match_execution_odds(
        player_name="Vinicius Junior",
        team="Real Madrid",
        opponent="Barcelona",
        stat_type="SHOTS",
        line=1.5,
        side="OVER",
    )

    assert res.execution_status == "NO_EXECUTION_MARKET"
    assert res.execution_odds["Superbet"].status == "UNAVAILABLE"


def test_prop_execution_matcher_reference_only_flagging():
    """8. Reference odds without executable quotes are cleanly flagged as REFERENCE_ONLY."""
    matcher = PropExecutionMatcher(normalized_quotes=[])

    ref_odds = [
        {"bookmaker": "Bet365", "line": 0.5, "side": "OVER", "decimal_odds": 2.20}
    ]

    res = matcher.match_execution_odds(
        player_name="Robert Lewandowski",
        team="Barcelona",
        opponent="Valencia",
        stat_type="GOALS",
        line=0.5,
        side="OVER",
        reference_odds=ref_odds,
    )

    assert res.execution_status == "REFERENCE_ONLY"
    assert res.reference_best_odds == 2.20
    assert res.reference_best_bookmaker == "Bet365"
    assert res.best_executable_odds is None
