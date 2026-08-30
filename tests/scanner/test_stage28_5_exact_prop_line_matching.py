"""
Stage 28.5 — Exact Prop Line Matching & Value Calculation Integrity Test Suite

Validates:
1. Production example regression test: Brian Rodríguez (Club América vs Club Puebla, SHOTS)
   - Input lines Over 0.5 @ 1.05 ... Over 9.5 @ 21.00
   - Target Over 0.5 returns 1.05 and NOT 21.00 (implied prob 95.24%, edge +4.8pp, EV +5.0%)
   - Target Over 9.5 returns 21.00 (implied prob 4.76%, edge +95.2pp, EV +2000%)
2. Negative test cases A through I:
   - A. Over 0.5 does not match Over 1.5
   - B. Over 0.5 does not match Under 0.5
   - C. Over 0.5 does not match another player
   - D. Over 0.5 does not match another fixture
   - E. Full-time Over 0.5 does not match First-half Over 0.5
   - F. Different stat types do not match (Shots vs SOT vs Fouls)
   - G. Exact reference line absent -> reference odds None, edge None, NO_REFERENCE_ODDS flag
   - H. Exact execution line absent -> execution odds None, execution edge None, REFERENCE_ONLY
   - I. Multiple reference lines remain independently addressable without crosstalk
"""

import unittest
from decimal import Decimal

from scanner.prop_execution_matcher import (
    PropExecutionMatcher,
    PropOddsComparison,
    CanonicalPropKey,
)
from scanner.execution_providers import NormalizedExecutionQuote
from scanner.prop_opportunity_engine import PropOpportunityEngine, PropOpportunityEvaluation


