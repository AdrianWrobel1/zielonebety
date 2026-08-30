"""
RetryEngine Unit Tests
"""

import pytest
from providers.base.recovery.retry_engine import RetryEngine
from providers.base.models import RetryConfig
from providers.base.exceptions import RetryExhaustedError, NonRetryableError, FetchError


def test_retry_engine_success_first_try():
    engine = RetryEngine(config=RetryConfig(max_retries=3, base_delay_seconds=0.01))
    call_count = 0

    def fn():
        nonlocal call_count
        call_count += 1
        return "success"

    result = engine.execute(fn, stage="test")
    assert result == "success"
    assert call_count == 1


def test_retry_engine_retries_transient_error():
    engine = RetryEngine(config=RetryConfig(max_retries=3, base_delay_seconds=0.01))
    call_count = 0

    def fn():
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise FetchError("Transient failure")
        return "recovered"

    result = engine.execute(fn, stage="test")
    assert result == "recovered"
    assert call_count == 3


def test_retry_engine_exhausts_retries():
    engine = RetryEngine(config=RetryConfig(max_retries=2, base_delay_seconds=0.01))
    call_count = 0

    def fn():
        nonlocal call_count
        call_count += 1
        raise FetchError("Persistent failure")

    with pytest.raises(RetryExhaustedError) as exc_info:
        engine.execute(fn, stage="test")

    assert exc_info.value.attempt_count == 3
    assert call_count == 3


def test_retry_engine_non_retryable_fails_immediately():
    engine = RetryEngine(config=RetryConfig(max_retries=3, base_delay_seconds=0.01))
    call_count = 0

    def fn():
        nonlocal call_count
        call_count += 1
        raise NonRetryableError("Permanent bug")

    with pytest.raises(NonRetryableError):
        engine.execute(fn, stage="test")

    assert call_count == 1


@pytest.mark.asyncio
async def test_retry_engine_async_recovery_callback():
    engine = RetryEngine(config=RetryConfig(max_retries=2, base_delay_seconds=0.01))
    call_count = 0
    recovery_count = 0

    async def async_fn():
        nonlocal call_count
        call_count += 1
        if call_count < 2:
            raise FetchError("Temporary timeout")
        return "async_recovered"

    async def recovery_cb(ctx):
        nonlocal recovery_count
        recovery_count += 1
        assert ctx.stage == "async_test"

    result = await engine.execute_async(async_fn, stage="async_test", recovery=recovery_cb)
    assert result == "async_recovered"
    assert call_count == 2
    assert recovery_count == 1

