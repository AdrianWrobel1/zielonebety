"""
Unit Tests for QualityReportBuilder (Task 019)
"""

import pytest
from providers.base.models import (
    DiagnosticsBundle,
    ProviderMetrics,
    QualityStatus,
    ValidationReport,
)
from providers.base.provider_state import ProviderState
from providers.base.quality_report import QualityReportBuilder


def test_quality_report_excellent_status():
    builder = QualityReportBuilder()
    metrics = ProviderMetrics(
        events_discovered=10,
        parsed_object_count=10,
        markets_found=50,
        odds_found=100
    )
    val_report = ValidationReport(is_valid=True, total_objects=10, valid_objects=10)
    diag = DiagnosticsBundle(execution_id="e1", provider_name="betclic")

    report = builder.build(
        execution_id="e1",
        provider_name="betclic",
        status=ProviderState.COMPLETED,
        duration_seconds=1.5,
        metrics=metrics,
        validation_report=val_report,
        diagnostics=diag,
    )

    assert report.status == QualityStatus.EXCELLENT
    assert report.coverage_pct == 100.0
    assert report.missing_data_fields == []


def test_quality_report_good_status():
    builder = QualityReportBuilder()
    metrics = ProviderMetrics(
        events_discovered=10,
        parsed_object_count=8,
        markets_found=40,
        odds_found=80
    )
    val_report = ValidationReport(is_valid=True, total_objects=8, valid_objects=8)
    diag = DiagnosticsBundle(execution_id="e2", provider_name="betclic")

    report = builder.build(
        execution_id="e2",
        provider_name="betclic",
        status=ProviderState.COMPLETED,
        duration_seconds=2.0,
        metrics=metrics,
        validation_report=val_report,
        diagnostics=diag,
    )

    assert report.status == QualityStatus.GOOD
    assert report.coverage_pct == 86.0


def test_quality_report_degraded_status():
    builder = QualityReportBuilder()
    metrics = ProviderMetrics(
        events_discovered=10,
        parsed_object_count=5,
        markets_found=20,
        odds_found=40
    )
    val_report = ValidationReport(is_valid=False, total_objects=5, valid_objects=3, invalid_objects=2)
    diag = DiagnosticsBundle(execution_id="e3", provider_name="betclic")

    report = builder.build(
        execution_id="e3",
        provider_name="betclic",
        status=ProviderState.COMPLETED,
        duration_seconds=3.0,
        metrics=metrics,
        validation_report=val_report,
        diagnostics=diag,
    )

    assert report.status == QualityStatus.DEGRADED
    assert len(report.missing_data_fields) > 0


def test_quality_report_failed_status():
    builder = QualityReportBuilder()
    metrics = ProviderMetrics()
    val_report = ValidationReport()
    diag = DiagnosticsBundle(execution_id="e4", provider_name="betclic")

    report = builder.build(
        execution_id="e4",
        provider_name="betclic",
        status=ProviderState.FAILED,
        duration_seconds=0.5,
        metrics=metrics,
        validation_report=val_report,
        diagnostics=diag,
    )

    assert report.status == QualityStatus.FAILED
    assert report.coverage_pct == 0.0
