"""
Stage 8.6: Opportunity Quality, Filtering & Deterministic Ranking Engine

Provides a deterministic, pure, financial-grade evaluation and ranking layer for betting
opportunities discovered across multi-bookmaker and multi-market pipelines.

Core Principles:
- Single Responsibility: Decides whether a mathematically detected surebet is of sufficient
  quality, reliability, and liquidity to surface to users / dispatch to alert channels.
- Zero Mathematical Modification: Never changes underlying arbitrage sum (S) or margin calculations.
- Strict Decimal Arithmetic: Exact Decimal math for margin, odds, and probability boundaries.
- Deterministic Quality Scoring: Calculates an explainable, multi-factor quality score (0.0 - 100.0)
  incorporating guaranteed margin, competition liquidity tier, cross-bookmaker confidence,
  market type reliability, and kickoff proximity.
- Stable, Pure Ranking: Deterministically orders candidate opportunities such that identical inputs
  always yield identical ordering across cycles.
- Zero External Coupling: Pure decision logic with no database, network, or external side effects.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
import math
from typing import Any, Dict, List, Optional, Protocol, Sequence, Set, Tuple, Union

from domain.models import MatchEvidence
from normalization.market_identity import CanonicalMarketKey, CanonicalMarketType
from normalization.surebet import SurebetLeg, SurebetOpportunity, SurebetStatus

# Tier 0: Global top-flight leagues and major continental/international tournaments
DEFAULT_TOP_TIER_COMPETITIONS: Tuple[str, ...] = (
    "Premier League",
    "LaLiga",
    "Primera Division",
    "Serie A",
    "Bundesliga",
    "Ligue 1",
    "Ekstraklasa",
    "Champions League",
    "Liga Mistrzów",
    "Europa League",
    "Liga Europy",
    "Conference League",
    "Liga Konferencji",
    "World Cup",
    "Mistrzostwa Świata",
    "Euro",
    "Mistrzostwa Europy",
    "Nations League",
    "Liga Narodów",
    "Copa America",
)

# Tier 1: High-value domestic cups, secondary top-tier European leagues, and premier cups
DEFAULT_SECONDARY_TIER_COMPETITIONS: Tuple[str, ...] = (
    "FA Cup",
    "Puchar Anglii",
    "EFL Cup",
    "Copa del Rey",
    "Puchar Króla",
    "Coppa Italia",
    "Puchar Włoch",
    "DFB Pokal",
    "Puchar Niemiec",
    "Coupe de France",
    "Puchar Francji",
    "Puchar Polski",
    "Superpuchar",
    "Super Cup",
    "Championship",
    "Eredivisie",
    "Primeira Liga",
    "Liga Portugal",
    "Super Lig",
    "Pro League",
    "Premiership",
    "Major League Soccer",
    "MLS",
    "Brasileirao",
    "Liga Profesional",
)


# Country prefixes that denote other countries when combined with "Premier League"
NON_ENGLISH_PL_PREFIXES = (
    "uganda", "ghana", "egypt", "malta", "kenya", "nigeria", "tanzania", "rwanda",
    "zambia", "zimbabwe", "india", "hong kong", "singapore", "jamaica", "iceland",
    "faroe", "kuwait", "bahrain", "qatar", "uae", "jordan", "lebanon", "oman",
    "armenia", "azerbaijan", "kazakhstan", "belarus", "ukraine", "russia", "israel",
    "ireland", "northern ireland", "wales", "scotland", "south africa",
)


def calculate_default_competition_tier(comp_name: str) -> int:
    """Calculates competition tier (0=Top, 1=Secondary, 2=Standard) deterministically."""
    if not comp_name:
        return 2
    clean_comp = comp_name.lower().strip()

    def _matches_pattern(target: str, pattern: str) -> bool:
        p = pattern.lower().strip()
        if not p:
            return False
        if p == "premier league":
            for prefix in NON_ENGLISH_PL_PREFIXES:
                if prefix in target:
                    return False
            return (target == "premier league") or ("premier league" in target and ("england" in target or "anglia" in target or "english" in target or "uk" in target or target.startswith("premier league")))
        return p in target or target in p

    for top in DEFAULT_TOP_TIER_COMPETITIONS:
        if _matches_pattern(clean_comp, top):
            return 0
    for sec in DEFAULT_SECONDARY_TIER_COMPETITIONS:
        if _matches_pattern(clean_comp, sec):
            return 1
    return 2


class QualityRejectionReason(str, Enum):
    """Authoritative rejection reasons for opportunity quality vetoes."""
    INVALID_ARBITRAGE_SUM = "INVALID_ARBITRAGE_SUM"          # S >= 1.0 (no mathematical surebet)
    NEGATIVE_OR_ZERO_MARGIN = "NEGATIVE_OR_ZERO_MARGIN"      # Margin <= 0.0
    MARGIN_BELOW_THRESHOLD = "MARGIN_BELOW_THRESHOLD"        # Margin < min_margin threshold
    MARGIN_SANITY_CAP_EXCEEDED = "MARGIN_SANITY_CAP_EXCEEDED"# Margin > max_margin_sanity_cap (palpable error / corrupt data)
    INVALID_ODDS_VALUE = "INVALID_ODDS_VALUE"                # Odds <= 1.0 or > max_odds
    INCOMPLETE_LEGS = "INCOMPLETE_LEGS"                      # Missing required outcome legs
    DUPLICATE_SELECTION = "DUPLICATE_SELECTION"              # Duplicate selection outcomes
    SINGLE_BOOKMAKER_DISALLOWED = "SINGLE_BOOKMAKER_DISALLOWED"# Multi-bookmaker required but single bookmaker provided
    UNSUPPORTED_MARKET_TYPE = "UNSUPPORTED_MARKET_TYPE"      # Market type not supported or complete partition
    PAST_KICKOFF = "PAST_KICKOFF"                            # Match kickoff has already passed
    DISTANT_KICKOFF = "DISTANT_KICKOFF"                      # Match kickoff is too far in future
    CANONICAL_MISMATCH = "CANONICAL_MISMATCH"                # Selections span different events or lines


@dataclass(frozen=True)
class OpportunityQualityConfig:
    """Configuration container for opportunity quality filters and scoring parameters.

    All numerical thresholds utilize exact Decimal arithmetic for financial precision.
    """
    # Minimum required guaranteed arbitrage margin (e.g. 0.0010 = 0.10%)
    min_margin: Decimal = Decimal("0.0010")

    # Maximum credible margin sanity cap (e.g. 0.5000 = 50.00% — flags corrupted/palpable error odds)
    max_margin_sanity_cap: Decimal = Decimal("0.5000")

    # Minimum valid decimal odds per leg (must be strictly > 1.0, default 1.01)
    min_odds: Decimal = Decimal("1.0100")

    # Maximum valid decimal odds per leg (default 100.0)
    max_odds: Decimal = Decimal("100.0000")

    # Whether to require at least two distinct bookmakers (mixed bookmakers)
    require_mixed_bookmakers: bool = False

    # Allowed canonical market types for high-quality alerts
    allowed_market_types: Tuple[str, ...] = (
        CanonicalMarketType.ONE_X_TWO.value,
        CanonicalMarketType.TOTALS.value,
        CanonicalMarketType.BTTS.value,
        CanonicalMarketType.DRAW_NO_BET.value,
        CanonicalMarketType.HALF_TIME_RESULT.value,
        CanonicalMarketType.ODD_EVEN.value,
    )

    # Maximum allowed kickoff hours ahead (default 336h = 14 days; None for no limit)
    max_kickoff_hours_ahead: Optional[float] = 336.0

    # Whether to reject matches whose kickoff has already passed
    reject_past_kickoff: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.min_margin, Decimal) or self.min_margin < Decimal("0.0"):
            raise ValueError(f"min_margin must be a non-negative Decimal, got: {self.min_margin!r}")
        if not isinstance(self.max_margin_sanity_cap, Decimal) or self.max_margin_sanity_cap <= self.min_margin:
            raise ValueError(f"max_margin_sanity_cap must be > min_margin, got: {self.max_margin_sanity_cap!r}")
        if not isinstance(self.min_odds, Decimal) or self.min_odds <= Decimal("1.0"):
            raise ValueError(f"min_odds must be a Decimal strictly > 1.0, got: {self.min_odds!r}")
        if not isinstance(self.max_odds, Decimal) or self.max_odds <= self.min_odds:
            raise ValueError(f"max_odds must be > min_odds, got: {self.max_odds!r}")


@dataclass(frozen=True)
class OpportunityQualityEvaluation:
    """Immutable, explainable result of an opportunity quality evaluation."""
    is_qualified: bool
    quality_score: float  # 0.0 to 100.0
    competition_tier: int # 0: Top Tier, 1: Secondary, 2: Standard
    tier_name: str        # e.g. "Tier 0 (Top Flight)", "Tier 1 (Secondary)", "Tier 2 (Standard)"
    rejection_reasons: Tuple[str, ...]
    signals: Dict[str, Any] = field(default_factory=dict)


class OpportunityQualityPolicy(Protocol):
    """Protocol for opportunity quality evaluation components."""

    def evaluate_quality(
        self,
        opportunity: SurebetOpportunity,
        current_time: Optional[datetime] = None,
    ) -> OpportunityQualityEvaluation:
        """Evaluates a SurebetOpportunity against quality filters and scoring rules."""
        ...


class DefaultOpportunityQualityPolicy:
    """Authoritative deterministic implementation of the Stage 8.6 Opportunity Quality Policy.

    Features:
    - Pure, side-effect-free evaluation.
    - Exact Decimal validation for boundary arithmetic ($S < 1.0$).
    - Robust competition tier extraction using Stage 8.5 taxonomy.
    - Multi-factor scoring (Margin, Tier, Bookmakers, Proximity, Market Liquidity).
    """

    def __init__(
        self,
        config: Optional[OpportunityQualityConfig] = None,
        event_selection_policy: Optional[Any] = None,
    ) -> None:
        self.config = config or OpportunityQualityConfig()
        self.event_selection_policy = event_selection_policy

    def calculate_competition_tier(self, comp_name: str) -> int:
        """Calculates competition tier using configured event_selection_policy or default taxonomy."""
        if self.event_selection_policy is not None and hasattr(self.event_selection_policy, "calculate_competition_tier"):
            return self.event_selection_policy.calculate_competition_tier(comp_name)
        return calculate_default_competition_tier(comp_name)

    def _extract_competition_and_kickoff(
        self,
        opportunity: SurebetOpportunity,
    ) -> Tuple[str, Optional[datetime]]:
        """Safely extracts competition name and kickoff datetime from opportunity evidence."""
        evidence: Optional[MatchEvidence] = opportunity.event_evidence
        comp_name = ""
        kickoff_dt: Optional[datetime] = None

        if evidence:
            ev_dict = getattr(evidence, "evidence", {}) or {}
            comp = ev_dict.get("competition_name") or getattr(evidence, "competition_name", None)
            if comp:
                comp_name = str(comp)

            start_t = ev_dict.get("start_time") or getattr(evidence, "start_time", None)
            if start_t is not None:
                if isinstance(start_t, datetime):
                    kickoff_dt = start_t if start_t.tzinfo else start_t.replace(tzinfo=timezone.utc)
                elif isinstance(start_t, str):
                    try:
                        from normalization.identity import parse_kickoff_to_utc
                        kickoff_dt = parse_kickoff_to_utc(start_t)
                    except Exception:
                        kickoff_dt = None

        return comp_name, kickoff_dt

    def evaluate_quality(
        self,
        opportunity: SurebetOpportunity,
        current_time: Optional[datetime] = None,
    ) -> OpportunityQualityEvaluation:
        """Evaluates opportunity quality, validity, safety vetoes, and calculates quality score."""
        now = current_time or datetime.now(timezone.utc)
        reasons: List[str] = []
        signals: Dict[str, Any] = {}

        # ──────────────────────────────────────────────────────────────────
        # 1. Strict Mathematical Verification (Decimal Invariants)
        # ──────────────────────────────────────────────────────────────────
        s_val = opportunity.implied_probability_sum
        margin_val = opportunity.arbitrage_margin

        signals["implied_probability_sum"] = str(s_val)
        signals["arbitrage_margin"] = str(margin_val)
        signals["margin_pct"] = float(margin_val * Decimal("100"))

        # Invariant 1: S must be strictly < 1.0
        if s_val >= Decimal("1.0"):
            reasons.append(
                f"{QualityRejectionReason.INVALID_ARBITRAGE_SUM.value}: "
                f"Implied probability sum S = {s_val} is >= 1.0 (no mathematical arbitrage)"
            )

        # Invariant 2: Margin must be strictly > 0.0
        if margin_val <= Decimal("0.0"):
            reasons.append(
                f"{QualityRejectionReason.NEGATIVE_OR_ZERO_MARGIN.value}: "
                f"Arbitrage margin = {margin_val} is non-positive"
            )
        elif margin_val < self.config.min_margin:
            reasons.append(
                f"{QualityRejectionReason.MARGIN_BELOW_THRESHOLD.value}: "
                f"Margin {margin_val * Decimal('100'):.3f}% < required minimum {self.config.min_margin * Decimal('100'):.3f}%"
            )

        # Invariant 3: Sanity Cap Veto (Palpable Error / Corrupted Odds Protection)
        if margin_val > self.config.max_margin_sanity_cap:
            reasons.append(
                f"{QualityRejectionReason.MARGIN_SANITY_CAP_EXCEEDED.value}: "
                f"Margin {margin_val * Decimal('100'):.2f}% exceeds sanity cap {self.config.max_margin_sanity_cap * Decimal('100'):.2f}% (probable bad data)"
            )

        # ──────────────────────────────────────────────────────────────────
        # 2. Leg & Odds Validation
        # ──────────────────────────────────────────────────────────────────
        if not opportunity.legs or len(opportunity.legs) < 2:
            reasons.append(f"{QualityRejectionReason.INCOMPLETE_LEGS.value}: Opportunity contains fewer than 2 outcome legs")

        seen_sel_types: Set[str] = set()
        for leg in opportunity.legs:
            # Check individual odds bounds
            if leg.odds <= Decimal("1.0"):
                reasons.append(
                    f"{QualityRejectionReason.INVALID_ODDS_VALUE.value}: "
                    f"Leg '{leg.selection_type}' odds {leg.odds} is <= 1.0"
                )
            elif leg.odds < self.config.min_odds:
                reasons.append(
                    f"{QualityRejectionReason.INVALID_ODDS_VALUE.value}: "
                    f"Leg '{leg.selection_type}' odds {leg.odds} < minimum valid odds {self.config.min_odds}"
                )
            elif leg.odds > self.config.max_odds:
                reasons.append(
                    f"{QualityRejectionReason.INVALID_ODDS_VALUE.value}: "
                    f"Leg '{leg.selection_type}' odds {leg.odds} > maximum allowed odds {self.config.max_odds}"
                )

            # Check duplicate selection type
            if leg.selection_type in seen_sel_types:
                reasons.append(
                    f"{QualityRejectionReason.DUPLICATE_SELECTION.value}: "
                    f"Duplicate selection outcome '{leg.selection_type}' detected in legs"
                )
            seen_sel_types.add(leg.selection_type)

        # ──────────────────────────────────────────────────────────────────
        # 3. Bookmaker Configuration Check
        # ──────────────────────────────────────────────────────────────────
        bookmakers = tuple(sorted(set(leg.provider.lower() for leg in opportunity.legs)))
        signals["bookmakers"] = list(bookmakers)
        signals["bookmaker_count"] = len(bookmakers)
        signals["is_mixed_bookmakers"] = len(bookmakers) > 1

        if self.config.require_mixed_bookmakers and len(bookmakers) < 2:
            reasons.append(
                f"{QualityRejectionReason.SINGLE_BOOKMAKER_DISALLOWED.value}: "
                f"Requires at least 2 distinct bookmakers, but only {bookmakers} provided"
            )

        # ──────────────────────────────────────────────────────────────────
        # 4. Market Type Whitelist Check
        # ──────────────────────────────────────────────────────────────────
        mkt_type = opportunity.canonical_market_key.market_type
        signals["market_type"] = mkt_type
        signals["market_key"] = opportunity.canonical_market_key.to_key_string()

        if self.config.allowed_market_types and mkt_type not in self.config.allowed_market_types:
            reasons.append(
                f"{QualityRejectionReason.UNSUPPORTED_MARKET_TYPE.value}: "
                f"Market type '{mkt_type}' is not in allowed quality markets whitelist"
            )

        # ──────────────────────────────────────────────────────────────────
        # 5. Competition & Kickoff Proximity
        # ──────────────────────────────────────────────────────────────────
        comp_name, kickoff_dt = self._extract_competition_and_kickoff(opportunity)
        if self.event_selection_policy is not None and hasattr(self.event_selection_policy, "calculate_competition_tier"):
            tier = self.event_selection_policy.calculate_competition_tier(comp_name)
        else:
            tier = calculate_default_competition_tier(comp_name)

        tier_names = {
            0: "Tier 0 (Top Flight)",
            1: "Tier 1 (Secondary)",
            2: "Tier 2 (Standard)",
        }
        tier_name = tier_names.get(tier, "Tier 2 (Standard)")

        signals["competition_name"] = comp_name
        signals["competition_tier"] = tier
        signals["tier_name"] = tier_name
        signals["kickoff_time"] = kickoff_dt.isoformat() if kickoff_dt else None

        hours_to_kickoff: Optional[float] = None
        if kickoff_dt is not None:
            hours_to_kickoff = (kickoff_dt - now).total_seconds() / 3600.0
            signals["hours_to_kickoff"] = round(hours_to_kickoff, 2)

            if self.config.reject_past_kickoff and hours_to_kickoff < -0.25:  # 15m grace window
                reasons.append(
                    f"{QualityRejectionReason.PAST_KICKOFF.value}: "
                    f"Match kickoff was at {kickoff_dt.isoformat()} ({abs(hours_to_kickoff):.1f}h in the past)"
                )

            if self.config.max_kickoff_hours_ahead is not None and hours_to_kickoff > self.config.max_kickoff_hours_ahead:
                reasons.append(
                    f"{QualityRejectionReason.DISTANT_KICKOFF.value}: "
                    f"Match kickoff ({hours_to_kickoff:.1f}h ahead) exceeds window of {self.config.max_kickoff_hours_ahead}h"
                )

        is_qualified = (len(reasons) == 0)

        # ──────────────────────────────────────────────────────────────────
        # 6. Multi-Factor Deterministic Quality Score (0.0 to 100.0)
        # ──────────────────────────────────────────────────────────────────
        # Factor A: Margin Component (Up to 40 points)
        # 0.5% margin -> 10 pts, 2.0% -> 25 pts, >= 5.0% -> 40 pts
        margin_pct_float = max(0.0, float(margin_val * Decimal("100")))
        margin_score = min(40.0, margin_pct_float * 8.0)

        # Factor B: Competition Tier Component (Up to 30 points)
        tier_score_map = {0: 30.0, 1: 18.0, 2: 5.0}
        tier_score = tier_score_map.get(tier, 5.0)

        # Factor C: Cross-Bookmaker Confidence (Up to 15 points)
        book_score = 15.0 if len(bookmakers) >= 2 else 5.0

        # Factor D: Kickoff Proximity (Up to 10 points)
        proximity_score = 5.0
        if hours_to_kickoff is not None:
            if 0.0 <= hours_to_kickoff <= 24.0:
                proximity_score = 10.0
            elif 24.0 < hours_to_kickoff <= 72.0:
                proximity_score = 7.5
            elif hours_to_kickoff > 72.0:
                proximity_score = 4.0
            else:
                proximity_score = 0.0

        # Factor E: Market Liquidity Weight (Up to 5 points)
        mkt_weights = {
            CanonicalMarketType.ONE_X_TWO.value: 5.0,
            CanonicalMarketType.TOTALS.value: 5.0,
            CanonicalMarketType.BTTS.value: 4.5,
            CanonicalMarketType.DRAW_NO_BET.value: 4.0,
            CanonicalMarketType.HALF_TIME_RESULT.value: 3.5,
            CanonicalMarketType.ODD_EVEN.value: 2.0,
        }
        mkt_score = mkt_weights.get(mkt_type, 3.0)

        raw_score = margin_score + tier_score + book_score + proximity_score + mkt_score
        quality_score = max(0.0, min(100.0, round(raw_score, 1))) if is_qualified else 0.0

        signals["scoring_breakdown"] = {
            "margin_score": round(margin_score, 2),
            "tier_score": round(tier_score, 2),
            "bookmaker_score": round(book_score, 2),
            "proximity_score": round(proximity_score, 2),
            "market_score": round(mkt_score, 2),
            "total_score": quality_score,
        }

        return OpportunityQualityEvaluation(
            is_qualified=is_qualified,
            quality_score=quality_score,
            competition_tier=tier,
            tier_name=tier_name,
            rejection_reasons=tuple(reasons),
            signals=signals,
        )


class OpportunityRankingEngine:
    """Deterministic, stable ranking engine for cross-bookmaker surebet opportunities."""

    def __init__(
        self,
        quality_policy: Optional[OpportunityQualityPolicy] = None,
    ) -> None:
        self.quality_policy = quality_policy or DefaultOpportunityQualityPolicy()

    def rank_opportunities(
        self,
        opportunities: Sequence[SurebetOpportunity],
        current_time: Optional[datetime] = None,
        filter_unqualified: bool = False,
    ) -> List[Tuple[SurebetOpportunity, OpportunityQualityEvaluation]]:
        """Ranks opportunities deterministically using quality evaluations and stable sorting.

        Args:
            opportunities: Input sequence of candidate SurebetOpportunities.
            current_time: Reference evaluation timestamp.
            filter_unqualified: If True, excludes opportunities that fail quality requirements.

        Returns:
            List of (opportunity, evaluation) tuples in authoritative priority order.
        """
        now = current_time or datetime.now(timezone.utc)
        evaluated_pairs: List[Tuple[SurebetOpportunity, OpportunityQualityEvaluation]] = []

        for opp in opportunities:
            eval_res = self.quality_policy.evaluate_quality(opp, current_time=now)
            if filter_unqualified and not eval_res.is_qualified:
                continue
            evaluated_pairs.append((opp, eval_res))

        # Deterministic Sort Key:
        # 1. is_qualified (True first -> 0 vs 1)
        # 2. quality_score (Descending -> negative float)
        # 3. arbitrage_margin (Descending -> negative Decimal)
        # 4. competition_tier (Ascending: Tier 0 > Tier 1 > Tier 2)
        # 5. kickoff proximity (Upcoming matches first; None sorted last)
        # 6. Stable tie-breaker: opportunity_id (Alphabetical ascending)
        def _sort_key(item: Tuple[SurebetOpportunity, OpportunityQualityEvaluation]) -> Tuple[Any, ...]:
            opp, ev = item
            k_dt = ev.signals.get("kickoff_time")
            k_sort = k_dt if k_dt is not None else "9999-12-31T23:59:59"
            return (
                0 if ev.is_qualified else 1,
                -ev.quality_score,
                -opp.arbitrage_margin,
                ev.competition_tier,
                k_sort,
                opp.opportunity_id,
            )

        evaluated_pairs.sort(key=_sort_key)
        return evaluated_pairs
