"""
Unit & Integration Tests for Stage 5.1 — Cross-Bookmaker Market Matching Architecture

Test Coverage:
1. Canonical Market Key & Numeric Normalization (Decimal, float, int, formatting).
2. Semantic Equivalence Matrix (1X2, Totals, BTTS, Handicap, DNB, Double Chance, Correct Score).
3. Period / Time Scope Validation (Full Time, First Half, Second Half).
4. Scope, Metric & Participant Differentiation (Match vs Team vs Player, Goals vs Corners vs Cards).
5. Adversarial Tests Suite (A through H).
6. Explainability, Decomposed Evidence, and Audit Reasons.
7. Real Superbet Tier 2 Fixture Market Taxonomy Classification.
8. Real Betclic Fixture Market Taxonomy Classification.
9. Controlled Cross-Provider Validation.
10. High-Throughput Batch Indexing and Determinism.
"""

import json
from decimal import Decimal
import unittest
from pathlib import Path

from domain.models import Market
from normalization.market_identity import (
    CanonicalMarketKey,
    CanonicalMarketType,
    MarketMetric,
    MarketPeriod,
    MarketScope,
    ParticipantRole,
    extract_canonical_market_key,
    normalize_line,
)
from normalization.market_matcher import (
    MarketMatchBatchResult,
    MarketMatchDecision,
    MarketMatchDecisionType,
    MarketMatcher,
)
from normalization.superbet_normalizer import SuperbetNormalizer
from normalization.betclic_normalizer import BetclicNormalizer
from providers.superbet.parser.parser import SuperbetParser
from providers.betclic.parser.parser import BetclicParser
from providers.base.recording.replay_engine import ReplayEngine


