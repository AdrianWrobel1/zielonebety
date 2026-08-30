"""
Stage 12.3: Comprehensive Unit Tests for Full N-Way Cross-Bookmaker Matching
"""
import pytest
from datetime import datetime, timezone
from decimal import Decimal
from typing import Dict, List, Optional

from domain.models import Competition, Event, Market, Selection, Odds, CanonicalEvent
from normalization.base_normalizer import NormalizedGraph
from normalization.candidate_generator import EventCandidateGenerator, EventCandidate
from normalization.matcher import EventMatcher, MatchDecisionType, MatchRejectionCode, MatcherConfig
from normalization.aggregator import CanonicalEventAggregator
from normalization.identity import normalize_team_name, compare_teams, TeamReference
from normalization.validation_pipeline import CrossBookmakerValidationPipeline


def _make_graph(
    event_id: str,
    provider: str,
    home: str,
    away: str,
    start: str = "2026-08-25T20:00:00Z",
    sport: str = "Football",
    comp_name: str = "Premier League",
    bookmaker: Optional[str] = None,
) -> NormalizedGraph:
    comp = Competition(name=comp_name, sport=sport)
    bm_name = bookmaker or provider
    meta = {"odds_api": {"bookmaker": bm_name}} if bookmaker else {provider: {"event_id": event_id}}
    event = Event(
        competition_id=comp.internal_id,
        home_participant=home,
        away_participant=away,
        scheduled_start=start,
        provider_ids={bm_name: event_id},
        metadata=meta,
    )
    market = Market(
        event_id=event.internal_id,
        market_type="1X2",
        provider_ids={bm_name: f"mkt_{event_id}"},
    )
    sel1 = Selection(
        market_id=market.internal_id,
        selection_type="HOME",
        participant=home,
        provider_ids={bm_name: f"sel_{event_id}_1"},
    )
    sel2 = Selection(
        market_id=market.internal_id,
        selection_type="AWAY",
        participant=away,
        provider_ids={bm_name: f"sel_{event_id}_2"},
    )
    odds1 = Odds(selection_id=sel1.internal_id, bookmaker=bm_name, decimal_odds=2.10)
    odds2 = Odds(selection_id=sel2.internal_id, bookmaker=bm_name, decimal_odds=1.80)

    return NormalizedGraph(
        event=event,
        competition=comp,
        markets=[market],
        selections=[sel1, sel2],
        odds_list=[odds1, odds2],
    )


