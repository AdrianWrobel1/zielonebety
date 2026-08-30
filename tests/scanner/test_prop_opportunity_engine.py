"""
Unit Tests for Player Prop Opportunity Engine (Stage 16)
"""

import unittest
from scanner.prop_opportunity_engine import PropOpportunityEngine, PropOpportunityEvaluation


class TestPropOpportunityEngine(unittest.TestCase):

    def setUp(self):
        self.engine = PropOpportunityEngine()

    def test_strong_opportunity_with_real_execution_odds(self):
        # 80% hit rate (8/10), Ref odds 7.00 (implied 14.29%), Execution odds 5.20 (implied 19.23%)
        eval_res = self.engine.evaluate(
            hit_rate_pct=80.0,
            sample_size=10,
            stat_average=3.30,
            line=2.5,
            side="OVER",
            best_odds=7.00,
            best_bookmaker="Bet365",
            best_execution_odds=5.20,
            best_execution_bookmaker="Superbet",
            execution_status="BETTABLE",
            last_5_avg=3.4,
            last_10_avg=3.3,
            hit_rate_count=8,
            stat_type="SHOTS",
        )

        self.assertEqual(eval_res.classification, "OPPORTUNITY")
        self.assertEqual(eval_res.actionability, "BETTABLE")
        self.assertGreaterEqual(eval_res.score, 75.0)
        self.assertAlmostEqual(eval_res.historical_probability, 0.80, places=2)
        self.assertAlmostEqual(eval_res.reference_market_probability, 1.0 / 7.00, places=3)
        self.assertAlmostEqual(eval_res.execution_market_probability, 1.0 / 5.20, places=3)
        self.assertAlmostEqual(eval_res.raw_edge, 0.80 - (1.0 / 7.00), places=3)
        self.assertAlmostEqual(eval_res.execution_edge, 0.80 - (1.0 / 5.20), places=3)
        self.assertAlmostEqual(eval_res.raw_edge_pct, 65.7, places=1)
        self.assertAlmostEqual(eval_res.execution_edge_pct, 60.8, places=1)
        self.assertEqual(eval_res.edge_type, "STATISTICAL_EDGE")
        self.assertTrue(any("Execution Edge" in r for r in eval_res.reasons))

    def test_reference_only_when_no_execution_odds(self):
        # 80% hit rate, 7.00 ref odds, NO execution odds -> REFERENCE_ONLY, execution_edge is None
        eval_res = self.engine.evaluate(
            hit_rate_pct=80.0,
            sample_size=10,
            stat_average=3.30,
            line=2.5,
            side="OVER",
            best_odds=7.00,
            best_bookmaker="Bet365",
            best_execution_odds=None,
            execution_status="REFERENCE_ONLY",
        )

        self.assertEqual(eval_res.classification, "OPPORTUNITY")
        self.assertEqual(eval_res.actionability, "REFERENCE_ONLY")
        self.assertIsNone(eval_res.execution_edge)
        self.assertIsNone(eval_res.execution_market_probability)
        self.assertIsNotNone(eval_res.raw_edge)
        self.assertTrue(any("No Polish execution odds" in w for w in eval_res.warnings))

    def test_negative_edge_warning_and_penalty(self):
        # 50% hit rate, 1.40 odds (implied 71.4%), raw edge is negative (-21.4pp)
        eval_res = self.engine.evaluate(
            hit_rate_pct=50.0,
            sample_size=10,
            stat_average=1.2,
            line=1.5,
            side="OVER",
            best_odds=1.40,
            best_bookmaker="Skybet",
            last_5_avg=1.0,
        )

        self.assertNotEqual(eval_res.classification, "OPPORTUNITY")
        self.assertLess(eval_res.raw_edge, 0.0)
        self.assertTrue(any("exceeds historical hit rate" in w for w in eval_res.warnings))

    def test_low_sample_size_classification(self):
        # High hit rate but tiny sample (< 5 games) -> SPECULATIVE with warning
        eval_res = self.engine.evaluate(
            hit_rate_pct=100.0,
            sample_size=2,
            stat_average=3.0,
            line=1.5,
            side="OVER",
            best_odds=2.00,
            best_bookmaker="Bet365",
        )

        self.assertEqual(eval_res.classification, "SPECULATIVE")
        self.assertTrue(any("Low sample size" in w for w in eval_res.warnings))

    def test_no_odds_evaluation(self):
        # Valid stats but no bookmaker odds available
        eval_res = self.engine.evaluate(
            hit_rate_pct=75.0,
            sample_size=12,
            stat_average=2.2,
            line=1.5,
            side="OVER",
            best_odds=None,
        )

        self.assertIsNone(eval_res.market_probability)
        self.assertIsNone(eval_res.raw_edge)
        self.assertEqual(eval_res.classification, "SHORTLIST")
        self.assertEqual(eval_res.actionability, "NO_EXECUTION_MARKET")
        self.assertTrue(any("No active reference bookmaker odds" in w for w in eval_res.warnings))


if __name__ == "__main__":
    unittest.main()
