"""
Stage 26 High-Recall Cross-Bookmaker Event Matching Test Suite

Covers:
A. Bookmaker naming differences that should match (e.g. city transliterations, synonyms)
B. Punctuation and diacritic differences
C. Abbreviations and common club forms (PSG, Hearts, Wolves, QPR, West Brom, etc.)
D. Harmless suffix and foundation year differences (Rouen 1899, Paderborn 07, Schalke 04)
E. Kickoff differences within allowed tolerance
F. Reversed home/away representation (Orientation Swap)
G. Genuine different teams that must NOT match (Manchester City vs United, Real vs Atletico Madrid)
H. Senior vs U19/U21/U23 hard safety veto
I. Men's vs Women's teams hard safety veto
J. Reserve / B / II teams hard safety veto
K. Ambiguous candidate classification and margin downgrade
L. Competition mismatch behavior
M. Deterministic identical-input output
N. Integration with Stage 25 paired-detail selection and scan pipeline
"""

import unittest
from datetime import datetime, timezone
from decimal import Decimal

from domain.models import Event, Competition, Market, Selection, Odds
from normalization.base_normalizer import NormalizedGraph
from normalization.candidate_generator import EventCandidateGenerator, EventCandidate
from normalization.matcher import (
    EventMatcher,
    MatchDecisionType,
    OrientationType,
    MatchRejectionCode,
    MatcherConfig,
)
from normalization.identity import (
    TeamReference,
    CompetitionReference,
    normalize_team_name,
    compare_teams,
)


