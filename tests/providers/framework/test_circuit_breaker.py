"""
Unit Tests for CircuitBreaker (Task 017)
"""

import time
import pytest
from providers.base.exceptions import CircuitBreakerOpenError
from providers.base.recovery.circuit_breaker import CircuitBreaker, CircuitState


def test_circuit_breaker_closed_state():
    cb = CircuitBreaker(provider_name="betclic", failure_threshold=3)
    assert cb.state == CircuitState.CLOSED

    res = cb.execute(lambda: "ok")
    assert res == "ok"
    assert cb.state == CircuitState.CLOSED


def test_circuit_breaker_trips_to_open():
    cb = CircuitBreaker(provider_name="betclic", failure_threshold=2)

    with pytest.raises(ValueError):
        cb.execute(lambda: (_ for _ in ()).throw(ValueError("Error 1")))

    assert cb.state == CircuitState.CLOSED

    with pytest.raises(ValueError):
        cb.execute(lambda: (_ for _ in ()).throw(ValueError("Error 2")))

    assert cb.state == CircuitState.OPEN


def test_circuit_breaker_rejects_in_open_state():
    cb = CircuitBreaker(provider_name="betclic", failure_threshold=1, recovery_timeout_seconds=60.0)

    with pytest.raises(ValueError):
        cb.execute(lambda: (_ for _ in ()).throw(ValueError("Fail")))

    assert cb.state == CircuitState.OPEN

    with pytest.raises(CircuitBreakerOpenError, match="is OPEN"):
        cb.execute(lambda: "should not run")


def test_circuit_breaker_half_open_recovery():
    cb = CircuitBreaker(
        provider_name="betclic",
        failure_threshold=1,
        recovery_timeout_seconds=0.1,
        success_threshold=2
    )

    # Trip breaker
    with pytest.raises(ValueError):
        cb.execute(lambda: (_ for _ in ()).throw(ValueError("Fail")))

    assert cb.state == CircuitState.OPEN

    # Wait for cooldown
    time.sleep(0.15)
    assert cb.state == CircuitState.HALF_OPEN

    # Trial 1 success
    cb.execute(lambda: "trial 1")
    assert cb.state == CircuitState.HALF_OPEN

    # Trial 2 success -> recovers to CLOSED
    cb.execute(lambda: "trial 2")
    assert cb.state == CircuitState.CLOSED


def test_circuit_breaker_reset():
    cb = CircuitBreaker(provider_name="betclic", failure_threshold=1)
    with pytest.raises(ValueError):
        cb.execute(lambda: (_ for _ in ()).throw(ValueError("Fail")))

    assert cb.state == CircuitState.OPEN

    cb.reset()
    assert cb.state == CircuitState.CLOSED
    assert cb.execute(lambda: "ok") == "ok"
