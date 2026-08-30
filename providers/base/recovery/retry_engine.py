"""
Retry Engine

Central retry orchestration for the scraping framework.

Owns:
- Configurable exponential backoff with jitter
- Per-attempt error classification (retryable vs non-retryable)
- Integration with DiagnosticsCollector (records every attempt)
- Integration with MetricsCollector (increments retry counter)
- Support for recovery strategies between attempts

Usage:
    engine = RetryEngine(config=retry_config, classifier=classifier)
    result = engine.execute(
        fn=lambda: scraper.fetch(url),
        stage="fetch",
        diagnostics=collector,
        metrics=metrics,
    )
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import random
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional, TypeVar

from providers.base.models import RetryableClassification, RetryConfig, RetryAttempt
from providers.base.recovery.error_classifier import ErrorClassifier, get_global_classifier
from providers.base.exceptions import RetryExhaustedError, NonRetryableError

logger = logging.getLogger("framework.retry_engine")

T = TypeVar("T")


@dataclass
class RetryContext:
    """Carries context into each retry attempt."""
    stage: str
    attempt_number: int
    last_error: Optional[Exception]
    total_delay_seconds: float


class RetryEngine:
    """
    Executes a callable with automatic retry on transient failures.

    Supports both sync and async callables.
    Every retry attempt is recorded in diagnostics and counted in metrics.
    """

    def __init__(
        self,
        config: Optional[RetryConfig] = None,
        classifier: Optional[ErrorClassifier] = None,
    ) -> None:
        self._config = config or RetryConfig()
        self._classifier = classifier or get_global_classifier()

    # ──────────────────────────────────────────────────────────────────────────
    # Synchronous Execution
    # ──────────────────────────────────────────────────────────────────────────

    def execute(
        self,
        fn: Callable[[], T],
        stage: str,
        diagnostics=None,     # DiagnosticsCollector | None
        metrics=None,         # MetricsCollector | None
        recovery: Optional[Callable[[RetryContext], None]] = None,
    ) -> T:
        """
        Execute fn with retry policy.

        Args:
            fn: Zero-argument callable to execute and retry.
            stage: Name of the lifecycle stage (for logging/diagnostics).
            diagnostics: Optional DiagnosticsCollector to record attempts.
            metrics: Optional MetricsCollector to increment retry counter.
            recovery: Optional callable invoked between retries (e.g., restart browser).

        Returns:
            The return value of fn on success.

        Raises:
            RetryExhaustedError: If all retries are consumed.
            Exception: If the error is classified as NON_RETRYABLE or FATAL.
        """
        max_retries = self._config.max_retries
        last_error: Optional[Exception] = None
        total_delay = 0.0

        for attempt in range(max_retries + 1):
            try:
                result = fn()
                if attempt > 0:
                    logger.info(f"[RetryEngine] {stage}: succeeded on attempt {attempt + 1}")
                return result

            except Exception as exc:
                last_error = exc
                classification = self._classifier.classify(exc)

                self._record_attempt(
                    diagnostics=diagnostics,
                    metrics=metrics,
                    stage=stage,
                    attempt=attempt,
                    exc=exc,
                    classification=classification,
                    delay=0.0,
                )

                if classification.is_non_retryable or classification.is_fatal:
                    logger.warning(
                        f"[RetryEngine] {stage}: non-retryable error after attempt {attempt + 1}: "
                        f"{type(exc).__name__} — {exc}"
                    )
                    raise

                if attempt >= max_retries:
                    break

                delay = self._compute_delay(attempt)
                total_delay += delay
                logger.info(
                    f"[RetryEngine] {stage}: attempt {attempt + 1}/{max_retries + 1} failed "
                    f"({type(exc).__name__}). Retrying in {delay:.2f}s..."
                )

                # Run recovery action if provided
                if recovery is not None:
                    try:
                        ctx = RetryContext(
                            stage=stage,
                            attempt_number=attempt,
                            last_error=last_error,
                            total_delay_seconds=total_delay,
                        )
                        recovery(ctx)
                    except Exception as rec_err:
                        logger.warning(f"[RetryEngine] Recovery action failed: {rec_err}")

                time.sleep(delay)

        raise RetryExhaustedError(
            message=f"Stage '{stage}' failed after {max_retries + 1} attempts: {last_error}",
            attempt_count=max_retries + 1,
            last_error=last_error,
        )

    # ──────────────────────────────────────────────────────────────────────────
    # Asynchronous Execution
    # ──────────────────────────────────────────────────────────────────────────

    async def execute_async(
        self,
        fn: Callable[[], Any],
        stage: str,
        diagnostics=None,
        metrics=None,
        recovery: Optional[Callable[[RetryContext], Any]] = None,
    ) -> Any:
        """
        Async version of execute().  fn may be sync or async.
        """
        max_retries = self._config.max_retries
        last_error: Optional[Exception] = None
        total_delay = 0.0

        for attempt in range(max_retries + 1):
            try:
                if inspect.iscoroutinefunction(fn):
                    result = await fn()
                else:
                    result = fn()

                if attempt > 0:
                    logger.info(f"[RetryEngine/async] {stage}: succeeded on attempt {attempt + 1}")
                return result

            except Exception as exc:
                last_error = exc
                classification = self._classifier.classify(exc)

                self._record_attempt(
                    diagnostics=diagnostics,
                    metrics=metrics,
                    stage=stage,
                    attempt=attempt,
                    exc=exc,
                    classification=classification,
                    delay=0.0,
                )

                if classification.is_non_retryable or classification.is_fatal:
                    logger.warning(
                        f"[RetryEngine/async] {stage}: non-retryable after attempt {attempt + 1}: "
                        f"{type(exc).__name__}"
                    )
                    raise

                if attempt >= max_retries:
                    break

                delay = self._compute_delay(attempt)
                total_delay += delay
                logger.info(
                    f"[RetryEngine/async] {stage}: attempt {attempt + 1}/{max_retries + 1} failed. "
                    f"Retrying in {delay:.2f}s..."
                )

                if recovery is not None:
                    try:
                        ctx = RetryContext(
                            stage=stage,
                            attempt_number=attempt,
                            last_error=last_error,
                            total_delay_seconds=total_delay,
                        )
                        if inspect.iscoroutinefunction(recovery):
                            await recovery(ctx)
                        else:
                            recovery(ctx)
                    except Exception as rec_err:
                        logger.warning(f"[RetryEngine/async] Recovery failed: {rec_err}")

                await asyncio.sleep(delay)

        raise RetryExhaustedError(
            message=f"Stage '{stage}' failed after {max_retries + 1} attempts: {last_error}",
            attempt_count=max_retries + 1,
            last_error=last_error,
        )

    # ──────────────────────────────────────────────────────────────────────────
    # Internal Helpers
    # ──────────────────────────────────────────────────────────────────────────

    def _compute_delay(self, attempt: int) -> float:
        """Compute exponential backoff delay with jitter."""
        base = self._config.base_delay_seconds
        multiplier = self._config.backoff_multiplier
        max_delay = self._config.max_delay_seconds
        jitter_frac = self._config.jitter_fraction

        delay = min(base * (multiplier ** attempt), max_delay)
        jitter = delay * jitter_frac * (2 * random.random() - 1)
        return max(0.0, delay + jitter)

    def _record_attempt(
        self,
        diagnostics,
        metrics,
        stage: str,
        attempt: int,
        exc: Exception,
        classification,
        delay: float,
    ) -> None:
        """Record the retry attempt in diagnostics and metrics."""
        if diagnostics is not None:
            try:
                ra = RetryAttempt(
                    stage=stage,
                    attempt_number=attempt,
                    error_type=type(exc).__name__,
                    error_message=str(exc)[:500],
                    delay_seconds=delay,
                    recovered=False,
                )
                diagnostics.record_retry(ra)
            except Exception:
                pass  # Never let diagnostics recording crash execution

        if metrics is not None and attempt > 0:
            try:
                metrics.count_retry()
            except Exception:
                pass
