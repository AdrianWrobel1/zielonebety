"""
Performance Profiler

Records per-stage timing and produces performance analysis reports.
Used by the ExecutionEngine to identify bottlenecks and measure framework overhead.
"""

from __future__ import annotations

import time
import logging
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, Generator, List, Optional

logger = logging.getLogger("framework.performance")


@dataclass
class StageMeasurement:
    """Timing data for a single stage or sub-operation."""
    stage: str
    started_at: float        # perf_counter timestamp
    finished_at: Optional[float] = None
    duration_ms: Optional[float] = None
    succeeded: bool = True
    sub_operations: List["StageMeasurement"] = field(default_factory=list)

    def finish(self, succeeded: bool = True) -> None:
        self.finished_at = time.perf_counter()
        self.succeeded = succeeded
        if self.started_at:
            self.duration_ms = (self.finished_at - self.started_at) * 1000

    def __repr__(self) -> str:
        return f"StageMeasurement(stage={self.stage!r}, duration_ms={self.duration_ms:.1f})"


@dataclass(frozen=True)
class PerformanceReport:
    """Immutable performance summary for one provider execution."""
    execution_id: str
    provider_name: str
    total_duration_ms: float
    stage_measurements: List[StageMeasurement]
    slowest_stage: Optional[str]
    slowest_stage_ms: float
    framework_overhead_ms: float   # total - sum(provider stages)
    generated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @property
    def has_bottleneck(self) -> bool:
        """True if any single stage consumed >60% of total time."""
        if self.total_duration_ms <= 0:
            return False
        return self.slowest_stage_ms / self.total_duration_ms > 0.6

    def summary(self) -> str:
        lines = [
            f"Performance Report [{self.provider_name} / {self.execution_id[:8]}]",
            f"  Total: {self.total_duration_ms:.0f}ms",
            f"  Framework overhead: {self.framework_overhead_ms:.0f}ms",
        ]
        for m in sorted(self.stage_measurements, key=lambda x: (x.duration_ms or 0), reverse=True):
            pct = (m.duration_ms or 0) / max(self.total_duration_ms, 1) * 100
            lines.append(f"  {m.stage:20s}: {m.duration_ms or 0:8.1f}ms  ({pct:.1f}%)")
        if self.has_bottleneck:
            lines.append(f"  ⚠ BOTTLENECK: {self.slowest_stage} ({self.slowest_stage_ms:.0f}ms)")
        return "\n".join(lines)


class PerformanceProfiler:
    """
    Records wall-clock timings for each lifecycle stage.

    Usage:
        profiler = PerformanceProfiler(execution_id, provider_name)
        with profiler.measure("discovery"):
            ...do discovery...
        report = profiler.report()
    """

    PROVIDER_STAGES = {"discovery", "fetch", "parse", "validate"}

    def __init__(self, execution_id: str, provider_name: str) -> None:
        self._execution_id = execution_id
        self._provider_name = provider_name
        self._execution_start: float = time.perf_counter()
        self._measurements: List[StageMeasurement] = []

    @contextmanager
    def measure(self, stage: str) -> Generator[StageMeasurement, None, None]:
        """Context manager that auto-records start and end of a stage."""
        m = StageMeasurement(stage=stage, started_at=time.perf_counter())
        self._measurements.append(m)
        succeeded = True
        try:
            yield m
        except Exception:
            succeeded = False
            raise
        finally:
            m.finish(succeeded=succeeded)
            logger.debug(
                f"[Profiler:{self._execution_id[:8]}] {stage}: {m.duration_ms:.1f}ms"
            )

    def report(self) -> PerformanceReport:
        """Build and return the immutable performance report."""
        total_ms = (time.perf_counter() - self._execution_start) * 1000

        stage_sum_ms = sum(
            m.duration_ms or 0
            for m in self._measurements
            if m.stage in self.PROVIDER_STAGES
        )
        overhead_ms = max(0.0, total_ms - stage_sum_ms)

        slowest: Optional[StageMeasurement] = None
        if self._measurements:
            slowest = max(self._measurements, key=lambda m: m.duration_ms or 0)

        report = PerformanceReport(
            execution_id=self._execution_id,
            provider_name=self._provider_name,
            total_duration_ms=total_ms,
            stage_measurements=list(self._measurements),
            slowest_stage=slowest.stage if slowest else None,
            slowest_stage_ms=slowest.duration_ms or 0 if slowest else 0,
            framework_overhead_ms=overhead_ms,
        )
        logger.info(f"\n{report.summary()}")
        return report

    def elapsed_ms(self) -> float:
        """Return elapsed time in milliseconds since profiler was created."""
        return (time.perf_counter() - self._execution_start) * 1000
