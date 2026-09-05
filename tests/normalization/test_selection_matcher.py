"""
Unit & Integration Tests for Stage 5.2 — Cross-Bookmaker Selection Matching Architecture

Test Coverage:
1. Canonical Selection Key, Line & Score Normalization.
2. Semantic Equivalence Matrix (1X2, Totals, BTTS, Double Chance, DNB, Handicap, Correct Score).
3. Parent Market Enforcement & Cross-Market Safety.
4. Adversarial Tests Suite (A through J).
5. Explainability, Decomposed Evidence, and Audit Reasons.
6. Real Superbet Tier 2 Fixture Selection Audit.
7. Real Betclic Fixture Selection Audit.
8. Controlled Cross-Provider Selection Matching.
9. High-Throughput Batch Indexing & Ambiguity Isolation.
"""

import json
from decimal import Decimal
import unittest
from pathlib import Path
from collections import defaultdict

from domain.models import Event, Selection
from normalization.market_identity import (
    CanonicalMarketKey,
    CanonicalMarketType,
    MarketPeriod,
    MarketScope,
    MarketMetric,
    extract_canonical_market_key,
)
from normalization.selection_identity import (
    CanonicalSelectionKey,
    CanonicalSelectionType,
    extract_canonical_selection_key,
    normalize_score_outcome,
)
from normalization.selection_matcher import (
    SelectionMatchBatchResult,
    SelectionMatchDecision,
    SelectionMatchDecisionType,
    SelectionMatcher,
)
from normalization.superbet_normalizer import SuperbetNormalizer
from normalization.betclic_normalizer import BetclicNormalizer
from providers.superbet.parser.parser import SuperbetParser
from providers.betclic.parser.parser import BetclicParser
from providers.base.recording.replay_engine import ReplayEngine


