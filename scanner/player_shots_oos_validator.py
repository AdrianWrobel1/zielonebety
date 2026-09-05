"""
Stage A.10: Player Shots Over 0.5 OOS Validation & Calibration Utilities

Provides minimal, strictly validated mathematical and filtering tools for
evaluating pre-match reference_probability against real ground-truth outcomes y in {0, 1}.

Invariants:
- Reference probability is never modified or fitted post-hoc.
- Hit Rate is never equated 1:1 with probability.
- Numerical log loss safeguards apply via eps clipping without mutating underlying data.
- Strict qualification filtering excludes unresolvable, pending, or leaky records.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

from database.models import PlayerPropSnapshotORM
from database.repositories.player_prop_snapshot_repository import PlayerPropSnapshotRepository


@dataclass(frozen=True)
class CalibrationBin:
    """Represents an empirical calibration evaluation bucket."""
    lower_bound: float
    upper_bound: float
    count: int
    mean_predicted_prob: float
    empirical_hit_rate: float
    calibration_gap: float  # mean_predicted_prob - empirical_hit_rate


def calculate_brier_score(
    probabilities: Sequence[float],
    outcomes: Sequence[int],
) -> float:
    """Calculates the Brier Score (Mean Squared Error of probability predictions).

    Brier = (1/N) * sum((p_i - y_i)^2)
    """
    if not probabilities or not outcomes or len(probabilities) != len(outcomes):
        return 0.0

    total_sq_err = sum(
        (float(p) - float(y)) ** 2
        for p, y in zip(probabilities, outcomes)
    )
    return total_sq_err / len(probabilities)


def calculate_log_loss(
    probabilities: Sequence[float],
    outcomes: Sequence[int],
    eps: float = 1e-15,
) -> float:
    """Calculates the binary cross-entropy (Log Loss) with numerical boundary clipping.

    LogLoss = - (1/N) * sum(y_i * ln(p_i) + (1 - y_i) * ln(1 - p_i))
    """
    if not probabilities or not outcomes or len(probabilities) != len(outcomes):
        return 0.0

    total_loss = 0.0
    for p_val, y_val in zip(probabilities, outcomes):
        p_clipped = max(eps, min(1.0 - eps, float(p_val)))
        y_float = float(y_val)
        loss_i = -(y_float * math.log(p_clipped) + (1.0 - y_float) * math.log(1.0 - p_clipped))
        total_loss += loss_i

    return total_loss / len(probabilities)


def compute_calibration_table(
    probabilities: Sequence[float],
    outcomes: Sequence[int],
    bins: Optional[Sequence[Tuple[float, float]]] = None,
) -> List[CalibrationBin]:
    """Computes calibration bins and empirical hit rates.

    Default Bins:
    [0.50, 0.60), [0.60, 0.70), [0.70, 0.80), [0.80, 0.90), [0.90, 1.00]
    """
    if bins is None:
        bins = [
            (0.50, 0.60),
            (0.60, 0.70),
            (0.70, 0.80),
            (0.80, 0.90),
            (0.90, 1.00),
        ]

    results: List[CalibrationBin] = []

    for idx, (low, high) in enumerate(bins):
        is_last = (idx == len(bins) - 1)
        bin_probs: List[float] = []
        bin_outcomes: List[int] = []

        for p, y in zip(probabilities, outcomes):
            p_f = float(p)
            in_bin = (low <= p_f <= high) if is_last else (low <= p_f < high)
            if in_bin:
                bin_probs.append(p_f)
                bin_outcomes.append(int(y))

        n_count = len(bin_probs)
        if n_count > 0:
            mean_prob = sum(bin_probs) / n_count
            hit_rate = sum(bin_outcomes) / n_count
            gap = mean_prob - hit_rate
        else:
            mean_prob = 0.0
            hit_rate = 0.0
            gap = 0.0

        results.append(
            CalibrationBin(
                lower_bound=low,
                upper_bound=high,
                count=n_count,
                mean_predicted_prob=mean_prob,
                empirical_hit_rate=hit_rate,
                calibration_gap=gap,
            )
        )

    return results


def filter_qualified_oos_records(
    records: Sequence[PlayerPropSnapshotORM],
) -> Tuple[List[PlayerPropSnapshotORM], Dict[str, Any]]:
    """Filters database snapshot records down to strictly qualified Out-Of-Sample validation records.

    Criteria for Qualification:
    1. Scope: stat_type == "SHOTS", line == 0.5, direction == "OVER".
    2. Status: Settled with conclusive outcome (outcome_status in ("SETTLED_WIN", "SETTLED_LOSS")).
    3. Prediction: Valid reference_probability present in (0, 1).
    4. Outcome: y in {0, 1} (actual_outcome is not None).
    5. Temporal Integrity:
       - observed_at < kickoff_at (pre-match observation)
       - settled_at >= kickoff_at (post-match resolution, when both are set)
    """
    qualified: List[PlayerPropSnapshotORM] = []
    audit: Dict[str, Any] = {
        "total": len(records),
        "qualified": 0,
        "excluded_pending": 0,
        "excluded_void": 0,
        "excluded_unknown": 0,
        "excluded_missing_probability": 0,
        "excluded_missing_outcome": 0,
        "excluded_temporal_violation": 0,
        "excluded_out_of_scope": 0,
        "excluded_duplicate": 0,
    }

    seen_props = set()

    for rec in records:
        # 1. Deduplication check
        if rec.canonical_prop_id in seen_props:
            audit["excluded_duplicate"] += 1
            continue
        seen_props.add(rec.canonical_prop_id)

        # 2. Scope check
        stat_norm = (rec.stat_type or "").strip().upper()
        dir_norm = (rec.direction or "").strip().upper()
        line_val = float(rec.line) if rec.line is not None else 0.5
        if stat_norm != "SHOTS" or abs(line_val - 0.5) > 0.01 or dir_norm != "OVER":
            audit["excluded_out_of_scope"] += 1
            continue

        # 3. Status checks
        status_norm = (rec.outcome_status or "PENDING").strip().upper()
        if status_norm == "PENDING":
            audit["excluded_pending"] += 1
            continue
        if status_norm == "VOID":
            audit["excluded_void"] += 1
            continue
        if status_norm == "UNKNOWN":
            audit["excluded_unknown"] += 1
            continue

        # 4. Prediction validity
        if (
            rec.reference_probability is None
            or rec.reference_probability <= 0.0
            or rec.reference_probability >= 1.0
        ):
            audit["excluded_missing_probability"] += 1
            continue

        # 5. Outcome validity (y in {0, 1})
        if rec.actual_outcome not in (0, 1):
            audit["excluded_missing_outcome"] += 1
            continue

        # 6. Temporal Integrity
        if rec.kickoff_at is not None:
            kickoff_utc = (
                rec.kickoff_at if rec.kickoff_at.tzinfo else rec.kickoff_at.replace(tzinfo=timezone.utc)
            )
            if rec.observed_at is not None:
                obs_utc = (
                    rec.observed_at if rec.observed_at.tzinfo else rec.observed_at.replace(tzinfo=timezone.utc)
                )
                if obs_utc >= kickoff_utc:
                    audit["excluded_temporal_violation"] += 1
                    continue

            if rec.settled_at is not None:
                settled_utc = (
                    rec.settled_at if rec.settled_at.tzinfo else rec.settled_at.replace(tzinfo=timezone.utc)
                )
                if settled_utc < kickoff_utc:
                    audit["excluded_temporal_violation"] += 1
                    continue

        qualified.append(rec)

    audit["qualified"] = len(qualified)
    return qualified, audit


def audit_oos_dataset(repository: PlayerPropSnapshotRepository) -> Dict[str, Any]:
    """Audits the player prop snapshots currently stored in the database."""
    all_snapshots = repository.list_all()

    win_count = sum(1 for s in all_snapshots if s.outcome_status == "SETTLED_WIN")
    loss_count = sum(1 for s in all_snapshots if s.outcome_status == "SETTLED_LOSS")
    void_count = sum(1 for s in all_snapshots if s.outcome_status == "VOID")
    pending_count = sum(1 for s in all_snapshots if s.outcome_status == "PENDING")
    unknown_count = sum(1 for s in all_snapshots if s.outcome_status == "UNKNOWN")
    no_prob_count = sum(1 for s in all_snapshots if s.reference_probability is None)
    no_outcome_count = sum(1 for s in all_snapshots if s.actual_outcome is None)

    qualified, exclusion_audit = filter_qualified_oos_records(all_snapshots)

    # Date range and unique entity counts
    dates = [s.kickoff_at for s in all_snapshots if s.kickoff_at is not None]
    date_range = "N/A (No records)"
    if dates:
        min_date = min(dates).strftime("%Y-%m-%d")
        max_date = max(dates).strftime("%Y-%m-%d")
        date_range = f"{min_date} to {max_date}"

    unique_events = len({s.canonical_event_id for s in all_snapshots if s.canonical_event_id})
    unique_players = len({s.canonical_player_id for s in all_snapshots if s.canonical_player_id})
    unique_competitions = len({s.competition for s in all_snapshots if s.competition})

    # Data sufficiency category
    n_qual = len(qualified)
    if n_qual < 30:
        sufficiency_category = "<30"
        decision = "C — DATASET TOO SMALL / CONTINUE COLLECTION"
    elif n_qual < 100:
        sufficiency_category = "30-99"
        decision = "B — PRELIMINARY OOS ANALYSIS ONLY"
    elif n_qual < 300:
        sufficiency_category = ">=100"
        decision = "A — DATASET READY FOR HISTORICAL CALIBRATION"
    else:
        sufficiency_category = ">=300"
        decision = "A — DATASET READY FOR HISTORICAL CALIBRATION & SEGMENTS"

    return {
        "total_snapshots": len(all_snapshots),
        "settled_count": win_count + loss_count + void_count,
        "win_count": win_count,
        "loss_count": loss_count,
        "void_count": void_count,
        "pending_count": pending_count,
        "unknown_count": unknown_count,
        "no_probability_count": no_prob_count,
        "no_outcome_count": no_outcome_count,
        "qualified_oos_count": n_qual,
        "exclusion_audit": exclusion_audit,
        "date_range": date_range,
        "unique_events": unique_events,
        "unique_players": unique_players,
        "unique_competitions": unique_competitions,
        "sufficiency_category": sufficiency_category,
        "decision": decision,
        "qualified_records": qualified,
    }
