"""
Stage 9.2: Valuebet Lifecycle Management & Persistent Deduplication

Implements deterministic opportunity identification, state machine transitions
(NEW -> ALERTED -> UPDATED -> EXPIRED -> RESURRECTED), persistent deduplication,
material EV change classification, and notification budget enforcement for ValueBetCandidate.

Core Invariants:
- Deterministic Identity: Stable fingerprint based on (Event, Market, Line, Selection, Bookmaker, ReferenceSource).
  Volatile metrics (odds, EV %, timestamps) are NOT part of identity.
- Single Persisted Truth: Uses OpportunityRepository (OpportunityRecordORM) to survive process restarts.
- Conservative Expiration: Missed in consecutive scans causes EXPIRED.
- Delivery Safety: Failed/skipped alerts never mark an opportunity as ALERTED.
- Zero Alert Noise: Deduplicates identical scans and suppresses sub-material EV fluctuations.
- Notification Budgeting: Conservative cap on alerts per scan with quality-driven tie-breaking.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
import json
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple
import uuid

from database.models import OpportunityRecordORM
from database.repositories.opportunity_repository import OpportunityRepository
from normalization.lifecycle import LifecycleAction, OpportunityStatus
from normalization.market_identity import CanonicalMarketKey
from valuebets.models import ValueBetCandidate
from valuebets.quality_policy import ValuebetQualityEvaluation, ValuebetQualityPolicy


def generate_valuebet_fingerprint(candidate: ValueBetCandidate) -> str:
    """Generates a stable, deterministic logical fingerprint for a ValueBetCandidate.

    Formula:
      opp:VALUEBET:<EVENT_ID>:<MARKET_TYPE>:<LINE>:<SELECTION_TYPE>:<BOOKMAKER>:<REF_SOURCE>
    """
    line_str = f"{candidate.line:f}" if candidate.line is not None else "no_line"
    if "." in line_str and line_str != "no_line":
        line_str = line_str.rstrip("0").rstrip(".")
    bm = (candidate.bookmaker or "").lower().strip()
    ref = (candidate.reference_source or "").lower().strip()
    sel = (candidate.selection_type or "").upper().strip()
    mkt = (candidate.market_type or "").upper().strip()
    ev_id = str(candidate.canonical_event_id).strip()

    return f"opp:VALUEBET:{ev_id}:{mkt}:{line_str}:{sel}:{bm}:{ref}"


def _serialize_valuebet_snapshot(
    candidate: ValueBetCandidate,
    quality_eval: Optional[ValuebetQualityEvaluation] = None,
) -> Dict[str, Any]:
    """Serializes ValueBetCandidate and quality evaluation into a full audit snapshot."""
    line_str = str(candidate.line) if candidate.line is not None else None
    mkt_key_str = f"{candidate.market_type}:{candidate.period}:{candidate.scope}:{line_str or 'no_line'}"

    ref_odds_dict = {k: str(v) for k, v in candidate.reference_odds.items()}
    raw_p_dict = {k: str(v) for k, v in candidate.raw_probabilities.items()}

    snapshot: Dict[str, Any] = {
        "opportunity_id": candidate.candidate_id,
        "opportunity_type": "VALUEBET",
        "canonical_event_id": candidate.canonical_event_id,
        "home_team": candidate.home_team,
        "away_team": candidate.away_team,
        "competition_name": candidate.competition_name,
        "kickoff_time": candidate.kickoff_time,
        "canonical_market_key": {
            "market_type": candidate.market_type,
            "period": candidate.period,
            "scope": candidate.scope,
            "line": line_str,
            "key_string": mkt_key_str,
        },
        "selection_type": candidate.selection_type,
        "bookmaker": candidate.bookmaker,
        "bookmaker_odds": str(candidate.bookmaker_odds),
        "reference_source": candidate.reference_source,
        "reference_bookmaker": candidate.reference_bookmaker,
        "reference_market_timestamp": candidate.reference_market_timestamp,
        "reference_odds": ref_odds_dict,
        "raw_probabilities": raw_p_dict,
        "overround": str(candidate.overround),
        "fair_probability": str(candidate.fair_probability),
        "fair_odds": str(candidate.fair_odds),
        "value_edge": str(candidate.value_edge),
        "value_percent": str(candidate.value_percent),
        "event_evidence": {
            "home_team": candidate.home_team,
            "away_team": candidate.away_team,
            "competition_name": candidate.competition_name,
            "start_time": candidate.kickoff_time,
        },
        "legs": [
            {
                "selection_type": candidate.selection_type,
                "provider": candidate.bookmaker,
                "odds": str(candidate.bookmaker_odds),
                "implied_probability": str(Decimal("1") / candidate.bookmaker_odds) if candidate.bookmaker_odds > 0 else "0",
                "fair_odds": str(candidate.fair_odds),
                "fair_probability": str(candidate.fair_probability),
                "value_percent": str(candidate.value_percent),
            }
        ],
    }

    if quality_eval is not None:
        snapshot["quality_score"] = quality_eval.quality_score
        snapshot["competition_tier"] = quality_eval.competition_tier
        snapshot["tier_name"] = quality_eval.tier_name
        snapshot["is_qualified"] = quality_eval.is_qualified
        snapshot["rejection_reasons"] = [r.value for r in quality_eval.rejection_reasons]
        snapshot["score_breakdown"] = quality_eval.score_breakdown

    return snapshot


@dataclass(frozen=True)
class ValuebetEvaluationResult:
    """Evaluation result for an individual valuebet candidate."""
    candidate: ValueBetCandidate
    fingerprint: str
    action: LifecycleAction
    previous_status: Optional[str]
    current_status: str
    odds_changed: bool = False
    value_changed: bool = False
    value_delta: Decimal = Decimal("0")
    quality_evaluation: Optional[ValuebetQualityEvaluation] = None
    persisted_record: Optional[OpportunityRecordORM] = None


@dataclass
class ValuebetLifecycleBatch:
    """Batch result of evaluating multiple valuebet candidates."""
    evaluations: List[ValuebetEvaluationResult] = field(default_factory=list)
    to_dispatch: List[ValueBetCandidate] = field(default_factory=list)
    new_count: int = 0
    updated_count: int = 0
    suppressed_count: int = 0
    expired_count: int = 0


class ValuebetLifecycleManager:
    """Orchestrates valuebet persistence, state transitions, deduplication, and alert budgeting."""

    def __init__(
        self,
        repository: OpportunityRepository,
        quality_policy: Optional[ValuebetQualityPolicy] = None,
        material_value_delta_threshold: Decimal = Decimal("1.0"),  # 1.0% EV delta is material
        max_alerts_per_scan: int = 5,
    ) -> None:
        self.repository = repository
        self.quality_policy = quality_policy or ValuebetQualityPolicy()
        self.material_value_delta_threshold = material_value_delta_threshold
        self.max_alerts_per_scan = max_alerts_per_scan

    def evaluate_candidate(
        self,
        candidate: ValueBetCandidate,
        evaluation_time: Optional[datetime] = None,
    ) -> ValuebetEvaluationResult:
        """Evaluates a single ValueBetCandidate against persistent state and quality policies."""
        now = evaluation_time or datetime.now(timezone.utc)
        fingerprint = generate_valuebet_fingerprint(candidate)
        quality_eval = self.quality_policy.evaluate_quality(candidate, current_time=now)
        snapshot_dict = _serialize_valuebet_snapshot(candidate, quality_eval)
        snapshot_json = json.dumps(snapshot_dict)

        existing_record = self.repository.get_by_fingerprint(fingerprint)

        # Case 1: Brand New Candidate
        if existing_record is None:
            record = OpportunityRecordORM(
                id=f"rec_{uuid.uuid4().hex[:16]}",
                fingerprint=fingerprint,
                opportunity_type="VALUEBET",
                canonical_event_id=candidate.canonical_event_id,
                market_key=f"{candidate.market_type}:{candidate.period}:{candidate.scope}:{candidate.line or 'no_line'}",
                status=OpportunityStatus.NEW.value,
                first_seen_at=now,
                last_seen_at=now,
                last_changed_at=now,
                arbitrage_margin=float(candidate.value_percent),  # Stored as value % for valuebets
                implied_probability_sum=float(candidate.fair_probability),
                consecutive_misses=0,
                snapshot_json=snapshot_json,
                delivery_status=None,
                alert_count=0,
            )
            saved_record = self.repository.save_or_update(record)
            action = LifecycleAction.DISPATCH_INITIAL if quality_eval.is_qualified else LifecycleAction.SUPPRESS_INSIGNIFICANT
            return ValuebetEvaluationResult(
                candidate=candidate,
                fingerprint=fingerprint,
                action=action,
                previous_status=None,
                current_status=OpportunityStatus.NEW.value,
                odds_changed=True,
                value_changed=True,
                value_delta=candidate.value_percent,
                quality_evaluation=quality_eval,
                persisted_record=saved_record,
            )

        prev_status = existing_record.status

        # Case 2: Resurrect Expired Candidate
        if prev_status == OpportunityStatus.EXPIRED.value:
            existing_record.status = OpportunityStatus.NEW.value
            existing_record.last_seen_at = now
            existing_record.last_changed_at = now
            existing_record.expired_at = None
            existing_record.consecutive_misses = 0
            existing_record.arbitrage_margin = float(candidate.value_percent)
            existing_record.implied_probability_sum = float(candidate.fair_probability)
            existing_record.snapshot_json = snapshot_json
            saved_record = self.repository.save_or_update(existing_record)
            action = LifecycleAction.DISPATCH_INITIAL if quality_eval.is_qualified else LifecycleAction.SUPPRESS_INSIGNIFICANT
            return ValuebetEvaluationResult(
                candidate=candidate,
                fingerprint=fingerprint,
                action=action,
                previous_status=prev_status,
                current_status=OpportunityStatus.NEW.value,
                odds_changed=True,
                value_changed=True,
                value_delta=candidate.value_percent,
                quality_evaluation=quality_eval,
                persisted_record=saved_record,
            )

        # Case 3: Existing Candidate Re-observed — Evaluate Value & Odds Delta
        try:
            prev_snapshot = json.loads(existing_record.snapshot_json)
            prev_val_pct = Decimal(str(prev_snapshot.get("value_percent", "0")))
            prev_bm_odds = Decimal(str(prev_snapshot.get("bookmaker_odds", "0")))
        except Exception:
            prev_val_pct = Decimal(str(existing_record.arbitrage_margin))
            prev_bm_odds = Decimal("0")

        val_delta = candidate.value_percent - prev_val_pct
        abs_val_delta = abs(val_delta)
        odds_changed = abs(candidate.bookmaker_odds - prev_bm_odds) > Decimal("0.001")
        is_material_change = abs_val_delta >= self.material_value_delta_threshold

        if is_material_change:
            existing_record.status = OpportunityStatus.UPDATED.value
            existing_record.last_seen_at = now
            existing_record.last_changed_at = now
            existing_record.consecutive_misses = 0
            existing_record.arbitrage_margin = float(candidate.value_percent)
            existing_record.implied_probability_sum = float(candidate.fair_probability)
            existing_record.snapshot_json = snapshot_json
            saved_record = self.repository.save_or_update(existing_record)
            action = LifecycleAction.DISPATCH_UPDATE if quality_eval.is_qualified else LifecycleAction.SUPPRESS_INSIGNIFICANT
            return ValuebetEvaluationResult(
                candidate=candidate,
                fingerprint=fingerprint,
                action=action,
                previous_status=prev_status,
                current_status=OpportunityStatus.UPDATED.value,
                odds_changed=odds_changed,
                value_changed=True,
                value_delta=val_delta,
                quality_evaluation=quality_eval,
                persisted_record=saved_record,
            )
        elif abs_val_delta > Decimal("0.0001") or odds_changed:
            # Insignificant price fluctuation: update snapshot to latest reality without drift
            existing_record.last_seen_at = now
            existing_record.consecutive_misses = 0
            existing_record.arbitrage_margin = float(candidate.value_percent)
            existing_record.implied_probability_sum = float(candidate.fair_probability)
            existing_record.snapshot_json = snapshot_json
            saved_record = self.repository.save_or_update(existing_record)

            # If previous status was NEW or UPDATED and never delivered, permit initial/update dispatch retry
            if quality_eval.is_qualified and prev_status == OpportunityStatus.NEW.value and existing_record.delivery_status in (None, "FAILED"):
                action = LifecycleAction.DISPATCH_INITIAL
            elif quality_eval.is_qualified and prev_status == OpportunityStatus.UPDATED.value and existing_record.delivery_status in (None, "FAILED"):
                action = LifecycleAction.DISPATCH_UPDATE
            else:
                action = LifecycleAction.SUPPRESS_INSIGNIFICANT

            return ValuebetEvaluationResult(
                candidate=candidate,
                fingerprint=fingerprint,
                action=action,
                previous_status=prev_status,
                current_status=existing_record.status,
                odds_changed=odds_changed,
                value_changed=True,
                value_delta=val_delta,
                quality_evaluation=quality_eval,
                persisted_record=saved_record,
            )
        else:
            # Identical candidate observation
            existing_record.last_seen_at = now
            existing_record.consecutive_misses = 0
            saved_record = self.repository.save_or_update(existing_record)

            if quality_eval.is_qualified and prev_status == OpportunityStatus.NEW.value and existing_record.delivery_status in (None, "FAILED"):
                action = LifecycleAction.DISPATCH_INITIAL
            elif quality_eval.is_qualified and prev_status == OpportunityStatus.UPDATED.value and existing_record.delivery_status in (None, "FAILED"):
                action = LifecycleAction.DISPATCH_UPDATE
            else:
                action = LifecycleAction.SUPPRESS_DUPLICATE

            return ValuebetEvaluationResult(
                candidate=candidate,
                fingerprint=fingerprint,
                action=action,
                previous_status=prev_status,
                current_status=existing_record.status,
                odds_changed=False,
                value_changed=False,
                value_delta=Decimal("0"),
                quality_evaluation=quality_eval,
                persisted_record=saved_record,
            )

    def evaluate_candidates(
        self,
        candidates: Sequence[ValueBetCandidate],
        evaluation_time: Optional[datetime] = None,
    ) -> ValuebetLifecycleBatch:
        """Evaluates candidates in batch and applies notification budgeting to to_dispatch list."""
        batch = ValuebetLifecycleBatch()
        now = evaluation_time or datetime.now(timezone.utc)
        seen_fps: Set[str] = set()

        # Step 1: Evaluate each candidate
        for cand in candidates:
            fp = generate_valuebet_fingerprint(cand)
            if fp in seen_fps:
                continue
            seen_fps.add(fp)

            eval_res = self.evaluate_candidate(cand, evaluation_time=now)
            batch.evaluations.append(eval_res)

            if eval_res.current_status == OpportunityStatus.NEW.value and eval_res.previous_status in (None, OpportunityStatus.EXPIRED.value):
                batch.new_count += 1
            elif eval_res.current_status == OpportunityStatus.UPDATED.value:
                batch.updated_count += 1
            elif eval_res.action in (LifecycleAction.SUPPRESS_DUPLICATE, LifecycleAction.SUPPRESS_INSIGNIFICANT):
                batch.suppressed_count += 1

        # Step 2: Extract dispatch-eligible candidates
        dispatch_candidates: List[ValuebetEvaluationResult] = [
            e for e in batch.evaluations
            if e.action in (LifecycleAction.DISPATCH_INITIAL, LifecycleAction.DISPATCH_UPDATE)
            and e.quality_evaluation is not None
            and e.quality_evaluation.is_qualified
        ]

        # Step 3: Sort by Quality Score & EV % deterministically
        def _dispatch_sort_key(e: ValuebetEvaluationResult) -> Tuple[Any, ...]:
            q_score = e.quality_evaluation.quality_score if e.quality_evaluation else 0.0
            val_pct = e.candidate.value_percent
            tier = e.quality_evaluation.competition_tier if e.quality_evaluation else 2
            return (-q_score, -val_pct, tier, e.candidate.candidate_id)

        dispatch_candidates.sort(key=_dispatch_sort_key)

        # Step 4: Apply notification budget (top N candidates)
        top_dispatches = dispatch_candidates[:self.max_alerts_per_scan]
        batch.to_dispatch = [e.candidate for e in top_dispatches]

        return batch

    def expire_missing_candidates(
        self,
        evaluated_fingerprints: Set[str],
        evaluation_time: Optional[datetime] = None,
        max_misses: int = 2,
    ) -> List[OpportunityRecordORM]:
        """Marks missing persisted active valuebets as EXPIRED after consecutive misses."""
        now = evaluation_time or datetime.now(timezone.utc)
        expired_records: List[OpportunityRecordORM] = []

        active_records = self.repository.list_active()
        for rec in active_records:
            if rec.opportunity_type != "VALUEBET":
                continue
            if rec.fingerprint not in evaluated_fingerprints:
                rec.consecutive_misses = (rec.consecutive_misses or 0) + 1
                if rec.consecutive_misses >= max_misses:
                    rec.status = OpportunityStatus.EXPIRED.value
                    rec.expired_at = now
                    expired_records.append(rec)
                self.repository.save_or_update(rec)

        return expired_records
