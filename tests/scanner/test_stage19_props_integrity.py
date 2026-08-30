"""
Stage 19 — Player Props Finalization: Data Integrity, Edge Validation & Production Hardening Test Suite
"""

import pytest
from decimal import Decimal
from typing import List, Dict, Any

from scanner.prop_opportunity_engine import PropOpportunityEngine, PropOpportunityEvaluation
from scanner.prop_execution_matcher import (
    PropExecutionMatcher,
    CanonicalPropKey,
    ExecutionBookmakerOdds,
    PropOddsComparison,
    STAT_TYPE_CANONICAL_MAP,
)
from scanner.execution_providers import NormalizedExecutionQuote
from providers.statshub.models import (
    StatsHubPlayerStat,
    StatsHubFixture,
    StatsHubHistoricalMatch,
    StatsHubBookmakerOdds,
    StatsHubPropResult,
)
from providers.statshub.parser import StatsHubParser


class TestStage19ProbabilityAndEdgeMath:
    """Mathematical validation for probability, statistical edge, execution edge, and EV."""

    def test_probability_and_edge_formulas_exact(self):
        engine = PropOpportunityEngine()

        # Target Example from Stage 19 specification:
        # P_hist = 0.90 (90.0%), odds = 1.45
        p_hist = 0.90
        odds = 1.45

        # 1. Implied Probability: P_implied = 1 / 1.45 = 0.6897
        implied_prob = engine.calculate_implied_probability(odds)
        assert implied_prob == pytest.approx(0.6897, abs=1e-4)

        # 2. Execution Edge: P_hist - P_implied = 0.90 - 0.6897 = +0.2103 (+21.0pp)
        edge, edge_pct = engine.calculate_statistical_edge(p_hist, implied_prob)
        assert edge == pytest.approx(0.2103, abs=1e-4)
        assert edge_pct == pytest.approx(21.0, abs=0.1)

        # 3. Expected Value: EV = (0.90 * 1.45) - 1 = 1.305 - 1 = +0.3050 (+30.5%)
        ev, ev_pct = engine.calculate_expected_value(p_hist, odds)
        assert ev == pytest.approx(0.3050, abs=1e-4)
        assert ev_pct == pytest.approx(30.5, abs=0.1)

        # Full engine evaluation integration
        eval_res = engine.evaluate(
            hit_rate_pct=90.0,
            sample_size=10,
            stat_average=2.5,
            line=0.5,
            side="OVER",
            best_odds=1.40,
            best_bookmaker="Bet365",
            best_execution_odds=1.45,
            best_execution_bookmaker="Superbet",
            execution_status="BETTABLE",
        )

        assert eval_res.historical_probability == 0.9000
        assert eval_res.execution_market_probability == 0.6897
        assert eval_res.execution_edge == pytest.approx(0.2103, abs=1e-4)
        assert eval_res.execution_edge_pct == pytest.approx(21.0, abs=0.1)
        assert eval_res.execution_ev == pytest.approx(0.3050, abs=1e-4)
        assert eval_res.execution_ev_pct == pytest.approx(30.5, abs=0.1)
        assert eval_res.actionability == "BETTABLE"

    def test_distinction_between_edge_and_ev(self):
        """Verify that Edge (probability difference in percentage points) is NEVER confused with EV (ROI %)."""
        engine = PropOpportunityEngine()

        # Scenario: P_hist = 0.50 (50%), Odds = 2.50
        # P_implied = 1 / 2.50 = 0.40 (40%)
        # Edge = 0.50 - 0.40 = +0.10 (+10.0pp)
        # EV = 0.50 * 2.50 - 1 = 1.25 - 1 = +0.25 (+25.0%)
        eval_res = engine.evaluate(
            hit_rate_pct=50.0,
            sample_size=10,
            stat_average=1.2,
            line=0.5,
            best_execution_odds=2.50,
            best_execution_bookmaker="Superbet",
            execution_status="BETTABLE",
        )
        assert eval_res.execution_edge_pct == 10.0  # +10.0pp
        assert eval_res.execution_ev_pct == 25.0    # +25.0%
        assert eval_res.execution_edge_pct != eval_res.execution_ev_pct


