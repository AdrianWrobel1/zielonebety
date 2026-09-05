"""
Unit & Integration Tests for Stage 3: StatsHub Props -> Reference Odds -> Value Evaluation.

Verifies:
1. Reference Odds calculation & Harmonic Consensus over multiple foreign bookmakers.
2. De-margin & Fair Probability calculation.
3. Rejection reasons:
   - INSUFFICIENT_REFERENCE_SOURCES
   - INVALID_REFERENCE_ODDS
   - STALE_REFERENCE_DATA
   - REFERENCE_LINE_MISMATCH
   - REFERENCE_SELECTION_MISMATCH
   - POLISH_ODDS_UNAVAILABLE
   - INVALID_POLISH_ODDS
   - BELOW_VALUE_THRESHOLD
   - EXCESSIVE_VALUE_SANITY_CAP
4. Polish Tax Engine integration:
   - Superbet: 12% turnover tax (0.88 multiplier)
   - Betclic: 0% tax (1.0 multiplier)
5. Decision state transitions:
   MATCHED -> REFERENCE_VALID -> EVALUATED -> POSITIVE_EDGE -> QUALIFIED
6. Unified evaluation for both Player Props and Team Props.
7. Full Provenance audit trail.
"""

import unittest
from datetime import datetime, timezone, timedelta
from decimal import Decimal

from providers.statshub.models import (
    StatsHubFixture,
    StatsHubPlayerStat,
    StatsHubBookmakerOdds,
    StatsHubPropResult,
)
from providers.statshub.team_models import (
    StatsHubTeamFixture,
    StatsHubTeamStat,
    StatsHubTeamBookmakerOdds,
    StatsHubTeamPropResult,
)
from scanner.prop_execution_matcher import (
    PropExecutionMatcher,
    PropOddsComparison,
    ExecutionBookmakerOdds,
)
from scanner.team_prop_execution_matcher import (
    TeamPropExecutionMatcher,
    TeamPropOddsComparison,
    TeamExecutionOdds,
)
from scanner.props_value_evaluator import (
    PropsValueEvaluator,
    PropsEvaluationConfig,
    ValueEvaluationReasonCode,
    ReferenceOddsCalculation,
    PropsValueEvaluationResult,
)


