"""
Team Prop Opportunity Ranking & Decision Engine (Stage 30 + Stage 29 Value Bet Engine)

Provides explainable, deterministic scoring, classification, actionability, and reasoning
for team proposition betting opportunities without black-box models.
Strictly distinguishes Statistical Edge (historical vs reference odds) from Execution Edge & EV
(historical/model probability vs real Polish bookmaker prices).

Stage 30 Team Props Value Bet Engine Invariants:
- fair_odds = 1 / P_model
- EV = P_model * execution_odds - 1
- value_edge_pp = P_model * 100 - (1 / execution_odds) * 100
- Distinct actionable status: REFERENCE_ONLY / BETTABLE / VALUEBET
- Missing execution odds -> cannot be VALUEBET or BETTABLE
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class TeamPropOpportunityEvaluation:
    """Evaluation result from TeamPropOpportunityEngine."""
    score: float  # 0.0 to 100.0
    classification: str  # "OPPORTUNITY", "SHORTLIST", "STANDARD", "SPECULATIVE"
    actionability: str  # "BETTABLE", "NO_EXECUTION_MARKET", "NO_EXECUTION_ODDS", "MATCH_UNCERTAIN", "REFERENCE_ONLY"
    historical_probability: float  # 0.0 to 1.0 (hits / sample_size)
    reference_market_probability: Optional[float]  # 1 / reference_odds
    market_probability: Optional[float]  # Alias for reference_market_probability
    raw_edge: Optional[float]  # historical_probability - reference_market_probability
    raw_edge_pct: Optional[float]  # raw_edge in percentage points (+65.7pp)
    execution_market_probability: Optional[float] = None  # 1 / execution_odds
    execution_edge: Optional[float] = None  # historical_probability - execution_market_probability
    execution_edge_pct: Optional[float] = None  # execution_edge in percentage points
    reference_ev: Optional[float] = None  # (historical_probability * reference_odds) - 1
    reference_ev_pct: Optional[float] = None  # reference_ev * 100.0
    execution_ev: Optional[float] = None  # (historical_probability * execution_odds) - 1
    execution_ev_pct: Optional[float] = None  # execution_ev * 100.0
    # Value Bet Engine Fields
    status: str = ""  # Distinct status: "VALUEBET", "BETTABLE", "REFERENCE_ONLY", etc.
    model_probability: float = 0.0  # Alias for historical_probability
    fair_odds: Optional[float] = None  # 1 / P_model
    value_edge_pp: Optional[float] = None  # P_model * 100 - (1 / execution_odds) * 100
    is_valuebet: bool = False  # True iff positive EV on verified Polish execution odds
    reasons: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    data_quality_flags: List[str] = field(default_factory=list)
    score_breakdown: Dict[str, float] = field(default_factory=dict)
    edge_type: str = "STATISTICAL_EDGE"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "score": self.score,
            "classification": self.classification,
            "actionability": self.actionability,
            "status": self.status or ("VALUEBET" if self.is_valuebet else self.actionability),
            "historical_probability": self.historical_probability,
            "model_probability": self.model_probability or self.historical_probability,
            "model_probability_pct": round((self.model_probability or self.historical_probability) * 100.0, 1),
            "fair_odds": self.fair_odds,
            "reference_market_probability": self.reference_market_probability,
            "reference_probability": self.reference_market_probability,
            "market_probability": self.market_probability,
            "raw_edge": self.raw_edge,
            "raw_edge_pct": self.raw_edge_pct,
            "execution_market_probability": self.execution_market_probability,
            "execution_probability": self.execution_market_probability,
            "execution_edge": self.execution_edge,
            "execution_edge_pct": self.execution_edge_pct,
            "value_edge_pp": self.value_edge_pp,
            "reference_ev": self.reference_ev,
            "reference_ev_pct": self.reference_ev_pct,
            "execution_ev": self.execution_ev,
            "execution_ev_pct": self.execution_ev_pct,
            "is_valuebet": self.is_valuebet,
            "reasons": self.reasons,
            "warnings": self.warnings,
            "data_quality_flags": self.data_quality_flags,
            "score_breakdown": self.score_breakdown,
            "edge_type": self.edge_type,
        }


class TeamPropOpportunityEngine:
    """Deterministic Team Prop Opportunity Engine, Value Bet Engine, and Decision Layer."""

    OPPORTUNITY_SCORE_THRESHOLD: float = 75.0
    SHORTLIST_SCORE_THRESHOLD: float = 60.0
    MIN_SAMPLE_FOR_OPPORTUNITY: int = 5

    def __init__(self, min_ev_threshold: float = 0.0) -> None:
        self.min_ev_threshold = min_ev_threshold

    @staticmethod
    def calculate_fair_odds(p_model: float) -> Optional[float]:
        """Calculates fair decimal odds = 1 / P_model."""
        if p_model is not None and isinstance(p_model, (int, float)) and 0.0 < float(p_model) <= 1.0:
            return round(1.0 / float(p_model), 4)
        return None

    @staticmethod
    def calculate_implied_probability(odds: Optional[float]) -> Optional[float]:
        """Calculates implied probability P = 1 / odds for odds > 1.0."""
        if odds is not None and isinstance(odds, (int, float)) and odds > 1.0:
            return round(1.0 / float(odds), 4)
        return None

    @staticmethod
    def calculate_statistical_edge(historical_prob: float, implied_prob: Optional[float]) -> Tuple[Optional[float], Optional[float]]:
        """Calculates statistical edge (P_hist - P_implied) in probability and percentage points."""
        if implied_prob is not None and 0.0 <= historical_prob <= 1.0 and 0.0 < implied_prob <= 1.0:
            edge = round(historical_prob - implied_prob, 4)
            edge_pct = round(edge * 100.0, 1)
            return edge, edge_pct
        return None, None

    @staticmethod
    def calculate_expected_value(historical_prob: float, odds: Optional[float]) -> Tuple[Optional[float], Optional[float]]:
        """Calculates expected value EV = (P_hist * odds) - 1 for decimal odds."""
        if odds is not None and isinstance(odds, (int, float)) and odds > 1.0 and 0.0 <= historical_prob <= 1.0:
            ev = round((historical_prob * float(odds)) - 1.0, 4)
            ev_pct = round(ev * 100.0, 1)
            return ev, ev_pct
        return None, None

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

    def evaluate(
        self,
        hit_rate_pct: float,
        sample_size: int,
        stat_average: float,
        line: float,
        side: str = "OVER",
        best_odds: Optional[float] = None,
        best_bookmaker: Optional[str] = None,
        best_execution_odds: Optional[float] = None,
        best_execution_bookmaker: Optional[str] = None,
        execution_status: str = "REFERENCE_ONLY",
        last_5_avg: Optional[float] = None,
        last_10_avg: Optional[float] = None,
        hit_rate_count: Optional[int] = None,
        stat_type: str = "SHOTS",
        participant_role: str = "HOME",
        min_ev_threshold: Optional[float] = None,
    ) -> TeamPropOpportunityEvaluation:
        """Evaluate, score, and rank a team prop opportunity with Value Bet Engine detection."""
        reasons: List[str] = []
        warnings: List[str] = []
        breakdown: Dict[str, float] = {}

        effective_min_ev = min_ev_threshold if min_ev_threshold is not None else self.min_ev_threshold

        # 1. Historical & Model Probability & Sample Integrity
        has_sample = sample_size is not None and int(sample_size) > 0
        effective_sample = int(sample_size) if has_sample else 0

        hr_clamped = max(0.0, min(100.0, float(hit_rate_pct or 0.0))) if has_sample else 0.0
        historical_probability = round(hr_clamped / 100.0, 4) if has_sample else 0.0
        model_probability = historical_probability
        fair_odds = self.calculate_fair_odds(model_probability) if has_sample else None
        hits = hit_rate_count if hit_rate_count is not None else round(hr_clamped * effective_sample / 100.0)

        if effective_sample > 0:
            reasons.append(f"{hits}/{effective_sample} ({hr_clamped:.0f}%) historical hit rate (Fair Odds: {fair_odds:.2f})" if fair_odds else f"{hits}/{effective_sample} ({hr_clamped:.0f}%) historical hit rate")
        else:
            warnings.append("Missing or zero historical sample. Probability cannot be derived.")

        # 2. Reference Market Implied Probability & Raw Statistical Edge & EV
        ref_market_prob: Optional[float] = None
        raw_edge: Optional[float] = None
        raw_edge_pct: Optional[float] = None
        ref_ev: Optional[float] = None
        ref_ev_pct: Optional[float] = None

        if best_odds and isinstance(best_odds, (int, float)) and best_odds > 1.0:
            ref_market_prob = round(1.0 / float(best_odds), 4)
            if has_sample:
                raw_edge, raw_edge_pct = self.calculate_statistical_edge(historical_probability, ref_market_prob)
                ref_ev, ref_ev_pct = self.calculate_expected_value(historical_probability, best_odds)

            book_label = f" ({best_bookmaker})" if best_bookmaker and best_bookmaker != "N/A" else ""
            reasons.append(f"Best reference odds: {best_odds:.2f}{book_label}")

            if raw_edge is not None:
                if raw_edge > 0:
                    reasons.append(
                        f"Statistical Edge: Historical prob ({historical_probability*100:.1f}%) exceeds reference implied prob ({ref_market_prob*100:.1f}%) by +{raw_edge_pct:.1f}pp"
                    )
                else:
                    warnings.append(
                        f"Implied reference probability ({ref_market_prob*100:.1f}%) exceeds historical hit rate ({historical_probability*100:.1f}%)"
                    )
        else:
            warnings.append("No active reference bookmaker odds currently available for this line")

        # 3. Execution Market Implied Probability & Execution Edge & Value Bet EV
        exec_market_prob: Optional[float] = None
        exec_edge: Optional[float] = None
        exec_edge_pct: Optional[float] = None
        exec_ev: Optional[float] = None
        exec_ev_pct: Optional[float] = None
        value_edge_pp: Optional[float] = None
        is_val = False

        is_bettable_execution = (
            execution_status in ("BETTABLE", "AVAILABLE", "VALUEBET")
            and best_execution_odds is not None
            and isinstance(best_execution_odds, (int, float))
            and best_execution_odds > 1.0
        )

        if is_bettable_execution:
            exec_market_prob = round(1.0 / float(best_execution_odds), 4)
            if has_sample:
                exec_edge, exec_edge_pct = self.calculate_statistical_edge(historical_probability, exec_market_prob)
                exec_ev, exec_ev_pct = self.calculate_expected_value(historical_probability, best_execution_odds)
                value_edge_pp = self.calculate_value_edge_pp(model_probability, best_execution_odds)

                if exec_ev is not None and exec_ev > 0.0 and (exec_ev_pct is not None and exec_ev_pct >= effective_min_ev):
                    is_val = True

            if exec_edge is not None and exec_edge > 0:
                reasons.append(
                    f"Execution Edge: Real Polish bookmaker price yields +{exec_edge_pct:.1f}pp edge (Implied: {exec_market_prob*100:.1f}%)"
                )

            if is_val:
                reasons.append(
                    f"VALUEBET DETECTED: +{exec_ev_pct:.1f}% Expected Value at {best_execution_bookmaker or 'Polish Bookmaker'} (Fair Odds: {fair_odds:.2f}, Exec Odds: {best_execution_odds:.2f})"
                )
            elif exec_edge is not None and exec_edge <= 0:
                warnings.append(
                    f"Execution odds ({best_execution_odds:.2f}) yield non-positive edge ({exec_edge_pct:.1f}pp, EV: {exec_ev_pct:+.1f}%)"
                )
        else:
            if execution_status == "NO_EXECUTION_ODDS":
                warnings.append("Polish bookmaker market mapped, but odds currently inactive/suspended")
            elif execution_status == "MATCH_UNCERTAIN":
                warnings.append("Cross-bookmaker match uncertain — execution price unverified")
            elif execution_status == "NO_EXECUTION_MARKET":
                warnings.append("No Polish execution market found at Superbet / Betclic")
            else:
                warnings.append("No Polish execution odds available at Superbet / Betclic (Reference Only)")

        # 4. Line Clearance & Average Signals
        line_val = float(line)
        stat_name = stat_type.replace("_", " ").capitalize()
        role_label = f" ({participant_role})" if participant_role else ""
        if side.upper() == "OVER":
            if stat_average > line_val:
                diff = stat_average - line_val
                reasons.append(f"Team average {stat_average:.2f} {stat_name}{role_label} exceeds line {line_val:.1f} (+{diff:.2f} margin)")
            elif stat_average > 0:
                diff = line_val - stat_average
                warnings.append(f"Team average {stat_average:.2f} {stat_name}{role_label} is below line {line_val:.1f} (-{diff:.2f})")

            if last_5_avg is not None:
                if last_5_avg > line_val:
                    reasons.append(f"Strong recent team form: Last 5 games avg {last_5_avg:.1f} vs line {line_val:.1f}")
                elif last_5_avg < line_val * 0.7:
                    warnings.append(f"Cold recent team form: Last 5 games avg {last_5_avg:.1f} is below line {line_val:.1f}")

            if last_10_avg is not None and last_10_avg > line_val:
                reasons.append(f"Sustained team form: Last 10 games avg {last_10_avg:.1f} vs line {line_val:.1f}")

        # 5. Sample Size Reliability
        if effective_sample < self.MIN_SAMPLE_FOR_OPPORTUNITY:
            warnings.append(f"Low team sample size ({effective_sample} matches). Statistical reliability is limited.")
        elif effective_sample >= 10:
            reasons.append(f"Reliable team sample size ({effective_sample} matches evaluated)")

        # 6. Core Scoring Math (0.0 to 100.0)
        # Factor A: Hit Rate Score (max 35 pts)
        hit_rate_score = min(35.0, (hr_clamped / 100.0) * 35.0) if has_sample else 0.0
        breakdown["hit_rate_score"] = round(hit_rate_score, 1)

        # Factor B: Edge Score (max 30 pts)
        edge_score = 0.0
        active_edge = exec_edge if exec_edge is not None else raw_edge
        if active_edge is not None:
            if active_edge > 0:
                max_pts = 30.0 if exec_edge is not None else 25.0
                edge_score = min(max_pts, (active_edge / 0.30) * max_pts)
            else:
                edge_score = max(-15.0, (active_edge / 0.20) * 15.0)
        breakdown["edge_score"] = round(edge_score, 1)

        # Factor C: Sample Size Confidence (max 15 pts)
        if effective_sample >= 15:
            sample_score = 15.0
        elif effective_sample >= 10:
            sample_score = 12.0
        elif effective_sample >= 7:
            sample_score = 9.0
        elif effective_sample >= 5:
            sample_score = 6.0
        elif effective_sample >= 3:
            sample_score = 3.0
        elif effective_sample >= 1:
            sample_score = 1.0
        else:
            sample_score = 0.0
        breakdown["sample_score"] = sample_score

        # Factor D: Form & Line Clearance (max 15 pts)
        form_score = 0.0
        if side.upper() == "OVER":
            if stat_average > line_val:
                form_score += 7.5
            if last_5_avg is not None and last_5_avg > line_val:
                form_score += 5.0
            if last_10_avg is not None and last_10_avg > line_val:
                form_score += 2.5
        breakdown["form_score"] = round(form_score, 1)

        # Factor E: Odds Viability (max 5 pts)
        odds_score = 0.0
        eval_odds = (best_execution_odds if is_bettable_execution else None) or best_odds
        if eval_odds and eval_odds >= 1.40:
            odds_score = 5.0 if eval_odds <= 3.50 else 3.0
        elif eval_odds and eval_odds > 1.0:
            odds_score = 2.0
        breakdown["odds_score"] = odds_score

        raw_total = hit_rate_score + edge_score + sample_score + form_score + odds_score
        if effective_sample < self.MIN_SAMPLE_FOR_OPPORTUNITY:
            max_small_sample_score = 45.0 + (effective_sample * 5.0)
            total_score = max(0.0, min(max_small_sample_score, raw_total))
        else:
            total_score = max(0.0, min(100.0, raw_total))
        total_score = round(total_score, 1)

        # 7. Opportunity Classification
        if effective_sample < self.MIN_SAMPLE_FOR_OPPORTUNITY or hr_clamped < 40.0:
            classification = "SPECULATIVE"
        elif (
            total_score >= self.OPPORTUNITY_SCORE_THRESHOLD
            and ((is_bettable_execution and exec_edge is not None and exec_edge > 0) or (best_odds and raw_edge is not None and raw_edge > 0))
        ):
            classification = "OPPORTUNITY"
        elif (
            (total_score >= self.SHORTLIST_SCORE_THRESHOLD and hr_clamped >= 60.0)
            or (best_odds is None and hr_clamped >= 65.0 and effective_sample >= 10 and stat_average > line_val)
        ):
            classification = "SHORTLIST"
        else:
            classification = "STANDARD"

        # 8. Actionability State & Distinct Status
        if is_bettable_execution:
            actionability = "BETTABLE"
            distinct_status = "VALUEBET" if is_val else "BETTABLE"
        elif execution_status == "MATCH_UNCERTAIN":
            actionability = "MATCH_UNCERTAIN"
            distinct_status = "MATCH_UNCERTAIN"
        elif execution_status == "NO_EXECUTION_ODDS":
            actionability = "NO_EXECUTION_ODDS"
            distinct_status = "NO_EXECUTION_ODDS"
        elif best_odds and best_odds > 1.0:
            actionability = "REFERENCE_ONLY"
            distinct_status = "REFERENCE_ONLY"
        else:
            actionability = "NO_EXECUTION_MARKET"
            distinct_status = "NO_EXECUTION_MARKET"

        # 9. Explicit Data Quality Flags
        quality_flags: List[str] = []
        if effective_sample < self.MIN_SAMPLE_FOR_OPPORTUNITY:
            quality_flags.append("SMALL_SAMPLE")
        if hr_clamped < 40.0 and has_sample:
            quality_flags.append("LOW_HIT_RATE")
        if not (best_odds and best_odds > 1.0):
            quality_flags.append("NO_REFERENCE_ODDS")
        if not (best_execution_odds and best_execution_odds > 1.0 and is_bettable_execution):
            quality_flags.append("NO_POLISH_ODDS")
        if execution_status == "MATCH_UNCERTAIN":
            quality_flags.append("MATCH_UNCERTAIN")
        if execution_status == "NO_EXECUTION_ODDS":
            quality_flags.append("INACTIVE_ODDS")
        if best_odds and is_bettable_execution and abs(best_odds - best_execution_odds) >= 0.35:
            quality_flags.append("LARGE_REFERENCE_EXECUTION_DISCREPANCY")
        if is_val:
            quality_flags.append("VALUEBET_POSITIVE_EV")

        warnings.append("Statistical edge only: historical sample does not guarantee future betting profitability.")

        return TeamPropOpportunityEvaluation(
            score=total_score,
            classification=classification,
            actionability=actionability,
            status=distinct_status,
            historical_probability=historical_probability,
            model_probability=model_probability,
            fair_odds=fair_odds,
            reference_market_probability=ref_market_prob,
            market_probability=ref_market_prob,
            raw_edge=raw_edge,
            raw_edge_pct=raw_edge_pct,
            execution_market_probability=exec_market_prob,
            execution_edge=exec_edge,
            execution_edge_pct=exec_edge_pct,
            value_edge_pp=value_edge_pp,
            reference_ev=ref_ev,
            reference_ev_pct=ref_ev_pct,
            execution_ev=exec_ev,
            execution_ev_pct=exec_ev_pct,
            is_valuebet=is_val,
            reasons=reasons,
            warnings=warnings,
            data_quality_flags=quality_flags,
            score_breakdown=breakdown,
            edge_type="STATISTICAL_EDGE",
        )