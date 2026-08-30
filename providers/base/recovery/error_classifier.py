"""
Error Classifier

Classifies exceptions into Retryable / Non-Retryable / Fatal categories.

The RetryEngine uses this to decide whether to retry an operation.
Providers can register custom classification rules to handle provider-specific
error patterns without modifying the framework.

Classification priority:
  1. Custom rules registered by providers (checked first)
  2. Built-in exception type rules
  3. Default: RETRYABLE (conservative — retry unless we know it's permanent)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum, auto
from typing import Callable, Dict, List, Optional, Type

from providers.base.models import RetryableClassification

logger = logging.getLogger("framework.error_classifier")


# ─────────────────────────────────────────────────────────────────────────────
# Classification Result
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ErrorClassification:
    """Result of classifying a single exception."""
    classification: RetryableClassification
    reason: str
    error_type: str

    @property
    def is_retryable(self) -> bool:
        return self.classification == RetryableClassification.RETRYABLE

    @property
    def is_non_retryable(self) -> bool:
        return self.classification == RetryableClassification.NON_RETRYABLE

    @property
    def is_fatal(self) -> bool:
        return self.classification == RetryableClassification.FATAL

    def __repr__(self) -> str:
        return f"ErrorClassification({self.classification.value}, {self.error_type}, reason={self.reason!r})"


# ─────────────────────────────────────────────────────────────────────────────
# Classifier
# ─────────────────────────────────────────────────────────────────────────────

class ErrorClassifier:
    """
    Classifies exceptions into retryability categories.

    Providers register custom rules via register_rule().
    The framework populates built-in rules in __init__.
    """

    def __init__(self) -> None:
        # List of (exception_type_or_predicate, classification, reason)
        self._rules: List[tuple] = []
        self._populate_builtin_rules()

    # ──────────────────────────────────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────────────────────────────────

    def classify(self, error: Exception) -> ErrorClassification:
        """
        Classify an exception.  Custom rules are checked before built-in rules.
        Returns RETRYABLE by default if no rule matches.
        """
        error_type = type(error).__name__

        for exc_type, classification, reason, predicate in self._rules:
            # Check predicate-based rules first
            if predicate is not None:
                try:
                    if predicate(error):
                        logger.debug(f"ErrorClassifier: {error_type} → {classification.value} via predicate")
                        return ErrorClassification(classification, reason, error_type)
                except Exception:
                    pass  # Predicate itself errored; skip
                continue

            # Check type-based rules
            if exc_type is not None and isinstance(error, exc_type):
                logger.debug(f"ErrorClassifier: {error_type} → {classification.value} via type match")
                return ErrorClassification(classification, reason, error_type)

        # Default: retryable (conservative)
        logger.debug(f"ErrorClassifier: {error_type} → RETRYABLE (default)")
        return ErrorClassification(
            RetryableClassification.RETRYABLE,
            "No matching rule; defaulting to retryable",
            error_type,
        )

    def register_rule(
        self,
        exc_type: Optional[Type[Exception]] = None,
        classification: RetryableClassification = RetryableClassification.RETRYABLE,
        reason: str = "",
        predicate: Optional[Callable[[Exception], bool]] = None,
    ) -> None:
        """
        Register a custom classification rule.

        Rules are checked in registration order before built-in rules.
        Use exc_type for simple type-based matching.
        Use predicate for complex conditions (e.g., check error message).
        """
        if exc_type is None and predicate is None:
            raise ValueError("Either exc_type or predicate must be provided")
        # Prepend so custom rules run before built-in rules
        self._rules.insert(0, (exc_type, classification, reason, predicate))

    def is_retryable(self, error: Exception) -> bool:
        """
        Convenience predicate returning True if an exception is classified as RETRYABLE.
        """
        return self.classify(error).is_retryable

    # ──────────────────────────────────────────────────────────────────────────
    # Built-in Rules
    # ──────────────────────────────────────────────────────────────────────────

    def _populate_builtin_rules(self) -> None:
        """Populate classification rules from built-in exception types and message patterns."""
        # Message-based string patterns checked first
        def _is_playwright_browser_closed(e: Exception) -> bool:
            msg = str(e).lower()
            return "browser has been closed" in msg or "target page, context or browser has been closed" in msg

        def _is_transient_network_msg(e: Exception) -> bool:
            msg = str(e).lower()
            transient_signals = [
                "err_connection_reset",
                "err_connection_refused",
                "err_name_not_resolved",
                "err_timed_out",
                "socket hang up",
                "connection reset by peer",
            ]
            return any(s in msg for s in transient_signals)

        self._rules.append((None, RetryableClassification.RETRYABLE, "Playwright browser/context closed", _is_playwright_browser_closed))
        self._rules.append((None, RetryableClassification.RETRYABLE, "Transient network error message", _is_transient_network_msg))

        # Import here to avoid circular imports
        from providers.base.exceptions import (
            NonRetryableError,
            ProviderConfigurationError,
            ProviderStateError,
            ProviderRegistrationError,
            AuthenticationError,
            ParseError,
            ProviderValidationError,
            ExecutionCancelledError,
            ProviderDisabledError,
            # Retryable
            RateLimitError,
            NavigationTimeoutError,
            NavigationError,
            BrowserCrashError,
            PageCrashError,
            SessionError,
            FetchError,
            DiscoveryError,
            ProxyError,
            RetryExhaustedError,
            ProviderTimeoutError,
        )

        # Non-retryable (programming errors, permanent failures)
        non_retryable = [
            (NonRetryableError, "Marked non-retryable"),
            (ProviderConfigurationError, "Configuration is permanent"),
            (AuthenticationError, "Credentials are invalid"),
            (ParseError, "Parser logic must be fixed"),
            (ProviderValidationError, "Validation logic issue"),
            (ProviderDisabledError, "Provider is disabled"),
        ]
        for exc_type, reason in non_retryable:
            self._rules.append(
                (exc_type, RetryableClassification.NON_RETRYABLE, reason, None)
            )

        # Fatal (state machine corruption, cancellation)
        fatal = [
            (ExecutionCancelledError, "Execution was cancelled"),
            (RetryExhaustedError, "All retries consumed"),
            (ProviderStateError, "Illegal state transition"),
        ]
        for exc_type, reason in fatal:
            self._rules.append(
                (exc_type, RetryableClassification.FATAL, reason, None)
            )

        # Retryable (transient failures)
        retryable = [
            (RateLimitError, "Rate limit — wait and retry"),
            (NavigationTimeoutError, "Navigation timeout — transient"),
            (NavigationError, "Navigation failure — transient"),
            (BrowserCrashError, "Browser crash — restart and retry"),
            (PageCrashError, "Page crash — reload and retry"),
            (SessionError, "Session error — refresh and retry"),
            (FetchError, "Fetch failure — transient"),
            (DiscoveryError, "Discovery failure — transient"),
            (ProxyError, "Proxy failure — rotate and retry"),
            (ProviderTimeoutError, "Timeout — transient"),
        ]
        for exc_type, reason in retryable:
            self._rules.append(
                (exc_type, RetryableClassification.RETRYABLE, reason, None)
            )

        # Network exceptions from standard library
        import socket
        import urllib.error
        stdlib_retryable = [
            (TimeoutError, "Socket timeout"),
            (ConnectionError, "Connection error"),
            (socket.timeout, "Socket timeout"),
            (urllib.error.URLError, "URL error"),
        ]
        for exc_type, reason in stdlib_retryable:
            self._rules.append(
                (exc_type, RetryableClassification.RETRYABLE, reason, None)
            )

        # requests library exceptions (imported lazily)
        try:
            import requests.exceptions as req_exc
            req_retryable = [
                (req_exc.Timeout, "HTTP timeout"),
                (req_exc.ConnectionError, "HTTP connection error"),
                (req_exc.ChunkedEncodingError, "Chunked encoding error"),
                (req_exc.ContentDecodingError, "Content decoding error"),
            ]
            req_non_retryable = [
                (req_exc.InvalidURL, "Invalid URL"),
                (req_exc.MissingSchema, "Missing URL schema"),
            ]
            for exc_type, reason in req_retryable:
                self._rules.append(
                    (exc_type, RetryableClassification.RETRYABLE, reason, None)
                )
            for exc_type, reason in req_non_retryable:
                self._rules.append(
                    (exc_type, RetryableClassification.NON_RETRYABLE, reason, None)
                )
        except ImportError:
            pass  # requests not installed — skip


# Global shared classifier instance
_global_classifier: Optional[ErrorClassifier] = None


def get_global_classifier() -> ErrorClassifier:
    global _global_classifier
    if _global_classifier is None:
        _global_classifier = ErrorClassifier()
    return _global_classifier

