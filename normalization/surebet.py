"""
Stage 5.5: Cross-Bookmaker Surebet Detection Engine

Implements the provider-independent surebet / arbitrage detection layer on top of
the completed Stage 5.4 Odds Comparison layer:

    Stage 5.4 OddsComparisonResult (OddsComparison)
                        ↓
             Stage 5.5 SurebetDetectorEngine
                        ↓
               SurebetDetectionResult

Core Question:
    "Do the available bookmaker odds provide complete coverage of every mutually
     exclusive outcome of the SAME canonical market, with total implied probability
     strictly below 1?"

Architecture Invariants:
- Gated Input: Consumes validated OddsComparison records or validated comparisons.
- Strict Market Completeness: Requires all mutually exclusive canonical selections to be present.
- Best-Odds Optimization: Maximizes outcome odds independently to strictly minimize arbitrage sum S.
- Provider Agnosticism: Supports both multi-bookmaker and single-bookmaker surebets.
- Absolute Market Isolation: Never combines selections across events, markets, lines, periods, or scopes.
- Numerical Determinism: Exact Decimal arithmetic throughout; zero early rounding.
- Strict Boundary Condition: S < 1.0 -> SUREBET; S == 1.0 -> NO_SUREBET (zero margin); S > 1.0 -> NO_SUREBET.
- Deterministic Identity: Stable opportunity IDs derived from canonical IDs, market keys, and legs.
- Full Lineage Preservation: Retains provider IDs, canonical keys, native odds, and match evidence.
- Zero External Coupling: No stake allocation, bankroll policy, EV calculation, or betting APIs.
"""

import time
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple, Union

from domain.models import (
    MatchEvidence,
    Odds,
)
from core.tax_engine import (
    TaxEngine,
    get_tax_engine,
)
from normalization.market_identity import (
    CanonicalMarketKey,
    CanonicalMarketType,
    MarketPeriod,
    MarketScope,
    MarketCompletenessStatus,
)
from normalization.selection_identity import (
    CanonicalSelectionKey,
    CanonicalSelectionType,
)
from normalization.odds_comparison import (
    OddsComparison,
    OddsComparisonEngine,
    OddsComparisonResult,
    OddsComparisonStatus,
)
from normalization.validation_pipeline import (
    ComparableSelectionPair,
    CrossBookmakerValidationResult,
)


class SurebetStatus(str, Enum):
    """Authoritative classification for a market surebet detection evaluation."""
    SUREBET = "SUREBET"                      # Complete market with S < 1.0 and margin > 0
    NO_SUREBET = "NO_SUREBET"                # Complete market with S >= 1.0 (margin <= 0)
    INCOMPLETE_MARKET = "INCOMPLETE_MARKET"  # Missing one or more required canonical selections
    UNSUPPORTED_MARKET = "UNSUPPORTED_MARKET"# Market type or scope not supported for arbitrage
    INVALID_MARKET = "INVALID_MARKET"        # Malformed market parameters (e.g. missing line for totals)


# Explicit required canonical selection sets for supported canonical market types.
# Only markets with mathematically proven mutually exclusive and collectively exhaustive
# partitions are admitted here.
SUPPORTED_MARKET_REQUIRED_SELECTIONS: Dict[str, Tuple[str, ...]] = {
    # 1X2 Match Result: Home, Draw, Away (3-way partition)
    CanonicalMarketType.ONE_X_TWO.value: (
        CanonicalSelectionType.HOME.value,
        CanonicalSelectionType.DRAW.value,
        CanonicalSelectionType.AWAY.value,
    ),
    # Both Teams To Score (BTTS): Yes, No (2-way partition)
    CanonicalMarketType.BTTS.value: (
        CanonicalSelectionType.YES.value,
        CanonicalSelectionType.NO.value,
    ),
    # Totals (Over / Under): Over, Under (2-way partition bound to parent line)
    CanonicalMarketType.TOTALS.value: (
        CanonicalSelectionType.OVER.value,
        CanonicalSelectionType.UNDER.value,
    ),
    # Draw No Bet (DNB): Home, Away (2-way partition for decisive match)
    CanonicalMarketType.DRAW_NO_BET.value: (
        CanonicalSelectionType.HOME.value,
        CanonicalSelectionType.AWAY.value,
    ),
    # Half Time Result: Home, Draw, Away (3-way partition for FIRST_HALF period)
    CanonicalMarketType.HALF_TIME_RESULT.value: (
        CanonicalSelectionType.HOME.value,
        CanonicalSelectionType.DRAW.value,
        CanonicalSelectionType.AWAY.value,
    ),
    # Match Goals Odd/Even: Odd, Even (2-way partition)
    CanonicalMarketType.ODD_EVEN.value: (
        CanonicalSelectionType.ODD.value,
        CanonicalSelectionType.EVEN.value,
    ),
    # Handicap (2-way partition bound to line: Home, Away)
    CanonicalMarketType.HANDICAP.value: (
        CanonicalSelectionType.HOME.value,
        CanonicalSelectionType.AWAY.value,
    ),
    # Asian Handicap (2-way partition bound to line: Home, Away)
    CanonicalMarketType.ASIAN_HANDICAP.value: (
        CanonicalSelectionType.HOME.value,
        CanonicalSelectionType.AWAY.value,
    ),
    # Double Chance: 1X (Home/Draw), 12 (Home/Away), X2 (Draw/Away) (3-way partition)
    CanonicalMarketType.DOUBLE_CHANCE.value: (
        CanonicalSelectionType.HOME_DRAW.value,
        CanonicalSelectionType.HOME_AWAY.value,
        CanonicalSelectionType.DRAW_AWAY.value,
    ),
    # Player Props (Over / Under partitions)
    CanonicalMarketType.PLAYER_SHOTS.value: (
        CanonicalSelectionType.OVER.value,
        CanonicalSelectionType.UNDER.value,
    ),
    CanonicalMarketType.PLAYER_SHOTS_ON_TARGET.value: (
        CanonicalSelectionType.OVER.value,
        CanonicalSelectionType.UNDER.value,
    ),
    CanonicalMarketType.PLAYER_FOULS.value: (
        CanonicalSelectionType.OVER.value,
        CanonicalSelectionType.UNDER.value,
    ),
    CanonicalMarketType.PLAYER_PASSES.value: (
        CanonicalSelectionType.OVER.value,
        CanonicalSelectionType.UNDER.value,
    ),
    CanonicalMarketType.PLAYER_TACKLES.value: (
        CanonicalSelectionType.OVER.value,
        CanonicalSelectionType.UNDER.value,
    ),
    CanonicalMarketType.PLAYER_GOALS.value: (
        CanonicalSelectionType.YES.value,
        CanonicalSelectionType.NO.value,
    ),
    CanonicalMarketType.PLAYER_CARDS.value: (
        CanonicalSelectionType.YES.value,
        CanonicalSelectionType.NO.value,
    ),
    CanonicalMarketType.PLAYER_ASSISTS.value: (
        CanonicalSelectionType.YES.value,
        CanonicalSelectionType.NO.value,
    ),
}


