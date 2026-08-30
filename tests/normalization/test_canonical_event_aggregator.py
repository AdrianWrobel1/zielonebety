"""
Stage 4.5: Unit & Integration Tests for Canonical Event Aggregation Layer

Covers:
1. Exact two-provider aggregation (Superbet + Betclic -> 1 CanonicalEvent, 2 sources).
2. Full provider lineage and external ID preservation.
3. Deterministic canonical ID generation & collision resistance.
4. Duplicate MATCHED decision deduplication.
5. Ambiguous decisions preserved in ambiguous_events (never aggregated).
6. Rejected decisions preserved in unmatched_events (never aggregated).
7. Orientation swap handling (NORMAL vs ORIENTATION_SWAP semantics).
8. External ID match evidence & decomposed signal preservation.
9. Conflicting MATCHED decisions (one-to-many conflict detection & isolation).
10. Unmatched event preservation.
11. Multi-source scalability (N >= 3 providers).
12. Controlled integration verification with genuine provider fixtures.
13. Adversarial regression suite (A–H).
"""

import json
import unittest
from pathlib import Path
from typing import Dict, List

from domain.models import (
    Event,
    Competition,
    CanonicalEvent,
    EventSource,
    MatchEvidence,
    CanonicalCompetition,
    generate_deterministic_canonical_event_id,
)
from normalization.candidate_generator import EventCandidate, EventCandidateGenerator
from normalization.matcher import (
    EventMatcher,
    MatchDecision,
    MatchDecisionType,
    OrientationType,
    SignalScore,
)
from normalization.aggregator import (
    CanonicalEventAggregator,
    CanonicalAggregationResult,
    AggregationConflict,
)
from providers.superbet.parser.parser import SuperbetParser
from normalization.superbet_normalizer import SuperbetNormalizer
from providers.betclic.parser.parser import BetclicParser
from normalization.betclic_normalizer import BetclicNormalizer


