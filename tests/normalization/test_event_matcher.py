"""
Unit & Integration Tests for Stage 4.4 Cross-Bookmaker Event Scoring & Match Decision Engine

Covers:
1. Exact match and minor formatting variants (MATCHED)
2. America MG case (AMBIGUOUS without external ID / MATCHED with external ID)
3. Hard safety vetoes:
   - Youth mismatch (Barcelona vs Barcelona U19 -> REJECTED)
   - Women mismatch (Chelsea vs Chelsea Women -> REJECTED)
   - Reserve mismatch (Porto vs Porto B -> REJECTED)
   - Sport mismatch (Football vs Basketball -> REJECTED)
4. Suffix compatibility when both sides share youth/reserve designations (Barcelona U19 vs Barcelona U19 -> MATCHED)
5. Orientation evaluation (Milan vs Inter vs Inter vs Milan -> ORIENTATION_SWAP)
6. Kickoff proximity curve (15m, 1h, midnight window, distant dates)
7. Missing vs conflicting metadata (neutral handling vs negative evidence)
8. Best-match ambiguity margin resolution (competing close candidates -> AMBIGUOUS)
9. Adversarial regression suite (A–H)
10. Property and invariant tests (symmetry, score decomposition, idempotence)
"""

import unittest
from domain.models import Event, Competition
from normalization.candidate_generator import EventCandidate
from normalization.matcher import (
    MatchDecisionType,
    OrientationType,
    SuffixCompatibility,
    MatcherConfig,
    SignalScore,
    MatchDecision,
    MatchResult,
    EventMatcher,
)


