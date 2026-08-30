"""
Stage 21B Event Matching Rebuild Unit & Regression Test Suite
Strict limit: exactly 10 targeted tests.
"""

import pytest
from datetime import datetime, timezone
from domain.models import Event, Competition
from normalization.identity import normalize_team_name, compare_teams, TeamReference, parse_kickoff_to_utc
from normalization.candidate_generator import EventCandidate, EventCandidateGenerator
from normalization.matcher import EventMatcher, MatchDecisionType, OrientationType, MatcherConfig


def _create_event(h: str, a: str, start: str = "2026-08-25T20:00:00Z", eid: str = "e1", prov: str = "sb") -> Event:
    return Event(
        competition_id="c1",
        home_participant=h,
        away_participant=a,
        scheduled_start=start,
        provider_ids={prov: eid},
        internal_id=f"int_{eid}",
    )


def test_1_team_alias_normalization():
    """Test 1: Normalizes transliterations and city aliases (e.g. Mediolan -> milan, Zagrzeb -> zagreb)."""
    norm, tokens = normalize_team_name("Inter Mediolan")
    assert "milan" in tokens
    assert "inter" in tokens

    norm2, tokens2 = normalize_team_name("Dinamo Zagrzeb")
    assert "zagreb" in tokens2


def test_2_gender_suffix_normalization_compatibility():
    """Test 2: Women team representations across providers (K vs W vs Women) match without false suffix veto."""
    matcher = EventMatcher()
    e1 = _create_event("Millonarios FC K.", "Internacional de Bogota W.", eid="e1", prov="betclic")
    e2 = _create_event("Millonarios (K)", "Internacional de Bogota (K)", eid="e2", prov="superbet")

    cand = EventCandidate("int_e1", "int_e2", "betclic", "superbet", ("KEY",), {})
    decision = matcher.score_candidate(cand, e1, e2)

    assert decision.decision == MatchDecisionType.MATCHED
    assert len(decision.veto_reasons) == 0


def test_3_noise_token_filtering_preserves_core_match():
    """Test 3: Club prefixes and noise tokens (CA, FC, de, del) do not degrade core team matches."""
    ref_a = TeamReference.from_raw("CA Lanus")
    ref_b = TeamReference.from_raw("Atletico Lanus")
    comp = compare_teams(ref_a, ref_b)
    assert comp.token_overlap >= 0.90


def test_4_regional_state_and_descriptor_synonyms():
    """Test 4: State descriptors (Mineiro <-> MG, Paranaense <-> PR) match with high confidence."""
    matcher = EventMatcher()
    e1 = Event(
        competition_id="c1",
        home_participant="Sport Recife",
        away_participant="America Mineiro",
        scheduled_start="2026-08-25T20:00:00Z",
        provider_ids={"superbet": "e1"},
        external_ids={"betradar": "sr:match:1234"},
        internal_id="int_e1",
    )
    e2 = Event(
        competition_id="c1",
        home_participant="Sport Recife",
        away_participant="America MG",
        scheduled_start="2026-08-25T20:00:00Z",
        provider_ids={"betclic": "e2"},
        external_ids={"betradar": "sr:match:1234"},
        internal_id="int_e2",
    )

    cand = EventCandidate("int_e1", "int_e2", "superbet", "betclic", ("KEY",), {})
    decision = matcher.score_candidate(cand, e1, e2)

    assert decision.decision == MatchDecisionType.MATCHED
    assert decision.total_score >= 0.80


def test_5_competition_mismatch_is_soft_signal():
    """Test 5: Different competition names do not veto when teams and kickoff match."""
    matcher = EventMatcher()
    e1 = _create_event("Valencia", "Betis", eid="e1", prov="superbet")
    e2 = _create_event("Valencia", "Betis", eid="e2", prov="betclic")

    c1 = Competition(name="La Liga EA Sports", sport="Football", internal_id="c1")
    c2 = Competition(name="Hiszpania 1", sport="Football", internal_id="c2")

    cand = EventCandidate("int_e1", "int_e2", "superbet", "betclic", ("KEY",), {})
    decision = matcher.score_candidate(cand, e1, e2, c1, c2)

    assert decision.decision == MatchDecisionType.MATCHED
    assert decision.total_score >= 0.90


