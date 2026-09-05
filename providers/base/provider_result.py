"""
Provider Execution Result

The single immutable object returned by every provider execution.
Always produced — even on total failure — so callers never receive None.

Consumers of ProviderResult must never assume success.  Always check `.status`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from providers.base.provider_state import ProviderState
from providers.base.models import (
    DiagnosticsReport,
    ProviderAcquisitionAccounting,
    ProviderMetrics,
    QualityReport,
    QualityStatus,
    ValidationReport,
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class ProviderResult:
    """
    Immutable execution result returned by every provider run.

    Guaranteed fields (always present):
        - provider_name
        - execution_id
        - status
        - execution_duration
        - started_at
        - finished_at

    Optional fields (present when execution reached that stage):
        - discovered_objects, parsed_objects, validation_report
        - diagnostics, quality_report, metrics, accounting
        - warnings, errors
    """

    # Identity
    provider_name: str
    status: ProviderState
    execution_duration: float
    execution_id: str = field(default_factory=lambda: str(datetime.now(timezone.utc).timestamp()))

    # Timestamps
    started_at: str = field(default_factory=_utc_now)
    finished_at: str = field(default_factory=_utc_now)

    # Stage outputs
    discovered_objects: List[Any] = field(default_factory=list)
    parsed_objects: List[Any] = field(default_factory=list)
    validation_report: ValidationReport = field(default_factory=ValidationReport)

    # Observability
    diagnostics: Optional[DiagnosticsReport] = None
    quality_report: Optional[QualityReport] = None
    metrics: ProviderMetrics = field(default_factory=ProviderMetrics)
    accounting: Optional[ProviderAcquisitionAccounting] = None

    # Human-readable summaries
    warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    # ──────────────────────────────────────────────────────────────────────────
    # Convenience Properties
    # ──────────────────────────────────────────────────────────────────────────

    @property
    def succeeded(self) -> bool:
        return self.status in (ProviderState.COMPLETED, ProviderState.DEGRADED)

    @property
    def failed(self) -> bool:
        return self.status == ProviderState.FAILED

    @property
    def was_disabled(self) -> bool:
        return self.status == ProviderState.CANCELLED

    @property
    def has_warnings(self) -> bool:
        return len(self.warnings) > 0

    @property
    def has_errors(self) -> bool:
        return len(self.errors) > 0

    @property
    def total_retries(self) -> int:
        if self.diagnostics:
            return len(self.diagnostics.retry_attempts)
        return 0

    @property
    def events_found(self) -> int:
        return self.metrics.events_discovered

    @property
    def markets_found(self) -> int:
        return self.metrics.markets_found

    @property
    def odds_found(self) -> int:
        return self.metrics.odds_found

    # ──────────────────────────────────────────────────────────────────────────
    # Factory Methods
    # ──────────────────────────────────────────────────────────────────────────

    @classmethod
    def failure(
        cls,
        provider_name: str,
        execution_id: str,
        error: str,
        started_at: str,
        diagnostics: Optional[DiagnosticsReport] = None,
    ) -> "ProviderResult":
        """Create a canonical failure result with guaranteed fields."""
        return cls(
            provider_name=provider_name,
            execution_id=execution_id,
            status=ProviderState.FAILED,
            execution_duration=0.0,
            started_at=started_at,
            finished_at=_utc_now(),
            errors=[error],
            diagnostics=diagnostics,
            quality_report=QualityReport(
                status=QualityStatus.FAILED,
                execution_id=execution_id,
                provider_name=provider_name,
                execution_time_seconds=0.0,
                events_found=0,
                markets_found=0,
                odds_found=0,
                warnings=[],
                errors=[error],
                coverage_pct=0.0,
                missing_data_fields=["events", "markets", "odds"],
                retry_count=0,
                extraction_strategy_used=None,
            ),
        )

    @classmethod
    def disabled(cls, provider_name: str, execution_id: str) -> "ProviderResult":
        """Create a result for a provider that was disabled in configuration."""
        return cls(
            provider_name=provider_name,
            execution_id=execution_id,
            status=ProviderState.CANCELLED,
            execution_duration=0.0,
            warnings=["Provider is disabled in configuration"],
        )

    def __repr__(self) -> str:
        return (
            f"ProviderResult("
            f"provider={self.provider_name!r}, "
            f"status={self.status.name}, "
            f"duration={self.execution_duration:.2f}s, "
            f"events={self.events_found}, "
            f"markets={self.markets_found}, "
            f"retries={self.total_retries})"
        )
