"""
Stage 9.2: Valuebet Quality Policy & Deterministic Ranking Engine

Provides pure, explainable, financial-grade quality scoring and deterministic
ranking for ValueBetCandidate instances.

Core Principles:
- Single Responsibility: Evaluates whether a detected valuebet meets quality and liquidity standards.
- Deterministic Scoring: 0.0 - 100.0 multi-factor quality score factoring Value %, Competition Tier,
  Reference Freshness, Market Reliability, and Kickoff Proximity.
- Stable, Pure Ranking: Deterministically orders valuebet candidates such that identical inputs
  always yield identical ordering across cycles.
- Zero External Coupling: Pure decision logic with no database, network, or external side effects.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
import math
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from normalization.quality_policy import calculate_default_competition_tier
from valuebets.models import ValueBetCandidate


class ValuebetRejectionReason(str, Enum):
    """Authoritative rejection reasons for valuebet qualification."""
    VALUE_BELOW_MINIMUM = "VALUE_BELOW_MINIMUM"
    LOW_VALUE = "LOW_VALUE"
    LOW_QUALITY_SCORE = "LOW_QUALITY_SCORE"
    INVALID_ODDS = "INVALID_ODDS"
    STALE_REFERENCE_DATA = "STALE_REFERENCE_DATA"
    DISQUALIFIED_BOOKMAKER = "DISQUALIFIED_BOOKMAKER"
    DISQUALIFIED_COMPETITION = "DISQUALIFIED_COMPETITION"
    EXCESSIVE_KICKOFF_HORIZON = "EXCESSIVE_KICKOFF_HORIZON"


@dataclass(frozen=True)
class ValuebetQualityConfig:
    """Configuration thresholds and weights for valuebet quality evaluation."""
    min_value_percent: Decimal = Decimal("3.0")
    max_value_sanity_cap: Decimal = Decimal("40.0")  # Palpable error cap
    min_bookmaker_odds: Decimal = Decimal("1.05")
    max_bookmaker_odds: Decimal = Decimal("35.0")
    min_quality_score: float = 0.0
    max_reference_age_seconds: int = 1800  # 30 minutes
    max_kickoff_hours_ahead: int = 168  # 7 days
    preferred_competitions: Tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ValuebetQualityEvaluation:
    """Auditable quality evaluation result for a ValueBetCandidate."""
    candidate: ValueBetCandidate
    quality_score: float
    is_qualified: bool
    competition_tier: int
    tier_name: str
    rejection_reasons: Tuple[ValuebetRejectionReason, ...] = field(default_factory=tuple)
    score_breakdown: Dict[str, float] = field(default_factory=dict)


class ValuebetQualityPolicy:
    """Evaluates and ranks ValueBetCandidate objects deterministically."""

    def __init__(self, config: Optional[ValuebetQualityConfig] = None) -> None:
        self.config = config or ValuebetQualityConfig()

    def calculate_competition_tier(self, competition_name: Optional[str]) -> int:
        """Assigns 0, 1, or 2 based on competition prestige and liquidity."""
        if not competition_name:
            return 2
        comp_lower = competition_name.lower().strip()
        if any(k in comp_lower for k in ("la liga", "laliga", "champions league", "premier league", "serie a", "bundesliga", "ligue 1", "ekstraklasa", "world cup", "euro")):
            return 0
        return calculate_default_competition_tier(competition_name)

    def evaluate_quality(
        self,
        candidate: ValueBetCandidate,
        current_time: Optional[datetime] = None,
    ) -> ValuebetQualityEvaluation:
        """Evaluates quality score (0.0 - 100.0) and qualification state for a candidate."""
        now = current_time or datetime.now(timezone.utc)
        rejection_reasons: List[ValuebetRejectionReason] = []
        score_breakdown: Dict[str, float] = {}

        # 1. Bookmaker Odds Sanity
        bm_odds = candidate.bookmaker_odds
        if bm_odds < self.config.min_bookmaker_odds or bm_odds > self.config.max_bookmaker_odds:
            rejection_reasons.append(ValuebetRejectionReason.INVALID_ODDS)

        # 2. Value Percent Bounds (gross diagnostic + net gate)
        val_pct = candidate.value_percent
        if val_pct < self.config.min_value_percent:
            rejection_reasons.append(ValuebetRejectionReason.LOW_VALUE)

        # P0-NEW-001: QUALIFIED => NET_EV >= threshold. Gross alone must not
        # qualify a taxed (e.g. Superbet 12%) opportunity whose net is negative.
        net_pct = candidate.net_value_percent
        if net_pct is not None and net_pct < self.config.min_value_percent:
            if ValuebetRejectionReason.LOW_VALUE not in rejection_reasons:
                rejection_reasons.append(ValuebetRejectionReason.LOW_VALUE)

        # 3. Competition Tier
        comp_name = candidate.competition_name or ""
        tier = self.calculate_competition_tier(comp_name)
        tier_names = {0: "Tier 0 (Top Flight)", 1: "Tier 1 (Secondary)", 2: "Tier 2 (Standard)"}
        tier_name = tier_names.get(tier, "Tier 2 (Standard)")

        # 4. Score Calculation (0.0 - 100.0)
        # Factor A: Value Percentage (0.0 - 40.0 pts)
        val_f = float(val_pct)
        # 3% gives ~12 pts, 10% gives ~35 pts, 15%+ gives 40 pts
        val_score = min(40.0, max(0.0, val_f * 3.0))
        score_breakdown["value_score"] = round(val_score, 2)

        # Factor B: Competition Tier (0.0 - 30.0 pts)
        tier_score_map = {0: 30.0, 1: 18.0, 2: 8.0}
        tier_score = tier_score_map.get(tier, 8.0)
        score_breakdown["competition_tier_score"] = tier_score

        # Factor C: Reference Freshness (0.0 - 15.0 pts)
        freshness_score = 15.0
        ref_ts = getattr(candidate, "reference_market_timestamp", None) or getattr(candidate, "reference_timestamp", None)
        if ref_ts:
            try:
                ref_dt = datetime.fromisoformat(ref_ts.replace("Z", "+00:00"))
                age_sec = (now - ref_dt).total_seconds()
                if age_sec < 300:
                    freshness_score = 15.0
                elif age_sec < 900:
                    freshness_score = 10.0
                elif age_sec < 1800:
                    freshness_score = 5.0
                else:
                    freshness_score = 0.0
                    rejection_reasons.append(ValuebetRejectionReason.STALE_REFERENCE_DATA)
            except Exception:
                freshness_score = 5.0
        score_breakdown["reference_freshness_score"] = freshness_score

        # Factor D: Market Reliability (0.0 - 10.0 pts)
        mkt_score = 10.0 if candidate.market_type in ("1X2", "BTTS", "TOTALS", "DRAW_NO_BET") else 6.0
        score_breakdown["market_reliability_score"] = mkt_score

        # Factor E: Kickoff Proximity (0.0 - 5.0 pts)
        kickoff_score = 3.0
        ko_str = getattr(candidate, "kickoff_time", None) or getattr(candidate, "kickoff", None)
        if ko_str:
            try:
                ko_dt = datetime.fromisoformat(ko_str.replace("Z", "+00:00"))
                hrs_to_ko = (ko_dt - now).total_seconds() / 3600.0
                if 0 <= hrs_to_ko <= 24:
                    kickoff_score = 5.0
                elif 24 < hrs_to_ko <= 72:
                    kickoff_score = 4.0
                elif hrs_to_ko > self.config.max_kickoff_hours_ahead:
                    kickoff_score = 1.0
                    rejection_reasons.append(ValuebetRejectionReason.EXCESSIVE_KICKOFF_HORIZON)
                else:
                    kickoff_score = 2.0
            except Exception:
                kickoff_score = 3.0
        score_breakdown["kickoff_proximity_score"] = kickoff_score

        total_score = min(100.0, max(0.0, val_score + tier_score + freshness_score + mkt_score + kickoff_score))

        if self.config.min_quality_score > 0.0 and total_score < self.config.min_quality_score:
            rejection_reasons.append(ValuebetRejectionReason.LOW_QUALITY_SCORE)

        is_qualified = len(rejection_reasons) == 0

        return ValuebetQualityEvaluation(
            candidate=candidate,
            quality_score=round(total_score, 1),
            is_qualified=is_qualified,
            competition_tier=tier,
            tier_name=tier_name,
            rejection_reasons=tuple(rejection_reasons),
            score_breakdown=score_breakdown,
        )

    def rank_evaluations(
        self,
        evaluations: Sequence[ValuebetQualityEvaluation],
    ) -> List[ValuebetQualityEvaluation]:
        """Deterministically ranks quality evaluations."""
        def _sort_key(ev: ValuebetQualityEvaluation) -> Tuple[Any, ...]:
            cand = ev.candidate
            # Priority: qualified first, higher quality score, higher value_percent, better tier (0 best), candidate id
            return (
                0 if ev.is_qualified else 1,
                -ev.quality_score,
                -cand.value_percent,
                ev.competition_tier,
                cand.candidate_id,
            )

        return sorted(evaluations, key=_sort_key)

    def rank_candidates(
        self,
        candidates: Sequence[ValueBetCandidate],
        current_time: Optional[datetime] = None,
    ) -> List[ValueBetCandidate]:
        """Evaluates and deterministically ranks candidate objects directly."""
        evals = [self.evaluate_quality(c, current_time=current_time) for c in candidates]
        ranked_evals = self.rank_evaluations(evals)
        return [e.candidate for e in ranked_evals]