@dataclass(frozen=True)
class SurebetLeg:
    """Immutable, auditable container representing a single outcome leg of a surebet opportunity.

    Preserves full lineage from canonical entities down to native provider selections.
    """
    canonical_selection_key: CanonicalSelectionKey
    selection_type: str
    provider: str
    odds: Decimal
    source_selection_id: str
    source_event_id: Optional[str] = None
    source_market_id: Optional[str] = None
    implied_probability: Optional[Decimal] = None  # Exact Decimal(1) / effective_odds
    raw_odds: Optional[Odds] = None
    selection_evidence: Dict[str, Any] = field(default_factory=dict)
    effective_odds: Optional[Decimal] = None      # Tax-adjusted effective decimal price: odds * (1 - tax_rate)
    tax_rate: Decimal = Decimal("0")              # Configured bookmaker tax rate
    is_tax_applied: bool = False                  # Whether tax was applied to this leg


@dataclass(frozen=True)
class SurebetOpportunity:
    """Immutable, auditable record of a mathematically validated surebet (arbitrage) opportunity."""
    opportunity_id: str
    canonical_event_id: str
    canonical_market_key: CanonicalMarketKey
    legs: Tuple[SurebetLeg, ...]
    implied_probability_sum: Decimal  # S = sum(1 / effective_odds_i)
    arbitrage_margin: Decimal         # margin = (1 / S) - 1
    status: SurebetStatus = SurebetStatus.SUREBET
    is_mixed_bookmakers: bool = False
    bookmakers: Tuple[str, ...] = ()
    event_evidence: Optional[MatchEvidence] = None
    market_evidence: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MarketSurebetEvaluation:
    """Detailed audit record of surebet evaluation for a single canonical market."""
    canonical_event_id: str
    canonical_market_key: CanonicalMarketKey
    status: SurebetStatus
    completeness_status: MarketCompletenessStatus
    required_selection_types: Tuple[str, ...]
    available_selection_types: Tuple[str, ...]
    missing_selection_types: Tuple[str, ...]
    best_legs: Tuple[SurebetLeg, ...]
    implied_probability_sum: Optional[Decimal] = None
    arbitrage_margin: Optional[Decimal] = None
    rejection_reason: Optional[str] = None
    opportunity: Optional[SurebetOpportunity] = None
    exclusion_reason_code: Optional[str] = None


@dataclass
class SurebetDetectionMetrics:
    """Telemetry and summary metrics for a surebet detection run."""
    input_market_count: int = 0
    supported_market_count: int = 0
    unsupported_market_count: int = 0
    complete_market_count: int = 0
    incomplete_market_count: int = 0
    invalid_market_count: int = 0

    surebet_count: int = 0
    no_surebet_count: int = 0

    markets_with_mixed_bookmakers: int = 0
    markets_with_single_bookmaker: int = 0

    average_arbitrage_margin: Optional[Decimal] = None
    max_arbitrage_margin: Optional[Decimal] = None
    min_arbitrage_margin: Optional[Decimal] = None

    average_number_of_bookmakers_used: Optional[Decimal] = None
    max_number_of_bookmakers_used: int = 0

    detection_duration_ms: float = 0.0


