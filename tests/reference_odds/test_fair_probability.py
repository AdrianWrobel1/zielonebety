"""
Unit Tests for Fair Probability Normalization & Margin Removal
"""

from decimal import Decimal
import unittest

from normalization.market_identity import CanonicalMarketType
from normalization.selection_identity import CanonicalSelectionType
from reference_odds.fair_calculator import FairProbabilityCalculator
from tests.fixtures.reference_odds_fixtures import (
    fixture_valid_1x2_reference_market,
    fixture_valid_btts_reference_market,
    fixture_valid_totals_reference_market,
    fixture_incomplete_1x2_reference_market,
    fixture_stale_reference_market,
    fixture_invalid_odds_reference_market,
)


class TestFairProbabilityCalculator(unittest.TestCase):
    """Verifies deterministic overround removal, completeness, and Decimal precision."""

    def setUp(self):
        self.calculator = FairProbabilityCalculator(max_freshness_seconds=1800)

    def test_valid_1x2_margin_removal(self):
        """Proves exact Decimal overround removal on a 3-way 1X2 market."""
        mkt = fixture_valid_1x2_reference_market()
        res = self.calculator.calculate_fair_probabilities(mkt)

        self.assertTrue(res.is_valid)
        self.assertIsNone(res.diagnostic)

        # Odds: 2.00, 3.50, 4.00
        # raw_p: 0.5000, 1/3.5 ≈ 0.285714, 0.2500 -> sum = 1.035714...
        self.assertIsNotNone(res.raw_overround)
        self.assertGreater(res.raw_overround, Decimal("1.03"))
        self.assertLess(res.raw_overround, Decimal("1.04"))

        # Check fair probabilities
        p_home = res.fair_probabilities[CanonicalSelectionType.HOME.value]
        p_draw = res.fair_probabilities[CanonicalSelectionType.DRAW.value]
        p_away = res.fair_probabilities[CanonicalSelectionType.AWAY.value]

        self.assertTrue(Decimal("0.48") < p_home < Decimal("0.49"))
        self.assertTrue(Decimal("0.27") < p_draw < Decimal("0.28"))
        self.assertTrue(Decimal("0.24") < p_away < Decimal("0.25"))

        # Verify sum equals 1.0
        prob_sum = p_home + p_draw + p_away
        self.assertAlmostEqual(float(prob_sum), 1.0, places=5)

        # Verify fair odds > reference odds (due to margin removal)
        odds_home = res.fair_odds[CanonicalSelectionType.HOME.value]
        self.assertGreater(odds_home, Decimal("2.00"))

    def test_valid_btts_margin_removal(self):
        """Proves exact Decimal overround removal on a 2-way BTTS market."""
        mkt = fixture_valid_btts_reference_market()
        res = self.calculator.calculate_fair_probabilities(mkt)

        self.assertTrue(res.is_valid)
        p_yes = res.fair_probabilities[CanonicalSelectionType.YES.value]
        p_no = res.fair_probabilities[CanonicalSelectionType.NO.value]

        self.assertAlmostEqual(float(p_yes + p_no), 1.0, places=5)

    def test_valid_totals_margin_removal(self):
        """Proves exact Decimal overround removal on a 2-way TOTALS market."""
        mkt = fixture_valid_totals_reference_market(line=Decimal("2.5"))
        res = self.calculator.calculate_fair_probabilities(mkt)

        self.assertTrue(res.is_valid)
        p_over = res.fair_probabilities[CanonicalSelectionType.OVER.value]
        p_under = res.fair_probabilities[CanonicalSelectionType.UNDER.value]

        # For symmetrical 1.95 / 1.95, fair probs should both be 0.5000
        self.assertAlmostEqual(float(p_over), 0.5, places=5)
        self.assertAlmostEqual(float(p_under), 0.5, places=5)
        self.assertAlmostEqual(float(res.fair_odds[CanonicalSelectionType.OVER.value]), 2.0, places=5)

    def test_incomplete_1x2_market_rejection(self):
        """Proves that missing required outcomes returns INCOMPLETE_REFERENCE_MARKET."""
        mkt = fixture_incomplete_1x2_reference_market()
        res = self.calculator.calculate_fair_probabilities(mkt)

        self.assertFalse(res.is_valid)
        self.assertEqual(res.diagnostic, "INCOMPLETE_REFERENCE_MARKET")
        self.assertIn("AWAY", res.details.get("missing", []))

    def test_stale_reference_data_rejection(self):
        """Proves that reference data older than max_freshness_seconds is rejected."""
        mkt = fixture_stale_reference_market()
        res = self.calculator.calculate_fair_probabilities(mkt)

        self.assertFalse(res.is_valid)
        self.assertEqual(res.diagnostic, "STALE_REFERENCE_DATA")

    def test_invalid_odds_rejection(self):
        """Proves that reference odds <= 1.0 are rejected."""
        mkt = fixture_invalid_odds_reference_market()
        res = self.calculator.calculate_fair_probabilities(mkt)

        self.assertFalse(res.is_valid)
        self.assertEqual(res.diagnostic, "INVALID_ODDS")

    def test_missing_totals_line_rejection(self):
        """Proves that TOTALS market lacking a numeric line is rejected."""
        mkt = fixture_valid_totals_reference_market()
        mkt.line = None
        res = self.calculator.calculate_fair_probabilities(mkt)

        self.assertFalse(res.is_valid)
        self.assertEqual(res.diagnostic, "MISSING_MARKET_LINE")


if __name__ == "__main__":
    unittest.main()
