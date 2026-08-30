"""
Tests for Team Prop Execution Matcher (Stage 30).

Verifies strict invariants:
1. Exact line matching (no line fallback).
2. No cross-market pollution (team fouls vs team shots vs match totals).
3. Participant role isolation (HOME vs AWAY team props).
4. Inverted fixture rejection (Home vs Away directionality).
5. Missing / inactive execution odds handling (REFERENCE_ONLY / NO_EXECUTION_ODDS).
"""

import pytest
from decimal import Decimal
from scanner.execution_providers import NormalizedExecutionQuote
from scanner.team_prop_execution_matcher import (
    TeamPropExecutionMatcher,
    CanonicalTeamPropKey,
)


def test_canonical_team_prop_key_deterministic():
    key1 = CanonicalTeamPropKey(
        team="Arsenal",
        opponent="Chelsea",
        stat_type="CORNERS",
        line=Decimal("4.5"),
        participant_role="HOME",
        side="OVER",
    )
    key2 = CanonicalTeamPropKey(
        team=" Arsenal FC ",
        opponent=" Chelsea ",
        stat_type="corners",
        line=Decimal("4.50"),
        participant_role="home",
        side="over",
    )
    assert key1.to_key_string() == key2.to_key_string()
    assert "team_prop:arsenal:chelsea:HOME:CORNERS:OVER:FULL_TIME:4.5" in key1.to_key_string()


def test_exact_line_matching_no_fallback():
    matcher = TeamPropExecutionMatcher()
    quotes = [
        NormalizedExecutionQuote(
            bookmaker="Superbet",
            team="Arsenal",
            participant_role="HOME",
            fixture="Arsenal vs Chelsea",
            stat_type="CORNERS",
            line=5.5,
            side="OVER",
            odds=2.10,
            active=True,
            scope="TEAM",
        ),
        NormalizedExecutionQuote(
            bookmaker="Superbet",
            team="Arsenal",
            participant_role="HOME",
            fixture="Arsenal vs Chelsea",
            stat_type="CORNERS",
            line=3.5,
            side="OVER",
            odds=1.40,
            active=True,
            scope="TEAM",
        ),
    ]

    # Target line is 4.5. Neither 3.5 nor 5.5 should match!
    res = matcher.match_execution_odds(
        team="Arsenal",
        opponent="Chelsea",
        stat_type="CORNERS",
        line=4.5,
        side="OVER",
        participant_role="HOME",
        normalized_quotes=quotes,
    )

    assert res.execution_odds["Superbet"].status == "UNAVAILABLE"
    assert res.best_executable_odds is None
    assert res.execution_status == "NO_EXECUTION_MARKET"


def test_exact_line_matching_success():
    matcher = TeamPropExecutionMatcher()
    quotes = [
        NormalizedExecutionQuote(
            bookmaker="Superbet",
            team="Arsenal",
            participant_role="HOME",
            fixture="Arsenal vs Chelsea",
            stat_type="CORNERS",
            line=4.5,
            side="OVER",
            odds=1.85,
            active=True,
            scope="TEAM",
        ),
        NormalizedExecutionQuote(
            bookmaker="Betclic",
            team="Arsenal",
            participant_role="HOME",
            fixture="Arsenal vs Chelsea",
            stat_type="CORNERS",
            line=4.5,
            side="OVER",
            odds=1.92,
            active=True,
            scope="TEAM",
        ),
    ]

    res = matcher.match_execution_odds(
        team="Arsenal",
        opponent="Chelsea",
        stat_type="CORNERS",
        line=4.5,
        side="OVER",
        participant_role="HOME",
        normalized_quotes=quotes,
    )

    assert res.execution_status == "BETTABLE"
    assert res.best_executable_odds == 1.92
    assert res.best_executable_bookmaker == "Betclic"
    assert res.execution_odds["Superbet"].status == "AVAILABLE"
    assert res.execution_odds["Betclic"].status == "AVAILABLE"


def test_no_cross_market_pollution_scope_and_stat():
    matcher = TeamPropExecutionMatcher()
    quotes = [
        # Match total corners (scope="MATCH") - must NOT match team prop
        NormalizedExecutionQuote(
            bookmaker="Superbet",
            team="",
            participant_role="",
            fixture="Arsenal vs Chelsea",
            stat_type="CORNERS",
            line=4.5,
            side="OVER",
            odds=1.15,
            active=True,
            scope="MATCH",
        ),
        # Team fouls (stat_type="FOULS") - must NOT match team corners
        NormalizedExecutionQuote(
            bookmaker="Superbet",
            team="Arsenal",
            participant_role="HOME",
            fixture="Arsenal vs Chelsea",
            stat_type="FOULS",
            line=4.5,
            side="OVER",
            odds=1.60,
            active=True,
            scope="TEAM",
        ),
    ]

    res = matcher.match_execution_odds(
        team="Arsenal",
        opponent="Chelsea",
        stat_type="CORNERS",
        line=4.5,
        side="OVER",
        participant_role="HOME",
        normalized_quotes=quotes,
    )

    assert res.execution_odds["Superbet"].status == "UNAVAILABLE"
    assert res.best_executable_odds is None
    assert res.execution_status == "NO_EXECUTION_MARKET"


def test_participant_role_home_vs_away_isolation():
    matcher = TeamPropExecutionMatcher()
    quotes = [
        # Chelsea (AWAY team) corners
        NormalizedExecutionQuote(
            bookmaker="Superbet",
            team="Chelsea",
            participant_role="AWAY",
            fixture="Arsenal vs Chelsea",
            stat_type="CORNERS",
            line=4.5,
            side="OVER",
            odds=2.30,
            active=True,
            scope="TEAM",
        ),
    ]

    # Matching Arsenal (HOME team) corners
    res = matcher.match_execution_odds(
        team="Arsenal",
        opponent="Chelsea",
        stat_type="CORNERS",
        line=4.5,
        side="OVER",
        participant_role="HOME",
        normalized_quotes=quotes,
    )

    assert res.execution_odds["Superbet"].status == "UNAVAILABLE"
    assert res.best_executable_odds is None

    # Matching Chelsea (AWAY team) corners
    res_away = matcher.match_execution_odds(
        team="Chelsea",
        opponent="Arsenal",
        stat_type="CORNERS",
        line=4.5,
        side="OVER",
        participant_role="AWAY",
        normalized_quotes=quotes,
    )
    assert res_away.execution_odds["Superbet"].status == "AVAILABLE"
    assert res_away.best_executable_odds == 2.30


def test_inverted_fixture_rejection():
    is_match, conf = TeamPropExecutionMatcher.is_fixture_match(
        target_home="Arsenal",
        target_away="Chelsea",
        cand_home="Chelsea",
        cand_away="Arsenal",
    )
    assert is_match is False
    assert conf == 0.0


def test_reference_only_when_no_execution_market():
    matcher = TeamPropExecutionMatcher()
    ref_odds = [
        {"bookmaker": "Bet365", "line": 4.5, "side": "OVER", "decimal_odds": 1.95},
    ]

    res = matcher.match_execution_odds(
        team="Arsenal",
        opponent="Chelsea",
        stat_type="CORNERS",
        line=4.5,
        side="OVER",
        participant_role="HOME",
        reference_odds=ref_odds,
        normalized_quotes=[],
    )

    assert res.execution_status == "REFERENCE_ONLY"
    assert res.reference_best_odds == 1.95
    assert res.reference_best_bookmaker == "Bet365"
    assert res.best_executable_odds is None