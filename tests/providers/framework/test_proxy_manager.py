"""
Unit Tests for ProxyManager (Task 007)
"""

import time
import pytest
from providers.base.models import ProxyConfig
from providers.base.scraping.proxy_manager import StaticProxyManager, RotatingProxyManager


def test_static_proxy_manager():
    config = ProxyConfig(server="http://static-proxy.test:8080")
    spm = StaticProxyManager(config)

    assert spm.get_proxy() == "http://static-proxy.test:8080"
    assert spm.rotate() == "http://static-proxy.test:8080"

    empty_spm = StaticProxyManager()
    assert empty_spm.get_proxy() is None


def test_rotating_proxy_manager_rotation():
    proxies = ["http://proxy1:8080", "http://proxy2:8080", "http://proxy3:8080"]
    rpm = RotatingProxyManager(proxies)

    assert rpm.total_proxies == 3
    assert rpm.get_proxy() == "http://proxy1:8080"
    assert rpm.rotate() == "http://proxy2:8080"
    assert rpm.rotate() == "http://proxy3:8080"
    assert rpm.rotate() == "http://proxy1:8080"


def test_rotating_proxy_manager_failure_threshold():
    proxies = ["http://proxy1:8080", "http://proxy2:8080"]
    rpm = RotatingProxyManager(proxies, max_failures=2, cooldown_seconds=60.0)

    # First failure on proxy1
    rpm.report_failure("http://proxy1:8080")
    assert rpm.get_proxy() == "http://proxy1:8080"

    # Second failure triggers exclusion
    rpm.report_failure("http://proxy1:8080")
    # Now only proxy2 should be returned
    assert rpm.get_proxy() == "http://proxy2:8080"
    assert rpm.rotate() == "http://proxy2:8080"


def test_rotating_proxy_manager_cooldown_recovery():
    proxies = ["http://proxy1:8080"]
    # Cooldown of 0.1s
    rpm = RotatingProxyManager(proxies, max_failures=1, cooldown_seconds=0.1)

    rpm.report_failure("http://proxy1:8080")
    # All proxies unhealthy -> resets counters as fallback
    assert rpm.get_proxy() == "http://proxy1:8080"

    # Test cooldown sleep
    rpm.report_failure("http://proxy1:8080")
    time.sleep(0.15)
    # Should be reinstated after cooldown
    assert rpm.get_proxy() == "http://proxy1:8080"


def test_rotating_proxy_manager_report_success():
    proxies = ["http://proxy1:8080", "http://proxy2:8080"]
    rpm = RotatingProxyManager(proxies, max_failures=2)

    rpm.report_failure("http://proxy1:8080")
    assert rpm._failure_counts["http://proxy1:8080"] == 1

    rpm.report_success("http://proxy1:8080")
    assert rpm._failure_counts["http://proxy1:8080"] == 0
