"""
Metrics Collector

Thread-safe accumulator for all quantitative metrics produced during a provider
execution.  The ExecutionEngine owns one MetricsCollector per run and freezes it
into a ProviderMetrics instance at the end.

Providers and framework components call increment/set methods.
They never read from the collector directly — metrics flow out via flush().
"""

from __future__ import annotations

import threading
from typing import Dict

from providers.base.models import ProviderMetrics


class MetricsCollector:
    """
    Thread-safe counter and gauge accumulator for one provider execution.

    All methods are safe to call from concurrent threads (e.g., parallel fetch).
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: Dict[str, float] = {
            # Timing (seconds)
            "execution_duration": 0.0,
            "discovery_duration": 0.0,
            "fetch_duration": 0.0,
            "parse_duration": 0.0,
            "validate_duration": 0.0,
            # Request counters
            "request_count": 0.0,
            "bytes_downloaded": 0.0,
            "retry_count": 0.0,
            "rate_limit_waits": 0.0,
            # Outcome counters
            "success_count": 0.0,
            "failure_count": 0.0,
            "warning_count": 0.0,
            # Domain data
            "events_discovered": 0.0,
            "events_fetched": 0.0,
            "markets_found": 0.0,
            "odds_found": 0.0,
            "parsed_object_count": 0.0,
            "validation_failure_count": 0.0,
            # Browser
            "page_loads": 0.0,
            "js_errors": 0.0,
            "network_responses_intercepted": 0.0,
            "browser_restarts": 0.0,
        }

    # ──────────────────────────────────────────────────────────────────────────
    # Increment / Set
    # ──────────────────────────────────────────────────────────────────────────

    def increment(self, key: str, amount: float = 1.0) -> None:
        """Atomically increment a counter by amount."""
        with self._lock:
            if key in self._counters:
                self._counters[key] += amount
            else:
                self._counters[key] = amount

    def set(self, key: str, value: float) -> None:
        """Set a gauge value directly (overwrites previous value)."""
        with self._lock:
            self._counters[key] = value

    def set_stage_duration(self, stage: str, duration_seconds: float) -> None:
        """Record the duration of a lifecycle stage."""
        key = f"{stage}_duration"
        self.set(key, duration_seconds)
        if stage != "execution":
            # Also track in total if not already the overall
            pass

    # ──────────────────────────────────────────────────────────────────────────
    # Convenience Increment Helpers
    # ──────────────────────────────────────────────────────────────────────────

    def count_request(self, bytes_downloaded: int = 0) -> None:
        self.increment("request_count")
        if bytes_downloaded:
            self.increment("bytes_downloaded", bytes_downloaded)

    def count_retry(self) -> None:
        self.increment("retry_count")

    def count_rate_limit_wait(self) -> None:
        self.increment("rate_limit_waits")

    def count_event_discovered(self) -> None:
        self.increment("events_discovered")

    def count_events_discovered(self, count: int) -> None:
        self.increment("events_discovered", count)

    def count_events_fetched(self, count: int) -> None:
        self.increment("events_fetched", count)

    def count_markets(self, count: int) -> None:
        self.increment("markets_found", count)

    def count_odds(self, count: int) -> None:
        self.increment("odds_found", count)

    def count_parsed(self, count: int) -> None:
        self.increment("parsed_object_count", count)

    def count_validation_failures(self, count: int) -> None:
        self.increment("validation_failure_count", count)

    def count_page_load(self) -> None:
        self.increment("page_loads")

    def count_js_error(self) -> None:
        self.increment("js_errors")

    def count_network_response(self) -> None:
        self.increment("network_responses_intercepted")

    def count_browser_restart(self) -> None:
        self.increment("browser_restarts")

    def count_success(self) -> None:
        self.increment("success_count")

    def count_failure(self) -> None:
        self.increment("failure_count")

    def count_warning(self) -> None:
        self.increment("warning_count")

    # ──────────────────────────────────────────────────────────────────────────
    # Read
    # ──────────────────────────────────────────────────────────────────────────

    def get(self, key: str) -> float:
        with self._lock:
            return self._counters.get(key, 0.0)

    # ──────────────────────────────────────────────────────────────────────────
    # Flush to Immutable Snapshot
    # ──────────────────────────────────────────────────────────────────────────

    def flush(self) -> ProviderMetrics:
        """
        Produce an immutable ProviderMetrics snapshot.
        The collector remains usable after flush.
        """
        with self._lock:
            c = self._counters
            return ProviderMetrics(
                execution_duration=c["execution_duration"],
                discovery_duration=c["discovery_duration"],
                fetch_duration=c["fetch_duration"],
                parse_duration=c["parse_duration"],
                validate_duration=c["validate_duration"],
                request_count=int(c["request_count"]),
                bytes_downloaded=int(c["bytes_downloaded"]),
                retry_count=int(c["retry_count"]),
                rate_limit_waits=int(c["rate_limit_waits"]),
                success_count=int(c["success_count"]),
                failure_count=int(c["failure_count"]),
                warning_count=int(c["warning_count"]),
                events_discovered=int(c["events_discovered"]),
                events_fetched=int(c["events_fetched"]),
                markets_found=int(c["markets_found"]),
                odds_found=int(c["odds_found"]),
                parsed_object_count=int(c["parsed_object_count"]),
                validation_failure_count=int(c["validation_failure_count"]),
                page_loads=int(c["page_loads"]),
                js_errors=int(c["js_errors"]),
                network_responses_intercepted=int(c["network_responses_intercepted"]),
                browser_restarts=int(c["browser_restarts"]),
            )
