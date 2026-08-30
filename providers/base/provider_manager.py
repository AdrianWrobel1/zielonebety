"""
Provider Manager

Provider Orchestration, Health Reporting, Quality Summaries, and Multi-Provider Execution.
"""

from __future__ import annotations

import asyncio
import time
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, field

from providers.base.provider_registry import ProviderRegistry
from providers.base.provider_factory import ProviderFactory
from providers.base.execution_engine import ExecutionEngine
from providers.base.provider_result import ProviderResult
from providers.base.provider_state import ProviderState
from providers.base.models import FrameworkHealthReport, HealthSnapshot, QualityReport
from providers.base.observability.health_monitor import get_global_health_monitor, HealthMonitor


@dataclass(frozen=True)
class AggregatedExecutionResult:
    total_duration: float
    total_providers: int
    successful_providers: int
    failed_providers: int
    disabled_providers: int
    results: Dict[str, ProviderResult]
    warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)


class ProviderManager:
    """Manager owning provider selection, orchestration, and health aggregation."""

    def __init__(
        self,
        engine: Optional[ExecutionEngine] = None,
        health_monitor: Optional[HealthMonitor] = None,
    ):
        self.health_monitor = health_monitor or get_global_health_monitor()
        self.engine = engine or ExecutionEngine(health_monitor=self.health_monitor)

    def execute_provider(
        self,
        name: str,
        config: Optional[Dict[str, Any]] = None,
        enabled: bool = True,
    ) -> ProviderResult:
        """Execute a single provider by name."""
        provider = ProviderFactory.create_provider(name=name, config=config, enabled=enabled)
        return self.engine.execute(provider)

    async def execute_provider_async(
        self,
        name: str,
        config: Optional[Dict[str, Any]] = None,
        enabled: bool = True,
    ) -> ProviderResult:
        """Asynchronously execute a single provider by name."""
        provider = ProviderFactory.create_provider(name=name, config=config, enabled=enabled)
        return await self.engine.execute_async(provider)

    def execute_providers(
        self,
        names: Optional[List[str]] = None,
        configs: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> AggregatedExecutionResult:
        """Execute multiple or all registered providers with error isolation."""
        start_time = time.perf_counter()
        target_names = names if names is not None else ProviderRegistry.list_providers()
        configs_map = configs or {}

        results: Dict[str, ProviderResult] = {}
        overall_warnings: List[str] = []
        overall_errors: List[str] = []

        success_count = 0
        failure_count = 0
        disabled_count = 0

        for provider_name in target_names:
            try:
                p_config = configs_map.get(provider_name, {})
                result = self.execute_provider(name=provider_name, config=p_config)
                results[provider_name] = result

                if result.status in (ProviderState.COMPLETED, ProviderState.DEGRADED):
                    success_count += 1
                elif result.status == ProviderState.CANCELLED:
                    disabled_count += 1
                else:
                    failure_count += 1
                    if result.errors:
                        overall_errors.extend(result.errors)

                if result.warnings:
                    overall_warnings.extend(result.warnings)

            except Exception as e:
                failure_count += 1
                err_msg = f"Orchestration failure for '{provider_name}': {e}"
                overall_errors.append(err_msg)
                results[provider_name] = ProviderResult.failure(
                    provider_name=provider_name,
                    execution_id="unknown",
                    error=err_msg,
                    started_at=time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                )

        total_duration = time.perf_counter() - start_time

        return AggregatedExecutionResult(
            total_duration=total_duration,
            total_providers=len(target_names),
            successful_providers=success_count,
            failed_providers=failure_count,
            disabled_providers=disabled_count,
            results=results,
            warnings=overall_warnings,
            errors=overall_errors,
        )

    def check_health(self, names: Optional[List[str]] = None) -> FrameworkHealthReport:
        """Collect current framework health status report."""
        target_names = names if names is not None else ProviderRegistry.list_providers()
        return self.health_monitor.get_framework_health(target_names)
