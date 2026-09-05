"""
Unit & Integration Tests for Valuebet Detection Engine
"""

from datetime import datetime, timezone
from decimal import Decimal
import unittest

from normalization.market_identity import CanonicalMarketType
from normalization.selection_identity import CanonicalSelectionType
from reference_odds.models import ReferenceMarket, ReferenceSelection
from valuebets.config import ValuebetConfig
from valuebets.engine import ValuebetEngine
from tests.fixtures.reference_odds_fixtures import (
    create_bookmaker_graph,
    create_reference_event,
    fixture_valid_1x2_reference_market,
    fixture_valid_btts_reference_market,
    fixture_valid_totals_reference_market,
)


class TestValuebetEngine(unittest.TestCase):
    """Verifies end-to-end valuebet calculation, thresholding, and metrics."""

    def setUp(self):
        self.config = ValuebetConfig(min_value_percent=Decimal("3.0"))
        self.engine = ValuebetEngine(config=self.config)

    def test_positive_valuebet_detection_1x2(self):
        """Proves exact positive value detection when bookmaker offers higher odds than fair baseline.

        Reference 1X2 Market:
            Pinnacle: Home 2.00, Draw 3.50, Away 4.00
            Overround: ~1.0357
            Fair prob Home: ~0.482758... -> Fair odds: ~2.0714

        Bookmaker (Superbet):
            Home: 2.50 (Bookmaker implies 1/2.50 = 40.00%)
            Gross value: (2.50 * 0.482758) - 1 = +20.69%
            Net (12% tax, eff 2.20): (2.20 * 0.482758) - 1 = +6.21% >= 3% -> qualified
        """
        ref_mkt = fixture_valid_1x2_reference_market()
        ref_ev = create_reference_event(home_team="Arsenal", away_team="Chelsea", markets=[ref_mkt])

        bm_graph = create_bookmaker_graph(
            home_team="Arsenal",
            away_team="Chelsea",
            bookmaker="superbet",
            market_type=CanonicalMarketType.ONE_X_TWO.value,
            selections_odds={"HOME": 2.50, "DRAW": 3.40, "AWAY": 3.60},
        )

        res = self.engine.detect_valuebets([bm_graph], [ref_ev])

        self.assertEqual(res.metrics.events_matched, 1)
        self.assertEqual(res.metrics.markets_matched, 1)
        self.assertGreaterEqual(len(res.candidates), 1)
        self.assertGreaterEqual(len(res.qualified_valuebets), 1)

        val_home = next((c for c in res.qualified_valuebets if c.selection_type == "HOME"), None)
        self.assertIsNotNone(val_home)
        self.assertEqual(val_home.bookmaker, "superbet")
        self.assertEqual(val_home.bookmaker_odds, Decimal("2.50"))
        self.assertTrue(val_home.is_qualified)
        self.assertGreater(val_home.value_percent, Decimal("19.0"))
        self.assertLess(val_home.value_percent, Decimal("22.0"))
        self.assertGreaterEqual(val_home.net_value_percent, Decimal("3.0"))

        # Verify fingerprint structure (stable canonical form, no volatile odds)
        self.assertIn("opp:VALUEBET:", val_home.fingerprint)
        self.assertIn("1X2", val_home.fingerprint)
        self.assertIn("HOME", val_home.fingerprint)

    def test_threshold_filtering(self):
        """Proves that a candidate below min_value_percent (+3.0%) is not marked as qualified."""
        ref_mkt = fixture_valid_1x2_reference_market()
        ref_ev = create_reference_event(home_team="Arsenal", away_team="Chelsea", markets=[ref_mkt])

        # Fair odds for Home is ~2.0714.
        # Bookmaker offering 2.10 yields value = (2.10 * 0.482758) - 1 = +1.38% (below 3.0%)
        bm_graph = create_bookmaker_graph(
            home_team="Arsenal",
            away_team="Chelsea",
            bookmaker="superbet",
            market_type=CanonicalMarketType.ONE_X_TWO.value,
            selections_odds={"HOME": 2.10, "DRAW": 3.20, "AWAY": 3.50},
        )

        res = self.engine.detect_valuebets([bm_graph], [ref_ev])

        # Candidate found because EV > 0
        self.assertEqual(len(res.candidates), 1)
        # But qualified_valuebets should be 0 because 1.38% < 3.0%
        self.assertEqual(len(res.qualified_valuebets), 0)
        self.assertFalse(res.candidates[0].is_qualified)

    def test_positive_totals_valuebet_detection(self):
        """Proves exact Decimal valuebet detection on Totals 2.5 market."""
        ref_mkt = fixture_valid_totals_reference_market(line=Decimal("2.5"))
        ref_ev = create_reference_event(home_team="Real Madrid", away_team="Barcelona", markets=[ref_mkt])

        # Fair odds for Over 2.5 is 2.00 (prob = 0.5000)
        # Bookmaker offers Over 2.5 at 2.25 -> value = 2.25 * 0.5 - 1 = +12.50%
        bm_graph = create_bookmaker_graph(
            home_team="Real Madrid",
            away_team="Barcelona",
            bookmaker="betclic",
            market_type=CanonicalMarketType.TOTALS.value,
            line=2.5,
            selections_odds={"OVER": 2.25, "UNDER": 1.70},
        )

        res = self.engine.detect_valuebets([bm_graph], [ref_ev])

        self.assertEqual(len(res.qualified_valuebets), 1)
        val_over = res.qualified_valuebets[0]
        self.assertEqual(val_over.selection_type, "OVER")
        self.assertEqual(val_over.line, Decimal("2.5"))
        self.assertEqual(val_over.value_percent, Decimal("12.50"))

    def test_zero_and_negative_value_rejection(self):
        """Proves that when bookmaker odds are worse than fair odds, no valuebets are detected."""
        ref_mkt = fixture_valid_1x2_reference_market()
        ref_ev = create_reference_event(home_team="Arsenal", away_team="Chelsea", markets=[ref_mkt])

        # Bookmaker odds: Home 1.85, Draw 3.10, Away 3.40 (all worse than fair odds)
        bm_graph = create_bookmaker_graph(
            home_team="Arsenal",
            away_team="Chelsea",
            bookmaker="superbet",
            market_type=CanonicalMarketType.ONE_X_TWO.value,
            selections_odds={"HOME": 1.85, "DRAW": 3.10, "AWAY": 3.40},
        )

        res = self.engine.detect_valuebets([bm_graph], [ref_ev])
        self.assertEqual(len(res.candidates), 0)
        self.assertEqual(len(res.qualified_valuebets), 0)

    def test_positive_btts_valuebet_detection(self):
        """Proves valuebet detection on Both Teams To Score (BTTS) market."""
        ref_mkt = fixture_valid_btts_reference_market()
        ref_ev = create_reference_event(home_team="Bayern Munich", away_team="Dortmund", markets=[ref_mkt])

        # Reference: Yes 1.80, No 2.10. Overround: 1/1.8 + 1/2.1 = 0.5556 + 0.4762 = 1.0317
        # Fair prob Yes: 0.5556 / 1.0317 = 0.5385 -> Fair odds: 1.857
        # Bookmaker offers Yes at 2.30 -> gross = 2.30 * 0.5385 - 1 = +23.85%
        # Net (12% tax, eff 2.024): +8.98% >= 3% -> qualified (P0-NEW-001)
        bm_graph = create_bookmaker_graph(
            home_team="Bayern Munich",
            away_team="Dortmund",
            bookmaker="superbet",
            market_type=CanonicalMarketType.BTTS.value,
            selections_odds={"YES": 2.30, "NO": 1.70},
        )

        res = self.engine.detect_valuebets([bm_graph], [ref_ev])
        self.assertEqual(len(res.qualified_valuebets), 1)
        val_yes = res.qualified_valuebets[0]
        self.assertEqual(val_yes.selection_type, "YES")
        self.assertGreater(val_yes.value_percent, Decimal("10.0"))

    def test_positive_draw_no_bet_valuebet_detection(self):
        """Proves valuebet detection on Draw No Bet market."""
        ref_mkt = ReferenceMarket(
            market_type=CanonicalMarketType.DRAW_NO_BET.value,
            selections={
                CanonicalSelectionType.HOME.value: ReferenceSelection(
                    selection_type=CanonicalSelectionType.HOME.value,
                    odds=Decimal("1.80"),
                ),
                CanonicalSelectionType.AWAY.value: ReferenceSelection(
                    selection_type=CanonicalSelectionType.AWAY.value,
                    odds=Decimal("2.10"),
                ),
            },
            timestamp=datetime.now(timezone.utc).isoformat(),
            bookmaker_name="pinnacle",
        )
        ref_ev = create_reference_event(home_team="Juventus", away_team="Milan", markets=[ref_mkt])

        bm_graph = create_bookmaker_graph(
            home_team="Juventus",
            away_team="Milan",
            bookmaker="superbet",
            market_type=CanonicalMarketType.DRAW_NO_BET.value,
            selections_odds={"HOME": 2.30, "AWAY": 1.70},
        )

        res = self.engine.detect_valuebets([bm_graph], [ref_ev])
        self.assertEqual(len(res.qualified_valuebets), 1)
        self.assertEqual(res.qualified_valuebets[0].market_type, "DRAW_NO_BET")
        self.assertEqual(res.qualified_valuebets[0].selection_type, "HOME")

    def test_unsupported_non_mutually_exclusive_market_rejection(self):
        """Proves that non-mutually exclusive markets without proven 1-winner partition are rejected."""
        ref_mkt = ReferenceMarket(
            market_type="CORRECT_SCORE",
            selections={
                "1-0": ReferenceSelection(selection_type="1-0", odds=Decimal("7.50")),
                "2-0": ReferenceSelection(selection_type="2-0", odds=Decimal("9.00")),
            },
            bookmaker_name="pinnacle",
        )
        res = self.engine.fair_calculator.calculate_fair_probabilities(ref_mkt)
        self.assertFalse(res.is_valid)
        self.assertEqual(res.diagnostic, "UNSUPPORTED_MARKET_TYPE")

    def test_positive_handicap_valuebet_detection(self):
        """Proves valuebet detection on Handicap market."""
        ref_mkt = ReferenceMarket(
            market_type=CanonicalMarketType.HANDICAP.value,
            line=Decimal("1.5"),
            selections={
                CanonicalSelectionType.HOME.value: ReferenceSelection(
                    selection_type=CanonicalSelectionType.HOME.value,
                    odds=Decimal("1.95"),
                    line=Decimal("-1.5"),
                    participant_role="HOME",
                ),
                CanonicalSelectionType.AWAY.value: ReferenceSelection(
                    selection_type=CanonicalSelectionType.AWAY.value,
                    odds=Decimal("1.95"),
                    line=Decimal("1.5"),
                    participant_role="AWAY",
                ),
            },
            bookmaker_name="pinnacle",
        )
        ref_ev = create_reference_event(home_team="Man City", away_team="Fulham", markets=[ref_mkt])

        bm_graph = create_bookmaker_graph(
            home_team="Man City",
            away_team="Fulham",
            bookmaker="superbet",
            market_type=CanonicalMarketType.HANDICAP.value,
            line=1.5,
            selections_odds={"HOME": 2.50, "AWAY": 1.70},
        )

        res = self.engine.detect_valuebets([bm_graph], [ref_ev])
        self.assertEqual(len(res.qualified_valuebets), 1)
        val_hd = res.qualified_valuebets[0]
        self.assertEqual(val_hd.market_type, "HANDICAP")
        self.assertEqual(val_hd.selection_type, "HOME")
        self.assertEqual(val_hd.value_percent, Decimal("25.00"))
        self.assertEqual(val_hd.net_value_percent, Decimal("10.00"))


if __name__ == "__main__":
    unittest.main()

