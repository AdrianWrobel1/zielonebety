"""
Stage 6.4: Opportunity Alert Policy & Change Significance

Provides deterministic, pure, configurable change classification for betting
opportunities to distinguish actionable market shifts from price noise.

Core Principles:
- Single Responsibility: Decides if an opportunity change justifies a new alert.
- Pure Decision Component: Zero database, network, Telegram, or state mutations.
- Strict Decimal Arithmetic: Exact Decimal math for margin and odds comparisons; zero float drift.
- Comprehensive Lineage & Structure Awareness: Bookmaker changes, line changes, or leg changes are structural.
- Directional Awareness: Tracks margin improvement vs deterioration explicitly.
- Explainable Decisions: Returns structured reasons and metrics for full auditability.
"""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
import json
from typing import Any, Dict, List, Optional, Protocol, Sequence, Set, Tuple, Union

from normalization.surebet import SurebetLeg, SurebetOpportunity


class ChangeClassification(str, Enum):
    """Authoritative classification of the significance of an opportunity change."""
    NO_CHANGE = "NO_CHANGE"                    # Exact identical opportunity (odds, margin, legs)
    INSIGNIFICANT_CHANGE = "INSIGNIFICANT_CHANGE"  # Minor price/margin fluctuations below thresholds
    MATERIAL_CHANGE = "MATERIAL_CHANGE"        # Significant economic or structural change justifying alert


@dataclass(frozen=True)
class OpportunityAlertConfig:
    """Configuration container for opportunity alert thresholds and policy rules.

    All numerical thresholds use Decimal arithmetic for precision and financial consistency.
    """
    # Minimum absolute margin delta threshold to qualify as material (e.g. 0.0050 = 0.50 percentage points)
    min_margin_delta: Decimal = Decimal("0.0050")

    # Minimum relative odds change threshold on any leg to qualify as material (e.g. 0.0200 = 2.0%)
    min_odds_relative_delta: Decimal = Decimal("0.0200")

    # Optional minimum absolute odds delta threshold on any leg (e.g. Decimal("0.10"))
    min_odds_absolute_delta: Optional[Decimal] = None

    # Optional cooldown in seconds between alerts for the same opportunity
    cooldown_seconds: Optional[float] = None

    # Whether structural changes (provider, line, leg changes) bypass cooldown
    bypass_cooldown_on_structural: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.min_margin_delta, Decimal) or self.min_margin_delta < Decimal("0.0"):
            raise ValueError(f"min_margin_delta must be a non-negative Decimal, got: {self.min_margin_delta!r}")
        if not isinstance(self.min_odds_relative_delta, Decimal) or self.min_odds_relative_delta < Decimal("0.0"):
            raise ValueError(f"min_odds_relative_delta must be a non-negative Decimal, got: {self.min_odds_relative_delta!r}")
        if self.min_odds_absolute_delta is not None:
            if not isinstance(self.min_odds_absolute_delta, Decimal) or self.min_odds_absolute_delta < Decimal("0.0"):
                raise ValueError(f"min_odds_absolute_delta must be a non-negative Decimal or None, got: {self.min_odds_absolute_delta!r}")
        if self.cooldown_seconds is not None and self.cooldown_seconds < 0.0:
            raise ValueError(f"cooldown_seconds must be a non-negative float or None, got: {self.cooldown_seconds!r}")


@dataclass(frozen=True)
class OpportunityChangeEvaluation:
    """Immutable, explainable result of an alert policy change evaluation."""
    classification: ChangeClassification
    is_material: bool
    reasons: Tuple[str, ...]
    margin_delta: Decimal
    max_odds_relative_delta: Decimal
    max_odds_absolute_delta: Decimal
    structural_change: bool = False
    is_cooldown_suppressed: bool = False
    details: Dict[str, Any] = field(default_factory=dict)


class OpportunityAlertPolicy(Protocol):
    """Protocol for opportunity change classification policies."""

    def classify_change(
        self,
        previous: Union[SurebetOpportunity, Dict[str, Any], str],
        current: SurebetOpportunity,
        last_alerted_at: Optional[datetime] = None,
        current_time: Optional[datetime] = None,
    ) -> OpportunityChangeEvaluation:
        """Evaluates previous opportunity snapshot against current opportunity."""
        ...


