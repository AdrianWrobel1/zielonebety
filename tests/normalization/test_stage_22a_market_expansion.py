"""
Tests for Stage 22A: Surebet Market Type Expansion (HANDICAP & DOUBLE_CHANCE).

Covers:
1. Unit tests for valid HANDICAP 2-way evaluation (both surebet and no-surebet).
2. Unit tests for incomplete HANDICAP rejection (missing HOME or missing AWAY).
3. Unit tests ensuring different handicap lines cannot be combined.
4. Unit tests for valid DOUBLE_CHANCE 3-way evaluation (both surebet and no-surebet).
5. Unit tests for incomplete DOUBLE_CHANCE rejection (missing one or two of the 3 outcomes).
6. Regression tests proving existing 1X2 / BTTS / TOTALS behavior is unchanged.
7. Verification that UNSUPPORTED_MARKET is no longer emitted for valid HANDICAP / DOUBLE_CHANCE markets.
"""

from decimal import Decimal
import unittest

from normalization.market_identity import (
    CanonicalMarketKey,
    CanonicalMarketType,
    MarketPeriod,
    MarketScope,
)
from normalization.odds_comparison import (
    OddsComparison,
    OddsComparisonStatus,
)
from normalization.selection_identity import (
    CanonicalSelectionKey,
    CanonicalSelectionType,
)
from core.tax_engine import TaxEngine
from normalization.surebet import (
    MarketCompletenessStatus,
    SurebetDetectorEngine,
    SurebetStatus,
)


