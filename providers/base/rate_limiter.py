"""
Rate Limiter

Token bucket rate limiter for controlling request throughput per provider.

Each provider gets its own RateLimiter instance configured from RateLimitConfig.
Thread-safe.  Async-safe via asyncio.sleep.

The framework creates and owns the rate limiter.
Providers never manage rate limiting directly.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Optional

from providers.base.models import RateLimitConfig

logger = logging.getLogger("framework.rate_limiter")


class RateLimiter:
    """
    Token bucket rate limiter.

    Tokens are refilled at `requests_per_second` rate.
    Up to `burst_limit` tokens can accumulate.
    A cooldown_ms pause is enforced after consuming all tokens.
    """

    def __init__(self, config: Optional[RateLimitConfig] = None) -> None:
        self._config = config or RateLimitConfig()
        self._lock = threading.Lock()

        self._tokens: float = float(self._config.burst_limit)
        self._last_refill: float = time.perf_counter()
        self._total_waits: int = 0
        self._total_wait_seconds: float = 0.0

    # ──────────────────────────────────────────────────────────────────────────
    # Synchronous Acquire
    # ──────────────────────────────────────────────────────────────────────────

    def acquire(self, tokens: int = 1) -> float:
        """
        Block until `tokens` tokens are available.

        Returns the seconds waited (0.0 if no wait was needed).
        """
        wait_start = time.perf_counter()

        while True:
            with self._lock:
                self._refill()
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    waited = time.perf_counter() - wait_start
                    if waited > 0.001:
                        self._total_waits += 1
                        self._total_wait_seconds += waited
                        logger.debug(f"[RateLimiter] Waited {waited:.3f}s for {tokens} token(s)")
                    return waited

            # Not enough tokens — wait for refill
            sleep_time = (tokens - self._tokens) / max(self._config.requests_per_second, 0.001)
            sleep_time = max(sleep_time, self._config.cooldown_ms / 1000)
            time.sleep(min(sleep_time, 1.0))  # Cap single sleep at 1s

    # ──────────────────────────────────────────────────────────────────────────
    # Asynchronous Acquire
    # ──────────────────────────────────────────────────────────────────────────

    async def acquire_async(self, tokens: int = 1) -> float:
        """
        Async version of acquire().  Non-blocking for the event loop.
        """
        wait_start = time.perf_counter()

        while True:
            with self._lock:
                self._refill()
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    waited = time.perf_counter() - wait_start
                    if waited > 0.001:
                        self._total_waits += 1
                        self._total_wait_seconds += waited
                    return waited

            sleep_time = (tokens - self._tokens) / max(self._config.requests_per_second, 0.001)
            sleep_time = max(sleep_time, self._config.cooldown_ms / 1000)
            await asyncio.sleep(min(sleep_time, 1.0))

    # ──────────────────────────────────────────────────────────────────────────
    # Non-Blocking Check
    # ──────────────────────────────────────────────────────────────────────────

    def try_acquire(self, tokens: int = 1) -> bool:
        """
        Non-blocking attempt to acquire tokens.
        Returns True if successful, False if rate limit is active.
        """
        with self._lock:
            self._refill()
            if self._tokens >= tokens:
                self._tokens -= tokens
                return True
            return False

    # ──────────────────────────────────────────────────────────────────────────
    # Statistics
    # ──────────────────────────────────────────────────────────────────────────

    @property
    def total_waits(self) -> int:
        return self._total_waits

    @property
    def total_wait_seconds(self) -> float:
        return self._total_wait_seconds

    @property
    def current_tokens(self) -> float:
        with self._lock:
            self._refill()
            return self._tokens

    # ──────────────────────────────────────────────────────────────────────────
    # Internal
    # ──────────────────────────────────────────────────────────────────────────

    def _refill(self) -> None:
        """Refill tokens based on elapsed time.  Must be called under _lock."""
        now = time.perf_counter()
        elapsed = now - self._last_refill
        new_tokens = elapsed * self._config.requests_per_second
        self._tokens = min(
            self._tokens + new_tokens,
            float(self._config.burst_limit)
        )
        self._last_refill = now

    def reset(self) -> None:
        """Reset the limiter to full token bucket (for testing)."""
        with self._lock:
            self._tokens = float(self._config.burst_limit)
            self._last_refill = time.perf_counter()
            self._total_waits = 0
            self._total_wait_seconds = 0.0

    def __repr__(self) -> str:
        return (
            f"RateLimiter("
            f"rps={self._config.requests_per_second}, "
            f"burst={self._config.burst_limit}, "
            f"tokens={self._tokens:.1f})"
        )
