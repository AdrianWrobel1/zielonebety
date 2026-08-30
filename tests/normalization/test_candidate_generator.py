"""
Unit & Integration Tests for Stage 4.3 Event Candidate Generation & Blocking Engine

Covers:
1. Basic candidate generation (exact match, disjoint events, missing metadata)
2. External ID fast-path and non-suppression invariants
3. Team token blocking & partial overlap (America MG vs America Mineiro)
4. Orientation swap detection (Home/Away reversed)
5. Timezone & midnight boundary tolerance (adjacent date windows)
6. Safety checks on Youth/Women/Reserve squads (candidates generated for Stage 4.4 review)
7. Determinism across repeated executions
8. Adversarial regression suite (A–J)
9. Real fixture benchmark between Superbet and Betclic recordings
"""

import json
import unittest
from datetime import datetime, timezone

from domain.models import Event, Competition
from normalization.base_normalizer import NormalizedGraph
from normalization.candidate_generator import (
    EventCandidate,
    CandidateGenerationResult,
    EventCandidateGenerator,
)


class TestCandidateGenerator(unittest.TestCase):

    def setUp(self):
        self.generator = EventCandidateGenerator(midnight_tolerance_hours=2)

    # -------------------------------------------------------------------------
    # 1. Basic Candidate Generation
    # -------------------------------------------------------------------------
    def test_exact_match_generates_candidate(self):
        """Verify identical normalized events produce candidate with high evidence."""
        s = Event(
            competition_id="comp_1",
            home_participant="Arsenal",
            away_participant="Chelsea",
            scheduled_start="2026-08-25T20:00:00Z",
            provider_ids={"superbet": "sb_101"},
            internal_id="ev_sb_101",
        )
        t = Event(
            competition_id="comp_2",
            home_participant="Arsenal FC",
            away_participant="Chelsea",
            scheduled_start="2026-08-25T20:00:00Z",
            provider_ids={"betclic": "btc_201"},
            internal_id="ev_btc_201",
        )

        result = self.generator.generate_candidates([s], [t])
        self.assertEqual(result.total_candidates, 1)
        cand = result.candidates[0]
        self.assertEqual(cand.source_event_id, "ev_sb_101")
        self.assertEqual(cand.target_event_id, "ev_btc_201")
        self.assertIn("HOME_TOKEN:arsenal", cand.blocking_keys)
        self.assertIn("AWAY_TOKEN:chelsea", cand.blocking_keys)

    def test_disjoint_events_no_candidate(self):
        """Verify completely unrelated events produce 0 candidates."""
        s = Event(
            competition_id="comp_1",
            home_participant="Barcelona",
            away_participant="Real Madrid",
            scheduled_start="2026-08-25T20:00:00Z",
            provider_ids={"superbet": "sb_1"},
            internal_id="ev_sb_1",
        )
        t = Event(
            competition_id="comp_2",
            home_participant="Bayern Munich",
            away_participant="Borussia Dortmund",
            scheduled_start="2026-08-25T20:00:00Z",
            provider_ids={"betclic": "btc_1"},
            internal_id="ev_btc_1",
        )

        result = self.generator.generate_candidates([s], [t])
        self.assertEqual(result.total_candidates, 0)
        self.assertEqual(result.reduction_percentage, 100.0)

    def test_empty_inputs_safe(self):
        """Verify empty source or target collections return empty result safely."""
        r1 = self.generator.generate_candidates([], [])
        self.assertEqual(r1.total_candidates, 0)
        self.assertEqual(r1.total_naive_pairs, 0)

        s = Event(competition_id="c1", home_participant="A", away_participant="B", internal_id="e1")
        r2 = self.generator.generate_candidates([s], [])
        self.assertEqual(r2.total_candidates, 0)

    # -------------------------------------------------------------------------
    # 2. External ID Fast Path & Invariants
    # -------------------------------------------------------------------------
    def test_same_external_id_fast_path(self):
        """Verify matching external IDs generate a candidate with EXTERNAL_ID key."""
        s = Event(
            competition_id="c1",
            home_participant="Team X",
            away_participant="Team Y",
            external_ids={"betradar": "sr:match:999"},
            provider_ids={"superbet": "sb_99"},
            internal_id="ev_sb_99",
        )
        t = Event(
            competition_id="c2",
            home_participant="Variant X",
            away_participant="Variant Y",
            external_ids={"betradar": "sr:match:999"},
            provider_ids={"betclic": "btc_99"},
            internal_id="ev_btc_99",
        )

        result = self.generator.generate_candidates([s], [t])
        self.assertEqual(result.total_candidates, 1)
        self.assertIn("EXTERNAL_ID:betradar", result.candidates[0].blocking_keys)

    def test_conflicting_external_id_does_not_suppress_token_candidate(self):
        """Verify conflicting external IDs do NOT block candidates if date and teams match."""
        s = Event(
            competition_id="c1",
            home_participant="Liverpool",
            away_participant="Everton",
            scheduled_start="2026-08-25T15:00:00Z",
            external_ids={"betradar": "sr:match:111"},
            provider_ids={"superbet": "sb_1"},
            internal_id="ev_sb_1",
        )
        t = Event(
            competition_id="c2",
            home_participant="Liverpool",
            away_participant="Everton",
            scheduled_start="2026-08-25T15:00:00Z",
            external_ids={"betradar": "sr:match:222"},  # Different external ID
            provider_ids={"betclic": "btc_1"},
            internal_id="ev_btc_1",
        )

        result = self.generator.generate_candidates([s], [t])
        self.assertEqual(result.total_candidates, 1)
        self.assertIn("HOME_TOKEN:liverpool", result.candidates[0].blocking_keys)

    # -------------------------------------------------------------------------
    # 3. Token Blocking & Partial Overlap (America MG vs America Mineiro MG)
    # -------------------------------------------------------------------------
    def test_america_mg_partial_overlap_candidate_generated(self):
        """Verify America MG and America Mineiro MG successfully produce a candidate pair."""
        s = Event(
            competition_id="c1",
            home_participant="America MG",
            away_participant="Athletic Club MG",
            scheduled_start="2026-08-16 21:30:00",
            provider_ids={"superbet": "13207040"},
            internal_id="ev_sb_am",
        )
        t = Event(
            competition_id="c2",
            home_participant="América Mineiro (MG)",
            away_participant="Athletic Club",
            scheduled_start="2026-08-16T21:30:00Z",
            provider_ids={"betclic": "btc_am"},
            internal_id="ev_btc_am",
        )

        result = self.generator.generate_candidates([s], [t])
        self.assertEqual(result.total_candidates, 1)
        cand = result.candidates[0]
        # Token 'america' (and 'mg') match
        self.assertTrue(any("HOME_TOKEN:america" in k or "HOME_TOKEN:mg" in k for k in cand.blocking_keys))

    # -------------------------------------------------------------------------
    # 4. Orientation Swap Fallback (Reversed Home/Away)
    # -------------------------------------------------------------------------
    def test_orientation_swap_generates_candidate_with_label(self):
        """Verify reversed Home/Away produces candidate labeled ORIENTATION_SWAP."""
        s = Event(
            competition_id="c1",
            home_participant="Lakers",
            away_participant="Celtics",
            scheduled_start="2026-08-25T20:00:00Z",
            provider_ids={"superbet": "sb_rev"},
            internal_id="ev_sb_rev",
        )
        t = Event(
            competition_id="c2",
            home_participant="Celtics",
            away_participant="Lakers",
            scheduled_start="2026-08-25T20:00:00Z",
            provider_ids={"betclic": "btc_rev"},
            internal_id="ev_btc_rev",
        )

        result = self.generator.generate_candidates([s], [t])
        self.assertEqual(result.total_candidates, 1)
        cand = result.candidates[0]
        self.assertTrue(any("ORIENTATION_SWAP" in k for k in cand.blocking_keys))

    # -------------------------------------------------------------------------
    # 5. Midnight & Date Boundary Tolerance
    # -------------------------------------------------------------------------
    def test_adjacent_date_near_midnight_generates_candidate(self):
        """Verify events at 23:45 and 00:15 UTC (different dates) produce candidate."""
        s = Event(
            competition_id="c1",
            home_participant="Flamengo",
            away_participant="Fluminense",
            scheduled_start="2026-08-16T23:45:00Z",  # Date 2026-08-16
            provider_ids={"superbet": "sb_mid"},
            internal_id="ev_sb_mid",
        )
        t = Event(
            competition_id="c2",
            home_participant="Flamengo",
            away_participant="Fluminense",
            scheduled_start="2026-08-17T00:15:00Z",  # Date 2026-08-17
            provider_ids={"betclic": "btc_mid"},
            internal_id="ev_btc_mid",
        )

        result = self.generator.generate_candidates([s], [t])
        self.assertEqual(result.total_candidates, 1)
        self.assertTrue(any("ADJACENT" in k or "TOKEN" in k for k in result.candidates[0].blocking_keys))

    # -------------------------------------------------------------------------
    # 6. Safety Checks (Youth, Women, Reserves)
    # -------------------------------------------------------------------------
    def test_youth_vs_senior_candidate_generated_for_scorer_review(self):
        """Verify Youth vs Senior event IS generated as candidate so Stage 4.4 can reject it."""
        s = Event(
            competition_id="c1",
            home_participant="Barcelona",
            away_participant="Real Madrid",
            scheduled_start="2026-08-25T20:00:00Z",
            provider_ids={"superbet": "sb_sr"},
            internal_id="ev_sb_sr",
        )
        t = Event(
            competition_id="c2",
            home_participant="Barcelona U19",
            away_participant="Real Madrid U19",
            scheduled_start="2026-08-25T20:00:00Z",
            provider_ids={"betclic": "btc_u19"},
            internal_id="ev_btc_u19",
        )

        result = self.generator.generate_candidates([s], [t])
        # Recall over precision: candidate must exist for scorer to evaluate
        self.assertEqual(result.total_candidates, 1)

    # -------------------------------------------------------------------------
    # 7. Determinism
    # -------------------------------------------------------------------------
    def test_candidate_generation_determinism(self):
        """Verify repeated executions produce identical sorted candidate sets."""
        events_s = [
            Event(competition_id="c1", home_participant="Team B", away_participant="Team C", scheduled_start="2026-08-25T20:00:00Z", internal_id="ev_2"),
            Event(competition_id="c1", home_participant="Team A", away_participant="Team D", scheduled_start="2026-08-25T20:00:00Z", internal_id="ev_1"),
        ]
        events_t = [
            Event(competition_id="c2", home_participant="Team A", away_participant="Team D", scheduled_start="2026-08-25T20:00:00Z", internal_id="ev_t1"),
            Event(competition_id="c2", home_participant="Team B", away_participant="Team C", scheduled_start="2026-08-25T20:00:00Z", internal_id="ev_t2"),
        ]

        r1 = self.generator.generate_candidates(events_s, events_t)
        r2 = self.generator.generate_candidates(events_s, events_t)

        self.assertEqual(len(r1.candidates), len(r2.candidates))
        for c1, c2 in zip(r1.candidates, r2.candidates):
            self.assertEqual(c1.source_event_id, c2.source_event_id)
            self.assertEqual(c1.target_event_id, c2.target_event_id)
            self.assertEqual(c1.blocking_keys, c2.blocking_keys)

    # -------------------------------------------------------------------------
    # 8. Adversarial Suite (A–J)
    # -------------------------------------------------------------------------
    def test_adversarial_a_same_teams_distant_dates(self):
        """Adversarial A: Same teams 1 month apart must NOT produce candidate with strict date block."""
        s = Event(competition_id="c1", home_participant="Arsenal", away_participant="Chelsea", scheduled_start="2026-08-01T20:00:00Z", internal_id="ev_a1")
        t = Event(competition_id="c2", home_participant="Arsenal", away_participant="Chelsea", scheduled_start="2026-09-01T20:00:00Z", internal_id="ev_a2")
        result = self.generator.generate_candidates([s], [t])
        self.assertEqual(result.total_candidates, 0)

    def test_adversarial_b_same_teams_adjacent_utc_date(self):
        """Adversarial B: Same teams near UTC midnight boundary must produce candidate."""
        s = Event(competition_id="c1", home_participant="Ajax", away_participant="PSV", scheduled_start="2026-08-20T23:30:00Z", internal_id="ev_b1")
        t = Event(competition_id="c2", home_participant="Ajax", away_participant="PSV", scheduled_start="2026-08-21T00:30:00Z", internal_id="ev_b2")
        result = self.generator.generate_candidates([s], [t])
        self.assertEqual(result.total_candidates, 1)

    def test_adversarial_c_overlapping_tokens_america_mg(self):
        """Adversarial C: 'America MG' and 'America Mineiro' produce candidate via shared token."""
        s = Event(competition_id="c1", home_participant="America MG", away_participant="Cruzeiro", scheduled_start="2026-08-16T20:00:00Z", internal_id="ev_c1")
        t = Event(competition_id="c2", home_participant="America Mineiro", away_participant="Cruzeiro MG", scheduled_start="2026-08-16T20:00:00Z", internal_id="ev_c2")
        result = self.generator.generate_candidates([s], [t])
        self.assertEqual(result.total_candidates, 1)

    def test_adversarial_d_home_away_reversed_marked_orientation_swap(self):
        """Adversarial D: Home/Away swap produces candidate with ORIENTATION_SWAP key."""
        s = Event(competition_id="c1", home_participant="Milan", away_participant="Inter", scheduled_start="2026-08-25T19:45:00Z", internal_id="ev_d1")
        t = Event(competition_id="c2", home_participant="Inter", away_participant="Milan", scheduled_start="2026-08-25T19:45:00Z", internal_id="ev_d2")
        result = self.generator.generate_candidates([s], [t])
        self.assertEqual(result.total_candidates, 1)
        self.assertTrue(any("ORIENTATION_SWAP" in k for k in result.candidates[0].blocking_keys))

    def test_adversarial_e_missing_competition_does_not_prevent_candidate(self):
        """Adversarial E: Missing competition object still produces candidate via event data."""
        s = Event(competition_id="missing_comp_s", home_participant="PSG", away_participant="Marseille", scheduled_start="2026-08-25T20:00:00Z", internal_id="ev_e1")
        t = Event(competition_id="missing_comp_t", home_participant="PSG", away_participant="Marseille", scheduled_start="2026-08-25T20:00:00Z", internal_id="ev_e2")
        result = self.generator.generate_candidates([s], [t], competition_map=None)
        self.assertEqual(result.total_candidates, 1)

    def test_adversarial_f_missing_external_id_generates_token_candidate(self):
        """Adversarial F: Missing external ID produces candidate via team tokens and date."""
        s = Event(competition_id="c1", home_participant="Napoli", away_participant="Roma", scheduled_start="2026-08-25T19:45:00Z", external_ids={}, internal_id="ev_f1")
        t = Event(competition_id="c2", home_participant="Napoli", away_participant="Roma", scheduled_start="2026-08-25T19:45:00Z", external_ids={}, internal_id="ev_f2")
        result = self.generator.generate_candidates([s], [t])
        self.assertEqual(result.total_candidates, 1)

    def test_adversarial_g_conflicting_external_id_retained(self):
        """Adversarial G: Conflicting external ID does NOT suppress valid token match."""
        s = Event(competition_id="c1", home_participant="Monaco", away_participant="Lyon", scheduled_start="2026-08-25T20:00:00Z", external_ids={"betradar": "111"}, internal_id="ev_g1")
        t = Event(competition_id="c2", home_participant="Monaco", away_participant="Lyon", scheduled_start="2026-08-25T20:00:00Z", external_ids={"betradar": "999"}, internal_id="ev_g2")
        result = self.generator.generate_candidates([s], [t])
        self.assertEqual(result.total_candidates, 1)

    def test_adversarial_h_youth_team_candidate_retained_for_scoring(self):
        """Adversarial H: Barcelona vs Barcelona U19 produces candidate for scoring layer."""
        s = Event(competition_id="c1", home_participant="Barcelona", away_participant="Valencia", scheduled_start="2026-08-25T20:00:00Z", internal_id="ev_h1")
        t = Event(competition_id="c2", home_participant="Barcelona U19", away_participant="Valencia U19", scheduled_start="2026-08-25T20:00:00Z", internal_id="ev_h2")
        result = self.generator.generate_candidates([s], [t])
        self.assertEqual(result.total_candidates, 1)

    def test_adversarial_i_women_team_candidate_retained_for_scoring(self):
        """Adversarial I: Chelsea vs Chelsea Women produces candidate for scoring layer."""
        s = Event(competition_id="c1", home_participant="Chelsea", away_participant="Arsenal", scheduled_start="2026-08-25T14:00:00Z", internal_id="ev_i1")
        t = Event(competition_id="c2", home_participant="Chelsea Women", away_participant="Arsenal Women", scheduled_start="2026-08-25T14:00:00Z", internal_id="ev_i2")
        result = self.generator.generate_candidates([s], [t])
        self.assertEqual(result.total_candidates, 1)

    def test_adversarial_j_common_tokens_different_teams_no_explosion(self):
        """Adversarial J: 'Manchester City' vs 'Leicester City' does NOT produce false candidate purely on 'city'."""
        s = Event(competition_id="c1", home_participant="Manchester City", away_participant="Chelsea", scheduled_start="2026-08-25T16:00:00Z", internal_id="ev_j1")
        t = Event(competition_id="c2", home_participant="Leicester City", away_participant="Liverpool", scheduled_start="2026-08-25T16:00:00Z", internal_id="ev_j2")
        result = self.generator.generate_candidates([s], [t])
        # Neither Manchester matches Leicester, nor Chelsea matches Liverpool
        self.assertEqual(result.total_candidates, 0)

    # -------------------------------------------------------------------------
    # 9. Real Fixture Benchmark (Superbet Live vs Betclic Live)
    # -------------------------------------------------------------------------
    def test_real_fixture_candidate_generation_benchmark(self):
        """Benchmark candidate generator against real recorded Superbet and Betclic manifests."""
        # Load Superbet live recording
        with open("tests/fixtures/recordings/superbet/live_manifest/response_000.json", "r", encoding="utf-8") as f:
            sb_data = json.load(f)

        # Load Betclic live recording
        with open("tests/fixtures/recordings/betclic/live_manifest/response_000.json", "r", encoding="utf-8") as f:
            btc_data = json.load(f)

        # Convert Superbet raw events to Event domain objects
        sb_events = []
        for raw_ev in sb_data.get("events", []):
            fix = raw_ev.get("fixture", {})
            name = fix.get("event_name", "")
            parts = name.split("·") if "·" in name else name.split("vs")
            home = parts[0].strip() if len(parts) > 0 else name
            away = parts[1].strip() if len(parts) > 1 else ""
            ev = Event(
                competition_id=str(fix.get("tournament_id", "")),
                home_participant=home,
                away_participant=away,
                scheduled_start=fix.get("utc_date"),
                provider_ids={"superbet": str(raw_ev.get("event_id"))},
                external_ids={"betradar": str(fix.get("betradar_id", ""))},
                internal_id=f"sb_{raw_ev.get('event_id')}",
            )
            sb_events.append(ev)

        # Convert Betclic raw events to Event domain objects
        btc_events = []
        for raw_ev in btc_data:
            ev = Event(
                competition_id=raw_ev.get("competition", "La Liga"),
                home_participant=raw_ev.get("home_team", "Barcelona"),
                away_participant=raw_ev.get("away_team", "Real Madrid"),
                scheduled_start=raw_ev.get("start_date", "2026-08-25T20:00:00Z"),
                provider_ids={"betclic": str(raw_ev.get("id"))},
                internal_id=f"btc_{raw_ev.get('id')}",
            )
            btc_events.append(ev)

        # Run candidate generator
        result = self.generator.generate_candidates(sb_events, btc_events)

        self.assertGreater(result.total_source_events, 0)
        self.assertGreater(result.total_target_events, 0)
        self.assertEqual(result.total_naive_pairs, len(sb_events) * len(btc_events))
        # Verify significant search space reduction
        self.assertGreaterEqual(result.reduction_percentage, 95.0)


if __name__ == "__main__":
    unittest.main()
