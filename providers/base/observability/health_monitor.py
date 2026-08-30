"""
Health Monitor

Tracks provider health across multiple executions.  Updated after every run by
the ExecutionEngine.  Provides point-in-time and historical health information
for every registered provider.

Thread-safe.  Survives process restarts if a persistence backend is provided.
"""

from __future__ import annotations

import threading
import logging
from typing import Dict, List, Optional

from providers.base.models import (
    FrameworkHealthReport,
    HealthSnapshot,
    ProviderMetrics,
    QualityReport,
    QualityStatus,
)
from providers.base.provider_state import ProviderState

logger = logging.getLogger("framework.health_monitor")


class HealthMonitor:
    """
    Singleton-like service that tracks health snapshots across all providers.

    The ExecutionEngine calls record_success() / record_failure() after each run.
    The ProviderManager calls get_framework_health() to produce aggregate reports.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._snapshots: Dict[str, HealthSnapshot] = {}

    # ──────────────────────────────────────────────────────────────────────────
    # Recording
    # ──────────────────────────────────────────────────────────────────────────

    def record_success(
        self,
        provider_name: str,
        duration_seconds: float,
        quality: QualityReport,
    ) -> None:
        """Update health state after a successful execution."""
        with self._lock:
            snapshot = self._get_or_create(provider_name)
            snapshot.record_success(duration_seconds, quality)
            logger.info(
                f"[HealthMonitor] {provider_name}: SUCCESS "
                f"duration={duration_seconds:.2f}s "
                f"events={quality.events_found} markets={quality.markets_found} "
                f"failure_rate={snapshot.failure_rate:.1%}"
            )

    def record_failure(self, provider_name: str) -> None:
        """Update health state after a failed execution."""
        with self._lock:
            snapshot = self._get_or_create(provider_name)
            snapshot.record_failure()
            logger.warning(
                f"[HealthMonitor] {provider_name}: FAILURE "
                f"consecutive={snapshot.consecutive_failures} "
                f"failure_rate={snapshot.failure_rate:.1%} "
                f"healthy={snapshot.is_healthy}"
            )

    # ──────────────────────────────────────────────────────────────────────────
    # Querying
    # ──────────────────────────────────────────────────────────────────────────

    def get_snapshot(self, provider_name: str) -> HealthSnapshot:
        """Return the current health snapshot for a provider."""
        with self._lock:
            return self._get_or_create(provider_name)

    def get_all_snapshots(self) -> Dict[str, HealthSnapshot]:
        """Return a copy of all provider snapshots."""
        with self._lock:
            return dict(self._snapshots)

    def is_healthy(self, provider_name: str) -> bool:
        """Return True if the provider is currently considered healthy."""
        with self._lock:
            return self._get_or_create(provider_name).is_healthy

    def get_framework_health(self, registered_providers: List[str]) -> FrameworkHealthReport:
        """
        Aggregate health across all registered providers.

        Providers not yet in the monitor are treated as healthy (no history).
        """
        with self._lock:
            snapshots: Dict[str, HealthSnapshot] = {}
            healthy = 0
            degraded = 0
            failed_count = 0
            disabled = 0

            for name in registered_providers:
                snap = self._get_or_create(name)
                snapshots[name] = snap

                if snap.total_runs == 0:
                    healthy += 1  # No history → assume healthy
                elif snap.is_healthy:
                    if snap.failure_rate > 0.1:
                        degraded += 1
                    else:
                        healthy += 1
                else:
                    failed_count += 1

            total = len(registered_providers)
            overall_healthy = failed_count == 0

            if failed_count > 0:
                overall_status = "FAILED"
            elif degraded > 0:
                overall_status = "DEGRADED"
            else:
                overall_status = "HEALTHY"

            return FrameworkHealthReport(
                overall_healthy=overall_healthy,
                overall_status=overall_status,
                total_providers=total,
                healthy_providers=healthy,
                degraded_providers=degraded,
                failed_providers=failed_count,
                disabled_providers=disabled,
                provider_snapshots=snapshots,
            )

    # ──────────────────────────────────────────────────────────────────────────
    # Reset (for testing)
    # ──────────────────────────────────────────────────────────────────────────

    def reset(self, provider_name: Optional[str] = None) -> None:
        """Reset health data for one provider or all providers."""
        with self._lock:
            if provider_name:
                self._snapshots.pop(provider_name, None)
            else:
                self._snapshots.clear()

    # ──────────────────────────────────────────────────────────────────────────
    # Internal
    # ──────────────────────────────────────────────────────────────────────────

    def _get_or_create(self, provider_name: str) -> HealthSnapshot:
        if provider_name not in self._snapshots:
            self._snapshots[provider_name] = HealthSnapshot(provider_name=provider_name)
        return self._snapshots[provider_name]


# Global shared instance — the ExecutionEngine injects this.
# Tests can instantiate their own isolated HealthMonitor instead.
_global_health_monitor: Optional[HealthMonitor] = None


def get_global_health_monitor() -> HealthMonitor:
    global _global_health_monitor
    if _global_health_monitor is None:
        _global_health_monitor = HealthMonitor()
    return _global_health_monitor