def test_6_kickoff_timezone_normalization():
    """Test 6: Parses different timezone representations (UTC 'Z' vs offset +02:00) into matching UTC timestamps."""
    dt_utc = parse_kickoff_to_utc("2026-08-25T18:00:00Z")
    dt_offset = parse_kickoff_to_utc("2026-08-25T20:00:00+02:00")

    assert dt_utc == dt_offset


def test_7_home_away_orientation_and_anti_swap_integrity():
    """Test 7: Swapped teams (Real Madrid vs Barcelona vs Barcelona vs Real Madrid) are marked ORIENTATION_SWAP."""
    matcher = EventMatcher()
    e1 = _create_event("Real Madrid", "Barcelona", eid="e1", prov="superbet")
    e2 = _create_event("Barcelona", "Real Madrid", eid="e2", prov="betclic")

    cand = EventCandidate("int_e1", "int_e2", "superbet", "betclic", ("KEY",), {})
    decision = matcher.score_candidate(cand, e1, e2)

    assert decision.orientation == OrientationType.ORIENTATION_SWAP
    assert "orientation_swap_detected" in decision.warnings


def test_8_false_positive_protection():
    """Test 8: Completely different events (Real Madrid vs Barcelona vs Sevilla vs Betis) are REJECTED."""
    matcher = EventMatcher()
    e1 = _create_event("Real Madrid", "Barcelona", eid="e1", prov="superbet")
    e2 = _create_event("Sevilla", "Betis", eid="e2", prov="betclic")

    cand = EventCandidate("int_e1", "int_e2", "superbet", "betclic", ("KEY",), {})
    decision = matcher.score_candidate(cand, e1, e2)

    assert decision.decision == MatchDecisionType.REJECTED


def test_9_borderline_ambiguity_handling():
    """Test 9: Genuine incomplete team references (America vs America de Natal) resolve to AMBIGUOUS."""
    matcher = EventMatcher()
    e1 = _create_event("America", "Athletic Club", eid="e1", prov="superbet")
    e2 = _create_event("America de Natal", "Athletic Club", eid="e2", prov="betclic")

    cand = EventCandidate("int_e1", "int_e2", "superbet", "betclic", ("KEY",), {})
    decision = matcher.score_candidate(cand, e1, e2)

    assert decision.decision == MatchDecisionType.AMBIGUOUS


def test_10_end_to_end_canonical_event_matching():
    """Test 10: End-to-end candidate generation and match scoring across multi-provider dataset."""
    generator = EventCandidateGenerator()
    matcher = EventMatcher()

    events_sb = [
        _create_event("Valencia", "Betis", "2026-08-25T19:00:00Z", "sb1", "superbet"),
        Event(
            competition_id="c1",
            home_participant="Sport Recife",
            away_participant="America Mineiro",
            scheduled_start="2026-08-25T23:00:00Z",
            provider_ids={"superbet": "sb2"},
            external_ids={"betradar": "sr:match:5678"},
            internal_id="int_sb2",
        ),
    ]
    events_bc = [
        _create_event("Valencia", "Betis", "2026-08-25T19:00:00Z", "bc1", "betclic"),
        Event(
            competition_id="c1",
            home_participant="Sport Recife",
            away_participant="America MG",
            scheduled_start="2026-08-25T23:00:00Z",
            provider_ids={"betclic": "bc2"},
            external_ids={"betradar": "sr:match:5678"},
            internal_id="int_bc2",
        ),
    ]

    cand_res = generator.generate_candidates_n_way(events_sb + events_bc)
    assert cand_res.total_candidates == 2

    ev_map = {e.internal_id: e for e in events_sb + events_bc}
    match_res = matcher.match_candidates(cand_res.candidates, ev_map, ev_map)

    assert match_res.matched_count == 2
    assert match_res.rejected_count == 0
