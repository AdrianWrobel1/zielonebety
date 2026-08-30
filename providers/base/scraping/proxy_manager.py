"""
Proxy Manager

Interface and production implementations for proxy management, round-robin rotation,
failure threshold tracking, and automatic cool-down recovery.
"""

from __future__ import annotations

import abc
import logging
import threading
import time
from typing import Dict, List, Optional

from providers.base.models import ProxyConfig
from providers.base.exceptions import ProxyError

logger = logging.getLogger("framework.proxy_manager")


class ProxyManager(abc.ABC):
    """Abstract interface for proxy selection, rotation, and health tracking."""

    @abc.abstractmethod
    def get_proxy(self) -> Optional[str]:
        """Return a proxy server URL or None if direct connection."""
        raise NotImplementedError

    @abc.abstractmethod
    def rotate(self) -> Optional[str]:
        """Rotate to next available proxy."""
        raise NotImplementedError

    @abc.abstractmethod
    def report_failure(self, proxy: str) -> None:
        """Report a proxy failure to track degradation."""
        raise NotImplementedError

    @abc.abstractmethod
    def report_success(self, proxy: str) -> None:
        """Report a proxy success to clear failure counter."""
        raise NotImplementedError


class StaticProxyManager(ProxyManager):
    """Single fixed proxy configuration."""

    def __init__(self, config: Optional[ProxyConfig] = None) -> None:
        self._config = config or ProxyConfig()

    def get_proxy(self) -> Optional[str]:
        return self._config.server if self._config.server else None

    def rotate(self) -> Optional[str]:
        return self.get_proxy()

    def report_failure(self, proxy: str) -> None:
        logger.warning(f"StaticProxyManager: Reported failure on static proxy {proxy}")

    def report_success(self, proxy: str) -> None:
        pass


class RotatingProxyManager(ProxyManager):
    """
    Round-robin proxy manager supporting failure counters, max failure thresholds,
    and automatic cool-down reinstatement.
    """

    def __init__(
        self,
        proxies: List[str],
        max_failures: int = 3,
        cooldown_seconds: float = 300.0
    ) -> None:
        self._lock = threading.Lock()
        self._proxies: List[str] = list(proxies)
        self.max_failures = max_failures
        self.cooldown_seconds = cooldown_seconds
        self._failure_counts: Dict[str, int] = {p: 0 for p in self._proxies}
        self._unhealthy_timestamp: Dict[str, float] = {}
        self._index: int = 0

    @property
    def total_proxies(self) -> int:
        return len(self._proxies)

    def get_proxy(self) -> Optional[str]:
        """
        Get current active proxy, auto-reinstating any proxies that have passed cooldown.
        """
        with self._lock:
            if not self._proxies:
                return None

            now = time.time()
            # Reinstate cooled-down proxies
            for p, ts in list(self._unhealthy_timestamp.items()):
                if now - ts >= self.cooldown_seconds:
                    logger.info(f"RotatingProxyManager: Cooldown period passed, reinstating proxy {p}")
                    del self._unhealthy_timestamp[p]
                    self._failure_counts[p] = 0

            healthy = [p for p in self._proxies if p not in self._unhealthy_timestamp]
            if not healthy:
                logger.warning("RotatingProxyManager: All proxies unhealthy, resetting pool counters")
                self._unhealthy_timestamp.clear()
                self._failure_counts = {p: 0 for p in self._proxies}
                healthy = self._proxies

            return healthy[self._index % len(healthy)]

    def rotate(self) -> Optional[str]:
        """Advance index and return next healthy proxy."""
        with self._lock:
            self._index += 1
        return self.get_proxy()

    def get_next_proxy(self) -> Optional[str]:
        """Alias for rotate()."""
        return self.rotate()

    def report_failure(self, proxy: str) -> None:
        """Increment failure count for proxy and mark unhealthy if threshold reached."""
        with self._lock:
            if proxy not in self._proxies:
                return

            self._failure_counts[proxy] = self._failure_counts.get(proxy, 0) + 1
            failures = self._failure_counts[proxy]

            if failures >= self.max_failures:
                self._unhealthy_timestamp[proxy] = time.time()
                logger.warning(
                    f"RotatingProxyManager: Proxy {proxy} exceeded max failures ({failures}/{self.max_failures}). "
                    f"Marked unhealthy for {self.cooldown_seconds}s cooldown."
                )

    def report_success(self, proxy: str) -> None:
        """Reset failure counter for successful proxy."""
        with self._lock:
            if proxy in self._failure_counts:
                self._failure_counts[proxy] = 0
                self._unhealthy_timestamp.pop(proxy, None)