class TestPropsValueEvaluation(unittest.TestCase):

    def setUp(self):
        self.config = PropsEvaluationConfig(
            min_value_percent=Decimal("3.0"),
            max_value_sanity_cap=Decimal("40.0"),
            min_reference_sources=1,
            max_freshness_seconds=1800,
            default_prop_margin_factor=Decimal("1.04"),
        )
        self.evaluator = PropsValueEvaluator(config=self.config)

    # =========================================================================
    # 1. REFERENCE ODDS & FAIR PROBABILITY CALCULATIONS
    # =========================================================================

    def test_harmonic_consensus_and_demargin_multiple_foreign_sources(self):
        """
        Calculates consensus from multiple foreign bookmakers:
        Bet365 @ 1.80 (implied p = 1/1.80 = 0.5556)
        Unibet @ 1.90 (implied p = 1/1.90 = 0.5263)
        Skybet @ 1.85 (implied p = 1/1.85 = 0.5405)
        Mean implied p = (0.5556 + 0.5263 + 0.5405) / 3 = 0.5408
        Harmonic consensus odds = 1 / 0.5408 = 1.8491
        Fair probability = 0.5408 / 1.04 = 0.5200
        Fair odds = 1 / 0.5200 = 1.9231
        """
        ref_odds = [
            {"bookmaker": "Bet365", "line": 1.5, "side": "OVER", "odds": 1.80},
            {"bookmaker": "Unibet", "line": 1.5, "side": "OVER", "odds": 1.90},
            {"bookmaker": "Skybet", "line": 1.5, "side": "OVER", "odds": 1.85},
        ]

        calc = self.evaluator.calculate_reference_odds(
            reference_odds_list=ref_odds,
            target_line=1.5,
            target_side="OVER",
        )

        self.assertTrue(calc.is_valid)
        self.assertEqual(len(calc.used_sources), 3)
        self.assertEqual(len(calc.rejected_sources), 0)
        self.assertAlmostEqual(calc.consensus_implied_prob, 0.5408, places=3)
        self.assertAlmostEqual(calc.consensus_odds, 1.8491, places=2)
        self.assertAlmostEqual(calc.fair_probability, 0.5200, places=3)
        self.assertAlmostEqual(calc.fair_odds, 1.9231, places=2)

    def test_insufficient_reference_sources_rejection(self):
        """When empty reference list or count < min_reference_sources -> INSUFFICIENT_REFERENCE_SOURCES."""
        calc_empty = self.evaluator.calculate_reference_odds(
            reference_odds_list=[],
            target_line=1.5,
            target_side="OVER",
        )
        self.assertFalse(calc_empty.is_valid)
        self.assertEqual(calc_empty.diagnostic, ValueEvaluationReasonCode.INSUFFICIENT_REFERENCE_SOURCES.value)

        strict_evaluator = PropsValueEvaluator(config=PropsEvaluationConfig(min_reference_sources=2))
        calc_one = strict_evaluator.calculate_reference_odds(
            reference_odds_list=[{"bookmaker": "Bet365", "line": 1.5, "side": "OVER", "odds": 1.80}],
            target_line=1.5,
            target_side="OVER",
        )
        self.assertFalse(calc_one.is_valid)
        self.assertEqual(calc_one.diagnostic, ValueEvaluationReasonCode.INSUFFICIENT_REFERENCE_SOURCES.value)
        self.assertEqual(len(calc_one.used_sources), 1)

    def test_invalid_reference_odds_rejection(self):
        """Odds <= 1.0 or non-numeric rejected -> INVALID_REFERENCE_ODDS."""
        ref_odds = [
            {"bookmaker": "Bet365", "line": 1.5, "side": "OVER", "odds": 0.0},
            {"bookmaker": "Unibet", "line": 1.5, "side": "OVER", "odds": -1.5},
            {"bookmaker": "Skybet", "line": 1.5, "side": "OVER", "odds": "N/A"},
        ]

        calc = self.evaluator.calculate_reference_odds(
            reference_odds_list=ref_odds,
            target_line=1.5,
            target_side="OVER",
        )
        self.assertFalse(calc.is_valid)
        self.assertEqual(len(calc.rejected_sources), 3)
        self.assertTrue(all(r["reason_code"] == ValueEvaluationReasonCode.INVALID_REFERENCE_ODDS.value for r in calc.rejected_sources))

    def test_stale_reference_data_rejection(self):
        """Reference quote older than max_freshness_seconds (1800s) -> STALE_REFERENCE_DATA."""
        now = datetime.now(timezone.utc)
        stale_ts = (now - timedelta(seconds=2400)).isoformat()
        fresh_ts = (now - timedelta(seconds=300)).isoformat()

        ref_odds = [
            {"bookmaker": "Bet365", "line": 1.5, "side": "OVER", "odds": 1.80, "timestamp": stale_ts},
            {"bookmaker": "Unibet", "line": 1.5, "side": "OVER", "odds": 1.85, "timestamp": fresh_ts},
        ]

        calc = self.evaluator.calculate_reference_odds(
            reference_odds_list=ref_odds,
            target_line=1.5,
            target_side="OVER",
            current_time=now,
        )

        self.assertTrue(calc.is_valid)
        self.assertEqual(len(calc.used_sources), 1)  # Only Unibet is fresh
        self.assertEqual(len(calc.rejected_sources), 1)  # Bet365 is stale
        self.assertEqual(calc.rejected_sources[0]["reason_code"], ValueEvaluationReasonCode.STALE_REFERENCE_DATA.value)

    def test_reference_line_and_selection_mismatch_filtering(self):
        """Reference quotes with different line (2.5 != 1.5) or side (UNDER != OVER) are rejected."""
        ref_odds = [
            {"bookmaker": "Bet365", "line": 2.5, "side": "OVER", "odds": 2.80},  # Line mismatch
            {"bookmaker": "Unibet", "line": 1.5, "side": "UNDER", "odds": 1.60},  # Selection mismatch
            {"bookmaker": "Skybet", "line": 1.5, "side": "OVER", "odds": 1.85},  # Valid
        ]

        calc = self.evaluator.calculate_reference_odds(
            reference_odds_list=ref_odds,
            target_line=1.5,
            target_side="OVER",
        )

        self.assertTrue(calc.is_valid)
        self.assertEqual(len(calc.used_sources), 1)
        self.assertEqual(calc.used_sources[0]["bookmaker"], "Skybet")
        self.assertEqual(len(calc.rejected_sources), 2)
        rejection_codes = [r["reason_code"] for r in calc.rejected_sources]
        self.assertIn(ValueEvaluationReasonCode.REFERENCE_LINE_MISMATCH.value, rejection_codes)
        self.assertIn(ValueEvaluationReasonCode.REFERENCE_SELECTION_MISMATCH.value, rejection_codes)

    # =========================================================================
    # 2. POLISH TAX & VALUE EVALUATION
    # =========================================================================

    def test_superbet_tax_and_qualified_valuebet(self):
        """
        Reference Fair Probability P_fair = 0.50 (Fair odds = 2.00).
        Superbet raw odds = 2.50.
        Superbet 12% turnover tax -> effective net odds = 2.50 * 0.88 = 2.20.
        Gross EV = (2.50 * 0.50) - 1 = +25.0%.
        Net EV = (2.20 * 0.50) - 1 = +10.0%.
        Net EV (+10.0%) >= min_value_percent (+3.0%) -> QUALIFIED VALUEBET.
        """
        ref_odds = [{"bookmaker": "Bet365", "line": 1.5, "side": "OVER", "odds": 1.9231}]  # Implied = 0.52, Fair = 0.50
        exec_odds = {
            "Superbet": {
                "status": "AVAILABLE",
                "decimal_odds": 2.50,
                "selection_id": "sb-sel-1",
                "market_id": "sb-mkt-1",
            }
        }

        res = self.evaluator.evaluate_matched_prop(
            canonical_key="player_prop:vinicius:shots_on_target:over:1.5",
            stat_type="SHOTS_ON_TARGET",
            line=1.5,
            side="OVER",
            reference_odds_list=ref_odds,
            execution_odds=exec_odds,
        )

        self.assertEqual(res.overall_status, "QUALIFIED")
        self.assertTrue(res.is_valuebet)
        self.assertEqual(res.primary_reason_code, ValueEvaluationReasonCode.QUALIFIED.value)
        self.assertEqual(res.best_bookmaker, "Superbet")
        self.assertEqual(res.best_raw_odds, 2.50)
        self.assertEqual(res.best_effective_odds, 2.20)
        self.assertAlmostEqual(res.best_gross_ev_pct, 25.0, places=1)
        self.assertAlmostEqual(res.best_net_ev_pct, 10.0, places=1)

        sb_eval = res.bookmaker_evaluations["Superbet"]
        self.assertTrue(sb_eval.is_tax_applied)
        self.assertEqual(sb_eval.tax_rate, 0.12)
        self.assertTrue(sb_eval.is_qualified)

    def test_betclic_zero_tax_promotion_evaluation(self):
        """
        Reference Fair Probability P_fair = 0.50.
        Betclic raw odds = 2.10 (0% tax promotion -> effective net odds = 2.10).
        Net EV = (2.10 * 0.50) - 1 = +5.0%.
        Net EV (+5.0%) >= min_value_percent (+3.0%) -> QUALIFIED.
        """
        ref_odds = [{"bookmaker": "Bet365", "line": 0.5, "side": "OVER", "odds": 1.9231}]
        exec_odds = {
            "Betclic": {
                "status": "AVAILABLE",
                "decimal_odds": 2.10,
                "selection_id": "bc-sel-1",
            }
        }

        res = self.evaluator.evaluate_matched_prop(
            canonical_key="player_prop:haaland:shots:over:0.5",
            stat_type="SHOTS",
            line=0.5,
            side="OVER",
            reference_odds_list=ref_odds,
            execution_odds=exec_odds,
        )

        self.assertEqual(res.overall_status, "QUALIFIED")
        self.assertTrue(res.is_valuebet)
        bc_eval = res.bookmaker_evaluations["Betclic"]
        self.assertFalse(bc_eval.is_tax_applied)
        self.assertEqual(bc_eval.effective_net_odds, 2.10)
        self.assertAlmostEqual(bc_eval.net_ev_pct, 5.0, places=1)

    def test_positive_gross_ev_but_negative_net_ev_after_tax_is_rejected(self):
        """
        P_fair = 0.50.
        Superbet raw odds = 2.10.
        Gross EV = (2.10 * 0.50) - 1 = +5.0% (Positive gross value!).
        Effective net odds after 12% tax = 2.10 * 0.88 = 1.848.
        Net EV = (1.848 * 0.50) - 1 = -7.6% (Negative return for user!).
        Result MUST NOT be QUALIFIED or POSITIVE_EDGE -> EVALUATED / BELOW_VALUE_THRESHOLD.
        """
        ref_odds = [{"bookmaker": "Bet365", "line": 1.5, "side": "OVER", "odds": 1.9231}]
        exec_odds = {
            "Superbet": {
                "status": "AVAILABLE",
                "decimal_odds": 2.10,
            }
        }

        res = self.evaluator.evaluate_matched_prop(
            canonical_key="player_prop:saka:fouls:over:1.5",
            stat_type="FOULS",
            line=1.5,
            side="OVER",
            reference_odds_list=ref_odds,
            execution_odds=exec_odds,
        )

        self.assertEqual(res.overall_status, "EVALUATED")
        self.assertFalse(res.is_valuebet)
        self.assertEqual(res.primary_reason_code, ValueEvaluationReasonCode.BELOW_VALUE_THRESHOLD.value)
        self.assertAlmostEqual(res.best_gross_ev_pct, 5.0, places=1)
        self.assertAlmostEqual(res.best_net_ev_pct, -7.6, places=1)

    def test_palpable_error_sanity_cap_protection(self):
        """
        P_fair = 0.50.
        Superbet mistakenly prices raw odds = 3.50 (Effective = 3.08, Net EV = +54.0%).
        Net EV (+54.0%) > max_value_sanity_cap (+40.0%) -> EXCESSIVE_VALUE_SANITY_CAP (Palpable Error).
        """
        ref_odds = [{"bookmaker": "Bet365", "line": 1.5, "side": "OVER", "odds": 1.9231}]
        exec_odds = {
            "Superbet": {
                "status": "AVAILABLE",
                "decimal_odds": 3.50,
            }
        }

        res = self.evaluator.evaluate_matched_prop(
            canonical_key="player_prop:kane:shots:over:1.5",
            stat_type="SHOTS",
            line=1.5,
            side="OVER",
            reference_odds_list=ref_odds,
            execution_odds=exec_odds,
        )

        self.assertEqual(res.overall_status, "POSITIVE_EDGE")
        self.assertFalse(res.is_valuebet)
        self.assertEqual(res.primary_reason_code, ValueEvaluationReasonCode.EXCESSIVE_VALUE_SANITY_CAP.value)
        self.assertIn("exceeds palpable error sanity cap", res.primary_reason)

    def test_polish_odds_unavailable_state(self):
        """When reference calculation is valid but Polish bookmakers have no odds -> REFERENCE_VALID."""
        ref_odds = [{"bookmaker": "Bet365", "line": 1.5, "side": "OVER", "odds": 1.80}]
        exec_odds = {
            "Superbet": {"status": "UNAVAILABLE", "decimal_odds": None, "reason": "No market found"},
            "Betclic": {"status": "UNAVAILABLE", "decimal_odds": None, "reason": "No market found"},
        }

        res = self.evaluator.evaluate_matched_prop(
            canonical_key="player_prop:palmer:assists:over:1.5",
            stat_type="ASSISTS",
            line=1.5,
            side="OVER",
            reference_odds_list=ref_odds,
            execution_odds=exec_odds,
        )

        self.assertEqual(res.overall_status, "REFERENCE_VALID")
        self.assertFalse(res.is_valuebet)
        self.assertEqual(res.primary_reason_code, ValueEvaluationReasonCode.POLISH_ODDS_UNAVAILABLE.value)

    # =========================================================================
    # 3. HIGH-LEVEL PLAYER PROPS & TEAM PROPS WORKFLOWS
    # =========================================================================

    def test_evaluate_player_prop_high_level_workflow(self):
        """End-to-end evaluation taking StatsHubPropResult and PropOddsComparison."""
        statshub_prop = StatsHubPropResult(
            player_stat=StatsHubPlayerStat(
                player_id=1001,
                player_name="Robert Lewandowski",
                team="Barcelona",
                opponent="Real Madrid",
                stat_type="shots_on_target",
                line=1.5,
                odds_type="over",
                fixture=StatsHubFixture(fixture_id="16416308", home_team="Barcelona", away_team="Real Madrid"),
                bookmaker_odds=[
                    StatsHubBookmakerOdds(bookmaker="Bet365", line=1.5, side="over", decimal_odds=1.80),
                    StatsHubBookmakerOdds(bookmaker="Unibet", line=1.5, side="over", decimal_odds=1.85),
                ],
            )
        )

        matcher = PropExecutionMatcher()
        odds_comp = matcher.match_statshub_prop(
            statshub_prop=statshub_prop,
            normalized_quotes=[],  # No execution quotes
        )

        # Inject matched Superbet quote
        odds_comp.execution_odds["Superbet"] = ExecutionBookmakerOdds(
            bookmaker="Superbet",
            status="AVAILABLE",
            decimal_odds=2.40,
            line=1.5,
            side="OVER",
            selection_id="sb-sel-lew-15",
            market_id="PLAYER_SHOTS_ON_TARGET",
        )

        val_res = self.evaluator.evaluate_player_prop(
            statshub_prop=statshub_prop,
            odds_comparison=odds_comp,
        )

        self.assertEqual(val_res.overall_status, "QUALIFIED")
        self.assertTrue(val_res.is_valuebet)
        self.assertEqual(val_res.best_bookmaker, "Superbet")
        self.assertEqual(val_res.best_raw_odds, 2.40)
        self.assertAlmostEqual(val_res.best_effective_odds, 2.112, places=3)
        self.assertIn("reference_fair_probability", val_res.provenance)

    def test_evaluate_team_prop_high_level_workflow(self):
        """End-to-end evaluation taking StatsHubTeamPropResult and TeamPropOddsComparison."""
        statshub_team_prop = StatsHubTeamPropResult(
            team_stat=StatsHubTeamStat(
                team_id=2821,
                team_name="Celta Vigo",
                opponent_name="Athletic Club",
                stat_type="corners",
                line=4.5,
                odds_type="over",
                participant_role="HOME",
                fixture=StatsHubTeamFixture(fixture_id="16416308", home_team="Celta Vigo", away_team="Athletic Club"),
                bookmaker_odds=[
                    StatsHubTeamBookmakerOdds(bookmaker="Bet365", line=4.5, side="over", decimal_odds=1.80),
                    StatsHubTeamBookmakerOdds(bookmaker="Paddy Power", line=4.5, side="over", decimal_odds=1.85),
                ],
            )
        )

        matcher = TeamPropExecutionMatcher()
        odds_comp = matcher.match_statshub_team_prop(
            statshub_team_prop=statshub_team_prop,
            normalized_quotes=[],
        )

        # Inject matched Betclic quote (0% tax)
        odds_comp.execution_odds["Betclic"] = TeamExecutionOdds(
            bookmaker="Betclic",
            status="AVAILABLE",
            decimal_odds=2.15,
            line=4.5,
            side="OVER",
            selection_id="bc-sel-celta-corn-45",
            market_id="TEAM_CORNERS",
        )

        val_res = self.evaluator.evaluate_team_prop(
            statshub_team_prop=statshub_team_prop,
            odds_comparison=odds_comp,
        )

        self.assertEqual(val_res.overall_status, "QUALIFIED")
        self.assertTrue(val_res.is_valuebet)
        self.assertEqual(val_res.best_bookmaker, "Betclic")
        self.assertEqual(val_res.best_raw_odds, 2.15)
        self.assertEqual(val_res.best_effective_odds, 2.15)
        self.assertAlmostEqual(val_res.best_net_ev_pct, 13.3, places=1)



if __name__ == "__main__":
    unittest.main()