class TestStage19InvalidAndEdgeCases:
    """Validation of invalid inputs, missing data, and edge cases."""

    def test_invalid_odds_less_than_or_equal_to_one(self):
        engine = PropOpportunityEngine()

        # Odds <= 1.0 must not produce valid probability, edge or EV
        for invalid_odds in [1.0, 0.95, 0.0, -1.5, None]:
            prob = engine.calculate_implied_probability(invalid_odds)
            assert prob is None
            ev, ev_pct = engine.calculate_expected_value(0.8, invalid_odds)
            assert ev is None
            assert ev_pct is None

        eval_res = engine.evaluate(
            hit_rate_pct=80.0,
            sample_size=10,
            stat_average=2.0,
            line=0.5,
            best_odds=1.0,
            best_execution_odds=0.5,
            execution_status="NO_EXECUTION_MARKET",
        )
        assert eval_res.reference_market_probability is None
        assert eval_res.raw_edge is None
        assert eval_res.execution_market_probability is None
        assert eval_res.execution_edge is None
        assert "NO_REFERENCE_ODDS" in eval_res.data_quality_flags
        assert "NO_POLISH_ODDS" in eval_res.data_quality_flags

    def test_missing_historical_sample(self):
        """Missing historical sample (sample_size=0) must produce no probability-derived edge and flag SMALL_SAMPLE."""
        engine = PropOpportunityEngine()

        eval_res = engine.evaluate(
            hit_rate_pct=0.0,
            sample_size=0,
            stat_average=0.0,
            line=0.5,
            best_odds=1.85,
            best_execution_odds=1.90,
            execution_status="BETTABLE",
        )

        assert eval_res.historical_probability == 0.0
        assert eval_res.raw_edge is None
        assert eval_res.execution_edge is None
        assert eval_res.execution_ev is None
        assert "SMALL_SAMPLE" in eval_res.data_quality_flags
        assert eval_res.classification == "SPECULATIVE"

    def test_tiny_sample_with_100_percent_hit_rate(self):
        """100% hit rate from tiny sample (N=2) must NOT be treated as certainty and must flag SMALL_SAMPLE."""
        engine = PropOpportunityEngine()

        eval_res = engine.evaluate(
            hit_rate_pct=100.0,
            sample_size=2,
            stat_average=3.0,
            line=0.5,
            best_odds=1.50,
            best_execution_odds=1.55,
            execution_status="BETTABLE",
        )

        assert "SMALL_SAMPLE" in eval_res.data_quality_flags
        assert eval_res.classification == "SPECULATIVE"
        # Score must be constrained and not artificially high
        assert eval_res.score < 75.0


class TestStage19ExecutionStatusInvariants:
    """Validation of status invariants: BETTABLE, REFERENCE_ONLY, MATCH_UNCERTAIN, NO_EXECUTION_MARKET."""

    def test_match_uncertain_can_never_be_bettable(self):
        engine = PropOpportunityEngine()

        eval_res = engine.evaluate(
            hit_rate_pct=85.0,
            sample_size=10,
            stat_average=2.0,
            line=0.5,
            best_odds=1.60,
            best_execution_odds=1.70,  # Quote might exist in ambiguous match
            execution_status="MATCH_UNCERTAIN",
        )

        assert eval_res.actionability == "MATCH_UNCERTAIN"
        assert eval_res.actionability != "BETTABLE"
        assert eval_res.execution_edge is None
        assert eval_res.execution_ev is None
        assert "MATCH_UNCERTAIN" in eval_res.data_quality_flags

    def test_reference_only_never_claims_execution_edge(self):
        engine = PropOpportunityEngine()

        eval_res = engine.evaluate(
            hit_rate_pct=80.0,
            sample_size=10,
            stat_average=2.0,
            line=0.5,
            best_odds=1.80,
            best_bookmaker="Bet365",
            best_execution_odds=None,
            execution_status="REFERENCE_ONLY",
        )

        assert eval_res.actionability == "REFERENCE_ONLY"
        assert eval_res.execution_market_probability is None
        assert eval_res.execution_edge is None
        assert eval_res.execution_edge_pct is None
        assert eval_res.raw_edge is not None
        assert "NO_POLISH_ODDS" in eval_res.data_quality_flags

    def test_no_execution_market_and_no_execution_odds_invariants(self):
        engine = PropOpportunityEngine()

        eval_no_mkt = engine.evaluate(
            hit_rate_pct=70.0,
            sample_size=10,
            stat_average=1.5,
            line=0.5,
            execution_status="NO_EXECUTION_MARKET",
        )
        assert eval_no_mkt.actionability == "NO_EXECUTION_MARKET"
        assert eval_no_mkt.execution_edge is None

        eval_no_odds = engine.evaluate(
            hit_rate_pct=70.0,
            sample_size=10,
            stat_average=1.5,
            line=0.5,
            execution_status="NO_EXECUTION_ODDS",
        )
        assert eval_no_odds.actionability == "NO_EXECUTION_ODDS"
        assert eval_no_odds.execution_edge is None
        assert "INACTIVE_ODDS" in eval_no_odds.data_quality_flags