def _parse_snapshot_dict(
    snapshot: Union[SurebetOpportunity, Dict[str, Any], str],
) -> Optional[Dict[str, Any]]:
    """Converts input snapshot into a normalized dictionary for safe comparison."""
    if isinstance(snapshot, SurebetOpportunity):
        legs_data = []
        for leg in sorted(snapshot.legs, key=lambda l: l.selection_type):
            legs_data.append({
                "selection_type": leg.selection_type,
                "provider": leg.provider,
                "odds": str(leg.odds),
                "source_selection_id": leg.source_selection_id,
            })
        mkt_key = snapshot.canonical_market_key
        return {
            "opportunity_id": snapshot.opportunity_id,
            "canonical_event_id": snapshot.canonical_event_id,
            "canonical_market_key": {
                "market_type": mkt_key.market_type,
                "period": mkt_key.period,
                "scope": mkt_key.scope,
                "line": str(mkt_key.line) if mkt_key.line is not None else None,
                "key_string": mkt_key.to_key_string(),
            },
            "arbitrage_margin": str(snapshot.arbitrage_margin),
            "implied_probability_sum": str(snapshot.implied_probability_sum),
            "bookmakers": list(snapshot.bookmakers),
            "legs": legs_data,
        }
    elif isinstance(snapshot, dict):
        return snapshot
    elif isinstance(snapshot, str):
        try:
            parsed = json.loads(snapshot)
            if isinstance(parsed, dict):
                return parsed
            return None
        except Exception:
            return None
    return None


