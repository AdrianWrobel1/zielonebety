"""
Stage 6.3 & 6.4: Opportunity Lifecycle & Persistent Deduplication

Implements deterministic opportunity identification, state machine transitions
(NEW -> ALERTED -> UPDATED -> EXPIRED), persistent deduplication, conservative
expiration handling, change significance policy integration (Stage 6.4), and
safe notification feedback.

Principles:
- Deterministic Identity: Stable fingerprint based on canonical event, market key, and leg assignments.
- Decoupled Pricing: Odds and margins are dynamic state payload, NOT part of logical identity.
- Single Persisted Truth: Lifecycle state is persisted to database and survives process restarts.
- Conservative Expiration: Only confirmed absence during successful scans causes expiration.
- Delivery Safety: Failed or skipped alerts never mark an opportunity as ALERTED.
- Zero Alert Noise: Repeated identical observations and insignificant price fluctuations are suppressed.
- Latest Observation Truth: Insignificant observations update the persisted snapshot so future checks
  are compared against the latest reality without false cumulative drift.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
import json
import uuid
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple, Union


from database.models import DeliveryRecordORM, OpportunityRecordORM
from database.repositories.delivery_repository import DeliveryRepository
from database.repositories.opportunity_repository import OpportunityRepository
from domain.models import MatchEvidence
from normalization.alert_policy import (
    ChangeClassification,
    DefaultOpportunityAlertPolicy,
    OpportunityAlertConfig,
    OpportunityAlertPolicy,
    OpportunityChangeEvaluation,
)
from normalization.quality_policy import (
    DefaultOpportunityQualityPolicy,
    OpportunityQualityConfig,
    OpportunityQualityEvaluation,
    OpportunityQualityPolicy,
    OpportunityRankingEngine,
)
from normalization.delivery_reliability import (
    DeliveryRetryConfig,
    DeliveryState,
    FailureCategory,
    calculate_next_retry_time,
    classify_delivery_failure,
    determine_lifecycle_version,
    generate_delivery_idempotency_key,
)


from normalization.dispatcher import (
    BatchDispatchResult,
    DispatchResult,
    DispatchStatus,
    OpportunityDispatcher,
)
from normalization.market_identity import CanonicalMarketKey
from normalization.surebet import (
    MarketSurebetEvaluation,
    SurebetDetectionResult,
    SurebetLeg,
    SurebetOpportunity,
    SurebetStatus,
)


class OpportunityStatus(str, Enum):
    """Authoritative lifecycle status for an opportunity."""
    NEW = "NEW"          # First observed; awaiting initial alert delivery
    ALERTED = "ALERTED"  # Successfully delivered to notification consumer(s)
    UPDATED = "UPDATED"  # Existing opportunity re-observed with meaningful price/margin changes
    EXPIRED = "EXPIRED"  # Opportunity no longer present or valid in the market


class LifecycleAction(str, Enum):
    """Action determined by lifecycle evaluation."""
    DISPATCH_INITIAL = "DISPATCH_INITIAL"          # Dispatch initial alert for NEW opportunity
    DISPATCH_UPDATE = "DISPATCH_UPDATE"            # Dispatch update alert for changed opportunity
    SUPPRESS_DUPLICATE = "SUPPRESS_DUPLICATE"      # Suppress duplicate notification for unchanged opportunity
    SUPPRESS_INSIGNIFICANT = "SUPPRESS_INSIGNIFICANT"  # Suppress insignificant change notification
    EXPIRED = "EXPIRED"                            # Mark expired
    IGNORED = "IGNORED"                            # No action taken


def generate_opportunity_fingerprint(
    opportunity_type: str,
    canonical_event_id: str,
    canonical_market_key: CanonicalMarketKey,
    legs: Sequence[SurebetLeg],
) -> str:
    """Generates a stable, deterministic logical opportunity fingerprint.

    Formula:
      opp:<TYPE>:<EVENT_ID>:<MARKET_KEY_STRING>:<SORTED_LEGS>
    where SORTED_LEGS is:
      <selection_type>:<bookmaker>|... sorted by selection_type.
    """
    sorted_legs = sorted(legs, key=lambda l: (l.selection_type, l.provider.lower()))
    leg_str = "|".join(f"{l.selection_type}:{l.provider.lower()}" for l in sorted_legs)
    mkt_str = canonical_market_key.to_key_string()
    return f"opp:{opportunity_type.upper()}:{canonical_event_id}:{mkt_str}:{leg_str}"


def _serialize_opportunity_snapshot(opp: SurebetOpportunity) -> Dict[str, Any]:
    """Serializes SurebetOpportunity state into a JSON-safe dictionary."""
    legs_data = []
    for leg in sorted(opp.legs, key=lambda l: l.selection_type):
        legs_data.append({
            "selection_type": leg.selection_type,
            "provider": leg.provider,
            "odds": str(leg.odds),
            "raw_odds": str(leg.odds),
            "effective_odds": str(leg.effective_odds) if leg.effective_odds is not None else str(leg.odds),
            "tax_rate": str(leg.tax_rate),
            "is_tax_applied": leg.is_tax_applied,
            "source_selection_id": leg.source_selection_id,
            "source_event_id": leg.source_event_id,
            "source_market_id": leg.source_market_id,
            "implied_probability": str(leg.implied_probability) if leg.implied_probability is not None else None,
        })

    mkt_key_dict = {
        "market_type": opp.canonical_market_key.market_type,
        "period": opp.canonical_market_key.period,
        "scope": opp.canonical_market_key.scope,
        "line": str(opp.canonical_market_key.line) if opp.canonical_market_key.line is not None else None,
        "key_string": opp.canonical_market_key.to_key_string(),
    }

    evidence_dict: Optional[Dict[str, Any]] = None
    if opp.event_evidence:
        ev = opp.event_evidence
        ev_data = getattr(ev, "evidence", {}) or {}
        home = ev_data.get("home_team") or getattr(ev, "home_team", None)
        away = ev_data.get("away_team") or getattr(ev, "away_team", None)
        comp = ev_data.get("competition_name") or getattr(ev, "competition_name", None)
        start_t = ev_data.get("start_time") or getattr(ev, "start_time", None)

        evidence_dict = {
            "source_provider": getattr(ev, "source_provider", None),
            "target_provider": getattr(ev, "target_provider", None),
            "source_event_id": getattr(ev, "source_event_id", None),
            "target_event_id": getattr(ev, "target_event_id", None),
            "decision": getattr(ev, "decision", None),
            "home_team": home,
            "away_team": away,
            "competition_name": comp,
            "start_time": str(start_t) if start_t else None,
        }

    return {
        "opportunity_id": opp.opportunity_id,
        "canonical_event_id": opp.canonical_event_id,
        "canonical_market_key": mkt_key_dict,
        "arbitrage_margin": str(opp.arbitrage_margin),
        "arbitrage_margin_pct": str(opp.arbitrage_margin * Decimal("100")),
        "implied_probability_sum": str(opp.implied_probability_sum),
        "is_mixed_bookmakers": opp.is_mixed_bookmakers,
        "bookmakers": list(opp.bookmakers),
        "legs": legs_data,
        "event_evidence": evidence_dict,
        "market_evidence": opp.market_evidence,
    }


def _has_meaningful_change(
    existing_snapshot_json: str,
    current_opp: SurebetOpportunity,
) -> Tuple[bool, bool, bool]:
    """Legacy helper detecting whether current opportunity has changed compared to persisted snapshot.

    Returns: (is_changed, odds_changed, margin_changed)
    """
    try:
        prev = json.loads(existing_snapshot_json)
    except Exception:
        return True, True, True

    # 1. Compare arbitrage margin
    prev_margin_str = prev.get("arbitrage_margin", "0.0")
    prev_margin = Decimal(prev_margin_str)
    margin_changed = abs(current_opp.arbitrage_margin - prev_margin) > Decimal("0.000001")

    # 2. Compare individual leg odds
    prev_legs_map = {l["selection_type"]: Decimal(str(l["odds"])) for l in prev.get("legs", [])}
    current_legs_map = {l.selection_type: l.odds for l in current_opp.legs}

    odds_changed = False
    if set(prev_legs_map.keys()) != set(current_legs_map.keys()):
        odds_changed = True
    else:
        for sel_type, curr_odds in current_legs_map.items():
            prev_odds = prev_legs_map.get(sel_type)
            if prev_odds is None or abs(curr_odds - prev_odds) > Decimal("0.000001"):
                odds_changed = True
                break

    is_changed = margin_changed or odds_changed
    return is_changed, odds_changed, margin_changed


@dataclass(frozen=True)
class OpportunityEvaluationResult:
    """Evaluation result for an individual opportunity."""
    opportunity: SurebetOpportunity
    fingerprint: str
    action: LifecycleAction
    previous_status: Optional[str]
    current_status: str
    odds_changed: bool = False
    margin_changed: bool = False
    change_evaluation: Optional[OpportunityChangeEvaluation] = None
    quality_evaluation: Optional[OpportunityQualityEvaluation] = None
    persisted_record: Optional[OpportunityRecordORM] = None


@dataclass
class LifecycleEvaluationBatch:
    """Batch result of evaluating multiple opportunities against persistent state."""
    evaluations: List[OpportunityEvaluationResult] = field(default_factory=list)
    to_dispatch: List[SurebetOpportunity] = field(default_factory=list)
    new_count: int = 0
    updated_count: int = 0
    suppressed_count: int = 0
    expired_count: int = 0


@dataclass
class LifecycleDispatchSummary:
    """Comprehensive summary of lifecycle processing and downstream dispatch."""
    evaluation_batch: LifecycleEvaluationBatch
    dispatch_result: Optional[BatchDispatchResult] = None
    delivered_count: int = 0
    failed_count: int = 0
    skipped_count: int = 0
    expired_records: List[OpportunityRecordORM] = field(default_factory=list)


class OpportunityLifecycleManager:
    """Lifecycle manager and persistent deduplication orchestrator.

    Integrates SurebetDetectorEngine output with OpportunityRepository persistence,
    OpportunityAlertPolicy change significance evaluation, DeliveryRepository reliability,
    and OpportunityDispatcher delivery.
    """

    def __init__(
        self,
        repository: OpportunityRepository,
        alert_policy: Optional[OpportunityAlertPolicy] = None,
        delivery_repository: Optional[DeliveryRepository] = None,
        retry_config: Optional[DeliveryRetryConfig] = None,
        quality_policy: Optional[OpportunityQualityPolicy] = None,
    ):
        self.repository = repository
        self.alert_policy = alert_policy or DefaultOpportunityAlertPolicy()
        self.delivery_repository = delivery_repository
        self.retry_config = retry_config or DeliveryRetryConfig()
        self.quality_policy = quality_policy or DefaultOpportunityQualityPolicy()
        self.ranking_engine = OpportunityRankingEngine(quality_policy=self.quality_policy)


    def evaluate_opportunity(
        self,
        opportunity: SurebetOpportunity,
        evaluation_time: Optional[datetime] = None,
    ) -> OpportunityEvaluationResult:
        """Evaluates a single SurebetOpportunity against persisted lifecycle state, alert policy, and quality filters."""
        now = evaluation_time or datetime.now(timezone.utc)
        opp_type = "SUREBET"
        fingerprint = generate_opportunity_fingerprint(
            opportunity_type=opp_type,
            canonical_event_id=opportunity.canonical_event_id,
            canonical_market_key=opportunity.canonical_market_key,
            legs=opportunity.legs,
        )

        quality_eval = self.quality_policy.evaluate_quality(opportunity, current_time=now)
        existing_record = self.repository.get_by_fingerprint(fingerprint)
        snapshot_dict = _serialize_opportunity_snapshot(opportunity)
        # Attach quality telemetry into snapshot dict
        snapshot_dict["quality_score"] = quality_eval.quality_score
        snapshot_dict["competition_tier"] = quality_eval.competition_tier
        snapshot_dict["tier_name"] = quality_eval.tier_name
        snapshot_dict["is_qualified"] = quality_eval.is_qualified
        snapshot_dict["rejection_reasons"] = list(quality_eval.rejection_reasons)
        snapshot_json = json.dumps(snapshot_dict)

        if existing_record is None:
            # Case 1: Brand new opportunity
            record = OpportunityRecordORM(
                id=f"rec_{uuid.uuid4().hex[:16]}",
                fingerprint=fingerprint,
                opportunity_type=opp_type,
                canonical_event_id=opportunity.canonical_event_id,
                market_key=opportunity.canonical_market_key.to_key_string(),
                status=OpportunityStatus.NEW.value,
                first_seen_at=now,
                last_seen_at=now,
                last_changed_at=now,
                arbitrage_margin=float(opportunity.arbitrage_margin),
                implied_probability_sum=float(opportunity.implied_probability_sum),
                consecutive_misses=0,
                snapshot_json=snapshot_json,
                delivery_status=None,
                alert_count=0,
            )
            saved_record = self.repository.save_or_update(record)
            action = LifecycleAction.DISPATCH_INITIAL if quality_eval.is_qualified else LifecycleAction.SUPPRESS_INSIGNIFICANT
            return OpportunityEvaluationResult(
                opportunity=opportunity,
                fingerprint=fingerprint,
                action=action,
                previous_status=None,
                current_status=OpportunityStatus.NEW.value,
                odds_changed=True,
                margin_changed=True,
                quality_evaluation=quality_eval,
                persisted_record=saved_record,
            )

        prev_status = existing_record.status

        # Case 2: Expired opportunity reappearing -> Resurrect as NEW
        if prev_status == OpportunityStatus.EXPIRED.value:
            existing_record.status = OpportunityStatus.NEW.value
            existing_record.last_seen_at = now
            existing_record.last_changed_at = now
            existing_record.expired_at = None
            existing_record.consecutive_misses = 0
            existing_record.arbitrage_margin = float(opportunity.arbitrage_margin)
            existing_record.implied_probability_sum = float(opportunity.implied_probability_sum)
            existing_record.snapshot_json = snapshot_json
            saved_record = self.repository.save_or_update(existing_record)
            action = LifecycleAction.DISPATCH_INITIAL if quality_eval.is_qualified else LifecycleAction.SUPPRESS_INSIGNIFICANT
            return OpportunityEvaluationResult(
                opportunity=opportunity,
                fingerprint=fingerprint,
                action=action,
                previous_status=prev_status,
                current_status=OpportunityStatus.NEW.value,
                odds_changed=True,
                margin_changed=True,
                quality_evaluation=quality_eval,
                persisted_record=saved_record,
            )

        # Case 3: Policy-driven change classification against latest persisted snapshot
        change_eval = self.alert_policy.classify_change(
            previous=existing_record.snapshot_json,
            current=opportunity,
            last_alerted_at=existing_record.last_alerted_at,
            current_time=now,
        )

        odds_changed = change_eval.max_odds_absolute_delta > Decimal("0.0")
        margin_changed = abs(change_eval.margin_delta) > Decimal("0.0")

        if change_eval.classification == ChangeClassification.MATERIAL_CHANGE:
            # Transition to UPDATED
            existing_record.status = OpportunityStatus.UPDATED.value
            existing_record.last_seen_at = now
            existing_record.last_changed_at = now
            existing_record.consecutive_misses = 0
            existing_record.arbitrage_margin = float(opportunity.arbitrage_margin)
            existing_record.implied_probability_sum = float(opportunity.implied_probability_sum)
            existing_record.snapshot_json = snapshot_json
            saved_record = self.repository.save_or_update(existing_record)
            action = LifecycleAction.DISPATCH_UPDATE if quality_eval.is_qualified else LifecycleAction.SUPPRESS_INSIGNIFICANT
            return OpportunityEvaluationResult(
                opportunity=opportunity,
                fingerprint=fingerprint,
                action=action,
                previous_status=prev_status,
                current_status=OpportunityStatus.UPDATED.value,
                odds_changed=odds_changed,
                margin_changed=margin_changed,
                change_evaluation=change_eval,
                quality_evaluation=quality_eval,
                persisted_record=saved_record,
            )
        elif change_eval.classification == ChangeClassification.INSIGNIFICANT_CHANGE:
            # Insignificant price fluctuation: update persisted snapshot and last_seen_at
            # so future scans compare against latest reality without accumulating false drift (Invariants 20 & 27)
            existing_record.last_seen_at = now
            existing_record.consecutive_misses = 0
            existing_record.arbitrage_margin = float(opportunity.arbitrage_margin)
            existing_record.implied_probability_sum = float(opportunity.implied_probability_sum)
            existing_record.snapshot_json = snapshot_json
            saved_record = self.repository.save_or_update(existing_record)

            # If previous status was NEW or UPDATED and never delivered, we should still allow dispatch retry if qualified
            if quality_eval.is_qualified and prev_status == OpportunityStatus.NEW.value and existing_record.delivery_status in (None, "FAILED"):
                action = LifecycleAction.DISPATCH_INITIAL
            elif quality_eval.is_qualified and prev_status == OpportunityStatus.UPDATED.value and existing_record.delivery_status in (None, "FAILED"):
                action = LifecycleAction.DISPATCH_UPDATE
            else:
                action = LifecycleAction.SUPPRESS_INSIGNIFICANT

            return OpportunityEvaluationResult(
                opportunity=opportunity,
                fingerprint=fingerprint,
                action=action,
                previous_status=prev_status,
                current_status=existing_record.status,
                odds_changed=odds_changed,
                margin_changed=margin_changed,
                change_evaluation=change_eval,
                quality_evaluation=quality_eval,
                persisted_record=saved_record,
            )
        else:
            # NO_CHANGE: Identical observation: Keep status, update last_seen_at, reset misses, suppress alert
            existing_record.last_seen_at = now
            existing_record.consecutive_misses = 0
            saved_record = self.repository.save_or_update(existing_record)

            # If previous status was NEW or UPDATED and never delivered, we should still allow dispatch retry if qualified
            if quality_eval.is_qualified and prev_status == OpportunityStatus.NEW.value and existing_record.delivery_status in (None, "FAILED"):
                action = LifecycleAction.DISPATCH_INITIAL
            elif quality_eval.is_qualified and prev_status == OpportunityStatus.UPDATED.value and existing_record.delivery_status in (None, "FAILED"):
                action = LifecycleAction.DISPATCH_UPDATE
            else:
                action = LifecycleAction.SUPPRESS_DUPLICATE

            return OpportunityEvaluationResult(
                opportunity=opportunity,
                fingerprint=fingerprint,
                action=action,
                previous_status=prev_status,
                current_status=existing_record.status,
                odds_changed=False,
                margin_changed=False,
                change_evaluation=change_eval,
                quality_evaluation=quality_eval,
                persisted_record=saved_record,
            )

    def evaluate_opportunities(
        self,
        opportunities: Sequence[SurebetOpportunity],
        evaluation_time: Optional[datetime] = None,
    ) -> LifecycleEvaluationBatch:
        """Batch evaluates opportunities against persistent lifecycle state and ranks qualified dispatch items."""
        batch = LifecycleEvaluationBatch()
        seen_batch_fps: Set[str] = set()

        for opp in opportunities:
            eval_res = self.evaluate_opportunity(opp, evaluation_time=evaluation_time)
            batch.evaluations.append(eval_res)

            # Intra-batch deduplication protection
            if eval_res.fingerprint in seen_batch_fps:
                continue
            seen_batch_fps.add(eval_res.fingerprint)

            if eval_res.action in (LifecycleAction.DISPATCH_INITIAL, LifecycleAction.DISPATCH_UPDATE):
                batch.to_dispatch.append(opp)
                if eval_res.action == LifecycleAction.DISPATCH_INITIAL:
                    batch.new_count += 1
                else:
                    batch.updated_count += 1
            elif eval_res.action in (LifecycleAction.SUPPRESS_DUPLICATE, LifecycleAction.SUPPRESS_INSIGNIFICANT):
                batch.suppressed_count += 1

        # Deterministic Ranking of dispatch batch
        if batch.to_dispatch:
            ranked_pairs = self.ranking_engine.rank_opportunities(
                batch.to_dispatch,
                current_time=evaluation_time,
                filter_unqualified=True,
            )
            batch.to_dispatch = [pair[0] for pair in ranked_pairs]

        return batch

    def process_market_evaluations(
        self,
        evaluations: Sequence[MarketSurebetEvaluation],
        max_misses: int = 2,
        evaluation_time: Optional[datetime] = None,
    ) -> List[OpportunityRecordORM]:
        """Conservatively expires opportunities on markets that were scanned successfully but where surebets disappeared.

        Only evaluations from successful scans (where status is SUREBET, NO_SUREBET, or INCOMPLETE_MARKET)
        are processed. Scanner exceptions or un-scanned markets are never treated as absence.
        """
        now = evaluation_time or datetime.now(timezone.utc)
        expired_records: List[OpportunityRecordORM] = []

        for eval_mkt in evaluations:
            # Guard: Only evaluate valid completed market scans (not unsupported/invalid)
            if eval_mkt.status not in (SurebetStatus.SUREBET, SurebetStatus.NO_SUREBET, SurebetStatus.INCOMPLETE_MARKET):
                continue

            event_id = eval_mkt.canonical_event_id
            mkt_key_str = eval_mkt.canonical_market_key.to_key_string()

            active_fps: Set[str] = set()
            if eval_mkt.opportunity is not None and eval_mkt.status == SurebetStatus.SUREBET:
                fp = generate_opportunity_fingerprint(
                    opportunity_type="SUREBET",
                    canonical_event_id=event_id,
                    canonical_market_key=eval_mkt.canonical_market_key,
                    legs=eval_mkt.opportunity.legs,
                )
                active_fps.add(fp)

            exp = self.repository.increment_misses_for_market(
                canonical_event_id=event_id,
                market_key=mkt_key_str,
                active_fingerprints=active_fps,
                max_misses=max_misses,
                miss_time=now,
            )
            expired_records.extend(exp)

        return expired_records

    def process_and_dispatch(
        self,
        detection_result: SurebetDetectionResult,
        dispatcher: OpportunityDispatcher,
        max_misses: int = 2,
        evaluation_time: Optional[datetime] = None,
    ) -> LifecycleDispatchSummary:
        """Full lifecycle orchestration pipeline: evaluate -> expire absent -> dispatch eligible -> commit delivery state."""
        now = evaluation_time or datetime.now(timezone.utc)

        # 1. Evaluate market absence on all successfully evaluated markets
        expired_records = self.process_market_evaluations(
            evaluations=detection_result.evaluations,
            max_misses=max_misses,
            evaluation_time=now,
        )

        # 2. Evaluate opportunities against lifecycle state
        eval_batch = self.evaluate_opportunities(
            opportunities=detection_result.opportunities,
            evaluation_time=now,
        )
        eval_batch.expired_count = len(expired_records)

        # 3. If nothing to dispatch, return summary
        if not eval_batch.to_dispatch:
            return LifecycleDispatchSummary(
                evaluation_batch=eval_batch,
                dispatch_result=None,
                delivered_count=0,
                failed_count=0,
                skipped_count=0,
                expired_records=expired_records,
            )

        # 4. If delivery_repository is configured, pre-persist PENDING records and supersede older pending versions
        opp_version_map: Dict[str, int] = {}
        eval_by_fp = {e.fingerprint: e for e in eval_batch.evaluations}

        if self.delivery_repository is not None:
            for opp in eval_batch.to_dispatch:
                fp = generate_opportunity_fingerprint(
                    opportunity_type="SUREBET",
                    canonical_event_id=opp.canonical_event_id,
                    canonical_market_key=opp.canonical_market_key,
                    legs=opp.legs,
                )
                opp_rec = self.repository.get_by_fingerprint(fp)
                eval_item = eval_by_fp.get(fp)
                is_mat = eval_item.change_evaluation.is_material if (eval_item and eval_item.change_evaluation) else False
                is_res = (eval_item.previous_status == "EXPIRED") if eval_item else False

                version = determine_lifecycle_version(
                    delivery_repository=self.delivery_repository,
                    fingerprint=fp,
                    is_material_change=is_mat,
                    is_resurrection=is_res,
                )
                opp_version_map[fp] = version

                # Mark older pending deliveries for this fingerprint as SUPERSEDED
                self.delivery_repository.mark_superseded_for_fingerprint(fp, current_version=version)

                # Persist PENDING intent for each registered consumer before transport execution
                snapshot_str = opp_rec.snapshot_json if opp_rec else json.dumps(_serialize_opportunity_snapshot(opp))
                for consumer in dispatcher.get_registered_consumers():
                    idem_key = generate_delivery_idempotency_key(fp, version, consumer.name)
                    existing_del = self.delivery_repository.get_by_idempotency_key(idem_key)
                    if existing_del is None:
                        del_record = DeliveryRecordORM(
                            id=f"del_{uuid.uuid4().hex[:16]}",
                            idempotency_key=idem_key,
                            opportunity_fingerprint=fp,
                            opportunity_id=opp.opportunity_id,
                            consumer_name=consumer.name,
                            lifecycle_version=version,
                            state=DeliveryState.PENDING.value,
                            attempt_count=0,
                            max_attempts=self.retry_config.max_attempts,
                            created_at=now,
                            payload_snapshot_json=snapshot_str,
                        )
                        self.delivery_repository.save_or_update(del_record)

        # 5. Dispatch eligible (NEW and UPDATED) opportunities downstream
        dispatch_batch_result = dispatcher.dispatch_batch(eval_batch.to_dispatch)

        delivered_cnt = 0
        failed_cnt = 0
        skipped_cnt = 0

        # 6. Delivery feedback loop: update persistent status based on concrete delivery results
        opp_by_id = {opp.opportunity_id: opp for opp in eval_batch.to_dispatch}

        for disp_res in dispatch_batch_result.results:
            opp = opp_by_id.get(disp_res.opportunity_id)
            if not opp:
                continue

            fp = generate_opportunity_fingerprint(
                opportunity_type="SUREBET",
                canonical_event_id=opp.canonical_event_id,
                canonical_market_key=opp.canonical_market_key,
                legs=opp.legs,
            )

            # If delivery_repository is configured, update individual DeliveryRecordORM records
            if self.delivery_repository is not None:
                version = opp_version_map.get(fp, 1)

                for cons_delivery in disp_res.deliveries:
                    idem_key = generate_delivery_idempotency_key(fp, version, cons_delivery.consumer_name)
                    del_rec = self.delivery_repository.get_by_idempotency_key(idem_key)
                    if del_rec is None:
                        continue

                    del_rec.last_attempt_at = now
                    if del_rec.first_attempt_at is None:
                        del_rec.first_attempt_at = now
                    del_rec.attempt_count += 1


                    category = classify_delivery_failure(cons_delivery)

                    if category == FailureCategory.SUCCESS:
                        del_rec.state = DeliveryState.DELIVERED.value
                        del_rec.delivered_at = now
                        del_rec.next_retry_at = None
                        del_rec.last_error = None
                        del_rec.last_error_category = None
                    elif category == FailureCategory.TRANSIENT_FAILURE:
                        del_rec.last_error = cons_delivery.error or "Transient delivery failure"
                        del_rec.last_error_category = FailureCategory.TRANSIENT_FAILURE.value
                        if del_rec.attempt_count < del_rec.max_attempts:
                            del_rec.state = DeliveryState.RETRY_PENDING.value
                            del_rec.next_retry_at = calculate_next_retry_time(
                                attempt=del_rec.attempt_count,
                                config=self.retry_config,
                                base_time=now,
                            )
                        else:
                            del_rec.state = DeliveryState.EXHAUSTED.value
                            del_rec.next_retry_at = None
                    elif category == FailureCategory.PERMANENT_FAILURE:
                        del_rec.state = DeliveryState.FAILED_PERMANENT.value
                        del_rec.next_retry_at = None
                        del_rec.last_error = cons_delivery.error or "Permanent delivery failure"
                        del_rec.last_error_category = FailureCategory.PERMANENT_FAILURE.value
                    else:  # CLIENT_DISABLED
                        del_rec.state = DeliveryState.SKIPPED.value
                        del_rec.next_retry_at = None
                        del_rec.last_error = (cons_delivery.metadata or {}).get("reason", "Consumer disabled")

                    self.delivery_repository.save_or_update(del_rec)

            if disp_res.status == DispatchStatus.DELIVERED:
                # Successfully delivered to active consumer(s) -> Commit ALERTED
                self.repository.record_delivery_result(
                    fingerprint=fp,
                    success=True,
                    delivery_status="DELIVERED",
                    alert_time=now,
                )
                delivered_cnt += 1
            elif disp_res.status in (DispatchStatus.FAILED, DispatchStatus.PARTIAL_FAILURE):
                # Delivery failed -> Do NOT mark ALERTED, keep current status
                self.repository.record_delivery_result(
                    fingerprint=fp,
                    success=False,
                    delivery_status="FAILED",
                    alert_time=now,
                )
                failed_cnt += 1
            else:  # SKIPPED_DUPLICATE, NO_CONSUMERS, REJECTED
                self.repository.record_delivery_result(
                    fingerprint=fp,
                    success=False,
                    delivery_status="SKIPPED",
                    alert_time=now,
                )
                skipped_cnt += 1

        return LifecycleDispatchSummary(
            evaluation_batch=eval_batch,
            dispatch_result=dispatch_batch_result,
            delivered_count=delivered_cnt,
            failed_count=failed_cnt,
            skipped_count=skipped_cnt,
            expired_records=expired_records,
        )