@dataclass
class SurebetDetectionResult:
    """Aggregate result container for a cross-bookmaker surebet detection execution."""
    opportunities: List[SurebetOpportunity] = field(default_factory=list)
    evaluations: List[MarketSurebetEvaluation] = field(default_factory=list)
    surebet_evaluations: List[MarketSurebetEvaluation] = field(default_factory=list)
    no_surebet_evaluations: List[MarketSurebetEvaluation] = field(default_factory=list)
    incomplete_evaluations: List[MarketSurebetEvaluation] = field(default_factory=list)
    unsupported_evaluations: List[MarketSurebetEvaluation] = field(default_factory=list)
    invalid_evaluations: List[MarketSurebetEvaluation] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    metrics: SurebetDetectionMetrics = field(default_factory=SurebetDetectionMetrics)

    def generate_audit_report(self, detailed: bool = False) -> str:
        """Generates a human-readable mathematical audit report of the detection run."""
        lines = [
            "=" * 70,
            "STAGE 5.5: CROSS-BOOKMAKER SUREBET DETECTION AUDIT REPORT",
            "=" * 70,
            f"Markets Evaluated: TOTAL={self.metrics.input_market_count}, "
            f"COMPLETE={self.metrics.complete_market_count}, "
            f"INCOMPLETE={self.metrics.incomplete_market_count}, "
            f"INVALID={self.metrics.invalid_market_count}, "
            f"UNSUPPORTED={self.metrics.unsupported_market_count}",
            f"Arbitrage Results: SUREBETS_FOUND={self.metrics.surebet_count}, "
            f"NO_SUREBET={self.metrics.no_surebet_count}"
        ]
        if self.metrics.surebet_count > 0:
            lines.append(
                f"Bookmaker Distribution: Mixed Bookmakers={self.metrics.markets_with_mixed_bookmakers}, "
                f"Single Bookmaker={self.metrics.markets_with_single_bookmaker}, "
                f"Avg Books/Surebet={self.metrics.average_number_of_bookmakers_used}, "
                f"Max Books={self.metrics.max_number_of_bookmakers_used}"
            )
            lines.append(
                f"Arbitrage Margins: Avg Margin={self.metrics.average_arbitrage_margin}, "
                f"Min Margin={self.metrics.min_arbitrage_margin}, "
                f"Max Margin={self.metrics.max_arbitrage_margin}"
            )
        lines.append(f"Execution Duration: {self.metrics.detection_duration_ms:.2f} ms")

        if detailed and self.opportunities:
            lines.append("-" * 70)
            lines.append("CONFIRMED SUREBET OPPORTUNITIES:")
            for idx, opp in enumerate(self.opportunities, 1):
                lines.append(
                    f"[{idx:03d}] ID: {opp.opportunity_id} | Event: {opp.canonical_event_id} | "
                    f"Mkt: {opp.canonical_market_key.to_key_string()}"
                )
                lines.append(
                    f"      Arbitrage Sum (S): {opp.implied_probability_sum} | "
                    f"Margin: {opp.arbitrage_margin} ({(opp.arbitrage_margin * Decimal(100)):.2f}%) | "
                    f"Mixed Books: {opp.is_mixed_bookmakers}"
                )
                lines.append("      Legs:")
                for leg in opp.legs:
                    eff_str = f" (Eff: {leg.effective_odds})" if leg.effective_odds is not None else ""
                    lines.append(
                        f"        - {leg.selection_type:<6} @ {leg.odds:<6}{eff_str} [{leg.provider}] "
                        f"(Implied: {leg.implied_probability}) (SelID: {leg.source_selection_id})"
                    )
        lines.append("=" * 70)
        return "\n".join(lines)


@dataclass(frozen=True)
class _CandidatePrice:
    """Internal helper to represent an individual provider price candidate for a selection."""
    provider: str
    odds: Decimal
    selection_id: str
    event_id: str
    market_id: str
    raw_odds: Optional[Odds] = None
    selection_evidence: Dict[str, Any] = field(default_factory=dict)
    effective_odds: Optional[Decimal] = None
    tax_rate: Decimal = Decimal("0")
    is_tax_applied: bool = False


# Explicitly authorized execution bookmakers and excluded reference bookmakers
EXECUTION_BOOKMAKERS: Tuple[str, ...] = ("superbet", "betclic")
ALLOWED_EXECUTION_BOOKMAKERS: Set[str] = {"superbet", "betclic"}
REFERENCE_BOOKMAKERS: Set[str] = {"bet365", "unibet"}


