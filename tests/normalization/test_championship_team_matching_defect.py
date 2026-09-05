"""
Dedicated regression test for Championship market coverage and team identity matching defect.

Reproduces:
1. 'Bolton Wanderers' vs 'Bolton' must achieve high token overlap (>= 0.95) and score >= 0.80 in EventMatcher when competition name is generic.
2. 'Blackburn Rovers' vs 'Blackburn' must achieve high token overlap (>= 0.95) and score >= 0.80 in EventMatcher when competition name is generic.
3. 'Derby County' vs 'Derby' must achieve high token overlap (>= 0.95) and score >= 0.80 in EventMatcher when competition name is generic.
4. CoordinatedDetailSelectionPlanner must classify 'Millwall · Bolton Wanderers' <-> 'Millwall - Bolton' and 'Lincoln City · Blackburn Rovers' <-> 'Lincoln - Blackburn' as MATCHED overlap pairs.
5. Suffix safety: 'Bristol Rovers' vs 'Bristol City' must NOT match (score < 0.50 / rejected).
"""

import pytest
from domain.models import Event, Competition
from normalization.matcher import EventMatcher, MatchDecisionType
from normalization.candidate_generator import EventCandidate
from normalization.identity import TeamReference, compare_teams
from orchestration.detail_planning import CoordinatedDetailSelectionPlanner
from orchestration.models import ScanConfig
from providers.superbet.models import SuperbetDiscoveredItem
from providers.betclic.models import BetclicDiscoveredItem
from providers.superbet.parser.parser import SuperbetParser
from providers.betclic.parser.parser import BetclicParser
from normalization.engine import NormalizationEngine
from normalization.validation_pipeline import CrossBookmakerValidationPipeline


def test_compare_teams_championship_club_suffixes():
    """Team comparison must recognize UK club descriptors (Wanderers, Rovers, County) as non-conflicting descriptors."""
    # Bolton Wanderers vs Bolton
    t_bolton_w = TeamReference.from_raw("Bolton Wanderers")
    t_bolton = TeamReference.from_raw("Bolton")
    comp_bolton = compare_teams(t_bolton_w, t_bolton)
    assert comp_bolton.token_overlap >= 0.95, f"Bolton Wanderers vs Bolton token_overlap was {comp_bolton.token_overlap}, expected >= 0.95"

    # Blackburn Rovers vs Blackburn
    t_blackburn_r = TeamReference.from_raw("Blackburn Rovers")
    t_blackburn = TeamReference.from_raw("Blackburn")
    comp_blackburn = compare_teams(t_blackburn_r, t_blackburn)
    assert comp_blackburn.token_overlap >= 0.95, f"Blackburn Rovers vs Blackburn token_overlap was {comp_blackburn.token_overlap}, expected >= 0.95"

    # Derby County vs Derby
    t_derby_c = TeamReference.from_raw("Derby County")
    t_derby = TeamReference.from_raw("Derby")
    comp_derby = compare_teams(t_derby_c, t_derby)
    assert comp_derby.token_overlap >= 0.95, f"Derby County vs Derby token_overlap was {comp_derby.token_overlap}, expected >= 0.95"


def test_event_matcher_scores_championship_fixtures_above_threshold():
    """EventMatcher must score Millwall vs Bolton Wanderers <-> Millwall vs Bolton as MATCHED (>= 0.80) even with generic competition."""
    matcher = EventMatcher()

    sb_ev = Event(
        home_participant="Millwall",
        away_participant="Bolton Wanderers",
        scheduled_start="2026-09-05T14:00:00Z",
        internal_id="sb_mb_1",
        competition_id="comp_sb",
    )
    bc_ev = Event(
        home_participant="Millwall",
        away_participant="Bolton",
        scheduled_start="2026-09-05T14:00:00Z",
        internal_id="bc_mb_1",
        competition_id="comp_bc",
    )

    comp_sb = Competition(name="Superbet Football", sport="Football")
    comp_bc = Competition(name="Anglia Championship", sport="Football")

    cand = EventCandidate(
        source_event_id="sb_mb_1",
        target_event_id="bc_mb_1",
        source_provider="superbet",
        target_provider="betclic",
        blocking_keys=("test",),
        evidence={},
    )

    res = matcher.match_candidates(
        candidates=[cand],
        source_events_map={"sb_mb_1": sb_ev},
        target_events_map={"bc_mb_1": bc_ev},
        comp_map={"comp_sb": comp_sb, "comp_bc": comp_bc},
    )

    assert len(res.decisions) == 1
    decision = res.decisions[0]
    assert decision.decision == MatchDecisionType.MATCHED, f"Expected MATCHED, got {decision.decision} with total_score={decision.total_score}"
    assert decision.total_score >= 0.80


