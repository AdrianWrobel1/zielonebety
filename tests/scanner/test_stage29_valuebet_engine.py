"""
Stage 29 — Value Bet Engine Test Suite

Validates:
1. Mathematical precision:
   - fair_odds = 1 / P_model
   - EV = P_model * execution_odds - 1
   - value_edge_pp = P_model * 100 - (1 / execution_odds) * 100
2. Positive EV example:
   - P_model = 0.80, Superbet Over 0.5 = 1.80 -> fair odds = 1.25, EV = +44%, status = VALUEBET, is_valuebet = True
3. Negative EV example:
   - P_model = 0.80, Superbet = 1.10 -> EV = -12%, status = BETTABLE (NOT VALUEBET), is_valuebet = False
4. Execution odds missing:
   - Reference odds exist (e.g. Bet365 1.80), no Superbet/Betclic -> status = REFERENCE_ONLY, is_valuebet = False
5. Invariants:
   - REFERENCE_ONLY can never be marked as BETTABLE or VALUEBET
   - Exact-line matching strictly preserved across multiple lines
   - Configurable min_ev_threshold
   - GenericValueBetEngine compatibility with Team Props
6. Unified Opportunity Explorer integration:
   - Adapter mapping, valuebet status, and filtering
"""

import unittest
from decimal import Decimal

from scanner.prop_valuebet_engine import GenericValueBetEngine, ValueBetResult
from scanner.prop_opportunity_engine import PropOpportunityEngine, PropOpportunityEvaluation
from scanner.prop_execution_matcher import PropExecutionMatcher
from scanner.execution_providers import NormalizedExecutionQuote
from core.opportunity_explorer import (
    OpportunityExplorerAdapter,
    OpportunityType,
    UnifiedOpportunityDTO,
)