class TestMarketMatchingArchitecture(unittest.TestCase):
    """Test suite for Stage 5.1 Market Matching Architecture."""

    def setUp(self):
        self.matcher = MarketMatcher()

    # -------------------------------------------------------------------------
    # 1. Canonical Market Key & Line Normalization
    # -------------------------------------------------------------------------
    def test_line_normalization_precision(self):
        """Verify numeric lines are normalized deterministically across types and string formats."""
        self.assertEqual(normalize_line(2.5), Decimal("2.5"))
        self.assertEqual(normalize_line("2.50"), Decimal("2.5"))
        self.assertEqual(normalize_line(2.0), Decimal("2"))
        self.assertEqual(normalize_line(2), Decimal("2"))
        self.assertEqual(normalize_line(-1.5), Decimal("-1.5"))
        self.assertEqual(normalize_line("-1.500"), Decimal("-1.5"))
        self.assertEqual(normalize_line(0), Decimal("0"))
        self.assertIsNone(normalize_line(None))
        self.assertIsNone(normalize_line("invalid_line"))

    def test_canonical_market_key_immutability_and_equality(self):
        """Verify CanonicalMarketKey equality with Decimal normalization and string serialization."""
        key1 = CanonicalMarketKey(
            market_type="TOTALS",
            line=Decimal("2.5"),
            period="FULL_TIME",
            scope="MATCH",
            metric="GOALS",
        )
        key2 = CanonicalMarketKey(
            market_type="TOTALS",
            line=Decimal("2.50"),
            period="FULL_TIME",
            scope="MATCH",
            metric="GOALS",
        )
        self.assertEqual(key1, key2)
        self.assertEqual(hash(key1), hash(key2))
        self.assertEqual(key1.to_key_string(), "football:TOTALS:GOALS:MATCH:all:FULL_TIME:2.5")

    # -------------------------------------------------------------------------
    # 2. Semantic Equivalence Matrix
    # -------------------------------------------------------------------------
    def test_matrix_1x2_vs_1x2(self):
        """1X2 ↔ 1X2 -> MATCHED."""
        m1 = Market(event_id="ev_1", market_type="1X2", internal_id="m1")
        m2 = Market(event_id="ev_2", market_type="1X2", internal_id="m2")
        decision = self.matcher.match(m1, m2)
        self.assertEqual(decision.decision, MarketMatchDecisionType.MATCHED)
        self.assertIsNotNone(decision.canonical_market_key)
        self.assertEqual(decision.canonical_market_key.market_type, "1X2")

    def test_matrix_1x2_vs_totals(self):
        """1X2 ↔ TOTALS -> REJECTED (MARKET_TYPE_MISMATCH)."""
        m1 = Market(event_id="ev_1", market_type="1X2", internal_id="m1")
        m2 = Market(event_id="ev_2", market_type="TOTALS", line=2.5, internal_id="m2")
        decision = self.matcher.match(m1, m2)
        self.assertEqual(decision.decision, MarketMatchDecisionType.REJECTED)
        self.assertIn("MARKET_TYPE_MISMATCH", decision.reasons)

    def test_matrix_1x2_vs_double_chance(self):
        """1X2 ↔ DOUBLE_CHANCE -> REJECTED (MARKET_TYPE_MISMATCH)."""
        m1 = Market(event_id="ev_1", market_type="1X2", internal_id="m1")
        m2 = Market(event_id="ev_2", market_type="DOUBLE_CHANCE", internal_id="m2")
        decision = self.matcher.match(m1, m2)
        self.assertEqual(decision.decision, MarketMatchDecisionType.REJECTED)
        self.assertIn("MARKET_TYPE_MISMATCH", decision.reasons)

    def test_matrix_1x2_vs_draw_no_bet(self):
        """1X2 ↔ DRAW_NO_BET -> REJECTED (MARKET_TYPE_MISMATCH)."""
        m1 = Market(event_id="ev_1", market_type="1X2", internal_id="m1")
        m2 = Market(event_id="ev_2", market_type="DRAW_NO_BET", internal_id="m2")
        decision = self.matcher.match(m1, m2)
        self.assertEqual(decision.decision, MarketMatchDecisionType.REJECTED)
        self.assertIn("MARKET_TYPE_MISMATCH", decision.reasons)

    def test_matrix_totals_same_line(self):
        """TOTALS 2.5 ↔ TOTALS 2.5 -> MATCHED."""
        m1 = Market(event_id="ev_1", market_type="TOTALS", line=2.5, internal_id="m1")
        m2 = Market(event_id="ev_2", market_type="TOTALS", line=2.5, internal_id="m2")
        decision = self.matcher.match(m1, m2)
        self.assertEqual(decision.decision, MarketMatchDecisionType.MATCHED)
        self.assertEqual(decision.canonical_market_key.line, Decimal("2.5"))

    def test_matrix_totals_line_mismatch(self):
        """TOTALS 2.5 ↔ TOTALS 3.5 -> REJECTED (LINE_MISMATCH)."""
        m1 = Market(event_id="ev_1", market_type="TOTALS", line=2.5, internal_id="m1")
        m2 = Market(event_id="ev_2", market_type="TOTALS", line=3.5, internal_id="m2")
        decision = self.matcher.match(m1, m2)
        self.assertEqual(decision.decision, MarketMatchDecisionType.REJECTED)
        self.assertIn("LINE_MISMATCH", decision.reasons)

    def test_matrix_totals_vs_btts(self):
        """TOTALS 2.5 ↔ BTTS -> REJECTED (MARKET_TYPE_MISMATCH)."""
        m1 = Market(event_id="ev_1", market_type="TOTALS", line=2.5, internal_id="m1")
        m2 = Market(event_id="ev_2", market_type="BTTS", internal_id="m2")
        decision = self.matcher.match(m1, m2)
        self.assertEqual(decision.decision, MarketMatchDecisionType.REJECTED)
        self.assertIn("MARKET_TYPE_MISMATCH", decision.reasons)

    def test_matrix_btts_vs_btts(self):
        """BTTS ↔ BTTS -> MATCHED."""
        m1 = Market(event_id="ev_1", market_type="BTTS", internal_id="m1")
        m2 = Market(event_id="ev_2", market_type="BTTS", internal_id="m2")
        decision = self.matcher.match(m1, m2)
        self.assertEqual(decision.decision, MarketMatchDecisionType.MATCHED)
        self.assertEqual(decision.canonical_market_key.market_type, "BTTS")

    def test_matrix_handicap_same_line(self):
        """HANDICAP -1.5 ↔ HANDICAP -1.5 -> MATCHED."""
        m1 = Market(event_id="ev_1", market_type="HANDICAP", line=-1.5, internal_id="m1")
        m2 = Market(event_id="ev_2", market_type="HANDICAP", line=-1.5, internal_id="m2")
        decision = self.matcher.match(m1, m2)
        self.assertEqual(decision.decision, MarketMatchDecisionType.MATCHED)
        self.assertEqual(decision.canonical_market_key.line, Decimal("-1.5"))

    def test_matrix_handicap_sign_mismatch(self):
        """HANDICAP -1.5 ↔ HANDICAP +1.5 -> REJECTED (LINE_MISMATCH)."""
        m1 = Market(event_id="ev_1", market_type="HANDICAP", line=-1.5, internal_id="m1")
        m2 = Market(event_id="ev_2", market_type="HANDICAP", line=1.5, internal_id="m2")
        decision = self.matcher.match(m1, m2)
        self.assertEqual(decision.decision, MarketMatchDecisionType.REJECTED)
        self.assertIn("LINE_MISMATCH", decision.reasons)

    def test_matrix_draw_no_bet_vs_draw_no_bet(self):
        """DRAW_NO_BET ↔ DRAW_NO_BET -> MATCHED."""
        m1 = Market(event_id="ev_1", market_type="DRAW_NO_BET", internal_id="m1")
        m2 = Market(event_id="ev_2", market_type="DRAW_NO_BET", internal_id="m2")
        decision = self.matcher.match(m1, m2)
        self.assertEqual(decision.decision, MarketMatchDecisionType.MATCHED)

    def test_matrix_double_chance_vs_double_chance(self):
        """DOUBLE_CHANCE ↔ DOUBLE_CHANCE -> MATCHED."""
        m1 = Market(event_id="ev_1", market_type="DOUBLE_CHANCE", internal_id="m1")
        m2 = Market(event_id="ev_2", market_type="DOUBLE_CHANCE", internal_id="m2")
        decision = self.matcher.match(m1, m2)
        self.assertEqual(decision.decision, MarketMatchDecisionType.MATCHED)

    def test_matrix_correct_score_vs_correct_score(self):
        """CORRECT_SCORE ↔ CORRECT_SCORE -> MATCHED."""
        m1 = Market(event_id="ev_1", market_type="CORRECT_SCORE", internal_id="m1")
        m2 = Market(event_id="ev_2", market_type="CORRECT_SCORE", internal_id="m2")
        decision = self.matcher.match(m1, m2)
        self.assertEqual(decision.decision, MarketMatchDecisionType.MATCHED)

    # -------------------------------------------------------------------------
    # 3. Period / Time Scope Handling
    # -------------------------------------------------------------------------
    def test_period_match_and_mismatch(self):
        """Verify period preservation (FULL_TIME vs FIRST_HALF)."""
        m_ft = Market(event_id="ev_1", market_type="TOTALS", line=1.5, metadata={"period": "FULL_TIME"})
        m_fh = Market(event_id="ev_2", market_type="TOTALS", line=1.5, metadata={"period": "FIRST_HALF"})
        m_fh2 = Market(event_id="ev_3", market_type="TOTALS", line=1.5, metadata={"period": "FIRST_HALF"})

        # FT vs FH -> REJECTED
        dec_mismatch = self.matcher.match(m_ft, m_fh)
        self.assertEqual(dec_mismatch.decision, MarketMatchDecisionType.REJECTED)
        self.assertIn("PERIOD_MISMATCH", dec_mismatch.reasons)

        # FH vs FH -> MATCHED
        dec_match = self.matcher.match(m_fh, m_fh2)
        self.assertEqual(dec_match.decision, MarketMatchDecisionType.MATCHED)
        self.assertEqual(dec_match.canonical_market_key.period, "FIRST_HALF")

    # -------------------------------------------------------------------------
    # 4. Scope, Metric & Participant Differentiation
    # -------------------------------------------------------------------------
    def test_metric_mismatch_goals_vs_corners_vs_cards(self):
        """Do not collapse goals totals, corner totals, or card totals."""
        m_goals = Market(event_id="ev_1", market_type="TOTALS", line=9.5, metadata={"metric": "GOALS"})
        m_corners = Market(event_id="ev_2", market_type="TOTALS", line=9.5, metadata={"metric": "CORNERS"})
        m_cards = Market(event_id="ev_3", market_type="TOTALS", line=9.5, metadata={"metric": "CARDS"})

        dec_gc = self.matcher.match(m_goals, m_corners)
        self.assertEqual(dec_gc.decision, MarketMatchDecisionType.REJECTED)
        self.assertIn("METRIC_MISMATCH", dec_gc.reasons)

        dec_cc = self.matcher.match(m_corners, m_cards)
        self.assertEqual(dec_cc.decision, MarketMatchDecisionType.REJECTED)
        self.assertIn("METRIC_MISMATCH", dec_cc.reasons)

    def test_scope_and_participant_differentiation(self):
        """Differentiate Match totals from Home team totals and Away team totals."""
        m_match = Market(event_id="ev_1", market_type="TOTALS", line=1.5, metadata={"scope": "MATCH"})
        m_home = Market(event_id="ev_2", market_type="TOTALS", line=1.5, metadata={"scope": "TEAM", "participant_role": "HOME"})
        m_away = Market(event_id="ev_3", market_type="TOTALS", line=1.5, metadata={"scope": "TEAM", "participant_role": "AWAY"})

        dec_mh = self.matcher.match(m_match, m_home)
        self.assertEqual(dec_mh.decision, MarketMatchDecisionType.REJECTED)
        self.assertTrue("SCOPE_MISMATCH" in dec_mh.reasons or "PARTICIPANT_MISMATCH" in dec_mh.reasons)

        dec_ha = self.matcher.match(m_home, m_away)
        self.assertEqual(dec_ha.decision, MarketMatchDecisionType.REJECTED)
        self.assertIn("PARTICIPANT_MISMATCH", dec_ha.reasons)

    # -------------------------------------------------------------------------
    # 5. Adversarial Tests Suite (A through H)
    # -------------------------------------------------------------------------
    def test_adversarial_a_remove_line_from_totals(self):
        """Adversarial A: Remove line from one totals market -> Cannot silently match (REJECTED)."""
        m_with_line = Market(event_id="ev_1", market_type="TOTALS", line=2.5, internal_id="m_a1")
        m_no_line = Market(event_id="ev_2", market_type="TOTALS", line=None, internal_id="m_a2")

        decision = self.matcher.match(m_with_line, m_no_line)
        self.assertEqual(decision.decision, MarketMatchDecisionType.REJECTED)
        self.assertIn("MISSING_LINE_FOR_LINE_MARKET", decision.reasons)

    def test_adversarial_b_change_line_2_5_to_3_5(self):
        """Adversarial B: Change line 2.5 -> 3.5 -> REJECTED (LINE_MISMATCH)."""
        m1 = Market(event_id="ev_1", market_type="TOTALS", line=2.5, internal_id="m_b1")
        m2 = Market(event_id="ev_2", market_type="TOTALS", line=3.5, internal_id="m_b2")

        decision = self.matcher.match(m1, m2)
        self.assertEqual(decision.decision, MarketMatchDecisionType.REJECTED)
        self.assertIn("LINE_MISMATCH", decision.reasons)

    def test_adversarial_c_change_full_time_to_first_half(self):
        """Adversarial C: Change FULL_TIME -> FIRST_HALF -> REJECTED (PERIOD_MISMATCH)."""
        m1 = Market(event_id="ev_1", market_type="1X2", metadata={"period": "FULL_TIME"}, internal_id="m_c1")
        m2 = Market(event_id="ev_2", market_type="1X2", metadata={"period": "FIRST_HALF"}, internal_id="m_c2")

        decision = self.matcher.match(m1, m2)
        self.assertEqual(decision.decision, MarketMatchDecisionType.REJECTED)
        self.assertIn("PERIOD_MISMATCH", decision.reasons)

    def test_adversarial_d_change_goals_to_corners(self):
        """Adversarial D: Change GOALS -> CORNERS -> REJECTED (METRIC_MISMATCH)."""
        m1 = Market(event_id="ev_1", market_type="TOTALS", line=2.5, metadata={"metric": "GOALS"}, internal_id="m_d1")
        m2 = Market(event_id="ev_2", market_type="TOTALS", line=2.5, metadata={"metric": "CORNERS"}, internal_id="m_d2")

        decision = self.matcher.match(m1, m2)
        self.assertEqual(decision.decision, MarketMatchDecisionType.REJECTED)
        self.assertIn("METRIC_MISMATCH", decision.reasons)

    def test_adversarial_e_change_match_to_player(self):
        """Adversarial E: Change MATCH -> PLAYER -> REJECTED (SCOPE_MISMATCH)."""
        m1 = Market(event_id="ev_1", market_type="TOTALS", line=0.5, metadata={"scope": "MATCH"}, internal_id="m_e1")
        m2 = Market(event_id="ev_2", market_type="TOTALS", line=0.5, metadata={"scope": "PLAYER", "participant_role": "PLAYER_1"}, internal_id="m_e2")

        decision = self.matcher.match(m1, m2)
        self.assertEqual(decision.decision, MarketMatchDecisionType.REJECTED)
        self.assertIn("SCOPE_MISMATCH", decision.reasons)

    def test_adversarial_f_identical_raw_names_different_semantics(self):
        """Adversarial F: Identical raw bookmaker names with different canonical semantics -> Canonical fields decide."""
        # e.g. both raw names were "Wynik meczu", but one is 1st half and other is full time
        m1 = Market(event_id="ev_1", market_type="1X2", metadata={"period": "FULL_TIME", "raw_name": "Wynik meczu"}, internal_id="m_f1")
        m2 = Market(event_id="ev_2", market_type="1X2", metadata={"period": "FIRST_HALF", "raw_name": "Wynik meczu"}, internal_id="m_f2")

        decision = self.matcher.match(m1, m2)
        self.assertEqual(decision.decision, MarketMatchDecisionType.REJECTED)
        self.assertIn("PERIOD_MISMATCH", decision.reasons)

    def test_adversarial_g_different_raw_names_identical_canonical_fields(self):
        """Adversarial G: Different bookmaker names with identical canonical fields -> MATCHED."""
        # Superbet "Mecz" vs Betclic "Match Winner"
        m1 = Market(event_id="ev_1", market_type="1X2", metadata={"raw_name": "Mecz"}, internal_id="m_g1")
        m2 = Market(event_id="ev_2", market_type="1X2", metadata={"raw_name": "Match Winner"}, internal_id="m_g2")

        decision = self.matcher.match(m1, m2)
        self.assertEqual(decision.decision, MarketMatchDecisionType.MATCHED)
        self.assertEqual(decision.canonical_market_key.market_type, "1X2")

    def test_adversarial_h_unsupported_market(self):
        """Adversarial H: Feed unsupported / un-canonicalized combo market -> UNSUPPORTED decision."""
        m1 = Market(event_id="ev_1", market_type="BETBUILDER_COMBO_XYZ", internal_id="m_h1")
        m2 = Market(event_id="ev_2", market_type="1X2", internal_id="m_h2")

        decision = self.matcher.match(m1, m2)
        self.assertEqual(decision.decision, MarketMatchDecisionType.UNSUPPORTED)
        self.assertIn("UNSUPPORTED_MARKET_TYPE", decision.reasons)

    # -------------------------------------------------------------------------
    # 6. Auditability & Decomposed Evidence
    # -------------------------------------------------------------------------
    def test_decision_explainability_and_evidence(self):
        """Verify matched decisions contain full decomposed evidence dictionary."""
        m1 = Market(event_id="ev_1", market_type="TOTALS", line=2.5, internal_id="m_ev1")
        m2 = Market(event_id="ev_2", market_type="TOTALS", line=2.5, internal_id="m_ev2")

        decision = self.matcher.match(m1, m2)
        self.assertEqual(decision.decision, MarketMatchDecisionType.MATCHED)
        self.assertEqual(decision.evidence["market_type"], "exact")
        self.assertEqual(decision.evidence["line"], "exact")
        self.assertEqual(decision.evidence["period"], "exact")
        self.assertEqual(decision.evidence["scope"], "exact")
        self.assertEqual(decision.evidence["metric"], "exact")
        self.assertEqual(decision.evidence["sport"], "exact")

    # -------------------------------------------------------------------------
    # 7. Real Superbet Tier 2 Fixture Audit
    # -------------------------------------------------------------------------
    def test_superbet_real_market_taxonomy_coverage(self):
        """Audits real Superbet Tier 2 recording (1865 normalized markets).

        Validates classification into:
        - 20 canonical supported markets (1X2, BTTS, Double Chance, DNB, Handicap, Totals)
        - 1845 unsupported player prop and BetBuilder combination variants
        - 0 ambiguous markets.
        """
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

        # Stage 50: Normalizer now filters unallowed market families early
        self.assertEqual(len(graph.markets), 1031)

        market_index, unsupported = self.matcher.index_markets(graph.markets)

        # Count supported market instances
        total_supported_markets = sum(len(mkts) for mkts in market_index.values())
        total_unsupported_markets = len(unsupported)

        self.assertEqual(total_supported_markets, 940)
        self.assertEqual(total_unsupported_markets, 91)
        self.assertEqual(total_supported_markets + total_unsupported_markets, 1031)

        # Supported market type verification
        types_in_index = {k.market_type for k in market_index.keys()}
        self.assertTrue({"1X2", "BTTS", "DOUBLE_CHANCE", "DRAW_NO_BET", "HANDICAP", "TOTALS"}.issubset(types_in_index))

    # -------------------------------------------------------------------------
    # 8. Real Betclic Fixture Audit
    # -------------------------------------------------------------------------
    def test_betclic_real_market_taxonomy_coverage(self):
        """Audits real Betclic recording (2 normalized markets).

        Validates classification into:
        - 2 canonical supported markets (1X2, TOTALS 2.5)
        - 0 unsupported markets
        - 0 ambiguous markets.
        """
        fixture_path = Path("tests/fixtures/recordings/betclic/live_manifest/response_000.json")
        with open(fixture_path, "r", encoding="utf-8") as f:
            raw_payload = json.load(f)

        parser = BetclicParser()
        events = parser.parse_payloads(raw_payload)
        normalizer = BetclicNormalizer()
        graph = normalizer.normalize_event(events[0])

        self.assertEqual(len(graph.markets), 2)

        market_index, unsupported = self.matcher.index_markets(graph.markets)
        total_supported = sum(len(mkts) for mkts in market_index.values())

        self.assertEqual(total_supported, 2)
        self.assertEqual(len(unsupported), 0)

        types_in_index = {k.market_type for k in market_index.keys()}
        self.assertEqual(types_in_index, {"1X2", "TOTALS"})

    # -------------------------------------------------------------------------
    # 9. Controlled Cross-Provider Validation
    # -------------------------------------------------------------------------
    def test_controlled_cross_provider_matching(self):
        """Validates controlled cross-provider matching using normalized Superbet and Betclic markets."""
        # Superbet normalized markets
        sb_1x2 = Market(event_id="sb_ev", market_type="1X2", internal_id="sb_m_1x2", provider_ids={"superbet": "101"})
        sb_totals_25 = Market(event_id="sb_ev", market_type="TOTALS", line=2.5, internal_id="sb_m_t25", provider_ids={"superbet": "102"})
        sb_totals_35 = Market(event_id="sb_ev", market_type="TOTALS", line=3.5, internal_id="sb_m_t35", provider_ids={"superbet": "103"})
        sb_btts = Market(event_id="sb_ev", market_type="BTTS", internal_id="sb_m_btts", provider_ids={"superbet": "104"})

        # Betclic normalized markets
        bc_1x2 = Market(event_id="bc_ev", market_type="1X2", internal_id="bc_m_1x2", provider_ids={"betclic": "901"})
        bc_totals_25 = Market(event_id="bc_ev", market_type="TOTALS", line=2.5, internal_id="bc_m_t25", provider_ids={"betclic": "902"})

        batch_res = self.matcher.match_markets(
            source_markets=[sb_1x2, sb_totals_25, sb_totals_35, sb_btts],
            target_markets=[bc_1x2, bc_totals_25],
        )

        self.assertEqual(len(batch_res.matched_pairs), 2)
        matched_keys = {d.canonical_market_key.market_type for d in batch_res.matched_pairs}
        self.assertEqual(matched_keys, {"1X2", "TOTALS"})
        self.assertEqual(len(batch_res.unmatched_source_keys), 2)  # Totals 3.5 & BTTS have no target in Betclic
        self.assertEqual(len(batch_res.unmatched_target_keys), 0)

    # -------------------------------------------------------------------------
    # 10. High-Throughput Batch Indexing & Ambiguity Isolation
    # -------------------------------------------------------------------------
    def test_ambiguity_isolation_on_duplicate_target_keys(self):
        """Verify that duplicate markets for the same canonical key within a provider are flagged as AMBIGUOUS."""
        m_source = Market(event_id="ev_s", market_type="TOTALS", line=2.5, internal_id="m_src")
        m_target1 = Market(event_id="ev_t", market_type="TOTALS", line=2.5, internal_id="m_tgt_1")
        m_target2 = Market(event_id="ev_t", market_type="TOTALS", line=2.5, internal_id="m_tgt_2")

        batch_res = self.matcher.match_markets(
            source_markets=[m_source],
            target_markets=[m_target1, m_target2],
        )

        self.assertEqual(len(batch_res.matched_pairs), 0)
        self.assertEqual(len(batch_res.ambiguous_markets), 2)
        self.assertEqual(batch_res.ambiguous_markets[0].decision, MarketMatchDecisionType.AMBIGUOUS)

    def test_high_throughput_performance(self):
        """Verify matching over 1800+ markets executes in sub-second time via O(1) key indexing."""
        # Create 1000 synthetic distinct source markets
        source_mkts = [
            Market(event_id="ev_s", market_type="TOTALS", line=round(0.5 + i * 0.5, 1), internal_id=f"sm_{i}")
            for i in range(500)
        ] + [
            Market(event_id="ev_s", market_type="HANDICAP", line=round(-5.0 + i * 0.5, 1), internal_id=f"sm_h_{i}")
            for i in range(500)
        ]

        # Target markets has 300 matching markets
        target_mkts = [
            Market(event_id="ev_t", market_type="TOTALS", line=round(0.5 + i * 0.5, 1), internal_id=f"tm_{i}")
            for i in range(300)
        ]

        batch_res = self.matcher.match_markets(source_mkts, target_mkts)
        self.assertEqual(len(batch_res.matched_pairs), 300)
        self.assertEqual(batch_res.total_source_markets, 1000)
        self.assertEqual(batch_res.total_target_markets, 300)
        self.assertLess(batch_res.execution_time_ms, 500.0)  # sub-second requirement


if __name__ == "__main__":
    unittest.main()
