"""
Quality Report Builder

Constructs QualityReport objects from execution data at the end of each run.
The ExecutionEngine calls QualityReportBuilder.build() after every execution.

Quality assessment is automatic — providers never build quality reports themselves.
"""

from __future__ import annotations

import logging
from typing import List, Optional

from providers.base.models import (
    DiagnosticsBundle,
    ProviderMetrics,
    QualityReport,
    QualityStatus,
    ValidationReport,
)
from providers.base.provider_state import ProviderState

logger = logging.getLogger("framework.quality_report")


class QualityReportBuilder:
    """
    Builds a QualityReport from execution data.

    Encapsulates all quality assessment logic in one place.
    The algorithm is deterministic: same inputs always produce the same report.
    """

    def build(
        self,
        execution_id: str,
        provider_name: str,
        status: ProviderState,
        duration_seconds: float,
        metrics: ProviderMetrics,
        validation_report: ValidationReport,
        diagnostics: DiagnosticsBundle,
    ) -> QualityReport:
        """
        Build the quality report from execution outputs.

        This is the authoritative quality assessment for every execution.
        """
        warnings = list(diagnostics.warnings)
        errors = list(diagnostics.errors)

        # Compute coverage
        coverage_pct = self._compute_coverage(metrics, validation_report)

        # Detect missing data fields
        missing_fields = self._detect_missing_fields(metrics, validation_report)

        # Determine overall quality status
        quality_status = self._determine_status(
            provider_state=status,
            coverage_pct=coverage_pct,
            error_count=len(errors),
            warning_count=len(warnings),
            validation_failures=validation_report.invalid_objects,
            retry_count=diagnostics.total_retries(),
        )

        report = QualityReport(
            status=quality_status,
            execution_id=execution_id,
            provider_name=provider_name,
            execution_time_seconds=duration_seconds,
            events_found=metrics.events_discovered,
            markets_found=metrics.markets_found,
            odds_found=metrics.odds_found,
            warnings=warnings,
            errors=errors,
            coverage_pct=coverage_pct,
            missing_data_fields=missing_fields,
            retry_count=diagnostics.total_retries(),
            extraction_strategy_used=diagnostics.extraction_strategy_used,
        )

        logger.info(
            f"[QualityReport] {provider_name}: status={quality_status.value} "
            f"coverage={coverage_pct:.1f}% events={metrics.events_discovered} "
            f"markets={metrics.markets_found} odds={metrics.odds_found} "
            f"retries={diagnostics.total_retries()}"
        )

        return report

    # ──────────────────────────────────────────────────────────────────────────
    # Coverage Computation
    # ──────────────────────────────────────────────────────────────────────────

    def _compute_coverage(
        self,
        metrics: ProviderMetrics,
        validation: ValidationReport,
    ) -> float:
        """
        Compute data coverage as a percentage.

        Coverage considers:
        - How many discovered events were successfully fetched and parsed
        - Validation pass rate
        """
        discovered = metrics.events_discovered
        if discovered == 0:
            return 0.0

        parsed = metrics.parsed_object_count
        if parsed == 0:
            return 0.0

        fetch_coverage = min(100.0, (parsed / discovered) * 100)

        # Apply validation penalty
        if validation.total_objects > 0:
            valid_rate = validation.valid_objects / validation.total_objects
            validation_coverage = valid_rate * 100
        else:
            validation_coverage = 100.0

        # Weighted: 70% fetch coverage, 30% validation coverage
        return round((fetch_coverage * 0.7) + (validation_coverage * 0.3), 1)

    # ──────────────────────────────────────────────────────────────────────────
    # Missing Data Detection
    # ──────────────────────────────────────────────────────────────────────────

    def _detect_missing_fields(
        self,
        metrics: ProviderMetrics,
        validation: ValidationReport,
    ) -> List[str]:
        """Identify what data is absent in this execution."""
        missing = []

        if metrics.events_discovered == 0:
            missing.append("events")
        if metrics.markets_found == 0 and metrics.events_discovered > 0:
            missing.append("markets")
        if metrics.odds_found == 0 and metrics.markets_found > 0:
            missing.append("odds")
        if metrics.parsed_object_count == 0 and metrics.events_discovered > 0:
            missing.append("parsed_data")
        if validation.invalid_objects > 0:
            missing.append(f"valid_objects({validation.invalid_objects}_rejected)")

        return missing

    # ──────────────────────────────────────────────────────────────────────────
    # Status Determination
    # ──────────────────────────────────────────────────────────────────────────

    def _determine_status(
        self,
        provider_state: ProviderState,
        coverage_pct: float,
        error_count: int,
        warning_count: int,
        validation_failures: int,
        retry_count: int,
    ) -> QualityStatus:
        """Determine the overall quality status classification."""

        # Hard failure
        if provider_state == ProviderState.FAILED:
            return QualityStatus.FAILED

        # No data at all
        if coverage_pct == 0.0:
            return QualityStatus.FAILED

        # Excellent: high coverage, no errors, few warnings
        if coverage_pct >= 90.0 and error_count == 0 and warning_count <= 2 and retry_count == 0:
            return QualityStatus.EXCELLENT

        # Good: reasonable coverage, no critical errors
        if coverage_pct >= 75.0 and error_count == 0:
            return QualityStatus.GOOD

        # Degraded: partial coverage or notable issues
        if coverage_pct >= 40.0 or (error_count > 0 and coverage_pct > 0):
            return QualityStatus.DEGRADED

        # Poor: very low coverage
        if coverage_pct > 0:
            return QualityStatus.POOR

        return QualityStatus.FAILED