class TestStage29ValueBetEngine(unittest.TestCase):

    def setUp(self):
        self.val_engine = GenericValueBetEngine(min_ev_threshold=0.0)
        self.opp_engine = PropOpportunityEngine(min_ev_threshold=0.0)

    def test_positive_ev_specification_example(self):
        """
        User Specification Example 1:
        P_model = 0.80
        Superbet Over 0.5 = 1.80
        fair odds = 1 / 0.80 = 1.25
        EV = 0.80 * 1.80 - 1 = +0.44 (+44.0%)
        value_edge_pp = 0.80 * 100 - (1 / 1.80) * 100 = 80.0 - 55.56 = +24.44pp
        -> VALUEBET
        """
        res = self.val_engine.evaluate_candidate(
            p_model=0.80,
            execution_odds=1.80,
            reference_odds=1.75,
            execution_status="BETTABLE",
        )

        self.assertAlmostEqual(res.fair_odds, 1.25, places=3)
        self.assertAlmostEqual(res.ev, 0.44, places=3)
        self.assertAlmostEqual(res.ev_pct, 44.0, places=1)
        self.assertAlmostEqual(res.value_edge_pp, 24.44, places=1)
        self.assertTrue(res.is_valuebet)
        self.assertEqual(res.status, "VALUEBET")

        # PropOpportunityEngine integration
        eval_res = self.opp_engine.evaluate(
            hit_rate_pct=80.0,
            sample_size=10,
            stat_average=2.5,
            line=0.5,
            side="OVER",
            best_odds=1.75,
            best_bookmaker="Bet365",
            best_execution_odds=1.80,
            best_execution_bookmaker="Superbet",
            execution_status="BETTABLE",
            stat_type="SHOTS",
        )

        self.assertAlmostEqual(eval_res.fair_odds, 1.25, places=3)
        self.assertAlmostEqual(eval_res.execution_ev, 0.44, places=3)
        self.assertAlmostEqual(eval_res.execution_ev_pct, 44.0, delta=0.2)
        self.assertTrue(eval_res.is_valuebet)
        self.assertEqual(eval_res.status, "VALUEBET")
        self.assertEqual(eval_res.actionability, "BETTABLE")
        self.assertTrue(any("VALUEBET DETECTED" in r for r in eval_res.reasons))

    def test_negative_ev_specification_example(self):
        """
        User Specification Example 2:
        P_model = 0.80
        Superbet = 1.10
        EV = 0.80 * 1.10 - 1 = -0.12 (-12.0%)
        value_edge_pp = 0.80 * 100 - (1 / 1.10) * 100 = 80.0 - 90.91 = -10.91pp
        -> brak valuebetu (status is BETTABLE, but NOT VALUEBET)
        """
        res = self.val_engine.evaluate_candidate(
            p_model=0.80,
            execution_odds=1.10,
            reference_odds=1.20,
            execution_status="BETTABLE",
        )

        self.assertAlmostEqual(res.fair_odds, 1.25, places=3)
        self.assertAlmostEqual(res.ev, -0.12, places=3)
        self.assertAlmostEqual(res.ev_pct, -12.0, places=1)
        self.assertAlmostEqual(res.value_edge_pp, -10.91, delta=0.1)
        self.assertFalse(res.is_valuebet)
        self.assertEqual(res.status, "BETTABLE")

        # PropOpportunityEngine integration
        eval_res = self.opp_engine.evaluate(
            hit_rate_pct=80.0,
            sample_size=10,
            stat_average=2.5,
            line=0.5,
            side="OVER",
            best_odds=1.20,
            best_bookmaker="Bet365",
            best_execution_odds=1.10,
            best_execution_bookmaker="Superbet",
            execution_status="BETTABLE",
            stat_type="SHOTS",
        )

        self.assertAlmostEqual(eval_res.execution_ev_pct, -12.0, delta=0.2)
        self.assertFalse(eval_res.is_valuebet)
        self.assertEqual(eval_res.actionability, "BETTABLE")
        self.assertNotIn("VALUEBET_POSITIVE_EV", eval_res.data_quality_flags)

    def test_missing_execution_odds_cannot_be_valuebet_or_bettable(self):
        """
        Missing execution odds:
        P_model = 0.80, Bet365 = 2.50 (theoretical +100% EV on Bet365),
        but no Polish execution odds at Superbet/Betclic.
        Invariant:
        - Must NOT be marked as BETTABLE or VALUEBET.
        - Must be REFERENCE_ONLY.
        - Execution EV and execution edge must be None.
        """
        res = self.val_engine.evaluate_candidate(
            p_model=0.80,
            execution_odds=None,
            reference_odds=2.50,
            execution_status="REFERENCE_ONLY",
        )

        self.assertFalse(res.is_valuebet)
        self.assertEqual(res.status, "REFERENCE_ONLY")
        self.assertIsNone(res.ev)
        self.assertIsNone(res.ev_pct)
        self.assertIsNone(res.value_edge_pp)

        eval_res = self.opp_engine.evaluate(
            hit_rate_pct=80.0,
            sample_size=10,
            stat_average=2.5,
            line=0.5,
            side="OVER",
            best_odds=2.50,
            best_bookmaker="Bet365",
            best_execution_odds=None,
            execution_status="REFERENCE_ONLY",
            stat_type="SHOTS",
        )

        self.assertFalse(eval_res.is_valuebet)
        self.assertEqual(eval_res.actionability, "REFERENCE_ONLY")
        self.assertIsNone(eval_res.execution_ev)
        self.assertIsNone(eval_res.execution_ev_pct)
        self.assertIn("NO_POLISH_ODDS", eval_res.data_quality_flags)

    def test_configurable_min_ev_threshold(self):
        """
        Configurable min_ev_threshold:
        If P_model = 0.60, execution_odds = 1.70 -> EV = (0.60 * 1.70) - 1 = +2.0%
        With min_ev_threshold = 5.0%:
          - EV +2.0% is positive, but < 5.0% threshold -> status is BETTABLE (not VALUEBET).
        With min_ev_threshold = 1.0%:
          - EV +2.0% >= 1.0% threshold -> status is VALUEBET.
        """
        engine_5pct = GenericValueBetEngine(min_ev_threshold=5.0)
        res_low = engine_5pct.evaluate_candidate(
            p_model=0.60,
            execution_odds=1.70,
            reference_odds=1.65,
            execution_status="BETTABLE",
        )
        self.assertFalse(res_low.is_valuebet)
        self.assertEqual(res_low.status, "BETTABLE")

        engine_1pct = GenericValueBetEngine(min_ev_threshold=1.0)
        res_high = engine_1pct.evaluate_candidate(
            p_model=0.60,
            execution_odds=1.70,
            reference_odds=1.65,
            execution_status="BETTABLE",
        )
        self.assertTrue(res_high.is_valuebet)
        self.assertEqual(res_high.status, "VALUEBET")

    def test_exact_line_matching_prevents_false_valuebet(self):
        """
        Exact-line matching from Stage 28.5:
        Player has Over 0.5 hit rate 90% (P_model = 0.90, fair odds = 1.11)
        Bookmaker offers:
          - Over 0.5 @ 1.05 (EV = 0.90 * 1.05 - 1 = -5.5%, NOT a valuebet)
          - Over 2.5 @ 3.50
        Requesting Over 0.5 must match Over 0.5 @ 1.05 and NOT Over 2.5 @ 3.50.
        """
        quotes = [
            NormalizedExecutionQuote(
                bookmaker="Superbet",
                player="Cole Palmer",
                fixture="Chelsea vs Wolves",
                stat_type="SHOTS",
                line=0.5,
                side="OVER",
                odds=1.05,
            ),
            NormalizedExecutionQuote(
                bookmaker="Superbet",
                player="Cole Palmer",
                fixture="Chelsea vs Wolves",
                stat_type="SHOTS",
                line=2.5,
                side="OVER",
                odds=3.50,
            ),
        ]

        matcher = PropExecutionMatcher(normalized_quotes=quotes)
        comp_05 = matcher.match_execution_odds(
            player_name="Cole Palmer",
            team="Chelsea",
            opponent="Wolves",
            stat_type="SHOTS",
            line=0.5,
            side="OVER",
        )

        self.assertEqual(comp_05.best_executable_odds, 1.05)
        self.assertNotEqual(comp_05.best_executable_odds, 3.50)

        eval_05 = self.opp_engine.evaluate(
            hit_rate_pct=90.0,
            sample_size=10,
            stat_average=2.2,
            line=0.5,
            side="OVER",
            best_execution_odds=comp_05.best_executable_odds,
            best_execution_bookmaker=comp_05.best_executable_bookmaker,
            execution_status=comp_05.execution_status,
            stat_type="SHOTS",
        )

        # EV for Over 0.5 = 0.90 * 1.05 - 1 = -0.055 (-5.5%) -> BETTABLE, NOT VALUEBET
        self.assertAlmostEqual(eval_05.execution_ev_pct, -5.5, places=1)
        self.assertFalse(eval_05.is_valuebet)
        self.assertEqual(eval_05.actionability, "BETTABLE")

    def test_team_props_extensibility(self):
        """
        Team Props architecture verification:
        GenericValueBetEngine evaluates team prop candidates identically without new scrapers.
        Example: Real Madrid Team Shots Over 14.5
        P_model = 0.70 (model/historical rate), Superbet = 1.60
        fair odds = 1 / 0.70 = 1.43
        EV = 0.70 * 1.60 - 1 = +12.0% -> VALUEBET
        """
        team_prop_res = self.val_engine.evaluate_candidate(
            p_model=0.70,
            execution_odds=1.60,
            reference_odds=1.55,
            execution_status="BETTABLE",
        )

        self.assertTrue(team_prop_res.is_valuebet)
        self.assertEqual(team_prop_res.status, "VALUEBET")
        self.assertAlmostEqual(team_prop_res.fair_odds, 1.4286, places=3)
        self.assertAlmostEqual(team_prop_res.ev_pct, 12.0, places=1)

    def test_opportunity_explorer_adapter_valuebet_presentation(self):
        """
        Validates UnifiedOpportunityDTO contains all Stage 29 Value Bet fields:
        fair_odds, model_probability_pct, value_edge_pp, is_valuebet, status.
        """
        mock_prop = {
            "prop_id": "prop_test_saka_shots_05",
            "player_name": "Bukayo Saka",
            "team": "Arsenal",
            "opponent": "Chelsea",
            "stat_type": "SHOTS",
            "line": 0.5,
            "side": "OVER",
            "best_odds": 1.20,
            "best_bookmaker": "Bet365",
            "best_execution_odds": 1.45,
            "best_execution_bookmaker": "Superbet",
            "execution_status": "VALUEBET",
            "actionability": "VALUEBET",
            "historical_probability": 0.85,
            "model_probability": 0.85,
            "model_probability_pct": 85.0,
            "fair_odds": 1.1765,
            "execution_ev_pct": 23.25,
            "value_edge_pp": 16.03,
            "is_valuebet": True,
            "score": 88.0,
            "execution_odds": {
                "Superbet": {"status": "AVAILABLE", "decimal_odds": 1.45}
            },
        }

        dto = OpportunityExplorerAdapter.from_player_prop(mock_prop)

        self.assertEqual(dto.type, OpportunityType.PLAYER_PROP.value)
        self.assertEqual(dto.status, "VALUEBET")
        self.assertTrue(dto.is_valuebet)
        self.assertAlmostEqual(dto.fair_odds, 1.1765, places=3)
        self.assertEqual(dto.model_probability_pct, 85.0)
        self.assertAlmostEqual(dto.value_edge_pp, 16.03, places=2)
        self.assertEqual(dto.best_bookmaker, "Superbet")
        self.assertEqual(dto.execution_odds, 1.45)

        dto_dict = dto.to_dict()
        self.assertIn("fair_odds", dto_dict)
        self.assertIn("model_probability_pct", dto_dict)
        self.assertIn("value_edge_pp", dto_dict)
        self.assertIn("is_valuebet", dto_dict)

    def test_carranza_over_0_5_goals_regression_cannot_use_first_goalscorer_odds(self):
        """
        Regression Test: Julián Carranza Over 0.5 Goals.
        Ensures that First Goalscorer (@ 13.00), 2+ Goals (@ 15.00), or 1st Half Goal (@ 5.50)
        can NEVER be assigned to Over 0.5 Goals anytime prop.
        The exact anytime goalscorer quote (@ 2.67) must be used.
        """
        quotes = [
            NormalizedExecutionQuote(
                bookmaker="Superbet",
                player="Julián Carranza",
                fixture="Necaxa vs Cruz Azul",
                stat_type="FIRST_GOAL",
                line=0.5,
                side="OVER",
                odds=13.00,
                market_name="zawodnik - strzeli 1. gola",
                market_type="PLAYER_FIRST_GOAL",
            ),
            NormalizedExecutionQuote(
                bookmaker="Superbet",
                player="Julián Carranza",
                fixture="Necaxa vs Cruz Azul",
                stat_type="GOALS",
                line=1.5,
                side="OVER",
                odds=15.00,
                market_name="zawodnik - strzeli 2+ gole",
                market_type="PLAYER_GOALS",
            ),
            NormalizedExecutionQuote(
                bookmaker="Superbet",
                player="Julián Carranza",
                fixture="Necaxa vs Cruz Azul",
                stat_type="GOALS",
                line=0.5,
                side="OVER",
                odds=5.50,
                market_name="zawodnik - strzeli gola w 1. połowie",
                market_type="PLAYER_GOALS_FIRST_HALF",
                period="FIRST_HALF",
            ),
            NormalizedExecutionQuote(
                bookmaker="Superbet",
                player="Julián Carranza",
                fixture="Necaxa vs Cruz Azul",
                stat_type="GOALS",
                line=0.5,
                side="OVER",
                odds=2.67,
                market_name="zawodnik - strzeli gola",
                market_type="PLAYER_GOALS",
                period="FULL_TIME",
            ),
        ]

        matcher = PropExecutionMatcher(normalized_quotes=quotes)
        comp = matcher.match_execution_odds(
            player_name="Julián Carranza",
            team="Necaxa",
            opponent="Cruz Azul",
            stat_type="GOALS",
            line=0.5,
            side="OVER",
            reference_odds=[{"bookmaker": "Bet365", "line": 0.5, "side": "OVER", "decimal_odds": 2.60}],
        )

        self.assertEqual(comp.execution_status, "BETTABLE")
        self.assertEqual(comp.best_executable_odds, 2.67)
        self.assertNotEqual(comp.best_executable_odds, 13.00)
        self.assertNotEqual(comp.best_executable_odds, 15.00)
        self.assertNotEqual(comp.best_executable_odds, 5.50)

        # Evaluate with P_model = 0.40 (fair odds = 2.50)
        # EV with proper odds 2.67: 0.40 * 2.67 - 1 = +6.8% -> Genuine Valuebet
        # EV with false odds 13.00: 0.40 * 13.00 - 1 = +420.0% -> False Valuebet (PREVENTED)
        eval_res = self.opp_engine.evaluate(
            hit_rate_pct=40.0,
            sample_size=10,
            stat_average=0.4,
            line=0.5,
            side="OVER",
            best_odds=2.60,
            best_bookmaker="Bet365",
            best_execution_odds=comp.best_executable_odds,
            best_execution_bookmaker=comp.best_executable_bookmaker,
            execution_status=comp.execution_status,
            stat_type="GOALS",
        )

        self.assertEqual(eval_res.status, "VALUEBET")
        self.assertTrue(eval_res.is_valuebet)
        self.assertAlmostEqual(eval_res.execution_ev_pct, 6.8, delta=0.2)
        self.assertLess(eval_res.execution_ev_pct, 50.0)

    def test_carranza_no_anytime_quote_must_be_reference_only_not_valuebet(self):
        """
        When ONLY First Goalscorer (@ 13.00) or other sub-market exists and no genuine Anytime quote exists,
        matcher MUST reject the 13.00 quote, returning REFERENCE_ONLY / NO_EXECUTION_MARKET,
        and ValueBet Engine MUST NOT create a VALUEBET.
        """
        quotes = [
            NormalizedExecutionQuote(
                bookmaker="Superbet",
                player="Julián Carranza",
                fixture="Necaxa vs Cruz Azul",
                stat_type="FIRST_GOAL",
                line=0.5,
                side="OVER",
                odds=13.00,
                market_name="zawodnik - strzeli 1. gola",
                market_type="PLAYER_FIRST_GOAL",
            ),
            NormalizedExecutionQuote(
                bookmaker="Superbet",
                player="Julián Carranza",
                fixture="Necaxa vs Cruz Azul",
                stat_type="GOALS",
                line=1.5,
                side="OVER",
                odds=15.00,
                market_name="zawodnik - strzeli 2+ gole",
                market_type="PLAYER_GOALS",
            ),
        ]

        matcher = PropExecutionMatcher(normalized_quotes=quotes)
        comp = matcher.match_execution_odds(
            player_name="Julián Carranza",
            team="Necaxa",
            opponent="Cruz Azul",
            stat_type="GOALS",
            line=0.5,
            side="OVER",
            reference_odds=[{"bookmaker": "Bet365", "line": 0.5, "side": "OVER", "decimal_odds": 2.60}],
        )

        self.assertIsNone(comp.best_executable_odds)
        self.assertEqual(comp.execution_status, "REFERENCE_ONLY")

        # ValueBet Engine Evaluation
        val_res = self.val_engine.evaluate_candidate(
            p_model=0.40,
            execution_odds=comp.best_executable_odds,
            reference_odds=comp.reference_best_odds,
            execution_status=comp.execution_status,
        )

        self.assertFalse(val_res.is_valuebet)
        self.assertEqual(val_res.status, "REFERENCE_ONLY")
        self.assertIsNone(val_res.ev)
        self.assertIsNone(val_res.ev_pct)


if __name__ == "__main__":
    unittest.main()