class TestStage22ASurebetMarketExpansion(unittest.TestCase):
    def setUp(self):
        tax_engine = TaxEngine()
        tax_engine.update_config("superbet", tax_enabled=False, tax_rate=0.0)
        tax_engine.update_config("betclic", tax_enabled=False, tax_rate=0.0)
        self.engine = SurebetDetectorEngine(tax_engine=tax_engine)

    def _create_comparison(
        self,
        selection_type: str,
        market_key: CanonicalMarketKey,
        source_odds: float,
        target_odds: float,
        canonical_event_id: str = "cev_stage22a_test",
        source_provider: str = "superbet",
        target_provider: str = "betclic",
        status: OddsComparisonStatus = OddsComparisonStatus.VALID,
        source_status: str = "VALID",
        target_status: str = "VALID",
        participant_role: str = None,
        selection_line: Decimal = None,
    ) -> OddsComparison:
        sel_key = CanonicalSelectionKey(
            market_key=market_key,
            selection_type=selection_type,
            participant_role=participant_role,
            selection_line=selection_line if selection_line is not None else market_key.line,
        )
        return OddsComparison(
            canonical_event_id=canonical_event_id,
            canonical_market_key=market_key,
            canonical_selection_key=sel_key,
            source_provider=source_provider,
            target_provider=target_provider,
            source_event_id="ev_sb_1",
            target_event_id="ev_bc_1",
            source_internal_event_id="int_ev_sb_1",
            target_internal_event_id="int_ev_bc_1",
            source_market_id="mkt_sb_1",
            target_market_id="mkt_bc_1",
            source_selection_id=f"sel_sb_{selection_type}",
            target_selection_id=f"sel_bc_{selection_type}",
            status=status,
            source_odds=Decimal(str(source_odds)) if source_odds else None,
            target_odds=Decimal(str(target_odds)) if target_odds else None,
            evidence={
                "source_validation_status": source_status,
                "target_validation_status": target_status,
            },
        )

    # -------------------------------------------------------------------------
    # 1. HANDICAP 2-Way Evaluation
    # -------------------------------------------------------------------------
    def test_valid_handicap_positive_surebet(self):
        """Test a mathematically valid 2-outcome HANDICAP surebet (S < 1.0)."""
        # Line -1.5 / +1.5: Superbet Home -1.5 @ 2.10, Betclic Away +1.5 @ 2.10
        # S = 1/2.10 + 1/2.10 = 0.47619 + 0.47619 = 0.95238 < 1.0 (margin = +5.0%)
        hc_mkt = CanonicalMarketKey(
            market_type=CanonicalMarketType.HANDICAP,
            line=Decimal("-1.5"),
        )
        comps = [
            self._create_comparison("HOME", market_key=hc_mkt, source_odds=2.10, target_odds=1.80, participant_role="HOME"),
            self._create_comparison("AWAY", market_key=hc_mkt, source_odds=1.80, target_odds=2.10, participant_role="AWAY"),
        ]
        res = self.engine.detect(comps)

        self.assertEqual(res.metrics.surebet_count, 1)
        self.assertEqual(res.metrics.supported_market_count, 1)
        self.assertEqual(res.metrics.unsupported_market_count, 0)
        self.assertEqual(res.metrics.complete_market_count, 1)

        opp = res.opportunities[0]
        self.assertEqual(opp.canonical_market_key.market_type, "HANDICAP")
        self.assertEqual(opp.canonical_market_key.line, Decimal("-1.5"))
        self.assertEqual(len(opp.legs), 2)
        self.assertTrue(opp.is_mixed_bookmakers)
        self.assertEqual(set(opp.bookmakers), {"superbet", "betclic"})
        self.assertAlmostEqual(float(opp.arbitrage_margin), 0.05, places=3)

    def test_valid_handicap_no_surebet(self):
        """Test a complete HANDICAP market that is not a surebet (S > 1.0)."""
        # Odds: Superbet Home @ 1.90, Betclic Away @ 1.90 -> S = 1/1.9 + 1/1.9 = 1.0526 > 1.0
        hc_mkt = CanonicalMarketKey(
            market_type=CanonicalMarketType.HANDICAP,
            line=Decimal("1.0"),
        )
        comps = [
            self._create_comparison("HOME", market_key=hc_mkt, source_odds=1.90, target_odds=1.85, participant_role="HOME"),
            self._create_comparison("AWAY", market_key=hc_mkt, source_odds=1.85, target_odds=1.90, participant_role="AWAY"),
        ]
        res = self.engine.detect(comps)

        self.assertEqual(res.metrics.surebet_count, 0)
        self.assertEqual(res.metrics.no_surebet_count, 1)
        self.assertEqual(res.metrics.complete_market_count, 1)
        self.assertEqual(res.metrics.unsupported_market_count, 0)

    # -------------------------------------------------------------------------
    # 2. Incomplete HANDICAP Rejection
    # -------------------------------------------------------------------------
    def test_incomplete_handicap_missing_away(self):
        """Test that HANDICAP with missing AWAY outcome is rejected as INCOMPLETE_MARKET."""
        hc_mkt = CanonicalMarketKey(
            market_type=CanonicalMarketType.HANDICAP,
            line=Decimal("-0.5"),
        )
        comps = [
            self._create_comparison("HOME", market_key=hc_mkt, source_odds=2.10, target_odds=2.05, participant_role="HOME"),
        ]
        res = self.engine.detect(comps)

        self.assertEqual(res.metrics.surebet_count, 0)
        self.assertEqual(res.metrics.incomplete_market_count, 1)
        self.assertEqual(res.incomplete_evaluations[0].status, SurebetStatus.INCOMPLETE_MARKET)
        self.assertIn("AWAY", res.incomplete_evaluations[0].missing_selection_types)

    def test_incomplete_handicap_missing_home(self):
        """Test that HANDICAP with missing HOME outcome is rejected as INCOMPLETE_MARKET."""
        hc_mkt = CanonicalMarketKey(
            market_type=CanonicalMarketType.HANDICAP,
            line=Decimal("-0.5"),
        )
        comps = [
            self._create_comparison("AWAY", market_key=hc_mkt, source_odds=2.10, target_odds=2.05, participant_role="AWAY"),
        ]
        res = self.engine.detect(comps)

        self.assertEqual(res.metrics.surebet_count, 0)
        self.assertEqual(res.metrics.incomplete_market_count, 1)
        self.assertEqual(res.incomplete_evaluations[0].status, SurebetStatus.INCOMPLETE_MARKET)
        self.assertIn("HOME", res.incomplete_evaluations[0].missing_selection_types)

    # -------------------------------------------------------------------------
    # 3. Line Isolation: Different Handicap Lines Cannot Combine
    # -------------------------------------------------------------------------
    def test_different_handicap_lines_never_combined(self):
        """Test that HOME on line -1.5 and AWAY on line +2.5 are isolated into distinct incomplete markets."""
        mkt_line1 = CanonicalMarketKey(market_type=CanonicalMarketType.HANDICAP, line=Decimal("-1.5"))
        mkt_line2 = CanonicalMarketKey(market_type=CanonicalMarketType.HANDICAP, line=Decimal("2.5"))

        comps = [
            self._create_comparison("HOME", market_key=mkt_line1, source_odds=2.50, target_odds=2.50, participant_role="HOME"),
            self._create_comparison("AWAY", market_key=mkt_line2, source_odds=2.50, target_odds=2.50, participant_role="AWAY"),
        ]
        res = self.engine.detect(comps)

        # Must not form a combined market! Both should be separate incomplete markets.
        self.assertEqual(res.metrics.surebet_count, 0)
        self.assertEqual(res.metrics.incomplete_market_count, 2)
        self.assertEqual(res.metrics.input_market_count, 2)

    def test_handicap_missing_line_is_invalid(self):
        """Test that a HANDICAP market with None line is rejected as INVALID_MARKET."""
        mkt_noline = CanonicalMarketKey(market_type=CanonicalMarketType.HANDICAP, line=None)
        comps = [
            self._create_comparison("HOME", market_key=mkt_noline, source_odds=2.00, target_odds=2.00, participant_role="HOME"),
            self._create_comparison("AWAY", market_key=mkt_noline, source_odds=2.00, target_odds=2.00, participant_role="AWAY"),
        ]
        res = self.engine.detect(comps)

        self.assertEqual(res.metrics.surebet_count, 0)
        self.assertEqual(res.metrics.invalid_market_count, 1)
        self.assertEqual(res.invalid_evaluations[0].status, SurebetStatus.INVALID_MARKET)

    # -------------------------------------------------------------------------
    # 4. DOUBLE_CHANCE 3-Way Evaluation
    # -------------------------------------------------------------------------
    def test_valid_double_chance_positive_surebet(self):
        """Test a mathematically valid 3-outcome DOUBLE_CHANCE surebet (1X, 12, X2)."""
        # Odds: 1X @ 3.20 (Superbet), 12 @ 3.20 (Betclic), X2 @ 3.20 (Superbet)
        # S = 1/3.20 + 1/3.20 + 1/3.20 = 3 * 0.3125 = 0.9375 < 1.0 (margin = +6.67%)
        dc_mkt = CanonicalMarketKey(market_type=CanonicalMarketType.DOUBLE_CHANCE)
        comps = [
            self._create_comparison("HOME_DRAW", market_key=dc_mkt, source_odds=3.20, target_odds=2.80),
            self._create_comparison("HOME_AWAY", market_key=dc_mkt, source_odds=2.80, target_odds=3.20),
            self._create_comparison("DRAW_AWAY", market_key=dc_mkt, source_odds=3.20, target_odds=2.80),
        ]
        res = self.engine.detect(comps)

        self.assertEqual(res.metrics.surebet_count, 1)
        self.assertEqual(res.metrics.supported_market_count, 1)
        self.assertEqual(res.metrics.unsupported_market_count, 0)
        self.assertEqual(res.metrics.complete_market_count, 1)

        opp = res.opportunities[0]
        self.assertEqual(opp.canonical_market_key.market_type, "DOUBLE_CHANCE")
        self.assertEqual(len(opp.legs), 3)
        self.assertTrue(opp.is_mixed_bookmakers)
        self.assertAlmostEqual(float(opp.arbitrage_margin), 0.0667, places=3)

    def test_valid_double_chance_no_surebet(self):
        """Test a complete DOUBLE_CHANCE market that is not a surebet (S > 1.0)."""
        # Standard realistic Double Chance odds: 1X @ 1.30, 12 @ 1.35, X2 @ 1.40
        # S = 1/1.30 + 1/1.35 + 1/1.40 = 0.7692 + 0.7407 + 0.7143 = 2.2242 >> 1.0
        dc_mkt = CanonicalMarketKey(market_type=CanonicalMarketType.DOUBLE_CHANCE)
        comps = [
            self._create_comparison("HOME_DRAW", market_key=dc_mkt, source_odds=1.30, target_odds=1.28),
            self._create_comparison("HOME_AWAY", market_key=dc_mkt, source_odds=1.35, target_odds=1.33),
            self._create_comparison("DRAW_AWAY", market_key=dc_mkt, source_odds=1.40, target_odds=1.38),
        ]
        res = self.engine.detect(comps)

        self.assertEqual(res.metrics.surebet_count, 0)
        self.assertEqual(res.metrics.no_surebet_count, 1)
        self.assertEqual(res.metrics.complete_market_count, 1)
        self.assertEqual(res.metrics.unsupported_market_count, 0)

    # -------------------------------------------------------------------------
    # 5. Incomplete DOUBLE_CHANCE Rejection
    # -------------------------------------------------------------------------
    def test_incomplete_double_chance_missing_one_outcome(self):
        """Test that DOUBLE_CHANCE missing X2 is rejected as INCOMPLETE_MARKET."""
        dc_mkt = CanonicalMarketKey(market_type=CanonicalMarketType.DOUBLE_CHANCE)
        comps = [
            self._create_comparison("HOME_DRAW", market_key=dc_mkt, source_odds=1.30, target_odds=1.28),
            self._create_comparison("HOME_AWAY", market_key=dc_mkt, source_odds=1.35, target_odds=1.33),
        ]
        res = self.engine.detect(comps)

        self.assertEqual(res.metrics.surebet_count, 0)
        self.assertEqual(res.metrics.incomplete_market_count, 1)
        self.assertEqual(res.incomplete_evaluations[0].status, SurebetStatus.INCOMPLETE_MARKET)
        self.assertEqual(res.incomplete_evaluations[0].missing_selection_types, ("DRAW_AWAY",))

    def test_incomplete_double_chance_missing_two_outcomes(self):
        """Test that DOUBLE_CHANCE with only 1X is rejected as INCOMPLETE_MARKET."""
        dc_mkt = CanonicalMarketKey(market_type=CanonicalMarketType.DOUBLE_CHANCE)
        comps = [
            self._create_comparison("HOME_DRAW", market_key=dc_mkt, source_odds=1.30, target_odds=1.28),
        ]
        res = self.engine.detect(comps)

        self.assertEqual(res.metrics.surebet_count, 0)
        self.assertEqual(res.metrics.incomplete_market_count, 1)
        self.assertIn("HOME_AWAY", res.incomplete_evaluations[0].missing_selection_types)
        self.assertIn("DRAW_AWAY", res.incomplete_evaluations[0].missing_selection_types)

    # -------------------------------------------------------------------------
    # 6. Regression Tests: 1X2 / BTTS / TOTALS Behavior Unchanged
    # -------------------------------------------------------------------------
    def test_regression_1x2_positive_surebet(self):
        """Verify 1X2 surebet detection is untouched and continues to work perfectly."""
        mkt_1x2 = CanonicalMarketKey(market_type=CanonicalMarketType.ONE_X_TWO)
        comps = [
            self._create_comparison("HOME", market_key=mkt_1x2, source_odds=3.20, target_odds=2.80),
            self._create_comparison("DRAW", market_key=mkt_1x2, source_odds=3.20, target_odds=3.00),
            self._create_comparison("AWAY", market_key=mkt_1x2, source_odds=2.80, target_odds=3.20),
        ]
        res = self.engine.detect(comps)
        self.assertEqual(res.metrics.surebet_count, 1)
        self.assertEqual(res.opportunities[0].canonical_market_key.market_type, "1X2")

    def test_regression_btts_positive_surebet(self):
        """Verify BTTS surebet detection is untouched."""
        mkt_btts = CanonicalMarketKey(market_type=CanonicalMarketType.BTTS)
        comps = [
            self._create_comparison("YES", market_key=mkt_btts, source_odds=2.10, target_odds=1.90),
            self._create_comparison("NO", market_key=mkt_btts, source_odds=1.90, target_odds=2.10),
        ]
        res = self.engine.detect(comps)
        self.assertEqual(res.metrics.surebet_count, 1)
        self.assertEqual(res.opportunities[0].canonical_market_key.market_type, "BTTS")

    def test_regression_totals_positive_surebet(self):
        """Verify TOTALS surebet detection is untouched."""
        mkt_totals = CanonicalMarketKey(market_type=CanonicalMarketType.TOTALS, line=Decimal("2.5"))
        comps = [
            self._create_comparison("OVER", market_key=mkt_totals, source_odds=2.15, target_odds=1.85),
            self._create_comparison("UNDER", market_key=mkt_totals, source_odds=1.85, target_odds=2.15),
        ]
        res = self.engine.detect(comps)
        self.assertEqual(res.metrics.surebet_count, 1)
        self.assertEqual(res.opportunities[0].canonical_market_key.market_type, "TOTALS")

    # -------------------------------------------------------------------------
    # 7. UNSUPPORTED_MARKET is No Longer Emitted for Valid HANDICAP / DOUBLE_CHANCE
    # -------------------------------------------------------------------------
    def test_unsupported_market_not_emitted_for_handicap_or_double_chance(self):
        """Verify that HANDICAP and DOUBLE_CHANCE are never labeled as UNSUPPORTED_MARKET."""
        hc_mkt = CanonicalMarketKey(market_type=CanonicalMarketType.HANDICAP, line=Decimal("0.0"))
        dc_mkt = CanonicalMarketKey(market_type=CanonicalMarketType.DOUBLE_CHANCE)

        comps = [
            self._create_comparison("HOME", market_key=hc_mkt, source_odds=1.90, target_odds=1.90, participant_role="HOME"),
            self._create_comparison("AWAY", market_key=hc_mkt, source_odds=1.90, target_odds=1.90, participant_role="AWAY"),
            self._create_comparison("HOME_DRAW", market_key=dc_mkt, source_odds=1.30, target_odds=1.30),
            self._create_comparison("HOME_AWAY", market_key=dc_mkt, source_odds=1.35, target_odds=1.35),
            self._create_comparison("DRAW_AWAY", market_key=dc_mkt, source_odds=1.40, target_odds=1.40),
        ]
        res = self.engine.detect(comps)

        self.assertEqual(res.metrics.unsupported_market_count, 0)
        self.assertEqual(len(res.unsupported_evaluations), 0)
        self.assertEqual(res.metrics.supported_market_count, 2)
        self.assertEqual(res.metrics.complete_market_count, 2)


if __name__ == "__main__":
    unittest.main()