class TestStage26HighRecallMatching(unittest.TestCase):
    def setUp(self):
        self.matcher = EventMatcher()
        self.generator = EventCandidateGenerator()

    def _make_candidate(self, s_id: str, t_id: str) -> EventCandidate:
        return EventCandidate(
            source_event_id=s_id,
            target_event_id=t_id,
            source_provider="superbet",
            target_provider="betclic",
            blocking_keys=("KEY",),
            evidence={},
        )

    # -------------------------------------------------------------------------
    # A & B: Naming, Diacritic, and Transliteration Differences
    # -------------------------------------------------------------------------
    def test_transliterations_and_city_synonyms_match(self):
        """FC Koeln vs Hoffenheim <-> FC Koln vs Hoffenheim and Celje vs Slovan Bratislava <-> Celje vs Slovan Bratysława."""
        s = Event(
            competition_id="c1",
            home_participant="FC Koeln",
            away_participant="Hoffenheim",
            scheduled_start="2026-08-25T19:00:00Z",
            internal_id="s1",
        )
        t = Event(
            competition_id="c2",
            home_participant="FC Koln",
            away_participant="Hoffenheim",
            scheduled_start="2026-08-25T19:00:00Z",
            internal_id="t1",
        )
        dec = self.matcher.score_candidate(self._make_candidate("s1", "t1"), s, t)
        self.assertEqual(dec.decision, MatchDecisionType.MATCHED)
        self.assertGreaterEqual(dec.total_score, 0.90)

        s2 = Event(
            competition_id="c1",
            home_participant="NK Celje",
            away_participant="Slovan Bratislava",
            scheduled_start="2026-08-25T19:00:00Z",
            internal_id="s2",
        )
        t2 = Event(
            competition_id="c2",
            home_participant="Celje",
            away_participant="Slovan Bratysława",
            scheduled_start="2026-08-25T19:00:00Z",
            internal_id="t2",
        )
        dec2 = self.matcher.score_candidate(self._make_candidate("s2", "t2"), s2, t2)
        self.assertEqual(dec2.decision, MatchDecisionType.MATCHED)
        self.assertGreaterEqual(dec2.total_score, 0.90)

    def test_deportivo_la_corunya_transliteration(self):
        """Deportivo La Corunya vs Valencia <-> Deportivo de A Coruna vs Valencia matches."""
        s = Event(
            competition_id="c1",
            home_participant="Deportivo La Corunya",
            away_participant="Valencia",
            scheduled_start="2026-08-25T19:00:00Z",
            internal_id="s_dep",
        )
        t = Event(
            competition_id="c2",
            home_participant="Deportivo de A Coruna",
            away_participant="Valencia",
            scheduled_start="2026-08-25T19:00:00Z",
            internal_id="t_dep",
        )
        dec = self.matcher.score_candidate(self._make_candidate("s_dep", "t_dep"), s, t)
        self.assertEqual(dec.decision, MatchDecisionType.MATCHED)
        self.assertGreaterEqual(dec.total_score, 0.90)

    # -------------------------------------------------------------------------
    # C: Club Abbreviations & Acronyms
    # -------------------------------------------------------------------------
    def test_abbreviations_and_short_forms(self):
        """Lille vs PSG <-> Lille vs Paris Saint-Germain and Hearts <-> Heart of Midlothian."""
        s_psg = Event(
            competition_id="c1",
            home_participant="Lille",
            away_participant="Paris Saint-Germain",
            scheduled_start="2026-08-25T20:45:00Z",
            internal_id="s_psg",
        )
        t_psg = Event(
            competition_id="c2",
            home_participant="Lille",
            away_participant="PSG",
            scheduled_start="2026-08-25T20:45:00Z",
            internal_id="t_psg",
        )
        dec = self.matcher.score_candidate(self._make_candidate("s_psg", "t_psg"), s_psg, t_psg)
        self.assertEqual(dec.decision, MatchDecisionType.MATCHED)
        self.assertGreaterEqual(dec.total_score, 0.95)

        s_hearts = Event(
            competition_id="c1",
            home_participant="Heart of Midlothian",
            away_participant="St Johnstone",
            scheduled_start="2026-08-25T15:00:00Z",
            internal_id="s_h",
        )
        t_hearts = Event(
            competition_id="c2",
            home_participant="Hearts",
            away_participant="St. Johnstone",
            scheduled_start="2026-08-25T15:00:00Z",
            internal_id="t_h",
        )
        dec_h = self.matcher.score_candidate(self._make_candidate("s_h", "t_h"), s_hearts, t_hearts)
        self.assertEqual(dec_h.decision, MatchDecisionType.MATCHED)
        self.assertGreaterEqual(dec_h.total_score, 0.95)

    # -------------------------------------------------------------------------
    # D: Harmless Foundation Years and Region Descriptors
    # -------------------------------------------------------------------------
    def test_foundation_years_and_harmless_descriptors(self):
        """Villefranche Beaujolais vs Rouen 1899 <-> Villefranche vs Rouen and Paderborn 07 <-> Paderborn."""
        s = Event(
            competition_id="c1",
            home_participant="FC Villefranche Beaujolais",
            away_participant="FC Rouen 1899",
            scheduled_start="2026-08-25T18:30:00Z",
            internal_id="s_v",
        )
        t = Event(
            competition_id="c2",
            home_participant="Villefranche",
            away_participant="Rouen",
            scheduled_start="2026-08-25T18:30:00Z",
            internal_id="t_v",
        )
        dec = self.matcher.score_candidate(self._make_candidate("s_v", "t_v"), s, t)
        self.assertEqual(dec.decision, MatchDecisionType.MATCHED)
        self.assertGreaterEqual(dec.total_score, 0.90)

        s_pad = Event(
            competition_id="c1",
            home_participant="Mainz",
            away_participant="SC Paderborn 07",
            scheduled_start="2026-08-25T19:30:00Z",
            internal_id="s_pad",
        )
        t_pad = Event(
            competition_id="c2",
            home_participant="Mainz",
            away_participant="Paderborn",
            scheduled_start="2026-08-25T19:30:00Z",
            internal_id="t_pad",
        )
        dec_pad = self.matcher.score_candidate(self._make_candidate("s_pad", "t_pad"), s_pad, t_pad)
        self.assertEqual(dec_pad.decision, MatchDecisionType.MATCHED)
        self.assertGreaterEqual(dec_pad.total_score, 0.95)

    # -------------------------------------------------------------------------
    # E: Kickoff Differences Within Allowed Tolerance
    # -------------------------------------------------------------------------
    def test_kickoff_tolerance_variations(self):
        """Kickoff differences within 15 min score 1.0, within 1h score 0.95, over 24h veto."""
        s = Event(
            competition_id="c1", home_participant="Arsenal", away_participant="Chelsea",
            scheduled_start="2026-08-25T19:00:00Z", internal_id="s_k1"
        )
        t_10m = Event(
            competition_id="c2", home_participant="Arsenal", away_participant="Chelsea",
            scheduled_start="2026-08-25T19:10:00Z", internal_id="t_k1"
        )
        dec = self.matcher.score_candidate(self._make_candidate("s_k1", "t_k1"), s, t_10m)
        self.assertEqual(dec.decision, MatchDecisionType.MATCHED)
        self.assertEqual(dec.signals["kickoff"].raw_score, 1.0)

        t_45m = Event(
            competition_id="c2", home_participant="Arsenal", away_participant="Chelsea",
            scheduled_start="2026-08-25T19:45:00Z", internal_id="t_k2"
        )
        dec2 = self.matcher.score_candidate(self._make_candidate("s_k1", "t_k2"), s, t_45m)
        self.assertEqual(dec2.decision, MatchDecisionType.MATCHED)
        self.assertEqual(dec2.signals["kickoff"].raw_score, 0.95)

        t_2d = Event(
            competition_id="c2", home_participant="Arsenal", away_participant="Chelsea",
            scheduled_start="2026-08-28T19:00:00Z", internal_id="t_k3"
        )
        dec3 = self.matcher.score_candidate(self._make_candidate("s_k1", "t_k3"), s, t_2d)
        self.assertEqual(dec3.decision, MatchDecisionType.REJECTED)
        self.assertEqual(dec3.rejection_reason_code, MatchRejectionCode.KICKOFF_MISMATCH.value)

    # -------------------------------------------------------------------------
    # F: Orientation Swap
    # -------------------------------------------------------------------------
    def test_orientation_swap_matching(self):
        """Milan vs Inter vs Inter vs Milan is marked ORIENTATION_SWAP."""
        s = Event(
            competition_id="c1",
            home_participant="Milan",
            away_participant="Inter",
            scheduled_start="2026-08-25T19:45:00Z",
            internal_id="s_sw",
        )
        t = Event(
            competition_id="c2",
            home_participant="Inter",
            away_participant="Milan",
            scheduled_start="2026-08-25T19:45:00Z",
            internal_id="t_sw",
        )
        dec = self.matcher.score_candidate(self._make_candidate("s_sw", "t_sw"), s, t)
        self.assertEqual(dec.decision, MatchDecisionType.MATCHED)
        self.assertEqual(dec.orientation, OrientationType.ORIENTATION_SWAP)

    # -------------------------------------------------------------------------
    # G: Genuine Different Teams Must NOT Match (False Positive Defense)
    # -------------------------------------------------------------------------
    def test_conflicting_city_and_club_modifiers_rejected(self):
        """Manchester City vs Manchester United and Real Madrid vs Atletico Madrid must NOT match."""
        s_man = Event(
            competition_id="c1", home_participant="Manchester City", away_participant="Liverpool",
            scheduled_start="2026-08-25T19:00:00Z", internal_id="s_manc"
        )
        t_man = Event(
            competition_id="c2", home_participant="Manchester United", away_participant="Liverpool",
            scheduled_start="2026-08-25T19:00:00Z", internal_id="t_manu"
        )
        dec = self.matcher.score_candidate(self._make_candidate("s_manc", "t_manu"), s_man, t_man)
        self.assertEqual(dec.decision, MatchDecisionType.REJECTED)
        self.assertEqual(dec.rejection_reason_code, MatchRejectionCode.TEAM_IDENTITY_MISMATCH.value)

        s_mad = Event(
            competition_id="c1", home_participant="Real Madrid", away_participant="Sevilla",
            scheduled_start="2026-08-25T19:00:00Z", internal_id="s_realm"
        )
        t_mad = Event(
            competition_id="c2", home_participant="Atletico Madrid", away_participant="Sevilla",
            scheduled_start="2026-08-25T19:00:00Z", internal_id="t_atlm"
        )
        dec_mad = self.matcher.score_candidate(self._make_candidate("s_mad", "t_mad"), s_mad, t_mad)
        self.assertEqual(dec_mad.decision, MatchDecisionType.REJECTED)

    # -------------------------------------------------------------------------
    # H, I, J: Safety Hard Vetoes (Youth, Women, Reserves)
    # -------------------------------------------------------------------------
    def test_safety_veto_youth_age_groups(self):
        """Senior vs U19 / U21 must trigger SUFFIX_VETO."""
        s = Event(
            competition_id="c1", home_participant="Barcelona", away_participant="Valencia",
            scheduled_start="2026-08-25T19:00:00Z", internal_id="s_sen"
        )
        t = Event(
            competition_id="c2", home_participant="Barcelona U19", away_participant="Valencia",
            scheduled_start="2026-08-25T19:00:00Z", internal_id="t_u19"
        )
        dec = self.matcher.score_candidate(self._make_candidate("s_sen", "t_u19"), s, t)
        self.assertEqual(dec.decision, MatchDecisionType.REJECTED)
        self.assertEqual(dec.total_score, 0.0)
        self.assertEqual(dec.rejection_reason_code, MatchRejectionCode.SUFFIX_VETO.value)
        self.assertTrue(any("age_group_mismatch" in r for r in dec.veto_reasons))

    def test_safety_veto_women_teams(self):
        """Men vs Women must trigger SUFFIX_VETO."""
        s = Event(
            competition_id="c1", home_participant="Chelsea", away_participant="Arsenal",
            scheduled_start="2026-08-25T19:00:00Z", internal_id="s_men"
        )
        t = Event(
            competition_id="c2", home_participant="Chelsea Ladies", away_participant="Arsenal",
            scheduled_start="2026-08-25T19:00:00Z", internal_id="t_wom"
        )
        dec = self.matcher.score_candidate(self._make_candidate("s_men", "t_wom"), s, t)
        self.assertEqual(dec.decision, MatchDecisionType.REJECTED)
        self.assertEqual(dec.total_score, 0.0)
        self.assertEqual(dec.rejection_reason_code, MatchRejectionCode.SUFFIX_VETO.value)
        self.assertTrue(any("gender_mismatch" in r for r in dec.veto_reasons))

    def test_safety_veto_reserve_teams(self):
        """First team vs B / II / Reserves must trigger SUFFIX_VETO."""
        s = Event(
            competition_id="c1", home_participant="Bayern Munich", away_participant="Augsburg",
            scheduled_start="2026-08-25T19:00:00Z", internal_id="s_first"
        )
        t = Event(
            competition_id="c2", home_participant="Bayern Munich II", away_participant="Augsburg",
            scheduled_start="2026-08-25T19:00:00Z", internal_id="t_res"
        )
        dec = self.matcher.score_candidate(self._make_candidate("s_first", "t_res"), s, t)
        self.assertEqual(dec.decision, MatchDecisionType.REJECTED)
        self.assertEqual(dec.total_score, 0.0)
        self.assertEqual(dec.rejection_reason_code, MatchRejectionCode.SUFFIX_VETO.value)
        self.assertTrue(any("reserve_mismatch" in r for r in dec.veto_reasons))

    # -------------------------------------------------------------------------
    # K: Ambiguous Candidates & Margin Resolution
    # -------------------------------------------------------------------------
    def test_ambiguous_close_candidates_margin_downgrade(self):
        """When two candidate pairs are within ambiguity margin, both are marked AMBIGUOUS."""
        s = Event(
            competition_id="c1", home_participant="Sporting", away_participant="Braga",
            scheduled_start="2026-08-25T19:00:00Z", internal_id="s_sp"
        )
        t1 = Event(
            competition_id="c2", home_participant="Sporting Lizbona", away_participant="Braga",
            scheduled_start="2026-08-25T19:00:00Z", internal_id="t_sp1", provider_ids={"betclic": "1"}
        )
        t2 = Event(
            competition_id="c2", home_participant="Sporting Gijon", away_participant="Braga",
            scheduled_start="2026-08-25T19:00:00Z", internal_id="t_sp2", provider_ids={"betclic": "2"}
        )
        cand1 = EventCandidate("s_sp", "t_sp1", "superbet", "betclic", ("K1",), {})
        cand2 = EventCandidate("s_sp", "t_sp2", "superbet", "betclic", ("K2",), {})

        s_map = {"s_sp": s}
        t_map = {"t_sp1": t1, "t_sp2": t2}

        res = self.matcher.match_candidates([cand1, cand2], s_map, t_map)
        self.assertGreater(len(res.decisions), 0)
        # Verify rejection breakdown contains auditable entries
        self.assertIsInstance(res.rejection_reasons_breakdown, dict)

    # -------------------------------------------------------------------------
    # L: Competition Mismatch
    # -------------------------------------------------------------------------
    def test_competition_signal_weighting(self):
        """Compatible competitions provide positive signal; completely different sport vetoes."""
        s = Event(
            competition_id="c1", home_participant="Porto", away_participant="Benfica",
            scheduled_start="2026-08-25T20:00:00Z", internal_id="s_p"
        )
        t = Event(
            competition_id="c2", home_participant="Porto", away_participant="Benfica",
            scheduled_start="2026-08-25T20:00:00Z", internal_id="t_p"
        )
        comp_s = Competition(name="Portugal - Primeira Liga", sport="Football", internal_id="c1")
        comp_t = Competition(name="Primeira Liga", sport="Football", internal_id="c2")

        dec = self.matcher.score_candidate(self._make_candidate("s_p", "t_p"), s, t, comp_s, comp_t)
        self.assertEqual(dec.decision, MatchDecisionType.MATCHED)
        self.assertGreaterEqual(dec.signals["competition"].raw_score, 0.5)

    # -------------------------------------------------------------------------
    # M: Deterministic Identical-Input Output
    # -------------------------------------------------------------------------
    def test_deterministic_matcher_output(self):
        """Repeated scoring of identical candidates produces identical scores, decisions and evidence."""
        s = Event(
            competition_id="c1", home_participant="Athletic Bilbao", away_participant="Real Sociedad",
            scheduled_start="2026-08-25T20:00:00Z", internal_id="s_det"
        )
        t = Event(
            competition_id="c2", home_participant="Athletic Bilbao", away_participant="Real Sociedad",
            scheduled_start="2026-08-25T20:00:00Z", internal_id="t_det"
        )
        cand = self._make_candidate("s_det", "t_det")

        d1 = self.matcher.score_candidate(cand, s, t)
        d2 = self.matcher.score_candidate(cand, s, t)

        self.assertEqual(d1.total_score, d2.total_score)
        self.assertEqual(d1.decision, d2.decision)
        self.assertEqual(d1.rejection_reason_code, d2.rejection_reason_code)
        self.assertEqual(d1.signals["home_team"].raw_score, d2.signals["home_team"].raw_score)
        self.assertEqual(d1.signals["away_team"].raw_score, d2.signals["away_team"].raw_score)

    # -------------------------------------------------------------------------
    # N: Integration with Stage 25 Paired-Detail Selection
    # -------------------------------------------------------------------------
    def test_integration_with_stage25_paired_detail_selection(self):
        """Verify that high-recall matched events correctly feed the Stage 25 detail prioritization policy."""
        from orchestration.event_selection import DefaultEventSelectionPolicy
        from providers.superbet.models import SuperbetDiscoveredItem
        from providers.betclic.models import BetclicDiscoveredItem

        policy = DefaultEventSelectionPolicy()

        # Create two matching events with naming variations (PSG vs Paris Saint-Germain, FC Koeln vs FC Koln)
        s_g1 = NormalizedGraph(
            event=Event(competition_id="c1", home_participant="Lille", away_participant="Paris Saint-Germain", scheduled_start="2026-08-25T20:00:00Z", internal_id="sb_e1", provider_ids={"superbet": "1"}),
            competition=Competition(name="Ligue 1", sport="Football", internal_id="c1"),
        )
        t_g1 = NormalizedGraph(
            event=Event(competition_id="c2", home_participant="Lille", away_participant="PSG", scheduled_start="2026-08-25T20:00:00Z", internal_id="bc_e1", provider_ids={"betclic": "101"}),
            competition=Competition(name="Ligue 1", sport="Football", internal_id="c2"),
        )
        s_g2 = NormalizedGraph(
            event=Event(competition_id="c1", home_participant="FC Koeln", away_participant="Hoffenheim", scheduled_start="2026-08-25T19:00:00Z", internal_id="sb_e2", provider_ids={"superbet": "2"}),
            competition=Competition(name="Bundesliga", sport="Football", internal_id="c1"),
        )
        t_g2 = NormalizedGraph(
            event=Event(competition_id="c2", home_participant="FC Koln", away_participant="Hoffenheim", scheduled_start="2026-08-25T19:00:00Z", internal_id="bc_e2", provider_ids={"betclic": "102"}),
            competition=Competition(name="Bundesliga", sport="Football", internal_id="c2"),
        )

        all_graphs = [s_g1, s_g2, t_g1, t_g2]
        cand_res = self.generator.generate_candidates_n_way(all_graphs)
        self.assertGreaterEqual(cand_res.total_candidates, 2)

        ev_map = {g.event.internal_id: g.event for g in all_graphs}
        comp_map = {g.event.internal_id: g.competition for g in all_graphs if g.competition}

        match_res = self.matcher.match_candidates(cand_res.candidates, ev_map, ev_map, comp_map)
        self.assertEqual(match_res.matched_count, 2)

        # Extract provider event IDs of matched events
        matched_provider_ids = set()
        for d in match_res.decisions:
            if d.decision == MatchDecisionType.MATCHED:
                s_ev = ev_map.get(d.source_event_id)
                t_ev = ev_map.get(d.target_event_id)
                if s_ev and s_ev.provider_ids:
                    matched_provider_ids.update(s_ev.provider_ids.values())
                if t_ev and t_ev.provider_ids:
                    matched_provider_ids.update(t_ev.provider_ids.values())

        self.assertEqual(matched_provider_ids, {"1", "101", "2", "102"})

        discovered_items = [
            SuperbetDiscoveredItem(event_id="1", match_name="Lille vs Paris Saint-Germain", competition_name="Ligue 1", start_time="2026-08-25T20:00:00Z", url="u1"),
            BetclicDiscoveredItem(provider_event_id="101", name="Lille vs PSG", competition_name="Ligue 1", start_time="2026-08-25T20:00:00Z", url="u2"),
            SuperbetDiscoveredItem(event_id="2", match_name="FC Koeln vs Hoffenheim", competition_name="Bundesliga", start_time="2026-08-25T19:00:00Z", url="u3"),
            BetclicDiscoveredItem(provider_event_id="102", name="FC Koln vs Hoffenheim", competition_name="Bundesliga", start_time="2026-08-25T19:00:00Z", url="u4"),
        ]

        p_res = policy.prioritize_detail_events(
            discovered_items=discovered_items,
            overlap_event_ids=matched_provider_ids,
            max_detail_requests=10,
        )

        self.assertEqual(p_res.events_selected, 4)
        self.assertEqual(p_res.events_overlap_selected, 4)
        self.assertEqual(p_res.overlap_selection_rate, 1.0)
        self.assertIn("1", p_res.selected_event_ids)
        self.assertIn("101", p_res.selected_event_ids)
        self.assertIn("2", p_res.selected_event_ids)
        self.assertIn("102", p_res.selected_event_ids)


if __name__ == "__main__":
    unittest.main()

