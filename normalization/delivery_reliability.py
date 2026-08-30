"""
Stage 6.5: Delivery Reliability, Retry & Reconciliation

Implements:
- DeliveryState: Authoritative lifecycle states for notification delivery attempts.
- FailureCategory: Transport-independent failure classification (transient vs permanent).
- DeliveryRetryConfig: Bounded, deterministic exponential backoff retry configuration.
- Deterministic idempotency key formula for notification streams.
- Snapshot deserialization for safe offline replay.
- DeliveryReconciliationService: Crash-safe reconciliation and retry recovery after process restart.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
import json
import uuid
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from database.models import DeliveryRecordORM, OpportunityRecordORM
from database.repositories.delivery_repository import DeliveryRepository
from database.repositories.opportunity_repository import OpportunityRepository
from domain.models import MatchEvidence
from normalization.dispatcher import (
    ConsumerDeliveryResult,
    DeliveryStatus,
    DispatchableOpportunity,
    OpportunityConsumer,
    OpportunityDispatcher,
)
from normalization.market_identity import CanonicalMarketKey
from normalization.selection_identity import CanonicalSelectionKey
from normalization.surebet import SurebetLeg, SurebetOpportunity, SurebetStatus


class DeliveryState(str, Enum):
    """Authoritative lifecycle state of a notification delivery stream."""
    PENDING = "PENDING"                    # Persistent delivery intent recorded; awaiting transport execution
    DELIVERED = "DELIVERED"                # Successfully delivered and accepted by downstream consumer
    RETRY_PENDING = "RETRY_PENDING"        # Transient failure occurred; awaiting scheduled retry
    FAILED_PERMANENT = "FAILED_PERMANENT"  # Permanent/non-retryable failure (e.g. auth/config error)
    EXHAUSTED = "EXHAUSTED"                # Max retry attempts reached without successful delivery
    SKIPPED = "SKIPPED"                    # Consumer disabled or intentionally filtered
    SUPERSEDED = "SUPERSEDED"              # Superseded by a newer material version of the opportunity


class FailureCategory(str, Enum):
    """Transport-independent failure classification."""
    SUCCESS = "SUCCESS"
    TRANSIENT_FAILURE = "TRANSIENT_FAILURE"      # 5xx, 429, timeout, network error -> retryable
    PERMANENT_FAILURE = "PERMANENT_FAILURE"      # 400, 401, 403, 404, invalid auth/payload -> non-retryable
    CLIENT_DISABLED = "CLIENT_DISABLED"          # Consumer unconfigured or disabled -> skipped


@dataclass(frozen=True)
class DeliveryRetryConfig:
    """Bounded, deterministic retry policy configuration."""
    max_attempts: int = 3
    initial_delay_seconds: float = 5.0
    backoff_multiplier: float = 3.0
    max_delay_seconds: float = 300.0  # 5 minutes

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError(f"max_attempts must be >= 1, got {self.max_attempts}")
        if self.initial_delay_seconds < 0.0:
            raise ValueError(f"initial_delay_seconds must be >= 0, got {self.initial_delay_seconds}")
        if self.backoff_multiplier < 1.0:
            raise ValueError(f"backoff_multiplier must be >= 1.0, got {self.backoff_multiplier}")
        if self.max_delay_seconds < self.initial_delay_seconds:
            raise ValueError("max_delay_seconds must be >= initial_delay_seconds")


def generate_delivery_idempotency_key(
    opportunity_fingerprint: str,
    lifecycle_version: int,
    consumer_name: str,
) -> str:
    """Generates a stable, deterministic idempotency key for an alert delivery attempt.

    Formula:
      deliv:<FINGERPRINT>:v<VERSION>:<CONSUMER_NAME>
    """
    c_name = consumer_name.strip().lower()
    return f"deliv:{opportunity_fingerprint}:v{lifecycle_version}:{c_name}"


def determine_lifecycle_version(
    delivery_repository: DeliveryRepository,
    fingerprint: str,
    is_material_change: bool = False,
    is_resurrection: bool = False,
) -> int:
    """Determines the appropriate lifecycle version for an alert attempt.

    - Initial alert: version 1.
    - Subsequent material change or resurrection: increments to next version.
    - Retry of current undelivered version: preserves current version.
    """
    existing = delivery_repository.list_by_fingerprint(fingerprint)
    if not existing:
        return 1
    max_ver = max(r.lifecycle_version for r in existing)
    if is_material_change or is_resurrection:
        return max_ver + 1
    return max_ver



def classify_delivery_failure(result: ConsumerDeliveryResult) -> FailureCategory:
    """Classifies a ConsumerDeliveryResult into a transport-independent FailureCategory."""
    if result.status == DeliveryStatus.DELIVERED:
        return FailureCategory.SUCCESS

    if result.status == DeliveryStatus.SKIPPED:
        return FailureCategory.CLIENT_DISABLED

    # Status is FAILED: inspect metadata and error string
    meta = result.metadata or {}
    http_status = meta.get("http_status")
    tg_error_code = meta.get("telegram_error_code")

    # 1. Check structured HTTP / error codes
    code = tg_error_code or http_status
    if code is not None:
        try:
            code_int = int(code)
            if code_int in (429, 500, 502, 503, 504):
                return FailureCategory.TRANSIENT_FAILURE
            if code_int in (400, 401, 403, 404):
                return FailureCategory.PERMANENT_FAILURE
        except (ValueError, TypeError):
            pass

    # 2. Check error message patterns
    err = (result.error or "").lower()
    if any(k in err for k in ("timeout", "timed out", "connection", "rate limit", "too many requests", "502", "503", "504")):
        return FailureCategory.TRANSIENT_FAILURE

    if any(k in err for k in ("unauthorized", "forbidden", "bad request", "chat not found", "invalid token", "401", "403", "400", "404")):
        return FailureCategory.PERMANENT_FAILURE

    # 3. Default to TRANSIENT_FAILURE (conservative: bounded retries up to max_attempts)
    return FailureCategory.TRANSIENT_FAILURE


def calculate_next_retry_time(
    attempt: int,
    config: DeliveryRetryConfig,
    base_time: Optional[datetime] = None,
    retry_after_seconds: Optional[float] = None,
) -> datetime:
    """Calculates deterministic next retry timestamp with exponential backoff."""
    now = base_time or datetime.now(timezone.utc)
    if retry_after_seconds is not None and retry_after_seconds > 0.0:
        return now + timedelta(seconds=retry_after_seconds)

    # attempt index starting at 1
    exponent = max(0, attempt - 1)
    delay = min(config.initial_delay_seconds * (config.backoff_multiplier ** exponent), config.max_delay_seconds)
    return now + timedelta(seconds=delay)


def deserialize_opportunity_snapshot(snapshot_json: str) -> Optional[DispatchableOpportunity]:
    """Reconstructs an immutable DispatchableOpportunity from persisted snapshot JSON for replay and reconciliation."""
    try:
        data = json.loads(snapshot_json)
        if not isinstance(data, dict):
            return None

        mkt_dict = data.get("canonical_market_key", {})
        mkt_key = CanonicalMarketKey(
            market_type=mkt_dict.get("market_type", ""),
            period=mkt_dict.get("period", ""),
            scope=mkt_dict.get("scope", ""),
            line=Decimal(str(mkt_dict["line"])) if mkt_dict.get("line") is not None else None,
        )

        legs_data = data.get("legs", [])
        legs: List[SurebetLeg] = []
        for l in legs_data:
            sel_type = l.get("selection_type", "")
            sel_key = CanonicalSelectionKey(
                market_key=mkt_key,
                selection_type=sel_type,
            )
            legs.append(
                SurebetLeg(
                    selection_type=sel_type,
                    canonical_selection_key=sel_key,
                    provider=l.get("provider", ""),
                    odds=Decimal(str(l.get("odds", "1.0"))),
                    source_selection_id=l.get("source_selection_id", "rec"),
                    source_event_id=l.get("source_event_id", "rec"),
                    source_market_id=l.get("source_market_id", "rec"),
                    implied_probability=Decimal(str(l["implied_probability"])) if l.get("implied_probability") is not None else None,
                )
            )

        opp_id = data.get("opportunity_id", f"opp_{uuid.uuid4().hex[:12]}")
        canonical_event_id = data.get("canonical_event_id", "unknown_event")
        margin = Decimal(str(data.get("arbitrage_margin", "0.0")))
        prob_sum = Decimal(str(data.get("implied_probability_sum", "1.0")))
        is_mixed = bool(data.get("is_mixed_bookmakers", True))
        bookmakers = tuple(data.get("bookmakers", [leg.provider for leg in legs]))

        # Reconstruct MatchEvidence if present
        ev_dict = data.get("event_evidence")
        evidence: Optional[MatchEvidence] = None
        if ev_dict and isinstance(ev_dict, dict):
            evidence = MatchEvidence(
                source_provider=ev_dict.get("source_provider", ""),
                target_provider=ev_dict.get("target_provider", ""),
                source_event_id=ev_dict.get("source_event_id", ""),
                target_event_id=ev_dict.get("target_event_id", ""),
                decision=ev_dict.get("decision", "MATCH"),
                total_score=1.0,
                orientation="NATURAL",
                evidence=ev_dict,
            )


        source_opp = SurebetOpportunity(
            opportunity_id=opp_id,
            canonical_event_id=canonical_event_id,
            canonical_market_key=mkt_key,
            legs=tuple(legs),
            implied_probability_sum=prob_sum,
            arbitrage_margin=margin,
            is_mixed_bookmakers=is_mixed,
            bookmakers=bookmakers,
            status=SurebetStatus.SUREBET,
            event_evidence=evidence,
            market_evidence=data.get("market_evidence", {}),
        )

        return DispatchableOpportunity(
            opportunity_id=opp_id,
            canonical_event_id=canonical_event_id,
            canonical_market_key=mkt_key,
            legs=tuple(legs),
            implied_probability_sum=prob_sum,
            arbitrage_margin=margin,
            is_mixed_bookmakers=is_mixed,
            bookmakers=bookmakers,
            source_opportunity=source_opp,
            event_evidence=evidence,
            market_evidence=data.get("market_evidence", {}),
        )
    except Exception:
        return None


@dataclass
class ReconciliationSummary:
    """Detailed summary of delivery reconciliation execution."""
    scanned_count: int = 0
    attempted_count: int = 0
    delivered_count: int = 0
    retried_count: int = 0
    exhausted_count: int = 0
    permanent_failed_count: int = 0
    superseded_count: int = 0
    skipped_count: int = 0
    corrupted_count: int = 0


class DeliveryReconciliationService:
    """Reconciliation and retry recovery service.

    Recovers persisted pending and retryable delivery attempts following process restarts
    or transient consumer delivery errors.
    """

    def __init__(
        self,
        delivery_repository: DeliveryRepository,
        opportunity_repository: OpportunityRepository,
        dispatcher: OpportunityDispatcher,
        retry_config: Optional[DeliveryRetryConfig] = None,
    ) -> None:
        self.delivery_repository = delivery_repository
        self.opportunity_repository = opportunity_repository
        self.dispatcher = dispatcher
        self.retry_config = retry_config or DeliveryRetryConfig()

    def reconcile(
        self,
        current_time: Optional[datetime] = None,
        limit: int = 100,
    ) -> ReconciliationSummary:
        """Executes a reconciliation pass over due pending and retryable delivery records."""
        now = current_time or datetime.now(timezone.utc)
        records = self.delivery_repository.list_pending_or_retryable(current_time=now, limit=limit)

        summary = ReconciliationSummary(scanned_count=len(records))
        consumer_map = {c.name: c for c in self.dispatcher.get_registered_consumers()}

        for record in records:
            # 1. Verify corresponding opportunity record in database
            opp_record = self.opportunity_repository.get_by_fingerprint(record.opportunity_fingerprint)
            if opp_record is None or opp_record.status == "EXPIRED":
                # Opportunity no longer exists or has expired -> Mark EXHAUSTED / SUPERSEDED
                record.state = DeliveryState.EXHAUSTED.value
                record.next_retry_at = None
                record.last_error = "Opportunity expired or removed from database"
                self.delivery_repository.save_or_update(record)
                summary.exhausted_count += 1
                continue

            # 2. Check if a newer lifecycle version has superseded this record
            current_opp_version = (opp_record.alert_count or 0) + 1
            if record.lifecycle_version < (opp_record.alert_count or 0):
                record.state = DeliveryState.SUPERSEDED.value
                record.next_retry_at = None
                record.last_error = f"Superseded by newer delivered lifecycle version {opp_record.alert_count}"
                self.delivery_repository.save_or_update(record)
                summary.superseded_count += 1
                continue

            # 3. Reconstruct dispatchable opportunity from persisted snapshot
            dispatchable_opp = deserialize_opportunity_snapshot(record.payload_snapshot_json)
            if dispatchable_opp is None:
                record.state = DeliveryState.FAILED_PERMANENT.value
                record.next_retry_at = None
                record.last_error = "Corrupted or invalid snapshot JSON in delivery record"
                record.last_error_category = FailureCategory.PERMANENT_FAILURE.value
                self.delivery_repository.save_or_update(record)
                summary.corrupted_count += 1
                summary.permanent_failed_count += 1
                continue

            # 4. Find designated consumer
            consumer = consumer_map.get(record.consumer_name)
            if consumer is None:
                # Consumer is not currently registered in dispatcher
                record.state = DeliveryState.SKIPPED.value
                record.next_retry_at = None
                record.last_error = f"Consumer '{record.consumer_name}' not registered in dispatcher"
                self.delivery_repository.save_or_update(record)
                summary.skipped_count += 1
                continue

            # 5. Execute delivery attempt
            summary.attempted_count += 1
            if record.first_attempt_at is None:
                record.first_attempt_at = now
            record.last_attempt_at = now

            try:
                delivery_res = consumer.consume(dispatchable_opp)
            except Exception as exc:
                delivery_res = ConsumerDeliveryResult(
                    consumer_name=consumer.name,
                    status=DeliveryStatus.FAILED,
                    error=f"{type(exc).__name__}: {exc}",
                )

            failure_category = classify_delivery_failure(delivery_res)

            if failure_category == FailureCategory.SUCCESS:
                record.state = DeliveryState.DELIVERED.value
                record.delivered_at = now
                record.attempt_count += 1
                record.next_retry_at = None
                record.last_error = None
                record.last_error_category = None
                self.delivery_repository.save_or_update(record)

                # Commit ALERTED state to opportunity record
                self.opportunity_repository.record_delivery_result(
                    fingerprint=record.opportunity_fingerprint,
                    success=True,
                    delivery_status="DELIVERED",
                    alert_time=now,
                )
                summary.delivered_count += 1

            elif failure_category == FailureCategory.TRANSIENT_FAILURE:
                record.attempt_count += 1
                record.last_error = delivery_res.error or "Transient delivery failure"
                record.last_error_category = FailureCategory.TRANSIENT_FAILURE.value

                if record.attempt_count < record.max_attempts:
                    record.state = DeliveryState.RETRY_PENDING.value
                    record.next_retry_at = calculate_next_retry_time(
                        attempt=record.attempt_count,
                        config=self.retry_config,
                        base_time=now,
                    )
                    summary.retried_count += 1
                else:
                    record.state = DeliveryState.EXHAUSTED.value
                    record.next_retry_at = None
                    summary.exhausted_count += 1

                self.delivery_repository.save_or_update(record)
                # Keep opportunity in NEW or UPDATED (not ALERTED)
                self.opportunity_repository.record_delivery_result(
                    fingerprint=record.opportunity_fingerprint,
                    success=False,
                    delivery_status="FAILED",
                    alert_time=now,
                )

            elif failure_category == FailureCategory.PERMANENT_FAILURE:
                record.attempt_count += 1
                record.state = DeliveryState.FAILED_PERMANENT.value
                record.next_retry_at = None
                record.last_error = delivery_res.error or "Permanent delivery failure"
                record.last_error_category = FailureCategory.PERMANENT_FAILURE.value
                self.delivery_repository.save_or_update(record)
                self.opportunity_repository.record_delivery_result(
                    fingerprint=record.opportunity_fingerprint,
                    success=False,
                    delivery_status="FAILED",
                    alert_time=now,
                )
                summary.permanent_failed_count += 1

            else:  # CLIENT_DISABLED
                record.state = DeliveryState.SKIPPED.value
                record.next_retry_at = None
                record.last_error = (delivery_res.metadata or {}).get("reason", "Consumer disabled")
                self.delivery_repository.save_or_update(record)
                summary.skipped_count += 1

        return summary
