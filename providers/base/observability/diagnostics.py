"""
Diagnostics Collector

High-level facade over DiagnosticsBundle that the ExecutionEngine and scraping
components use to record diagnostic events without caring about bundle internals.

Keeps the bundle open during execution, then seals it at the end.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from providers.base.models import (
    ConsoleLog,
    DiagnosticsBundle,
    DiagnosticsReport,
    FailedRequest,
    NetworkResponse,
    RetryAttempt,
    StageTiming,
)


logger = logging.getLogger("framework.diagnostics")


class DiagnosticsCollector:
    """
    Owns a DiagnosticsBundle for one execution.

    Provides a clean interface for all framework components to record
    diagnostic events without direct access to the underlying bundle.
    """

    def __init__(self, execution_id: str, provider_name: str) -> None:
        self._bundle = DiagnosticsBundle(
            execution_id=execution_id,
            provider_name=provider_name,
        )
        self._sealed = False

    # ──────────────────────────────────────────────────────────────────────────
    # Stage Tracking
    # ──────────────────────────────────────────────────────────────────────────

    def start_stage(self, stage: str) -> StageTiming:
        """Record stage start.  Returns a StageTiming that must be closed."""
        self._assert_open()
        timing = self._bundle.start_stage(stage)
        logger.debug(f"[{self._bundle.execution_id}] Stage started: {stage}")
        return timing

    def finish_stage(self, timing: StageTiming, succeeded: bool = True) -> None:
        """Record stage completion with duration."""
        self._assert_open()
        self._bundle.finish_stage(timing, succeeded=succeeded)
        status = "OK" if succeeded else "FAILED"
        logger.debug(
            f"[{self._bundle.execution_id}] Stage finished: {timing.stage} "
            f"({status}, {timing.duration_ms:.1f}ms)"
        )

    # ──────────────────────────────────────────────────────────────────────────
    # Retry Tracking
    # ──────────────────────────────────────────────────────────────────────────

    def record_retry(self, attempt: RetryAttempt) -> None:
        self._assert_open()
        self._bundle.add_retry_attempt(attempt)
        logger.info(
            f"[{self._bundle.execution_id}] Retry #{attempt.attempt_number} "
            f"on stage={attempt.stage} after {attempt.error_type}: {attempt.error_message}"
        )

    # ──────────────────────────────────────────────────────────────────────────
    # Browser / Network Events
    # ──────────────────────────────────────────────────────────────────────────

    def record_console_log(self, level: str, text: str, url: Optional[str] = None) -> None:
        self._assert_open()
        self._bundle.add_console_log(ConsoleLog(level=level, text=text, url=url))

    def record_failed_request(
        self,
        url: str,
        method: str,
        error_message: str,
        status_code: Optional[int] = None,
    ) -> None:
        self._assert_open()
        self._bundle.add_failed_request(
            FailedRequest(url=url, method=method, error_message=error_message, status_code=status_code)
        )

    def record_network_response(self, response: NetworkResponse) -> None:
        self._assert_open()
        self._bundle.add_network_response(response)

    # ──────────────────────────────────────────────────────────────────────────
    # Artifact Paths
    # ──────────────────────────────────────────────────────────────────────────

    def record_screenshot(self, path: Path) -> None:
        self._assert_open()
        self._bundle.add_screenshot(path)

    def record_html_snapshot(self, path: Path) -> None:
        self._assert_open()
        self._bundle.add_html_snapshot(path)

    # ──────────────────────────────────────────────────────────────────────────
    # Warnings & Errors
    # ──────────────────────────────────────────────────────────────────────────

    def warn(self, message: str) -> None:
        self._assert_open()
        self._bundle.add_warning(message)
        logger.warning(f"[{self._bundle.execution_id}] {message}")

    def error(self, message: str) -> None:
        self._assert_open()
        self._bundle.add_error(message)
        logger.error(f"[{self._bundle.execution_id}] {message}")

    # ──────────────────────────────────────────────────────────────────────────
    # Extraction Tracking
    # ──────────────────────────────────────────────────────────────────────────

    def record_extraction_strategy(self, strategy_name: str, objects_count: int) -> None:
        self._assert_open()
        self._bundle.extraction_strategy_used = strategy_name
        self._bundle.extracted_objects_count = objects_count

    # ──────────────────────────────────────────────────────────────────────────
    # Sealing
    # ──────────────────────────────────────────────────────────────────────────

    def seal(self) -> DiagnosticsReport:
        """
        Close the bundle and return an immutable DiagnosticsReport.
        After sealing, no further events can be recorded.
        """
        if not self._sealed:
            self._bundle.close()
            self._sealed = True
        return self._bundle.to_report()

    @property
    def bundle(self) -> DiagnosticsBundle:
        """Direct access to the bundle for framework-internal use."""
        return self._bundle

    @property
    def execution_id(self) -> str:
        return self._bundle.execution_id

    def export_summary(self) -> dict:
        """
        Return a structured dictionary summary of the diagnostic bundle state.
        """
        return {
            "execution_id": self._bundle.execution_id,
            "provider_name": self._bundle.provider_name,
            "started_at": self._bundle.started_at,
            "finished_at": self._bundle.finished_at,
            "sealed": self._sealed,
            "warnings_count": len(self._bundle.warnings),
            "errors_count": len(self._bundle.errors),
            "retry_attempts_count": self._bundle.total_retries(),
            "console_logs_count": len(self._bundle.console_logs),
            "failed_requests_count": len(self._bundle.failed_requests),
            "extraction_strategy_used": self._bundle.extraction_strategy_used,
            "extracted_objects_count": self._bundle.extracted_objects_count,
        }


    def _assert_open(self) -> None:
        if self._sealed:
            raise RuntimeError(
                f"DiagnosticsCollector for execution {self._bundle.execution_id} is already sealed"
            )

