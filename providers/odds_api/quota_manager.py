"""
Odds API.io Quota Manager — Hourly Request Budget Enforcement

Implements a module-level singleton that tracks API requests made within
a rolling 1-hour window and enforces a configurable safety budget.

The free tier of Odds-API.io is limited to ~100 requests/hour.
Default internal safety budget: 90 requests/hour (leaving 10 reserve).

Accounting model (P1-008 contract):

  - One recorded request == one transport handoff (one chunk HTTP call).
  - Every real HTTP attempt is counted, including retried attempts, because
    each retry re-sends the request.
  - A quota-block decision or a cache hit sends no HTTP and counts nothing.
  - Recording happens at transport handoff (before the response arrives):
    a handed-off request may already have reached the provider (e.g. a read
    timeout after the server counted it), so counting at handoff is the
    conservative direction. The 90-vs-100 headroom absorbs this error, and
    the provider's own HTTP 429 remains the authoritative backstop.

Exhaustion contract (P1-008, explicit choice B):

  - Internal budget exhaustion does NOT raise. Fetchers return
    partial/cached results with explicit telemetry (blocked counters and
    the ``budget_exhausted`` status flag) so callers can distinguish
    "quota blocked" from "empty success".
  - External quota exhaustion (HTTP 429) raises OddsApiQuotaExceededError.

Durability (P1-008):

  - State is persisted to a small JSON file (atomic replace) so a process
    restart inside the same quota window does NOT silently reset usage to
    zero. ``ODDS_API_QUOTA_STATE_PATH`` overrides the location.

This module persists across provider instance recreation because it lives
at module scope, not on provider instances.
"""

import json
import logging
import os
import tempfile
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("zielonebety.provider.odds_api.quota")

# Rolling window duration in seconds (1 hour)
_WINDOW_SECONDS = 3600.0

# Default hourly budget (internal safety limit below external 100/hour)
DEFAULT_HOURLY_BUDGET = 90

# Environment override for the durable state file location.
STATE_PATH_ENV_VAR = "ODDS_API_QUOTA_STATE_PATH"


def _default_state_path() -> str:
    return os.path.join(tempfile.gettempdir(), "zielonebety_odds_api_quota.json")


def _resolve_state_path(explicit: Optional[str] = None) -> str:
    if explicit:
        return explicit
    return os.environ.get(STATE_PATH_ENV_VAR) or _default_state_path()


class OddsApiQuotaManager:
    """Thread-safe hourly request budget tracker for Odds API.io.

    Tracks requests via timestamps in a rolling 1-hour window.
    Refuses new requests when the internal safety budget is exhausted.
    """

    def __init__(self, hourly_budget: int = DEFAULT_HOURLY_BUDGET, state_path: Optional[str] = None) -> None:
        self._lock = threading.Lock()
        self._hourly_budget = max(1, hourly_budget)
        self._state_path = _resolve_state_path(state_path)
        # List of request timestamps within the current window
        self._request_timestamps: List[float] = []
        # Cumulative counters (never reset, for telemetry)
        self._total_requests: int = 0
        self._total_blocked: int = 0
        self._load_state()

    @property
    def state_path(self) -> str:
        return self._state_path

    def _load_state(self) -> None:
        """Load persisted window state; corrupt/missing files start empty."""
        try:
            with open(self._state_path, "r", encoding="utf-8") as f:
                payload = json.load(f)
        except FileNotFoundError:
            return
        except (OSError, ValueError) as e:
            logger.warning("Odds API quota state unreadable (%s); starting with empty window.", e)
            return
        try:
            now = time.time()
            cutoff = now - _WINDOW_SECONDS
            timestamps = [float(t) for t in payload.get("timestamps", []) if isinstance(t, (int, float))]
            with self._lock:
                self._request_timestamps = sorted(t for t in timestamps if t >= cutoff)
                self._total_requests = int(payload.get("total_requests", 0) or 0)
                self._total_blocked = int(payload.get("total_blocked", 0) or 0)
        except (TypeError, ValueError) as e:
            logger.warning("Odds API quota state malformed (%s); starting with empty window.", e)

    def _save_state_locked(self) -> None:
        """Atomically persist window state. Caller must hold the lock."""
        payload = {
            "timestamps": list(self._request_timestamps),
            "total_requests": self._total_requests,
            "total_blocked": self._total_blocked,
        }
        try:
            parent = os.path.dirname(os.path.abspath(self._state_path))
            if parent:
                os.makedirs(parent, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(dir=parent, prefix=".quota_", suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(payload, f)
                os.replace(tmp_name, self._state_path)
            except BaseException:
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass
                raise
        except OSError as e:
            # Durability is best-effort: in-memory accounting stays truthful.
            logger.warning("Odds API quota state could not be persisted (%s).", e)

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
        """Record that `count` API requests were handed to the transport."""
        now = time.time()
        with self._lock:
            self._prune_expired(now)
            for _ in range(count):
                self._request_timestamps.append(now)
            self._total_requests += count
            self._save_state_locked()

    def record_blocked(self, count: int = 1) -> None:
        """Record that `count` requests were blocked by budget."""
        with self._lock:
            self._total_blocked += count
            self._save_state_locked()

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
        """Reset all counters, including the persisted state (for testing)."""
        with self._lock:
            self._request_timestamps.clear()
            self._total_requests = 0
            self._total_blocked = 0
            self._save_state_locked()


# ─────────────────────────────────────────────────────────────────────────────
# Module-level singleton (survives provider instance recreation)
# ─────────────────────────────────────────────────────────────────────────────
_global_quota_manager: OddsApiQuotaManager | None = None
_global_quota_lock = threading.Lock()


def get_quota_manager(hourly_budget: int = DEFAULT_HOURLY_BUDGET) -> OddsApiQuotaManager:
    """Return the global quota manager singleton, creating it if needed."""
    global _global_quota_manager
    with _global_quota_lock:
        if _global_quota_manager is None:
            _global_quota_manager = OddsApiQuotaManager(hourly_budget=hourly_budget)
        return _global_quota_manager


def reset_global_quota_manager() -> None:
    """Drop the global singleton so the next access reloads persisted state.

    Test hook for isolation between suites with different state paths.
    Production code must use :func:`get_quota_manager`.
    """
    global _global_quota_manager
    with _global_quota_lock:
        _global_quota_manager = None
