"""
Unit tests for ValuebetQualityPolicy and Ranking Engine (Stage 9.2).
"""

from datetime import datetime, timezone
from decimal import Decimal
import pytest

from valuebets.models import ValueBetCandidate
from valuebets.quality_policy import (
    ValuebetQualityConfig,
    ValuebetQualityEvaluation,
    ValuebetQualityPolicy,
    ValuebetRejectionReason,
)


def _make_candidate(
    candidate_id: str = "cand_01",
    event_id: str = "ev_test_123",
    home_team: str = "Arsenal",
    away_team: str = "Chelsea",
    competition_name: str = "Premier League",
    value_percent: Decimal = Decimal("5.50"),
    reference_source: str = "the_odds_api",
    reference_bookmaker: str = "pinnacle",
    reference_timestamp: Optional[str] = None,
    bookmaker: str = "superbet",
    bookmaker_odds: Decimal = Decimal("2.10"),
    fair_odds: Decimal = Decimal("1.99"),
    fair_probability: Decimal = Decimal("0.5025"),
    kickoff_time: str = "2026-08-18T18:00:00Z",
    market_type: str = "1X2",
    selection_type: str = "HOME",
    line: Optional[Decimal] = None,
) -> ValueBetCandidate:
    event_name = f"{home_team} vs {away_team}"
    return ValueBetCandidate(
        candidate_id=candidate_id,
        canonical_event_id=event_id,
        event_name=event_name,
        sport="soccer",
        competition_name=competition_name,
        kickoff=kickoff_time,
        market_type=market_type,
        line=line,
        selection_type=selection_type,
        bookmaker=bookmaker,
        bookmaker_odds=bookmaker_odds,
        bookmaker_implied_prob=Decimal("1") / bookmaker_odds if bookmaker_odds > 0 else Decimal("0"),
        reference_source=reference_source,
        reference_bookmaker=reference_bookmaker,
        reference_raw_odds=Decimal("2.00"),
        reference_overround=Decimal("1.0573"),
        reference_fair_probability=fair_probability,
        reference_fair_odds=fair_odds,
        value_edge=value_percent / Decimal("100"),
        value_percent=value_percent,
        is_qualified=True,
        reference_timestamp=reference_timestamp,
    )


class TestValuebetQualityPolicy:
    def test_default_evaluation_top_flight(self):
        policy = ValuebetQualityPolicy()
        cand = _make_candidate(competition_name="Premier League", value_percent=Decimal("4.00"))
        eval_res = policy.evaluate_quality(cand)

        assert eval_res.is_qualified is True
        assert eval_res.competition_tier == 0
        assert eval_res.tier_name == "Tier 0 (Top Flight)"
        assert eval_res.quality_score >= 60.0
        assert len(eval_res.rejection_reasons) == 0

    def test_competition_tier_assignment(self):
        policy = ValuebetQualityPolicy()
        assert policy.calculate_competition_tier("UEFA Champions League") == 0
        assert policy.calculate_competition_tier("La Liga") == 0
        assert policy.calculate_competition_tier("Ekstraklasa") == 0
        assert policy.calculate_competition_tier("Championship") == 1
        assert policy.calculate_competition_tier("Unknown Regional League") == 2

    def test_rejection_low_value(self):
        config = ValuebetQualityConfig(min_value_percent=Decimal("2.0"))
        policy = ValuebetQualityPolicy(config=config)
        cand = _make_candidate(value_percent=Decimal("0.50"))
        eval_res = policy.evaluate_quality(cand)

        assert eval_res.is_qualified is False
        assert ValuebetRejectionReason.LOW_VALUE in eval_res.rejection_reasons

    def test_rejection_low_quality_score(self):
        config = ValuebetQualityConfig(min_quality_score=75.0)
        policy = ValuebetQualityPolicy(config=config)
        cand = _make_candidate(competition_name="Unknown League", value_percent=Decimal("1.10"))
        eval_res = policy.evaluate_quality(cand)

        assert eval_res.is_qualified is False
        assert ValuebetRejectionReason.LOW_QUALITY_SCORE in eval_res.rejection_reasons

    def test_freshness_penalty(self):
        policy = ValuebetQualityPolicy()
        eval_time = datetime(2026, 8, 17, 12, 30, 0, tzinfo=timezone.utc)
        
        # Fresh reference (10s old)
        cand_fresh = _make_candidate(reference_timestamp="2026-08-17T12:29:50Z")
        eval_fresh = policy.evaluate_quality(cand_fresh, current_time=eval_time)

        # Stale reference (15 minutes old)
        cand_stale = _make_candidate(reference_timestamp="2026-08-17T12:15:00Z")
        eval_stale = policy.evaluate_quality(cand_stale, current_time=eval_time)

        assert eval_fresh.score_breakdown["reference_freshness_score"] > eval_stale.score_breakdown["reference_freshness_score"]
        assert eval_fresh.quality_score > eval_stale.quality_score

    def test_ranking_multiple_candidates(self):
        policy = ValuebetQualityPolicy()
        c1 = _make_candidate(candidate_id="c1", value_percent=Decimal("6.0"), competition_name="Premier League")
        c2 = _make_candidate(candidate_id="c2", value_percent=Decimal("2.0"), competition_name="Championship")
        c3 = _make_candidate(candidate_id="c3", value_percent=Decimal("8.0"), competition_name="La Liga")

        ranked = policy.rank_candidates([c1, c2, c3])
        # Higher score / higher value first
        assert [c.candidate_id for c in ranked] == ["c3", "c1", "c2"]
