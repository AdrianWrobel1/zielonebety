"""
Adversarial & Boundary Invariant Tests for Valuebet Engine
"""

from decimal import Decimal
import unittest

from normalization.market_identity import CanonicalMarketType
from tests.fixtures.reference_odds_fixtures import (
    create_bookmaker_graph,
    create_reference_event,
    fixture_incomplete_1x2_reference_market,
    fixture_invalid_odds_reference_market,
    fixture_stale_reference_market,
    fixture_valid_1x2_reference_market,
    fixture_valid_totals_reference_market,
)
from valuebets.config import ValuebetConfig
from valuebets.engine import ValuebetEngine


class TestValuebetAdversarial(unittest.TestCase):
    """Verifies robustness against mismatches, inversions, stale data, and false positives."""

    def setUp(self):
        self.config = ValuebetConfig(min_value_percent=Decimal("3.0"))
        self.engine = ValuebetEngine(config=self.config)

    def test_inverted_teams_rejected(self):
        """Proves that when Home and Away teams are inverted, the match is rejected."""
        ref_mkt = fixture_valid_1x2_reference_market()
        # Reference has Arsenal vs Chelsea
        ref_ev = create_reference_event(home_team="Arsenal", away_team="Chelsea", markets=[ref_mkt])

        # Bookmaker has Chelsea vs Arsenal (inverted)
        bm_graph = create_bookmaker_graph(
            home_team="Chelsea",
            away_team="Arsenal",
            bookmaker="superbet",
            market_type=CanonicalMarketType.ONE_X_TWO.value,
            selections_odds={"HOME": 3.80, "DRAW": 3.40, "AWAY": 2.10},
        )

        res = self.engine.detect_valuebets([bm_graph], [ref_ev])

        # Must not match inverted event to prevent cross-selection false positive
        self.assertEqual(res.metrics.events_matched, 0)
        self.assertEqual(len(res.candidates), 0)

    def test_u21_suffix_mismatch_veto(self):
        """Proves that U21 team is never matched to Senior team."""
        ref_mkt = fixture_valid_1x2_reference_market()
        ref_ev = create_reference_event(home_team="Arsenal U21", away_team="Chelsea U21", markets=[ref_mkt])

        # Bookmaker has Senior Arsenal vs Chelsea
        bm_graph = create_bookmaker_graph(
            home_team="Arsenal",
            away_team="Chelsea",
            bookmaker="superbet",
            market_type=CanonicalMarketType.ONE_X_TWO.value,
            selections_odds={"HOME": 2.50, "DRAW": 3.40, "AWAY": 3.00},
        )

        res = self.engine.detect_valuebets([bm_graph], [ref_ev])
        self.assertEqual(res.metrics.events_matched, 0)
        self.assertEqual(len(res.candidates), 0)

    def test_totals_line_mismatch_rejected(self):
        """Proves that Totals 2.5 is never matched to Totals 3.5."""
        # Reference is Totals 3.5
        ref_mkt = fixture_valid_totals_reference_market(line=Decimal("3.5"))
        ref_ev = create_reference_event(home_team="Arsenal", away_team="Chelsea", markets=[ref_mkt])

        # Bookmaker has Totals 2.5
        bm_graph = create_bookmaker_graph(
            home_team="Arsenal",
            away_team="Chelsea",
            bookmaker="superbet",
            market_type=CanonicalMarketType.TOTALS.value,
            line=2.5,
            selections_odds={"OVER": 2.20, "UNDER": 1.70},
        )

        res = self.engine.detect_valuebets([bm_graph], [ref_ev])

        # Event matches, but market line does not match
        self.assertEqual(res.metrics.events_matched, 1)
        self.assertEqual(res.metrics.markets_matched, 0)
        self.assertEqual(len(res.candidates), 0)

    def test_incomplete_reference_market_rejection(self):
        """Proves that incomplete reference market does not produce false valuebet."""
        ref_mkt = fixture_incomplete_1x2_reference_market()
        ref_ev = create_reference_event(home_team="Arsenal", away_team="Chelsea", markets=[ref_mkt])

        bm_graph = create_bookmaker_graph(
            home_team="Arsenal",
            away_team="Chelsea",
            bookmaker="superbet",
            market_type=CanonicalMarketType.ONE_X_TWO.value,
            selections_odds={"HOME": 2.50, "DRAW": 3.40, "AWAY": 3.00},
        )

        res = self.engine.detect_valuebets([bm_graph], [ref_ev])
        self.assertEqual(res.metrics.markets_rejected_incomplete, 1)
        self.assertEqual(len(res.candidates), 0)

    def test_stale_reference_market_rejection(self):
        """Proves that stale reference data is rejected."""
        ref_mkt = fixture_stale_reference_market()
        ref_ev = create_reference_event(home_team="Arsenal", away_team="Chelsea", markets=[ref_mkt])

        bm_graph = create_bookmaker_graph(
            home_team="Arsenal",
            away_team="Chelsea",
            bookmaker="superbet",
            market_type=CanonicalMarketType.ONE_X_TWO.value,
            selections_odds={"HOME": 2.50, "DRAW": 3.40, "AWAY": 3.00},
        )

        res = self.engine.detect_valuebets([bm_graph], [ref_ev])
        self.assertEqual(res.metrics.markets_rejected_stale, 1)
        self.assertEqual(len(res.candidates), 0)

    def test_invalid_odds_rejection(self):
        """Proves that odds <= 1.0 are rejected."""
        ref_mkt = fixture_invalid_odds_reference_market()
        ref_ev = create_reference_event(home_team="Arsenal", away_team="Chelsea", markets=[ref_mkt])

        bm_graph = create_bookmaker_graph(
            home_team="Arsenal",
            away_team="Chelsea",
            bookmaker="superbet",
            market_type=CanonicalMarketType.ONE_X_TWO.value,
            selections_odds={"HOME": 2.50, "DRAW": 3.40, "AWAY": 3.00},
        )

        res = self.engine.detect_valuebets([bm_graph], [ref_ev])
        self.assertEqual(res.metrics.markets_rejected_invalid_odds, 1)
        self.assertEqual(len(res.candidates), 0)


if __name__ == "__main__":
    unittest.main()
