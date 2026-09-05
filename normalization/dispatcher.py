"""
Stage 6.1: Opportunity Dispatcher & Delivery Boundary

Provides the authoritative, fail-safe boundary between the completed Stage 5.5
Surebet Detection Engine and downstream delivery consumers (e.g. Telegram alerts,
persistence, UI, monitoring).

Core Principles:
- Single Responsibility: Validates boundary invariants and safely delivers opportunities.
- No Second Engine: Verifies consistency of Stage 5.5 SurebetOpportunity objects without recalculating.
- Consumer Agnostic: Dispatches to registered OpportunityConsumer implementations.
- Fault Isolation: Protects pipeline; individual consumer failures never crash the dispatcher.
- Intra-Run Deduplication: Prevents delivering duplicate opportunity IDs within the same run.
- Immutability & Determinism: Input order and consumer registration order are strictly preserved.
- Full Lineage Preservation: Retains provider IDs, canonical keys, native odds, and match evidence.
- Zero External Coupling: No Telegram APIs, persistence, UI, automatic betting, or bookmaker calls.
"""

import time
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Any, Callable, Dict, Iterable, List, Optional, Protocol, Sequence, Set, Tuple, Union

from domain.models import MatchEvidence
from normalization.market_identity import CanonicalMarketKey
from normalization.selection_identity import CanonicalSelectionKey
from normalization.surebet import (
    SurebetDetectionResult,
    SurebetLeg,
    SurebetOpportunity,
    SurebetStatus,
)


class DeliveryStatus(str, Enum):
    """Status of an individual consumer delivery attempt."""
    DELIVERED = "DELIVERED"          # Consumer successfully processed/accepted the opportunity
    FAILED = "FAILED"                # Consumer encountered an error or raised an exception
    SKIPPED = "SKIPPED"              # Consumer intentionally did not consume (e.g. consumer-level filter)


class DispatchStatus(str, Enum):
    """Overall dispatch outcome status for an opportunity."""
    DELIVERED = "DELIVERED"                  # Dispatched and successfully accepted by all active consumers
    REJECTED = "REJECTED"                    # Dispatcher rejected the opportunity at the boundary
    SKIPPED = "SKIPPED"                      # All consumers intentionally skipped (no delivery attempted/succeeded)
    SKIPPED_DUPLICATE = "SKIPPED_DUPLICATE"  # Duplicate opportunity ID encountered within the same run
    PARTIAL_FAILURE = "PARTIAL_FAILURE"      # Delivered to some consumers, but failed on others
    FAILED = "FAILED"                        # Failed across all registered consumers
    NO_CONSUMERS = "NO_CONSUMERS"            # Valid opportunity, but no consumers were registered


@dataclass(frozen=True)
class DispatchableOpportunity:
    """Immutable, provider-independent representation of a validated surebet opportunity.

    Acts as the clean data container provided to registered consumers.
    """
    opportunity_id: str
    canonical_event_id: str
    canonical_market_key: CanonicalMarketKey
    legs: Tuple[SurebetLeg, ...]
    implied_probability_sum: Decimal
    arbitrage_margin: Decimal
    is_mixed_bookmakers: bool
    bookmakers: Tuple[str, ...]
    source_opportunity: Optional[SurebetOpportunity] = None
    event_evidence: Optional[MatchEvidence] = None
    market_evidence: Dict[str, Any] = field(default_factory=dict)
    opportunity_type: str = "SUREBET"
    source_valuebet: Optional[Any] = None

    def to_dict(self) -> Dict[str, Any]:
        """Serializes the dispatchable opportunity into a deterministic, Decimal-safe dictionary."""
        d = {
            "opportunity_id": self.opportunity_id,
            "opportunity_type": self.opportunity_type,
            "canonical_event_id": self.canonical_event_id,
            "canonical_market_key": {
                "market_type": self.canonical_market_key.market_type,
                "period": self.canonical_market_key.period,
                "scope": self.canonical_market_key.scope,
                "line": str(self.canonical_market_key.line) if self.canonical_market_key.line is not None else None,
                "key_string": self.canonical_market_key.to_key_string(),
            },
            "implied_probability_sum": str(self.implied_probability_sum),
            "arbitrage_margin": str(self.arbitrage_margin),
            "arbitrage_margin_pct": str(self.arbitrage_margin * Decimal("100")),
            "is_mixed_bookmakers": self.is_mixed_bookmakers,
            "bookmakers": list(self.bookmakers),
            "legs": [
                {
                    "selection_type": leg.selection_type,
                    "canonical_selection_key": {
                        "selection_type": leg.canonical_selection_key.selection_type,
                        "market_type": leg.canonical_selection_key.market_key.market_type,
                        "period": leg.canonical_selection_key.market_key.period,
                        "scope": leg.canonical_selection_key.market_key.scope,
                        "line": str(leg.canonical_selection_key.market_key.line) if leg.canonical_selection_key.market_key.line is not None else None,
                    },
                    "provider": leg.provider,
                    "odds": str(leg.odds),
                    "source_selection_id": leg.source_selection_id,
                    "source_event_id": leg.source_event_id,
                    "source_market_id": leg.source_market_id,
                    "implied_probability": str(leg.implied_probability) if leg.implied_probability is not None else None,
                }
                for leg in self.legs
            ],
        }
        if self.source_valuebet is not None and hasattr(self.source_valuebet, "value_percent"):
            d["value_percent"] = str(self.source_valuebet.value_percent)
            d["fair_odds"] = str(self.source_valuebet.fair_odds)
            d["fair_probability"] = str(self.source_valuebet.fair_probability)
            d["reference_source"] = self.source_valuebet.reference_source
        return d


