"""
Recovery Layer — Package Init
"""

from providers.base.recovery.error_classifier import ErrorClassifier, ErrorClassification
from providers.base.recovery.retry_engine import RetryEngine, RetryContext
from providers.base.recovery.recovery_strategy import (
    RecoveryStrategy,
    BrowserRestartRecovery,
    SessionRefreshRecovery,
    CookieRefreshRecovery,
    ProxyRotationRecovery,
    NoOpRecovery,
    CompositeRecovery,
)

__all__ = [
    "ErrorClassifier",
    "ErrorClassification",
    "RetryEngine",
    "RetryContext",
    "RecoveryStrategy",
    "BrowserRestartRecovery",
    "SessionRefreshRecovery",
    "CookieRefreshRecovery",
    "ProxyRotationRecovery",
    "NoOpRecovery",
    "CompositeRecovery",
]
