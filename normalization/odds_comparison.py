"""
Stage 5.4: Cross-Bookmaker Odds Comparison Layer

Implements the provider-independent odds comparison layer on top of validated
Stage 5.3 ComparableSelectionPair and CrossBookmakerValidationResult records:

    Stage 5.3 CrossBookmakerValidationResult (ComparableSelectionPair)
                                ↓
                 Stage 5.4 OddsComparisonEngine
                                ↓
                    OddsComparisonResult

Architecture Invariants:
- Gated Input: Consumes ONLY validated MATCHED selection pairs from Stage 5.3.
- Numerical Determinism: Exact Decimal representation for arithmetic and implied probabilities.
- Zero Semantic Mutation: Odds comparison never alters or rematches events/markets/selections.
- Strict Provider Symmetry: Comparing A vs B or B vs A produces identical semantic conclusions.
- Explicit Error Handling: Differentiates VALID, INCOMPLETE, NO_ODDS, INVALID, INACTIVE, and AMBIGUOUS.
- Full Multi-Layer Lineage: Preserves canonical IDs, provider IDs, internal IDs, and match evidence.
- Zero Arbitrage Boundary: Computes NO surebet margins, stake distributions, or Kelly staking (Stage 5.5).
- Zero Valuebet Boundary: Accesses NO reference bookmakers, sharp odds, or expected values (EV).
- Immutability: Pure functions returning new frozen result objects without mutating domain entities.
"""

import math
import time
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from domain.models import (
    MatchEvidence,
    Odds,
    Selection,
)
from normalization.market_identity import CanonicalMarketKey
from normalization.selection_identity import CanonicalSelectionKey
from normalization.validation_pipeline import (
    ComparableSelectionPair,
    CrossBookmakerValidationResult,
)


class OddsComparisonStatus(str, Enum):
    """Authoritative status classification for a cross-bookmaker selection odds comparison."""
    VALID = "VALID"            # Both provider odds present, numeric, finite, > 1.0, and active
    INCOMPLETE = "INCOMPLETE"  # One provider has valid odds, other provider odds are missing/None
    NO_ODDS = "NO_ODDS"        # Neither provider has odds available (both None/missing)
    INVALID = "INVALID"        # Non-numeric, non-finite (NaN/Inf), <= 1.0, or malformed odds
    INACTIVE = "INACTIVE"      # Odds or selection marked inactive, suspended, or closed
    AMBIGUOUS = "AMBIGUOUS"    # Multiple conflicting active prices found for single selection


@dataclass(frozen=True)
class OddsComparison:
    """Immutable, auditable cross-bookmaker odds comparison record for a single matched selection.

    Preserves full lineage from canonical entities down to native bookmaker IDs and decomposed
    upstream match evidence.
    """
    canonical_event_id: str
    canonical_market_key: CanonicalMarketKey
    canonical_selection_key: CanonicalSelectionKey

    source_provider: str
    target_provider: str

    source_event_id: str
    target_event_id: str
    source_internal_event_id: str
    target_internal_event_id: str

    source_market_id: str
    target_market_id: str

    source_selection_id: str
    target_selection_id: str

    status: OddsComparisonStatus

    source_odds: Optional[Decimal] = None
    target_odds: Optional[Decimal] = None

    higher_odds_provider: Optional[str] = None  # source_provider, target_provider, or "TIE"
    odds_difference: Optional[Decimal] = None     # target_odds - source_odds (signed difference)
    absolute_difference: Optional[Decimal] = None # abs(target_odds - source_odds)
    odds_ratio: Optional[Decimal] = None          # higher_odds / lower_odds

    source_implied_probability: Optional[Decimal] = None  # 1 / source_odds (raw bookmaker implied)
    target_implied_probability: Optional[Decimal] = None  # 1 / target_odds (raw bookmaker implied)

    raw_source_odds: Optional[Odds] = None
    raw_target_odds: Optional[Odds] = None

    rejection_reason: Optional[str] = None

    event_evidence: Optional[MatchEvidence] = None
    market_evidence: Dict[str, Any] = field(default_factory=dict)
    selection_evidence: Dict[str, Any] = field(default_factory=dict)
    evidence: Dict[str, Any] = field(default_factory=dict)


