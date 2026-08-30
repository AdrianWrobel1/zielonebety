"""
Stage 5.4: Cross-Bookmaker Odds Comparison Layer Test Suite

Verifies:
1. Controlled mathematical examples (Examples 1-8).
2. Complete adversarial test suite (Adversarial A through O).
3. Precision and decimal accuracy policies.
4. Provider orientation symmetry.
5. End-to-end integration with Stage 5.3 (CrossBookmakerValidationResult -> OddsComparisonResult).
6. Real fixture audit & diagnostic reporting.
7. Strict Zero Arbitrage & Zero Valuebet boundary invariants.
8. Determinism and performance benchmarking (10, 100, 1000 pairs).
9. Audit report formatting and diagnostic telemetry.
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
from normalization.market_identity import CanonicalMarketKey, CanonicalMarketType
from normalization.selection_identity import CanonicalSelectionKey, CanonicalSelectionType
from normalization.validation_pipeline import (
    ComparableSelectionPair,
    CrossBookmakerValidationPipeline,
    CrossBookmakerValidationResult,
)
from normalization.odds_comparison import (
    OddsComparison,
    OddsComparisonEngine,
    OddsComparisonMetrics,
    OddsComparisonResult,
    OddsComparisonStatus,
)
from normalization.base_normalizer import NormalizedGraph
from providers.superbet.parser.parser import SuperbetParser
from normalization.superbet_normalizer import SuperbetNormalizer
from providers.betclic.parser.parser import BetclicParser
from normalization.betclic_normalizer import BetclicNormalizer


class TestOddsComparisonLayer(unittest.TestCase):
    """Authoritative test suite for Stage 5.4 Cross-Bookmaker Odds Comparison Layer."""

    def setUp(self):
        self.engine = OddsComparisonEngine()
        self.market_key = CanonicalMarketKey(
            market_type=CanonicalMarketType.ONE_X_TWO,
            scope="MATCH",
            period="FULL_TIME",
        )
        self.selection_key = CanonicalSelectionKey(
            market_key=self.market_key,
            selection_type=CanonicalSelectionType.HOME.value,
        )


    def _create_selection_pair(
        self,
        source_provider: str = "superbet",
        target_provider: str = "betclic",
        source_odds_val: Any = 2.10,
        target_odds_val: Any = 2.25,
        source_sel_meta: Optional[Dict[str, Any]] = None,
        target_sel_meta: Optional[Dict[str, Any]] = None,
        mkt_key: Optional[CanonicalMarketKey] = None,
        sel_key: Optional[CanonicalSelectionKey] = None,
    ) -> ComparableSelectionPair:
        """Helper to create a controlled ComparableSelectionPair."""
        s_sel = Selection(
            market_id="mkt_sb_1",
            selection_type="HOME",
            internal_id="sel_sb_1",
            provider_ids={source_provider: "p_sel_sb_1"},
            metadata=source_sel_meta or {},
        )
        t_sel = Selection(
            market_id="mkt_bc_1",
            selection_type="HOME",
            internal_id="sel_bc_1",
            provider_ids={target_provider: "p_sel_bc_1"},
            metadata=target_sel_meta or {},
        )

        s_odds = None
        if source_odds_val is not None:
            if isinstance(source_odds_val, Odds):
                s_odds = source_odds_val
            else:
                s_odds = Odds(
                    selection_id="sel_sb_1",
                    bookmaker=source_provider,
                    decimal_odds=source_odds_val,
                    internal_id="odds_sb_1",
                )

        t_odds = None
        if target_odds_val is not None:
            if isinstance(target_odds_val, Odds):
                t_odds = target_odds_val
            else:
                t_odds = Odds(
                    selection_id="sel_bc_1",
                    bookmaker=target_provider,
                    decimal_odds=target_odds_val,
                    internal_id="odds_bc_1",
                )

        return ComparableSelectionPair(
            canonical_event_id="cev_arsenal_chelsea_2026",
            canonical_market_key=mkt_key or self.market_key,
            canonical_selection_key=sel_key or self.selection_key,
            source_provider=source_provider,
            target_provider=target_provider,
            source_event_id="sb_ev_100",
            target_event_id="bc_ev_200",
            source_internal_event_id="ev_sb_1",
            target_internal_event_id="ev_bc_1",
            source_market_id="mkt_sb_1",
            target_market_id="mkt_bc_1",
            source_selection_id="p_sel_sb_1",
            target_selection_id="p_sel_bc_1",
            source_selection=s_sel,
            target_selection=t_sel,
            source_odds=s_odds,
            target_odds=t_odds,
            event_evidence=MatchEvidence(
                source_provider=source_provider,
                target_provider=target_provider,
                source_event_id="sb_ev_100",
                target_event_id="bc_ev_200",
                decision="MATCHED",
                total_score=1.0,
                orientation="NORMAL",
            ),
            market_evidence={"market_score": 1.0, "match_type": "EXACT"},
            selection_evidence={"selection_score": 1.0, "match_type": "EXACT"},
        )

    # -------------------------------------------------------------------------
    # 1. Controlled Examples (Section 32)
    # -------------------------------------------------------------------------

    def test_example_1_target_higher(self):
        """Example 1: Superbet=2.10, Betclic=2.25 -> diff=0.15, higher=Betclic, raw implied."""
        pair = self._create_selection_pair(source_odds_val=2.10, target_odds_val=2.25)
        comp = self.engine.compare_pair(pair)

        self.assertEqual(comp.status, OddsComparisonStatus.VALID)
        self.assertEqual(comp.source_odds, Decimal("2.10"))
        self.assertEqual(comp.target_odds, Decimal("2.25"))
        self.assertEqual(comp.higher_odds_provider, "betclic")
        self.assertEqual(comp.odds_difference, Decimal("0.15"))
        self.assertEqual(comp.absolute_difference, Decimal("0.15"))
        self.assertEqual(comp.odds_ratio, Decimal("2.25") / Decimal("2.10"))
        self.assertEqual(comp.source_implied_probability, Decimal(1) / Decimal("2.10"))
        self.assertEqual(comp.target_implied_probability, Decimal(1) / Decimal("2.25"))

    def test_example_2_source_higher(self):
        """Example 2: Superbet=1.50, Betclic=1.40 -> diff=-0.10, abs_diff=0.10, higher=Superbet."""
        pair = self._create_selection_pair(source_odds_val=1.50, target_odds_val=1.40)
        comp = self.engine.compare_pair(pair)

        self.assertEqual(comp.status, OddsComparisonStatus.VALID)
        self.assertEqual(comp.source_odds, Decimal("1.50"))
        self.assertEqual(comp.target_odds, Decimal("1.40"))
        self.assertEqual(comp.higher_odds_provider, "superbet")
        self.assertEqual(comp.odds_difference, Decimal("-0.10"))
        self.assertEqual(comp.absolute_difference, Decimal("0.10"))
        self.assertEqual(comp.odds_ratio, Decimal("1.50") / Decimal("1.40"))

    def test_example_3_equal_odds_tie(self):
        """Example 3: Superbet=2.10, Betclic=2.10 -> diff=0, higher=TIE."""
        pair = self._create_selection_pair(source_odds_val=2.10, target_odds_val=2.10)
        comp = self.engine.compare_pair(pair)

        self.assertEqual(comp.status, OddsComparisonStatus.VALID)
        self.assertEqual(comp.source_odds, Decimal("2.10"))
        self.assertEqual(comp.target_odds, Decimal("2.10"))
        self.assertEqual(comp.higher_odds_provider, "TIE")
        self.assertEqual(comp.odds_difference, Decimal("0.00"))
        self.assertEqual(comp.absolute_difference, Decimal("0.00"))
        self.assertEqual(comp.odds_ratio, Decimal("1.0"))

    def test_example_4_missing_source_odds_incomplete(self):
        """Example 4: Superbet=None, Betclic=2.20 -> INCOMPLETE."""
        pair = self._create_selection_pair(source_odds_val=None, target_odds_val=2.20)
        comp = self.engine.compare_pair(pair)

        self.assertEqual(comp.status, OddsComparisonStatus.INCOMPLETE)
        self.assertIsNone(comp.source_odds)
        self.assertEqual(comp.target_odds, Decimal("2.20"))
        self.assertIsNone(comp.higher_odds_provider)
        self.assertIsNone(comp.odds_difference)
        self.assertIsNone(comp.absolute_difference)
        self.assertIsNone(comp.source_implied_probability)
        self.assertEqual(comp.target_implied_probability, Decimal(1) / Decimal("2.20"))

    def test_example_5_both_odds_missing_no_odds(self):
        """Example 5: Superbet=None, Betclic=None -> NO_ODDS."""
        pair = self._create_selection_pair(source_odds_val=None, target_odds_val=None)
        comp = self.engine.compare_pair(pair)

        self.assertEqual(comp.status, OddsComparisonStatus.NO_ODDS)
        self.assertIsNone(comp.source_odds)
        self.assertIsNone(comp.target_odds)
        self.assertIsNone(comp.higher_odds_provider)
        self.assertIsNone(comp.odds_difference)
        self.assertIsNone(comp.absolute_difference)

    def test_example_6_odds_one_point_zero_invalid(self):
        """Example 6: Superbet=1.00, Betclic=2.20 -> INVALID."""
        pair = self._create_selection_pair(source_odds_val=1.00, target_odds_val=2.20)
        comp = self.engine.compare_pair(pair)

        self.assertEqual(comp.status, OddsComparisonStatus.INVALID)
        self.assertIn("<= 1.0", comp.rejection_reason)

    def test_example_7_nan_odds_invalid(self):
        """Example 7: Superbet=NaN, Betclic=2.20 -> INVALID."""
        pair = self._create_selection_pair(source_odds_val=float("nan"), target_odds_val=2.20)
        comp = self.engine.compare_pair(pair)

        self.assertEqual(comp.status, OddsComparisonStatus.INVALID)
        self.assertIn("NaN", comp.rejection_reason)

    def test_example_8_provider_orientation_symmetry(self):
        """Example 8 & Section 21/22: Symmetrical results under reverse provider orientation."""
        # A vs B
        pair_ab = self._create_selection_pair(
            source_provider="superbet",
            target_provider="betclic",
            source_odds_val=2.10,
            target_odds_val=2.25,
        )
        comp_ab = self.engine.compare_pair(pair_ab)

        # B vs A
        pair_ba = self._create_selection_pair(
            source_provider="betclic",
            target_provider="superbet",
            source_odds_val=2.25,
            target_odds_val=2.10,
        )
        comp_ba = self.engine.compare_pair(pair_ba)

        # Both agree that Betclic has higher odds
        self.assertEqual(comp_ab.higher_odds_provider, "betclic")
        self.assertEqual(comp_ba.higher_odds_provider, "betclic")

        # Absolute difference and ratio are identical
        self.assertEqual(comp_ab.absolute_difference, comp_ba.absolute_difference)
        self.assertEqual(comp_ab.odds_ratio, comp_ba.odds_ratio)

        # Signed differences are inverted
        self.assertEqual(comp_ab.odds_difference, Decimal("0.15"))
        self.assertEqual(comp_ba.odds_difference, Decimal("-0.15"))

        # Implied probabilities correspond to providers
        self.assertEqual(comp_ab.source_implied_probability, comp_ba.target_implied_probability)
        self.assertEqual(comp_ab.target_implied_probability, comp_ba.source_implied_probability)

    # -------------------------------------------------------------------------
    # 2. Adversarial Matrix (Section 33: A through O)
    # -------------------------------------------------------------------------

    def test_adversarial_a_non_matched_selection_pair(self):
        """Adversarial A: Rejects invalid input types before comparison."""
        with self.assertRaises(TypeError):
            self.engine.compare_pair("invalid_pair_object")  # type: ignore

    def test_adversarial_b_missing_source_odds(self):
        """Adversarial B: Missing source odds flagged INCOMPLETE."""
        pair = self._create_selection_pair(source_odds_val=None, target_odds_val=1.85)
        comp = self.engine.compare_pair(pair)
        self.assertEqual(comp.status, OddsComparisonStatus.INCOMPLETE)
        self.assertIn("missing odds for provider 'superbet'", comp.rejection_reason)

    def test_adversarial_c_missing_target_odds(self):
        """Adversarial C: Missing target odds flagged INCOMPLETE."""
        pair = self._create_selection_pair(source_odds_val=1.85, target_odds_val=None)
        comp = self.engine.compare_pair(pair)
        self.assertEqual(comp.status, OddsComparisonStatus.INCOMPLETE)
        self.assertIn("missing odds for provider 'betclic'", comp.rejection_reason)

    def test_adversarial_d_both_odds_missing(self):
        """Adversarial D: Both odds missing flagged NO_ODDS."""
        pair = self._create_selection_pair(source_odds_val=None, target_odds_val=None)
        comp = self.engine.compare_pair(pair)
        self.assertEqual(comp.status, OddsComparisonStatus.NO_ODDS)
        self.assertEqual(comp.rejection_reason, "Both bookmaker odds are missing (None)")

    def test_adversarial_e_odds_equal_one_point_zero(self):
        """Adversarial E: Decimal odds == 1.0 is invalid."""
        pair = self._create_selection_pair(source_odds_val=1.0, target_odds_val=2.5)
        comp = self.engine.compare_pair(pair)
        self.assertEqual(comp.status, OddsComparisonStatus.INVALID)

    def test_adversarial_f_odds_less_than_one(self):
        """Adversarial F: Odds < 1.0 (e.g. 0.85) is invalid."""
        pair = self._create_selection_pair(source_odds_val=0.85, target_odds_val=2.5)
        comp = self.engine.compare_pair(pair)
        self.assertEqual(comp.status, OddsComparisonStatus.INVALID)

    def test_adversarial_g_negative_odds(self):
        """Adversarial G: Negative odds (e.g. -2.5) is invalid."""
        pair = self._create_selection_pair(source_odds_val=-2.5, target_odds_val=2.5)
        comp = self.engine.compare_pair(pair)
        self.assertEqual(comp.status, OddsComparisonStatus.INVALID)

    def test_adversarial_h_nan_odds(self):
        """Adversarial H: NaN float or Decimal is invalid."""
        pair = self._create_selection_pair(source_odds_val=float("nan"), target_odds_val=2.5)
        comp = self.engine.compare_pair(pair)
        self.assertEqual(comp.status, OddsComparisonStatus.INVALID)

    def test_adversarial_i_infinity_odds(self):
        """Adversarial I: Infinity is invalid."""
        pair = self._create_selection_pair(source_odds_val=float("inf"), target_odds_val=2.5)
        comp = self.engine.compare_pair(pair)
        self.assertEqual(comp.status, OddsComparisonStatus.INVALID)

    def test_adversarial_j_equal_odds_tie(self):
        """Adversarial J: Equal odds returns higher_odds_provider == 'TIE' without bias."""
        pair = self._create_selection_pair(source_odds_val=3.45, target_odds_val=3.45)
        comp = self.engine.compare_pair(pair)
        self.assertEqual(comp.status, OddsComparisonStatus.VALID)
        self.assertEqual(comp.higher_odds_provider, "TIE")
        self.assertEqual(comp.absolute_difference, Decimal("0.00"))

    def test_adversarial_k_very_high_odds(self):
        """Adversarial K: Very high finite odds (e.g. 999.00) are valid."""
        pair = self._create_selection_pair(source_odds_val=501.00, target_odds_val=999.00)
        comp = self.engine.compare_pair(pair)
        self.assertEqual(comp.status, OddsComparisonStatus.VALID)
        self.assertEqual(comp.higher_odds_provider, "betclic")
        self.assertEqual(comp.absolute_difference, Decimal("498.00"))
        self.assertEqual(comp.odds_ratio, Decimal("999.00") / Decimal("501.00"))

    def test_adversarial_l_high_decimal_precision(self):
        """Adversarial L: No premature rounding or floating-point distortion."""
        pair = self._create_selection_pair(
            source_odds_val=Decimal("1.3333333333333333"),
            target_odds_val=Decimal("2.3750000000000000"),
        )
        comp = self.engine.compare_pair(pair)
        self.assertEqual(comp.status, OddsComparisonStatus.VALID)
        expected_diff = Decimal("2.3750000000000000") - Decimal("1.3333333333333333")
        self.assertEqual(comp.odds_difference, expected_diff)
        self.assertEqual(comp.absolute_difference, expected_diff)

    def test_adversarial_m_multiple_active_odds_ambiguous(self):
        """Adversarial M: Ambiguous multiple active odds flagged AMBIGUOUS."""
        pair = self._create_selection_pair(
            source_odds_val=2.10,
            target_odds_val=2.25,
            source_sel_meta={"multiple_active_odds": True},
        )
        comp = self.engine.compare_pair(pair)
        self.assertEqual(comp.status, OddsComparisonStatus.AMBIGUOUS)
        self.assertIn("Multiple conflicting active odds", comp.rejection_reason)

    def test_adversarial_n_inactive_suspended_odds(self):
        """Adversarial N: Suspended or inactive selections flagged INACTIVE."""
        pair = self._create_selection_pair(
            source_odds_val=2.10,
            target_odds_val=2.25,
            target_sel_meta={"status": "SUSPENDED"},
        )
        comp = self.engine.compare_pair(pair)
        self.assertEqual(comp.status, OddsComparisonStatus.INACTIVE)
        self.assertIn("SUSPENDED", comp.rejection_reason)

    def test_adversarial_o_reverse_provider_orientation_batch(self):
        """Adversarial O: Batch comparison under reverse provider orientation."""
        pairs_ab = [
            self._create_selection_pair("superbet", "betclic", 1.90, 2.05),
            self._create_selection_pair("superbet", "betclic", 3.40, 3.20),
            self._create_selection_pair("superbet", "betclic", 2.10, 2.10),
        ]
        pairs_ba = [
            self._create_selection_pair("betclic", "superbet", 2.05, 1.90),
            self._create_selection_pair("betclic", "superbet", 3.20, 3.40),
            self._create_selection_pair("betclic", "superbet", 2.10, 2.10),
        ]

        res_ab = self.engine.compare_pairs(pairs_ab)
        res_ba = self.engine.compare_pairs(pairs_ba)

        self.assertEqual(res_ab.metrics.valid_comparison_count, 3)
        self.assertEqual(res_ba.metrics.valid_comparison_count, 3)
        self.assertEqual(
            res_ab.metrics.average_absolute_odds_difference,
            res_ba.metrics.average_absolute_odds_difference,
        )
        self.assertEqual(
            res_ab.metrics.average_odds_ratio,
            res_ba.metrics.average_odds_ratio,
        )

    # -------------------------------------------------------------------------
    # 3. Batch Telemetry, Aggregate Metrics, & Zero-Division Safety (Section 30)
    # -------------------------------------------------------------------------

    def test_batch_metrics_calculation_and_division_safety(self):
        """Verifies aggregate metrics on mixed batch and division-by-zero protection."""
        # 1. Empty batch
        empty_res = self.engine.compare_pairs([])
        self.assertEqual(empty_res.metrics.input_comparable_selection_count, 0)
        self.assertEqual(empty_res.metrics.valid_comparison_count, 0)
        self.assertIsNone(empty_res.metrics.average_absolute_odds_difference)
        self.assertIsNone(empty_res.metrics.max_absolute_odds_difference)
        self.assertIsNone(empty_res.metrics.min_absolute_odds_difference)
        self.assertIsNone(empty_res.metrics.average_odds_ratio)
        self.assertIsNone(empty_res.metrics.validity_rate)

        # 2. Mixed batch
        pairs = [
            self._create_selection_pair(source_odds_val=2.00, target_odds_val=2.20),  # Valid, diff=0.20, higher=target
            self._create_selection_pair(source_odds_val=1.80, target_odds_val=1.50),  # Valid, diff=-0.30, abs=0.30, higher=source
            self._create_selection_pair(source_odds_val=2.50, target_odds_val=2.50),  # Valid, diff=0.00, tie
            self._create_selection_pair(source_odds_val=None, target_odds_val=2.10),  # Incomplete
            self._create_selection_pair(source_odds_val=None, target_odds_val=None),  # No odds
            self._create_selection_pair(source_odds_val=-1.0, target_odds_val=2.00),  # Invalid
            self._create_selection_pair(source_odds_val=2.00, target_odds_val=2.00, source_sel_meta={"status": "SUSPENDED"}), # Inactive
            self._create_selection_pair(source_odds_val=2.00, target_odds_val=2.00, target_sel_meta={"multiple_active_odds": True}), # Ambiguous
        ]

        res = self.engine.compare_pairs(pairs)

        self.assertEqual(res.metrics.input_comparable_selection_count, 8)
        self.assertEqual(res.metrics.valid_comparison_count, 3)
        self.assertEqual(res.metrics.incomplete_count, 1)
        self.assertEqual(res.metrics.no_odds_count, 1)
        self.assertEqual(res.metrics.invalid_count, 1)
        self.assertEqual(res.metrics.inactive_count, 1)
        self.assertEqual(res.metrics.ambiguous_count, 1)

        self.assertEqual(res.metrics.higher_odds_target_count, 1)
        self.assertEqual(res.metrics.higher_odds_source_count, 1)
        self.assertEqual(res.metrics.equal_odds_count, 1)

        # Expected avg diff: (0.20 + 0.30 + 0.00) / 3 = 0.50 / 3 ≈ 0.16666...
        expected_avg_diff = Decimal("0.50") / Decimal("3")
        self.assertEqual(res.metrics.average_absolute_odds_difference, expected_avg_diff)
        self.assertEqual(res.metrics.min_absolute_odds_difference, Decimal("0.00"))
        self.assertEqual(res.metrics.max_absolute_odds_difference, Decimal("0.30"))

        report = res.generate_audit_report(detailed=True)
        self.assertIn("STAGE 5.4 CROSS-BOOKMAKER ODDS COMPARISON AUDIT REPORT", report)
        self.assertIn("VALID=3", report)
        self.assertIn("DETAILED ODDS COMPARISON RECORDS:", report)

    # -------------------------------------------------------------------------
    # 4. Controlled End-to-End Pipeline Integration (Section 35 & 36)
    # -------------------------------------------------------------------------

    def _build_controlled_graph(
        self,
        provider: str,
        home: str,
        away: str,
        start: str,
        comp_name: str,
        markets_data: List[Dict],
    ) -> NormalizedGraph:
        ev_id = f"ev_{provider}_{home.lower()}_{away.lower()}"
        comp_id = f"comp_{provider}_{comp_name.lower().replace(' ', '_')}"

        competition = Competition(
            name=comp_name,
            sport="Football",
            internal_id=comp_id,
            provider_ids={provider: f"p_comp_{comp_name.lower()}"},
        )
        event = Event(
            competition_id=comp_id,
            home_participant=home,
            away_participant=away,
            scheduled_start=start,
            internal_id=ev_id,
            provider_ids={provider: f"p_ev_{ev_id}"},
            metadata={"provider": provider},
        )
        markets: List[Market] = []
        selections: List[Selection] = []
        odds_list: List[Odds] = []

        for m_idx, m_info in enumerate(markets_data):
            mkt_id = f"mkt_{provider}_{ev_id}_{m_idx}"
            mkt = Market(
                event_id=ev_id,
                market_type=m_info["type"],
                line=m_info.get("line"),
                internal_id=mkt_id,
                provider_ids={provider: f"p_mkt_{mkt_id}"},
            )
            markets.append(mkt)

            for s_idx, s_info in enumerate(m_info.get("selections", [])):
                sel_id = f"sel_{provider}_{mkt_id}_{s_idx}"
                sel = Selection(
                    market_id=mkt_id,
                    selection_type=s_info["type"],
                    line=s_info.get("line"),
                    participant=s_info.get("participant"),
                    internal_id=sel_id,
                    provider_ids={provider: f"p_sel_{sel_id}"},
                )
                selections.append(sel)

                if "odds" in s_info:
                    odds = Odds(
                        selection_id=sel_id,
                        bookmaker=provider,
                        decimal_odds=float(s_info["odds"]),
                        internal_id=f"odds_{provider}_{sel_id}",
                    )
                    odds_list.append(odds)

        return NormalizedGraph(
            competition=competition,
            event=event,
            markets=markets,
            selections=selections,
            odds_list=odds_list,
        )

    def test_controlled_end_to_end_validation_pipeline_integration(self):
        """End-to-end integration: Stage 5.3 validation pipeline into Stage 5.4 comparison."""
        sb_markets = [
            {
                "type": "1X2",
                "selections": [
                    {"type": "HOME", "participant": "Arsenal", "odds": 2.10},
                    {"type": "DRAW", "odds": 3.40},
                    {"type": "AWAY", "participant": "Chelsea", "odds": 3.90},
                ],
            },
            {
                "type": "TOTALS",
                "line": 2.5,
                "selections": [
                    {"type": "OVER", "line": 2.5, "odds": 2.05},
                    {"type": "UNDER", "line": 2.5, "odds": 1.75},
                ],
            },
            {
                "type": "BTTS",
                "selections": [
                    {"type": "YES", "odds": 1.80},
                    {"type": "NO", "odds": 1.95},
                ],
            },
        ]

        bc_markets = [
            {
                "type": "1X2",
                "selections": [
                    {"type": "HOME", "participant": "Arsenal", "odds": 2.25},
                    {"type": "DRAW", "odds": 3.30},
                    {"type": "AWAY", "participant": "Chelsea", "odds": 4.10},
                ],
            },
            {
                "type": "TOTALS",
                "line": 2.5,
                "selections": [
                    {"type": "OVER", "line": 2.5, "odds": 2.15},
                    {"type": "UNDER", "line": 2.5, "odds": 1.70},
                ],
            },
            {
                "type": "BTTS",
                "selections": [
                    {"type": "YES", "odds": 1.90},
                    {"type": "NO", "odds": 1.90},
                ],
            },
        ]

        sb_graph = self._build_controlled_graph("superbet", "Arsenal", "Chelsea", "2026-08-20T19:00:00Z", "Premier League", sb_markets)
        bc_graph = self._build_controlled_graph("betclic", "Arsenal", "Chelsea", "2026-08-20T19:00:00Z", "Premier League", bc_markets)

        # Stage 5.3 run
        pipeline = CrossBookmakerValidationPipeline()
        val_result = pipeline.run([sb_graph], [bc_graph])

        self.assertEqual(len(val_result.canonical_events), 1)
        self.assertEqual(len(val_result.comparable_selections), 7)  # 3 (1X2) + 2 (TOTALS) + 2 (BTTS)

        # Stage 5.4 run
        comp_result = self.engine.compare(val_result)

        self.assertIsInstance(comp_result, OddsComparisonResult)
        self.assertEqual(comp_result.metrics.input_comparable_selection_count, 7)
        self.assertEqual(comp_result.metrics.valid_comparison_count, 7)

        # Spot-check 1X2 Arsenal Home comparison
        home_comps = [
            c for c in comp_result.valid_comparisons
            if c.canonical_market_key.market_type == CanonicalMarketType.ONE_X_TWO
            and c.canonical_selection_key.selection_type == CanonicalSelectionType.HOME
        ]
        self.assertEqual(len(home_comps), 1)
        home_c = home_comps[0]
        # Canonical validation pipeline orders providers deterministically ('betclic' < 'superbet')
        self.assertEqual(home_c.source_provider, "betclic")
        self.assertEqual(home_c.target_provider, "superbet")
        self.assertEqual(home_c.source_odds, Decimal("2.25"))
        self.assertEqual(home_c.target_odds, Decimal("2.10"))
        self.assertEqual(home_c.higher_odds_provider, "betclic")
        self.assertEqual(home_c.odds_difference, Decimal("-0.15"))
        self.assertEqual(home_c.absolute_difference, Decimal("0.15"))


        # Spot-check Lineage Preservation
        self.assertEqual(home_c.canonical_event_id, val_result.canonical_events[0].canonical_event_id)
        self.assertIsNotNone(home_c.event_evidence)
        self.assertIsNotNone(home_c.market_evidence)
        self.assertIsNotNone(home_c.selection_evidence)

    # -------------------------------------------------------------------------
    # 5. Strict Zero Arbitrage & Zero Valuebet Boundary Invariants (Section 37 & 38)
    # -------------------------------------------------------------------------

    def test_zero_arbitrage_and_zero_valuebet_boundary_invariants(self):
        """Proves that Stage 5.4 strictly enforces boundaries against arbitrage/valuebet leakage."""
        pair = self._create_selection_pair(source_odds_val=2.10, target_odds_val=2.25)
        comp = self.engine.compare_pair(pair)

        # Forbidden Stage 5.5 attributes
        forbidden_attrs = [
            "arbitrage_margin",
            "is_surebet",
            "surebet_margin",
            "stake_home",
            "stake_away",
            "stake_draw",
            "stake_distribution",
            "guaranteed_profit",
            "kelly_fraction",
            "expected_value",
            "ev",
            "valuebet_score",
            "sharp_odds",
            "fair_odds",
            "no_vig_odds",
            "reference_bookmaker",
        ]

        for attr in forbidden_attrs:
            self.assertFalse(
                hasattr(comp, attr),
                f"Stage 5.4 OddsComparison leaked forbidden attribute '{attr}'",
            )
            self.assertFalse(
                hasattr(self.engine, attr),
                f"Stage 5.4 OddsComparisonEngine leaked forbidden attribute '{attr}'",
            )

    # -------------------------------------------------------------------------
    # 6. Real Fixture Audit (Section 34)
    # -------------------------------------------------------------------------

    def test_real_provider_odds_fixture_audit(self):
        """Audits real recordings for Superbet and Betclic odds parsing without fabricating overlap."""
        # 1. Superbet Live/Detail Fixture Audit
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

        # 3. Cross-Bookmaker Overlap Check (Confirm 0 Overlap without fabrication)
        pipeline = CrossBookmakerValidationPipeline()
        real_result = pipeline.run(sb_graphs, bc_graphs)

        # Authoritative check: No overlapping events between Superbet and Betclic in current recordings
        self.assertEqual(len(real_result.comparable_selections), 0)

        # Feed empty comparable list into Stage 5.4 engine
        stage_54_real_res = self.engine.compare(real_result)
        self.assertEqual(stage_54_real_res.metrics.input_comparable_selection_count, 0)
        self.assertEqual(stage_54_real_res.metrics.valid_comparison_count, 0)

    # -------------------------------------------------------------------------
    # 7. Determinism & Performance Benchmarking (Section 39 & 40)
    # -------------------------------------------------------------------------

    def test_determinism_across_runs(self):
        """Ensures that executing compare() multiple times produces byte-identical results."""
        pairs = [
            self._create_selection_pair(source_odds_val=1.95, target_odds_val=2.05),
            self._create_selection_pair(source_odds_val=3.50, target_odds_val=3.40),
            self._create_selection_pair(source_odds_val=4.20, target_odds_val=4.20),
        ]

        res_1 = self.engine.compare_pairs(pairs)
        res_2 = self.engine.compare_pairs(pairs)

        self.assertEqual(len(res_1.comparisons), len(res_2.comparisons))
        for c1, c2 in zip(res_1.comparisons, res_2.comparisons):
            self.assertEqual(c1.canonical_event_id, c2.canonical_event_id)
            self.assertEqual(c1.status, c2.status)
            self.assertEqual(c1.source_odds, c2.source_odds)
            self.assertEqual(c1.target_odds, c2.target_odds)
            self.assertEqual(c1.higher_odds_provider, c2.higher_odds_provider)
            self.assertEqual(c1.odds_difference, c2.odds_difference)
            self.assertEqual(c1.absolute_difference, c2.absolute_difference)
            self.assertEqual(c1.odds_ratio, c2.odds_ratio)
            self.assertEqual(c1.source_implied_probability, c2.source_implied_probability)
            self.assertEqual(c1.target_implied_probability, c2.target_implied_probability)

    def test_performance_benchmark(self):
        """Benchmarks odds comparison for 10, 100, and 1000 pairs."""
        for count in (10, 100, 1000):
            pairs = [
                self._create_selection_pair(
                    source_odds_val=2.00 + (i % 50) * 0.05,
                    target_odds_val=2.05 + (i % 30) * 0.04,
                )
                for i in range(count)
            ]

            t0 = time.perf_counter()
            res = self.engine.compare_pairs(pairs)
            elapsed_ms = (time.perf_counter() - t0) * 1000.0

            self.assertEqual(res.metrics.valid_comparison_count, count)
            rate = count / (elapsed_ms / 1000.0) if elapsed_ms > 0 else 0
            # Ensure high throughput (at least 1000 comparisons/sec)
            self.assertGreater(rate, 1000, f"Throughput for {count} pairs was {rate:.0f} comparisons/sec")


if __name__ == "__main__":
    unittest.main()
