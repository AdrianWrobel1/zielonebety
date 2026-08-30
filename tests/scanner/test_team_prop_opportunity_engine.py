"""
Tests for Team Prop Opportunity Engine & Value Bet Engine (Stage 30).

Verifies strict mathematical invariants:
1. fair_odds = 1 / P_model
2. EV = P_model * execution_odds - 1
3. value_edge_pp = P_model * 100 - (1 / execution_odds) * 100
4. Valuebet status assigned iff execution odds are BETTABLE and EV > 0.
5. Missing execution odds -> REFERENCE_ONLY / NO_EXECUTION_MARKET, never VALUEBET.
6. Sample size penalty for small samples.
"""

import pytest
from scanner.team_prop_opportunity_engine import TeamPropOpportunityEngine


def test_valuebet_engine_math_positive_ev():
    engine = TeamPropOpportunityEngine()

    # Arsenal 8/10 corners over 4.5 -> P_model = 0.80, Fair Odds = 1.25
    # Superbet offers 1.50 execution odds
    # Implied P_exec = 1 / 1.50 = 0.6667 (66.7%)
    # EV = 0.80 * 1.50 - 1 = +0.20 (+20.0%)
    # Value Edge PP = 80.0% - 66.7% = +13.33pp
    eval_res = engine.evaluate(
        hit_rate_pct=80.0,
        sample_size=10,
        stat_average=5.8,
        line=4.5,
        side="OVER",
        best_odds=1.45,
        best_bookmaker="Bet365",
        best_execution_odds=1.50,
        best_execution_bookmaker="Superbet",
        execution_status="BETTABLE",
        hit_rate_count=8,
        stat_type="CORNERS",
        participant_role="HOME",
    )

    assert eval_res.historical_probability == 0.80
    assert eval_res.model_probability == 0.80
    assert eval_res.fair_odds == 1.25
    assert eval_res.execution_ev == 0.20
    assert eval_res.execution_ev_pct == 20.0
    assert eval_res.value_edge_pp == pytest.approx(13.33, 0.1)
    assert eval_res.is_valuebet is True
    assert eval_res.status == "VALUEBET"
    assert eval_res.actionability == "BETTABLE"
    assert "VALUEBET_POSITIVE_EV" in eval_res.data_quality_flags


def test_missing_execution_odds_cannot_be_valuebet():
    engine = TeamPropOpportunityEngine()

    # High hit rate (90%) and high reference odds (2.10)
    # BUT no execution odds (REFERENCE_ONLY)
    eval_res = engine.evaluate(
        hit_rate_pct=90.0,
        sample_size=10,
        stat_average=6.2,
        line=4.5,
        side="OVER",
        best_odds=2.10,
        best_bookmaker="Bet365",
        best_execution_odds=None,
        best_execution_bookmaker=None,
        execution_status="REFERENCE_ONLY",
        hit_rate_count=9,
        stat_type="CORNERS",
        participant_role="HOME",
    )

    assert eval_res.is_valuebet is False
    assert eval_res.status == "REFERENCE_ONLY"
    assert eval_res.actionability == "REFERENCE_ONLY"
    assert eval_res.execution_ev is None
    assert "NO_POLISH_ODDS" in eval_res.data_quality_flags


def test_negative_ev_not_a_valuebet():
    engine = TeamPropOpportunityEngine()

    # 5/10 hit rate = 50% probability -> Fair Odds = 2.00
    # Execution odds = 1.70 -> EV = 0.50 * 1.70 - 1 = -0.15 (-15%)
    eval_res = engine.evaluate(
        hit_rate_pct=50.0,
        sample_size=10,
        stat_average=4.4,
        line=4.5,
        side="OVER",
        best_odds=1.75,
        best_bookmaker="Bet365",
        best_execution_odds=1.70,
        best_execution_bookmaker="Superbet",
        execution_status="BETTABLE",
        hit_rate_count=5,
        stat_type="CORNERS",
        participant_role="HOME",
    )

    assert eval_res.historical_probability == 0.50
    assert eval_res.execution_ev == -0.15
    assert eval_res.execution_ev_pct == -15.0
    assert eval_res.is_valuebet is False
    assert eval_res.status == "BETTABLE"  # Bettable market, but not a valuebet
    assert eval_res.actionability == "BETTABLE"


def test_small_sample_penalty():
    engine = TeamPropOpportunityEngine()

    # 2/2 hits (100%), but sample is only 2 games
    eval_res = engine.evaluate(
        hit_rate_pct=100.0,
        sample_size=2,
        stat_average=7.0,
        line=4.5,
        side="OVER",
        best_odds=1.80,
        best_bookmaker="Bet365",
        best_execution_odds=1.85,
        best_execution_bookmaker="Superbet",
        execution_status="BETTABLE",
        hit_rate_count=2,
        stat_type="CORNERS",
        participant_role="HOME",
    )

    assert "SMALL_SAMPLE" in eval_res.data_quality_flags
    assert eval_res.classification == "SPECULATIVE"
    # Small sample caps max total score (45 + 2*5 = 55.0)
    assert eval_res.score <= 55.0