@dataclass
class OddsComparisonMetrics:
    """Telemetry and summary metrics for an odds comparison batch run."""
    input_comparable_selection_count: int = 0
    valid_comparison_count: int = 0
    incomplete_count: int = 0
    no_odds_count: int = 0
    invalid_count: int = 0
    inactive_count: int = 0
    ambiguous_count: int = 0

    higher_odds_source_count: int = 0
    higher_odds_target_count: int = 0
    equal_odds_count: int = 0

    average_absolute_odds_difference: Optional[Decimal] = None
    max_absolute_odds_difference: Optional[Decimal] = None
    min_absolute_odds_difference: Optional[Decimal] = None
    average_odds_ratio: Optional[Decimal] = None

    comparison_duration_ms: float = 0.0

    @property
    def validity_rate(self) -> Optional[float]:
        """Valid comparisons / total input selections (or None if 0 inputs)."""
        if self.input_comparable_selection_count == 0:
            return None
        return round(self.valid_comparison_count / self.input_comparable_selection_count, 4)


@dataclass
class OddsComparisonResult:
    """Aggregate result container for a cross-bookmaker odds comparison execution."""
    comparisons: List[OddsComparison] = field(default_factory=list)
    valid_comparisons: List[OddsComparison] = field(default_factory=list)
    incomplete_comparisons: List[OddsComparison] = field(default_factory=list)
    invalid_comparisons: List[OddsComparison] = field(default_factory=list)
    inactive_comparisons: List[OddsComparison] = field(default_factory=list)
    ambiguous_comparisons: List[OddsComparison] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    metrics: OddsComparisonMetrics = field(default_factory=OddsComparisonMetrics)

    def generate_audit_report(self, detailed: bool = False) -> str:
        """Generates a human-readable, auditable summary report of the odds comparison run."""
        lines: List[str] = []
        lines.append("=" * 70)
        lines.append("STAGE 5.4 CROSS-BOOKMAKER ODDS COMPARISON AUDIT REPORT")
        lines.append("=" * 70)
        lines.append(f"Input Comparable Selection Pairs: {self.metrics.input_comparable_selection_count}")
        lines.append(
            f"Comparison Status Breakdown: VALID={self.metrics.valid_comparison_count}, "
            f"INCOMPLETE={self.metrics.incomplete_count}, NO_ODDS={self.metrics.no_odds_count}, "
            f"INVALID={self.metrics.invalid_count}, INACTIVE={self.metrics.inactive_count}, "
            f"AMBIGUOUS={self.metrics.ambiguous_count}"
        )
        if self.metrics.valid_comparison_count > 0:
            lines.append(
                f"Provider Dominance: Source Higher={self.metrics.higher_odds_source_count}, "
                f"Target Higher={self.metrics.higher_odds_target_count}, "
                f"Equal Odds (TIE)={self.metrics.equal_odds_count}"
            )
            lines.append(
                f"Price Differences: Avg |Diff|={self.metrics.average_absolute_odds_difference}, "
                f"Min |Diff|={self.metrics.min_absolute_odds_difference}, "
                f"Max |Diff|={self.metrics.max_absolute_odds_difference}, "
                f"Avg Ratio={self.metrics.average_odds_ratio}"
            )
        lines.append(f"Execution Duration: {self.metrics.comparison_duration_ms:.2f} ms")

        if detailed and self.comparisons:
            lines.append("-" * 70)
            lines.append("DETAILED ODDS COMPARISON RECORDS:")
            for idx, comp in enumerate(self.comparisons, 1):
                lines.append(
                    f"[{idx:03d}] Status: {comp.status.value} | Event: {comp.canonical_event_id} | "
                    f"Mkt: {comp.canonical_market_key.to_key_string()} | "
                    f"Sel: {comp.canonical_selection_key.to_key_string()}"
                )
                if comp.status == OddsComparisonStatus.VALID:
                    lines.append(
                        f"      {comp.source_provider}: {comp.source_odds} vs "
                        f"{comp.target_provider}: {comp.target_odds} -> "
                        f"Higher: {comp.higher_odds_provider} | |Diff|: {comp.absolute_difference} | "
                        f"Ratio: {comp.odds_ratio}"
                    )
                    lines.append(
                        f"      Implied Probs -> {comp.source_provider}: {comp.source_implied_probability} | "
                        f"{comp.target_provider}: {comp.target_implied_probability}"
                    )
                else:
                    lines.append(f"      Rejection/Diagnostic: {comp.rejection_reason}")
        lines.append("=" * 70)
        return "\n".join(lines)