class TestEventMatcher(unittest.TestCase):

    def setUp(self):
        self.matcher = EventMatcher()

    def _make_candidate(self, s_id: str, t_id: str, keys=("KEY_1",)) -> EventCandidate:
        return EventCandidate(
            source_event_id=s_id,
            target_event_id=t_id,
            source_provider="superbet",
            target_provider="betclic",
            blocking_keys=tuple(keys),
            evidence={},
        )

    # -------------------------------------------------------------------------
    # 1. Exact Match & Minor Formatting
    # -------------------------------------------------------------------------
    def test_exact_match_success(self):
        """Verify identical event attributes produce MATCHED with high score >= 0.90."""
        s = Event(
            competition_id="comp_1",
            home_participant="Barcelona",
            away_participant="Real Madrid",
            scheduled_start="2026-08-25T20:00:00Z",
            external_ids={"betradar": "sr:match:101"},
            internal_id="ev_s1",
        )
        t = Event(
            competition_id="comp_2",
            home_participant="Barcelona",
            away_participant="Real Madrid",
            scheduled_start="2026-08-25T20:00:00Z",
            external_ids={"betradar": "sr:match:101"},
            internal_id="ev_t1",
        )
        c_s = Competition(name="La Liga", sport="Football", internal_id="comp_1")
        c_t = Competition(name="La Liga", sport="Football", internal_id="comp_2")

        cand = self._make_candidate("ev_s1", "ev_t1")
        decision = self.matcher.score_candidate(cand, s, t, c_s, c_t)

        self.assertEqual(decision.decision, MatchDecisionType.MATCHED)
        self.assertGreaterEqual(decision.total_score, 0.95)
        self.assertEqual(decision.orientation, OrientationType.NORMAL)
        self.assertEqual(decision.signals["external_id"].raw_score, 1.0)
        self.assertEqual(decision.signals["home_team"].raw_score, 1.0)
        self.assertEqual(decision.signals["away_team"].raw_score, 1.0)
        self.assertEqual(decision.signals["kickoff"].raw_score, 1.0)
        self.assertEqual(decision.signals["competition"].raw_score, 1.0)

    def test_minor_punctuation_formatting_success(self):
        """Verify minor punctuation differences (Paris Saint-Germain) match cleanly."""
        s = Event(
            competition_id="c1",
            home_participant="Paris Saint-Germain",
            away_participant="Olympique Marseille",
            scheduled_start="2026-08-25T20:00:00Z",
            internal_id="ev_s2",
        )
        t = Event(
            competition_id="c2",
            home_participant="Paris Saint Germain",
            away_participant="Marseille",
            scheduled_start="2026-08-25T20:00:00Z",
            internal_id="ev_t2",
        )

        cand = self._make_candidate("ev_s2", "ev_t2")
        decision = self.matcher.score_candidate(cand, s, t)
        self.assertEqual(decision.decision, MatchDecisionType.MATCHED)
        self.assertGreaterEqual(decision.total_score, 0.80)

    # -------------------------------------------------------------------------
    # 2. Suffix Safety Vetoes (Youth, Women, Reserves)
    # -------------------------------------------------------------------------
    def test_youth_team_mismatch_veto(self):
        """Verify Barcelona vs Barcelona U19 triggers hard veto REJECTED with score 0.0."""
        s = Event(competition_id="c1", home_participant="Barcelona", away_participant="Real Madrid", internal_id="ev_s3")
        t = Event(competition_id="c2", home_participant="Barcelona U19", away_participant="Real Madrid U19", internal_id="ev_t3")

        cand = self._make_candidate("ev_s3", "ev_t3")
        decision = self.matcher.score_candidate(cand, s, t)

        self.assertEqual(decision.decision, MatchDecisionType.REJECTED)
        self.assertEqual(decision.total_score, 0.0)
        self.assertTrue(any("age_group_mismatch" in r for r in decision.veto_reasons))

    def test_women_team_mismatch_veto(self):
        """Verify Chelsea vs Chelsea Women triggers hard veto REJECTED with score 0.0."""
        s = Event(competition_id="c1", home_participant="Chelsea", away_participant="Arsenal", internal_id="ev_s4")
        t = Event(competition_id="c2", home_participant="Chelsea Women", away_participant="Arsenal Women", internal_id="ev_t4")

        cand = self._make_candidate("ev_s4", "ev_t4")
        decision = self.matcher.score_candidate(cand, s, t)

        self.assertEqual(decision.decision, MatchDecisionType.REJECTED)
        self.assertEqual(decision.total_score, 0.0)
        self.assertTrue(any("gender_mismatch" in r for r in decision.veto_reasons))

    def test_reserve_team_mismatch_veto(self):
        """Verify Porto vs Porto B triggers hard veto REJECTED with score 0.0."""
        s = Event(competition_id="c1", home_participant="Porto", away_participant="Benfica", internal_id="ev_s5")
        t = Event(competition_id="c2", home_participant="Porto B", away_participant="Benfica B", internal_id="ev_t5")

        cand = self._make_candidate("ev_s5", "ev_t5")
        decision = self.matcher.score_candidate(cand, s, t)

        self.assertEqual(decision.decision, MatchDecisionType.REJECTED)
        self.assertEqual(decision.total_score, 0.0)
        self.assertTrue(any("reserve_mismatch" in r for r in decision.veto_reasons))

    def test_same_youth_teams_compatible(self):
        """Verify Barcelona U19 vs Barcelona U19 matches without penalty."""
        s = Event(
            competition_id="c1",
            home_participant="Barcelona U19",
            away_participant="Valencia U19",
            scheduled_start="2026-08-25T16:00:00Z",
            internal_id="ev_s6",
        )
        t = Event(
            competition_id="c2",
            home_participant="Barcelona U19",
            away_participant="Valencia U19",
            scheduled_start="2026-08-25T16:00:00Z",
            internal_id="ev_t6",
        )

        cand = self._make_candidate("ev_s6", "ev_t6")
        decision = self.matcher.score_candidate(cand, s, t)
        self.assertEqual(decision.decision, MatchDecisionType.MATCHED)
        self.assertEqual(len(decision.veto_reasons), 0)

    # -------------------------------------------------------------------------
    # 3. Sport Mismatch Veto
    # -------------------------------------------------------------------------
    def test_sport_mismatch_veto(self):
        """Verify Football vs Basketball triggers hard veto REJECTED with score 0.0."""
        s = Event(competition_id="c1", home_participant="Real Madrid", away_participant="Barcelona", internal_id="ev_s7")
        t = Event(competition_id="c2", home_participant="Real Madrid", away_participant="Barcelona", internal_id="ev_t7")
        c_s = Competition(name="La Liga", sport="Football", internal_id="c1")
        c_t = Competition(name="EuroLeague", sport="Basketball", internal_id="c2")

        cand = self._make_candidate("ev_s7", "ev_t7")
        decision = self.matcher.score_candidate(cand, s, t, c_s, c_t)

        self.assertEqual(decision.decision, MatchDecisionType.REJECTED)
        self.assertEqual(decision.total_score, 0.0)
        self.assertTrue(any("sport_mismatch" in r for r in decision.veto_reasons))

    # -------------------------------------------------------------------------
    # 4. America MG Analysis (Inconclusive Evidence -> AMBIGUOUS)
    # -------------------------------------------------------------------------
    def test_america_mg_without_external_id_is_ambiguous(self):
        """Verify generic America vs América de Natal without external ID evaluates to AMBIGUOUS."""
        s = Event(
            competition_id="c1",
            home_participant="America",
            away_participant="Athletic Club",
            scheduled_start="2026-08-16 21:30:00",
            internal_id="ev_s_am",
        )
        t = Event(
            competition_id="c2",
            home_participant="América de Natal",
            away_participant="Athletic Club",
            scheduled_start="2026-08-16T21:30:00Z",
            internal_id="ev_t_am",
        )

        cand = self._make_candidate("ev_s_am", "ev_t_am")
        decision = self.matcher.score_candidate(cand, s, t)

        # Without external ID, partial token similarity yields score ~0.70-0.80 -> AMBIGUOUS
        self.assertEqual(decision.decision, MatchDecisionType.AMBIGUOUS)
        self.assertGreaterEqual(decision.total_score, 0.60)
        self.assertLess(decision.total_score, 0.85)

    def test_america_mg_with_external_id_is_matched(self):
        """Verify America MG with matching external ID upgrades to MATCHED."""
        s = Event(
            competition_id="c1",
            home_participant="America MG",
            away_participant="Athletic Club MG",
            scheduled_start="2026-08-16 21:30:00",
            external_ids={"betradar": "sr:match:888"},
            internal_id="ev_s_am2",
        )
        t = Event(
            competition_id="c2",
            home_participant="América Mineiro (MG)",
            away_participant="Athletic Club",
            scheduled_start="2026-08-16T21:30:00Z",
            external_ids={"betradar": "sr:match:888"},
            internal_id="ev_t_am2",
        )

        cand = self._make_candidate("ev_s_am2", "ev_t_am2")
        decision = self.matcher.score_candidate(cand, s, t)
        self.assertEqual(decision.decision, MatchDecisionType.MATCHED)
        self.assertGreaterEqual(decision.total_score, 0.80)

    # -------------------------------------------------------------------------
    # 5. Orientation Swap Evaluation
    # -------------------------------------------------------------------------
    def test_orientation_swap_detection(self):
        """Verify Milan vs Inter vs Inter vs Milan is marked ORIENTATION_SWAP."""
        s = Event(
            competition_id="c1",
            home_participant="Milan",
            away_participant="Inter",
            scheduled_start="2026-08-25T19:45:00Z",
            internal_id="ev_s_swap",
        )
        t = Event(
            competition_id="c2",
            home_participant="Inter",
            away_participant="Milan",
            scheduled_start="2026-08-25T19:45:00Z",
            internal_id="ev_t_swap",
        )

        cand = self._make_candidate("ev_s_swap", "ev_t_swap")
        decision = self.matcher.score_candidate(cand, s, t)

        self.assertEqual(decision.orientation, OrientationType.ORIENTATION_SWAP)
        self.assertIn("orientation_swap_detected", decision.warnings)
        self.assertGreaterEqual(decision.total_score, 0.80)

    # -------------------------------------------------------------------------
    # 6. Kickoff Proximity & Distant Dates
    # -------------------------------------------------------------------------
    def test_distant_dates_rejected(self):
        """Verify identical teams 30 days apart evaluate to REJECTED with score 0.0."""
        s = Event(competition_id="c1", home_participant="Liverpool", away_participant="Everton", scheduled_start="2026-08-01T15:00:00Z", internal_id="ev_s_dist")
        t = Event(competition_id="c2", home_participant="Liverpool", away_participant="Everton", scheduled_start="2026-09-01T15:00:00Z", internal_id="ev_t_dist")

        cand = self._make_candidate("ev_s_dist", "ev_t_dist")
        decision = self.matcher.score_candidate(cand, s, t)
        self.assertEqual(decision.decision, MatchDecisionType.REJECTED)
        self.assertEqual(decision.total_score, 0.0)
        self.assertTrue(any("kickoff_mismatch" in r for r in decision.veto_reasons))

    def test_midnight_drift_window_matched(self):
        """Verify events at 23:30 and 00:30 UTC (next day) score kickoff >= 0.85."""
        s = Event(competition_id="c1", home_participant="Flamengo", away_participant="Vasco", scheduled_start="2026-08-16T23:30:00Z", internal_id="ev_s_mid")
        t = Event(competition_id="c2", home_participant="Flamengo", away_participant="Vasco", scheduled_start="2026-08-17T00:30:00Z", internal_id="ev_t_mid")

        cand = self._make_candidate("ev_s_mid", "ev_t_mid")
        decision = self.matcher.score_candidate(cand, s, t)
        self.assertEqual(decision.decision, MatchDecisionType.MATCHED)
        self.assertEqual(decision.signals["kickoff"].raw_score, 0.95)

    # -------------------------------------------------------------------------
    # 7. Best-Match Ambiguity Margin Resolution
    # -------------------------------------------------------------------------
    def test_competing_close_candidates_trigger_ambiguity(self):
        """Verify two close candidates for the same source event trigger AMBIGUOUS."""
        s = Event(competition_id="c1", home_participant="Rapid", away_participant="Austria", scheduled_start="2026-08-25T17:00:00Z", internal_id="ev_s_comp")
        t1 = Event(competition_id="c2", home_participant="SK Rapid", away_participant="FK Austria", scheduled_start="2026-08-25T17:00:00Z", internal_id="ev_t_comp1")
        t2 = Event(competition_id="c2", home_participant="Rapid FC", away_participant="Austria FC", scheduled_start="2026-08-25T17:00:00Z", internal_id="ev_t_comp2")

        cand1 = self._make_candidate("ev_s_comp", "ev_t_comp1")
        cand2 = self._make_candidate("ev_s_comp", "ev_t_comp2")

        sources = {"ev_s_comp": s}
        targets = {"ev_t_comp1": t1, "ev_t_comp2": t2}

        result = self.matcher.match_candidates([cand1, cand2], sources, targets)
        self.assertEqual(result.total_scored, 2)
        # Both close candidates should be marked AMBIGUOUS rather than guessing
        self.assertEqual(result.matched_count, 0)
        self.assertEqual(result.ambiguous_count, 2)
        self.assertTrue(any("competing_close_candidate" in w for w in result.decisions[0].warnings))

    # -------------------------------------------------------------------------
    # 8. Adversarial Regression Suite (A–H)
    # -------------------------------------------------------------------------
    def test_adversarial_a_suffix_protection_active(self):
        """Adversarial A: Barcelona vs Barcelona U19 must be REJECTED."""
        s = Event(competition_id="c1", home_participant="Barcelona", away_participant="Real Madrid", internal_id="ev_a")
        t = Event(competition_id="c2", home_participant="Barcelona U19", away_participant="Real Madrid", internal_id="ev_at")
        cand = self._make_candidate("ev_a", "ev_at")
        decision = self.matcher.score_candidate(cand, s, t)
        self.assertEqual(decision.decision, MatchDecisionType.REJECTED)

    def test_adversarial_b_token_overlap_not_sufficient_for_youth(self):
        """Adversarial B: Token overlap > 0.6 cannot override youth mismatch."""
        s = Event(competition_id="c1", home_participant="Arsenal", away_participant="Chelsea", internal_id="ev_b")
        t = Event(competition_id="c2", home_participant="Arsenal U21", away_participant="Chelsea U21", internal_id="ev_bt")
        cand = self._make_candidate("ev_b", "ev_bt")
        decision = self.matcher.score_candidate(cand, s, t)
        self.assertEqual(decision.decision, MatchDecisionType.REJECTED)
        self.assertEqual(decision.total_score, 0.0)

    def test_adversarial_c_external_id_mismatch_no_crash(self):
        """Adversarial C: Conflicting external IDs reduce confidence without exception."""
        s = Event(competition_id="c1", home_participant="Lyon", away_participant="Monaco", external_ids={"betradar": "111"}, internal_id="ev_c")
        t = Event(competition_id="c2", home_participant="Lyon", away_participant="Monaco", external_ids={"betradar": "222"}, internal_id="ev_ct")
        cand = self._make_candidate("ev_c", "ev_ct")
        decision = self.matcher.score_candidate(cand, s, t)
        self.assertEqual(decision.signals["external_id"].raw_score, 0.0)
        self.assertIn("conflicting_external_ids_detected", decision.warnings)

    def test_adversarial_d_realistic_kickoff_difference_rewarded(self):
        """Adversarial D: 10-minute kickoff discrepancy receives score >= 0.95."""
        s = Event(competition_id="c1", home_participant="Sevilla", away_participant="Betis", scheduled_start="2026-08-25T19:00:00Z", internal_id="ev_d")
        t = Event(competition_id="c2", home_participant="Sevilla", away_participant="Betis", scheduled_start="2026-08-25T19:10:00Z", internal_id="ev_dt")
        cand = self._make_candidate("ev_d", "ev_dt")
        decision = self.matcher.score_candidate(cand, s, t)
        self.assertGreaterEqual(decision.signals["kickoff"].raw_score, 0.95)

    def test_adversarial_e_same_teams_different_date_rejected(self):
        """Adversarial E: Same teams 30 days apart must be REJECTED with score 0.0."""
        s = Event(competition_id="c1", home_participant="Ajax", away_participant="Feyenoord", scheduled_start="2026-08-01T14:00:00Z", internal_id="ev_e")
        t = Event(competition_id="c2", home_participant="Ajax", away_participant="Feyenoord", scheduled_start="2026-09-01T14:00:00Z", internal_id="ev_et")
        cand = self._make_candidate("ev_e", "ev_et")
        decision = self.matcher.score_candidate(cand, s, t)
        self.assertEqual(decision.decision, MatchDecisionType.REJECTED)
        self.assertEqual(decision.total_score, 0.0)
        self.assertTrue(any("kickoff_mismatch" in r for r in decision.veto_reasons))

    def test_adversarial_f_ambiguity_margin_downgrade(self):
        """Adversarial F: Close second match causes top match to become AMBIGUOUS."""
        s = Event(competition_id="c1", home_participant="Inter", away_participant="Juventus", scheduled_start="2026-08-25T19:45:00Z", internal_id="ev_f")
        t1 = Event(competition_id="c2", home_participant="Inter Milan", away_participant="Juventus FC", scheduled_start="2026-08-25T19:45:00Z", internal_id="ev_ft1")
        t2 = Event(competition_id="c2", home_participant="FC Internazionale", away_participant="Juventus", scheduled_start="2026-08-25T19:45:00Z", internal_id="ev_ft2")
        cand1 = self._make_candidate("ev_f", "ev_ft1")
        cand2 = self._make_candidate("ev_f", "ev_ft2")
        res = self.matcher.match_candidates([cand1, cand2], {"ev_f": s}, {"ev_ft1": t1, "ev_ft2": t2})
        self.assertEqual(res.matched_count, 0)
        self.assertEqual(res.ambiguous_count, 2)

    def test_adversarial_g_ambiguous_output_valid(self):
        """Adversarial G: Mid-range score (0.72) produces AMBIGUOUS, not forced binary."""
        s = Event(competition_id="c1", home_participant="America", away_participant="Athletic Club", scheduled_start="2026-08-16 20:00:00", internal_id="ev_g")
        t = Event(competition_id="c2", home_participant="América de Natal", away_participant="Athletic Club", scheduled_start="2026-08-16T20:00:00Z", internal_id="ev_gt")
        cand = self._make_candidate("ev_g", "ev_gt")
        decision = self.matcher.score_candidate(cand, s, t)
        self.assertEqual(decision.decision, MatchDecisionType.AMBIGUOUS)

    def test_adversarial_h_unknown_metadata_is_neutral(self):
        """Adversarial H: Missing external IDs / competition treated neutrally (0.5), not veto."""
        s = Event(competition_id="missing_c", home_participant="Porto", away_participant="Braga", scheduled_start="2026-08-25T20:00:00Z", internal_id="ev_h")
        t = Event(competition_id="missing_c", home_participant="Porto", away_participant="Braga", scheduled_start="2026-08-25T20:00:00Z", internal_id="ev_ht")
        cand = self._make_candidate("ev_h", "ev_ht")
        decision = self.matcher.score_candidate(cand, s, t)
        self.assertEqual(decision.decision, MatchDecisionType.MATCHED)
        self.assertEqual(decision.signals["external_id"].raw_score, 0.5)
        self.assertEqual(decision.signals["competition"].raw_score, 0.5)

    # -------------------------------------------------------------------------
    # 9. Property & Invariant Tests
    # -------------------------------------------------------------------------
    def test_property_self_comparison_score_one(self):
        """Property 1: Comparing event with exact clone produces total_score >= 0.95."""
        ev = Event(
            competition_id="c1",
            home_participant="Bayern Munich",
            away_participant="Dortmund",
            scheduled_start="2026-08-25T17:30:00Z",
            external_ids={"betradar": "sr:match:777"},
            internal_id="ev_prop1",
        )
        comp = Competition(name="Bundesliga", sport="Football", internal_id="c1")
        cand = self._make_candidate("ev_prop1", "ev_prop1")
        decision = self.matcher.score_candidate(cand, ev, ev, comp, comp)
        self.assertEqual(decision.decision, MatchDecisionType.MATCHED)
        self.assertEqual(decision.total_score, 1.0)

    def test_property_score_explainability_decomposition(self):
        """Property 2: Total score equals sum of weighted signal scores (when no veto)."""
        s = Event(competition_id="c1", home_participant="Arsenal", away_participant="Chelsea", scheduled_start="2026-08-25T15:00:00Z", internal_id="ev_p2s")
        t = Event(competition_id="c2", home_participant="Arsenal FC", away_participant="Chelsea FC", scheduled_start="2026-08-25T15:00:00Z", internal_id="ev_p2t")
        cand = self._make_candidate("ev_p2s", "ev_p2t")
        decision = self.matcher.score_candidate(cand, s, t)
        computed_sum = sum(sig.weighted_score for sig in decision.signals.values())
        self.assertAlmostEqual(decision.total_score, computed_sum, places=3)

    def test_property_idempotence(self):
        """Property 3: Repeated scoring produces identical results."""
        s = Event(competition_id="c1", home_participant="Monaco", away_participant="Nice", scheduled_start="2026-08-25T20:00:00Z", internal_id="ev_p3s")
        t = Event(competition_id="c2", home_participant="AS Monaco", away_participant="OGC Nice", scheduled_start="2026-08-25T20:00:00Z", internal_id="ev_p3t")
        cand = self._make_candidate("ev_p3s", "ev_p3t")
        d1 = self.matcher.score_candidate(cand, s, t)
        d2 = self.matcher.score_candidate(cand, s, t)
        self.assertEqual(d1.total_score, d2.total_score)
        self.assertEqual(d1.decision, d2.decision)


if __name__ == "__main__":
    unittest.main()
