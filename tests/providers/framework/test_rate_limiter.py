"""
Unit Tests for RateLimiter (Task 013)
"""

import time
import pytest
from providers.base.rate_limiter import RateLimiter
from providers.base.models import RateLimitConfig


def test_rate_limiter_burst():
    limiter = RateLimiter(config=RateLimitConfig(requests_per_second=10.0, burst_limit=5))
    assert limiter.try_acquire(1) is True
    assert limiter.try_acquire(1) is True
    assert limiter.try_acquire(1) is True
    assert limiter.try_acquire(1) is True
    assert limiter.try_acquire(1) is True
    # Bucket empty
    assert limiter.try_acquire(1) is False


def test_rate_limiter_acquire_wait():
    limiter = RateLimiter(config=RateLimitConfig(requests_per_second=100.0, burst_limit=1, cooldown_ms=10))
    limiter.acquire(1)
    start = time.perf_counter()
    waited = limiter.acquire(1)
    duration = time.perf_counter() - start
    assert duration >= 0.005


@pytest.mark.asyncio
async def test_rate_limiter_acquire_async():
    limiter = RateLimiter(config=RateLimitConfig(requests_per_second=100.0, burst_limit=1, cooldown_ms=10))
    await limiter.acquire_async(1)
    start = time.perf_counter()
    waited = await limiter.acquire_async(1)
    duration = time.perf_counter() - start
    assert duration >= 0.005


def test_rate_limiter_reset_and_stats():
    limiter = RateLimiter(config=RateLimitConfig(requests_per_second=100.0, burst_limit=1, cooldown_ms=10))
    limiter.acquire(1)
    limiter.acquire(1)

    assert limiter.total_waits >= 1
    assert limiter.total_wait_seconds > 0.0

    limiter.reset()
    assert limiter.total_waits == 0
    assert limiter.total_wait_seconds == 0.0
    assert limiter.current_tokens == 1.0
