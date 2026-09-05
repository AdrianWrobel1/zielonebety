"""
Stage 3: StatsHub Props -> Reference Odds -> Value Evaluation Engine

Provides unified, deterministic, and auditable calculation of:
1. Reference Odds validation and consensus aggregation across foreign bookmakers (Bet365, Unibet, Ladbrokes, Skybet, etc.).
2. Margin removal (De-margin / Fair Probability calculation) using Harmonic Consensus and domain overround normalizations.
3. Integration with Centralized TaxEngine for Polish execution bookmakers (Superbet: 12% turnover tax, Betclic: 0%).
4. Calculation of Gross EV, Net EV, Effective Net Odds, and Value Edge in percentage points.
5. Strict separation of decision stages:
   MATCHED -> REFERENCE_VALID -> EVALUATED -> POSITIVE_EDGE -> QUALIFIED / REJECTED.
6. Comprehensive audit trail (provenance) and standardized reason codes.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP, getcontext
from enum import Enum
import math
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from core.tax_engine import TaxEngine, get_tax_engine

# Exact decimal precision
getcontext().prec = 28

DECIMAL_ZERO = Decimal("0")
DECIMAL_ONE = Decimal("1")
DECIMAL_HUNDRED = Decimal("100")


class ValueEvaluationReasonCode(str, Enum):
    """Authoritative reason taxonomy for reference odds calculation and value evaluation."""
    QUALIFIED = "QUALIFIED"
    POSITIVE_EDGE = "POSITIVE_EDGE"
    BELOW_VALUE_THRESHOLD = "BELOW_VALUE_THRESHOLD"
    EXCESSIVE_VALUE_SANITY_CAP = "EXCESSIVE_VALUE_SANITY_CAP"
    INSUFFICIENT_REFERENCE_SOURCES = "INSUFFICIENT_REFERENCE_SOURCES"
    REFERENCE_GAP = "REFERENCE_GAP"
    MATCHING_FAILURE = "MATCHING_FAILURE"
    INVALID_REFERENCE_ODDS = "INVALID_REFERENCE_ODDS"
    STALE_REFERENCE_DATA = "STALE_REFERENCE_DATA"
    REFERENCE_MARKET_MISMATCH = "REFERENCE_MARKET_MISMATCH"
    REFERENCE_LINE_MISMATCH = "REFERENCE_LINE_MISMATCH"
    REFERENCE_SELECTION_MISMATCH = "REFERENCE_SELECTION_MISMATCH"
    POLISH_ODDS_UNAVAILABLE = "POLISH_ODDS_UNAVAILABLE"
    INVALID_POLISH_ODDS = "INVALID_POLISH_ODDS"
    ODDS_INACTIVE = "ODDS_INACTIVE"



@dataclass(frozen=True)
class PropsEvaluationConfig:
    """Configurable policies and thresholds for props value evaluation."""
    min_value_percent: Decimal = Decimal("3.0")  # Minimum Net EV % for QUALIFIED (e.g. +3.0%)
    max_value_sanity_cap: Decimal = Decimal("40.0")  # Maximum Net EV % to guard against palpable errors
    min_reference_sources: int = 1  # Minimum valid foreign bookmaker quotes required
    max_freshness_seconds: int = 1800  # 30 minutes threshold for reference odds freshness
    default_prop_margin_factor: Decimal = Decimal("1.04")  # Standard 1-side prop margin factor (~4% per side)
    min_bookmaker_odds: Decimal = Decimal("1.05")
    max_bookmaker_odds: Decimal = Decimal("35.0")
    tax_enabled_by_default: bool = True


@dataclass
class ReferenceOddsCalculation:
    """Structured, deterministic calculation audit for reference odds aggregation."""
    used_sources: List[Dict[str, Any]] = field(default_factory=list)
    rejected_sources: List[Dict[str, Any]] = field(default_factory=list)
    consensus_odds: Optional[float] = None
    consensus_implied_prob: Optional[float] = None
    fair_probability: Optional[float] = None
    fair_odds: Optional[float] = None
    margin_factor_applied: float = 1.04
    de_margin_method: str = "HARMONIC_CONSENSUS_PROP_MARGIN"
    is_valid: bool = False
    diagnostic: Optional[str] = None
    calculated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "used_sources": self.used_sources,
            "rejected_sources": self.rejected_sources,
            "used_sources_count": len(self.used_sources),
            "rejected_sources_count": len(self.rejected_sources),
            "consensus_odds": self.consensus_odds,
            "consensus_implied_prob": self.consensus_implied_prob,
            "fair_probability": self.fair_probability,
            "fair_odds": self.fair_odds,
            "margin_factor_applied": self.margin_factor_applied,
            "de_margin_method": self.de_margin_method,
            "is_valid": self.is_valid,
            "diagnostic": self.diagnostic,
            "calculated_at": self.calculated_at,
        }


@dataclass
class BookmakerValueEvaluation:
    """Financial evaluation result for a single execution bookmaker (Superbet, Betclic)."""
    bookmaker: str
    raw_odds: Optional[float]
    effective_net_odds: Optional[float]
    tax_rate: float
    is_tax_applied: bool
    gross_ev: Optional[float]
    gross_ev_pct: Optional[float]
    net_ev: Optional[float]
    net_ev_pct: Optional[float]
    value_edge_pp: Optional[float]
    is_positive_edge: bool
    is_qualified: bool
    reason_code: str
    reason: Optional[str] = None
    selection_id: Optional[str] = None
    market_id: Optional[str] = None
    event_id: Optional[str] = None


    def to_dict(self) -> Dict[str, Any]:
        return {
            "bookmaker": self.bookmaker,
            "raw_odds": self.raw_odds,
            "effective_net_odds": self.effective_net_odds,
            "tax_rate": self.tax_rate,
            "is_tax_applied": self.is_tax_applied,
            "gross_ev": self.gross_ev,
            "gross_ev_pct": self.gross_ev_pct,
            "net_ev": self.net_ev,
            "net_ev_pct": self.net_ev_pct,
            "value_edge_pp": self.value_edge_pp,
            "is_positive_edge": self.is_positive_edge,
            "is_qualified": self.is_qualified,
            "reason_code": self.reason_code,
            "reason": self.reason,
            "selection_id": self.selection_id,
            "market_id": self.market_id,
            "event_id": self.event_id,
        }


@dataclass
class PropsValueEvaluationResult:
    """Comprehensive candidate value evaluation with complete provenance and metrics."""
    canonical_prop_key: str
    stat_type: str
    line: float
    side: str
    period: str
    reference_calculation: ReferenceOddsCalculation
    bookmaker_evaluations: Dict[str, BookmakerValueEvaluation] = field(default_factory=dict)
    best_bookmaker: Optional[str] = None
    best_raw_odds: Optional[float] = None
    best_effective_odds: Optional[float] = None
    best_net_ev_pct: Optional[float] = None
    best_gross_ev_pct: Optional[float] = None
    overall_status: str = "REJECTED"  # "QUALIFIED", "POSITIVE_EDGE", "EVALUATED", "REFERENCE_VALID", "REJECTED"
    primary_reason_code: str = ValueEvaluationReasonCode.INSUFFICIENT_REFERENCE_SOURCES.value
    primary_reason: Optional[str] = None
    is_valuebet: bool = False
    provenance: Dict[str, Any] = field(default_factory=dict)
    evaluated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "canonical_prop_key": self.canonical_prop_key,
            "stat_type": self.stat_type,
            "line": self.line,
            "side": self.side,
            "period": self.period,
            "reference_calculation": self.reference_calculation.to_dict(),
            "bookmaker_evaluations": {k: v.to_dict() for k, v in self.bookmaker_evaluations.items()},
            "best_bookmaker": self.best_bookmaker,
            "best_raw_odds": self.best_raw_odds,
            "best_effective_odds": self.best_effective_odds,
            "best_net_ev_pct": self.best_net_ev_pct,
            "best_gross_ev_pct": self.best_gross_ev_pct,
            "overall_status": self.overall_status,
            "primary_reason_code": self.primary_reason_code,
            "primary_reason": self.primary_reason,
            "is_valuebet": self.is_valuebet,
            "provenance": self.provenance,
            "evaluated_at": self.evaluated_at,
        }


class PropsValueEvaluator:
    """Unified Value Evaluation Engine for Player Props and Team Props."""

    def __init__(
        self,
        config: Optional[PropsEvaluationConfig] = None,
        tax_engine: Optional[TaxEngine] = None,
    ) -> None:
        self.config = config or PropsEvaluationConfig()
        self.tax_engine = tax_engine or get_tax_engine()

    def calculate_reference_odds(
        self,
        reference_odds_list: Sequence[Dict[str, Any]],
        target_line: float,
        target_side: str = "OVER",
        current_time: Optional[datetime] = None,
    ) -> ReferenceOddsCalculation:
        """Aggregates and calculates fair probability from foreign bookmaker reference quotes.

        Deterministic Algorithm:
        1. Sanitize & Filter:
           - Check exact line match (|line - target_line| < 0.01)
           - Check exact side match (side.upper() == target_side.upper())
           - Check odds value sanity (1.0 < odds <= 50.0)
           - Check timestamp freshness (age <= max_freshness_seconds if timestamp exists)
        2. Harmonic Consensus:
           - p_i = 1 / odds_i
           - consensus_p_implied = mean(p_i)
           - consensus_odds = 1 / consensus_p_implied
        3. De-margin:
           - P_fair = consensus_p_implied / default_prop_margin_factor
           - fair_odds = 1 / P_fair
        """
        now = current_time or datetime.now(timezone.utc)
        target_side_norm = str(target_side or "OVER").upper()

        used_sources: List[Dict[str, Any]] = []
        rejected_sources: List[Dict[str, Any]] = []

        if not reference_odds_list:
            return ReferenceOddsCalculation(
                is_valid=False,
                diagnostic=ValueEvaluationReasonCode.INSUFFICIENT_REFERENCE_SOURCES.value,
                used_sources=[],
                rejected_sources=[],
            )

        for ro in reference_odds_list:
            bm_name = str(ro.get("bookmaker") or "Reference")
            raw_line = ro.get("line")
            raw_side = str(ro.get("side") or "OVER").upper()
            raw_odds = ro.get("decimal_odds") if "decimal_odds" in ro else ro.get("odds")
            ts_str = ro.get("timestamp") or ro.get("captured_at") or (ro.get("metadata", {}).get("timestamp") if isinstance(ro.get("metadata"), dict) else None)

            # 1. Line Check
            try:
                line_val = float(raw_line) if raw_line is not None else 0.0
            except (ValueError, TypeError):
                rejected_sources.append({
                    "bookmaker": bm_name,
                    "odds": raw_odds,
                    "line": raw_line,
                    "side": raw_side,
                    "reason_code": ValueEvaluationReasonCode.REFERENCE_LINE_MISMATCH.value,
                    "reason": f"Invalid line representation '{raw_line}'",
                })
                continue

            if abs(line_val - target_line) >= 0.01:
                rejected_sources.append({
                    "bookmaker": bm_name,
                    "odds": raw_odds,
                    "line": line_val,
                    "side": raw_side,
                    "reason_code": ValueEvaluationReasonCode.REFERENCE_LINE_MISMATCH.value,
                    "reason": f"Line {line_val} differs from target line {target_line}",
                })
                continue

            # 2. Side Check
            if raw_side != target_side_norm:
                rejected_sources.append({
                    "bookmaker": bm_name,
                    "odds": raw_odds,
                    "line": line_val,
                    "side": raw_side,
                    "reason_code": ValueEvaluationReasonCode.REFERENCE_SELECTION_MISMATCH.value,
                    "reason": f"Side '{raw_side}' differs from target side '{target_side_norm}'",
                })
                continue

            # 3. Odds Value Check
            try:
                odds_val = float(raw_odds) if raw_odds is not None else 0.0
            except (ValueError, TypeError):
                rejected_sources.append({
                    "bookmaker": bm_name,
                    "odds": raw_odds,
                    "line": line_val,
                    "side": raw_side,
                    "reason_code": ValueEvaluationReasonCode.INVALID_REFERENCE_ODDS.value,
                    "reason": f"Non-numeric odds '{raw_odds}'",
                })
                continue

            if odds_val <= 1.0 or odds_val > 50.0:
                rejected_sources.append({
                    "bookmaker": bm_name,
                    "odds": odds_val,
                    "line": line_val,
                    "side": raw_side,
                    "reason_code": ValueEvaluationReasonCode.INVALID_REFERENCE_ODDS.value,
                    "reason": f"Odds {odds_val} outside acceptable range (1.0 < odds <= 50.0)",
                })
                continue

            # 4. Freshness Check
            if ts_str:
                try:
                    ts_dt = datetime.fromisoformat(str(ts_str).replace("Z", "+00:00"))
                    age_seconds = (now - ts_dt).total_seconds()
                    if age_seconds > self.config.max_freshness_seconds:
                        rejected_sources.append({
                            "bookmaker": bm_name,
                            "odds": odds_val,
                            "line": line_val,
                            "side": raw_side,
                            "timestamp": ts_str,
                            "age_seconds": age_seconds,
                            "reason_code": ValueEvaluationReasonCode.STALE_REFERENCE_DATA.value,
                            "reason": f"Reference odds stale ({age_seconds:.0f}s old > max {self.config.max_freshness_seconds}s)",
                        })
                        continue
                except Exception:
                    pass  # Non-parseable timestamp: do not aggressively reject if string format differs

            # Valid source entry
            implied_p = 1.0 / odds_val
            used_sources.append({
                "bookmaker": bm_name,
                "odds": odds_val,
                "line": line_val,
                "side": raw_side,
                "implied_probability": round(implied_p, 4),
                "timestamp": ts_str,
            })

        # 5. Check Source Count Sufficiency
        if len(used_sources) < self.config.min_reference_sources:
            return ReferenceOddsCalculation(
                is_valid=False,
                diagnostic=ValueEvaluationReasonCode.INSUFFICIENT_REFERENCE_SOURCES.value,
                used_sources=used_sources,
                rejected_sources=rejected_sources,
            )

        # 6. Harmonic Consensus Calculation
        probs = [s["implied_probability"] for s in used_sources]
        mean_implied_p = sum(probs) / len(probs)
        raw_consensus_odds = round(1.0 / mean_implied_p, 4) if mean_implied_p > 0 else None

        # 7. De-margin Calculation
        margin_factor = float(self.config.default_prop_margin_factor)
        fair_p = mean_implied_p / margin_factor
        # Guard probability range (0.01 <= fair_p <= 0.99)
        fair_p = max(0.01, min(0.99, fair_p))
        fair_odds = round(1.0 / fair_p, 4)

        return ReferenceOddsCalculation(
            used_sources=used_sources,
            rejected_sources=rejected_sources,
            consensus_odds=raw_consensus_odds,
            consensus_implied_prob=round(mean_implied_p, 4),
            fair_probability=round(fair_p, 4),
            fair_odds=fair_odds,
            margin_factor_applied=margin_factor,
            de_margin_method="HARMONIC_CONSENSUS_PROP_MARGIN",
            is_valid=True,
            diagnostic=None,
        )

    def evaluate_matched_prop(
        self,
        canonical_key: str,
        stat_type: str,
        line: float,
        side: str,
        reference_odds_list: Sequence[Dict[str, Any]],
        execution_odds: Dict[str, Any],
        period: str = "FULL_TIME",
        provenance_base: Optional[Dict[str, Any]] = None,
        current_time: Optional[datetime] = None,
    ) -> PropsValueEvaluationResult:
        """Evaluates a matched prop candidate through the complete value pipeline."""
        now = current_time or datetime.now(timezone.utc)
        target_line = float(line)
        target_side = str(side or "OVER").upper()

        # Step 1: Reference Odds Calculation
        ref_calc = self.calculate_reference_odds(
            reference_odds_list=reference_odds_list,
            target_line=target_line,
            target_side=target_side,
            current_time=now,
        )

        if not ref_calc.is_valid:
            diag_code = ref_calc.diagnostic or ValueEvaluationReasonCode.INSUFFICIENT_REFERENCE_SOURCES.value
            return PropsValueEvaluationResult(
                canonical_prop_key=canonical_key,
                stat_type=stat_type,
                line=target_line,
                side=target_side,
                period=period,
                reference_calculation=ref_calc,
                bookmaker_evaluations={},
                overall_status="REJECTED",
                primary_reason_code=diag_code,
                primary_reason=f"Reference odds invalid or insufficient: {diag_code}",
                is_valuebet=False,
                provenance=provenance_base or {},
            )

        fair_p = ref_calc.fair_probability
        if fair_p is None or fair_p <= 0.0 or fair_p >= 1.0:
            return PropsValueEvaluationResult(
                canonical_prop_key=canonical_key,
                stat_type=stat_type,
                line=target_line,
                side=target_side,
                period=period,
                reference_calculation=ref_calc,
                bookmaker_evaluations={},
                overall_status="REJECTED",
                primary_reason_code=ValueEvaluationReasonCode.INVALID_REFERENCE_ODDS.value,
                primary_reason="Fair probability outside valid range (0, 1)",
                is_valuebet=False,
                provenance=provenance_base or {},
            )

        # Step 2: Evaluate Each Polish Bookmaker Quote
        bm_evaluations: Dict[str, BookmakerValueEvaluation] = {}
        best_bm: Optional[str] = None
        best_raw_odds: Optional[float] = None
        best_effective_odds: Optional[float] = None
        best_net_ev_pct: Optional[float] = None
        best_gross_ev_pct: Optional[float] = None
        has_positive_edge = False
        has_qualified = False
        primary_reason_code = ValueEvaluationReasonCode.BELOW_VALUE_THRESHOLD.value
        primary_reason: Optional[str] = None

        min_val_pct = float(self.config.min_value_percent)
        max_sanity_cap = float(self.config.max_value_sanity_cap)

        for bm_name, quote_obj in execution_odds.items():
            # Extract quote fields whether quote_obj is a dataclass or dict
            if isinstance(quote_obj, dict):
                status = quote_obj.get("status")
                raw_odds_val = quote_obj.get("decimal_odds")
                sel_id = quote_obj.get("selection_id") or quote_obj.get("selection_name")
                mkt_id = quote_obj.get("market_id")
                ev_id = quote_obj.get("event_id")
                rej_reason = quote_obj.get("reason")
            else:
                status = getattr(quote_obj, "status", "UNAVAILABLE")
                raw_odds_val = getattr(quote_obj, "decimal_odds", None)
                sel_id = getattr(quote_obj, "selection_id", None) or getattr(quote_obj, "selection_name", None)
                mkt_id = getattr(quote_obj, "market_id", None)
                ev_id = getattr(quote_obj, "event_id", None)
                rej_reason = getattr(quote_obj, "reason", None)

            if status != "AVAILABLE" or raw_odds_val is None or float(raw_odds_val) <= 1.0:
                bm_evaluations[bm_name] = BookmakerValueEvaluation(
                    bookmaker=bm_name,
                    raw_odds=float(raw_odds_val) if raw_odds_val and float(raw_odds_val) > 1.0 else None,
                    effective_net_odds=None,
                    tax_rate=0.0,
                    is_tax_applied=False,
                    gross_ev=None,
                    gross_ev_pct=None,
                    net_ev=None,
                    net_ev_pct=None,
                    value_edge_pp=None,
                    is_positive_edge=False,
                    is_qualified=False,
                    reason_code=ValueEvaluationReasonCode.POLISH_ODDS_UNAVAILABLE.value if status != "NO_ODDS" else ValueEvaluationReasonCode.ODDS_INACTIVE.value,
                    reason=rej_reason or f"No active odds at {bm_name}",
                    selection_id=sel_id,
                    market_id=mkt_id,
                    event_id=ev_id,
                )
                continue

            raw_odds_f = float(raw_odds_val)

            # Bookmaker Odds Bounds Check
            if raw_odds_f < float(self.config.min_bookmaker_odds) or raw_odds_f > float(self.config.max_bookmaker_odds):
                bm_evaluations[bm_name] = BookmakerValueEvaluation(
                    bookmaker=bm_name,
                    raw_odds=raw_odds_f,
                    effective_net_odds=raw_odds_f,
                    tax_rate=0.0,
                    is_tax_applied=False,
                    gross_ev=0.0,
                    gross_ev_pct=0.0,
                    net_ev=0.0,
                    net_ev_pct=0.0,
                    value_edge_pp=0.0,
                    is_positive_edge=False,
                    is_qualified=False,
                    reason_code=ValueEvaluationReasonCode.INVALID_POLISH_ODDS.value,
                    reason=f"{bm_name} odds ({raw_odds_f}) outside bounds [{self.config.min_bookmaker_odds}, {self.config.max_bookmaker_odds}]",
                    selection_id=sel_id,
                    market_id=mkt_id,
                    event_id=ev_id,
                )
                continue

            # Centralized Tax Engine Net Payout & EV
            tax_res = self.tax_engine.calculate_ev(
                raw_odds=raw_odds_f,
                fair_probability=fair_p,
                bookmaker=bm_name,
            )

            eff_odds_f = float(tax_res["effective_net_odds"])
            gross_ev_f = float(tax_res["gross_ev_edge"])
            gross_ev_pct_f = round(float(tax_res["gross_ev_percent"]), 2)
            net_ev_f = float(tax_res["net_ev_edge"])
            # P2: threshold gating uses the unrounded net value; rounding is
            # display-only (2.995% must not become 3.00% and qualify).
            net_ev_pct_raw = float(tax_res["net_ev_percent"])
            net_ev_pct_f = round(net_ev_pct_raw, 2)
            is_tax_applied = bool(tax_res["is_tax_applied"])
            cfg_tax_rate = float(self.tax_engine.get_config(bm_name).tax_rate) if is_tax_applied else 0.0

            # Edge in percentage points: P_fair * 100 - (1 / eff_odds) * 100
            implied_eff_pct = (1.0 / eff_odds_f) * 100.0 if eff_odds_f > 0 else 100.0
            val_edge_pp = round((fair_p * 100.0) - implied_eff_pct, 2)

            is_pos_edge = net_ev_f > 0.0
            bm_reason_code = ValueEvaluationReasonCode.BELOW_VALUE_THRESHOLD.value
            bm_reason = None
            is_qual = False

            if is_pos_edge:
                has_positive_edge = True
                if net_ev_pct_raw > max_sanity_cap:
                    bm_reason_code = ValueEvaluationReasonCode.EXCESSIVE_VALUE_SANITY_CAP.value
                    bm_reason = f"Net EV (+{net_ev_pct_f}%) exceeds palpable error sanity cap ({max_sanity_cap}%)"
                elif net_ev_pct_raw >= min_val_pct:
                    is_qual = True
                    has_qualified = True
                    bm_reason_code = ValueEvaluationReasonCode.QUALIFIED.value
                    bm_reason = f"Qualified valuebet: +{net_ev_pct_f}% Net EV (Gross EV: +{gross_ev_pct_f}%)"
                else:
                    bm_reason_code = ValueEvaluationReasonCode.BELOW_VALUE_THRESHOLD.value
                    bm_reason = f"Positive Net EV (+{net_ev_pct_f}%) is below minimum qualification threshold ({min_val_pct}%)"
            else:
                bm_reason_code = ValueEvaluationReasonCode.BELOW_VALUE_THRESHOLD.value
                bm_reason = f"Negative Net EV ({net_ev_pct_f:+.1f}%) after tax factor"

            bm_evaluations[bm_name] = BookmakerValueEvaluation(
                bookmaker=bm_name,
                raw_odds=raw_odds_f,
                effective_net_odds=eff_odds_f,
                tax_rate=cfg_tax_rate,
                is_tax_applied=is_tax_applied,
                gross_ev=gross_ev_f,
                gross_ev_pct=gross_ev_pct_f,
                net_ev=net_ev_f,
                net_ev_pct=net_ev_pct_f,
                value_edge_pp=val_edge_pp,
                is_positive_edge=is_pos_edge,
                is_qualified=is_qual,
                reason_code=bm_reason_code,
                reason=bm_reason,
                selection_id=sel_id,
                market_id=mkt_id,
                event_id=ev_id,
            )

            # Track best execution option across Polish bookmakers
            if best_net_ev_pct is None or net_ev_pct_f > best_net_ev_pct:
                best_net_ev_pct = net_ev_pct_f
                best_gross_ev_pct = gross_ev_pct_f
                best_bm = bm_name
                best_raw_odds = raw_odds_f
                best_effective_odds = eff_odds_f
                primary_reason_code = bm_reason_code
                primary_reason = bm_reason

        # Step 3: Determine Overall Status
        if not bm_evaluations or all((b.effective_net_odds is None or b.effective_net_odds <= 0.0) for b in bm_evaluations.values()):
            overall_status = "REFERENCE_VALID"
            primary_reason_code = ValueEvaluationReasonCode.POLISH_ODDS_UNAVAILABLE.value
            primary_reason = "Reference benchmark established, but Polish execution odds currently unavailable"
        elif has_qualified:
            overall_status = "QUALIFIED"
            primary_reason_code = ValueEvaluationReasonCode.QUALIFIED.value
        elif has_positive_edge:
            overall_status = "POSITIVE_EDGE"
        else:
            overall_status = "EVALUATED"

        # Combine Provenance
        prov_dict = dict(provenance_base or {})
        prov_dict.update({
            "reference_sources_count": len(ref_calc.used_sources),
            "reference_consensus_odds": ref_calc.consensus_odds,
            "reference_fair_probability": ref_calc.fair_probability,
            "reference_fair_odds": ref_calc.fair_odds,
            "best_executable_bookmaker": best_bm,
            "best_executable_raw_odds": best_raw_odds,
            "best_executable_net_odds": best_effective_odds,
            "best_net_ev_pct": best_net_ev_pct,
            "is_valuebet": has_qualified,
            "value_reason_code": primary_reason_code,
        })

        return PropsValueEvaluationResult(
            canonical_prop_key=canonical_key,
            stat_type=stat_type,
            line=target_line,
            side=target_side,
            period=period,
            reference_calculation=ref_calc,
            bookmaker_evaluations=bm_evaluations,
            best_bookmaker=best_bm,
            best_raw_odds=best_raw_odds,
            best_effective_odds=best_effective_odds,
            best_net_ev_pct=best_net_ev_pct,
            best_gross_ev_pct=best_gross_ev_pct,
            overall_status=overall_status,
            primary_reason_code=primary_reason_code,
            primary_reason=primary_reason,
            is_valuebet=has_qualified,
            provenance=prov_dict,
            evaluated_at=now.isoformat(),
        )

    def evaluate_player_prop(
        self,
        statshub_prop: Any,
        odds_comparison: Any,
        current_time: Optional[datetime] = None,
    ) -> PropsValueEvaluationResult:
        """High-level evaluation method taking StatsHubPropResult and PropOddsComparison."""
        ps = getattr(statshub_prop, "player_stat", None)
        if not ps:
            raise ValueError("Invalid StatsHubPropResult: missing player_stat")

        target_line = float(ps.line) if ps.line is not None else 0.5
        target_side = (ps.odds_type or "OVER").upper()
        canon_key = getattr(odds_comparison, "canonical_prop_key", "")
        ref_list = getattr(odds_comparison, "reference_odds_list", []) or [
            {
                "bookmaker": o.bookmaker,
                "line": float(o.line),
                "side": o.side.upper(),
                "decimal_odds": float(o.decimal_odds),
                "timestamp": getattr(o, "metadata", {}).get("timestamp") if isinstance(getattr(o, "metadata", None), dict) else None,
            }
            for o in (ps.bookmaker_odds or [])
        ]

        exec_odds_dict = getattr(odds_comparison, "execution_odds", {})
        prov = getattr(odds_comparison, "provenance", {})

        return self.evaluate_matched_prop(
            canonical_key=canon_key,
            stat_type=ps.stat_type.upper(),
            line=target_line,
            side=target_side,
            reference_odds_list=ref_list,
            execution_odds=exec_odds_dict,
            period="FULL_TIME",
            provenance_base=prov,
            current_time=current_time,
        )

    def evaluate_team_prop(
        self,
        statshub_team_prop: Any,
        odds_comparison: Any,
        current_time: Optional[datetime] = None,
    ) -> PropsValueEvaluationResult:
        """High-level evaluation method taking StatsHubTeamPropResult and TeamPropOddsComparison."""
        ts = getattr(statshub_team_prop, "team_stat", None)
        if not ts:
            raise ValueError("Invalid StatsHubTeamPropResult: missing team_stat")

        target_line = float(ts.line) if ts.line is not None else 0.5
        target_side = (ts.odds_type or "OVER").upper()
        canon_key = getattr(odds_comparison, "canonical_team_prop_key", "")
        ref_list = getattr(odds_comparison, "reference_odds_list", []) or [
            {
                "bookmaker": o.bookmaker,
                "line": float(o.line),
                "side": o.side.upper(),
                "decimal_odds": float(o.decimal_odds),
                "timestamp": getattr(o, "metadata", {}).get("timestamp") if isinstance(getattr(o, "metadata", None), dict) else None,
            }
            for o in (ts.bookmaker_odds or [])
        ]

        exec_odds_dict = getattr(odds_comparison, "execution_odds", {})
        prov = getattr(odds_comparison, "provenance", {})

        return self.evaluate_matched_prop(
            canonical_key=canon_key,
            stat_type=ts.stat_type.upper(),
            line=target_line,
            side=target_side,
            reference_odds_list=ref_list,
            execution_odds=exec_odds_dict,
            period="FULL_TIME",
            provenance_base=prov,
            current_time=current_time,
        )
