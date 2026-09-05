"""
Scan Orchestration Data Models & Result Containers
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from normalization.base_normalizer import NormalizedGraph
from normalization.delivery_reliability import ReconciliationSummary
from normalization.engine import NormalizationResult
from normalization.lifecycle import LifecycleDispatchSummary
from normalization.surebet import SurebetDetectionResult
from normalization.validation_pipeline import CrossBookmakerValidationResult
from providers.base.provider_result import ProviderResult


class CycleStatus(str, Enum):
    """Authoritative status of a single scan cycle execution."""
    SUCCESS = "SUCCESS"    # All configured stages executed cleanly
    PARTIAL = "PARTIAL"    # Degraded provider/data availability; partial valid execution
    FAILED = "FAILED"      # Critical infrastructure failure or total provider unavailability


class TelemetryMode(str, Enum):
    """Telemetry collection and diagnostic detail fidelity mode."""
    MINIMAL = "MINIMAL"        # Core counters, cycle status, timing summary
    STANDARD = "STANDARD"      # Full stage diagnostics, cardinality tracking, timing breakdown
    FULL_DEBUG = "FULL_DEBUG"  # Full stage diagnostics, object sample breakdowns, complete trace logging


class MarketEvaluationState(str, Enum):
    """Authoritative explicit terminal evaluation state for a matched canonical market."""
    EVALUATED = "EVALUATED"          # Evaluated completely with valid odds; no arbitrage opportunity
    OPPORTUNITY = "OPPORTUNITY"      # Evaluated completely; valid opportunity produced (e.g. surebet)
    REJECTED = "REJECTED"            # Excluded during evaluation due to incomplete selections, invalid odds/lines
    NOT_EVALUATED = "NOT_EVALUATED"  # Excluded prior to evaluation (unsupported market, duplicate key, 0 selections)


class EvaluationExclusionReason(str, Enum):
    """Deterministic classification codes for markets excluded from evaluation or opportunities."""
    INCOMPLETE_SELECTIONS = "INCOMPLETE_SELECTIONS"        # Missing one or more required partition outcomes (e.g. only 1 of 3 legs)
    INVALID_ODDS = "INVALID_ODDS"                          # Odds <= 1.0, non-numeric, inactive, or suspended
    UNSUPPORTED_MARKET = "UNSUPPORTED_MARKET"              # Market type or scope not supported for arbitrage
    LINE_INVALID = "LINE_INVALID"                          # Line is None or malformed on a line-dependent market
    INVALID_MARKET_IDENTITY = "INVALID_MARKET_IDENTITY"    # Scope/period/participant role/player identity malformed on canonical key
    CROSS_MARKET_CONTAMINATION = "CROSS_MARKET_CONTAMINATION" # Legs originate from multiple distinct source market IDs
    SELECTION_MISMATCH = "SELECTION_MISMATCH"              # Selections could not be aligned between providers
    PROVIDER_DEGRADED = "PROVIDER_DEGRADED"                # Provider returned degraded / partial payload
    EVALUATOR_POLICY = "EVALUATOR_POLICY"                  # Rejection due to evaluation quality constraints
    DUPLICATE_CANONICAL_MARKET = "DUPLICATE_CANONICAL_MARKET" # Multiple provider market pairs collapsed into single canonical key
    ZERO_COMPARABLE_SELECTIONS = "ZERO_COMPARABLE_SELECTIONS" # Matched market pair produced 0 comparable selection pairs
    INCOMPLETE_EVENT_IDENTITY = "INCOMPLETE_EVENT_IDENTITY" # Event lacks valid participant / home / away identity
    OTHER = "OTHER"


@dataclass(frozen=True)
class MatchedMarketEvaluationRecord:
    """Explicit deterministic terminal record for a single matched market lineage."""
    canonical_event_id: str
    canonical_market_key: str
    state: MarketEvaluationState
    reason: Optional[EvaluationExclusionReason] = None
    market_type: str = ""
    source_provider: Optional[str] = None
    target_provider: Optional[str] = None
    opportunity_id: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class StageTiming:
    """Detailed stage-by-stage timing measurements in seconds."""
    acquisition_seconds: float = 0.0
    normalization_seconds: float = 0.0
    matching_seconds: float = 0.0
    detection_seconds: float = 0.0
    valuebet_seconds: float = 0.0
    lifecycle_seconds: float = 0.0
    dispatch_seconds: float = 0.0
    reconciliation_seconds: float = 0.0
    total_duration_seconds: float = 0.0


@dataclass(frozen=True)
class ResourceBudget:
    """Explicit safety limits and tripwires for a production scan cycle."""
    max_duration_seconds: Optional[float] = None
    max_http_requests: Optional[int] = None
    max_detail_requests: Optional[int] = None
    max_events: Optional[int] = None
    max_memory_mb: Optional[float] = None


@dataclass
class ResourceMetrics:
    """Measured resource consumption across all pipeline stages."""
    total_http_requests: int = 0
    detail_http_requests: int = 0
    response_bytes_total: int = 0
    events_discovered: int = 0
    events_selected: int = 0
    popular_events_discovered: int = 0
    popular_events_selected: int = 0
    events_parsed: int = 0
    normalized_graphs: int = 0
    markets_discovered: int = 0
    markets_normalized: int = 0
    markets_matched: int = 0
    matched_events: int = 0
    cross_bookmaker_overlap_rate: float = 0.0
    markets_evaluated: int = 0
    selections_evaluated: int = 0
    peak_memory_mb: float = 0.0
    # Stage 10.9 Matched-Event Detail Prioritization Telemetry
    detail_candidates_available: int = 0
    detail_candidates_overlap: int = 0
    detail_events_selected: int = 0
    detail_events_overlap_selected: int = 0
    detail_overlap_selection_rate: float = 0.0
    multi_market_expected_events: int = 0
    market_coverage_breakdown: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    # Stage 10.10 & Stage 13 Evaluation Coverage & Opportunity Telemetry
    matched_markets_total: int = 0
    evaluation_candidates_total: int = 0
    evaluated_markets_total: int = 0
    evaluation_excluded_total: int = 0
    evaluation_exclusion_breakdown: Dict[str, int] = field(default_factory=dict)
    evaluation_coverage_rate: float = 0.0
    # Stage 13 Explicit Terminal Evaluation Telemetry
    surebet_candidates: int = 0
    valid_surebets: int = 0
    value_candidates: int = 0
    rejected_markets_total: int = 0
    not_evaluated_markets_total: int = 0
    rejection_reasons_breakdown: Dict[str, int] = field(default_factory=dict)
    valuebet_rejection_reasons_breakdown: Dict[str, int] = field(default_factory=dict)
    market_evaluation_records: List[MatchedMarketEvaluationRecord] = field(default_factory=list)
    # Stage 10.12 Provider Overlap Telemetry
    superbet_events_count: int = 0
    betclic_events_count: int = 0
    raw_overlap_count: int = 0
    selected_overlap_count: int = 0
    overlap_rate: float = 0.0
    provider_overlap_summary: Dict[str, Any] = field(default_factory=dict)
    # Stage 25 Matched Event Detail Telemetry
    matched_events_eligible_for_detail: int = 0
    matched_events_selected_for_detail: int = 0
    overview_only_matched_events: int = 0
    full_detail_matched_events: int = 0
    detail_budget_allocated: int = 0
    scan_mode: str = "NORMAL"
    # Stage 34 Fine-Grained Detail Acquisition & Concurrency Telemetry
    detail_requests_total: int = 0
    detail_requests_success: int = 0
    detail_requests_failed: int = 0
    detail_requests_retried: int = 0
    detail_requests_timeout: int = 0
    detail_queue_wait_ms: float = 0.0
    detail_network_ms: float = 0.0
    detail_parse_ms: float = 0.0
    detail_normalization_ms: float = 0.0
    detail_total_ms: float = 0.0
    max_concurrent_details: int = 1
    # Stage 37 Intelligent Detail Budget & Acquisition Telemetry
    detail_acquisition_seconds: float = 0.0
    markets_per_selected_event: float = 0.0
    # Stage 50 Market & Competition Scope Telemetry
    events_in_competition_scope: int = 0
    raw_markets_received: int = 0
    allowed_markets: int = 0
    discarded_markets: int = 0
    normalized_allowed_markets: int = 0
    discarded_market_families_top_20: List[Tuple[str, int]] = field(default_factory=list)
    per_provider_counts: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    # Acquisition 2.0 Explicit Funnel Accounting
    per_provider_accounting: Dict[str, Dict[str, Any]] = field(default_factory=dict)



@dataclass(frozen=True)
class ScanConfig:
    """Configuration container for production scan cycle execution."""
    providers: Tuple[str, ...] = ("superbet", "betclic")
    source_provider: str = "superbet"
    target_provider: str = "betclic"
    sport: str = "football"
    event_limit: Optional[int] = None
    preferred_competitions: Tuple[str, ...] = field(default_factory=tuple)
    hours_ahead: int = 168
    selection_mode: str = "OVERVIEW_ONLY"
    scan_mode: str = "NORMAL"  # "NORMAL" or "DEEP"
    max_detail_requests: Optional[int] = None
    normal_detail_budget: int = 40  # Configurable NORMAL budget (~30-50)
    deep_detail_budget: int = 100   # Configurable DEEP budget (~75-150)
    request_timeout: float = 15.0
    rate_limit_per_sec: float = 5.0
    enable_reconciliation: bool = True
    reconciliation_limit: int = 100
    max_misses_for_expiration: int = 2
    fail_on_critical_error: bool = False
    resource_budget: Optional[ResourceBudget] = None
    enable_valuebets: bool = True
    reference_source: str = "the_odds_api"
    min_value_percent: Decimal = Decimal("3.0")
    max_valuebet_alerts_per_scan: int = 5
    max_provider_workers: int = 4
    provider_timeout: float = 60.0
    detail_workers: int = 6
    telemetry_mode: str = "STANDARD"  # "MINIMAL", "STANDARD", or "FULL_DEBUG"

    @property
    def effective_max_detail_requests(self) -> int:
        """Resolves the effective detail budget based on scan_mode and explicit max_detail_requests."""
        if self.max_detail_requests is not None and self.max_detail_requests > 0:
            return self.max_detail_requests
        if str(self.scan_mode).upper() == "DEEP":
            return self.deep_detail_budget
        return self.normal_detail_budget

    @property
    def effective_provider_timeout(self) -> float:
        """Resolves worker timeout adaptively based on scan_mode and provider_timeout."""
        if self.provider_timeout != 60.0 and self.provider_timeout > 0:
            return self.provider_timeout
        if str(self.scan_mode).upper() == "DEEP":
            return 180.0
        return 90.0


@dataclass
class ScanCycleResult:
    """Comprehensive, structured, immutable-friendly result of one complete scan cycle."""

    # Identity and Status
    execution_id: str
    cycle_status: CycleStatus
    started_at: str
    completed_at: str
    duration_seconds: float
    stage_timings: StageTiming = field(default_factory=StageTiming)
    resource_metrics: ResourceMetrics = field(default_factory=ResourceMetrics)

    # Subsystem Execution Results
    provider_results: Dict[str, ProviderResult] = field(default_factory=dict)
    normalization_results: Dict[str, NormalizationResult] = field(default_factory=dict)
    validation_result: Optional[CrossBookmakerValidationResult] = None
    detection_result: Optional[SurebetDetectionResult] = None
    lifecycle_summary: Optional[LifecycleDispatchSummary] = None
    reconciliation_summary: Optional[ReconciliationSummary] = None
    valuebet_result: Optional[Any] = None
    valuebet_lifecycle_batch: Optional[Any] = None

    # Telemetry and Aggregated Counts
    discovered_events_count: int = 0
    popular_events_discovered_count: int = 0
    popular_events_selected_count: int = 0
    parsed_events_count: int = 0
    normalized_graphs_count: int = 0
    normalization_failed_count: int = 0
    markets_discovered_count: int = 0
    markets_normalized_count: int = 0
    markets_matched_count: int = 0
    markets_evaluated_count: int = 0
    matched_events_count: int = 0
    unmatched_events_count: int = 0
    cross_bookmaker_overlap_rate: float = 0.0
    detected_opportunities_count: int = 0
    new_opportunities_count: int = 0
    updated_opportunities_count: int = 0
    suppressed_opportunities_count: int = 0
    expired_opportunities_count: int = 0
    dispatched_count: int = 0
    delivered_count: int = 0
    failed_delivery_count: int = 0
    skipped_delivery_count: int = 0

    # Stage 10.9 Detail Prioritization Telemetry
    detail_candidates_available: int = 0
    detail_candidates_overlap: int = 0
    detail_events_selected: int = 0
    detail_events_overlap_selected: int = 0
    detail_overlap_selection_rate: float = 0.0
    multi_market_expected_events: int = 0
    # Stage 25 Matched Event Detail Expansion Telemetry
    matched_events_eligible_for_detail: int = 0
    matched_events_selected_for_detail: int = 0
    overview_only_matched_events: int = 0
    full_detail_matched_events: int = 0
    detail_budget_allocated: int = 0
    scan_mode: str = "NORMAL"
    # Stage 37 Intelligent Detail Budget & Acquisition Telemetry
    detail_acquisition_seconds: float = 0.0
    markets_per_selected_event: float = 0.0
    # Stage 50 Market & Competition Scope Telemetry
    events_in_competition_scope: int = 0
    raw_markets_received: int = 0
    allowed_markets: int = 0
    discarded_markets: int = 0
    normalized_allowed_markets: int = 0
    discarded_market_families_top_20: List[Tuple[str, int]] = field(default_factory=list)
    per_provider_counts: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    # Valuebet Telemetry & Counts
    valuebet_candidates_count: int = 0
    valuebets_qualified_count: int = 0
    valuebets_new_count: int = 0
    valuebets_updated_count: int = 0
    valuebets_suppressed_count: int = 0
    valuebets_expired_count: int = 0
    valuebets_dispatched_count: int = 0
    valuebet_reference_requests: int = 0
    valuebet_reference_cache_hits: int = 0
    valuebet_reference_cache_misses: int = 0

    # Stage 10.10 & Stage 13 Evaluation Coverage & Opportunity Truth
    matched_markets_total: int = 0
    evaluation_candidates_total: int = 0
    evaluated_markets_total: int = 0
    evaluation_excluded_total: int = 0
    evaluation_exclusion_breakdown: Dict[str, int] = field(default_factory=dict)
    evaluation_coverage_rate: float = 0.0
    # Stage 13 Explicit Terminal Evaluation Records & Telemetry
    surebet_candidates_count: int = 0
    valid_surebets_count: int = 0
    rejected_markets_count: int = 0
    not_evaluated_markets_count: int = 0
    rejection_reasons_breakdown: Dict[str, int] = field(default_factory=dict)
    valuebet_rejection_reasons_breakdown: Dict[str, int] = field(default_factory=dict)
    market_evaluation_records: List[MatchedMarketEvaluationRecord] = field(default_factory=list)

    # Diagnostics & Nearest Opportunity
    market_coverage_breakdown: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    nearest_opportunity: Optional[Dict[str, Any]] = None
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    diagnostics: Dict[str, Any] = field(default_factory=dict)

    # ──────────────────────────────────────────────────────────────────────────
    # Convenience Properties
    # ──────────────────────────────────────────────────────────────────────────

    @property
    def is_success(self) -> bool:
        """True if the scan cycle finished in SUCCESS state."""
        return self.cycle_status == CycleStatus.SUCCESS

    @property
    def is_partial(self) -> bool:
        """True if the scan cycle finished in PARTIAL state."""
        return self.cycle_status == CycleStatus.PARTIAL

    @property
    def is_failed(self) -> bool:
        """True if the scan cycle finished in FAILED state."""
        return self.cycle_status == CycleStatus.FAILED

    @property
    def has_errors(self) -> bool:
        """True if any error was recorded during execution."""
        return len(self.errors) > 0

    @property
    def has_warnings(self) -> bool:
        """True if any warning was recorded during execution."""
        return len(self.warnings) > 0

    # ──────────────────────────────────────────────────────────────────────────
    # Audit Report Generation
    # ──────────────────────────────────────────────────────────────────────────

    def generate_audit_report(self, detailed: bool = False) -> str:
        """Generates a human-readable, auditable diagnostic report of the scan cycle."""
        lines: List[str] = []
        lines.append("=" * 76)
        lines.append(f"PRODUCTION SCAN CYCLE AUDIT REPORT — [{self.execution_id}]")
        lines.append("=" * 76)
        lines.append(f"Cycle Status: {self.cycle_status.value}")
        lines.append(f"Started At:   {self.started_at}")
        lines.append(f"Completed At: {self.completed_at}")
        lines.append(f"Total Duration: {self.duration_seconds:.3f}s")
        lines.append("-" * 76)
        lines.append("STAGE TIMINGS:")
        lines.append(f"  Acquisition:    {self.stage_timings.acquisition_seconds:.3f}s")
        lines.append(f"  Normalization:  {self.stage_timings.normalization_seconds:.3f}s")
        lines.append(f"  Matching:       {self.stage_timings.matching_seconds:.3f}s")
        lines.append(f"  Detection:      {self.stage_timings.detection_seconds:.3f}s")
        lines.append(f"  Lifecycle:      {self.stage_timings.lifecycle_seconds:.3f}s")
        lines.append(f"  Dispatch:       {self.stage_timings.dispatch_seconds:.3f}s")
        lines.append(f"  Reconciliation: {self.stage_timings.reconciliation_seconds:.3f}s")
        lines.append("-" * 76)
        lines.append("RESOURCE METRICS:")
        lines.append(
            f"  HTTP Requests: Total={self.resource_metrics.total_http_requests}, "
            f"Detail={self.resource_metrics.detail_http_requests}"
        )
        lines.append(
            f"  Memory Peak:   {self.resource_metrics.peak_memory_mb:.2f} MB"
        )
        if self.detail_events_selected > 0 or self.resource_metrics.detail_events_selected > 0:
            det_sel = self.detail_events_selected or self.resource_metrics.detail_events_selected
            det_ovr = self.detail_events_overlap_selected or self.resource_metrics.detail_events_overlap_selected
            det_rate = (self.detail_overlap_selection_rate or self.resource_metrics.detail_overlap_selection_rate) * 100
            acq_s = self.detail_acquisition_seconds or self.resource_metrics.detail_acquisition_seconds or self.stage_timings.acquisition_seconds
            mkt_per_ev = self.markets_per_selected_event or self.resource_metrics.markets_per_selected_event
            lines.append(
                f"  Detail Budget: Mode={self.scan_mode} (Allocated={self.detail_budget_allocated or self.resource_metrics.detail_budget_allocated}), "
                f"Selected={det_sel} ({det_ovr} overlap, {det_rate:.1f}%), "
                f"Acq Time={acq_s:.2f}s, Avg Mkts/Event={mkt_per_ev:.1f}"
            )
        lines.append("-" * 76)
        lines.append("STAGE COUNTS & METRICS:")
        lines.append(
            f"  Events:    Discovered={self.discovered_events_count} (Popular={self.popular_events_discovered_count}), "
            f"Selected={self.resource_metrics.events_selected} (Popular={self.popular_events_selected_count}), "
            f"Parsed={self.parsed_events_count}, "
            f"Normalized={self.normalized_graphs_count} (Failed={self.normalization_failed_count})"
        )
        if self.resource_metrics.superbet_events_count > 0 or self.resource_metrics.betclic_events_count > 0 or self.resource_metrics.raw_overlap_count > 0:
            lines.append("  Provider Overlap:")
            lines.append(f"    - Superbet Events:  {self.resource_metrics.superbet_events_count}")
            lines.append(f"    - Betclic Events:   {self.resource_metrics.betclic_events_count}")
            lines.append(f"    - Raw Overlap:      {self.resource_metrics.raw_overlap_count}")
            lines.append(f"    - Selected Overlap: {self.resource_metrics.selected_overlap_count}")
            lines.append(f"    - Overlap Rate:     {self.resource_metrics.overlap_rate * 100:.1f}%")
        lines.append(
            f"  Markets:   Discovered={self.markets_discovered_count}, "
            f"Normalized={self.markets_normalized_count}, "
            f"Matched={self.markets_matched_count}, "
            f"Evaluated={self.markets_evaluated_count}"
        )
        if self.market_coverage_breakdown:
            lines.append("  Market Breakdown:")
            for m_type, counts in self.market_coverage_breakdown.items():
                disc = counts.get("discovered", 0)
                norm = counts.get("normalized", 0)
                mtch = counts.get("matched", 0)
                evald = counts.get("evaluated", 0)
                reason = counts.get("zero_match_reason")
                reason_str = f" [Reason: {reason}]" if (mtch == 0 and reason) else ""
                if disc > 0 or norm > 0 or mtch > 0 or evald > 0 or reason:
                    lines.append(f"    - {m_type:<18}: Discovered={disc:<4} Normalized={norm:<4} Matched={mtch:<3} Evaluated={evald:<3}{reason_str}")

        lines.append(f"  Matching:  Matched Events={self.matched_events_count}, Unmatched={self.unmatched_events_count}")
        lines.append(
            f"  Surebets:  Detected={self.detected_opportunities_count}, "
            f"NEW={self.new_opportunities_count}, "
            f"UPDATED={self.updated_opportunities_count}, "
            f"SUPPRESSED={self.suppressed_opportunities_count}, "
            f"EXPIRED={self.expired_opportunities_count}"
        )
        lines.append(
            f"  Delivery:  Dispatched={self.dispatched_count}, "
            f"Delivered={self.delivered_count}, "
            f"Failed={self.failed_delivery_count}, "
            f"Skipped={self.skipped_delivery_count}"
        )

        if self.reconciliation_summary:
            rec = self.reconciliation_summary
            lines.append(
                f"  Reconciliation: Scanned={rec.scanned_count}, Attempted={rec.attempted_count}, "
                f"Delivered={rec.delivered_count}, Retried={rec.retried_count}, "
                f"Exhausted={rec.exhausted_count}"
            )

        if self.diagnostics.get("nearest_opportunity"):
            near = self.diagnostics["nearest_opportunity"]
            lines.append("-" * 76)
            lines.append("NEAREST OPPORTUNITY TELEMETRY (ZERO SUREBET):")
            lines.append(f"  Event:       {near.get('event', 'N/A')}")
            lines.append(f"  Market:      {near.get('market', 'N/A')}")
            lines.append(f"  Prob Sum S:  {near.get('implied_probability_sum', 'N/A')}")
            lines.append(f"  Margin:      {near.get('margin_pct', 'N/A')}%")
            lines.append(f"  Distance:    {near.get('distance_to_arbitrage', 'N/A')}")

        if self.warnings:
            lines.append("-" * 76)
            lines.append(f"WARNINGS ({len(self.warnings)}):")
            for w in self.warnings:
                lines.append(f"  [WARN] {w}")

        if self.errors:
            lines.append("-" * 76)
            lines.append(f"ERRORS ({len(self.errors)}):")
            for err in self.errors:
                lines.append(f"  [ERROR] {err}")

        if detailed and self.detection_result and self.detection_result.opportunities:
            lines.append("-" * 76)
            lines.append("DETECTED OPPORTUNITIES:")
            for opp in self.detection_result.opportunities:
                leg_str = ", ".join(f"{l.selection_type}@{l.provider} ({float(l.odds):.2f})" for l in opp.legs)
                margin_pct = float(opp.arbitrage_margin * 100)
                lines.append(f"  * [{opp.opportunity_id}] {opp.canonical_market_key.to_key_string()} | Margin: +{margin_pct:.2f}% | Legs: {leg_str}")

        lines.append("=" * 76)
        return "\n".join(lines)
