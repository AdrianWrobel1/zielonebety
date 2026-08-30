"""
Stage 5.5: Cross-Bookmaker Surebet Detection Engine Test Suite

Verifies:
1. Controlled mathematical examples (Examples 1-6).
2. Additional supported canonical markets (Totals 2.5, BTTS, DNB, Half Time Result, Odd/Even).
3. Complete adversarial test suite (Adversarial A through T).
4. Mathematical property tests (monotonicity and best-odds optimality).
5. Numerical precision and boundary behavior (S < 1, S == 1, S > 1).
6. Deterministic opportunity identification and lineage preservation.
7. Real fixture audit & diagnostic reporting.
8. End-to-end integration with Stage 5.3 and Stage 5.4.
9. Strict architectural boundary invariants (zero stake allocation, zero EV, zero valuebet).
10. Determinism and performance benchmarking (10, 100, 1000 markets).
"""

import json
import math
import time
import unittest
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional

from domain.models import (
    Competition,
    Event,
    Market,
    MatchEvidence,
    Odds,
    Selection,
)
from normalization.market_identity import (
    CanonicalMarketKey,
    CanonicalMarketType,
    MarketPeriod,
    MarketScope,
)
from normalization.selection_identity import (
    CanonicalSelectionKey,
    CanonicalSelectionType,
)
from normalization.validation_pipeline import (
    ComparableSelectionPair,
    CrossBookmakerValidationPipeline,
    CrossBookmakerValidationResult,
)
from normalization.odds_comparison import (
    OddsComparison,
    OddsComparisonEngine,
    OddsComparisonResult,
    OddsComparisonStatus,
)
from normalization.surebet import (
    MarketCompletenessStatus,
    MarketSurebetEvaluation,
    SurebetDetectionMetrics,
    SurebetDetectionResult,
    SurebetDetectorEngine,
    SurebetLeg,
    SurebetOpportunity,
    SurebetStatus,
)
from normalization.base_normalizer import NormalizedGraph
from providers.superbet.parser.parser import SuperbetParser
from normalization.superbet_normalizer import SuperbetNormalizer
from providers.betclic.parser.parser import BetclicParser
from normalization.betclic_normalizer import BetclicNormalizer


