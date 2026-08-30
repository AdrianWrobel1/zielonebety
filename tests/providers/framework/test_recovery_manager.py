"""
Unit Tests for Recovery Strategies (Task 016)
"""

import pytest
from unittest.mock import MagicMock
from providers.base.exceptions import BrowserCrashError, SessionError, RateLimitError, ProxyError
from providers.base.recovery.recovery_strategy import (
    BrowserRestartRecovery,
    SessionRefreshRecovery,
    CookieRefreshRecovery,
    ProxyRotationRecovery,
    CompositeRecovery,
)


def test_browser_restart_recovery():
    strategy = BrowserRestartRecovery()
    err = BrowserCrashError("Browser process crashed")
    assert strategy.can_recover(err, None) is True

    mock_bm = MagicMock()
    provider_ctx = {"_browser_manager": mock_bm}

    strategy.recover(err, None, provider_context=provider_ctx)
    mock_bm.restart.assert_called_once()


def test_session_refresh_recovery():
    strategy = SessionRefreshRecovery()
    err = SessionError("Session connection dropped")
    assert strategy.can_recover(err, None) is True

    mock_sm = MagicMock()
    provider_ctx = {"_session_manager": mock_sm}

    strategy.recover(err, None, provider_context=provider_ctx)
    mock_sm.refresh.assert_called_once()


def test_cookie_refresh_recovery():
    strategy = CookieRefreshRecovery()
    err = SessionError("Cookie expired")
    assert strategy.can_recover(err, None) is True

    mock_cm = MagicMock()
    provider_ctx = {"_cookie_manager": mock_cm}

    strategy.recover(err, None, provider_context=provider_ctx)
    mock_cm.clear.assert_called_once()
    mock_cm.refresh.assert_called_once()


def test_proxy_rotation_recovery():
    strategy = ProxyRotationRecovery()
    err = ProxyError("Proxy connection failed")
    assert strategy.can_recover(err, None) is True

    mock_pm = MagicMock()
    provider_ctx = {"_proxy_manager": mock_pm}

    strategy.recover(err, None, provider_context=provider_ctx)
    mock_pm.rotate.assert_called_once()


def test_composite_recovery_default():
    composite = CompositeRecovery.default()
    assert len(composite._strategies) == 4

    err = RateLimitError("HTTP 429 Too Many Requests")
    assert composite.can_recover(err, None) is True

    # Call recover on composite with empty context - must not raise exception
    composite.recover(err, None, provider_context={})
