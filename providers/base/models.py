"""
Provider Framework Data Models

Defines all shared data structures used across the scraping framework.
All models are strongly typed dataclasses.  Mutable models are used during
execution; frozen models are returned as part of final results.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum, auto
from pathlib import Path
from typing import Any, Dict, List, Optional


# ─────────────────────────────────────────────────────────────────────────────
# Enumerations
# ─────────────────────────────────────────────────────────────────────────────

class ExtractionStrategy(Enum):
    """Priority-ordered data extraction strategies."""
    NETWORK_RESPONSE = auto()   # 1st priority: intercept XHR/Fetch responses
    EMBEDDED_JSON = auto()      # 2nd priority: find JSON in page source
    DOM = auto()                # 3rd priority: CSS selector extraction
    PROVIDER_FALLBACK = auto()  # 4th priority: provider-specific custom logic


class QualityStatus(Enum):
    """Overall quality classification of a scraping execution."""
    EXCELLENT = "excellent"   # All data extracted, no warnings
    GOOD = "good"             # Minor warnings, data complete
    DEGRADED = "degraded"     # Partial data, warnings present
    POOR = "poor"             # Significant data missing
    FAILED = "failed"         # No usable data extracted


class RetryableClassification(Enum):
    """Classification of an error for retry purposes."""
    RETRYABLE = "retryable"
    NON_RETRYABLE = "non_retryable"
    FATAL = "fatal"


# ─────────────────────────────────────────────────────────────────────────────
# Configuration Models
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class TimeoutConfig:
    """Per-stage timeout configuration for a provider execution."""
    initialize_seconds: float = 30.0
    health_check_seconds: float = 10.0
    discovery_seconds: float = 120.0
    fetch_seconds: float = 300.0
    parse_seconds: float = 60.0
    validate_seconds: float = 30.0
    overall_seconds: float = 600.0
    navigation_seconds: float = 45.0
    page_load_seconds: float = 30.0

    def for_stage(self, stage: str) -> float:
        """Return the timeout for the given stage name."""
        mapping = {
            "initialize": self.initialize_seconds,
            "health_check": self.health_check_seconds,
            "discovery": self.discovery_seconds,
            "fetch": self.fetch_seconds,
            "parse": self.parse_seconds,
            "validate": self.validate_seconds,
        }
        return mapping.get(stage, self.overall_seconds)


@dataclass
class RetryConfig:
    """Retry policy configuration for a provider or stage."""
    max_retries: int = 3
    base_delay_seconds: float = 1.0
    max_delay_seconds: float = 60.0
    backoff_multiplier: float = 2.0
    jitter_fraction: float = 0.1
    retryable_http_status_codes: List[int] = field(
        default_factory=lambda: [429, 500, 502, 503, 504]
    )

    @classmethod
    def aggressive(cls) -> "RetryConfig":
        """High retry count for unreliable providers."""
        return cls(max_retries=5, base_delay_seconds=2.0, max_delay_seconds=120.0)

    @classmethod
    def conservative(cls) -> "RetryConfig":
        """Low retry count for stable providers."""
        return cls(max_retries=2, base_delay_seconds=0.5, max_delay_seconds=10.0)

    @classmethod
    def no_retry(cls) -> "RetryConfig":
        """Disable retries entirely."""
        return cls(max_retries=0)


@dataclass
class RateLimitConfig:
    """Rate limiting configuration for provider requests."""
    requests_per_second: float = 2.0
    burst_limit: int = 5
    cooldown_ms: int = 500
    concurrent_requests: int = 3


@dataclass
class BrowserConfig:
    """Configuration for browser-based providers."""
    browser_type: str = "chromium"           # chromium | firefox | webkit
    headless: bool = True
    viewport_width: int = 1920
    viewport_height: int = 1080
    locale: str = "pl-PL"
    timezone_id: str = "Europe/Warsaw"
    user_agent: Optional[str] = None
    blocked_resource_types: List[str] = field(
        default_factory=lambda: ["image", "font", "stylesheet", "media"]
    )
    extra_http_headers: Dict[str, str] = field(default_factory=dict)
    max_browser_restarts: int = 3
    pool_size: int = 2


@dataclass
class ProxyConfig:
    """Proxy server configuration."""
    server: str = ""                # e.g. "http://proxy.example.com:8080"
    username: Optional[str] = None
    password: Optional[str] = None
    rotate: bool = False
    proxy_list: List[str] = field(default_factory=list)


@dataclass
class ProviderMetadata:
    """Immutable descriptive metadata about a provider."""
    name: str
    code: str
    version: str = "1.0.0"
    enabled: bool = True
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    scraping_strategy: ExtractionStrategy = ExtractionStrategy.NETWORK_RESPONSE


# ─────────────────────────────────────────────────────────────────────────────
# Observability Models
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class StageEvent:
    """Records a single stage lifecycle event during execution."""
    stage: str
    event: str           # "started" | "finished" | "failed" | "retrying"
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    duration_ms: Optional[float] = None
    detail: Optional[str] = None


@dataclass
class RetryAttempt:
    """Records a single retry attempt."""
    stage: str
    attempt_number: int
    error_type: str
    error_message: str
    delay_seconds: float
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    recovered: bool = False


@dataclass
class ConsoleLog:
    """A console message captured from a browser page."""
    level: str      # "log" | "info" | "warn" | "error"
    text: str
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    url: Optional[str] = None


@dataclass
class FailedRequest:
    """A network request that failed during browser execution."""
    url: str
    method: str
    error_message: str
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    status_code: Optional[int] = None


@dataclass
class NetworkResponse:
    """A captured network response during browser execution."""
    url: str
    method: str
    status_code: int
    content_type: str
    body_bytes: int = 0
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    duration_ms: Optional[float] = None
    matched_pattern: Optional[str] = None
    body_preview: Optional[str] = None   # First 500 chars of body for diagnostics


@dataclass
class StageTiming:
    """Timing record for a single lifecycle stage."""
    stage: str
    started_at: str
    finished_at: Optional[str] = None
    duration_ms: Optional[float] = None
    succeeded: bool = True

    def finish(self, succeeded: bool = True) -> None:
        """Record the stage end time and compute duration."""
        now = datetime.now(timezone.utc)
        self.finished_at = now.isoformat()
        self.succeeded = succeeded
        started = datetime.fromisoformat(self.started_at)
        self.duration_ms = (now - started).total_seconds() * 1000


@dataclass
class DiagnosticsBundle:
    """
    Mutable accumulator for all diagnostic data produced during one execution.

    Created by the ExecutionEngine at the start of each run and passed through
    the ProviderContext.  Frozen into a DiagnosticsReport at execution end.
    """
    execution_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    provider_name: str = ""
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    finished_at: Optional[str] = None
    screenshots: List[Path] = field(default_factory=list)
    html_snapshots: List[Path] = field(default_factory=list)
    console_logs: List[ConsoleLog] = field(default_factory=list)
    failed_requests: List[FailedRequest] = field(default_factory=list)
    network_responses: List[NetworkResponse] = field(default_factory=list)
    stage_events: List[StageEvent] = field(default_factory=list)
    retry_attempts: List[RetryAttempt] = field(default_factory=list)
    stage_timings: List[StageTiming] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    extracted_objects_count: int = 0
    extraction_strategy_used: Optional[str] = None

    def add_stage_event(self, stage: str, event: str, detail: Optional[str] = None) -> None:
        self.stage_events.append(StageEvent(stage=stage, event=event, detail=detail))

    def add_retry_attempt(self, attempt: RetryAttempt) -> None:
        self.retry_attempts.append(attempt)

    def add_console_log(self, log: ConsoleLog) -> None:
        self.console_logs.append(log)

    def add_failed_request(self, req: FailedRequest) -> None:
        self.failed_requests.append(req)

    def add_network_response(self, resp: NetworkResponse) -> None:
        self.network_responses.append(resp)

    def add_warning(self, message: str) -> None:
        self.warnings.append(message)

    def add_error(self, message: str) -> None:
        self.errors.append(message)

    def add_screenshot(self, path: Path) -> None:
        self.screenshots.append(path)

    def add_html_snapshot(self, path: Path) -> None:
        self.html_snapshots.append(path)

    def start_stage(self, stage: str) -> StageTiming:
        timing = StageTiming(stage=stage, started_at=datetime.now(timezone.utc).isoformat())
        self.stage_timings.append(timing)
        self.add_stage_event(stage, "started")
        return timing

    def finish_stage(self, timing: StageTiming, succeeded: bool = True) -> None:
        timing.finish(succeeded=succeeded)
        self.add_stage_event(
            stage=timing.stage,
            event="finished" if succeeded else "failed",
            detail=f"duration_ms={timing.duration_ms:.1f}" if timing.duration_ms else None
        )

    def close(self) -> None:
        self.finished_at = datetime.now(timezone.utc).isoformat()

    def total_retries(self) -> int:
        return len(self.retry_attempts)

    def to_report(self) -> "DiagnosticsReport":
        return DiagnosticsReport(
            execution_id=self.execution_id,
            provider_name=self.provider_name,
            started_at=self.started_at,
            finished_at=self.finished_at or datetime.now(timezone.utc).isoformat(),
            screenshots=[str(p) for p in self.screenshots],
            html_snapshots=[str(p) for p in self.html_snapshots],
            console_logs=list(self.console_logs),
            failed_requests=list(self.failed_requests),
            network_responses=list(self.network_responses),
            stage_events=list(self.stage_events),
            retry_attempts=list(self.retry_attempts),
            stage_timings=list(self.stage_timings),
            warnings=list(self.warnings),
            errors=list(self.errors),
            extracted_objects_count=self.extracted_objects_count,
            extraction_strategy_used=self.extraction_strategy_used,
        )


@dataclass(frozen=True)
class DiagnosticsReport:
    """Frozen, immutable diagnostics snapshot returned in ProviderResult."""
    execution_id: str
    provider_name: str
    started_at: str
    finished_at: str
    screenshots: List[str]
    html_snapshots: List[str]
    console_logs: List[ConsoleLog]
    failed_requests: List[FailedRequest]
    network_responses: List[NetworkResponse]
    stage_events: List[StageEvent]
    retry_attempts: List[RetryAttempt]
    stage_timings: List[StageTiming]
    warnings: List[str]
    errors: List[str]
    extracted_objects_count: int
    extraction_strategy_used: Optional[str]


# ─────────────────────────────────────────────────────────────────────────────
# Metrics Models
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ProviderMetrics:
    """
    Comprehensive metrics collected during one provider execution.
    Extended from the original minimal version to capture all scraping dimensions.
    """
    # Timing
    execution_duration: float = 0.0
    discovery_duration: float = 0.0
    fetch_duration: float = 0.0
    parse_duration: float = 0.0
    validate_duration: float = 0.0

    # Requests
    request_count: int = 0
    bytes_downloaded: int = 0
    retry_count: int = 0
    rate_limit_waits: int = 0

    # Outcomes
    success_count: int = 0
    failure_count: int = 0
    warning_count: int = 0

    # Domain data
    events_discovered: int = 0
    events_fetched: int = 0
    markets_found: int = 0
    odds_found: int = 0
    parsed_object_count: int = 0
    validation_failure_count: int = 0

    # Browser metrics (optional, 0 for HTTP-only providers)
    page_loads: int = 0
    js_errors: int = 0
    network_responses_intercepted: int = 0
    browser_restarts: int = 0


@dataclass
class ValidationReport:
    """Results of the provider-level validation stage."""
    is_valid: bool = True
    total_objects: int = 0
    valid_objects: int = 0
    invalid_objects: int = 0
    rejection_reasons: List[Dict[str, Any]] = field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# Quality Report Models
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class QualityReport:
    """
    Per-execution data quality assessment.

    Generated automatically by the ExecutionEngine after every run,
    regardless of success or failure.
    """
    status: QualityStatus
    execution_id: str
    provider_name: str
    execution_time_seconds: float
    events_found: int
    markets_found: int
    odds_found: int
    warnings: List[str]
    errors: List[str]
    coverage_pct: float       # 0.0–100.0 — how much expected data was found
    missing_data_fields: List[str]
    retry_count: int
    extraction_strategy_used: Optional[str]
    generated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @property
    def has_warnings(self) -> bool:
        return len(self.warnings) > 0

    @property
    def has_errors(self) -> bool:
        return len(self.errors) > 0

    @property
    def is_usable(self) -> bool:
        return self.status not in (QualityStatus.FAILED, QualityStatus.POOR)


# ─────────────────────────────────────────────────────────────────────────────
# Health Models
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class HealthSnapshot:
    """
    Point-in-time health snapshot for a single provider.
    Updated after every execution by the HealthMonitor.
    """
    provider_name: str
    is_healthy: bool = True
    last_success_at: Optional[str] = None
    last_failure_at: Optional[str] = None
    consecutive_failures: int = 0
    consecutive_successes: int = 0
    total_runs: int = 0
    total_successes: int = 0
    total_failures: int = 0
    avg_duration_seconds: float = 0.0
    failure_rate: float = 0.0         # 0.0–1.0
    last_quality_status: Optional[str] = None
    last_events_found: int = 0
    last_markets_found: int = 0
    last_odds_found: int = 0
    last_retry_count: int = 0
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def record_success(self, duration_seconds: float, quality: QualityReport) -> None:
        self.total_runs += 1
        self.total_successes += 1
        self.consecutive_failures = 0
        self.consecutive_successes += 1
        self.is_healthy = True
        self.last_success_at = datetime.now(timezone.utc).isoformat()
        self.last_quality_status = quality.status.value
        self.last_events_found = quality.events_found
        self.last_markets_found = quality.markets_found
        self.last_odds_found = quality.odds_found
        self.last_retry_count = quality.retry_count
        # Rolling average
        n = self.total_successes
        self.avg_duration_seconds = ((self.avg_duration_seconds * (n - 1)) + duration_seconds) / n
        self._recalculate_failure_rate()
        self.updated_at = datetime.now(timezone.utc).isoformat()

    def record_failure(self) -> None:
        self.total_runs += 1
        self.total_failures += 1
        self.consecutive_successes = 0
        self.consecutive_failures += 1
        self.last_failure_at = datetime.now(timezone.utc).isoformat()
        if self.consecutive_failures >= 3:
            self.is_healthy = False
        self._recalculate_failure_rate()
        self.updated_at = datetime.now(timezone.utc).isoformat()

    def _recalculate_failure_rate(self) -> None:
        if self.total_runs > 0:
            self.failure_rate = self.total_failures / self.total_runs
        else:
            self.failure_rate = 0.0


@dataclass(frozen=True)
class FrameworkHealthReport:
    """Aggregated health across all registered providers."""
    overall_healthy: bool
    overall_status: str
    total_providers: int
    healthy_providers: int
    degraded_providers: int
    failed_providers: int
    disabled_providers: int
    provider_snapshots: Dict[str, HealthSnapshot]
    generated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def __getitem__(self, key: str) -> Any:
        if key == "overall_status":
            return self.overall_status
        if key == "provider_health":
            return {
                name: ("FAILED" if not snap.is_healthy else (snap.last_quality_status.upper() if snap.last_quality_status else "READY"))
                for name, snap in self.provider_snapshots.items()
            }
        if hasattr(self, key):
            return getattr(self, key)
        raise KeyError(f"Key '{key}' not found in FrameworkHealthReport")

    def __contains__(self, key: str) -> bool:
        return key in ("overall_status", "provider_health") or hasattr(self, key)

    def get(self, key: str, default: Any = None) -> Any:
        try:
            return self[key]
        except KeyError:
            return default


# ─────────────────────────────────────────────────────────────────────────────
# Acquisition Accounting Model (Acquisition 2.0)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ProviderAcquisitionAccounting:
    """
    Explicit mathematical acquisition accounting model (Acquisition 2.0).

    Strictly separates:
      PLANNED != SUBMITTED != STARTED != NETWORK REQUEST != CACHE HIT != SUCCESS != FAILED != RETRY != PARSED
    """
    provider_name: str = ""
    discovery_planned: int = 0
    discovery_executed: int = 0
    discovery_network_requests: int = 0
    discovery_cache_hits: int = 0
    events_discovered: int = 0

    detail_events_planned: int = 0
    detail_tasks_submitted: int = 0
    detail_tasks_started: int = 0
    detail_network_requests: int = 0
    overview_payloads_reused: int = 0  # Overview payloads reused directly from discovery without detail network requests

    detail_tasks_successful: int = 0
    detail_tasks_failed: int = 0
    detail_retries: int = 0
    detail_timeouts: int = 0

    events_parsed: int = 0
    markets_parsed: int = 0
    selections_parsed: int = 0

    failure_reasons: Dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "provider_name": self.provider_name,
            "discovery_planned": self.discovery_planned,
            "discovery_executed": self.discovery_executed,
            "discovery_network_requests": self.discovery_network_requests,
            "discovery_cache_hits": self.discovery_cache_hits,
            "events_discovered": self.events_discovered,
            "detail_events_planned": self.detail_events_planned,
            "detail_tasks_submitted": self.detail_tasks_submitted,
            "detail_tasks_started": self.detail_tasks_started,
            "detail_network_requests": self.detail_network_requests,
            "overview_payloads_reused": self.overview_payloads_reused,
            "detail_tasks_successful": self.detail_tasks_successful,
            "detail_tasks_failed": self.detail_tasks_failed,
            "detail_retries": self.detail_retries,
            "detail_timeouts": self.detail_timeouts,
            "events_parsed": self.events_parsed,
            "markets_parsed": self.markets_parsed,
            "selections_parsed": self.selections_parsed,
            "failure_reasons": dict(self.failure_reasons),
        }

    def validate_invariants(self) -> List[str]:
        """Validates mathematical integrity of acquisition cardinalities."""
        violations = []
        if self.detail_tasks_submitted > 0:
            if self.detail_tasks_successful + self.detail_tasks_failed != self.detail_tasks_submitted:
                violations.append(
                    f"Task completion mismatch: successful ({self.detail_tasks_successful}) + "
                    f"failed ({self.detail_tasks_failed}) != submitted ({self.detail_tasks_submitted})"
                )
        if self.detail_tasks_started > self.detail_tasks_submitted:
            violations.append(
                f"Tasks started ({self.detail_tasks_started}) exceeds submitted ({self.detail_tasks_submitted})"
            )
        return violations


# ─────────────────────────────────────────────────────────────────────────────
# Detail Acquisition Failure Contract (P1-003)
# ─────────────────────────────────────────────────────────────────────────────
#
# A failed Tier 2 detail request must never be represented solely as
# `markets == []`: that shape is indistinguishable from a legitimate
# successful empty response (SUCCESS_EMPTY_MARKETS). Fetchers therefore emit
# an explicit failure placeholder that preserves event identity plus a short
# safe failure reason; parsers propagate it onto the event model; the
# normalization engine refuses to turn it into a canonical graph.

DETAIL_FETCH_FAILED_KEY = "_detail_fetch_failed"
DETAIL_FETCH_ERROR_KEY = "_detail_fetch_error"
DETAIL_FETCH_ERROR_TYPE_KEY = "_detail_fetch_error_type"
DETAIL_FETCH_PROVIDER_KEY = "_detail_fetch_provider"

# P1-NEW-010: Tier-1 overview placeholder contract (NOT_ACQUIRED).
#
# Overview/Tier-1 items that were never selected for detail acquisition have
# unknown market state, but that is NOT an acquisition failure and NOT a
# legitimate zero-market detail response. Fabricated overview placeholders
# carry this marker so parsers propagate `overview_only=True` and the
# normalization engine skips them with a distinct counter instead of
# treating them as SUCCESS_EMPTY_MARKETS.
OVERVIEW_NOT_ACQUIRED_KEY = "_overview_not_acquired"
OVERVIEW_NOT_ACQUIRED_REASON_KEY = "_overview_not_acquired_reason"

DETAIL_FETCH_ERROR_MAX_CHARS = 200


def build_detail_fetch_failure_payload(
    provider: str,
    event_id: Any,
    name: Any = "",
    competition: Any = "",
    start_date: Any = "",
    exc: Optional[BaseException] = None,
) -> Dict[str, Any]:
    """Builds an explicit FETCH_FAILED placeholder for a failed detail request.

    Shape stays parser-compatible (`markets == []` plus identity fields) so a
    single failed event cannot abort the scan, while the marker keys carry
    independent failure information. `name` falls back to `event_id` because
    parsers require a non-empty event name.
    """
    err_type = type(exc).__name__ if exc is not None else "UnknownError"
    raw_msg = str(exc) if exc is not None else ""
    safe_msg = " ".join(raw_msg.split())[:DETAIL_FETCH_ERROR_MAX_CHARS]
    return {
        "id": str(event_id),
        "name": str(name) or str(event_id),
        "competition": str(competition or ""),
        "start_date": str(start_date or ""),
        "markets": [],
        DETAIL_FETCH_FAILED_KEY: True,
        DETAIL_FETCH_ERROR_KEY: f"{err_type}: {safe_msg}" if safe_msg else err_type,
        DETAIL_FETCH_ERROR_TYPE_KEY: err_type,
        DETAIL_FETCH_PROVIDER_KEY: str(provider),
    }


def is_detail_fetch_failure(payload: Any) -> bool:
    """True when a raw fetcher payload is an explicit FETCH_FAILED placeholder."""
    return isinstance(payload, dict) and payload.get(DETAIL_FETCH_FAILED_KEY) is True


def build_overview_not_acquired_payload(
    provider: str,
    event_id: Any,
    name: Any = "",
    competition: Any = "",
    start_date: Any = "",
    reason: str = "overview_only_not_selected_for_detail",
) -> Dict[str, Any]:
    """Builds an explicit NOT_ACQUIRED Tier-1 overview placeholder.

    Shape stays parser-compatible (`markets == []` plus identity fields) so
    overview items never abort the scan, while the marker keys keep the
    placeholder distinguishable from both SUCCESS_EMPTY_MARKETS and
    FETCH_FAILED downstream.
    """
    return {
        "id": str(event_id),
        "name": str(name) or str(event_id),
        "competition": str(competition or ""),
        "start_date": str(start_date or ""),
        "markets": [],
        OVERVIEW_NOT_ACQUIRED_KEY: True,
        OVERVIEW_NOT_ACQUIRED_REASON_KEY: str(reason),
        DETAIL_FETCH_PROVIDER_KEY: str(provider),
    }


def is_overview_not_acquired(payload: Any) -> bool:
    """True when a raw payload is an explicit NOT_ACQUIRED overview placeholder."""
    return isinstance(payload, dict) and payload.get(OVERVIEW_NOT_ACQUIRED_KEY) is True

