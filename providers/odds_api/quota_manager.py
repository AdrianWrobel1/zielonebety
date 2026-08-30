"""
Odds API.io Quota Manager — Hourly Request Budget Enforcement

Implements a module-level singleton that tracks API requests made within
a rolling 1-hour window and enforces a configurable safety budget.

The free tier of Odds-API.io is limited to ~100 requests/hour.
Default internal safety budget: 90 requests/hour (leaving 10 reserve).

This module persists across provider instance recreation because it lives
at module scope, not on provider instances.
"""

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Tuple

logger = logging.getLogger("zielonebety.provider.odds_api.quota")

# Rolling window duration in seconds (1 hour)
_WINDOW_SECONDS = 3600.0

# Default hourly budget (internal safety limit below external 100/hour)
DEFAULT_HOURLY_BUDGET = 90


class OddsApiQuotaManager:
    """Thread-safe hourly request budget tracker for Odds API.io.

    Tracks requests via timestamps in a rolling 1-hour window.
    Refuses new requests when the internal safety budget is exhausted.
    """

    def __init__(self, hourly_budget: int = DEFAULT_HOURLY_BUDGET) -> None:
        self._lock = threading.Lock()
        self._hourly_budget = max(1, hourly_budget)
        # List of request timestamps within the current window
        self._request_timestamps: List[float] = []
        # Cumulative counters (never reset, for telemetry)
        self._total_requests: int = 0
        self._total_blocked: int = 0

    @property
    def hourly_budget(self) -> int:
        return self._hourly_budget

    @hourly_budget.setter
    def hourly_budget(self, value: int) -> None:
        with self._lock:
            self._hourly_budget = max(1, value)

    def _prune_expired(self, now: float) -> None:
        """Remove timestamps older than the rolling window. Must hold lock."""
        cutoff = now - _WINDOW_SECONDS
        while self._request_timestamps and self._request_timestamps[0] < cutoff:
            self._request_timestamps.pop(0)

    def can_request(self, count: int = 1) -> bool:
        """Check if the budget allows `count` more requests in the current window."""
        now = time.time()
        with self._lock:
            self._prune_expired(now)
            return (len(self._request_timestamps) + count) <= self._hourly_budget

    def record_request(self, count: int = 1) -> None:
        """Record that `count` API requests were made."""
        now = time.time()
        with self._lock:
            self._prune_expired(now)
            for _ in range(count):
                self._request_timestamps.append(now)
            self._total_requests += count

    def record_blocked(self, count: int = 1) -> None:
        """Record that `count` requests were blocked by budget."""
        with self._lock:
            self._total_blocked += count

    def requests_in_window(self) -> int:
        """Current number of requests in the rolling window."""
        now = time.time()
        with self._lock:
            self._prune_expired(now)
            return len(self._request_timestamps)

    def remaining_budget(self) -> int:
        """Remaining requests allowed in the current window."""
        return max(0, self._hourly_budget - self.requests_in_window())

    def get_status(self) -> Dict[str, Any]:
        """Return a JSON-serializable quota status snapshot for telemetry."""
        now = time.time()
        with self._lock:
            self._prune_expired(now)
            used = len(self._request_timestamps)
            remaining = max(0, self._hourly_budget - used)
            window_start = self._request_timestamps[0] if self._request_timestamps else now
            window_age_seconds = now - window_start if self._request_timestamps else 0.0

        return {
            "hourly_budget": self._hourly_budget,
            "requests_used_this_window": used,
            "requests_remaining": remaining,
            "budget_exhausted": remaining <= 0,
            "total_requests_lifetime": self._total_requests,
            "total_blocked_lifetime": self._total_blocked,
            "window_age_seconds": round(window_age_seconds, 1),
        }

    def reset(self) -> None:
        """Reset all counters (for testing)."""
        with self._lock:
            self._request_timestamps.clear()
            self._total_requests = 0
            self._total_blocked = 0


# ─────────────────────────────────────────────────────────────────────────────
# Module-level singleton (survives provider instance recreation)
# ─────────────────────────────────────────────────────────────────────────────
_global_quota_manager: OddsApiQuotaManager | None = None


def get_quota_manager(hourly_budget: int = DEFAULT_HOURLY_BUDGET) -> OddsApiQuotaManager:
    """Return the global quota manager singleton, creating it if needed."""
    global _global_quota_manager
    if _global_quota_manager is None:
        _global_quota_manager = OddsApiQuotaManager(hourly_budget=hourly_budget)
    return _global_quota_manager