class DefaultOpportunityAlertPolicy:
    """Authoritative deterministic implementation of the Stage 6.4 Opportunity Alert Policy.

    Features:
    - Exact Decimal precision across all comparisons.
    - Multi-dimensional change detection: margin delta, relative odds delta, absolute odds delta.
    - Structural change classification (provider, selection, line, leg count).
    - Configurable cooldown suppression with structural bypass.
    - Rich, auditable reason generation.
    """

    def __init__(self, config: Optional[OpportunityAlertConfig] = None) -> None:
        self._config = config or OpportunityAlertConfig()

    @property
    def config(self) -> OpportunityAlertConfig:
        return self._config

    def classify_change(
        self,
        previous: Union[SurebetOpportunity, Dict[str, Any], str],
        current: SurebetOpportunity,
        last_alerted_at: Optional[datetime] = None,
        current_time: Optional[datetime] = None,
    ) -> OpportunityChangeEvaluation:
        """Classifies the change significance between previous snapshot and current opportunity."""
        prev_dict = _parse_snapshot_dict(previous)
        if prev_dict is None:
            # Fallback for unparseable snapshot: treat as material change for safety
            return OpportunityChangeEvaluation(
                classification=ChangeClassification.MATERIAL_CHANGE,
                is_material=True,
                reasons=("Previous opportunity snapshot could not be parsed or is empty; defaulting to MATERIAL_CHANGE",),
                margin_delta=Decimal("0.0"),
                max_odds_relative_delta=Decimal("0.0"),
                max_odds_absolute_delta=Decimal("0.0"),
                structural_change=True,
                details={"error": "unparseable_snapshot"},
            )

        if not isinstance(current, SurebetOpportunity):
            raise TypeError(f"Expected current to be SurebetOpportunity, got {type(current).__name__}")

        reasons: List[str] = []
        details: Dict[str, Any] = {}
        structural_change = False

        # 1. Structural Checks: Market Line, Scope, Period, Market Type
        prev_mkt = prev_dict.get("canonical_market_key", {})
        curr_mkt = current.canonical_market_key

        prev_line_str = prev_mkt.get("line") if isinstance(prev_mkt, dict) else None
        prev_line = Decimal(str(prev_line_str)) if prev_line_str is not None else None
        curr_line = curr_mkt.line

        if prev_line != curr_line:
            structural_change = True
            reasons.append(f"Market line changed: {prev_line} -> {curr_line}")

        if isinstance(prev_mkt, dict):
            if prev_mkt.get("market_type") != curr_mkt.market_type:
                structural_change = True
                reasons.append(f"Market type changed: {prev_mkt.get('market_type')} -> {curr_mkt.market_type}")
            if prev_mkt.get("period") != curr_mkt.period:
                structural_change = True
                reasons.append(f"Market period changed: {prev_mkt.get('period')} -> {curr_mkt.period}")
            if prev_mkt.get("scope") != curr_mkt.scope:
                structural_change = True
                reasons.append(f"Market scope changed: {prev_mkt.get('scope')} -> {curr_mkt.scope}")

        # 2. Structural Checks: Legs and Providers
        prev_legs_raw = prev_dict.get("legs", [])
        prev_legs_map: Dict[str, Dict[str, Any]] = {}
        if isinstance(prev_legs_raw, list):
            for l in prev_legs_raw:
                if isinstance(l, dict) and "selection_type" in l:
                    prev_legs_map[str(l["selection_type"])] = l

        curr_legs_map: Dict[str, SurebetLeg] = {leg.selection_type: leg for leg in current.legs}

        if len(prev_legs_map) != len(curr_legs_map):
            structural_change = True
            reasons.append(f"Leg count changed: {len(prev_legs_map)} -> {len(curr_legs_map)}")

        if set(prev_legs_map.keys()) != set(curr_legs_map.keys()):
            structural_change = True
            added = set(curr_legs_map.keys()) - set(prev_legs_map.keys())
            removed = set(prev_legs_map.keys()) - set(curr_legs_map.keys())
            if added:
                reasons.append(f"Legs added: {sorted(added)}")
            if removed:
                reasons.append(f"Legs removed: {sorted(removed)}")

        # Check provider changes for matching selections
        for sel_type, curr_leg in curr_legs_map.items():
            prev_leg_data = prev_legs_map.get(sel_type)
            if prev_leg_data is not None:
                prev_prov = str(prev_leg_data.get("provider", "")).lower()
                curr_prov = curr_leg.provider.lower()
                if prev_prov != curr_prov:
                    structural_change = True
                    reasons.append(f"Bookmaker changed on leg '{sel_type}': {prev_prov} -> {curr_prov}")

        # 3. Numeric Comparisons: Margin and Leg Odds
        try:
            prev_margin_raw = prev_dict.get("arbitrage_margin", "0.0")
            prev_margin = Decimal(str(prev_margin_raw))
        except Exception:
            prev_margin = Decimal("0.0")

        curr_margin = current.arbitrage_margin
        margin_delta = curr_margin - prev_margin
        abs_margin_delta = abs(margin_delta)

        details["previous_margin"] = str(prev_margin)
        details["current_margin"] = str(curr_margin)
        details["margin_delta"] = str(margin_delta)
        details["abs_margin_delta"] = str(abs_margin_delta)

        margin_is_material = abs_margin_delta >= self._config.min_margin_delta
        if margin_is_material:
            direction = "improved" if margin_delta > Decimal("0.0") else "deteriorated"
            reasons.append(
                f"Arbitrage margin {direction} by {margin_delta * Decimal('100'):+.2f} percentage points "
                f"(abs delta {abs_margin_delta * Decimal('100'):.2f} pp >= threshold {self._config.min_margin_delta * Decimal('100'):.2f} pp)"
            )

        # 4. Odds Per-Leg Comparison
        max_odds_rel_delta = Decimal("0.0")
        max_odds_abs_delta = Decimal("0.0")
        odds_is_material = False
        legs_odds_details: Dict[str, Any] = {}

        for sel_type, curr_leg in curr_legs_map.items():
            prev_leg_data = prev_legs_map.get(sel_type)
            if prev_leg_data is not None:
                try:
                    prev_odds = Decimal(str(prev_leg_data.get("odds", "1.0")))
                except Exception:
                    prev_odds = Decimal("1.0")

                curr_odds = curr_leg.odds
                abs_odds_delta = abs(curr_odds - prev_odds)
                rel_odds_delta = (abs_odds_delta / prev_odds) if prev_odds > Decimal("0.0") else Decimal("0.0")

                legs_odds_details[sel_type] = {
                    "previous_odds": str(prev_odds),
                    "current_odds": str(curr_odds),
                    "abs_delta": str(abs_odds_delta),
                    "rel_delta": str(rel_odds_delta),
                }

                if abs_odds_delta > max_odds_abs_delta:
                    max_odds_abs_delta = abs_odds_delta
                if rel_odds_delta > max_odds_rel_delta:
                    max_odds_rel_delta = rel_odds_delta

                if rel_odds_delta >= self._config.min_odds_relative_delta:
                    odds_is_material = True
                    reasons.append(
                        f"Leg '{sel_type}' odds changed from {prev_odds} to {curr_odds} "
                        f"(relative delta {rel_odds_delta * Decimal('100'):.2f}% >= threshold {self._config.min_odds_relative_delta * Decimal('100'):.2f}%)"
                    )

                if self._config.min_odds_absolute_delta is not None:
                    if abs_odds_delta >= self._config.min_odds_absolute_delta:
                        odds_is_material = True
                        reasons.append(
                            f"Leg '{sel_type}' odds absolute change {abs_odds_delta:.4f} >= threshold {self._config.min_odds_absolute_delta:.4f}"
                        )

        details["legs_odds"] = legs_odds_details
        details["max_odds_relative_delta"] = str(max_odds_rel_delta)
        details["max_odds_absolute_delta"] = str(max_odds_abs_delta)

        # 5. Cooldown Check
        in_cooldown = False
        if self._config.cooldown_seconds is not None and last_alerted_at is not None and current_time is not None:
            elapsed_seconds = (current_time - last_alerted_at).total_seconds()
            if elapsed_seconds < self._config.cooldown_seconds:
                in_cooldown = True
                details["cooldown_elapsed_seconds"] = elapsed_seconds
                details["cooldown_required_seconds"] = self._config.cooldown_seconds

        # 6. Final Decision Synthesis
        is_material_candidate = structural_change or margin_is_material or odds_is_material

        if not is_material_candidate:
            # Check for exact equality vs noise
            if abs_margin_delta == Decimal("0.0") and max_odds_abs_delta == Decimal("0.0") and not structural_change:
                return OpportunityChangeEvaluation(
                    classification=ChangeClassification.NO_CHANGE,
                    is_material=False,
                    reasons=("No change in arbitrage margin, odds, or market structure",),
                    margin_delta=margin_delta,
                    max_odds_relative_delta=max_odds_rel_delta,
                    max_odds_absolute_delta=max_odds_abs_delta,
                    structural_change=False,
                    is_cooldown_suppressed=False,
                    details=details,
                )
            else:
                reasons.append(
                    f"Changes below material significance thresholds (abs margin delta: {abs_margin_delta * Decimal('100'):.3f} pp < {self._config.min_margin_delta * Decimal('100'):.2f} pp, "
                    f"max odds relative delta: {max_odds_rel_delta * Decimal('100'):.2f}% < {self._config.min_odds_relative_delta * Decimal('100'):.2f}%)"
                )
                return OpportunityChangeEvaluation(
                    classification=ChangeClassification.INSIGNIFICANT_CHANGE,
                    is_material=False,
                    reasons=tuple(reasons),
                    margin_delta=margin_delta,
                    max_odds_relative_delta=max_odds_rel_delta,
                    max_odds_absolute_delta=max_odds_abs_delta,
                    structural_change=False,
                    is_cooldown_suppressed=False,
                    details=details,
                )

        # Candidate is material: apply cooldown rule if applicable
        if in_cooldown:
            if structural_change and self._config.bypass_cooldown_on_structural:
                reasons.append("Structural change bypasses alert cooldown window")
                return OpportunityChangeEvaluation(
                    classification=ChangeClassification.MATERIAL_CHANGE,
                    is_material=True,
                    reasons=tuple(reasons),
                    margin_delta=margin_delta,
                    max_odds_relative_delta=max_odds_rel_delta,
                    max_odds_absolute_delta=max_odds_abs_delta,
                    structural_change=structural_change,
                    is_cooldown_suppressed=False,
                    details=details,
                )
            else:
                suppress_reason = (
                    f"Material change suppressed due to active alert cooldown "
                    f"({details.get('cooldown_elapsed_seconds', 0.0):.1f}s elapsed < {self._config.cooldown_seconds:.1f}s window)"
                )
                reasons.append(suppress_reason)
                return OpportunityChangeEvaluation(
                    classification=ChangeClassification.INSIGNIFICANT_CHANGE,
                    is_material=False,
                    reasons=tuple(reasons),
                    margin_delta=margin_delta,
                    max_odds_relative_delta=max_odds_rel_delta,
                    max_odds_absolute_delta=max_odds_abs_delta,
                    structural_change=structural_change,
                    is_cooldown_suppressed=True,
                    details=details,
                )

        return OpportunityChangeEvaluation(
            classification=ChangeClassification.MATERIAL_CHANGE,
            is_material=True,
            reasons=tuple(reasons),
            margin_delta=margin_delta,
            max_odds_relative_delta=max_odds_rel_delta,
            max_odds_absolute_delta=max_odds_abs_delta,
            structural_change=structural_change,
            is_cooldown_suppressed=False,
            details=details,
        )
