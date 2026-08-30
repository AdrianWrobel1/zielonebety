"""
Unit Tests for ErrorClassifier (Task 014)
"""

import pytest
from providers.base.models import RetryableClassification
from providers.base.exceptions import (
    RateLimitError,
    FetchError,
    AuthenticationError,
    ParseError,
    ProviderConfigurationError,
    ExecutionCancelledError,
    RetryExhaustedError,
)
from providers.base.recovery.error_classifier import ErrorClassifier


def test_error_classifier_retryable_exceptions():
    classifier = ErrorClassifier()

    assert classifier.is_retryable(RateLimitError("Rate limit 429")) is True
    assert classifier.is_retryable(FetchError("Server error 500")) is True
    assert classifier.is_retryable(TimeoutError("Connection timeout")) is True


def test_error_classifier_non_retryable_exceptions():
    classifier = ErrorClassifier()

    assert classifier.is_retryable(AuthenticationError("Invalid credentials")) is False
    assert classifier.classify(ParseError("Schema missing")).classification == RetryableClassification.NON_RETRYABLE
    assert classifier.classify(ProviderConfigurationError("Config missing")).classification == RetryableClassification.NON_RETRYABLE


def test_error_classifier_fatal_exceptions():
    classifier = ErrorClassifier()

    res_cancelled = classifier.classify(ExecutionCancelledError("User cancelled"))
    assert res_cancelled.classification == RetryableClassification.FATAL

    res_exhausted = classifier.classify(RetryExhaustedError("Retries spent", 3, Exception()))
    assert res_exhausted.classification == RetryableClassification.FATAL


def test_error_classifier_playwright_string_matching():
    classifier = ErrorClassifier()

    err_closed = RuntimeError("Target page, context or browser has been closed")
    assert classifier.is_retryable(err_closed) is True

    err_reset = RuntimeError("net::ERR_CONNECTION_RESET at https://example.com")
    assert classifier.is_retryable(err_reset) is True


def test_error_classifier_custom_rule():
    classifier = ErrorClassifier()

    class CustomBookmakerError(Exception):
        pass

    classifier.register_rule(
        exc_type=CustomBookmakerError,
        classification=RetryableClassification.NON_RETRYABLE,
        reason="Custom bookmaker error is permanent"
    )

    res = classifier.classify(CustomBookmakerError("Custom error"))
    assert res.classification == RetryableClassification.NON_RETRYABLE
    assert res.reason == "Custom bookmaker error is permanent"