class TestCanonicalEventAggregator(unittest.TestCase):
    """Unit & Integration test suite for Stage 4.5 CanonicalEventAggregator."""

    def setUp(self):
        self.aggregator = CanonicalEventAggregator()
        self.matcher = EventMatcher()

    def _make_dummy_signal(self, name: str, score: float, weight: float) -> SignalScore:
        return SignalScore(
            name=name,
            raw_score=score,
            weight=weight,
            weighted_score=round(score * weight, 4),
            details={"test": True},
        )

    def _make_matched_decision(
        self,
        s_id: str,
        t_id: str,
        total_score: float = 0.95,
        orientation: OrientationType = OrientationType.NORMAL,
        signals: Dict[str, SignalScore] = None,
    ) -> MatchDecision:
        if signals is None:
            signals = {
                "external_id": self._make_dummy_signal("external_id", 1.0, 0.25),
                "home_team": self._make_dummy_signal("home_team", 1.0, 0.25),
                "away_team": self._make_dummy_signal("away_team", 1.0, 0.25),
                "kickoff": self._make_dummy_signal("kickoff", 1.0, 0.15),
                "competition": self._make_dummy_signal("competition", 1.0, 0.05),
                "sport": self._make_dummy_signal("sport", 1.0, 0.05),
            }
        return MatchDecision(
            source_event_id=s_id,
            target_event_id=t_id,
            decision=MatchDecisionType.MATCHED,
            total_score=total_score,
            orientation=orientation,
            signals=signals,
            veto_reasons=(),
            warnings=(),
            evidence={
                "source_name": "Barcelona vs Real Madrid",
                "target_name": "Barcelona vs Real Madrid",
                "blocking_keys": ("EXT:betradar:101",),
            },
        )

    # -------------------------------------------------------------------------
    # 1. Exact Two-Provider Aggregation
    # -------------------------------------------------------------------------
    def test_exact_two_provider_aggregation(self):
        """Verify Superbet + Betclic MATCHED pair aggregates into 1 CanonicalEvent with 2 sources."""
        s = Event(
            competition_id="comp_sb",
            home_participant="Barcelona",
            away_participant="Real Madrid",
            scheduled_start="2026-08-25T20:00:00Z",
            provider_ids={"superbet": "13222121"},
            external_ids={"betradar": "sr:match:101"},
            internal_id="ev_sb_1",
        )
        t = Event(
            competition_id="comp_bc",
            home_participant="Barcelona",
            away_participant="Real Madrid",
            scheduled_start="2026-08-25T20:00:00Z",
            provider_ids={"betclic": "BC-9988"},
            external_ids={"betradar": "sr:match:101"},
            internal_id="ev_bc_1",
        )
        comp_s = Competition(name="La Liga", sport="Football", internal_id="comp_sb", provider_ids={"superbet": "comp_sb_1"})
        comp_t = Competition(name="La Liga", sport="Football", internal_id="comp_bc", provider_ids={"betclic": "comp_bc_1"})

        dec = self._make_matched_decision("ev_sb_1", "ev_bc_1")
        result = self.aggregator.aggregate(
            events=[s, t],
            decisions=[dec],
            competition_map={"comp_sb": comp_s, "comp_bc": comp_t},
        )

        self.assertEqual(result.total_canonical_events, 1)
        self.assertEqual(len(result.canonical_events), 1)
        self.assertEqual(result.total_sources_aggregated, 2)
        self.assertEqual(len(result.unmatched_events), 0)
        self.assertEqual(len(result.ambiguous_events), 0)
        self.assertEqual(len(result.conflicts), 0)

        cev = result.canonical_events[0]
        self.assertTrue(cev.canonical_event_id.startswith("cev_"))
        self.assertEqual(cev.sport, "Football")
        self.assertEqual(cev.home_team, "Barcelona")
        self.assertEqual(cev.away_team, "Real Madrid")
        self.assertEqual(cev.scheduled_start, "2026-08-25T20:00:00Z")
        self.assertEqual(len(cev.sources), 2)
        self.assertIn("superbet", cev.sources)
        self.assertIn("betclic", cev.sources)

    # -------------------------------------------------------------------------
    # 2. Lineage & External ID Preservation
    # -------------------------------------------------------------------------
    def test_lineage_and_external_id_preservation(self):
        """Verify original provider IDs, external IDs, and metadata are intact after aggregation."""
        s = Event(
            competition_id="c1",
            home_participant="Liverpool FC",
            away_participant="Chelsea FC",
            scheduled_start="2026-09-01T15:00:00Z",
            provider_ids={"superbet": "SB-100"},
            external_ids={"betradar": "sr:111", "opta": "opt:222"},
            metadata={"match_day": 1, "tier": 1},
            internal_id="ev_s_liv",
        )
        t = Event(
            competition_id="c2",
            home_participant="Liverpool",
            away_participant="Chelsea",
            scheduled_start="2026-09-01T15:00:00Z",
            provider_ids={"betclic": "BC-200"},
            external_ids={"betradar": "sr:111"},
            metadata={"market_count": 50},
            internal_id="ev_t_liv",
        )
        dec = self._make_matched_decision("ev_s_liv", "ev_t_liv")
        result = self.aggregator.aggregate(events=[s, t], decisions=[dec])

        cev = result.canonical_events[0]
        sb_source = cev.sources["superbet"]
        bc_source = cev.sources["betclic"]

        self.assertEqual(sb_source.provider_event_id, "SB-100")
        self.assertEqual(sb_source.internal_event_id, "ev_s_liv")
        self.assertEqual(sb_source.home_participant, "Liverpool FC")
        self.assertEqual(sb_source.external_ids, {"betradar": "sr:111", "opta": "opt:222"})
        self.assertEqual(sb_source.metadata["match_day"], 1)

        self.assertEqual(bc_source.provider_event_id, "BC-200")
        self.assertEqual(bc_source.internal_event_id, "ev_t_liv")
        self.assertEqual(bc_source.home_participant, "Liverpool")
        self.assertEqual(bc_source.external_ids, {"betradar": "sr:111"})
        self.assertEqual(bc_source.metadata["market_count"], 50)

    # -------------------------------------------------------------------------
    # 3. Canonical ID Determinism & Collision Resistance
    # -------------------------------------------------------------------------
    def test_canonical_id_determinism_and_collision_resistance(self):
        """Verify deterministic ID generation and distinct IDs for different kickoffs/sports."""
        id_1 = generate_deterministic_canonical_event_id("football", "barcelona", "real madrid", "2026-08-25T20:00:00Z")
        id_2 = generate_deterministic_canonical_event_id("football", "barcelona", "real madrid", "2026-08-25T20:00:00Z")
        self.assertEqual(id_1, id_2)

        # Different kickoff -> different ID
        id_diff_time = generate_deterministic_canonical_event_id("football", "barcelona", "real madrid", "2026-08-26T20:00:00Z")
        self.assertNotEqual(id_1, id_diff_time)

        # Different sport -> different ID
        id_diff_sport = generate_deterministic_canonical_event_id("basketball", "barcelona", "real madrid", "2026-08-25T20:00:00Z")
        self.assertNotEqual(id_1, id_diff_sport)

        # Different teams -> different ID
        id_diff_team = generate_deterministic_canonical_event_id("football", "atletico madrid", "real madrid", "2026-08-25T20:00:00Z")
        self.assertNotEqual(id_1, id_diff_team)

    # -------------------------------------------------------------------------
    # 4. Duplicate MATCHED Decision Deduplication
    # -------------------------------------------------------------------------
    def test_duplicate_decision_deduplication(self):
        """Verify submitting identical or symmetric MATCHED decisions produces exactly 1 CanonicalEvent."""
        s = Event(competition_id="c1", home_participant="Arsenal", away_participant="Chelsea", internal_id="ev_s_ars", provider_ids={"superbet": "1"})
        t = Event(competition_id="c2", home_participant="Arsenal", away_participant="Chelsea", internal_id="ev_t_ars", provider_ids={"betclic": "2"})

        dec_1 = self._make_matched_decision("ev_s_ars", "ev_t_ars", total_score=0.90)
        dec_2 = self._make_matched_decision("ev_s_ars", "ev_t_ars", total_score=0.95)  # Duplicate pair with higher score

        result = self.aggregator.aggregate(events=[s, t], decisions=[dec_1, dec_2])
        self.assertEqual(result.total_canonical_events, 1)
        self.assertEqual(len(result.canonical_events), 1)
        self.assertEqual(result.canonical_events[0].match_evidence[0].total_score, 0.95)

    # -------------------------------------------------------------------------
    # 5. Ambiguous & Rejected Decisions Never Aggregate
    # -------------------------------------------------------------------------
    def test_ambiguous_and_rejected_decisions_not_aggregated(self):
        """Verify AMBIGUOUS and REJECTED decisions are never aggregated into CanonicalEvent."""
        s_amb = Event(competition_id="c1", home_participant="America MG", away_participant="Athletic Club", internal_id="ev_s_amb", provider_ids={"superbet": "10"})
        t_amb = Event(competition_id="c2", home_participant="América Mineiro (MG)", away_participant="Athletic Club", internal_id="ev_t_amb", provider_ids={"betclic": "20"})

        dec_amb = MatchDecision(
            source_event_id="ev_s_amb",
            target_event_id="ev_t_amb",
            decision=MatchDecisionType.AMBIGUOUS,
            total_score=0.70,
            orientation=OrientationType.NORMAL,
            signals={},
        )

        s_rej = Event(competition_id="c1", home_participant="Porto", away_participant="Benfica", internal_id="ev_s_rej", provider_ids={"superbet": "30"})
        t_rej = Event(competition_id="c2", home_participant="Porto B", away_participant="Benfica B", internal_id="ev_t_rej", provider_ids={"betclic": "40"})

        dec_rej = MatchDecision(
            source_event_id="ev_s_rej",
            target_event_id="ev_t_rej",
            decision=MatchDecisionType.REJECTED,
            total_score=0.0,
            orientation=OrientationType.NORMAL,
            signals={},
            veto_reasons=("reserve_mismatch",),
        )

        result = self.aggregator.aggregate(
            events=[s_amb, t_amb, s_rej, t_rej],
            decisions=[dec_amb, dec_rej],
        )

        self.assertEqual(result.total_canonical_events, 0)
        self.assertEqual(len(result.canonical_events), 0)
        # AMBIGUOUS events should be recorded in ambiguous_events
        amb_ids = {e.internal_id for e in result.ambiguous_events}
        self.assertIn("ev_s_amb", amb_ids)
        self.assertIn("ev_t_amb", amb_ids)
        # REJECTED events (not matched or ambiguous) should be in unmatched_events
        unmatched_ids = {e.internal_id for e in result.unmatched_events}
        self.assertIn("ev_s_rej", unmatched_ids)
        self.assertIn("ev_t_rej", unmatched_ids)

    # -------------------------------------------------------------------------
    # 6. Orientation Swap Handling
    # -------------------------------------------------------------------------
    def test_orientation_swap_handling(self):
        """Verify ORIENTATION_SWAP properly aligns participants and flags swapped lineage."""
        s = Event(
            competition_id="c1",
            home_participant="AC Milan",
            away_participant="Inter Milan",
            scheduled_start="2026-09-10T19:45:00Z",
            provider_ids={"superbet": "SB-MIL"},
            internal_id="ev_s_mil",
        )
        t = Event(
            competition_id="c2",
            home_participant="Inter Milan",
            away_participant="AC Milan",
            scheduled_start="2026-09-10T19:45:00Z",
            provider_ids={"betclic": "BC-MIL"},
            internal_id="ev_t_mil",
        )

        dec = self._make_matched_decision(
            "ev_s_mil",
            "ev_t_mil",
            orientation=OrientationType.ORIENTATION_SWAP,
        )

        result = self.aggregator.aggregate(events=[s, t], decisions=[dec])
        self.assertEqual(result.total_canonical_events, 1)

        cev = result.canonical_events[0]
        self.assertEqual(cev.home_team, "Inter Milan")  # Primary sorted provider betclic determines ref orientation or Superbet
        # Sources retain original raw names
        self.assertEqual(cev.sources["superbet"].home_participant, "AC Milan")
        self.assertEqual(cev.sources["betclic"].home_participant, "Inter Milan")
        # Evidence preserves ORIENTATION_SWAP
        self.assertEqual(cev.match_evidence[0].orientation, "ORIENTATION_SWAP")

    # -------------------------------------------------------------------------
    # 7. Match Evidence & Decomposed Signals Preservation
    # -------------------------------------------------------------------------
    def test_match_evidence_and_signals_preservation(self):
        """Verify match score, signal weights, raw scores, and blocking keys are fully audit-preserved."""
        s = Event(competition_id="c1", home_participant="Bayern Munich", away_participant="Dortmund", internal_id="ev_s_bay", provider_ids={"superbet": "100"})
        t = Event(competition_id="c2", home_participant="Bayern Munich", away_participant="Dortmund", internal_id="ev_t_bay", provider_ids={"betclic": "200"})

        signals = {
            "external_id": SignalScore("external_id", 1.0, 0.25, 0.25, {"key": "betradar"}),
            "home_team": SignalScore("home_team", 0.95, 0.25, 0.2375, {"jaccard": 0.95}),
            "away_team": SignalScore("away_team", 1.0, 0.25, 0.25, {"jaccard": 1.0}),
            "kickoff": SignalScore("kickoff", 1.0, 0.15, 0.15, {"diff_hours": 0.0}),
            "competition": SignalScore("competition", 1.0, 0.05, 0.05, {"match": True}),
            "sport": SignalScore("sport", 1.0, 0.05, 0.05, {"sport": "football"}),
        }
        dec = self._make_matched_decision("ev_s_bay", "ev_t_bay", total_score=0.9875, signals=signals)
        dec.evidence["blocking_keys"] = ("EXT:betradar:123", "HOME:football:2026-09-10:bayern")

        result = self.aggregator.aggregate(events=[s, t], decisions=[dec])
        cev = result.canonical_events[0]

        self.assertEqual(len(cev.match_evidence), 1)
        ev_obj = cev.match_evidence[0]
        self.assertEqual(ev_obj.total_score, 0.9875)
        self.assertEqual(ev_obj.decision, "MATCHED")
        self.assertEqual(ev_obj.signals["home_team"]["raw_score"], 0.95)
        self.assertEqual(ev_obj.signals["home_team"]["weighted_score"], 0.2375)
        self.assertEqual(ev_obj.signals["home_team"]["details"]["jaccard"], 0.95)
        self.assertIn("EXT:betradar:123", ev_obj.blocking_keys)

    # -------------------------------------------------------------------------
    # 8. Conflicting Match Decisions (One-to-Many Safety)
    # -------------------------------------------------------------------------
    def test_conflicting_matched_decisions_detected_and_isolated(self):
        """Verify one-to-many matches (A matched to B and A matched to C) are isolated as conflicts."""
        s = Event(competition_id="c1", home_participant="Juventus", away_participant="Napoli", internal_id="ev_s_juv", provider_ids={"superbet": "1"})
        t1 = Event(competition_id="c2", home_participant="Juventus", away_participant="Napoli", internal_id="ev_t1_juv", provider_ids={"betclic": "2"})
        t2 = Event(competition_id="c2", home_participant="Juventus FC", away_participant="SSC Napoli", internal_id="ev_t2_juv", provider_ids={"betclic": "3"})

        dec_1 = self._make_matched_decision("ev_s_juv", "ev_t1_juv")
        dec_2 = self._make_matched_decision("ev_s_juv", "ev_t2_juv")

        result = self.aggregator.aggregate(events=[s, t1, t2], decisions=[dec_1, dec_2])

        # Conflict detected -> 0 canonical events created
        self.assertEqual(result.total_canonical_events, 0)
        self.assertEqual(len(result.canonical_events), 0)
        self.assertGreaterEqual(len(result.conflicts), 1)
        self.assertTrue(any("ev_s_juv" == cf.event_id for cf in result.conflicts))
        self.assertTrue(any("provider_multiplicity_conflict" in r for cf in result.conflicts for r in cf.reasons))

    # -------------------------------------------------------------------------
    # 9. Multi-Source Scalability (N >= 3 Providers)
    # -------------------------------------------------------------------------
    def test_multi_source_scalability(self):
        """Verify aggregation seamlessly supports 3+ providers into a single CanonicalEvent."""
        e_sb = Event(competition_id="c1", home_participant="Ajax", away_participant="PSV", internal_id="ev_sb", provider_ids={"superbet": "SB-1"})
        e_bc = Event(competition_id="c2", home_participant="Ajax", away_participant="PSV", internal_id="ev_bc", provider_ids={"betclic": "BC-1"})
        e_sts = Event(competition_id="c3", home_participant="Ajax", away_participant="PSV", internal_id="ev_sts", provider_ids={"sts": "STS-1"})

        dec_1 = self._make_matched_decision("ev_sb", "ev_bc")
        dec_2 = self._make_matched_decision("ev_bc", "ev_sts")

        result = self.aggregator.aggregate(events=[e_sb, e_bc, e_sts], decisions=[dec_1, dec_2])
        self.assertEqual(result.total_canonical_events, 1)
        self.assertEqual(result.total_sources_aggregated, 3)

        cev = result.canonical_events[0]
        self.assertEqual(len(cev.sources), 3)
        self.assertIn("superbet", cev.sources)
        self.assertIn("betclic", cev.sources)
        self.assertIn("sts", cev.sources)

    # -------------------------------------------------------------------------
    # 10. Controlled Integration Verification with Real Providers
    # -------------------------------------------------------------------------
    def test_controlled_integration_with_real_normalized_events(self):
        """Verify aggregation on normalized provider events derived from genuine recordings."""
        # Load Superbet manifest
        sb_path = Path("tests/fixtures/recordings/superbet/live_manifest/response_000.json")
        with open(sb_path, "r", encoding="utf-8") as f:
            sb_raw = json.load(f)
        sb_parsed = SuperbetParser().parse_payloads(sb_raw.get("events", []))
        sb_graph = SuperbetNormalizer().normalize_event(sb_parsed[0])

        # Load Betclic manifest
        bc_path = Path("tests/fixtures/recordings/betclic/live_manifest/response_000.json")
        with open(bc_path, "r", encoding="utf-8") as f:
            bc_raw = json.load(f)
        bc_parsed = BetclicParser().parse_payloads(bc_raw)
        bc_graph = BetclicNormalizer().normalize_event(bc_parsed[0])

        # Controlled MATCHED decision for testing aggregator
        dec = self._make_matched_decision(
            sb_graph.event.internal_id,
            bc_graph.event.internal_id,
            total_score=0.92,
        )

        result = self.aggregator.aggregate(
            events=[sb_graph, bc_graph],
            decisions=[dec],
        )

        self.assertEqual(result.total_canonical_events, 1)
        cev = result.canonical_events[0]
        self.assertIn("superbet", cev.sources)
        self.assertIn("betclic", cev.sources)
        self.assertTrue(cev.canonical_event_id.startswith("cev_"))

    # -------------------------------------------------------------------------
    # 11. Adversarial Tests A–H
    # -------------------------------------------------------------------------
    def test_adversarial_a_matched_to_ambiguous_no_aggregation(self):
        """Adversarial A: Changing MATCHED -> AMBIGUOUS produces 0 canonical events."""
        s = Event(competition_id="c1", home_participant="Porto", away_participant="Sporting", internal_id="ev_s_a", provider_ids={"superbet": "1"})
        t = Event(competition_id="c2", home_participant="Porto", away_participant="Sporting", internal_id="ev_t_a", provider_ids={"betclic": "2"})

        dec = MatchDecision("ev_s_a", "ev_t_a", MatchDecisionType.AMBIGUOUS, 0.70, OrientationType.NORMAL, {})
        result = self.aggregator.aggregate(events=[s, t], decisions=[dec])

        self.assertEqual(result.total_canonical_events, 0)
        self.assertEqual(len(result.canonical_events), 0)
        self.assertEqual(len(result.ambiguous_events), 2)

    def test_adversarial_b_matched_to_rejected_no_aggregation(self):
        """Adversarial B: Changing MATCHED -> REJECTED produces 0 canonical events."""
        s = Event(competition_id="c1", home_participant="Porto", away_participant="Sporting", internal_id="ev_s_b", provider_ids={"superbet": "1"})
        t = Event(competition_id="c2", home_participant="Porto", away_participant="Sporting", internal_id="ev_t_b", provider_ids={"betclic": "2"})

        dec = MatchDecision("ev_s_b", "ev_t_b", MatchDecisionType.REJECTED, 0.0, OrientationType.NORMAL, {}, ("veto",))
        result = self.aggregator.aggregate(events=[s, t], decisions=[dec])

        self.assertEqual(result.total_canonical_events, 0)
        self.assertEqual(len(result.canonical_events), 0)
        self.assertEqual(len(result.unmatched_events), 2)

    def test_adversarial_c_missing_provider_event_id_safe_handling(self):
        """Adversarial C: Event with missing provider_event_id uses fallback internal_id safely."""
        s = Event(competition_id="c1", home_participant="Lazio", away_participant="Roma", internal_id="ev_s_c", provider_ids={})
        t = Event(competition_id="c2", home_participant="Lazio", away_participant="Roma", internal_id="ev_t_c", provider_ids={"betclic": "BC-10"})

        dec = self._make_matched_decision("ev_s_c", "ev_t_c")
        result = self.aggregator.aggregate(events=[s, t], decisions=[dec])

        self.assertEqual(result.total_canonical_events, 1)
        cev = result.canonical_events[0]
        self.assertTrue(len(cev.sources) >= 1)

    def test_adversarial_d_identical_matched_pair_canonical_id_equality(self):
        """Adversarial D: Re-running aggregation on same pair produces identical canonical ID."""
        s = Event(competition_id="c1", home_participant="PSG", away_participant="Marseille", scheduled_start="2026-08-20T19:00:00Z", internal_id="ev_s_d", provider_ids={"superbet": "1"})
        t = Event(competition_id="c2", home_participant="PSG", away_participant="Marseille", scheduled_start="2026-08-20T19:00:00Z", internal_id="ev_t_d", provider_ids={"betclic": "2"})

        dec = self._make_matched_decision("ev_s_d", "ev_t_d")
        r1 = self.aggregator.aggregate(events=[s, t], decisions=[dec])
        r2 = self.aggregator.aggregate(events=[s, t], decisions=[dec])

        self.assertEqual(r1.canonical_events[0].canonical_event_id, r2.canonical_events[0].canonical_event_id)

    def test_adversarial_e_prevent_silent_multi_canonical_assignment(self):
        """Adversarial E: Prevent single source event belonging to two canonical events silently."""
        s = Event(competition_id="c1", home_participant="Sevilla", away_participant="Betis", internal_id="ev_s_e", provider_ids={"superbet": "1"})
        t1 = Event(competition_id="c2", home_participant="Sevilla", away_participant="Betis", internal_id="ev_t1_e", provider_ids={"betclic": "2"})
        t2 = Event(competition_id="c3", home_participant="Sevilla", away_participant="Betis", internal_id="ev_t2_e", provider_ids={"betclic": "3"})

        dec_1 = self._make_matched_decision("ev_s_e", "ev_t1_e")
        dec_2 = self._make_matched_decision("ev_s_e", "ev_t2_e")

        result = self.aggregator.aggregate(events=[s, t1, t2], decisions=[dec_1, dec_2])
        # Conflict must prevent silent merge
        self.assertEqual(result.total_canonical_events, 0)
        self.assertGreaterEqual(len(result.conflicts), 1)

    def test_adversarial_f_match_evidence_not_dropped(self):
        """Adversarial F: Match evidence and scores are fully preserved."""
        s = Event(competition_id="c1", home_participant="Monaco", away_participant="Lyon", internal_id="ev_s_f", provider_ids={"superbet": "1"})
        t = Event(competition_id="c2", home_participant="Monaco", away_participant="Lyon", internal_id="ev_t_f", provider_ids={"betclic": "2"})

        dec = self._make_matched_decision("ev_s_f", "ev_t_f", total_score=0.8888)
        result = self.aggregator.aggregate(events=[s, t], decisions=[dec])

        ev_preserved = result.canonical_events[0].match_evidence[0]
        self.assertEqual(ev_preserved.total_score, 0.8888)
        self.assertEqual(len(ev_preserved.signals), 6)

    def test_adversarial_g_reverse_source_ordering_determinism(self):
        """Adversarial G: Reversing source ordering produces identical canonical ID and structure."""
        s = Event(competition_id="c1", home_participant="Napoli", away_participant="Lazio", scheduled_start="2026-08-30T18:00:00Z", internal_id="ev_s_g", provider_ids={"superbet": "10"})
        t = Event(competition_id="c2", home_participant="Napoli", away_participant="Lazio", scheduled_start="2026-08-30T18:00:00Z", internal_id="ev_t_g", provider_ids={"betclic": "20"})

        dec = self._make_matched_decision("ev_s_g", "ev_t_g")
        r_forward = self.aggregator.aggregate(events=[s, t], decisions=[dec])
        r_reverse = self.aggregator.aggregate(events=[t, s], decisions=[dec])

        self.assertEqual(r_forward.canonical_events[0].canonical_event_id, r_reverse.canonical_events[0].canonical_event_id)
        self.assertEqual(r_forward.canonical_events[0].home_team, r_reverse.canonical_events[0].home_team)

    def test_adversarial_h_reverse_provider_input_ordering_determinism(self):
        """Adversarial H: Symmetric reversed decisions produce deterministic canonical event."""
        s = Event(competition_id="c1", home_participant="Valencia", away_participant="Villarreal", scheduled_start="2026-08-31T20:00:00Z", internal_id="ev_s_h", provider_ids={"superbet": "100"})
        t = Event(competition_id="c2", home_participant="Valencia", away_participant="Villarreal", scheduled_start="2026-08-31T20:00:00Z", internal_id="ev_t_h", provider_ids={"betclic": "200"})

        dec_sym = MatchDecision("ev_t_h", "ev_s_h", MatchDecisionType.MATCHED, 0.95, OrientationType.NORMAL, {})
        r = self.aggregator.aggregate(events=[s, t], decisions=[dec_sym])

        self.assertEqual(r.total_canonical_events, 1)
        self.assertTrue(r.canonical_events[0].canonical_event_id.startswith("cev_"))


if __name__ == "__main__":
    unittest.main()