@dataclass(frozen=True)
class ConsumerDeliveryResult:
    """Auditable result of a single consumer's execution for an opportunity."""
    consumer_name: str
    status: DeliveryStatus
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DispatchResult:
    """Auditable result for a single opportunity dispatch operation."""
    opportunity_id: str
    status: DispatchStatus
    deliveries: Tuple[ConsumerDeliveryResult, ...] = ()
    opportunity: Optional[DispatchableOpportunity] = None
    rejection_reason: Optional[str] = None
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class DispatchMetrics:
    """Telemetry and summary metrics for an opportunity dispatch execution."""
    input_opportunity_count: int = 0
    valid_opportunity_count: int = 0
    rejected_opportunity_count: int = 0
    duplicate_count: int = 0

    consumer_count: int = 0

    delivery_attempt_count: int = 0
    delivered_count: int = 0
    failed_count: int = 0
    skipped_count: int = 0

    dispatch_duration_ms: float = 0.0
    per_consumer: Dict[str, Dict[str, int]] = field(default_factory=dict)


@dataclass(frozen=True)
class BatchDispatchResult:
    """Aggregate result container for a batch dispatch execution."""
    results: Tuple[DispatchResult, ...] = ()
    metrics: DispatchMetrics = field(default_factory=DispatchMetrics)
    dispatch_run_id: Optional[str] = None


class OpportunityConsumer(Protocol):
    """Protocol defining the contract for all opportunity delivery consumers."""

    @property
    def name(self) -> str:
        """Unique identifier name for this consumer."""
        ...

    def consume(
        self,
        opportunity: DispatchableOpportunity,
    ) -> ConsumerDeliveryResult:
        """Consumes a validated, immutable opportunity."""
        ...


class InMemoryOpportunityConsumer:
    """In-memory opportunity consumer for testing and verification without side effects."""

    def __init__(self, name: str = "in_memory") -> None:
        self._name = name
        self.received_opportunities: List[DispatchableOpportunity] = []

    @property
    def name(self) -> str:
        return self._name

    def consume(
        self,
        opportunity: DispatchableOpportunity,
    ) -> ConsumerDeliveryResult:
        self.received_opportunities.append(opportunity)
        return ConsumerDeliveryResult(
            consumer_name=self.name,
            status=DeliveryStatus.DELIVERED,
        )


class ConsoleOpportunityConsumer:
    """Minimal human-readable console consumer for debugging and inspection."""

    def __init__(
        self,
        name: str = "console",
        sink: Optional[Callable[[str], None]] = None,
    ) -> None:
        self._name = name
        self._sink = sink or print

    @property
    def name(self) -> str:
        return self._name

    def consume(
        self,
        opportunity: DispatchableOpportunity,
    ) -> ConsumerDeliveryResult:
        try:
            msg = (
                f"[SUREBET DISPATCH] ID={opportunity.opportunity_id} | "
                f"Event={opportunity.canonical_event_id} | "
                f"Market={opportunity.canonical_market_key.to_key_string()} | "
                f"Margin={(opportunity.arbitrage_margin * Decimal('100')):.2f}% | "
                f"Books={','.join(opportunity.bookmakers)}"
            )
            self._sink(msg)
            return ConsumerDeliveryResult(
                consumer_name=self.name,
                status=DeliveryStatus.DELIVERED,
            )
        except Exception as exc:
            return ConsumerDeliveryResult(
                consumer_name=self.name,
                status=DeliveryStatus.FAILED,
                error=f"{type(exc).__name__}: {exc}",
            )


