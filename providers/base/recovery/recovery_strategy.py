"""
Recovery Strategies

Defines the RecoveryStrategy abstraction and concrete implementations.
The RetryEngine invokes recovery strategies between retry attempts to restore
a healthy execution state before re-attempting the failed operation.

Recovery strategies are composable (CompositeRecovery tries them in order).
"""

from __future__ import annotations

import abc
import logging
from typing import List, Optional

logger = logging.getLogger("framework.recovery")


# ─────────────────────────────────────────────────────────────────────────────
# Abstract Base
# ─────────────────────────────────────────────────────────────────────────────

class RecoveryStrategy(abc.ABC):
    """
    Abstract recovery action invoked between retry attempts.

    Implementations attempt to restore a healthy execution state.
    They must NOT raise exceptions — failures are logged and ignored
    so the retry attempt can proceed regardless.
    """

    @abc.abstractmethod
    def can_recover(self, error: Exception, context) -> bool:
        """
        Return True if this strategy can handle the given error.

        Args:
            error: The exception that triggered the retry.
            context: The RetryContext from the retry engine.
        """
        raise NotImplementedError

    @abc.abstractmethod
    def recover(self, error: Exception, context, provider_context=None) -> None:
        """
        Attempt to recover from the error.

        Must NOT raise — log failures and return gracefully.
        """
        raise NotImplementedError

    @property
    def name(self) -> str:
        return self.__class__.__name__


# ─────────────────────────────────────────────────────────────────────────────
# Concrete Implementations
# ─────────────────────────────────────────────────────────────────────────────

class NoOpRecovery(RecoveryStrategy):
    """Recovery strategy that does nothing.  Used as a safe default."""

    def can_recover(self, error: Exception, context) -> bool:
        return True

    def recover(self, error: Exception, context, provider_context=None) -> None:
        pass


class BrowserRestartRecovery(RecoveryStrategy):
    """
    Attempts to restart the browser after a crash.

    Requires the provider_context to have a browser manager reference
    accessible via provider_context.get('_browser_manager').
    """

    def can_recover(self, error: Exception, context) -> bool:
        from providers.base.exceptions import BrowserCrashError, PageCrashError, BrowserError
        return isinstance(error, (BrowserCrashError, PageCrashError, BrowserError))

    def recover(self, error: Exception, context, provider_context=None) -> None:
        logger.info(f"[BrowserRestartRecovery] Attempting browser restart after {type(error).__name__}")
        try:
            if provider_context is None:
                logger.warning("[BrowserRestartRecovery] No provider_context available — cannot restart")
                return

            browser_manager = provider_context.get("_browser_manager")
            if browser_manager is None:
                logger.warning("[BrowserRestartRecovery] No browser manager in context")
                return

            browser_manager.restart()
            logger.info("[BrowserRestartRecovery] Browser restarted successfully")

        except Exception as e:
            logger.warning(f"[BrowserRestartRecovery] Restart failed: {e}")


class SessionRefreshRecovery(RecoveryStrategy):
    """
    Attempts to refresh the HTTP session after a session-level error.
    """

    def can_recover(self, error: Exception, context) -> bool:
        from providers.base.exceptions import SessionError, RateLimitError
        return isinstance(error, (SessionError, RateLimitError))

    def recover(self, error: Exception, context, provider_context=None) -> None:
        logger.info(f"[SessionRefreshRecovery] Refreshing HTTP session after {type(error).__name__}")
        try:
            if provider_context is None:
                return

            session_manager = provider_context.get("_session_manager")
            if session_manager is None:
                return

            session_manager.refresh()
            logger.info("[SessionRefreshRecovery] Session refreshed successfully")

        except Exception as e:
            logger.warning(f"[SessionRefreshRecovery] Session refresh failed: {e}")


class CookieRefreshRecovery(RecoveryStrategy):
    """
    Clears and re-fetches cookies after authentication-related errors.
    """

    def can_recover(self, error: Exception, context) -> bool:
        from providers.base.exceptions import SessionError
        return isinstance(error, SessionError)

    def recover(self, error: Exception, context, provider_context=None) -> None:
        logger.info(f"[CookieRefreshRecovery] Refreshing cookies after {type(error).__name__}")
        try:
            if provider_context is None:
                return

            cookie_manager = provider_context.get("_cookie_manager")
            if cookie_manager is None:
                return

            cookie_manager.clear()
            cookie_manager.refresh()
            logger.info("[CookieRefreshRecovery] Cookies refreshed")

        except Exception as e:
            logger.warning(f"[CookieRefreshRecovery] Cookie refresh failed: {e}")


class ProxyRotationRecovery(RecoveryStrategy):
    """
    Rotates to the next available proxy after a proxy failure.
    """

    def can_recover(self, error: Exception, context) -> bool:
        from providers.base.exceptions import ProxyError, RateLimitError
        return isinstance(error, (ProxyError, RateLimitError))

    def recover(self, error: Exception, context, provider_context=None) -> None:
        logger.info(f"[ProxyRotationRecovery] Rotating proxy after {type(error).__name__}")
        try:
            if provider_context is None:
                return

            proxy_manager = provider_context.get("_proxy_manager")
            if proxy_manager is None:
                return

            new_proxy = proxy_manager.rotate()
            logger.info(f"[ProxyRotationRecovery] Rotated to proxy: {new_proxy}")

        except Exception as e:
            logger.warning(f"[ProxyRotationRecovery] Proxy rotation failed: {e}")


class CompositeRecovery(RecoveryStrategy):
    """
    Tries multiple recovery strategies in order.
    Stops after the first successful recovery.
    """

    def __init__(self, strategies: List[RecoveryStrategy]) -> None:
        self._strategies = strategies

    def can_recover(self, error: Exception, context) -> bool:
        return any(s.can_recover(error, context) for s in self._strategies)

    def recover(self, error: Exception, context, provider_context=None) -> None:
        for strategy in self._strategies:
            if strategy.can_recover(error, context):
                logger.debug(f"[CompositeRecovery] Applying {strategy.name}")
                strategy.recover(error, context, provider_context=provider_context)

    @classmethod
    def default(cls) -> "CompositeRecovery":
        """Create a composite with all standard recovery strategies."""
        return cls([
            BrowserRestartRecovery(),
            SessionRefreshRecovery(),
            CookieRefreshRecovery(),
            ProxyRotationRecovery(),
        ])