class TestSelectionMatchingArchitecture(unittest.TestCase):
    """Test suite for Stage 5.2 Selection Matching Architecture."""

    def setUp(self):
        self.matcher = SelectionMatcher()
        self.market_1x2 = CanonicalMarketKey(market_type="1X2")
        self.market_totals_25 = CanonicalMarketKey(market_type="TOTALS", line=Decimal("2.5"))
        self.market_totals_35 = CanonicalMarketKey(market_type="TOTALS", line=Decimal("3.5"))
        self.market_btts = CanonicalMarketKey(market_type="BTTS")
        self.market_dc = CanonicalMarketKey(market_type="DOUBLE_CHANCE")
        self.market_dnb = CanonicalMarketKey(market_type="DRAW_NO_BET")
        self.market_hcp_m15 = CanonicalMarketKey(market_type="HANDICAP", line=Decimal("-1.5"))
        self.market_cs = CanonicalMarketKey(market_type="CORRECT_SCORE")

        self.event_dummy = Event(
            competition_id="comp_1",
            home_participant="Arsenal",
            away_participant="Chelsea",
            internal_id="ev_1",
        )

    # -------------------------------------------------------------------------
    # 1. Canonical Selection Key, Line & Score Normalization
    # -------------------------------------------------------------------------
    def test_score_normalization(self):
        """Verify score outcomes are normalized into standard 'H-A' format."""
        self.assertEqual(normalize_score_outcome("1:0"), "1-0")
        self.assertEqual(normalize_score_outcome("1 - 0"), "1-0")
        self.assertEqual(normalize_score_outcome("2:1"), "2-1")
        self.assertEqual(normalize_score_outcome("0:0"), "0-0")
        self.assertIsNone(normalize_score_outcome(None))

    def test_canonical_selection_key_immutability_and_equality(self):
        """Verify CanonicalSelectionKey equality, hashing, and string formatting."""
        key1 = CanonicalSelectionKey(
            market_key=self.market_totals_25,
            selection_type="OVER",
        )
        key2 = CanonicalSelectionKey(
            market_key=self.market_totals_25,
            selection_type="OVER",
        )
        self.assertEqual(key1, key2)
        self.assertEqual(hash(key1), hash(key2))
        self.assertEqual(key1.to_key_string(), "football:TOTALS:GOALS:MATCH:all:FULL_TIME:2.5:OVER:none:none:none")

    # -------------------------------------------------------------------------
    # 2. Semantic Equivalence Matrix
    # -------------------------------------------------------------------------
    def test_matrix_1x2_outcomes(self):
        """1X2 selections: HOME↔HOME, DRAW↔DRAW, AWAY↔AWAY (MATCHED); cross pairs (REJECTED)."""
        s_h1 = Selection(market_id="m1", selection_type="HOME", internal_id="s_h1")
        s_h2 = Selection(market_id="m2", selection_type="HOME", internal_id="s_h2")
        s_d1 = Selection(market_id="m1", selection_type="DRAW", internal_id="s_d1")
        s_d2 = Selection(market_id="m2", selection_type="DRAW", internal_id="s_d2")
        s_a1 = Selection(market_id="m1", selection_type="AWAY", internal_id="s_a1")
        s_a2 = Selection(market_id="m2", selection_type="AWAY", internal_id="s_a2")

        # Positive matches
        self.assertEqual(self.matcher.match_selection(s_h1, s_h2, self.market_1x2, self.market_1x2).decision, SelectionMatchDecisionType.MATCHED)
        self.assertEqual(self.matcher.match_selection(s_d1, s_d2, self.market_1x2, self.market_1x2).decision, SelectionMatchDecisionType.MATCHED)
        self.assertEqual(self.matcher.match_selection(s_a1, s_a2, self.market_1x2, self.market_1x2).decision, SelectionMatchDecisionType.MATCHED)

        # Rejections
        dec_hd = self.matcher.match_selection(s_h1, s_d2, self.market_1x2, self.market_1x2)
        self.assertEqual(dec_hd.decision, SelectionMatchDecisionType.REJECTED)
        self.assertIn("SELECTION_TYPE_MISMATCH", dec_hd.reasons)

        dec_ha = self.matcher.match_selection(s_h1, s_a2, self.market_1x2, self.market_1x2)
        self.assertEqual(dec_ha.decision, SelectionMatchDecisionType.REJECTED)
        self.assertIn("SELECTION_TYPE_MISMATCH", dec_ha.reasons)

        dec_da = self.matcher.match_selection(s_d1, s_a2, self.market_1x2, self.market_1x2)
        self.assertEqual(dec_da.decision, SelectionMatchDecisionType.REJECTED)
        self.assertIn("SELECTION_TYPE_MISMATCH", dec_da.reasons)

    def test_matrix_totals_outcomes(self):
        """Totals selections: OVER↔OVER, UNDER↔UNDER (MATCHED); OVER↔UNDER (REJECTED)."""
        s_o1 = Selection(market_id="m1", selection_type="OVER", internal_id="s_o1")
        s_o2 = Selection(market_id="m2", selection_type="OVER", internal_id="s_o2")
        s_u1 = Selection(market_id="m1", selection_type="UNDER", internal_id="s_u1")
        s_u2 = Selection(market_id="m2", selection_type="UNDER", internal_id="s_u2")

        self.assertEqual(self.matcher.match_selection(s_o1, s_o2, self.market_totals_25, self.market_totals_25).decision, SelectionMatchDecisionType.MATCHED)
        self.assertEqual(self.matcher.match_selection(s_u1, s_u2, self.market_totals_25, self.market_totals_25).decision, SelectionMatchDecisionType.MATCHED)

        dec_ou = self.matcher.match_selection(s_o1, s_u2, self.market_totals_25, self.market_totals_25)
        self.assertEqual(dec_ou.decision, SelectionMatchDecisionType.REJECTED)
        self.assertIn("SELECTION_TYPE_MISMATCH", dec_ou.reasons)

    def test_matrix_btts_outcomes(self):
        """BTTS selections: YES↔YES, NO↔NO (MATCHED); YES↔NO (REJECTED)."""
        s_y1 = Selection(market_id="m1", selection_type="YES", internal_id="s_y1")
        s_y2 = Selection(market_id="m2", selection_type="YES", internal_id="s_y2")
        s_n1 = Selection(market_id="m1", selection_type="NO", internal_id="s_n1")
        s_n2 = Selection(market_id="m2", selection_type="NO", internal_id="s_n2")

        self.assertEqual(self.matcher.match_selection(s_y1, s_y2, self.market_btts, self.market_btts).decision, SelectionMatchDecisionType.MATCHED)
        self.assertEqual(self.matcher.match_selection(s_n1, s_n2, self.market_btts, self.market_btts).decision, SelectionMatchDecisionType.MATCHED)

        dec_yn = self.matcher.match_selection(s_y1, s_n2, self.market_btts, self.market_btts)
        self.assertEqual(dec_yn.decision, SelectionMatchDecisionType.REJECTED)
        self.assertIn("SELECTION_TYPE_MISMATCH", dec_yn.reasons)

    def test_matrix_double_chance_outcomes(self):
        """Double chance selections: HOME_DRAW, DRAW_AWAY, HOME_AWAY."""
        s_hd1 = Selection(market_id="m1", selection_type="HOME_DRAW", internal_id="s_hd1")
        s_hd2 = Selection(market_id="m2", selection_type="HOME_DRAW", internal_id="s_hd2")
        s_da1 = Selection(market_id="m1", selection_type="DRAW_AWAY", internal_id="s_da1")
        s_da2 = Selection(market_id="m2", selection_type="DRAW_AWAY", internal_id="s_da2")
        s_ha1 = Selection(market_id="m1", selection_type="HOME_AWAY", internal_id="s_ha1")
        s_ha2 = Selection(market_id="m2", selection_type="HOME_AWAY", internal_id="s_ha2")

        self.assertEqual(self.matcher.match_selection(s_hd1, s_hd2, self.market_dc, self.market_dc).decision, SelectionMatchDecisionType.MATCHED)
        self.assertEqual(self.matcher.match_selection(s_da1, s_da2, self.market_dc, self.market_dc).decision, SelectionMatchDecisionType.MATCHED)
        self.assertEqual(self.matcher.match_selection(s_ha1, s_ha2, self.market_dc, self.market_dc).decision, SelectionMatchDecisionType.MATCHED)

        dec_hd_da = self.matcher.match_selection(s_hd1, s_da2, self.market_dc, self.market_dc)
        self.assertEqual(dec_hd_da.decision, SelectionMatchDecisionType.REJECTED)
        self.assertIn("SELECTION_TYPE_MISMATCH", dec_hd_da.reasons)

    def test_matrix_draw_no_bet_outcomes(self):
        """Draw No Bet selections: HOME↔HOME, AWAY↔AWAY (MATCHED); HOME↔AWAY (REJECTED)."""
        s_h1 = Selection(market_id="m1", selection_type="HOME", internal_id="s_h1")
        s_h2 = Selection(market_id="m2", selection_type="HOME", internal_id="s_h2")
        s_a1 = Selection(market_id="m1", selection_type="AWAY", internal_id="s_a1")
        s_a2 = Selection(market_id="m2", selection_type="AWAY", internal_id="s_a2")

        self.assertEqual(self.matcher.match_selection(s_h1, s_h2, self.market_dnb, self.market_dnb).decision, SelectionMatchDecisionType.MATCHED)
        self.assertEqual(self.matcher.match_selection(s_a1, s_a2, self.market_dnb, self.market_dnb).decision, SelectionMatchDecisionType.MATCHED)

        dec_ha = self.matcher.match_selection(s_h1, s_a2, self.market_dnb, self.market_dnb)
        self.assertEqual(dec_ha.decision, SelectionMatchDecisionType.REJECTED)

    def test_matrix_handicap_outcomes(self):
        """Handicap selections: HOME -1.5 ↔ HOME -1.5 (MATCHED), HOME -1.5 ↔ HOME +1.5 (REJECTED)."""
        s_h_m15 = Selection(market_id="m1", selection_type="HOME", line=-1.5, internal_id="s_h_m15")
        s_h_m15_2 = Selection(market_id="m2", selection_type="HOME", line=-1.5, internal_id="s_h_m15_2")
        s_h_p15 = Selection(market_id="m2", selection_type="HOME", line=1.5, internal_id="s_h_p15")
        s_a_p15 = Selection(market_id="m1", selection_type="AWAY", line=1.5, internal_id="s_a_p15")
        s_a_p15_2 = Selection(market_id="m2", selection_type="AWAY", line=1.5, internal_id="s_a_p15_2")

        # Same side & line -> MATCHED
        self.assertEqual(self.matcher.match_selection(s_h_m15, s_h_m15_2, self.market_hcp_m15, self.market_hcp_m15).decision, SelectionMatchDecisionType.MATCHED)
        self.assertEqual(self.matcher.match_selection(s_a_p15, s_a_p15_2, self.market_hcp_m15, self.market_hcp_m15).decision, SelectionMatchDecisionType.MATCHED)

        # Line sign mismatch -> REJECTED
        dec_sign = self.matcher.match_selection(s_h_m15, s_h_p15, self.market_hcp_m15, self.market_hcp_m15)
        self.assertEqual(dec_sign.decision, SelectionMatchDecisionType.REJECTED)
        self.assertIn("LINE_MISMATCH", dec_sign.reasons)

    def test_matrix_correct_score_outcomes(self):
        """Correct Score selections: 1-0 ↔ 1-0 (MATCHED); 1-0 ↔ 0-1 (REJECTED)."""
        s_10_1 = Selection(market_id="m1", selection_type="1-0", internal_id="s_10_1")
        s_10_2 = Selection(market_id="m2", selection_type="1:0", internal_id="s_10_2")
        s_01 = Selection(market_id="m2", selection_type="0-1", internal_id="s_01")

        # 1-0 vs 1:0 -> MATCHED
        dec_match = self.matcher.match_selection(s_10_1, s_10_2, self.market_cs, self.market_cs)
        self.assertEqual(dec_match.decision, SelectionMatchDecisionType.MATCHED)
        self.assertEqual(dec_match.canonical_selection_key.score_outcome, "1-0")

        # 1-0 vs 0-1 -> REJECTED
        dec_mismatch = self.matcher.match_selection(s_10_1, s_01, self.market_cs, self.market_cs)
        self.assertEqual(dec_mismatch.decision, SelectionMatchDecisionType.REJECTED)
        self.assertIn("SCORE_OUTCOME_MISMATCH", dec_mismatch.reasons)

    # -------------------------------------------------------------------------
    # 3. Parent Market Enforcement & Cross-Market Safety
    # -------------------------------------------------------------------------
    def test_cross_market_safety_totals_over_vs_btts_yes(self):
        """TOTALS OVER vs BTTS YES -> INVALID_INPUT (MARKET_KEY_MISMATCH)."""
        s_o = Selection(market_id="m1", selection_type="OVER", internal_id="s_o")
        s_y = Selection(market_id="m2", selection_type="YES", internal_id="s_y")

        decision = self.matcher.match_selection(s_o, s_y, self.market_totals_25, self.market_btts)
        self.assertEqual(decision.decision, SelectionMatchDecisionType.INVALID_INPUT)
        self.assertIn("MARKET_KEY_MISMATCH", decision.reasons)

    def test_cross_market_safety_1x2_home_vs_dnb_home(self):
        """1X2 HOME vs DNB HOME -> INVALID_INPUT (MARKET_KEY_MISMATCH)."""
        s_h1 = Selection(market_id="m1", selection_type="HOME", internal_id="s_h1")
        s_h2 = Selection(market_id="m2", selection_type="HOME", internal_id="s_h2")

        decision = self.matcher.match_selection(s_h1, s_h2, self.market_1x2, self.market_dnb)
        self.assertEqual(decision.decision, SelectionMatchDecisionType.INVALID_INPUT)
        self.assertIn("MARKET_KEY_MISMATCH", decision.reasons)

    def test_cross_market_safety_totals_line_mismatch(self):
        """TOTALS 2.5 OVER vs TOTALS 3.5 OVER -> INVALID_INPUT (MARKET_KEY_MISMATCH)."""
        s_o1 = Selection(market_id="m1", selection_type="OVER", internal_id="s_o1")
        s_o2 = Selection(market_id="m2", selection_type="OVER", internal_id="s_o2")

        decision = self.matcher.match_selection(s_o1, s_o2, self.market_totals_25, self.market_totals_35)
        self.assertEqual(decision.decision, SelectionMatchDecisionType.INVALID_INPUT)
        self.assertIn("MARKET_KEY_MISMATCH", decision.reasons)

    def test_cross_market_safety_period_mismatch(self):
        """FIRST_HALF 1X2 HOME vs FULL_TIME 1X2 HOME -> INVALID_INPUT (MARKET_KEY_MISMATCH)."""
        mkt_fh = CanonicalMarketKey(market_type="1X2", period="FIRST_HALF")
        s_h1 = Selection(market_id="m1", selection_type="HOME", internal_id="s_h1")
        s_h2 = Selection(market_id="m2", selection_type="HOME", internal_id="s_h2")

        decision = self.matcher.match_selection(s_h1, s_h2, mkt_fh, self.market_1x2)
        self.assertEqual(decision.decision, SelectionMatchDecisionType.INVALID_INPUT)
        self.assertIn("MARKET_KEY_MISMATCH", decision.reasons)

    # -------------------------------------------------------------------------
    # 4. Adversarial Tests Suite (A through J)
    # -------------------------------------------------------------------------
    def test_adversarial_a_change_home_to_away(self):
        """Adversarial A: HOME -> AWAY -> REJECTED."""
        s1 = Selection(market_id="m1", selection_type="HOME", internal_id="s_a1")
        s2 = Selection(market_id="m2", selection_type="AWAY", internal_id="s_a2")

        decision = self.matcher.match_selection(s1, s2, self.market_1x2, self.market_1x2)
        self.assertEqual(decision.decision, SelectionMatchDecisionType.REJECTED)
        self.assertIn("SELECTION_TYPE_MISMATCH", decision.reasons)

    def test_adversarial_b_change_over_to_under(self):
        """Adversarial B: OVER -> UNDER -> REJECTED."""
        s1 = Selection(market_id="m1", selection_type="OVER", internal_id="s_b1")
        s2 = Selection(market_id="m2", selection_type="UNDER", internal_id="s_b2")

        decision = self.matcher.match_selection(s1, s2, self.market_totals_25, self.market_totals_25)
        self.assertEqual(decision.decision, SelectionMatchDecisionType.REJECTED)
        self.assertIn("SELECTION_TYPE_MISMATCH", decision.reasons)

    def test_adversarial_c_change_yes_to_no(self):
        """Adversarial C: YES -> NO -> REJECTED."""
        s1 = Selection(market_id="m1", selection_type="YES", internal_id="s_c1")
        s2 = Selection(market_id="m2", selection_type="NO", internal_id="s_c2")

        decision = self.matcher.match_selection(s1, s2, self.market_btts, self.market_btts)
        self.assertEqual(decision.decision, SelectionMatchDecisionType.REJECTED)
        self.assertIn("SELECTION_TYPE_MISMATCH", decision.reasons)

    def test_adversarial_d_change_home_draw_to_draw_away(self):
        """Adversarial D: HOME_DRAW -> DRAW_AWAY -> REJECTED."""
        s1 = Selection(market_id="m1", selection_type="HOME_DRAW", internal_id="s_d1")
        s2 = Selection(market_id="m2", selection_type="DRAW_AWAY", internal_id="s_d2")

        decision = self.matcher.match_selection(s1, s2, self.market_dc, self.market_dc)
        self.assertEqual(decision.decision, SelectionMatchDecisionType.REJECTED)
        self.assertIn("SELECTION_TYPE_MISMATCH", decision.reasons)

    def test_adversarial_e_change_handicap_sign(self):
        """Adversarial E: Handicap -1.5 -> +1.5 -> REJECTED."""
        s1 = Selection(market_id="m1", selection_type="HOME", line=-1.5, internal_id="s_e1")
        s2 = Selection(market_id="m2", selection_type="HOME", line=1.5, internal_id="s_e2")

        decision = self.matcher.match_selection(s1, s2, self.market_hcp_m15, self.market_hcp_m15)
        self.assertEqual(decision.decision, SelectionMatchDecisionType.REJECTED)
        self.assertIn("LINE_MISMATCH", decision.reasons)

    def test_adversarial_f_change_correct_score(self):
        """Adversarial F: CORRECT_SCORE 1-0 -> 0-1 -> REJECTED."""
        s1 = Selection(market_id="m1", selection_type="1-0", internal_id="s_f1")
        s2 = Selection(market_id="m2", selection_type="0-1", internal_id="s_f2")

        decision = self.matcher.match_selection(s1, s2, self.market_cs, self.market_cs)
        self.assertEqual(decision.decision, SelectionMatchDecisionType.REJECTED)
        self.assertIn("SCORE_OUTCOME_MISMATCH", decision.reasons)

    def test_adversarial_g_same_raw_names_different_semantics(self):
        """Adversarial G: Identical raw selection name ("Gospodarz") but one is Home under 1X2, other is Home under DNB."""
        s1 = Selection(market_id="m1", selection_type="GOSPODARZ", internal_id="s_g1")
        s2 = Selection(market_id="m2", selection_type="GOSPODARZ", internal_id="s_g2")

        # Different parent market keys -> INVALID_INPUT
        decision = self.matcher.match_selection(s1, s2, self.market_1x2, self.market_dnb)
        self.assertEqual(decision.decision, SelectionMatchDecisionType.INVALID_INPUT)
        self.assertIn("MARKET_KEY_MISMATCH", decision.reasons)

    def test_adversarial_h_different_raw_names_same_canonical_semantics(self):
        """Adversarial H: Different raw names ("1" vs "Home") -> MATCHED."""
        s1 = Selection(market_id="m1", selection_type="1", internal_id="s_h1")
        s2 = Selection(market_id="m2", selection_type="HOME", internal_id="s_h2")

        decision = self.matcher.match_selection(s1, s2, self.market_1x2, self.market_1x2)
        self.assertEqual(decision.decision, SelectionMatchDecisionType.MATCHED)
        self.assertEqual(decision.canonical_selection_key.selection_type, "HOME")

    def test_adversarial_i_duplicate_canonical_selection_key(self):
        """Adversarial I: Duplicate canonical selection keys in target market -> AMBIGUOUS."""
        s_src = Selection(market_id="m_s", selection_type="OVER", internal_id="s_src")
        s_tgt1 = Selection(market_id="m_t", selection_type="OVER", internal_id="s_tgt1")
        s_tgt2 = Selection(market_id="m_t", selection_type="OVER", internal_id="s_tgt2")

        batch_res = self.matcher.match_market_selections(
            source_selections=[s_src],
            target_selections=[s_tgt1, s_tgt2],
            source_market_key=self.market_totals_25,
            target_market_key=self.market_totals_25,
        )

        self.assertEqual(len(batch_res.matched_pairs), 0)
        self.assertEqual(len(batch_res.ambiguous_selections), 2)
        self.assertEqual(batch_res.ambiguous_selections[0].decision, SelectionMatchDecisionType.AMBIGUOUS)

    def test_adversarial_j_market_key_mismatch_in_batch(self):
        """Adversarial J: Different market keys in batch matching -> INVALID_INPUT."""
        s_src = Selection(market_id="m_s", selection_type="OVER", internal_id="s_src")
        s_tgt = Selection(market_id="m_t", selection_type="OVER", internal_id="s_tgt")

        batch_res = self.matcher.match_market_selections(
            source_selections=[s_src],
            target_selections=[s_tgt],
            source_market_key=self.market_totals_25,
            target_market_key=self.market_totals_35,
        )

        self.assertEqual(len(batch_res.invalid_input_decisions), 1)
        self.assertEqual(batch_res.invalid_input_decisions[0].decision, SelectionMatchDecisionType.INVALID_INPUT)

    # -------------------------------------------------------------------------
    # 5. Explainability & Evidence
    # -------------------------------------------------------------------------
    def test_decision_evidence_structure(self):
        """Verify matched decision produces full decomposed evidence."""
        s1 = Selection(market_id="m1", selection_type="HOME", internal_id="s1")
        s2 = Selection(market_id="m2", selection_type="HOME", internal_id="s2")

        decision = self.matcher.match_selection(s1, s2, self.market_1x2, self.market_1x2)
        self.assertEqual(decision.decision, SelectionMatchDecisionType.MATCHED)
        self.assertEqual(decision.evidence["selection_type"], "exact")
        self.assertEqual(decision.evidence["participant_role"], "exact")
        self.assertEqual(decision.evidence["market_key"], "exact")

    # -------------------------------------------------------------------------
    # 6. Real Superbet Tier 2 Fixture Selection Audit
    # -------------------------------------------------------------------------
    def test_superbet_real_selection_audit(self):
        """Audits selections in genuine Superbet Tier 2 production recording."""
        fixture_path = Path("tests/fixtures/recordings/superbet/detail_manifest")
        engine = ReplayEngine(session_dir=fixture_path, strict=True)
        resp = engine.get_response(
            "https://production-superbet-offer-pl.freetls.fastly.net/v2/pl-PL/events/13222121",
            "GET",
        )
        raw_payload = resp.json()
        parser = SuperbetParser()
        events = parser.parse_payloads([raw_payload])
        normalizer = SuperbetNormalizer()
        graph = normalizer.normalize_event(events[0])

        self.assertEqual(len(graph.selections), 688)

        # Map selections by market_id
        sels_by_market = defaultdict(list)
        for s in graph.selections:
            sels_by_market[s.market_id].append(s)

        supported_canonical_markets = []
        unsupported_markets = []

        for m in graph.markets:
            m_key = extract_canonical_market_key(m)
            if m_key:
                supported_canonical_markets.append((m, m_key, sels_by_market[m.internal_id]))
            else:
                unsupported_markets.append((m, sels_by_market[m.internal_id]))

        self.assertEqual(len(supported_canonical_markets), 482)
        self.assertEqual(len(unsupported_markets), 63)

        # Count selections
        supported_sels_count = sum(len(sels) for _, _, sels in supported_canonical_markets)
        unsupported_sels_count = sum(len(sels) for _, sels in unsupported_markets)

        self.assertEqual(supported_sels_count, 596)
        self.assertEqual(unsupported_sels_count, 92)
        self.assertEqual(supported_sels_count + unsupported_sels_count, 688)

        # Verify selections in supported markets have valid CanonicalSelectionKey
        for m, m_key, sels in supported_canonical_markets:
            indexed, unsupported_s = self.matcher.index_selections(sels, m_key, graph.event)
            self.assertEqual(len(unsupported_s), 0)
            self.assertEqual(sum(len(l) for l in indexed.values()), len(sels))

    # -------------------------------------------------------------------------
    # 7. Real Betclic Fixture Selection Audit
    # -------------------------------------------------------------------------
    def test_betclic_real_selection_audit(self):
        """Audits selections in genuine Betclic production recording."""
        fixture_path = Path("tests/fixtures/recordings/betclic/live_manifest/response_000.json")
        with open(fixture_path, "r", encoding="utf-8") as f:
            raw_payload = json.load(f)

        parser = BetclicParser()
        events = parser.parse_payloads(raw_payload)
        normalizer = BetclicNormalizer()
        graph = normalizer.normalize_event(events[0])

        self.assertEqual(len(graph.selections), 5)

        sels_by_market = defaultdict(list)
        for s in graph.selections:
            sels_by_market[s.market_id].append(s)

        for m in graph.markets:
            m_key = extract_canonical_market_key(m)
            self.assertIsNotNone(m_key)
            sels = sels_by_market[m.internal_id]
            indexed, unsupported_s = self.matcher.index_selections(sels, m_key, graph.event)
            self.assertEqual(len(unsupported_s), 0)
            self.assertEqual(sum(len(l) for l in indexed.values()), len(sels))

    # -------------------------------------------------------------------------
    # 8. Controlled Cross-Provider Selection Matching
    # -------------------------------------------------------------------------
    def test_controlled_cross_provider_selection_matching(self):
        """Validates cross-provider selection matching between normalized Superbet and Betclic models."""
        # 1X2 Market
        sb_1x2_sels = [
            Selection(market_id="sb_m1", selection_type="HOME", participant="Arsenal", internal_id="sb_s1"),
            Selection(market_id="sb_m1", selection_type="DRAW", internal_id="sb_s2"),
            Selection(market_id="sb_m1", selection_type="AWAY", participant="Chelsea", internal_id="sb_s3"),
        ]

        bc_1x2_sels = [
            Selection(market_id="bc_m1", selection_type="HOME", participant="Arsenal", internal_id="bc_s1"),
            Selection(market_id="bc_m1", selection_type="DRAW", internal_id="bc_s2"),
            Selection(market_id="bc_m1", selection_type="AWAY", participant="Chelsea", internal_id="bc_s3"),
        ]

        batch_1x2 = self.matcher.match_market_selections(
            source_selections=sb_1x2_sels,
            target_selections=bc_1x2_sels,
            source_market_key=self.market_1x2,
            target_market_key=self.market_1x2,
            source_event=self.event_dummy,
            target_event=self.event_dummy,
        )

        self.assertEqual(len(batch_1x2.matched_pairs), 3)
        matched_sel_types = {d.canonical_selection_key.selection_type for d in batch_1x2.matched_pairs}
        self.assertEqual(matched_sel_types, {"HOME", "DRAW", "AWAY"})

        # TOTALS 2.5 Market
        sb_totals_sels = [
            Selection(market_id="sb_m2", selection_type="OVER", line=2.5, internal_id="sb_s_o"),
            Selection(market_id="sb_m2", selection_type="UNDER", line=2.5, internal_id="sb_s_u"),
        ]
        bc_totals_sels = [
            Selection(market_id="bc_m2", selection_type="OVER", line=2.5, internal_id="bc_s_o"),
            Selection(market_id="bc_m2", selection_type="UNDER", line=2.5, internal_id="bc_s_u"),
        ]

        batch_totals = self.matcher.match_market_selections(
            source_selections=sb_totals_sels,
            target_selections=bc_totals_sels,
            source_market_key=self.market_totals_25,
            target_market_key=self.market_totals_25,
        )

        self.assertEqual(len(batch_totals.matched_pairs), 2)
        matched_tot_types = {d.canonical_selection_key.selection_type for d in batch_totals.matched_pairs}
        self.assertEqual(matched_tot_types, {"OVER", "UNDER"})


if __name__ == "__main__":
    unittest.main()