def validate_dispatchable_opportunity(
    opportunity: Any,
) -> Tuple[bool, Optional[str]]:
    """Performs delivery-boundary consistency validation on an incoming opportunity.

    Ensures malformed or inconsistent opportunities are rejected before reaching consumers.
    This is an integrity boundary check, not a second surebet or valuebet calculator.
    """
    if isinstance(opportunity, DispatchableOpportunity):
        return True, None

    is_val_cand = hasattr(opportunity, "value_percent") and (hasattr(opportunity, "reference_fair_probability") or hasattr(opportunity, "fair_probability"))
    if is_val_cand:
        # ValueBetCandidate boundary validation
        if not getattr(opportunity, "candidate_id", None) or not isinstance(opportunity.candidate_id, str):
            return False, "ValueBetCandidate missing or empty candidate_id"
        if not getattr(opportunity, "canonical_event_id", None) or not isinstance(opportunity.canonical_event_id, str):
            return False, "ValueBetCandidate missing or empty canonical_event_id"
        if not isinstance(opportunity.bookmaker_odds, Decimal) or opportunity.bookmaker_odds <= Decimal("1.0"):
            return False, f"ValueBetCandidate has invalid bookmaker_odds: {opportunity.bookmaker_odds}"
        fair_prob = getattr(opportunity, "reference_fair_probability", getattr(opportunity, "fair_probability", None))
        if not isinstance(fair_prob, Decimal) or fair_prob <= Decimal("0.0") or fair_prob >= Decimal("1.0"):
            return False, f"ValueBetCandidate has invalid fair_probability: {fair_prob}"
        if not isinstance(opportunity.value_percent, Decimal) or opportunity.value_percent < Decimal("0.0"):
            return False, f"ValueBetCandidate has negative or invalid value_percent: {opportunity.value_percent}"
        return True, None

    if not isinstance(opportunity, SurebetOpportunity):
        return False, f"Expected SurebetOpportunity instance, got '{type(opportunity).__name__}'"

    if opportunity.status != SurebetStatus.SUREBET:
        return False, f"Opportunity status is '{opportunity.status}', must be '{SurebetStatus.SUREBET.value}'"

    if not opportunity.opportunity_id or not isinstance(opportunity.opportunity_id, str):
        return False, "Opportunity missing or empty opportunity_id"

    if not opportunity.canonical_event_id or not isinstance(opportunity.canonical_event_id, str):
        return False, "Opportunity missing or empty canonical_event_id"

    if not isinstance(opportunity.canonical_market_key, CanonicalMarketKey):
        return False, "Opportunity missing or invalid canonical_market_key"

    if not opportunity.legs or not isinstance(opportunity.legs, (tuple, list)):
        return False, "Opportunity has empty or non-sequence legs"

    mkt_key = opportunity.canonical_market_key
    seen_selection_types: Set[str] = set()

    for idx, leg in enumerate(opportunity.legs):
        if not isinstance(leg, SurebetLeg):
            return False, f"Leg #{idx} is not a SurebetLeg instance"

        if not isinstance(leg.odds, Decimal) or leg.odds <= Decimal("1.0"):
            return False, f"Leg #{idx} has invalid odds: {leg.odds} (must be Decimal > 1.0)"

        if not leg.provider or not isinstance(leg.provider, str):
            return False, f"Leg #{idx} missing provider lineage"

        if not leg.source_selection_id or not isinstance(leg.source_selection_id, str):
            return False, f"Leg #{idx} missing source_selection_id lineage"

        if not isinstance(leg.canonical_selection_key, CanonicalSelectionKey):
            return False, f"Leg #{idx} missing canonical_selection_key"

        if not leg.selection_type or not isinstance(leg.selection_type, str):
            return False, f"Leg #{idx} missing selection_type"

        # Check market dimension consistency between leg and parent market key
        sel_key = leg.canonical_selection_key
        if sel_key.market_key.market_type != mkt_key.market_type:
            return False, f"Leg #{idx} market_type '{sel_key.market_key.market_type}' does not match market key '{mkt_key.market_type}'"

        if sel_key.market_key.period != mkt_key.period:
            return False, f"Leg #{idx} period '{sel_key.market_key.period}' does not match market key '{mkt_key.period}'"

        if sel_key.market_key.scope != mkt_key.scope:
            return False, f"Leg #{idx} scope '{sel_key.market_key.scope}' does not match market key '{mkt_key.scope}'"

        if sel_key.market_key.line != mkt_key.line:
            return False, f"Leg #{idx} line '{sel_key.market_key.line}' does not match market key '{mkt_key.line}'"

        if leg.selection_type in seen_selection_types:
            return False, f"Duplicate selection_type '{leg.selection_type}' found in legs"
        seen_selection_types.add(leg.selection_type)

    if not isinstance(opportunity.implied_probability_sum, Decimal):
        return False, "implied_probability_sum must be a Decimal"

    if opportunity.implied_probability_sum <= Decimal("0.0") or opportunity.implied_probability_sum >= Decimal("1.0"):
        return False, f"implied_probability_sum '{opportunity.implied_probability_sum}' is not strictly in (0.0, 1.0)"

    if not isinstance(opportunity.arbitrage_margin, Decimal) or opportunity.arbitrage_margin <= Decimal("0.0"):
        return False, f"arbitrage_margin '{opportunity.arbitrage_margin}' must be Decimal > 0.0"

    return True, None