class SurebetDetectorEngine:
    """Deterministic, provider-independent cross-bookmaker surebet (arbitrage) detection engine.

    Gated strictly to Stage 5.4 OddsComparison / OddsComparisonResult inputs.
    Restricted strictly to authorized execution bookmakers (Superbet, Betclic),
    excluding reference-only bookmakers (Bet365, Unibet).
    """

    def __init__(
        self,
        allowed_bookmakers: Optional[Set[str]] = None,
        disallowed_bookmakers: Optional[Set[str]] = None,
        tax_engine: Optional[TaxEngine] = None,
    ):
        self.allowed_bookmakers: Optional[Set[str]] = (
            {b.lower() for b in allowed_bookmakers} if allowed_bookmakers is not None else None
        )
        self.disallowed_bookmakers: Set[str] = {
            b.lower() for b in (disallowed_bookmakers if disallowed_bookmakers is not None else REFERENCE_BOOKMAKERS)
        }
        self.tax_engine: TaxEngine = tax_engine if tax_engine is not None else get_tax_engine()

    def is_provider_allowed(self, provider: str) -> bool:
        """Determines if a provider is eligible for surebet detection."""
        if not provider:
            return False
        p = provider.strip().lower()
        if p in self.disallowed_bookmakers:
            return False
        if self.allowed_bookmakers is not None and p not in self.allowed_bookmakers:
            return False
        return True

    def generate_deterministic_opportunity_id(
        self,
        canonical_event_id: str,
        canonical_market_key: CanonicalMarketKey,
        legs: Sequence[SurebetLeg],
    ) -> str:
        """Generates a stable, deterministic identifier for a surebet opportunity without timestamps or UUIDs."""
        mkt_str = canonical_market_key.to_key_string()
        legs_str = "_".join(
            f"{l.selection_type}:{l.provider}:{l.source_selection_id}:{l.odds:f}"
            for l in sorted(legs, key=lambda x: x.selection_type)
        )
        return f"sb:{canonical_event_id}:{mkt_str}:{legs_str}"

    def evaluate_market(
        self,
        canonical_event_id: str,
        canonical_market_key: CanonicalMarketKey,
        comparisons: Sequence[OddsComparison],
    ) -> MarketSurebetEvaluation:
        """Evaluates a single canonical market across all its available OddsComparison records.

        Steps:
        1. Market Support & Completeness Check
        2. Scope and Dimension Validation
        3. Best-Odds Selection per Outcome
        4. Strict Arbitrage Sum S Calculation
        5. Surebet Decision & Deterministic Opportunity Construction
        """
        # 0. Check event identity validity
        if not canonical_event_id or canonical_event_id.strip().lower() in ("", "unknown", "none", "—", "-"):
            return MarketSurebetEvaluation(
                canonical_event_id=canonical_event_id,
                canonical_market_key=canonical_market_key,
                status=SurebetStatus.INVALID_MARKET,
                completeness_status=MarketCompletenessStatus.INVALID,
                required_selection_types=(),
                available_selection_types=(),
                missing_selection_types=(),
                best_legs=(),
                rejection_reason="Canonical event ID is missing or invalid",
                exclusion_reason_code="INCOMPLETE_EVENT_IDENTITY",
            )

        mkt_type = canonical_market_key.market_type.upper()

        # 1. Check if canonical market type is supported for arbitrage detection
        if mkt_type not in SUPPORTED_MARKET_REQUIRED_SELECTIONS:
            return MarketSurebetEvaluation(
                canonical_event_id=canonical_event_id,
                canonical_market_key=canonical_market_key,
                status=SurebetStatus.UNSUPPORTED_MARKET,
                completeness_status=MarketCompletenessStatus.UNSUPPORTED,
                required_selection_types=(),
                available_selection_types=(),
                missing_selection_types=(),
                best_legs=(),
                rejection_reason=f"Market type '{mkt_type}' is not supported for arbitrage detection",
                exclusion_reason_code="UNSUPPORTED_MARKET",
            )

        # 2. Check market scope (MATCH and TEAM are standard; PLAYER is supported ONLY for specific PLAYER_* market types)
        if canonical_market_key.scope not in (MarketScope.MATCH.value, MarketScope.TEAM.value, MarketScope.PLAYER.value):
            return MarketSurebetEvaluation(
                canonical_event_id=canonical_event_id,
                canonical_market_key=canonical_market_key,
                status=SurebetStatus.UNSUPPORTED_MARKET,
                completeness_status=MarketCompletenessStatus.UNSUPPORTED,
                required_selection_types=(),
                available_selection_types=(),
                missing_selection_types=(),
                best_legs=(),
                rejection_reason=f"Market scope '{canonical_market_key.scope}' is not supported for arbitrage detection",
                exclusion_reason_code="UNSUPPORTED_MARKET",
            )
        if canonical_market_key.scope == MarketScope.PLAYER.value and not mkt_type.startswith("PLAYER_"):
            return MarketSurebetEvaluation(
                canonical_event_id=canonical_event_id,
                canonical_market_key=canonical_market_key,
                status=SurebetStatus.UNSUPPORTED_MARKET,
                completeness_status=MarketCompletenessStatus.UNSUPPORTED,
                required_selection_types=(),
                available_selection_types=(),
                missing_selection_types=(),
                best_legs=(),
                rejection_reason=f"Market scope 'PLAYER' is not supported for standard market type '{mkt_type}'",
                exclusion_reason_code="UNSUPPORTED_MARKET",
            )
        if canonical_market_key.scope == MarketScope.TEAM.value and not canonical_market_key.participant_role:
            return MarketSurebetEvaluation(
                canonical_event_id=canonical_event_id,
                canonical_market_key=canonical_market_key,
                status=SurebetStatus.INVALID_MARKET,
                completeness_status=MarketCompletenessStatus.INVALID,
                required_selection_types=(),
                available_selection_types=(),
                missing_selection_types=(),
                best_legs=(),
                rejection_reason="TEAM market key requires a valid participant_role (HOME or AWAY)",
                exclusion_reason_code="INVALID_MARKET_IDENTITY",
            )
        if canonical_market_key.scope == MarketScope.PLAYER.value and not canonical_market_key.player_name:
            return MarketSurebetEvaluation(
                canonical_event_id=canonical_event_id,
                canonical_market_key=canonical_market_key,
                status=SurebetStatus.INVALID_MARKET,
                completeness_status=MarketCompletenessStatus.INVALID,
                required_selection_types=(),
                available_selection_types=(),
                missing_selection_types=(),
                best_legs=(),
                rejection_reason="PLAYER market key requires a valid player_name",
                exclusion_reason_code="INVALID_MARKET_IDENTITY",
            )

        # 3. Check line requirement for line-dependent markets (TOTALS, HANDICAP, ASIAN_HANDICAP)
        if mkt_type == CanonicalMarketType.TOTALS.value and (canonical_market_key.line is None or canonical_market_key.line <= 0):
            return MarketSurebetEvaluation(
                canonical_event_id=canonical_event_id,
                canonical_market_key=canonical_market_key,
                status=SurebetStatus.INVALID_MARKET,
                completeness_status=MarketCompletenessStatus.INVALID,
                required_selection_types=(),
                available_selection_types=(),
                missing_selection_types=(),
                best_legs=(),
                rejection_reason="TOTALS market key is missing a valid positive numeric line",
                exclusion_reason_code="LINE_INVALID",
            )
        if mkt_type in (CanonicalMarketType.HANDICAP.value, CanonicalMarketType.ASIAN_HANDICAP.value) and canonical_market_key.line is None:
            return MarketSurebetEvaluation(
                canonical_event_id=canonical_event_id,
                canonical_market_key=canonical_market_key,
                status=SurebetStatus.INVALID_MARKET,
                completeness_status=MarketCompletenessStatus.INVALID,
                required_selection_types=(),
                available_selection_types=(),
                missing_selection_types=(),
                best_legs=(),
                rejection_reason=f"{mkt_type} market key is missing a valid numeric line",
                exclusion_reason_code="LINE_INVALID",
            )

        # 4. Check market period validity
        if not canonical_market_key.period or canonical_market_key.period.upper() not in [p.value for p in MarketPeriod]:
            return MarketSurebetEvaluation(
                canonical_event_id=canonical_event_id,
                canonical_market_key=canonical_market_key,
                status=SurebetStatus.INVALID_MARKET,
                completeness_status=MarketCompletenessStatus.INVALID,
                required_selection_types=(),
                available_selection_types=(),
                missing_selection_types=(),
                best_legs=(),
                rejection_reason="Canonical market key has an invalid period specification",
                exclusion_reason_code="INVALID_MARKET_IDENTITY",
            )

        required_types = SUPPORTED_MARKET_REQUIRED_SELECTIONS[mkt_type]

        # 4. Group valid provider odds per canonical selection type
        candidates_by_type: Dict[str, List[_CandidatePrice]] = {st: [] for st in required_types}
        selection_keys_by_type: Dict[str, CanonicalSelectionKey] = {}
        event_evidence: Optional[MatchEvidence] = None
        market_evidence: Dict[str, Any] = {}

        for comp in comparisons:
            # Enforce strict market and event isolation
            if comp.canonical_event_id != canonical_event_id or comp.canonical_market_key != canonical_market_key:
                continue

            sel_type = comp.canonical_selection_key.selection_type.upper()
            if sel_type not in candidates_by_type:
                continue

            if sel_type not in selection_keys_by_type:
                selection_keys_by_type[sel_type] = comp.canonical_selection_key

            if comp.event_evidence and event_evidence is None:
                event_evidence = comp.event_evidence
            if comp.market_evidence and not market_evidence:
                market_evidence = dict(comp.market_evidence)

            # Extract source candidate if valid and from authorized execution bookmaker
            s_valid = (
                comp.source_odds is not None
                and comp.source_odds > Decimal("1.0")
                and comp.status in (OddsComparisonStatus.VALID, OddsComparisonStatus.INCOMPLETE)
                and self.is_provider_allowed(comp.source_provider)
            )
            # Verify source wasn't explicitly flagged as invalid/inactive/ambiguous in evidence
            if s_valid and comp.evidence.get("source_validation_status") == "VALID":
                s_tax_res = self.tax_engine.calculate_net_odds(
                    raw_odds=comp.source_odds,
                    bookmaker=comp.source_provider,
                )
                candidates_by_type[sel_type].append(
                    _CandidatePrice(
                        provider=comp.source_provider,
                        odds=comp.source_odds,
                        selection_id=comp.source_selection_id,
                        event_id=comp.source_event_id,
                        market_id=comp.source_market_id,
                        raw_odds=comp.raw_source_odds,
                        selection_evidence=dict(comp.selection_evidence),
                        effective_odds=s_tax_res.effective_net_odds,
                        tax_rate=self.tax_engine.get_config(comp.source_provider).tax_rate if s_tax_res.is_tax_applied else Decimal("0"),
                        is_tax_applied=s_tax_res.is_tax_applied,
                    )
                )

            # Extract target candidate if valid and from authorized execution bookmaker
            t_valid = (
                comp.target_odds is not None
                and comp.target_odds > Decimal("1.0")
                and comp.status in (OddsComparisonStatus.VALID, OddsComparisonStatus.INCOMPLETE)
                and self.is_provider_allowed(comp.target_provider)
            )
            # Verify target wasn't explicitly flagged as invalid/inactive/ambiguous in evidence
            if t_valid and comp.evidence.get("target_validation_status") == "VALID":
                t_tax_res = self.tax_engine.calculate_net_odds(
                    raw_odds=comp.target_odds,
                    bookmaker=comp.target_provider,
                )
                candidates_by_type[sel_type].append(
                    _CandidatePrice(
                        provider=comp.target_provider,
                        odds=comp.target_odds,
                        selection_id=comp.target_selection_id,
                        event_id=comp.target_event_id,
                        market_id=comp.target_market_id,
                        raw_odds=comp.raw_target_odds,
                        selection_evidence=dict(comp.selection_evidence),
                        effective_odds=t_tax_res.effective_net_odds,
                        tax_rate=self.tax_engine.get_config(comp.target_provider).tax_rate if t_tax_res.is_tax_applied else Decimal("0"),
                        is_tax_applied=t_tax_res.is_tax_applied,
                    )
                )

        # 5. Check completeness across all required selection outcomes
        available_types = tuple(st for st in required_types if len(candidates_by_type[st]) > 0)
        missing_types = tuple(st for st in required_types if len(candidates_by_type[st]) == 0)

        if len(missing_types) > 0:
            return MarketSurebetEvaluation(
                canonical_event_id=canonical_event_id,
                canonical_market_key=canonical_market_key,
                status=SurebetStatus.INCOMPLETE_MARKET,
                completeness_status=MarketCompletenessStatus.INCOMPLETE,
                required_selection_types=required_types,
                available_selection_types=available_types,
                missing_selection_types=missing_types,
                best_legs=(),
                rejection_reason=f"Missing required canonical selections: {list(missing_types)}",
                exclusion_reason_code="INCOMPLETE_SELECTIONS",
            )

        # 6. Select BEST ODDS independently for every outcome maximizing effective net payout
        best_legs_list: List[SurebetLeg] = []
        for sel_type in required_types:
            cands = candidates_by_type[sel_type]
            # Deterministic best odds selection: highest effective net odds first; if tied, highest raw odds, then sort by provider name
            best_cand = max(
                cands,
                key=lambda c: (
                    c.effective_odds if c.effective_odds is not None else c.odds,
                    c.odds,
                    c.provider,
                ),
            )

            # Compute exact tax-adjusted implied probability: 1 / effective_odds
            eff_odds = best_cand.effective_odds if best_cand.effective_odds is not None else best_cand.odds
            implied_prob = Decimal("1.0") / eff_odds

            leg = SurebetLeg(
                canonical_selection_key=selection_keys_by_type[sel_type],
                selection_type=sel_type,
                provider=best_cand.provider,
                odds=best_cand.odds,
                source_selection_id=best_cand.selection_id,
                source_event_id=best_cand.event_id,
                source_market_id=best_cand.market_id,
                implied_probability=implied_prob,
                raw_odds=best_cand.raw_odds,
                selection_evidence=best_cand.selection_evidence,
                effective_odds=eff_odds,
                tax_rate=best_cand.tax_rate,
                is_tax_applied=best_cand.is_tax_applied,
            )
            best_legs_list.append(leg)

        best_legs = tuple(best_legs_list)

        # 7. Calculate Arbitrage Sum S = sum(1 / effective_odds_i)
        arbitrage_sum = sum(leg.implied_probability for leg in best_legs)
        assert arbitrage_sum is not None

        # Calculate theoretical arbitrage margin = (1 / S) - 1
        arbitrage_margin = (Decimal("1.0") / arbitrage_sum) - Decimal("1.0")

        # 8. Strict Surebet Boundary Evaluation:
        # S < 1.0 -> SUREBET
        # S == 1.0 -> NO_SUREBET (zero margin theoretical equilibrium)
        # S > 1.0 -> NO_SUREBET (negative margin)
        if arbitrage_sum < Decimal("1.0"):
            # Critical Safety Rule: Validate canonical event identity completeness
            if event_evidence is not None:
                ev_data = getattr(event_evidence, "evidence", {}) or {}
                h_name = getattr(event_evidence, "home_team", None) or ev_data.get("home_team")
                a_name = getattr(event_evidence, "away_team", None) or ev_data.get("away_team")
                if h_name is not None and a_name is not None:
                    h_clean = str(h_name).strip()
                    a_clean = str(a_name).strip()
                    if not h_clean or not a_clean or h_clean in ("—", "-", "Unknown") or a_clean in ("—", "-", "Unknown"):
                        return MarketSurebetEvaluation(
                            canonical_event_id=canonical_event_id,
                            canonical_market_key=canonical_market_key,
                            status=SurebetStatus.INVALID_MARKET,
                            completeness_status=MarketCompletenessStatus.INVALID,
                            required_selection_types=required_types,
                            available_selection_types=available_types,
                            missing_selection_types=(),
                            best_legs=(),
                            exclusion_reason_code="INCOMPLETE_EVENT_IDENTITY",
                        )

            # Critical Safety Rule: Prohibit single-bookmaker cross-market selection stitching
            used_bookmakers = tuple(sorted(set(leg.provider for leg in best_legs)))
            if len(used_bookmakers) == 1:
                market_ids = set(leg.source_market_id for leg in best_legs if leg.source_market_id)
                if len(market_ids) > 1:
                    return MarketSurebetEvaluation(
                        canonical_event_id=canonical_event_id,
                        canonical_market_key=canonical_market_key,
                        status=SurebetStatus.INVALID_MARKET,
                        completeness_status=MarketCompletenessStatus.INVALID,
                        required_selection_types=required_types,
                        available_selection_types=available_types,
                        missing_selection_types=(),
                        best_legs=best_legs,
                        implied_probability_sum=arbitrage_sum,
                        arbitrage_margin=arbitrage_margin,
                        rejection_reason="Single-bookmaker legs originate from multiple distinct source market IDs",
                        exclusion_reason_code="CROSS_MARKET_CONTAMINATION",
                    )

            opp_id = self.generate_deterministic_opportunity_id(
                canonical_event_id=canonical_event_id,
                canonical_market_key=canonical_market_key,
                legs=best_legs,
            )
            is_mixed = len(used_bookmakers) > 1

            opportunity = SurebetOpportunity(
                opportunity_id=opp_id,
                canonical_event_id=canonical_event_id,
                canonical_market_key=canonical_market_key,
                legs=best_legs,
                implied_probability_sum=arbitrage_sum,
                arbitrage_margin=arbitrage_margin,
                status=SurebetStatus.SUREBET,
                is_mixed_bookmakers=is_mixed,
                bookmakers=used_bookmakers,
                event_evidence=event_evidence,
                market_evidence=market_evidence,
            )

            return MarketSurebetEvaluation(
                canonical_event_id=canonical_event_id,
                canonical_market_key=canonical_market_key,
                status=SurebetStatus.SUREBET,
                completeness_status=MarketCompletenessStatus.COMPLETE,
                required_selection_types=required_types,
                available_selection_types=available_types,
                missing_selection_types=(),
                best_legs=best_legs,
                implied_probability_sum=arbitrage_sum,
                arbitrage_margin=arbitrage_margin,
                rejection_reason=None,
                opportunity=opportunity,
            )

        elif arbitrage_sum == Decimal("1.0"):
            return MarketSurebetEvaluation(
                canonical_event_id=canonical_event_id,
                canonical_market_key=canonical_market_key,
                status=SurebetStatus.NO_SUREBET,
                completeness_status=MarketCompletenessStatus.COMPLETE,
                required_selection_types=required_types,
                available_selection_types=available_types,
                missing_selection_types=(),
                best_legs=best_legs,
                implied_probability_sum=arbitrage_sum,
                arbitrage_margin=Decimal("0.0"),
                rejection_reason="Arbitrage sum equals exactly 1.0 (zero-margin theoretical equilibrium, not a surebet)",
            )

        else:  # arbitrage_sum > Decimal("1.0")
            return MarketSurebetEvaluation(
                canonical_event_id=canonical_event_id,
                canonical_market_key=canonical_market_key,
                status=SurebetStatus.NO_SUREBET,
                completeness_status=MarketCompletenessStatus.COMPLETE,
                required_selection_types=required_types,
                available_selection_types=available_types,
                missing_selection_types=(),
                best_legs=best_legs,
                implied_probability_sum=arbitrage_sum,
                arbitrage_margin=arbitrage_margin,
                rejection_reason=f"Arbitrage sum {arbitrage_sum} > 1.0 (negative margin {arbitrage_margin})",
            )

    def detect_markets(
        self,
        comparisons: Sequence[OddsComparison],
    ) -> SurebetDetectionResult:
        """Batch evaluates a collection of OddsComparison records grouped by canonical market."""
        t0 = time.perf_counter()

        # Group comparisons by (canonical_event_id, canonical_market_key)
        grouped_comps: Dict[Tuple[str, CanonicalMarketKey], List[OddsComparison]] = {}
        for comp in comparisons:
            grp_key = (comp.canonical_event_id, comp.canonical_market_key)
            grouped_comps.setdefault(grp_key, []).append(comp)

        evaluations: List[MarketSurebetEvaluation] = []
        surebet_evals: List[MarketSurebetEvaluation] = []
        no_surebet_evals: List[MarketSurebetEvaluation] = []
        incomplete_evals: List[MarketSurebetEvaluation] = []
        unsupported_evals: List[MarketSurebetEvaluation] = []
        invalid_evals: List[MarketSurebetEvaluation] = []
        opportunities: List[SurebetOpportunity] = []
        errors: List[str] = []

        supported_count = 0
        unsupported_count = 0
        complete_count = 0
        incomplete_count = 0
        invalid_count = 0
        mixed_books_count = 0
        single_book_count = 0

        for (event_id, mkt_key), mkt_comparisons in grouped_comps.items():
            try:
                evaluation = self.evaluate_market(
                    canonical_event_id=event_id,
                    canonical_market_key=mkt_key,
                    comparisons=mkt_comparisons,
                )
                evaluations.append(evaluation)

                if evaluation.status == SurebetStatus.SUREBET:
                    surebet_evals.append(evaluation)
                    complete_count += 1
                    supported_count += 1
                    if evaluation.opportunity is not None:
                        opportunities.append(evaluation.opportunity)
                        if evaluation.opportunity.is_mixed_bookmakers:
                            mixed_books_count += 1
                        else:
                            single_book_count += 1

                elif evaluation.status == SurebetStatus.NO_SUREBET:
                    no_surebet_evals.append(evaluation)
                    complete_count += 1
                    supported_count += 1

                elif evaluation.status == SurebetStatus.INCOMPLETE_MARKET:
                    incomplete_evals.append(evaluation)
                    incomplete_count += 1
                    supported_count += 1

                elif evaluation.status == SurebetStatus.UNSUPPORTED_MARKET:
                    unsupported_evals.append(evaluation)
                    unsupported_count += 1

                elif evaluation.status == SurebetStatus.INVALID_MARKET:
                    invalid_evals.append(evaluation)
                    invalid_count += 1

            except Exception as exc:
                errors.append(f"Error evaluating surebet for market '{mkt_key.to_key_string()}' in event '{event_id}': {exc}")

        duration_ms = (time.perf_counter() - t0) * 1000.0

        # Calculate margins summary safely (avoid misleading 0s when no surebets exist)
        avg_margin: Optional[Decimal] = None
        max_margin: Optional[Decimal] = None
        min_margin: Optional[Decimal] = None
        avg_books: Optional[Decimal] = None
        max_books: int = 0

        if opportunities:
            margins = [opp.arbitrage_margin for opp in opportunities]
            avg_margin = sum(margins) / Decimal(len(margins))
            max_margin = max(margins)
            min_margin = min(margins)

            book_counts = [len(opp.bookmakers) for opp in opportunities]
            avg_books = Decimal(sum(book_counts)) / Decimal(len(book_counts))
            max_books = max(book_counts)

        metrics = SurebetDetectionMetrics(
            input_market_count=len(grouped_comps),
            supported_market_count=supported_count,
            unsupported_market_count=unsupported_count,
            complete_market_count=complete_count,
            incomplete_market_count=incomplete_count,
            invalid_market_count=invalid_count,
            surebet_count=len(opportunities),
            no_surebet_count=len(no_surebet_evals),
            markets_with_mixed_bookmakers=mixed_books_count,
            markets_with_single_bookmaker=single_book_count,
            average_arbitrage_margin=avg_margin,
            max_arbitrage_margin=max_margin,
            min_arbitrage_margin=min_margin,
            average_number_of_bookmakers_used=avg_books,
            max_number_of_bookmakers_used=max_books,
            detection_duration_ms=duration_ms,
        )

        return SurebetDetectionResult(
            opportunities=opportunities,
            evaluations=evaluations,
            surebet_evaluations=surebet_evals,
            no_surebet_evaluations=no_surebet_evals,
            incomplete_evaluations=incomplete_evals,
            unsupported_evaluations=unsupported_evals,
            invalid_evaluations=invalid_evals,
            errors=errors,
            metrics=metrics,
        )

    def detect(
        self,
        input_data: Union[
            OddsComparisonResult,
            Sequence[OddsComparison],
            CrossBookmakerValidationResult,
            ComparableSelectionPair,
        ],
    ) -> SurebetDetectionResult:
        """Polymorphic entry point accepting OddsComparisonResult, comparisons, or Stage 5.3 result."""
        if isinstance(input_data, OddsComparisonResult):
            return self.detect_markets(input_data.comparisons)
        elif isinstance(input_data, CrossBookmakerValidationResult):
            odds_comp_result = OddsComparisonEngine().compare(input_data)
            return self.detect_markets(odds_comp_result.comparisons)
        elif isinstance(input_data, (list, tuple)):
            if len(input_data) > 0 and isinstance(input_data[0], ComparableSelectionPair):
                odds_comp_result = OddsComparisonEngine().compare(input_data)
                return self.detect_markets(odds_comp_result.comparisons)
            return self.detect_markets(input_data)
        else:
            raise TypeError(f"Unsupported input type for SurebetDetectorEngine.detect: {type(input_data).__name__}")