class OddsComparisonEngine:
    """Deterministic, provider-independent cross-bookmaker odds comparison engine.

    Gated strictly to validated ComparableSelectionPair objects from Stage 5.3.
    """

    def parse_and_validate_odds(
        self,
        raw_odds_input: Any,
        selection: Optional[Selection] = None,
    ) -> Tuple[Optional[Decimal], Optional[str], OddsComparisonStatus]:
        """Validates raw odds value or Odds model and extracts exact Decimal representation.

        Validation rules:
        - None / missing -> (None, reason, NO_ODDS)
        - Inactive / Suspended selection or odds -> (None, reason, INACTIVE)
        - Non-numeric / NaN / Infinity -> (None, reason, INVALID)
        - Odds <= 1.0 (e.g. 1.00, 0, -2.5) -> (None, reason, INVALID)
        - Strict odds > 1.0 -> (Decimal, None, VALID)
        """
        # 1. Check selection suspension or inactive status
        if selection is not None:
            if hasattr(selection, "metadata") and isinstance(selection.metadata, dict):
                if selection.metadata.get("is_active") is False:
                    return None, "Selection is marked inactive", OddsComparisonStatus.INACTIVE
                if selection.metadata.get("status") in ("SUSPENDED", "CLOSED", "INACTIVE"):
                    return None, f"Selection status is {selection.metadata.get('status')}", OddsComparisonStatus.INACTIVE

        # 2. Extract raw numerical value
        if raw_odds_input is None:
            return None, "Odds value is missing (None)", OddsComparisonStatus.NO_ODDS

        raw_val: Any = raw_odds_input
        if isinstance(raw_odds_input, Odds):
            raw_val = raw_odds_input.decimal_odds

        # 3. Check for float NaN / Inf
        if isinstance(raw_val, float):
            if math.isnan(raw_val):
                return None, "Odds value is NaN", OddsComparisonStatus.INVALID
            if math.isinf(raw_val):
                return None, "Odds value is Infinity", OddsComparisonStatus.INVALID

        # 4. Exact conversion to Decimal
        try:
            if isinstance(raw_val, Decimal):
                odds_dec = raw_val
            elif isinstance(raw_val, (int, float)):
                odds_dec = Decimal(str(raw_val))
            elif isinstance(raw_val, str):
                cleaned_str = raw_val.strip()
                if not cleaned_str:
                    return None, "Empty odds string", OddsComparisonStatus.NO_ODDS
                odds_dec = Decimal(cleaned_str)
            else:
                return None, f"Unsupported odds type: {type(raw_val).__name__}", OddsComparisonStatus.INVALID
        except (InvalidOperation, ValueError, TypeError) as exc:
            return None, f"Failed to parse numeric odds '{raw_val}': {exc}", OddsComparisonStatus.INVALID

        # 5. Check Decimal NaN / Inf
        if odds_dec.is_nan():
            return None, "Decimal odds is NaN", OddsComparisonStatus.INVALID
        if odds_dec.is_infinite():
            return None, "Decimal odds is Infinite", OddsComparisonStatus.INVALID

        # 6. Strict financial inequality: decimal odds must be strictly > 1.0
        if odds_dec <= Decimal("1.0"):
            return None, f"Odds value {odds_dec} is <= 1.0 (minimum valid decimal odds > 1.0)", OddsComparisonStatus.INVALID

        return odds_dec, None, OddsComparisonStatus.VALID

    def compare_pair(self, pair: ComparableSelectionPair) -> OddsComparison:
        """Compares odds for a single validated ComparableSelectionPair deterministically."""
        if not isinstance(pair, ComparableSelectionPair):
            raise TypeError(f"Expected ComparableSelectionPair, got {type(pair).__name__}")

        # Extract native odds objects
        raw_s_odds = pair.source_odds
        raw_t_odds = pair.target_odds

        # Check for multiple active odds passed in metadata / evidence (ambiguity detection)
        s_has_ambiguity = False
        t_has_ambiguity = False
        if pair.source_selection and isinstance(pair.source_selection.metadata, dict):
            if pair.source_selection.metadata.get("multiple_active_odds"):
                s_has_ambiguity = True
        if pair.target_selection and isinstance(pair.target_selection.metadata, dict):
            if pair.target_selection.metadata.get("multiple_active_odds"):
                t_has_ambiguity = True

        # Validate both sides
        s_dec, s_err, s_status = self.parse_and_validate_odds(raw_s_odds, pair.source_selection)
        t_dec, t_err, t_status = self.parse_and_validate_odds(raw_t_odds, pair.target_selection)

        if s_has_ambiguity:
            s_status = OddsComparisonStatus.AMBIGUOUS
            s_err = "Multiple conflicting active odds snapshots for source selection"
        if t_has_ambiguity:
            t_status = OddsComparisonStatus.AMBIGUOUS
            t_err = "Multiple conflicting active odds snapshots for target selection"

        # Calculate implied probabilities for any valid side (raw bookmaker implied: 1 / odds)
        s_implied: Optional[Decimal] = (Decimal(1) / s_dec) if s_dec is not None else None
        t_implied: Optional[Decimal] = (Decimal(1) / t_dec) if t_dec is not None else None

        # Build diagnostic evidence snapshot
        evidence: Dict[str, Any] = {
            "source_provider": pair.source_provider,
            "target_provider": pair.target_provider,
            "source_odds_raw": raw_s_odds.decimal_odds if isinstance(raw_s_odds, Odds) else raw_s_odds,
            "target_odds_raw": raw_t_odds.decimal_odds if isinstance(raw_t_odds, Odds) else raw_t_odds,
            "source_validation_status": s_status.value,
            "target_validation_status": t_status.value,
        }
        if s_err:
            evidence["source_error"] = s_err
        if t_err:
            evidence["target_error"] = t_err

        # Determine overall comparison status
        if s_status == OddsComparisonStatus.NO_ODDS and t_status == OddsComparisonStatus.NO_ODDS:
            return OddsComparison(
                canonical_event_id=pair.canonical_event_id,
                canonical_market_key=pair.canonical_market_key,
                canonical_selection_key=pair.canonical_selection_key,
                source_provider=pair.source_provider,
                target_provider=pair.target_provider,
                source_event_id=pair.source_event_id,
                target_event_id=pair.target_event_id,
                source_internal_event_id=pair.source_internal_event_id,
                target_internal_event_id=pair.target_internal_event_id,
                source_market_id=pair.source_market_id,
                target_market_id=pair.target_market_id,
                source_selection_id=pair.source_selection_id,
                target_selection_id=pair.target_selection_id,
                status=OddsComparisonStatus.NO_ODDS,
                raw_source_odds=raw_s_odds,
                raw_target_odds=raw_t_odds,
                rejection_reason="Both bookmaker odds are missing (None)",
                event_evidence=pair.event_evidence,
                market_evidence=dict(pair.market_evidence),
                selection_evidence=dict(pair.selection_evidence),
                evidence=evidence,
            )

        if s_status == OddsComparisonStatus.INVALID or t_status == OddsComparisonStatus.INVALID:
            reasons = [r for r in (s_err, t_err) if r]
            return OddsComparison(
                canonical_event_id=pair.canonical_event_id,
                canonical_market_key=pair.canonical_market_key,
                canonical_selection_key=pair.canonical_selection_key,
                source_provider=pair.source_provider,
                target_provider=pair.target_provider,
                source_event_id=pair.source_event_id,
                target_event_id=pair.target_event_id,
                source_internal_event_id=pair.source_internal_event_id,
                target_internal_event_id=pair.target_internal_event_id,
                source_market_id=pair.source_market_id,
                target_market_id=pair.target_market_id,
                source_selection_id=pair.source_selection_id,
                target_selection_id=pair.target_selection_id,
                status=OddsComparisonStatus.INVALID,
                source_odds=s_dec,
                target_odds=t_dec,
                source_implied_probability=s_implied,
                target_implied_probability=t_implied,
                raw_source_odds=raw_s_odds,
                raw_target_odds=raw_t_odds,
                rejection_reason=" | ".join(reasons),
                event_evidence=pair.event_evidence,
                market_evidence=dict(pair.market_evidence),
                selection_evidence=dict(pair.selection_evidence),
                evidence=evidence,
            )

        if s_status == OddsComparisonStatus.INACTIVE or t_status == OddsComparisonStatus.INACTIVE:
            reasons = [r for r in (s_err, t_err) if r]
            return OddsComparison(
                canonical_event_id=pair.canonical_event_id,
                canonical_market_key=pair.canonical_market_key,
                canonical_selection_key=pair.canonical_selection_key,
                source_provider=pair.source_provider,
                target_provider=pair.target_provider,
                source_event_id=pair.source_event_id,
                target_event_id=pair.target_event_id,
                source_internal_event_id=pair.source_internal_event_id,
                target_internal_event_id=pair.target_internal_event_id,
                source_market_id=pair.source_market_id,
                target_market_id=pair.target_market_id,
                source_selection_id=pair.source_selection_id,
                target_selection_id=pair.target_selection_id,
                status=OddsComparisonStatus.INACTIVE,
                source_odds=s_dec,
                target_odds=t_dec,
                source_implied_probability=s_implied,
                target_implied_probability=t_implied,
                raw_source_odds=raw_s_odds,
                raw_target_odds=raw_t_odds,
                rejection_reason=" | ".join(reasons) if reasons else "Inactive or suspended odds/selection",
                event_evidence=pair.event_evidence,
                market_evidence=dict(pair.market_evidence),
                selection_evidence=dict(pair.selection_evidence),
                evidence=evidence,
            )

        if s_status == OddsComparisonStatus.AMBIGUOUS or t_status == OddsComparisonStatus.AMBIGUOUS:
            reasons = [r for r in (s_err, t_err) if r]
            return OddsComparison(
                canonical_event_id=pair.canonical_event_id,
                canonical_market_key=pair.canonical_market_key,
                canonical_selection_key=pair.canonical_selection_key,
                source_provider=pair.source_provider,
                target_provider=pair.target_provider,
                source_event_id=pair.source_event_id,
                target_event_id=pair.target_event_id,
                source_internal_event_id=pair.source_internal_event_id,
                target_internal_event_id=pair.target_internal_event_id,
                source_market_id=pair.source_market_id,
                target_market_id=pair.target_market_id,
                source_selection_id=pair.source_selection_id,
                target_selection_id=pair.target_selection_id,
                status=OddsComparisonStatus.AMBIGUOUS,
                source_odds=s_dec,
                target_odds=t_dec,
                source_implied_probability=s_implied,
                target_implied_probability=t_implied,
                raw_source_odds=raw_s_odds,
                raw_target_odds=raw_t_odds,
                rejection_reason=" | ".join(reasons) if reasons else "Ambiguous multiple active odds",
                event_evidence=pair.event_evidence,
                market_evidence=dict(pair.market_evidence),
                selection_evidence=dict(pair.selection_evidence),
                evidence=evidence,
            )

        if s_status == OddsComparisonStatus.NO_ODDS or t_status == OddsComparisonStatus.NO_ODDS:
            missing_provider = pair.source_provider if s_status == OddsComparisonStatus.NO_ODDS else pair.target_provider
            return OddsComparison(
                canonical_event_id=pair.canonical_event_id,
                canonical_market_key=pair.canonical_market_key,
                canonical_selection_key=pair.canonical_selection_key,
                source_provider=pair.source_provider,
                target_provider=pair.target_provider,
                source_event_id=pair.source_event_id,
                target_event_id=pair.target_event_id,
                source_internal_event_id=pair.source_internal_event_id,
                target_internal_event_id=pair.target_internal_event_id,
                source_market_id=pair.source_market_id,
                target_market_id=pair.target_market_id,
                source_selection_id=pair.source_selection_id,
                target_selection_id=pair.target_selection_id,
                status=OddsComparisonStatus.INCOMPLETE,
                source_odds=s_dec,
                target_odds=t_dec,
                source_implied_probability=s_implied,
                target_implied_probability=t_implied,
                raw_source_odds=raw_s_odds,
                raw_target_odds=raw_t_odds,
                rejection_reason=f"Incomplete pair: missing odds for provider '{missing_provider}'",
                event_evidence=pair.event_evidence,
                market_evidence=dict(pair.market_evidence),
                selection_evidence=dict(pair.selection_evidence),
                evidence=evidence,
            )

        # --- Both sides are VALID ---
        assert s_dec is not None and t_dec is not None

        # Calculate difference (target - source) and absolute difference
        odds_diff = t_dec - s_dec
        abs_diff = abs(odds_diff)

        # Determine higher odds provider or tie
        if s_dec > t_dec:
            higher_provider = pair.source_provider
        elif t_dec > s_dec:
            higher_provider = pair.target_provider
        else:
            higher_provider = "TIE"

        # Calculate ratio: higher / lower (both > 1.0 guaranteed)
        odds_ratio = max(s_dec, t_dec) / min(s_dec, t_dec)

        evidence.update({
            "higher_odds_provider": higher_provider,
            "odds_difference": str(odds_diff),
            "absolute_difference": str(abs_diff),
            "odds_ratio": str(odds_ratio),
        })

        return OddsComparison(
            canonical_event_id=pair.canonical_event_id,
            canonical_market_key=pair.canonical_market_key,
            canonical_selection_key=pair.canonical_selection_key,
            source_provider=pair.source_provider,
            target_provider=pair.target_provider,
            source_event_id=pair.source_event_id,
            target_event_id=pair.target_event_id,
            source_internal_event_id=pair.source_internal_event_id,
            target_internal_event_id=pair.target_internal_event_id,
            source_market_id=pair.source_market_id,
            target_market_id=pair.target_market_id,
            source_selection_id=pair.source_selection_id,
            target_selection_id=pair.target_selection_id,
            status=OddsComparisonStatus.VALID,
            source_odds=s_dec,
            target_odds=t_dec,
            higher_odds_provider=higher_provider,
            odds_difference=odds_diff,
            absolute_difference=abs_diff,
            odds_ratio=odds_ratio,
            source_implied_probability=s_implied,
            target_implied_probability=t_implied,
            raw_source_odds=raw_s_odds,
            raw_target_odds=raw_t_odds,
            rejection_reason=None,
            event_evidence=pair.event_evidence,
            market_evidence=dict(pair.market_evidence),
            selection_evidence=dict(pair.selection_evidence),
            evidence=evidence,
        )

    def compare_pairs(self, pairs: Sequence[ComparableSelectionPair]) -> OddsComparisonResult:
        """Batch processes a sequence of ComparableSelectionPair records deterministically."""
        t0 = time.perf_counter()

        comparisons: List[OddsComparison] = []
        valid_comps: List[OddsComparison] = []
        incomplete_comps: List[OddsComparison] = []
        invalid_comps: List[OddsComparison] = []
        inactive_comps: List[OddsComparison] = []
        ambiguous_comps: List[OddsComparison] = []
        errors: List[str] = []

        higher_source_count = 0
        higher_target_count = 0
        equal_count = 0
        no_odds_count = 0

        for pair in pairs:
            try:
                comp = self.compare_pair(pair)
                comparisons.append(comp)

                if comp.status == OddsComparisonStatus.VALID:
                    valid_comps.append(comp)
                    if comp.higher_odds_provider == comp.source_provider:
                        higher_source_count += 1
                    elif comp.higher_odds_provider == comp.target_provider:
                        higher_target_count += 1
                    elif comp.higher_odds_provider == "TIE":
                        equal_count += 1

                elif comp.status == OddsComparisonStatus.INCOMPLETE:
                    incomplete_comps.append(comp)
                elif comp.status == OddsComparisonStatus.NO_ODDS:
                    no_odds_count += 1
                    incomplete_comps.append(comp)
                elif comp.status == OddsComparisonStatus.INVALID:
                    invalid_comps.append(comp)
                elif comp.status == OddsComparisonStatus.INACTIVE:
                    inactive_comps.append(comp)
                elif comp.status == OddsComparisonStatus.AMBIGUOUS:
                    ambiguous_comps.append(comp)

            except Exception as exc:
                err_msg = f"Exception comparing selection pair for event '{getattr(pair, 'canonical_event_id', 'unknown')}': {exc}"
                errors.append(err_msg)

        duration_ms = (time.perf_counter() - t0) * 1000.0

        # Compute descriptive statistics safely (Division-by-zero protection)
        avg_abs_diff: Optional[Decimal] = None
        max_abs_diff: Optional[Decimal] = None
        min_abs_diff: Optional[Decimal] = None
        avg_ratio: Optional[Decimal] = None

        if valid_comps:
            abs_diffs = [c.absolute_difference for c in valid_comps if c.absolute_difference is not None]
            ratios = [c.odds_ratio for c in valid_comps if c.odds_ratio is not None]

            if abs_diffs:
                avg_abs_diff = sum(abs_diffs) / Decimal(len(abs_diffs))
                max_abs_diff = max(abs_diffs)
                min_abs_diff = min(abs_diffs)

            if ratios:
                avg_ratio = sum(ratios) / Decimal(len(ratios))

        metrics = OddsComparisonMetrics(
            input_comparable_selection_count=len(pairs),
            valid_comparison_count=len(valid_comps),
            incomplete_count=len(incomplete_comps) - no_odds_count,
            no_odds_count=no_odds_count,
            invalid_count=len(invalid_comps),
            inactive_count=len(inactive_comps),
            ambiguous_count=len(ambiguous_comps),
            higher_odds_source_count=higher_source_count,
            higher_odds_target_count=higher_target_count,
            equal_odds_count=equal_count,
            average_absolute_odds_difference=avg_abs_diff,
            max_absolute_odds_difference=max_abs_diff,
            min_absolute_odds_difference=min_abs_diff,
            average_odds_ratio=avg_ratio,
            comparison_duration_ms=duration_ms,
        )

        return OddsComparisonResult(
            comparisons=comparisons,
            valid_comparisons=valid_comps,
            incomplete_comparisons=incomplete_comps,
            invalid_comparisons=invalid_comps,
            inactive_comparisons=inactive_comps,
            ambiguous_comparisons=ambiguous_comps,
            errors=errors,
            metrics=metrics,
        )

    def compare_validation_result(
        self,
        validation_result: CrossBookmakerValidationResult,
    ) -> OddsComparisonResult:
        """Processes the output of Stage 5.3 CrossBookmakerValidationResult."""
        if not isinstance(validation_result, CrossBookmakerValidationResult):
            raise TypeError(f"Expected CrossBookmakerValidationResult, got {type(validation_result).__name__}")
        return self.compare_pairs(validation_result.comparable_selections)

    def compare(
        self,
        input_data: Union[CrossBookmakerValidationResult, Sequence[ComparableSelectionPair], ComparableSelectionPair],
    ) -> Union[OddsComparisonResult, OddsComparison]:
        """Convenience polymorphic entry point for single pair, pair list, or validation result."""
        if isinstance(input_data, CrossBookmakerValidationResult):
            return self.compare_validation_result(input_data)
        elif isinstance(input_data, ComparableSelectionPair):
            return self.compare_pair(input_data)
        elif isinstance(input_data, (list, tuple)):
            return self.compare_pairs(input_data)
        else:
            raise TypeError(f"Unsupported input type for OddsComparisonEngine.compare: {type(input_data).__name__}")
