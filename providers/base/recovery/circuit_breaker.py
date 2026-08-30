"""
Circuit Breaker Pattern

Protects downstream providers and target sites from cascading failures.
Tracks failure rates and transitions between CLOSED, OPEN, and HALF_OPEN states.
"""

from __future__ import annotations

import asyncio
from enum import Enum, auto
import inspect
import logging
import threading
import time
from typing import Any, Callable, Optional

from providers.base.exceptions import CircuitBreakerOpenError

logger = logging.getLogger("framework.circuit_breaker")


class CircuitState(Enum):
    CLOSED = auto()     # Normal operation - all requests allowed
    OPEN = auto()       # Tripped - all requests rejected immediately
    HALF_OPEN = auto()  # Testing recovery - trial request allowed


class CircuitBreaker:
    """
    Thread-safe 3-state Circuit Breaker.
    """

    def __init__(
        self,
        provider_name: str = "default",
        failure_threshold: int = 5,
        recovery_timeout_seconds: float = 60.0,
        success_threshold: int = 2,
    ) -> None:
        self.provider_name = provider_name
        self.failure_threshold = failure_threshold
        self.recovery_timeout_seconds = recovery_timeout_seconds
        self.success_threshold = success_threshold

        self._lock = threading.Lock()
        self._state = CircuitState.CLOSED
        self._consecutive_failures = 0
        self._consecutive_successes = 0
        self._opened_at: Optional[float] = None

    @property
    def state(self) -> CircuitState:
        with self._lock:
            self._check_state_transition()
            return self._state

    def allow_execution(self) -> None:
        """
        Check if execution is allowed.
        Raises CircuitBreakerOpenError if circuit is OPEN.
        """
        with self._lock:
            self._check_state_transition()
            if self._state == CircuitState.OPEN:
                raise CircuitBreakerOpenError(
                    f"Circuit breaker for provider '{self.provider_name}' is OPEN. "
                    f"Tripped after {self.failure_threshold} consecutive failures.",
                    details={"provider": self.provider_name, "state": "OPEN"}
                )

    def record_success(self) -> None:
        """Record a successful operation."""
        with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                self._consecutive_successes += 1
                logger.debug(
                    f"CircuitBreaker[{self.provider_name}]: HALF_OPEN success "
                    f"({self._consecutive_successes}/{self.success_threshold})"
                )
                if self._consecutive_successes >= self.success_threshold:
                    self._state = CircuitState.CLOSED
                    self._consecutive_failures = 0
                    self._consecutive_successes = 0
                    self._opened_at = None
                    logger.info(f"CircuitBreaker[{self.provider_name}]: Recovered -> CLOSED")
            elif self._state == CircuitState.CLOSED:
                self._consecutive_failures = 0

    def record_failure(self, exc: Optional[Exception] = None) -> None:
        """Record a failed operation."""
        with self._lock:
            self._consecutive_failures += 1
            logger.warning(
                f"CircuitBreaker[{self.provider_name}]: Failure recorded "
                f"({self._consecutive_failures}/{self.failure_threshold})"
            )

            if self._state == CircuitState.HALF_OPEN:
                self._state = CircuitState.OPEN
                self._opened_at = time.time()
                self._consecutive_successes = 0
                logger.warning(f"CircuitBreaker[{self.provider_name}]: Trial failed -> OPEN")

            elif self._state == CircuitState.CLOSED and self._consecutive_failures >= self.failure_threshold:
                self._state = CircuitState.OPEN
                self._opened_at = time.time()
                logger.warning(
                    f"CircuitBreaker[{self.provider_name}]: Failure threshold reached -> OPEN "
                    f"(cooldown {self.recovery_timeout_seconds}s)"
                )

    def execute(self, fn: Callable[[], Any]) -> Any:
        """Execute callable protected by circuit breaker."""
        self.allow_execution()
        try:
            res = fn()
            self.record_success()
            return res
        except Exception as e:
            self.record_failure(e)
            raise

    async def execute_async(self, fn: Callable[[], Any]) -> Any:
        """Async execution protected by circuit breaker."""
        self.allow_execution()
        try:
            if inspect.iscoroutinefunction(fn):
                res = await fn()
            else:
                res = fn()
            self.record_success()
            return res
        except Exception as e:
            self.record_failure(e)
            raise

    def reset(self) -> None:
        """Force reset circuit breaker to CLOSED state."""
        with self._lock:
            self._state = CircuitState.CLOSED
            self._consecutive_failures = 0
            self._consecutive_successes = 0
            self._opened_at = None

    def _check_state_transition(self) -> None:
        """Must be called under _lock."""
        if self._state == CircuitState.OPEN and self._opened_at is not None:
            if time.time() - self._opened_at >= self.recovery_timeout_seconds:
                self._state = CircuitState.HALF_OPEN
                self._consecutive_successes = 0
                logger.info(f"CircuitBreaker[{self.provider_name}]: Cooldown expired -> HALF_OPEN")
