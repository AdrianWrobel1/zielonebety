"""
Execution Engine

Coordinates isolated execution of provider instances.
Handles:
- Per-stage execution timing & timeouts
- Retry Engine integration
- Diagnostics collection
- Quality Report generation
- Health Monitor updates
- Absolute fault isolation (guarantees ProviderResult, never raises to caller)
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, List, Optional

from providers.base.base_provider import BaseProvider
from providers.base.models import (
    ProviderMetrics,
    ValidationReport,
    QualityReport,
    QualityStatus,
)
from providers.base.provider_context import ProviderContext
from providers.base.provider_result import ProviderResult
from providers.base.provider_state import ProviderState
from providers.base.observability.diagnostics import DiagnosticsCollector
from providers.base.observability.metrics_collector import MetricsCollector
from providers.base.observability.health_monitor import get_global_health_monitor, HealthMonitor
from providers.base.observability.performance_profiler import PerformanceProfiler
from providers.base.recovery.retry_engine import RetryEngine
from providers.base.quality_report import QualityReportBuilder
from providers.base.rate_limiter import RateLimiter
from providers.base.exceptions import ProviderDisabledError, ProviderTimeoutError

logger = logging.getLogger("framework.execution_engine")


def _execute_stage_with_timeout(retry_engine, fn, stage, diagnostics, metrics, timeout_seconds):
    """Runs one provider stage bounded by ``timeout_seconds`` (P1-NEW-006).

    TimeoutConfig previously existed but was never enforced, so a hung
    stage (deadlocked semaphore, infinite parse loop below the HTTP layer)
    could stall the scan worker indefinitely. On expiry raises
    ProviderTimeoutError, which the caller's existing isolation converts to
    a FAILED ProviderResult with diagnostics (never a hang, never success).
    The abandoned worker thread is bounded (one per timed-out stage) and
    daemon-neutral: executors shut down without waiting.
    """
    import concurrent.futures

    try:
        timeout_value = float(timeout_seconds)
    except (TypeError, ValueError):
        timeout_value = 0.0
    if not timeout_value or timeout_value <= 0:
        return retry_engine.execute(fn=fn, stage=stage, diagnostics=diagnostics, metrics=metrics)
    # NOTE: plain constructor (not `with`) on purpose — the context
    # manager's exit would block until a hung worker finishes. Shutdown is
    # wait=False below so expiry returns immediately; the abandoned worker
    # is bounded to one thread per timed-out stage.
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        fut = pool.submit(retry_engine.execute, fn, stage, diagnostics, metrics)
        try:
            return fut.result(timeout=timeout_value)
        except concurrent.futures.TimeoutError as te:
            fut.cancel()
            try:
                diagnostics.error(f"Stage '{stage}' exceeded {timeout_value}s timeout")
            except Exception:
                pass
            raise ProviderTimeoutError(
                f"Stage '{stage}' exceeded {timeout_value}s timeout",
                stage=stage,
                timeout_seconds=float(timeout_value),
            ) from te
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


class ExecutionEngine:
    """
    Coordinates isolated provider execution.
    """

    def __init__(
        self,
        health_monitor: Optional[HealthMonitor] = None,
        retry_engine: Optional[RetryEngine] = None,
    ) -> None:
        self.health_monitor = health_monitor or get_global_health_monitor()
        self.retry_engine = retry_engine or RetryEngine()
        self.quality_builder = QualityReportBuilder()

    def execute(self, provider: BaseProvider) -> ProviderResult:
        """
        Synchronously execute a provider instance.
        Runs event loop if async features are invoked, or executes sync pipeline.
        """
        start_time = time.perf_counter()
        ctx = provider.context
        diagnostics = DiagnosticsCollector(ctx.execution_id, provider.metadata.name)
        metrics = MetricsCollector()
        profiler = PerformanceProfiler(ctx.execution_id, provider.metadata.name)

        # P1-NEW-006: TimeoutConfig was defined but never enforced. Resolve
        # per-stage budgets defensively (hand-rolled contexts may lack it).
        _timeout_cfg = getattr(ctx, "timeout_config", None)

        def _stage_timeout(stage_name):
            try:
                if _timeout_cfg is not None and hasattr(_timeout_cfg, "for_stage"):
                    return float(_timeout_cfg.for_stage(stage_name))
            except (TypeError, ValueError, AttributeError):
                pass
            return 0.0

        if not provider.metadata.enabled:
            return ProviderResult.disabled(provider.metadata.name, ctx.execution_id)

        discovered_items: List[Any] = []
        parsed_items: List[Any] = []
        validation_report = ValidationReport()

        try:
            # 1. Initialize
            with profiler.measure("initialize"):
                stage_t = diagnostics.start_stage("initialize")
                try:
                    _execute_stage_with_timeout(
                        self.retry_engine,
                        fn=provider.initialize,
                        stage="initialize",
                        diagnostics=diagnostics,
                        metrics=metrics,
                        timeout_seconds=_stage_timeout("initialize"),
                    )
                    diagnostics.finish_stage(stage_t, succeeded=True)
                except Exception as e:
                    diagnostics.finish_stage(stage_t, succeeded=False)
                    raise

            if provider.state == ProviderState.CANCELLED:
                return ProviderResult.disabled(provider.metadata.name, ctx.execution_id)

            # 2. Discovery
            with profiler.measure("discovery"):
                stage_t = diagnostics.start_stage("discovery")
                try:
                    discovered_items = _execute_stage_with_timeout(
                        self.retry_engine,
                        fn=provider.discover,
                        stage="discovery",
                        diagnostics=diagnostics,
                        metrics=metrics,
                        timeout_seconds=_stage_timeout("discovery"),
                    )
                    metrics.count_events_discovered(len(discovered_items))
                    diagnostics.finish_stage(stage_t, succeeded=True)
                except Exception as e:
                    diagnostics.finish_stage(stage_t, succeeded=False)
                    raise

            # 3. Fetch
            with profiler.measure("fetch"):
                stage_t = diagnostics.start_stage("fetch")
                try:
                    raw_data = _execute_stage_with_timeout(
                        self.retry_engine,
                        fn=lambda: provider.fetch(discovered_items),
                        stage="fetch",
                        diagnostics=diagnostics,
                        metrics=metrics,
                        timeout_seconds=_stage_timeout("fetch"),
                    )
                    metrics.count_events_fetched(len(raw_data))
                    diagnostics.finish_stage(stage_t, succeeded=True)
                except Exception as e:
                    diagnostics.finish_stage(stage_t, succeeded=False)
                    raise

            # 4. Parse
            with profiler.measure("parse"):
                stage_t = diagnostics.start_stage("parse")
                try:
                    parsed_items = _execute_stage_with_timeout(
                        self.retry_engine,
                        fn=lambda: provider.parse(raw_data),
                        stage="parse",
                        diagnostics=diagnostics,
                        metrics=metrics,
                        timeout_seconds=_stage_timeout("parse"),
                    )
                    metrics.count_parsed(len(parsed_items))

                    # Count markets and odds from parsed items
                    for item in parsed_items:
                        markets = getattr(item, "markets", [])
                        metrics.count_markets(len(markets))
                        for m in markets:
                            selections = getattr(m, "selections", [])
                            metrics.count_odds(len(selections))

                    diagnostics.finish_stage(stage_t, succeeded=True)
                except Exception as e:
                    diagnostics.finish_stage(stage_t, succeeded=False)
                    raise

            # 5. Validate
            with profiler.measure("validate"):
                stage_t = diagnostics.start_stage("validate")
                try:
                    validation_report = provider.validate(parsed_items)
                    metrics.count_validation_failures(validation_report.invalid_objects)
                    diagnostics.finish_stage(stage_t, succeeded=validation_report.is_valid)
                except Exception as e:
                    diagnostics.finish_stage(stage_t, succeeded=False)
                    raise

            final_state = ProviderState.COMPLETED if validation_report.is_valid else ProviderState.DEGRADED

        except Exception as e:
            err_msg = f"ExecutionEngine isolated error for '{provider.metadata.name}': {e}"
            logger.error(err_msg, exc_info=True)
            diagnostics.error(str(e))
            metrics.count_failure()
            final_state = ProviderState.FAILED

        finally:
            execution_duration = time.perf_counter() - start_time
            metrics.set_stage_duration("execution", execution_duration)
            final_metrics = metrics.flush()
            perf_report = profiler.report()

            # Build quality report
            diag_report = diagnostics.seal()
            quality_report = self.quality_builder.build(
                execution_id=ctx.execution_id,
                provider_name=provider.metadata.name,
                status=final_state,
                duration_seconds=execution_duration,
                metrics=final_metrics,
                validation_report=validation_report,
                diagnostics=diagnostics.bundle,
            )

            # Record health update
            if final_state in (ProviderState.COMPLETED, ProviderState.DEGRADED):
                self.health_monitor.record_success(provider.metadata.name, execution_duration, quality_report)
            else:
                self.health_monitor.record_failure(provider.metadata.name)

            acct = None
            if hasattr(provider, "get_accounting") and callable(provider.get_accounting):
                try:
                    acct = provider.get_accounting()
                except Exception as e:
                    logger.debug(f"Failed to retrieve acquisition accounting from {provider.metadata.name}: {e}")

            try:
                provider.shutdown()
            except Exception as e:
                logger.warning(f"Error shutting down provider {provider.metadata.name}: {e}")

        return ProviderResult(
            provider_name=provider.metadata.name,
            execution_id=ctx.execution_id,
            status=final_state,
            execution_duration=execution_duration,
            discovered_objects=discovered_items,
            parsed_objects=parsed_items,
            validation_report=validation_report,
            diagnostics=diag_report,
            quality_report=quality_report,
            metrics=final_metrics,
            accounting=acct,
            warnings=list(diag_report.warnings),
            errors=list(diag_report.errors),
        )

    async def execute_async(self, provider: BaseProvider) -> ProviderResult:
        """
        Asynchronously execute a provider instance.
        """
        # Execute synchronous pipeline in thread pool executor if provider is sync
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.execute, provider)