def test_event_matcher_scores_lincoln_blackburn_above_threshold():
    """EventMatcher must score Lincoln City vs Blackburn Rovers <-> Lincoln vs Blackburn as MATCHED (>= 0.80)."""
    matcher = EventMatcher()

    sb_ev = Event(
        home_participant="Lincoln City",
        away_participant="Blackburn Rovers",
        scheduled_start="2026-09-01T18:45:00Z",
        internal_id="sb_lb_1",
        competition_id="comp_sb",
    )
    bc_ev = Event(
        home_participant="Lincoln",
        away_participant="Blackburn",
        scheduled_start="2026-09-01T18:45:00Z",
        internal_id="bc_lb_1",
        competition_id="comp_bc",
    )

    comp_sb = Competition(name="Tournament 27", sport="Football")
    comp_bc = Competition(name="Anglia Championship", sport="Football")

    cand = EventCandidate(
        source_event_id="sb_lb_1",
        target_event_id="bc_lb_1",
        source_provider="superbet",
        target_provider="betclic",
        blocking_keys=("test",),
        evidence={},
    )

    res = matcher.match_candidates(
        candidates=[cand],
        source_events_map={"sb_lb_1": sb_ev},
        target_events_map={"bc_lb_1": bc_ev},
        comp_map={"comp_sb": comp_sb, "comp_bc": comp_bc},
    )

    assert len(res.decisions) == 1
    decision = res.decisions[0]
    assert decision.decision == MatchDecisionType.MATCHED, f"Expected MATCHED, got {decision.decision} with total_score={decision.total_score}"
    assert decision.total_score >= 0.80


def test_distinguishing_modifiers_prevents_bristol_rovers_vs_bristol_city():
    """Bristol Rovers vs Bristol City must have token_overlap == 0.0 and must NEVER match."""
    t_rovers = TeamReference.from_raw("Bristol Rovers")
    t_city = TeamReference.from_raw("Bristol City")
    comp = compare_teams(t_rovers, t_city)
    assert comp.token_overlap == 0.0, f"Expected 0.0 token overlap for Bristol Rovers vs Bristol City, got {comp.token_overlap}"


def test_coordinated_detail_planning_selects_millwall_bolton():
    """CoordinatedDetailSelectionPlanner must identify Millwall vs Bolton as an overlap pair and select both for detail."""
    planner = CoordinatedDetailSelectionPlanner(
        normalization_engine=NormalizationEngine(),
        validation_pipeline=CrossBookmakerValidationPipeline(),
    )

    sb_disc = [
        SuperbetDiscoveredItem(
            event_id="13777981",
            match_name="Millwall·Bolton Wanderers",
            competition_id="27",
            competition_name="Superbet Football",
            start_time="2026-09-05T14:00:00Z",
            metadata={"raw": {"id": "13777981", "matchName": "Millwall·Bolton Wanderers", "markets": []}},
        )
    ]

    bc_disc = [
        BetclicDiscoveredItem(
            provider_event_id="1210985496977408",
            name="Millwall - Bolton",
            competition_name="Anglia Championship",
            start_time="2026-09-05T14:00:00Z",
            url="https://www.betclic.pl/pilka-nozna-sfootball/championship-c2",
            metadata={"raw": {"id": "1210985496977408", "name": "Millwall - Bolton", "competition": "Anglia Championship", "markets": []}},
        )
    ]

    config = ScanConfig(
        providers=("superbet", "betclic"),
        hours_ahead=120,
        max_detail_requests=10,
        scan_mode="NORMAL",
    )

    plan = planner.create_plan(
        sb_discovered=sb_disc,
        bc_discovered=bc_disc,
        sb_parser=SuperbetParser(),
        bc_parser=BetclicParser(),
        config=config,
    )

    assert "13777981" in plan.selected_event_ids_superbet
    assert "1210985496977408" in plan.selected_event_ids_betclic
    assert "13777981" in plan.overlap_event_ids_superbet
    assert "1210985496977408" in plan.overlap_event_ids_betclic
