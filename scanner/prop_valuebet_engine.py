"""
Stage 29 — Generic Value Bet Engine for Player & Team Props

Core Value Bet Calculations:
- fair_odds = 1 / P_model
- EV = P_model * execution_odds - 1
- value_edge_pp = P_model * 100 - (1 / execution_odds) * 100

Invariants:
- exact-line matching is mandatory everywhere
- no fallback to another line
- missing execution odds -> cannot be a valuebet
- REFERENCE_ONLY can never be marked as BETTABLE or VALUEBET
- distinct status: REFERENCE_ONLY / BETTABLE / VALUEBET
- extensible architecture for both Player Props and Team Props
"""

from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class ValueBetResult:
    """Deterministic result of value bet analysis."""
    p_model: float
    fair_odds: Optional[float]
    execution_odds: Optional[float]
    reference_odds: Optional[float]
    ev: Optional[float]
    ev_pct: Optional[float]
    value_edge_pp: Optional[float]
    is_valuebet: bool
    status: str  # "VALUEBET", "BETTABLE", "REFERENCE_ONLY", "NO_EXECUTION_MARKET", "NO_EXECUTION_ODDS", "MATCH_UNCERTAIN"
    rejection_reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "p_model": self.p_model,
            "model_probability": self.p_model,
            "model_probability_pct": round(self.p_model * 100.0, 1) if self.p_model is not None else None,
            "fair_odds": self.fair_odds,
            "execution_odds": self.execution_odds,
            "reference_odds": self.reference_odds,
            "ev": self.ev,
            "ev_pct": self.ev_pct,
            "value_edge_pp": self.value_edge_pp,
            "is_valuebet": self.is_valuebet,
            "status": self.status,
            "rejection_reason": self.rejection_reason,
        }


class GenericValueBetEngine:
    """Deterministic, unified Value Bet calculation engine for Player Props and Team Props."""

    def __init__(self, min_ev_threshold: float = 0.0) -> None:
        self.min_ev_threshold = min_ev_threshold

    @staticmethod
    def calculate_fair_odds(p_model: float) -> Optional[float]:
        """Calculates fair decimal odds = 1 / P_model."""
        if p_model is not None and isinstance(p_model, (int, float)) and 0.0 < float(p_model) <= 1.0:
            return round(1.0 / float(p_model), 4)
        return None

    @staticmethod
    def calculate_ev(p_model: float, execution_odds: Optional[float]) -> Optional[float]:
        """Calculates EV = P_model * execution_odds - 1."""
        if (
            execution_odds is not None
            and isinstance(execution_odds, (int, float))
            and float(execution_odds) > 1.0
            and p_model is not None
            and 0.0 <= float(p_model) <= 1.0
        ):
            return round((float(p_model) * float(execution_odds)) - 1.0, 4)
        return None

    @staticmethod
    def calculate_value_edge_pp(p_model: float, execution_odds: Optional[float]) -> Optional[float]:
        """Calculates value edge in percentage points = P_model * 100 - (1 / execution_odds) * 100."""
        if (
            execution_odds is not None
            and isinstance(execution_odds, (int, float))
            and float(execution_odds) > 1.0
            and p_model is not None
            and 0.0 <= float(p_model) <= 1.0
        ):
            implied_pct = (1.0 / float(execution_odds)) * 100.0
            model_pct = float(p_model) * 100.0
            return round(model_pct - implied_pct, 2)
        return None

    def evaluate_candidate(
        self,
        p_model: float,
        execution_odds: Optional[float] = None,
        reference_odds: Optional[float] = None,
        execution_status: str = "REFERENCE_ONLY",
        min_ev_threshold: Optional[float] = None,
    ) -> ValueBetResult:
        """Evaluates a proposition candidate for positive expected value (Valuebet).

        Strict Invariants:
        1. Brak execution odds -> cannot be a valuebet (is_valuebet=False).
        2. REFERENCE_ONLY / NO_EXECUTION_MARKET / MATCH_UNCERTAIN -> never BETTABLE or VALUEBET.
        3. Only verified execution odds on Superbet / Betclic can yield BETTABLE or VALUEBET.
        4. Positive EV (>= min_ev_threshold) -> VALUEBET.
        5. Non-positive EV (< min_ev_threshold) -> BETTABLE (if execution odds available).
        """
        clamped_p = max(0.0, min(1.0, float(p_model))) if p_model is not None else 0.0
        fair_odds = self.calculate_fair_odds(clamped_p)

        effective_threshold = (
            float(min_ev_threshold) if min_ev_threshold is not None else self.min_ev_threshold
        )

        has_valid_exec_odds = (
            execution_odds is not None
            and isinstance(execution_odds, (int, float))
            and float(execution_odds) > 1.0
        )

        is_bettable_status = execution_status in ("BETTABLE", "AVAILABLE", "VALUEBET")

        # INVARIANT 1 & 2: No execution odds or non-bettable execution status
        if not has_valid_exec_odds or not is_bettable_status:
            # Determine correct un-actionable status
            if execution_status in ("MATCH_UNCERTAIN", "NO_EXECUTION_ODDS", "NO_EXECUTION_MARKET"):
                final_status = execution_status
            elif reference_odds is not None and float(reference_odds) > 1.0:
                final_status = "REFERENCE_ONLY"
            else:
                final_status = "NO_EXECUTION_MARKET"

            rejection = (
                "Missing execution odds"
                if not has_valid_exec_odds
                else f"Execution status '{execution_status}' is not bettable"
            )

            return ValueBetResult(
                p_model=clamped_p,
                fair_odds=fair_odds,
                execution_odds=None,
                reference_odds=float(reference_odds) if reference_odds is not None and float(reference_odds) > 1.0 else None,
                ev=None,
                ev_pct=None,
                value_edge_pp=None,
                is_valuebet=False,
                status=final_status,
                rejection_reason=rejection,
            )

        # Has valid execution odds and bettable status
        exec_odds_val = float(execution_odds)
        ref_odds_val = float(reference_odds) if reference_odds is not None and float(reference_odds) > 1.0 else None
        ev = self.calculate_ev(clamped_p, exec_odds_val)
        ev_pct = round(ev * 100.0, 2) if ev is not None else None
        val_edge_pp = self.calculate_value_edge_pp(clamped_p, exec_odds_val)

        # Check positive EV qualification
        # Threshold comparison is on EV percentage (e.g. 0.0% or 2.0%)
        is_val = bool(ev is not None and ev > 0.0 and (ev_pct is not None and ev_pct >= effective_threshold))

        final_status = "VALUEBET" if is_val else "BETTABLE"
        rejection = None if is_val else f"Expected value ({ev_pct:+.1f}%) is below minimum threshold ({effective_threshold:+.1f}%)"

        return ValueBetResult(
            p_model=clamped_p,
            fair_odds=fair_odds,
            execution_odds=exec_odds_val,
            reference_odds=ref_odds_val,
            ev=ev,
            ev_pct=ev_pct,
            value_edge_pp=val_edge_pp,
            is_valuebet=is_val,
            status=final_status,
            rejection_reason=rejection,
        )