class OpportunityDispatcher:
    """Provider-independent dispatcher and boundary for validated surebet opportunities.

    Features:
    - Consumer registration with deterministic invocation order.
    - Delivery-boundary validation.
    - Fault isolation (consumer exceptions are trapped and logged).
    - Intra-run deduplication (duplicate opportunity IDs in the same batch are handled deterministically).
    - Comprehensive auditable dispatch metrics.
    """

    def __init__(
        self,
        consumers: Optional[Sequence[OpportunityConsumer]] = None,
    ) -> None:
        self._consumers: List[OpportunityConsumer] = []
        if consumers:
            for consumer in consumers:
                self.register_consumer(consumer)

    def register_consumer(self, consumer: OpportunityConsumer) -> None:
        """Registers a delivery consumer in deterministic order."""
        if not hasattr(consumer, "name") or not hasattr(consumer, "consume"):
            raise TypeError("Consumer must implement OpportunityConsumer protocol (must have 'name' and 'consume')")
        if any(c.name == consumer.name for c in self._consumers):
            raise ValueError(f"Consumer with name '{consumer.name}' is already registered")
        self._consumers.append(consumer)

    def unregister_consumer(self, consumer_name: str) -> bool:
        """Unregisters a delivery consumer by name."""
        for idx, c in enumerate(self._consumers):
            if c.name == consumer_name:
                self._consumers.pop(idx)
                return True
        return False

    def get_registered_consumers(self) -> Tuple[OpportunityConsumer, ...]:
        """Returns the tuple of registered consumers in deterministic registration order."""
        return tuple(self._consumers)

    def _create_dispatchable_opportunity(
        self,
        opportunity: Any,
    ) -> DispatchableOpportunity:
        """Converts a validated SurebetOpportunity or ValueBetCandidate into an immutable DispatchableOpportunity."""
        if isinstance(opportunity, DispatchableOpportunity):
            return opportunity

        is_val_cand = hasattr(opportunity, "value_percent") and (hasattr(opportunity, "reference_fair_probability") or hasattr(opportunity, "fair_probability"))
        if is_val_cand:
            # ValueBetCandidate
            mkt_key = CanonicalMarketKey(
                market_type=opportunity.market_type,
                period=getattr(opportunity, "period", "FULL_TIME"),
                scope=getattr(opportunity, "scope", "MATCH"),
                line=opportunity.line,
            )
            sel_key = CanonicalSelectionKey(
                market_key=mkt_key,
                selection_type=opportunity.selection_type,
            )
            leg = SurebetLeg(
                selection_type=opportunity.selection_type,
                canonical_selection_key=sel_key,
                provider=opportunity.bookmaker,
                odds=opportunity.bookmaker_odds,
                source_selection_id=f"val_{opportunity.selection_type}",
                source_event_id=opportunity.canonical_event_id,
                source_market_id=f"val_{opportunity.market_type}",
                implied_probability=Decimal("1") / opportunity.bookmaker_odds if opportunity.bookmaker_odds > Decimal("0") else Decimal("0"),
            )
            ev_name = getattr(opportunity, "event_name", "")
            home_team = getattr(opportunity, "home_team", None) or (ev_name.split(" vs ")[0] if " vs " in ev_name else ev_name)
            away_team = getattr(opportunity, "away_team", None) or (ev_name.split(" vs ")[1] if " vs " in ev_name else "")
            start_time = getattr(opportunity, "kickoff", None) or getattr(opportunity, "kickoff_time", None)

            evidence = MatchEvidence(
                source_provider=opportunity.bookmaker,
                target_provider=opportunity.reference_source,
                source_event_id=opportunity.canonical_event_id,
                target_event_id=opportunity.canonical_event_id,
                decision="MATCHED",
                total_score=1.0,
                orientation="DIRECT",
                evidence={
                    "home_team": home_team,
                    "away_team": away_team,
                    "competition_name": getattr(opportunity, "competition_name", None),
                    "start_time": start_time,
                }
            )
            fair_prob = getattr(opportunity, "reference_fair_probability", getattr(opportunity, "fair_probability", Decimal("0.5")))
            val_edge = getattr(opportunity, "value_edge", opportunity.value_percent / Decimal("100"))

            return DispatchableOpportunity(
                opportunity_id=opportunity.candidate_id,
                canonical_event_id=opportunity.canonical_event_id,
                canonical_market_key=mkt_key,
                legs=(leg,),
                implied_probability_sum=fair_prob,
                arbitrage_margin=val_edge,
                is_mixed_bookmakers=False,
                bookmakers=(opportunity.bookmaker,),
                source_opportunity=None,
                event_evidence=evidence,
                opportunity_type="VALUEBET",
                source_valuebet=opportunity,
            )

        return DispatchableOpportunity(
            opportunity_id=opportunity.opportunity_id,
            canonical_event_id=opportunity.canonical_event_id,
            canonical_market_key=opportunity.canonical_market_key,
            legs=opportunity.legs,
            implied_probability_sum=opportunity.implied_probability_sum,
            arbitrage_margin=opportunity.arbitrage_margin,
            is_mixed_bookmakers=opportunity.is_mixed_bookmakers,
            bookmakers=opportunity.bookmakers,
            source_opportunity=opportunity,
            event_evidence=opportunity.event_evidence,
            market_evidence=opportunity.market_evidence,
            opportunity_type="SUREBET",
            source_valuebet=None,
        )

    def dispatch(
        self,
        opportunity: Union[SurebetOpportunity, SurebetDetectionResult],
    ) -> DispatchResult:
        """Dispatches a single opportunity or extracts the first opportunity from a detection result."""
        if isinstance(opportunity, SurebetDetectionResult):
            batch_res = self.dispatch_batch(opportunity.opportunities)
            if batch_res.results:
                return batch_res.results[0]
            return DispatchResult(
                opportunity_id="none",
                status=DispatchStatus.REJECTED,
                rejection_reason="SurebetDetectionResult contains no opportunities",
            )
        batch_res = self.dispatch_batch([opportunity])
        return batch_res.results[0]

    def dispatch_batch(
        self,
        opportunities: Union[Iterable[SurebetOpportunity], SurebetDetectionResult],
        dispatch_run_id: Optional[str] = None,
    ) -> BatchDispatchResult:
        """Batch dispatches opportunities across all registered consumers with fault isolation and metrics."""
        t0 = time.perf_counter()

        opp_list: List[SurebetOpportunity]
        if isinstance(opportunities, SurebetDetectionResult):
            opp_list = list(opportunities.opportunities)
        else:
            opp_list = list(opportunities)

        results: List[DispatchResult] = []
        seen_opportunity_ids: Set[str] = set()

        # Initialize telemetry metrics
        metrics = DispatchMetrics(
            input_opportunity_count=len(opp_list),
            consumer_count=len(self._consumers),
        )
        for consumer in self._consumers:
            metrics.per_consumer[consumer.name] = {
                "attempts": 0,
                "delivered": 0,
                "failed": 0,
                "skipped": 0,
            }

        for opp in opp_list:
            opp_id = getattr(opp, "opportunity_id", None) or getattr(opp, "candidate_id", None)
            # 1. Check if opp is valid object with opportunity ID
            if not opp_id or not isinstance(opp_id, str):
                opp_id_str = str(opp_id) if opp_id is not None else "malformed"
                metrics.rejected_opportunity_count += 1
                results.append(
                    DispatchResult(
                        opportunity_id=opp_id_str,
                        status=DispatchStatus.REJECTED,
                        rejection_reason="Malformed opportunity object or missing opportunity_id",
                    )
                )
                continue

            # 2. Check Intra-run deduplication
            if opp_id in seen_opportunity_ids:
                metrics.duplicate_count += 1
                results.append(
                    DispatchResult(
                        opportunity_id=opp_id,
                        status=DispatchStatus.SKIPPED_DUPLICATE,
                        rejection_reason=f"Duplicate opportunity_id '{opp_id}' in same dispatch run",
                    )
                )
                continue

            seen_opportunity_ids.add(opp_id)

            # 3. Delivery Boundary Validation
            is_valid, rejection_reason = validate_dispatchable_opportunity(opp)
            if not is_valid:
                metrics.rejected_opportunity_count += 1
                results.append(
                    DispatchResult(
                        opportunity_id=opp_id,
                        status=DispatchStatus.REJECTED,
                        rejection_reason=rejection_reason,
                    )
                )
                continue

            metrics.valid_opportunity_count += 1
            dispatchable_opp = self._create_dispatchable_opportunity(opp)

            # 4. Handle case when no consumers are registered
            if len(self._consumers) == 0:
                results.append(
                    DispatchResult(
                        opportunity_id=opp_id,
                        status=DispatchStatus.NO_CONSUMERS,
                        opportunity=dispatchable_opp,
                        rejection_reason="No consumers registered",
                    )
                )
                continue

            # 5. Deliver to each registered consumer in deterministic order
            deliveries: List[ConsumerDeliveryResult] = []
            delivered_cnt = 0
            failed_cnt = 0
            skipped_cnt = 0

            for consumer in self._consumers:
                metrics.delivery_attempt_count += 1
                metrics.per_consumer[consumer.name]["attempts"] += 1

                try:
                    res = consumer.consume(dispatchable_opp)
                    if not isinstance(res, ConsumerDeliveryResult):
                        res = ConsumerDeliveryResult(
                            consumer_name=consumer.name,
                            status=DeliveryStatus.FAILED,
                            error=f"Consumer returned invalid result type: '{type(res).__name__}'",
                        )
                except Exception as exc:
                    # Fault isolation: consumer exception is captured and never crashes the dispatcher
                    res = ConsumerDeliveryResult(
                        consumer_name=consumer.name,
                        status=DeliveryStatus.FAILED,
                        error=f"{type(exc).__name__}: {exc}",
                    )

                deliveries.append(res)

                if res.status == DeliveryStatus.DELIVERED:
                    delivered_cnt += 1
                    metrics.delivered_count += 1
                    metrics.per_consumer[consumer.name]["delivered"] += 1
                elif res.status == DeliveryStatus.FAILED:
                    failed_cnt += 1
                    metrics.failed_count += 1
                    metrics.per_consumer[consumer.name]["failed"] += 1
                elif res.status == DeliveryStatus.SKIPPED:
                    skipped_cnt += 1
                    metrics.skipped_count += 1
                    metrics.per_consumer[consumer.name]["skipped"] += 1

            # 6. Determine overall dispatch status for this opportunity
            if failed_cnt == 0 and delivered_cnt > 0:
                overall_status = DispatchStatus.DELIVERED
            elif delivered_cnt == 0 and failed_cnt > 0:
                overall_status = DispatchStatus.FAILED
            elif delivered_cnt > 0 and failed_cnt > 0:
                overall_status = DispatchStatus.PARTIAL_FAILURE
            elif skipped_cnt == len(self._consumers):
                overall_status = DispatchStatus.SKIPPED
            else:
                overall_status = DispatchStatus.SKIPPED

            results.append(
                DispatchResult(
                    opportunity_id=opp_id,
                    status=overall_status,
                    deliveries=tuple(deliveries),
                    opportunity=dispatchable_opp,
                )
            )

        duration_ms = (time.perf_counter() - t0) * 1000.0
        metrics.dispatch_duration_ms = duration_ms

        return BatchDispatchResult(
            results=tuple(results),
            metrics=metrics,
            dispatch_run_id=dispatch_run_id,
        )