class TestStage28_5ExactPropLineMatching(unittest.TestCase):

    def setUp(self):
        self.opp_engine = PropOpportunityEngine()

    def test_production_regression_brian_rodriguez_over_0_5_vs_9_5(self):
        """
        Production example regression test:
        Player: Brian Rodríguez
        Match: Club América vs Club Puebla
        Stat: SHOTS
        Available reference odds:
          Over 0.5 @ 1.05
          Over 1.5 @ 1.20
          Over 2.5 @ 1.50
          Over 3.5 @ 2.00
          ...
          Over 9.5 @ 21.00

        Target Over 0.5 MUST select 1.05 and NEVER 21.00.
        Target Over 9.5 MUST select 21.00.
        """
        ref_odds_all = [
            {"bookmaker": "Bet365", "line": 0.5, "side": "OVER", "decimal_odds": 1.05},
            {"bookmaker": "Bet365", "line": 1.5, "side": "OVER", "decimal_odds": 1.20},
            {"bookmaker": "Bet365", "line": 2.5, "side": "OVER", "decimal_odds": 1.50},
            {"bookmaker": "Bet365", "line": 3.5, "side": "OVER", "decimal_odds": 2.00},
            {"bookmaker": "Bet365", "line": 4.5, "side": "OVER", "decimal_odds": 3.20},
            {"bookmaker": "Bet365", "line": 5.5, "side": "OVER", "decimal_odds": 5.00},
            {"bookmaker": "Bet365", "line": 6.5, "side": "OVER", "decimal_odds": 8.00},
            {"bookmaker": "Bet365", "line": 7.5, "side": "OVER", "decimal_odds": 11.00},
            {"bookmaker": "Bet365", "line": 8.5, "side": "OVER", "decimal_odds": 15.00},
            {"bookmaker": "Bet365", "line": 9.5, "side": "OVER", "decimal_odds": 21.00},
        ]

        matcher = PropExecutionMatcher(canonical_events=[])

        # 1. Target Over 0.5
        comp_05 = matcher.match_execution_odds(
            player_name="Brian Rodríguez",
            team="Club América",
            opponent="Club Puebla",
            stat_type="SHOTS",
            line=0.5,
            side="OVER",
            reference_odds=ref_odds_all,
        )

        self.assertEqual(comp_05.reference_best_odds, 1.05)
        self.assertNotEqual(comp_05.reference_best_odds, 21.00)
        self.assertEqual(comp_05.reference_best_bookmaker, "Bet365")
        self.assertEqual(len(comp_05.reference_odds_list), 1)
        self.assertEqual(comp_05.reference_odds_list[0]["decimal_odds"], 1.05)
        self.assertEqual(comp_05.reference_odds_list[0]["line"], 0.5)

        # Mathematical verification for Over 0.5
        eval_05 = self.opp_engine.evaluate(
            hit_rate_pct=100.0,
            sample_size=10,
            stat_average=3.2,
            line=0.5,
            side="OVER",
            best_odds=comp_05.reference_best_odds,
            best_bookmaker=comp_05.reference_best_bookmaker,
            stat_type="SHOTS",
        )

        # Implied probability = 1 / 1.05 = 0.95238 (95.24%)
        # Historical prob = 1.00 (100%)
        # Statistical edge = 1.00 - 0.95238 = +0.0476 (+4.8pp, NOT +95.2pp!)
        # EV = (1.00 * 1.05) - 1 = +0.05 (+5.0%, NOT +2000%!)
        self.assertAlmostEqual(eval_05.reference_market_probability, 1.0 / 1.05, places=4)
        self.assertAlmostEqual(eval_05.raw_edge, 1.0 - (1.0 / 1.05), places=4)
        self.assertAlmostEqual(eval_05.raw_edge_pct, 4.8, delta=0.2)
        self.assertAlmostEqual(eval_05.reference_ev_pct, 5.0, delta=0.2)
        self.assertNotAlmostEqual(eval_05.raw_edge_pct, 95.2, delta=1.0)
        self.assertNotAlmostEqual(eval_05.reference_ev_pct, 2000.0, delta=1.0)

        # 2. Target Over 9.5
        comp_95 = matcher.match_execution_odds(
            player_name="Brian Rodríguez",
            team="Club América",
            opponent="Club Puebla",
            stat_type="SHOTS",
            line=9.5,
            side="OVER",
            reference_odds=ref_odds_all,
        )

        self.assertEqual(comp_95.reference_best_odds, 21.00)
        self.assertEqual(len(comp_95.reference_odds_list), 1)
        self.assertEqual(comp_95.reference_odds_list[0]["decimal_odds"], 21.00)
        self.assertEqual(comp_95.reference_odds_list[0]["line"], 9.5)

        eval_95 = self.opp_engine.evaluate(
            hit_rate_pct=100.0,
            sample_size=10,
            stat_average=3.2,
            line=9.5,
            side="OVER",
            best_odds=comp_95.reference_best_odds,
            best_bookmaker=comp_95.reference_best_bookmaker,
            stat_type="SHOTS",
        )
        self.assertAlmostEqual(eval_95.reference_market_probability, 1.0 / 21.00, places=4)
        self.assertAlmostEqual(eval_95.raw_edge_pct, 95.2, delta=0.2)
        self.assertAlmostEqual(eval_95.reference_ev_pct, 2000.0, delta=1.0)

    def test_a_over_0_5_does_not_match_over_1_5(self):
        """A. Over 0.5 target does not match Over 1.5 quote."""
        ref_odds = [{"bookmaker": "Bet365", "line": 1.5, "side": "OVER", "decimal_odds": 1.80}]
        quotes = [
            NormalizedExecutionQuote(
                bookmaker="Superbet",
                player="Vinicius Junior",
                fixture="Real Madrid vs Osasuna",
                stat_type="SHOTS",
                line=1.5,  # 1.5 offered
                side="OVER",
                odds=1.85,
            )
        ]

        matcher = PropExecutionMatcher(normalized_quotes=quotes)
        comp = matcher.match_execution_odds(
            player_name="Vinicius Junior",
            team="Real Madrid",
            opponent="Osasuna",
            stat_type="SHOTS",
            line=0.5,  # 0.5 requested
            side="OVER",
            reference_odds=ref_odds,
        )

        self.assertIsNone(comp.reference_best_odds)
        self.assertIsNone(comp.best_executable_odds)
        self.assertEqual(comp.execution_status, "NO_EXECUTION_MARKET")

    def test_b_over_0_5_does_not_match_under_0_5(self):
        """B. Over 0.5 target does not match Under 0.5 quote."""
        ref_odds = [{"bookmaker": "Bet365", "line": 0.5, "side": "UNDER", "decimal_odds": 3.50}]
        quotes = [
            NormalizedExecutionQuote(
                bookmaker="Superbet",
                player="Robert Lewandowski",
                fixture="Barcelona vs Valencia",
                stat_type="SHOTS",
                line=0.5,
                side="UNDER",  # Under offered
                odds=3.60,
            )
        ]

        matcher = PropExecutionMatcher(normalized_quotes=quotes)
        comp = matcher.match_execution_odds(
            player_name="Robert Lewandowski",
            team="Barcelona",
            opponent="Valencia",
            stat_type="SHOTS",
            line=0.5,
            side="OVER",  # Over requested
            reference_odds=ref_odds,
        )

        self.assertIsNone(comp.reference_best_odds)
        self.assertIsNone(comp.best_executable_odds)
        self.assertEqual(comp.execution_status, "NO_EXECUTION_MARKET")

    def test_c_over_0_5_does_not_match_another_player(self):
        """C. Over 0.5 target does not match another player's quote in the same match."""
        quotes = [
            NormalizedExecutionQuote(
                bookmaker="Superbet",
                player="Henry Martin",
                fixture="Club America vs Club Puebla",
                stat_type="SHOTS",
                line=0.5,
                side="OVER",
                odds=1.40,
            )
        ]

        matcher = PropExecutionMatcher(normalized_quotes=quotes)
        comp = matcher.match_execution_odds(
            player_name="Brian Rodríguez",
            team="Club América",
            opponent="Club Puebla",
            stat_type="SHOTS",
            line=0.5,
            side="OVER",
        )

        self.assertIsNone(comp.best_executable_odds)
        self.assertEqual(comp.execution_status, "NO_EXECUTION_MARKET")

    def test_d_over_0_5_does_not_match_another_fixture(self):
        """D. Over 0.5 target does not match quote from a different fixture."""
        quotes = [
            NormalizedExecutionQuote(
                bookmaker="Superbet",
                player="Brian Rodríguez",
                fixture="Club America vs Tigres",  # Different opponent
                stat_type="SHOTS",
                line=0.5,
                side="OVER",
                odds=1.35,
            )
        ]

        matcher = PropExecutionMatcher(normalized_quotes=quotes)
        comp = matcher.match_execution_odds(
            player_name="Brian Rodríguez",
            team="Club América",
            opponent="Club Puebla",
            stat_type="SHOTS",
            line=0.5,
            side="OVER",
        )

        self.assertIsNone(comp.best_executable_odds)
        self.assertEqual(comp.execution_status, "NO_EXECUTION_MARKET")

    def test_e_full_time_does_not_match_first_half(self):
        """E. Full-time prop does not match a First-Half market."""
        mock_events = [
            {
                "id": "match-rma-bar",
                "home_team": "Real Madrid",
                "away_team": "Barcelona",
                "markets": [
                    {
                        "market_type": "PLAYER_SHOTS_1ST_HALF",  # First half
                        "line": 0.5,
                        "selections": [
                            {
                                "selection_type": "OVER",
                                "participant": "Kylian Mbappe",
                                "odds": {"superbet": 1.75},
                            }
                        ],
                    }
                ],
            }
        ]

        matcher = PropExecutionMatcher(canonical_events=mock_events)
        comp = matcher.match_execution_odds(
            player_name="Kylian Mbappe",
            team="Real Madrid",
            opponent="Barcelona",
            stat_type="SHOTS",  # Full time shots
            line=0.5,
            side="OVER",
        )

        self.assertIsNone(comp.best_executable_odds)
        self.assertEqual(comp.execution_status, "NO_EXECUTION_MARKET")

    def test_f_different_stat_types_do_not_match(self):
        """F. Target stat type (e.g. SHOTS) does not match SOT or FOULS."""
        quotes = [
            NormalizedExecutionQuote(
                bookmaker="Superbet",
                player="Pedri",
                fixture="Barcelona vs Sevilla",
                stat_type="SHOTS_ON_TARGET",  # SOT, not SHOTS
                line=0.5,
                side="OVER",
                odds=2.10,
            ),
            NormalizedExecutionQuote(
                bookmaker="Betclic",
                player="Pedri",
                fixture="Barcelona vs Sevilla",
                stat_type="FOULS",  # FOULS, not SHOTS
                line=0.5,
                side="OVER",
                odds=1.45,
            ),
        ]

        matcher = PropExecutionMatcher(normalized_quotes=quotes)
        comp = matcher.match_execution_odds(
            player_name="Pedri",
            team="Barcelona",
            opponent="Sevilla",
            stat_type="SHOTS",
            line=0.5,
            side="OVER",
        )

        self.assertIsNone(comp.best_executable_odds)
        self.assertEqual(comp.execution_status, "NO_EXECUTION_MARKET")

    def test_g_exact_reference_line_absent_yields_unavailable_and_no_edge(self):
        """
        G. If exact reference line is absent:
        - Reference odds are unavailable (None).
        - Statistical edge & EV are not calculated.
        - NO_REFERENCE_ODDS data quality flag is present.
        """
        # Quotes exist for 1.5, 2.5, 9.5, but NOT 0.5
        ref_odds = [
            {"bookmaker": "Bet365", "line": 1.5, "side": "OVER", "decimal_odds": 1.40},
            {"bookmaker": "Bet365", "line": 2.5, "side": "OVER", "decimal_odds": 2.20},
            {"bookmaker": "Bet365", "line": 9.5, "side": "OVER", "decimal_odds": 25.00},
        ]

        matcher = PropExecutionMatcher(canonical_events=[])
        comp = matcher.match_execution_odds(
            player_name="Lamine Yamal",
            team="Barcelona",
            opponent="Girona",
            stat_type="SHOTS",
            line=0.5,  # 0.5 requested
            side="OVER",
            reference_odds=ref_odds,
        )

        self.assertIsNone(comp.reference_best_odds)
        self.assertEqual(len(comp.reference_odds_list), 0)

        eval_res = self.opp_engine.evaluate(
            hit_rate_pct=90.0,
            sample_size=10,
            stat_average=2.8,
            line=0.5,
            side="OVER",
            best_odds=comp.reference_best_odds,  # None
            best_bookmaker=comp.reference_best_bookmaker,
            stat_type="SHOTS",
        )

        self.assertIsNone(eval_res.reference_market_probability)
        self.assertIsNone(eval_res.raw_edge)
        self.assertIsNone(eval_res.raw_edge_pct)
        self.assertIsNone(eval_res.reference_ev)
        self.assertIsNone(eval_res.reference_ev_pct)
        self.assertIn("NO_REFERENCE_ODDS", eval_res.data_quality_flags)

    def test_h_exact_execution_line_absent_yields_reference_only_or_no_market(self):
        """
        H. If exact execution line is absent:
        - Execution odds are unavailable (None).
        - Execution edge is not calculated.
        - Status is REFERENCE_ONLY if reference odds exist.
        """
        ref_odds = [{"bookmaker": "Bet365", "line": 0.5, "side": "OVER", "decimal_odds": 1.30}]
        quotes = [
            NormalizedExecutionQuote(
                bookmaker="Superbet",
                player="Cole Palmer",
                fixture="Chelsea vs Tottenham",
                stat_type="SHOTS",
                line=1.5,  # Only 1.5 offered in Poland
                side="OVER",
                odds=1.85,
            )
        ]

        matcher = PropExecutionMatcher(normalized_quotes=quotes)
        comp = matcher.match_execution_odds(
            player_name="Cole Palmer",
            team="Chelsea",
            opponent="Tottenham",
            stat_type="SHOTS",
            line=0.5,  # 0.5 target
            side="OVER",
            reference_odds=ref_odds,
        )

        self.assertEqual(comp.execution_status, "REFERENCE_ONLY")
        self.assertEqual(comp.reference_best_odds, 1.30)
        self.assertIsNone(comp.best_executable_odds)

        eval_res = self.opp_engine.evaluate(
            hit_rate_pct=85.0,
            sample_size=10,
            stat_average=2.9,
            line=0.5,
            side="OVER",
            best_odds=comp.reference_best_odds,
            best_bookmaker=comp.reference_best_bookmaker,
            best_execution_odds=comp.best_executable_odds,
            execution_status=comp.execution_status,
            stat_type="SHOTS",
        )

        self.assertEqual(eval_res.actionability, "REFERENCE_ONLY")
        self.assertIsNone(eval_res.execution_market_probability)
        self.assertIsNone(eval_res.execution_edge)
        self.assertIsNone(eval_res.execution_ev)
        self.assertIn("NO_POLISH_ODDS", eval_res.data_quality_flags)

    def test_i_multiple_reference_lines_remain_independently_addressable(self):
        """
        I. Multiple reference lines for the same player/stat/event remain independently addressable.
        """
        ref_odds_dataset = [
            {"bookmaker": "Bet365", "line": 0.5, "side": "OVER", "decimal_odds": 1.10},
            {"bookmaker": "Bet365", "line": 1.5, "side": "OVER", "decimal_odds": 1.65},
            {"bookmaker": "Bet365", "line": 2.5, "side": "OVER", "decimal_odds": 2.80},
            {"bookmaker": "Bet365", "line": 3.5, "side": "OVER", "decimal_odds": 5.50},
        ]

        matcher = PropExecutionMatcher(canonical_events=[])

        lines_and_expected_odds = [
            (0.5, 1.10),
            (1.5, 1.65),
            (2.5, 2.80),
            (3.5, 5.50),
        ]

        for target_line, exp_odds in lines_and_expected_odds:
            comp = matcher.match_execution_odds(
                player_name="Erling Haaland",
                team="Manchester City",
                opponent="Liverpool",
                stat_type="SHOTS",
                line=target_line,
                side="OVER",
                reference_odds=ref_odds_dataset,
            )
            self.assertEqual(comp.reference_best_odds, exp_odds)
            self.assertEqual(len(comp.reference_odds_list), 1)
            self.assertEqual(comp.reference_odds_list[0]["line"], target_line)


if __name__ == "__main__":
    unittest.main()