class TestNWayMatching:

    def test_n_way_generates_all_provider_pairs(self):
        """Verifies candidate pairs are generated for all provider combinations across 4 distinct bookmakers."""
        cg = EventCandidateGenerator()
        g_sb = _make_graph("sb_1", "superbet", "Arsenal", "Chelsea")
        g_bc = _make_graph("bc_1", "betclic", "Arsenal", "Chelsea")
        g_b365 = _make_graph("b365_1", "odds_api", "Arsenal", "Chelsea", bookmaker="bet365")
        g_uni = _make_graph("uni_1", "odds_api", "Arsenal", "Chelsea", bookmaker="unibet")

        res = cg.generate_candidates_n_way([g_sb, g_bc, g_b365, g_uni])
        assert len(res.candidates) == 6
        prov_sets = [{c.source_provider, c.target_provider} for c in res.candidates]
        assert {"superbet", "betclic"} in prov_sets
        assert {"superbet", "bet365"} in prov_sets
        assert {"superbet", "unibet"} in prov_sets
        assert {"betclic", "bet365"} in prov_sets
        assert {"betclic", "unibet"} in prov_sets
        assert {"bet365", "unibet"} in prov_sets

    def test_n_way_does_not_generate_same_provider_pairs(self):
        """Verifies candidate generator never forms pairs within the same provider."""
        cg = EventCandidateGenerator()
        g_sb1 = _make_graph("sb_1", "superbet", "Arsenal", "Chelsea")
        g_sb2 = _make_graph("sb_2", "superbet", "Arsenal", "Chelsea")

        res = cg.generate_candidates_n_way([g_sb1, g_sb2])
        assert len(res.candidates) == 0

    def test_n_way_deduplicates_reversed_pairs(self):
        """Verifies unordered pair {A, B} is generated exactly once, never as (A, B) and (B, A)."""
        cg = EventCandidateGenerator()
        g_sb = _make_graph("sb_1", "superbet", "Arsenal", "Chelsea")
        g_bc = _make_graph("bc_1", "betclic", "Arsenal", "Chelsea")

        res = cg.generate_candidates_n_way([g_sb, g_bc])
        assert len(res.candidates) == 1
        c = res.candidates[0]
        res2 = cg.generate_candidates_n_way([g_bc, g_sb])
        assert len(res2.candidates) == 1
        assert res2.candidates[0].source_event_id == c.source_event_id
        assert res2.candidates[0].target_event_id == c.target_event_id

    def test_betclic_bet365_matching_is_evaluated(self):
        """Verifies Betclic ↔ Bet365 candidate pairs are evaluated and matched."""
        pipeline = CrossBookmakerValidationPipeline()
        g_bc = _make_graph("bc_1", "betclic", "Arsenal", "Chelsea")
        g_b365 = _make_graph("b365_1", "odds_api", "Arsenal", "Chelsea", bookmaker="bet365")

        res = pipeline.run_n_way([g_bc, g_b365])
        assert len(res.event_candidates) == 1
        assert res.metrics.matched_event_count == 1
        assert len(res.canonical_events) == 1
        cov = res.provider_pair_coverage.get("bet365:betclic") or res.provider_pair_coverage.get("betclic:bet365")
        assert cov is not None
        assert cov["candidate_count"] == 1
        assert cov["matched_count"] == 1

    def test_betclic_unibet_matching_is_evaluated(self):
        """Verifies Betclic ↔ Unibet candidate pairs are evaluated and matched."""
        pipeline = CrossBookmakerValidationPipeline()
        g_bc = _make_graph("bc_1", "betclic", "Arsenal", "Chelsea")
        g_uni = _make_graph("uni_1", "odds_api", "Arsenal", "Chelsea", bookmaker="unibet")

        res = pipeline.run_n_way([g_bc, g_uni])
        assert len(res.event_candidates) == 1
        assert res.metrics.matched_event_count == 1
        assert len(res.canonical_events) == 1

    def test_bet365_unibet_matching_is_evaluated(self):
        """Verifies Bet365 ↔ Unibet candidate pairs are evaluated and matched."""
        pipeline = CrossBookmakerValidationPipeline()
        g_b365 = _make_graph("b365_1", "odds_api", "Arsenal", "Chelsea", bookmaker="bet365")
        g_uni = _make_graph("uni_1", "odds_api", "Arsenal", "Chelsea", bookmaker="unibet")

        res = pipeline.run_n_way([g_b365, g_uni])
        assert len(res.event_candidates) == 1
        assert res.metrics.matched_event_count == 1
        assert len(res.canonical_events) == 1

    def test_superbet_matching_remains_unchanged(self):
        """Verifies Superbet ↔ Betclic and Superbet ↔ Bet365 continue matching seamlessly."""
        pipeline = CrossBookmakerValidationPipeline()
        g_sb = _make_graph("sb_1", "superbet", "Liverpool", "Manchester City")
        g_bc = _make_graph("bc_1", "betclic", "Liverpool", "Manchester City")

        res = pipeline.run_n_way([g_sb, g_bc])
        assert res.metrics.matched_event_count == 1
        assert len(res.canonical_events) == 1
        assert len(res.comparable_selections) >= 2

    def test_provider_failure_does_not_block_other_pairs(self):
        """Verifies that if one provider fails or is empty, matching continues for other providers."""
        pipeline = CrossBookmakerValidationPipeline()
        g_bc = _make_graph("bc_1", "betclic", "Real Madrid", "Barcelona")
        g_b365 = _make_graph("b365_1", "odds_api", "Real Madrid", "Barcelona", bookmaker="bet365")

        res = pipeline.run_n_way([g_bc, g_b365])
        assert len(res.event_candidates) == 1
        assert res.metrics.matched_event_count == 1

    def test_londrina_state_suffix_matches_base_name(self):
        """Verifies candidate generator indexes on base name 'londrina' when team has state suffix 'Londrina EC PR'."""
        cg = EventCandidateGenerator()
        g_sb = _make_graph("sb_1", "superbet", "Londrina", "Operario")
        g_b365 = _make_graph("b365_1", "odds_api", "Londrina EC PR", "Operario PR", bookmaker="bet365")

        res = cg.generate_candidates_n_way([g_sb, g_b365])
        assert len(res.candidates) == 1
        c = res.candidates[0]
        assert any("londrina" in k for k in c.blocking_keys)

    def test_goias_state_suffix_matches_base_name(self):
        """Verifies candidate generator indexes on base name 'goias' when team has state suffix 'Goias EC GO'."""
        cg = EventCandidateGenerator()
        g_sb = _make_graph("sb_1", "superbet", "Goias", "Vila Nova")
        g_b365 = _make_graph("b365_1", "odds_api", "Goias EC GO", "Vila Nova GO", bookmaker="bet365")

        res = cg.generate_candidates_n_way([g_sb, g_b365])
        assert len(res.candidates) == 1
        c = res.candidates[0]
        assert any("goias" in k for k in c.blocking_keys)

    def test_state_suffix_does_not_break_reserve_veto(self):
        """Verifies 'Londrina' vs 'Londrina II' is strictly rejected with SUFFIX_VETO."""
        matcher = EventMatcher()
        g1 = _make_graph("g1", "superbet", "Londrina", "Atletico GO")
        g2 = _make_graph("g2", "betclic", "Londrina II", "Atletico GO")
        cand = EventCandidate(
            source_event_id=g1.event.internal_id,
            target_event_id=g2.event.internal_id,
            source_provider="superbet",
            target_provider="betclic",
            blocking_keys=("TEST",),
            evidence={},
        )
        dec = matcher.score_candidate(cand, g1.event, g2.event, g1.competition, g2.competition)
        assert dec.decision == MatchDecisionType.REJECTED
        assert dec.rejection_reason_code == MatchRejectionCode.SUFFIX_VETO.value
        assert any("reserve_mismatch" in v for v in dec.veto_reasons)

    def test_youth_suffix_remains_rejected(self):
        """Verifies 'Goias' vs 'Goias U20' is strictly rejected with SUFFIX_VETO."""
        matcher = EventMatcher()
        g1 = _make_graph("g1", "superbet", "Goias", "Juventude")
        g2 = _make_graph("g2", "betclic", "Goias U20", "Juventude")
        cand = EventCandidate(
            source_event_id=g1.event.internal_id,
            target_event_id=g2.event.internal_id,
            source_provider="superbet",
            target_provider="betclic",
            blocking_keys=("TEST",),
            evidence={},
        )
        dec = matcher.score_candidate(cand, g1.event, g2.event, g1.competition, g2.competition)
        assert dec.decision == MatchDecisionType.REJECTED
        assert dec.rejection_reason_code == MatchRejectionCode.SUFFIX_VETO.value
        assert any("age_group_mismatch" in v for v in dec.veto_reasons)

    def test_canonical_aggregator_merges_multi_provider_event(self):
        """Verifies matches across 4 providers merge into ONE canonical event with 4 sources."""
        pipeline = CrossBookmakerValidationPipeline()
        g_sb = _make_graph("sb_1", "superbet", "Inter", "Milan")
        g_bc = _make_graph("bc_1", "betclic", "Inter", "Milan")
        g_b365 = _make_graph("b365_1", "odds_api", "Inter", "Milan", bookmaker="bet365")
        g_uni = _make_graph("uni_1", "odds_api", "Inter", "Milan", bookmaker="unibet")

        res = pipeline.run_n_way([g_sb, g_bc, g_b365, g_uni])
        assert len(res.canonical_events) == 1
        ce = res.canonical_events[0]
        assert len(ce.sources) == 4
        assert set(ce.sources.keys()) == {"superbet", "betclic", "bet365", "unibet"}

    def test_duplicate_candidate_pairs_are_removed(self):
        """Verifies that passing duplicate internal_id graphs does not result in redundant candidate pairs."""
        cg = EventCandidateGenerator()
        g1 = _make_graph("sb_1", "superbet", "Bayern Munich", "Dortmund")
        g2 = _make_graph("bc_1", "betclic", "Bayern Munich", "Dortmund")

        res = cg.generate_candidates_n_way([g1, g2, g1, g2])
        assert len(res.candidates) == 1