class TestStage19MultiBookmakerConsistency:
    """Validation of multi-bookmaker quotes preservation and deterministic max odds selection."""

    def test_multi_bookmaker_preservation_and_max_selection(self):
        # Setup matcher with quotes for both Superbet and Betclic
        quotes = [
            NormalizedExecutionQuote(
                bookmaker="Superbet",
                player="Kylian Mbappe",
                fixture="Real Madrid vs Barcelona",
                stat_type="SHOTS",
                line=2.5,
                side="OVER",
                odds=1.85,
                active=True,
            ),
            NormalizedExecutionQuote(
                bookmaker="Betclic",
                player="Kylian Mbappe",
                fixture="Real Madrid vs Barcelona",
                stat_type="SHOTS",
                line=2.5,
                side="OVER",
                odds=2.10,
                active=True,
            ),
        ]

        matcher = PropExecutionMatcher(normalized_quotes=quotes)
        ref_odds = [{"bookmaker": "Bet365", "line": 2.5, "side": "OVER", "decimal_odds": 1.90}]

        res = matcher.match_execution_odds(
            player_name="Kylian Mbappe",
            team="Real Madrid",
            opponent="Barcelona",
            stat_type="SHOTS",
            line=2.5,
            side="OVER",
            reference_odds=ref_odds,
        )

        assert res.execution_status == "BETTABLE"
        # Best Polish price must be max(1.85, 2.10) = 2.10 from Betclic
        assert res.best_executable_odds == 2.10
        assert res.best_executable_bookmaker == "Betclic"

        # Both quotes must be preserved in execution_odds dict
        assert "Superbet" in res.execution_odds
        assert "Betclic" in res.execution_odds
        assert res.execution_odds["Superbet"].decimal_odds == 1.85
        assert res.execution_odds["Betclic"].decimal_odds == 2.10

        # Reference bookmaker must be Bet365 and never mixed with Polish bookmakers
        assert res.reference_best_bookmaker == "Bet365"
        assert res.reference_best_odds == 1.90
        assert res.reference_best_bookmaker not in ("Superbet", "Betclic")


class TestStage19HistoricalSampleIntegrityAcrossStats:
    """Validation of line clearance and hit rate calculations across Shots, Fouls, Cards, Shots on Target."""

    def test_line_hit_calculation_invariant_for_all_stats(self):
        # 5 matches: stat values = [0, 1, 2, 2, 3]
        matches = [
            StatsHubHistoricalMatch(opponent="Team A", stat_value=0),
            StatsHubHistoricalMatch(opponent="Team B", stat_value=1),
            StatsHubHistoricalMatch(opponent="Team C", stat_value=2),
            StatsHubHistoricalMatch(opponent="Team D", stat_value=2),
            StatsHubHistoricalMatch(opponent="Team E", stat_value=3),
        ]

        # Line 0.5 (Over 0.5): 1+ is a hit -> stat_values > 0.5 -> [1, 2, 2, 3] = 4 hits / 5 = 80.0%
        hits_05 = sum(1 for m in matches if m.stat_value > 0.5)
        assert hits_05 == 4
        assert round((hits_05 / len(matches)) * 100.0, 1) == 80.0

        # Line 1.5 (Over 1.5): 2+ is a hit -> stat_values > 1.5 -> [2, 2, 3] = 3 hits / 5 = 60.0%
        hits_15 = sum(1 for m in matches if m.stat_value > 1.5)
        assert hits_15 == 3
        assert round((hits_15 / len(matches)) * 100.0, 1) == 60.0

        # Line 2.5 (Over 2.5): 3+ is a hit -> stat_values > 2.5 -> [3] = 1 hit / 5 = 20.0%
        hits_25 = sum(1 for m in matches if m.stat_value > 2.5)
        assert hits_25 == 1
        assert round((hits_25 / len(matches)) * 100.0, 1) == 20.0

    def test_parser_stat_extraction_without_shots_fallback(self):
        parser = StatsHubParser()

        # Fouls item
        foul_item = {
            "name": "Casemiro",
            "teamName": "Manchester United",
            "opponentName": "Arsenal",
            "stat": "fouls",
            "stats": {"fouls": 3},
            "averages": {"fouls": 2.4},
            "hitRates": {"fouls": 70.0},
            "historicalMatches": [
                {"event": {"homeTeamName": "Manchester United"}, "playerStats": {"fouls": 2, "minutesPlayed": 90}},
                {"event": {"homeTeamName": "Manchester United"}, "playerStats": {"fouls": 3, "minutesPlayed": 90}},
            ],
            "oddsByLine": {"1.5": {"over": [{"bookmakerName": "Bet365", "oddsValue": 1.72}]}},
        }

        parsed = parser.parse_payload(foul_item)
        assert len(parsed) == 1
        res = parsed[0]
        assert res.player_stat.stat_type == "fouls"
        assert res.player_stat.stat_value == 3
        assert len(res.player_stat.historical_matches) == 2
        assert res.player_stat.historical_matches[0].stat_value == 2
        assert res.player_stat.historical_matches[1].stat_value == 3

        # Cards item
        card_item = {
            "name": "Cristian Romero",
            "teamName": "Tottenham",
            "opponentName": "Chelsea",
            "stat": "cards",
            "stats": {"cards": 1},
            "historicalMatches": [
                {"event": {"homeTeamName": "Tottenham"}, "playerStats": {"yellowCards": 1, "minutesPlayed": 90}},
                {"event": {"homeTeamName": "Tottenham"}, "playerStats": {"cards": 0, "minutesPlayed": 90}},
            ],
        }
        parsed_card = parser.parse_payload(card_item)
        assert len(parsed_card) == 1
        assert parsed_card[0].player_stat.stat_type == "cards"
        assert parsed_card[0].player_stat.historical_matches[0].stat_value == 1
        assert parsed_card[0].player_stat.historical_matches[1].stat_value == 0
