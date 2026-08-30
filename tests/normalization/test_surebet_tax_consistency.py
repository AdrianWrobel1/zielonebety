import unittest
from decimal import Decimal

from core.tax_engine import TaxEngine
from normalization.market_identity import CanonicalMarketKey, CanonicalMarketType
from normalization.selection_identity import CanonicalSelectionKey
from normalization.odds_comparison import OddsComparison, OddsComparisonStatus
from normalization.surebet import SurebetDetectorEngine, SurebetStatus
from api.services import serialize_opportunity_detail


class TestSurebetTaxConsistency(unittest.TestCase):
    def setUp(self):
        self.tax_engine = TaxEngine()
        self.detector = SurebetDetectorEngine(tax_engine=self.tax_engine)
        self.event_id = 'cev_prod_test_001'
        self.market_key = CanonicalMarketKey(
            market_type=CanonicalMarketType.TOTALS,
            scope='MATCH',
            period='FULL_TIME',
            line=Decimal('2.5'),
        )

    def _create_comp(
        self,
        selection_type: str,
        source_provider: str,
        target_provider: str,
        source_odds: float,
        target_odds: float,
    ) -> OddsComparison:
        sel_k = CanonicalSelectionKey(market_key=self.market_key, selection_type=selection_type)
        return OddsComparison(
            canonical_event_id=self.event_id,
            canonical_market_key=self.market_key,
            canonical_selection_key=sel_k,
            source_provider=source_provider,
            target_provider=target_provider,
            source_event_id=f'p_ev_{source_provider}',
            target_event_id=f'p_ev_{target_provider}',
            source_internal_event_id='int_ev_src',
            target_internal_event_id='int_ev_tgt',
            source_market_id=f'p_mkt_{source_provider}',
            target_market_id=f'p_mkt_{target_provider}',
            source_selection_id=f'sel_{source_provider}_{selection_type.lower()}',
            target_selection_id=f'sel_{target_provider}_{selection_type.lower()}',
            status=OddsComparisonStatus.VALID,
            source_odds=Decimal(str(source_odds)),
            target_odds=Decimal(str(target_odds)),
            evidence={'source_validation_status': 'VALID', 'target_validation_status': 'VALID'},
        )

    def test_production_bug_reproduction_and_fix(self):
        # Production bug scenario: Betclic 2.80 OVER (tax 0%) vs Superbet 1.72 UNDER (tax 12% -> eff 1.5136)
        # S = 1/2.80 + 1/1.5136 = ~1.0178 >= 1.0 -> NO_SUREBET
        comparisons = [
            self._create_comp('OVER', 'superbet', 'betclic', source_odds=1.40, target_odds=2.80),
            self._create_comp('UNDER', 'superbet', 'betclic', source_odds=1.72, target_odds=1.45),
        ]

        result = self.detector.detect(comparisons)
        self.assertEqual(result.metrics.surebet_count, 0)
        self.assertEqual(len(result.opportunities), 0)
        self.assertEqual(len(result.no_surebet_evaluations), 1)

        eval_rec = result.no_surebet_evaluations[0]
        self.assertEqual(eval_rec.status, SurebetStatus.NO_SUREBET)

        legs_by_type = {l.selection_type: l for l in eval_rec.best_legs}
        self.assertEqual(legs_by_type['OVER'].provider, 'betclic')
        self.assertEqual(legs_by_type['OVER'].odds, Decimal('2.80'))
        self.assertEqual(legs_by_type['OVER'].effective_odds, Decimal('2.80'))
        self.assertEqual(legs_by_type['OVER'].tax_rate, Decimal('0'))
        self.assertFalse(legs_by_type['OVER'].is_tax_applied)

        self.assertEqual(legs_by_type['UNDER'].provider, 'superbet')
        self.assertEqual(legs_by_type['UNDER'].odds, Decimal('1.72'))
        self.assertEqual(legs_by_type['UNDER'].effective_odds, Decimal('1.72') * Decimal('0.88'))
        self.assertEqual(legs_by_type['UNDER'].tax_rate, Decimal('0.12'))
        self.assertTrue(legs_by_type['UNDER'].is_tax_applied)

        expected_s = Decimal('1.0') / Decimal('2.80') + Decimal('1.0') / Decimal('1.5136')
        self.assertEqual(eval_rec.implied_probability_sum, expected_s)
        self.assertGreater(eval_rec.implied_probability_sum, Decimal('1.0'))
        self.assertLess(eval_rec.arbitrage_margin, Decimal('0.0'))

    def test_tax_adjusted_positive_surebet(self):
        # Betclic 2.10 (0% tax) vs Superbet 2.40 (12% tax -> eff 2.112)
        # S = 1/2.10 + 1/2.112 = ~0.9497 < 1.0 -> SUREBET
        comparisons = [
            self._create_comp('OVER', 'superbet', 'betclic', source_odds=1.80, target_odds=2.10),
            self._create_comp('UNDER', 'superbet', 'betclic', source_odds=2.40, target_odds=1.85),
        ]

        result = self.detector.detect(comparisons)
        self.assertEqual(result.metrics.surebet_count, 1)
        self.assertEqual(len(result.opportunities), 1)

        opp = result.opportunities[0]
        self.assertEqual(opp.status, SurebetStatus.SUREBET)
        self.assertLess(opp.implied_probability_sum, Decimal('1.0'))
        self.assertGreater(opp.arbitrage_margin, Decimal('0.0'))

        detail = serialize_opportunity_detail(opp)
        self.assertTrue(detail['mathematical_explanation']['is_surebet'])
        self.assertTrue(detail['stake_calculator']['is_surebet'])
        self.assertLess(detail['mathematical_explanation']['implied_probability_sum'], 1.0)
        self.assertGreater(detail['mathematical_explanation']['arbitrage_margin_pct'], 0.0)

    def test_dynamic_tax_reconfiguration(self):
        comparisons = [
            self._create_comp('OVER', 'superbet', 'betclic', source_odds=1.80, target_odds=2.10),
            self._create_comp('UNDER', 'superbet', 'betclic', source_odds=2.40, target_odds=1.85),
        ]

        self.tax_engine.update_config('betclic', tax_enabled=False, tax_rate=0.0)
        res1 = self.detector.detect(comparisons)
        self.assertEqual(res1.metrics.surebet_count, 1)

        self.tax_engine.update_config('betclic', tax_enabled=True, tax_rate=Decimal('0.12'))
        res2 = self.detector.detect(comparisons)
        self.assertEqual(res2.metrics.surebet_count, 0)
        self.assertEqual(len(res2.no_surebet_evaluations), 1)
        self.assertGreater(res2.no_surebet_evaluations[0].implied_probability_sum, Decimal('1.0'))

    def test_stage_24d_production_observed_example_betclic_280_vs_superbet_188(self):
        """Validates the exact production case:
        Betclic OVER 2.80 (0% tax -> eff 2.80)
        Superbet UNDER 1.88 (12% tax -> eff 1.6544)
        Gross S = 1/2.80 + 1/1.88 = ~0.88907 -> Gross Margin = +12.48%
        Net S = 1/2.80 + 1/1.6544 = ~0.96159 -> Net Margin = +3.99%
        """
        shots_market = CanonicalMarketKey(
            market_type=CanonicalMarketType.TOTALS,
            metric="SHOTS",
            scope="MATCH",
            period="FULL_TIME",
            line=Decimal("30.5"),
        )
        sel_over = CanonicalSelectionKey(market_key=shots_market, selection_type="OVER")
        sel_under = CanonicalSelectionKey(market_key=shots_market, selection_type="UNDER")

        comparisons = [
            OddsComparison(
                canonical_event_id=self.event_id,
                canonical_market_key=shots_market,
                canonical_selection_key=sel_over,
                source_provider="superbet",
                target_provider="betclic",
                source_event_id="p_ev_superbet",
                target_event_id="p_ev_betclic",
                source_internal_event_id="int_ev_src",
                target_internal_event_id="int_ev_tgt",
                source_market_id="p_mkt_superbet",
                target_market_id="p_mkt_betclic",
                source_selection_id="sel_superbet_over",
                target_selection_id="sel_betclic_over",
                status=OddsComparisonStatus.VALID,
                source_odds=Decimal("1.50"),
                target_odds=Decimal("2.80"),
                evidence={"source_validation_status": "VALID", "target_validation_status": "VALID"},
            ),
            OddsComparison(
                canonical_event_id=self.event_id,
                canonical_market_key=shots_market,
                canonical_selection_key=sel_under,
                source_provider="superbet",
                target_provider="betclic",
                source_event_id="p_ev_superbet",
                target_event_id="p_ev_betclic",
                source_internal_event_id="int_ev_src",
                target_internal_event_id="int_ev_tgt",
                source_market_id="p_mkt_superbet",
                target_market_id="p_mkt_betclic",
                source_selection_id="sel_superbet_under",
                target_selection_id="sel_betclic_under",
                status=OddsComparisonStatus.VALID,
                source_odds=Decimal("1.88"),
                target_odds=Decimal("1.40"),
                evidence={"source_validation_status": "VALID", "target_validation_status": "VALID"},
            ),
        ]

        result = self.detector.detect(comparisons)
        self.assertEqual(len(result.opportunities), 1)
        opp = result.opportunities[0]

        from api.services import serialize_opportunity_summary
        summary = serialize_opportunity_summary(opp)
        detail = serialize_opportunity_detail(opp)

        # Problem 1 Verification: Single source of truth for margin (+3.99%)
        self.assertAlmostEqual(summary["margin_pct"], 3.99, places=2)
        self.assertAlmostEqual(summary["arbitrage_margin_pct"], 3.99, places=2)
        self.assertAlmostEqual(summary["calculation"]["roi"], 3.99, places=2)
        self.assertTrue(summary["calculation"]["is_surebet"])
        self.assertAlmostEqual(summary["calculation"]["probability_sum"], 0.96159, places=4)

        self.assertAlmostEqual(detail["margin_pct"], 3.99, places=2)
        self.assertAlmostEqual(detail["mathematical_explanation"]["net_margin_pct"], 3.99, places=2)
        self.assertAlmostEqual(detail["stake_calculator"]["roi_percentage"], 3.99, places=2)
        self.assertTrue(detail["mathematical_explanation"]["is_surebet"])
        self.assertTrue(detail["stake_calculator"]["is_surebet"])

        # Check gross vs net exposed in detail
        self.assertAlmostEqual(detail["gross_margin_pct"], 12.48, delta=0.05)

        # Problem 2 Verification: Human-readable market labels
        self.assertEqual(summary["market_label"], "Total Shots • 30.5")
        self.assertEqual(summary["market"]["display_name"], "Total Shots")
        self.assertEqual(summary["market"]["line_display"], "30.5")

        self.assertEqual(detail["market_label"], "Total Shots • 30.5")
        self.assertEqual(detail["market"]["display_name"], "Total Shots")

    def test_stage_24d_market_label_formatting(self):
        """Validates market label formatting across different market families."""
        from normalization.market_identity import format_canonical_market_label
        self.assertEqual(format_canonical_market_label("football:TOTALS:GOALS:MATCH:all:FULL_TIME:2.5"), "Total Goals • 2.5")
        self.assertEqual(format_canonical_market_label("football:BTTS:GOALS:MATCH:all:FULL_TIME:none"), "Both Teams to Score")
        self.assertEqual(format_canonical_market_label("football:1X2:GOALS:MATCH:all:FULL_TIME:none"), "Match Result")
        self.assertEqual(format_canonical_market_label("football:DOUBLE_CHANCE:GOALS:MATCH:all:FULL_TIME:none"), "Double Chance")
        self.assertEqual(format_canonical_market_label("football:DRAW_NO_BET:GOALS:MATCH:all:FULL_TIME:none"), "Draw No Bet")
        self.assertEqual(format_canonical_market_label("football:TOTALS:SHOTS:TEAM:away:FULL_TIME:10.5"), "Total Shots — Away • 10.5")


if __name__ == '__main__':
    unittest.main()