class TestSurebetDetectionEngine(unittest.TestCase):
    """Authoritative test suite for Stage 5.5 Cross-Bookmaker Surebet Detection Engine."""

    def setUp(self):
        self.engine = SurebetDetectorEngine()
        self.odds_engine = OddsComparisonEngine()
        self.event_id = "ev_canonical_001"
        self.market_key_1x2 = CanonicalMarketKey(
            market_type=CanonicalMarketType.ONE_X_TWO,
            scope="MATCH",
            period="FULL_TIME",
        )

    def _create_comparison(
        self,
        selection_type: str,
        source_provider: str = "superbet",
        target_provider: str = "betclic",
        source_odds: Optional[Any] = 2.10,
        target_odds: Optional[Any] = 2.20,
        status: OddsComparisonStatus = OddsComparisonStatus.VALID,
        canonical_event_id: Optional[str] = None,
        market_key: Optional[CanonicalMarketKey] = None,
        source_sel_id: Optional[str] = None,
        target_sel_id: Optional[str] = None,
        source_status: str = "VALID",
        target_status: str = "VALID",
    ) -> OddsComparison:
        """Helper to create a controlled OddsComparison record."""
        ev_id = canonical_event_id or self.event_id
        mkt_k = market_key or self.market_key_1x2
        sel_k = CanonicalSelectionKey(market_key=mkt_k, selection_type=selection_type)

        s_dec = Decimal(str(source_odds)) if source_odds is not None else None
        t_dec = Decimal(str(target_odds)) if target_odds is not None else None

        s_id = source_sel_id or f"sel_{source_provider}_{selection_type.lower()}"
        t_id = target_sel_id or f"sel_{target_provider}_{selection_type.lower()}"

        evidence = {
            "source_provider": source_provider,
            "target_provider": target_provider,
            "source_validation_status": source_status if source_odds is not None else "NO_ODDS",
            "target_validation_status": target_status if target_odds is not None else "NO_ODDS",
        }

        return OddsComparison(
            canonical_event_id=ev_id,
            canonical_market_key=mkt_k,
            canonical_selection_key=sel_k,
            source_provider=source_provider,
            target_provider=target_provider,
            source_event_id=f"p_ev_{source_provider}",
            target_event_id=f"p_ev_{target_provider}",
            source_internal_event_id="int_ev_src",
            target_internal_event_id="int_ev_tgt",
            source_market_id=f"p_mkt_{source_provider}",
            target_market_id=f"p_mkt_{target_provider}",
            source_selection_id=s_id,
            target_selection_id=t_id,
            status=status,
            source_odds=s_dec,
            target_odds=t_dec,
            evidence=evidence,
        )

    # -------------------------------------------------------------------------
    # 1. Controlled Examples (Section 40)
    # -------------------------------------------------------------------------

    def test_example_1_1x2_positive_surebet(self):
        """Example 1: 1X2 positive surebet with best odds across providers accounting for bookmaker tax.

        Superbet (tax 12%): HOME 2.80 -> eff 2.464, DRAW 3.40 -> eff 2.992, AWAY 4.00 -> eff 3.520
        Betclic (tax 0%):   HOME 2.10 -> eff 2.100, DRAW 3.60 -> eff 3.600, AWAY 4.20 -> eff 4.200
        Best:     HOME 2.80 (Superbet @ eff 2.464), DRAW 3.60 (Betclic @ eff 3.600), AWAY 4.20 (Betclic @ eff 4.200)
        S = 1/2.464 + 1/3.600 + 1/4.200 = 0.40584 + 0.27778 + 0.23810 = 0.92171 < 1.0 -> SUREBET
        """
        comparisons = [
            self._create_comparison("HOME", source_odds=2.80, target_odds=2.10),
            self._create_comparison("DRAW", source_odds=3.40, target_odds=3.60),
            self._create_comparison("AWAY", source_odds=4.00, target_odds=4.20),
        ]

        result = self.engine.detect(comparisons)

        self.assertEqual(result.metrics.surebet_count, 1)
        self.assertEqual(len(result.opportunities), 1)

        opp = result.opportunities[0]
        self.assertEqual(opp.status, SurebetStatus.SUREBET)
        self.assertTrue(opp.is_mixed_bookmakers)
        self.assertEqual(opp.bookmakers, ("betclic", "superbet"))

        # Verify selected legs and providers
        legs_by_type = {l.selection_type: l for l in opp.legs}
        self.assertEqual(legs_by_type["HOME"].provider, "superbet")
        self.assertEqual(legs_by_type["HOME"].odds, Decimal("2.80"))
        self.assertEqual(legs_by_type["HOME"].effective_odds, Decimal("2.80") * Decimal("0.88"))
        self.assertEqual(legs_by_type["DRAW"].provider, "betclic")
        self.assertEqual(legs_by_type["DRAW"].odds, Decimal("3.60"))
        self.assertEqual(legs_by_type["DRAW"].effective_odds, Decimal("3.60"))
        self.assertEqual(legs_by_type["AWAY"].provider, "betclic")
        self.assertEqual(legs_by_type["AWAY"].odds, Decimal("4.20"))
        self.assertEqual(legs_by_type["AWAY"].effective_odds, Decimal("4.20"))

        # Exact mathematical verification
        expected_s = Decimal("1.0") / (Decimal("2.80") * Decimal("0.88")) + Decimal("1.0") / Decimal("3.60") + Decimal("1.0") / Decimal("4.20")
        self.assertEqual(opp.implied_probability_sum, expected_s)
        self.assertLess(opp.implied_probability_sum, Decimal("1.0"))
        self.assertGreater(opp.arbitrage_margin, Decimal("0.0"))

    def test_example_2_1x2_no_surebet(self):
        """Example 2: 1X2 market where S > 1.0 produces NO_SUREBET.

        HOME = 1.80, DRAW = 3.40, AWAY = 4.20
        S = 1/1.80 + 1/3.40 + 1/4.20 = 1.087768... > 1.0 -> NO_SUREBET
        """
        comparisons = [
            self._create_comparison("HOME", source_odds=1.80, target_odds=1.75),
            self._create_comparison("DRAW", source_odds=3.30, target_odds=3.40),
            self._create_comparison("AWAY", source_odds=4.20, target_odds=4.10),
        ]

        result = self.engine.detect(comparisons)

        self.assertEqual(result.metrics.surebet_count, 0)
        self.assertEqual(result.metrics.no_surebet_count, 1)
        self.assertEqual(len(result.opportunities), 0)
        self.assertEqual(len(result.no_surebet_evaluations), 1)

        eval_rec = result.no_surebet_evaluations[0]
        self.assertEqual(eval_rec.status, SurebetStatus.NO_SUREBET)
        self.assertEqual(eval_rec.completeness_status, MarketCompletenessStatus.COMPLETE)
        self.assertGreater(eval_rec.implied_probability_sum, Decimal("1.0"))
        self.assertLess(eval_rec.arbitrage_margin, Decimal("0.0"))

    def test_example_3_missing_outcome_incomplete_market(self):
        """Example 3: Missing required outcome (DRAW) yields INCOMPLETE_MARKET, never a surebet."""
        comparisons = [
            self._create_comparison("HOME", source_odds=2.20, target_odds=2.10),
            self._create_comparison("AWAY", source_odds=4.00, target_odds=4.20),
        ]

        result = self.engine.detect(comparisons)

        self.assertEqual(result.metrics.surebet_count, 0)
        self.assertEqual(result.metrics.incomplete_market_count, 1)
        self.assertEqual(len(result.opportunities), 0)

        eval_rec = result.incomplete_evaluations[0]
        self.assertEqual(eval_rec.status, SurebetStatus.INCOMPLETE_MARKET)
        self.assertEqual(eval_rec.completeness_status, MarketCompletenessStatus.INCOMPLETE)
        self.assertIn("DRAW", eval_rec.missing_selection_types)

    def test_example_4_best_odds_all_from_single_bookmaker(self):
        """Example 4: Bookmaker B has best odds for all 3 outcomes.

        A: HOME 2.00, DRAW 3.20, AWAY 4.00
        B: HOME 2.20, DRAW 3.50, AWAY 4.10
        All selected legs must come from Bookmaker B.
        """
        comparisons = [
            self._create_comparison("HOME", source_provider="A", target_provider="B", source_odds=2.00, target_odds=2.20),
            self._create_comparison("DRAW", source_provider="A", target_provider="B", source_odds=3.20, target_odds=3.50),
            self._create_comparison("AWAY", source_provider="A", target_provider="B", source_odds=4.00, target_odds=4.10),
        ]

        result = self.engine.detect(comparisons)

        eval_rec = result.evaluations[0]
        for leg in eval_rec.best_legs:
            self.assertEqual(leg.provider, "B")

    def test_example_5_mixed_bookmakers_surebet(self):
        """Example 5: HOME -> Superbet 2.70 (eff 2.376), DRAW -> Betclic 3.80 (eff 3.80), AWAY -> Superbet 4.80 (eff 4.224) -> SUREBET."""
        comparisons = [
            self._create_comparison("HOME", source_provider="superbet", target_provider="betclic", source_odds=2.70, target_odds=2.10),
            self._create_comparison("DRAW", source_provider="superbet", target_provider="betclic", source_odds=3.40, target_odds=3.80),
            self._create_comparison("AWAY", source_provider="superbet", target_provider="betclic", source_odds=4.80, target_odds=3.90),
        ]

        result = self.engine.detect(comparisons)
        self.assertEqual(result.metrics.surebet_count, 1)
        opp = result.opportunities[0]
        self.assertTrue(opp.is_mixed_bookmakers)
        self.assertEqual(opp.bookmakers, ("betclic", "superbet"))

    def test_example_6_exact_equal_odds_boundary_s_equals_1(self):
        """Example 6: Exact boundary S == 1.0 (HOME 2.00, DRAW 3.00, AWAY 6.00).

        1/2.00 + 1/3.00 + 1/6.00 = 0.5 + 0.333333... + 0.166666... = 1.0
        Must result in NO_SUREBET with arbitrage_margin == 0.0.
        """
        comparisons = [
            self._create_comparison("HOME", source_odds=2.00, target_odds=2.00),
            self._create_comparison("DRAW", source_odds=3.00, target_odds=3.00),
            self._create_comparison("AWAY", source_odds=6.00, target_odds=6.00),
        ]

        result = self.engine.detect(comparisons)

        self.assertEqual(result.metrics.surebet_count, 0)
        self.assertEqual(result.metrics.no_surebet_count, 1)
        eval_rec = result.no_surebet_evaluations[0]
        self.assertEqual(eval_rec.status, SurebetStatus.NO_SUREBET)
        self.assertEqual(eval_rec.implied_probability_sum, Decimal("1.0"))
        self.assertEqual(eval_rec.arbitrage_margin, Decimal("0.0"))

    # -------------------------------------------------------------------------
    # 2. Additional Supported Markets (Section 41, 42, 43)
    # -------------------------------------------------------------------------

    def test_totals_market_surebet_and_no_surebet(self):
        """Totals 2.5 OVER 2.20, UNDER 2.20 -> SUREBET; OVER 1.90, UNDER 1.90 -> NO_SUREBET."""
        totals_mkt = CanonicalMarketKey(
            market_type=CanonicalMarketType.TOTALS,
            line=Decimal("2.5"),
            scope="MATCH",
            period="FULL_TIME",
        )

        # 1. Positive Totals Surebet
        res_positive = self.engine.detect([
            self._create_comparison("OVER", market_key=totals_mkt, source_odds=2.20, target_odds=2.15),
            self._create_comparison("UNDER", market_key=totals_mkt, source_odds=2.10, target_odds=2.20),
        ])
        self.assertEqual(res_positive.metrics.surebet_count, 1)
        opp = res_positive.opportunities[0]
        self.assertLess(opp.implied_probability_sum, Decimal("1.0"))

        # 2. Negative Totals No Surebet
        res_negative = self.engine.detect([
            self._create_comparison("OVER", market_key=totals_mkt, source_odds=1.90, target_odds=1.85),
            self._create_comparison("UNDER", market_key=totals_mkt, source_odds=1.85, target_odds=1.90),
        ])
        self.assertEqual(res_negative.metrics.surebet_count, 0)
        self.assertEqual(res_negative.metrics.no_surebet_count, 1)

    def test_btts_market_surebet(self):
        """BTTS YES 2.10, NO 2.10 -> SUREBET (2/2.10 < 1.0)."""
        btts_mkt = CanonicalMarketKey(
            market_type=CanonicalMarketType.BTTS,
            scope="MATCH",
            period="FULL_TIME",
        )
        res = self.engine.detect([
            self._create_comparison("YES", market_key=btts_mkt, source_odds=2.10, target_odds=2.00),
            self._create_comparison("NO", market_key=btts_mkt, source_odds=2.00, target_odds=2.10),
        ])
        self.assertEqual(res.metrics.surebet_count, 1)
        self.assertEqual(res.opportunities[0].canonical_market_key.market_type, CanonicalMarketType.BTTS.value)

    def test_draw_no_bet_market_surebet(self):
        """DNB HOME 2.20, AWAY 2.20 -> SUREBET."""
        dnb_mkt = CanonicalMarketKey(
            market_type=CanonicalMarketType.DRAW_NO_BET,
            scope="MATCH",
            period="FULL_TIME",
        )
        res = self.engine.detect([
            self._create_comparison("HOME", market_key=dnb_mkt, source_odds=2.20, target_odds=2.10),
            self._create_comparison("AWAY", market_key=dnb_mkt, source_odds=2.10, target_odds=2.20),
        ])
        self.assertEqual(res.metrics.surebet_count, 1)

    def test_half_time_result_market_surebet(self):
        """HALF_TIME_RESULT HOME 3.00, DRAW 3.00, AWAY 3.50 -> SUREBET."""
        ht_mkt = CanonicalMarketKey(
            market_type=CanonicalMarketType.HALF_TIME_RESULT,
            scope="MATCH",
            period="FIRST_HALF",
        )
        res = self.engine.detect([
            self._create_comparison("HOME", market_key=ht_mkt, source_odds=3.00, target_odds=2.90),
            self._create_comparison("DRAW", market_key=ht_mkt, source_odds=2.90, target_odds=3.00),
            self._create_comparison("AWAY", market_key=ht_mkt, source_odds=3.20, target_odds=3.50),
        ])
        self.assertEqual(res.metrics.surebet_count, 1)

    def test_odd_even_market_surebet(self):
        """ODD_EVEN ODD 2.10, EVEN 2.10 -> SUREBET."""
        oe_mkt = CanonicalMarketKey(
            market_type=CanonicalMarketType.ODD_EVEN,
            scope="MATCH",
            period="FULL_TIME",
        )
        res = self.engine.detect([
            self._create_comparison("ODD", market_key=oe_mkt, source_odds=2.10, target_odds=2.00),
            self._create_comparison("EVEN", market_key=oe_mkt, source_odds=2.00, target_odds=2.10),
        ])
        self.assertEqual(res.metrics.surebet_count, 1)

    # -------------------------------------------------------------------------
    # 3. Complete Adversarial Test Suite (Section 55: Adversarial A–T)
    # -------------------------------------------------------------------------

    def test_adversarial_a_missing_required_selection(self):
        """Adversarial A: 1X2 missing DRAW -> INCOMPLETE_MARKET."""
        res = self.engine.detect([
            self._create_comparison("HOME", source_odds=2.20, target_odds=2.20),
            self._create_comparison("AWAY", source_odds=4.20, target_odds=4.20),
        ])
        self.assertEqual(res.metrics.incomplete_market_count, 1)
        self.assertEqual(res.incomplete_evaluations[0].status, SurebetStatus.INCOMPLETE_MARKET)

    def test_adversarial_b_unsupported_market_type(self):
        """Adversarial B: UNSUPPORTED market type is classified as UNSUPPORTED_MARKET."""
        unsupported_mkt = CanonicalMarketKey(market_type=CanonicalMarketType.UNSUPPORTED)
        res = self.engine.detect([
            self._create_comparison("HOME", market_key=unsupported_mkt, source_odds=1.50, target_odds=1.55),
            self._create_comparison("AWAY", market_key=unsupported_mkt, source_odds=1.50, target_odds=1.55),
        ])
        self.assertEqual(res.metrics.unsupported_market_count, 1)
        self.assertEqual(res.unsupported_evaluations[0].status, SurebetStatus.UNSUPPORTED_MARKET)

    def test_adversarial_c_invalid_odds_excluded(self):
        """Adversarial C: Invalid odds (<= 1.0 or malformed) are excluded; market becomes incomplete."""
        res = self.engine.detect([
            self._create_comparison("HOME", source_odds=2.20, target_odds=2.10),
            self._create_comparison("DRAW", source_odds=0.50, target_odds=1.00, status=OddsComparisonStatus.INVALID, source_status="INVALID", target_status="INVALID"),
            self._create_comparison("AWAY", source_odds=4.20, target_odds=4.10),
        ])
        self.assertEqual(res.metrics.surebet_count, 0)
        self.assertEqual(res.metrics.incomplete_market_count, 1)

    def test_adversarial_d_inactive_odds_excluded(self):
        """Adversarial D: Inactive / suspended odds are excluded."""
        res = self.engine.detect([
            self._create_comparison("HOME", source_odds=2.20, target_odds=2.10),
            self._create_comparison("DRAW", source_odds=3.60, target_odds=3.60, status=OddsComparisonStatus.INACTIVE, source_status="INACTIVE", target_status="INACTIVE"),
            self._create_comparison("AWAY", source_odds=4.20, target_odds=4.10),
        ])
        self.assertEqual(res.metrics.surebet_count, 0)
        self.assertEqual(res.metrics.incomplete_market_count, 1)

    def test_adversarial_e_different_canonical_markets_never_combined(self):
        """Adversarial E: Selections from different markets (1X2 HOME + TOTALS OVER) are never combined."""
        mkt_1x2 = CanonicalMarketKey(market_type=CanonicalMarketType.ONE_X_TWO)
        mkt_totals = CanonicalMarketKey(market_type=CanonicalMarketType.TOTALS, line=Decimal("2.5"))

        res = self.engine.detect([
            self._create_comparison("HOME", market_key=mkt_1x2, source_odds=2.50, target_odds=2.50),
            self._create_comparison("OVER", market_key=mkt_totals, source_odds=2.50, target_odds=2.50),
        ])
        self.assertEqual(res.metrics.surebet_count, 0)
        self.assertEqual(res.metrics.incomplete_market_count, 2)

    def test_adversarial_f_different_lines_never_combined(self):
        """Adversarial F: TOTALS 2.5 OVER and TOTALS 3.5 UNDER are never combined into one market."""
        mkt_25 = CanonicalMarketKey(market_type=CanonicalMarketType.TOTALS, line=Decimal("2.5"))
        mkt_35 = CanonicalMarketKey(market_type=CanonicalMarketType.TOTALS, line=Decimal("3.5"))

        res = self.engine.detect([
            self._create_comparison("OVER", market_key=mkt_25, source_odds=2.20, target_odds=2.20),
            self._create_comparison("UNDER", market_key=mkt_35, source_odds=2.20, target_odds=2.20),
        ])
        self.assertEqual(res.metrics.surebet_count, 0)
        self.assertEqual(res.metrics.incomplete_market_count, 2)

    def test_adversarial_g_different_periods_never_combined(self):
        """Adversarial G: FULL_TIME HOME and FIRST_HALF DRAW are never combined."""
        mkt_ft = CanonicalMarketKey(market_type=CanonicalMarketType.ONE_X_TWO, period=MarketPeriod.FULL_TIME.value)
        mkt_ht = CanonicalMarketKey(market_type=CanonicalMarketType.ONE_X_TWO, period=MarketPeriod.FIRST_HALF.value)

        res = self.engine.detect([
            self._create_comparison("HOME", market_key=mkt_ft, source_odds=2.50, target_odds=2.50),
            self._create_comparison("DRAW", market_key=mkt_ht, source_odds=3.50, target_odds=3.50),
            self._create_comparison("AWAY", market_key=mkt_ft, source_odds=4.50, target_odds=4.50),
        ])
        self.assertEqual(res.metrics.surebet_count, 0)
        self.assertEqual(res.metrics.incomplete_market_count, 2)

    def test_adversarial_h_different_events_never_combined(self):
        """Adversarial H: Odds across different events are never combined."""
        res = self.engine.detect([
            self._create_comparison("HOME", canonical_event_id="ev_001", source_odds=2.50, target_odds=2.50),
            self._create_comparison("DRAW", canonical_event_id="ev_002", source_odds=3.50, target_odds=3.50),
            self._create_comparison("AWAY", canonical_event_id="ev_001", source_odds=4.50, target_odds=4.50),
        ])
        self.assertEqual(res.metrics.surebet_count, 0)
        self.assertEqual(res.metrics.incomplete_market_count, 2)

    def test_adversarial_i_two_of_three_1x2_outcomes_incomplete(self):
        """Adversarial I: Only 2 of 3 1X2 outcomes available (HOME + DRAW) -> INCOMPLETE_MARKET."""
        res = self.engine.detect([
            self._create_comparison("HOME", source_odds=2.20, target_odds=2.20),
            self._create_comparison("DRAW", source_odds=3.50, target_odds=3.50),
        ])
        self.assertEqual(res.metrics.surebet_count, 0)
        self.assertEqual(res.metrics.incomplete_market_count, 1)

    def test_adversarial_j_best_odds_from_different_bookmakers_combined(self):
        """Adversarial J: Leg 1 from Bookmaker A, Leg 2 from B, Leg 3 from A correctly combined."""
        res = self.engine.detect([
            self._create_comparison("HOME", source_provider="A", target_provider="B", source_odds=2.30, target_odds=2.10),
            self._create_comparison("DRAW", source_provider="A", target_provider="B", source_odds=3.30, target_odds=3.70),
            self._create_comparison("AWAY", source_provider="A", target_provider="B", source_odds=4.30, target_odds=4.00),
        ])
        self.assertEqual(res.metrics.surebet_count, 1)
        opp = res.opportunities[0]
        legs_by_type = {l.selection_type: l for l in opp.legs}
        self.assertEqual(legs_by_type["HOME"].provider, "A")
        self.assertEqual(legs_by_type["DRAW"].provider, "B")
        self.assertEqual(legs_by_type["AWAY"].provider, "A")

    def test_adversarial_k_same_bookmaker_for_all_outcomes(self):
        """Adversarial K: All outcomes from single bookmaker can produce valid surebet if S < 1."""
        res = self.engine.detect([
            self._create_comparison("HOME", source_provider="superbet", target_provider="betclic", source_odds=2.10, target_odds=2.40),
            self._create_comparison("DRAW", source_provider="superbet", target_provider="betclic", source_odds=3.40, target_odds=3.80),
            self._create_comparison("AWAY", source_provider="superbet", target_provider="betclic", source_odds=4.00, target_odds=4.40),
        ])
        self.assertEqual(res.metrics.surebet_count, 1)
        opp = res.opportunities[0]
        self.assertFalse(opp.is_mixed_bookmakers)
        self.assertEqual(opp.bookmakers, ("betclic",))

    def test_adversarial_l_exact_s_equals_1_boundary(self):
        """Adversarial L: Exact S == 1.0 is NOT a surebet."""
        res = self.engine.detect([
            self._create_comparison("HOME", source_odds=2.00, target_odds=2.00),
            self._create_comparison("DRAW", source_odds=4.00, target_odds=4.00),
            self._create_comparison("AWAY", source_odds=4.00, target_odds=4.00),
        ])
        # 1/2 + 1/4 + 1/4 = 1.0
        self.assertEqual(res.metrics.surebet_count, 0)
        self.assertEqual(res.metrics.no_surebet_count, 1)
        self.assertEqual(res.no_surebet_evaluations[0].arbitrage_margin, Decimal("0.0"))

    def test_adversarial_m_s_greater_than_1(self):
        """Adversarial M: S > 1.0 -> NO_SUREBET."""
        res = self.engine.detect([
            self._create_comparison("HOME", source_odds=2.00, target_odds=2.00),
            self._create_comparison("DRAW", source_odds=3.00, target_odds=3.00),
            self._create_comparison("AWAY", source_odds=3.00, target_odds=3.00),
        ])
        self.assertEqual(res.metrics.surebet_count, 0)
        self.assertEqual(res.metrics.no_surebet_count, 1)

    def test_adversarial_n_s_less_than_1(self):
        """Adversarial N: S < 1.0 -> SUREBET."""
        res = self.engine.detect([
            self._create_comparison("HOME", source_odds=2.20, target_odds=2.20),
            self._create_comparison("DRAW", source_odds=3.60, target_odds=3.60),
            self._create_comparison("AWAY", source_odds=4.20, target_odds=4.20),
        ])
        self.assertEqual(res.metrics.surebet_count, 1)

    def test_adversarial_o_very_large_odds_decimal_safe(self):
        """Adversarial O: Very large odds (e.g. 1000.00) are handled safely in Decimal without float underflow."""
        res = self.engine.detect([
            self._create_comparison("HOME", source_odds="1000.00", target_odds="1000.00"),
            self._create_comparison("DRAW", source_odds="1000.00", target_odds="1000.00"),
            self._create_comparison("AWAY", source_odds="1000.00", target_odds="1000.00"),
        ])
        self.assertEqual(res.metrics.surebet_count, 1)
        opp = res.opportunities[0]
        self.assertEqual(opp.implied_probability_sum, Decimal("3.0") / Decimal("1000.00"))

    def test_adversarial_p_very_small_valid_odds(self):
        """Adversarial P: Very small valid odds (e.g. 1.01) calculated correctly."""
        res = self.engine.detect([
            self._create_comparison("HOME", source_odds="1.01", target_odds="1.01"),
            self._create_comparison("DRAW", source_odds="1.01", target_odds="1.01"),
            self._create_comparison("AWAY", source_odds="1.01", target_odds="1.01"),
        ])
        self.assertEqual(res.metrics.surebet_count, 0)
        self.assertEqual(res.metrics.no_surebet_count, 1)

    def test_adversarial_q_duplicate_provider_prices(self):
        """Adversarial Q: Duplicate provider prices resolve deterministically without crash."""
        comps = [
            self._create_comparison("HOME", source_provider="A", target_provider="B", source_odds=2.20, target_odds=2.10),
            self._create_comparison("HOME", source_provider="A", target_provider="B", source_odds=2.20, target_odds=2.10),
            self._create_comparison("DRAW", source_provider="A", target_provider="B", source_odds=3.60, target_odds=3.50),
            self._create_comparison("AWAY", source_provider="A", target_provider="B", source_odds=4.20, target_odds=4.10),
        ]
        res = self.engine.detect(comps)
        self.assertEqual(res.metrics.surebet_count, 1)

    def test_adversarial_r_player_prop_unsupported(self):
        """Adversarial R: PLAYER scope markets default safely to UNSUPPORTED_MARKET."""
        prop_mkt = CanonicalMarketKey(
            market_type=CanonicalMarketType.TOTALS,
            scope=MarketScope.PLAYER.value,
            line=Decimal("1.5"),
        )
        res = self.engine.detect([
            self._create_comparison("OVER", market_key=prop_mkt, source_odds=2.20, target_odds=2.20),
            self._create_comparison("UNDER", market_key=prop_mkt, source_odds=2.20, target_odds=2.20),
        ])
        self.assertEqual(res.metrics.unsupported_market_count, 1)
        self.assertEqual(res.unsupported_evaluations[0].status, SurebetStatus.UNSUPPORTED_MARKET)

    def test_adversarial_s_correct_score_unsupported(self):
        """Adversarial S: CORRECT_SCORE market defaults safely to UNSUPPORTED_MARKET."""
        cs_mkt = CanonicalMarketKey(market_type=CanonicalMarketType.CORRECT_SCORE)
        res = self.engine.detect([
            self._create_comparison("SCORE", market_key=cs_mkt, source_odds=5.00, target_odds=5.00),
        ])
        self.assertEqual(res.metrics.unsupported_market_count, 1)
        self.assertEqual(res.unsupported_evaluations[0].status, SurebetStatus.UNSUPPORTED_MARKET)

    def test_adversarial_t_totals_missing_line_invalid(self):
        """Adversarial T: TOTALS market with line=None is rejected as INVALID_MARKET."""
        invalid_totals_mkt = CanonicalMarketKey(
            market_type=CanonicalMarketType.TOTALS,
            line=None,
        )
        res = self.engine.detect([
            self._create_comparison("OVER", market_key=invalid_totals_mkt, source_odds=2.20, target_odds=2.20),
            self._create_comparison("UNDER", market_key=invalid_totals_mkt, source_odds=2.20, target_odds=2.20),
        ])
        self.assertEqual(res.metrics.invalid_market_count, 1)
        self.assertEqual(res.invalid_evaluations[0].status, SurebetStatus.INVALID_MARKET)

    # -------------------------------------------------------------------------
    # 4. Mathematical Property Tests (Section 56)
    # -------------------------------------------------------------------------

    def test_property_monotonicity_increasing_odds(self):
        """Mathematical Property: Increasing any leg odds cannot destroy an existing surebet."""
        base_comps = [
            self._create_comparison("HOME", source_odds=2.20, target_odds=2.20),
            self._create_comparison("DRAW", source_odds=3.60, target_odds=3.60),
            self._create_comparison("AWAY", source_odds=4.20, target_odds=4.20),
        ]
        res_base = self.engine.detect(base_comps)
        self.assertEqual(res_base.metrics.surebet_count, 1)
        s_base = res_base.opportunities[0].implied_probability_sum

        # Increase AWAY odds to 4.50
        higher_comps = [
            self._create_comparison("HOME", source_odds=2.20, target_odds=2.20),
            self._create_comparison("DRAW", source_odds=3.60, target_odds=3.60),
            self._create_comparison("AWAY", source_odds=4.50, target_odds=4.50),
        ]
        res_higher = self.engine.detect(higher_comps)
        self.assertEqual(res_higher.metrics.surebet_count, 1)
        s_higher = res_higher.opportunities[0].implied_probability_sum

        # S must strictly decrease (profit margin must strictly increase)
        self.assertLess(s_higher, s_base)
        self.assertGreater(res_higher.opportunities[0].arbitrage_margin, res_base.opportunities[0].arbitrage_margin)

    def test_property_best_odds_strictly_minimizes_s(self):
        """Mathematical Property: Selecting max odds per outcome strictly minimizes arbitrage sum S."""
        comparisons = [
            self._create_comparison("HOME", source_provider="A", target_provider="B", source_odds=2.10, target_odds=2.30),
            self._create_comparison("DRAW", source_provider="A", target_provider="B", source_odds=3.60, target_odds=3.40),
            self._create_comparison("AWAY", source_provider="A", target_provider="B", source_odds=4.00, target_odds=4.30),
        ]
        res = self.engine.detect(comparisons)
        opp = res.opportunities[0]

        # S using optimal best odds
        s_optimal = opp.implied_probability_sum

        # S using purely provider A odds: HOME 2.10, DRAW 3.60, AWAY 4.00
        s_pure_a = Decimal("1.0") / Decimal("2.10") + Decimal("1.0") / Decimal("3.60") + Decimal("1.0") / Decimal("4.00")
        # S using purely provider B odds: HOME 2.30, DRAW 3.40, AWAY 4.30
        s_pure_b = Decimal("1.0") / Decimal("2.30") + Decimal("1.0") / Decimal("3.40") + Decimal("1.0") / Decimal("4.30")

        self.assertLessEqual(s_optimal, s_pure_a)
        self.assertLessEqual(s_optimal, s_pure_b)

    # -------------------------------------------------------------------------
    # 5. Determinism & Performance Benchmarking (Section 57)
    # -------------------------------------------------------------------------

    def test_determinism_across_runs(self):
        """Ensures that executing detect() multiple times produces byte-identical results."""
        comps = [
            self._create_comparison("HOME", source_odds=2.30, target_odds=2.20),
            self._create_comparison("DRAW", source_odds=3.40, target_odds=3.70),
            self._create_comparison("AWAY", source_odds=4.20, target_odds=4.00),
        ]

        res_1 = self.engine.detect(comps)
        res_2 = self.engine.detect(comps)

        self.assertEqual(len(res_1.opportunities), len(res_2.opportunities))
        opp_1 = res_1.opportunities[0]
        opp_2 = res_2.opportunities[0]

        self.assertEqual(opp_1.opportunity_id, opp_2.opportunity_id)
        self.assertEqual(opp_1.implied_probability_sum, opp_2.implied_probability_sum)
        self.assertEqual(opp_1.arbitrage_margin, opp_2.arbitrage_margin)
        self.assertEqual(opp_1.bookmakers, opp_2.bookmakers)
        self.assertEqual(len(opp_1.legs), len(opp_2.legs))
        for l1, l2 in zip(opp_1.legs, opp_2.legs):
            self.assertEqual(l1.selection_type, l2.selection_type)
            self.assertEqual(l1.provider, l2.provider)
            self.assertEqual(l1.odds, l2.odds)
            self.assertEqual(l1.implied_probability, l2.implied_probability)

    def test_performance_benchmark(self):
        """Benchmarks surebet detection across 10, 100, and 1000 markets."""
        for count in (10, 100, 1000):
            all_comps: List[OddsComparison] = []
            for i in range(count):
                ev_id = f"ev_bench_{i:04d}"
                mkt_k = CanonicalMarketKey(
                    market_type=CanonicalMarketType.ONE_X_TWO,
                    period="FULL_TIME",
                )
                all_comps.extend([
                    self._create_comparison("HOME", canonical_event_id=ev_id, market_key=mkt_k, source_odds=2.20, target_odds=2.15),
                    self._create_comparison("DRAW", canonical_event_id=ev_id, market_key=mkt_k, source_odds=3.40, target_odds=3.60),
                    self._create_comparison("AWAY", canonical_event_id=ev_id, market_key=mkt_k, source_odds=4.00, target_odds=4.25),
                ])

            t0 = time.perf_counter()
            res = self.engine.detect(all_comps)
            elapsed_ms = (time.perf_counter() - t0) * 1000.0

            self.assertEqual(res.metrics.surebet_count, count)
            rate = count / (elapsed_ms / 1000.0) if elapsed_ms > 0 else 0
            self.assertGreater(rate, 500, f"Throughput for {count} markets was {rate:.0f} markets/sec")

    # -------------------------------------------------------------------------
    # 6. End-to-End Pipeline Integration (Section 54)
    # -------------------------------------------------------------------------

    def test_controlled_end_to_end_pipeline(self):
        """End-to-End pipeline integration: Stage 5.3 -> Stage 5.4 -> Stage 5.5."""
        mkt_k = CanonicalMarketKey(market_type=CanonicalMarketType.ONE_X_TWO)

        # Create 3 matched ComparableSelectionPair records (HOME, DRAW, AWAY)
        pair_home = ComparableSelectionPair(
            canonical_event_id=self.event_id,
            canonical_market_key=mkt_k,
            canonical_selection_key=CanonicalSelectionKey(market_key=mkt_k, selection_type="HOME"),
            source_provider="superbet",
            target_provider="betclic",
            source_event_id="p_ev_sb",
            target_event_id="p_ev_bc",
            source_internal_event_id="int_ev_sb",
            target_internal_event_id="int_ev_bc",
            source_market_id="mkt_sb_1",
            target_market_id="mkt_bc_1",
            source_selection_id="sel_sb_h",
            target_selection_id="sel_bc_h",
            source_selection=Selection(market_id="mkt_sb_1", selection_type="HOME", internal_id="sel_sb_h"),
            target_selection=Selection(market_id="mkt_bc_1", selection_type="HOME", internal_id="sel_bc_h"),
            source_odds=Odds(selection_id="sel_sb_h", decimal_odds=2.80, bookmaker="superbet"),
            target_odds=Odds(selection_id="sel_bc_h", decimal_odds=2.10, bookmaker="betclic"),
        )
        pair_draw = ComparableSelectionPair(
            canonical_event_id=self.event_id,
            canonical_market_key=mkt_k,
            canonical_selection_key=CanonicalSelectionKey(market_key=mkt_k, selection_type="DRAW"),
            source_provider="superbet",
            target_provider="betclic",
            source_event_id="p_ev_sb",
            target_event_id="p_ev_bc",
            source_internal_event_id="int_ev_sb",
            target_internal_event_id="int_ev_bc",
            source_market_id="mkt_sb_1",
            target_market_id="mkt_bc_1",
            source_selection_id="sel_sb_d",
            target_selection_id="sel_bc_d",
            source_selection=Selection(market_id="mkt_sb_1", selection_type="DRAW", internal_id="sel_sb_d"),
            target_selection=Selection(market_id="mkt_bc_1", selection_type="DRAW", internal_id="sel_bc_d"),
            source_odds=Odds(selection_id="sel_sb_d", decimal_odds=3.40, bookmaker="superbet"),
            target_odds=Odds(selection_id="sel_bc_d", decimal_odds=3.80, bookmaker="betclic"),
        )
        pair_away = ComparableSelectionPair(
            canonical_event_id=self.event_id,
            canonical_market_key=mkt_k,
            canonical_selection_key=CanonicalSelectionKey(market_key=mkt_k, selection_type="AWAY"),
            source_provider="superbet",
            target_provider="betclic",
            source_event_id="p_ev_sb",
            target_event_id="p_ev_bc",
            source_internal_event_id="int_ev_sb",
            target_internal_event_id="int_ev_bc",
            source_market_id="mkt_sb_1",
            target_market_id="mkt_bc_1",
            source_selection_id="sel_sb_a",
            target_selection_id="sel_bc_a",
            source_selection=Selection(market_id="mkt_sb_1", selection_type="AWAY", internal_id="sel_sb_a"),
            target_selection=Selection(market_id="mkt_bc_1", selection_type="AWAY", internal_id="sel_bc_a"),
            source_odds=Odds(selection_id="sel_sb_a", decimal_odds=4.80, bookmaker="superbet"),
            target_odds=Odds(selection_id="sel_bc_a", decimal_odds=4.00, bookmaker="betclic"),
        )

        validation_result = CrossBookmakerValidationResult(
            comparable_selections=[pair_home, pair_draw, pair_away]
        )

        # Feed directly to Stage 5.5 polymorphic detect()
        surebet_result = self.engine.detect(validation_result)

        self.assertEqual(surebet_result.metrics.surebet_count, 1)
        opp = surebet_result.opportunities[0]
        self.assertEqual(opp.status, SurebetStatus.SUREBET)
        self.assertEqual(opp.canonical_event_id, self.event_id)
        self.assertEqual(len(opp.legs), 3)

        # Audit report generation check
        report = surebet_result.generate_audit_report(detailed=True)
        self.assertIn("CROSS-BOOKMAKER SUREBET DETECTION AUDIT REPORT", report)
        self.assertIn("CONFIRMED SUREBET OPPORTUNITIES", report)

    # -------------------------------------------------------------------------
    # 7. Real Fixture Audit (Section 52 & 53)
    # -------------------------------------------------------------------------

    def test_real_provider_fixtures_audit_and_overlap_verification(self):
        """Audits real recordings for Superbet and Betclic odds parsing and confirms 0 fabricated overlap."""
        # 1. Superbet Live Fixture Audit
        sb_live_path = Path("tests/fixtures/recordings/superbet/live_manifest/response_000.json")
        with open(sb_live_path, "r", encoding="utf-8") as f:
            sb_raw = json.load(f)

        sb_parser = SuperbetParser()
        sb_normalizer = SuperbetNormalizer()
        sb_events = sb_parser.parse_payloads(sb_raw.get("events", []))
        sb_graphs = [sb_normalizer.normalize_event(ev) for ev in sb_events]

        sb_total_odds = sum(len(g.odds_list) for g in sb_graphs)
        self.assertGreater(sb_total_odds, 0, "Superbet real fixtures must contain valid normalized odds")

        # 2. Betclic Live Fixture Audit
        bc_live_path = Path("tests/fixtures/recordings/betclic/live_manifest/response_000.json")
        with open(bc_live_path, "r", encoding="utf-8") as f:
            bc_raw = json.load(f)

        bc_parser = BetclicParser()
        bc_normalizer = BetclicNormalizer()
        bc_events = bc_parser.parse_payloads(bc_raw)
        bc_graphs = [bc_normalizer.normalize_event(ev) for ev in bc_events]

        bc_total_odds = sum(len(g.odds_list) for g in bc_graphs)
        self.assertGreater(bc_total_odds, 0, "Betclic real fixtures must contain valid normalized odds")

        # 3. Cross-Bookmaker Pipeline Execution
        pipeline = CrossBookmakerValidationPipeline()
        real_validation_res = pipeline.run(sb_graphs, bc_graphs)

        # Confirm 0 overlapping events between current recordings without fabrication
        self.assertEqual(len(real_validation_res.comparable_selections), 0)

        # Feed real result into Stage 5.5 engine
        real_surebet_res = self.engine.detect(real_validation_res)
        self.assertEqual(real_surebet_res.metrics.input_market_count, 0)
        self.assertEqual(real_surebet_res.metrics.surebet_count, 0)

    # -------------------------------------------------------------------------
    # 8. Architectural Boundary Verification (Section 61)
    # -------------------------------------------------------------------------

    def test_strict_architectural_boundary_no_forbidden_attributes(self):
        """Verifies that Stage 5.5 does NOT leak or implement forbidden bankroll, stake, EV, or betting concepts."""
        forbidden_attrs = [
            "stake",
            "stake_allocation",
            "stake_home",
            "stake_away",
            "stake_draw",
            "stake_distribution",
            "guaranteed_profit",
            "kelly_fraction",
            "bankroll",
            "expected_value",
            "ev",
            "valuebet_score",
            "sharp_odds",
            "fair_odds",
            "no_vig_odds",
            "reference_bookmaker",
            "place_bet",
            "auto_bet",
        ]

        opp = SurebetOpportunity(
            opportunity_id="test_id",
            canonical_event_id="test_ev",
            canonical_market_key=self.market_key_1x2,
            legs=(),
            implied_probability_sum=Decimal("0.97"),
            arbitrage_margin=Decimal("0.03"),
        )

        for attr in forbidden_attrs:
            self.assertFalse(
                hasattr(opp, attr),
                f"Stage 5.5 SurebetOpportunity leaked forbidden attribute '{attr}'",
            )
            self.assertFalse(
                hasattr(self.engine, attr),
                f"Stage 5.5 SurebetDetectorEngine leaked forbidden attribute '{attr}'",
            )

    # -------------------------------------------------------------------------
    # 9. Stage 10.14: Critical Safety Rule & Opportunity Integrity Tests
    # -------------------------------------------------------------------------

    def test_rejects_opportunity_with_incomplete_event_identity(self):
        """Stage 10.14: Opportunities must be rejected if canonical event identity has empty or placeholder team names."""
        bad_evidence = MatchEvidence(
            source_provider="betclic",
            target_provider="superbet",
            source_event_id="s_1",
            target_event_id="t_1",
            decision="MATCHED",
            total_score=0.95,
            orientation="NORMAL",
            home_team="—",
            away_team="",
            evidence={"home_team": "—", "away_team": ""},
        )

        c1 = self._create_comparison(
            selection_type="HOME",
            source_odds=Decimal("2.50"),
            target_odds=Decimal("2.50"),
            canonical_event_id="cev_dummy_incomplete",
        )
        c2 = self._create_comparison(
            selection_type="DRAW",
            source_odds=Decimal("4.00"),
            target_odds=Decimal("4.00"),
            canonical_event_id="cev_dummy_incomplete",
        )
        c3 = self._create_comparison(
            selection_type="AWAY",
            source_odds=Decimal("3.00"),
            target_odds=Decimal("3.00"),
            canonical_event_id="cev_dummy_incomplete",
        )
        # Attach bad evidence
        object.__setattr__(c1, "event_evidence", bad_evidence)
        object.__setattr__(c2, "event_evidence", bad_evidence)
        object.__setattr__(c3, "event_evidence", bad_evidence)

        eval_result = self.engine.evaluate_market(
            canonical_event_id="cev_dummy_incomplete",
            canonical_market_key=self.market_key_1x2,
            comparisons=[c1, c2, c3],
        )

        self.assertEqual(eval_result.status, SurebetStatus.INVALID_MARKET)
        self.assertEqual(eval_result.exclusion_reason_code, "INCOMPLETE_EVENT_IDENTITY")
        self.assertIsNone(eval_result.opportunity)

    def test_rejects_totals_with_invalid_or_missing_line(self):
        """Stage 10.14: Totals markets with missing or non-positive line must be rejected with LINE_INVALID."""
        invalid_totals_key = CanonicalMarketKey(
            market_type=CanonicalMarketType.TOTALS,
            scope="MATCH",
            period="FULL_TIME",
            line=0.0,
        )

        c1 = self._create_comparison(
            selection_type="OVER",
            source_odds=Decimal("2.50"),
            target_odds=Decimal("2.50"),
            market_key=invalid_totals_key,
        )
        c2 = self._create_comparison(
            selection_type="UNDER",
            source_odds=Decimal("2.50"),
            target_odds=Decimal("2.50"),
            market_key=invalid_totals_key,
        )

        eval_result = self.engine.evaluate_market(
            canonical_event_id=self.event_id,
            canonical_market_key=invalid_totals_key,
            comparisons=[c1, c2],
        )

        self.assertEqual(eval_result.status, SurebetStatus.INVALID_MARKET)
        self.assertEqual(eval_result.exclusion_reason_code, "LINE_INVALID")
        self.assertIsNone(eval_result.opportunity)


if __name__ == "__main__":
    unittest.main()

