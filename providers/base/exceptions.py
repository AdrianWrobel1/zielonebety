"""
Provider Framework Exceptions

Complete exception hierarchy for the scraping framework.
Every exception type is specific to one failure domain, enabling precise catch blocks
and the ErrorClassifier to route failures correctly.
"""

from core.exceptions import BaseApplicationError


# ─────────────────────────────────────────────────────────────────────────────
# Base Provider Error
# ─────────────────────────────────────────────────────────────────────────────

class ProviderError(BaseApplicationError):
    """Base exception for all provider-domain failures."""
    pass


# ─────────────────────────────────────────────────────────────────────────────
# Non-Retryable Marker
# ─────────────────────────────────────────────────────────────────────────────

class NonRetryableError(ProviderError):
    """
    Marker base class for errors that must NEVER be retried.

    Subclass this for configuration errors, programming errors, authentication
    failures, and any condition where retrying would be pointless or harmful.
    """
    pass


# ─────────────────────────────────────────────────────────────────────────────
# Lifecycle Errors
# ─────────────────────────────────────────────────────────────────────────────

class ProviderConfigurationError(NonRetryableError):
    """Raised when provider configuration is missing, malformed, or invalid."""
    pass


class ProviderInitializationError(ProviderError):
    """Raised when provider initialization fails (may be transient)."""
    pass


class ProviderStateError(NonRetryableError):
    """Raised when an illegal provider state transition is attempted."""
    pass


class ProviderRegistrationError(NonRetryableError):
    """Raised when provider registration fails."""
    pass


class ProviderNotFoundError(ProviderRegistrationError):
    """Raised when a requested provider is not found in the registry."""
    pass


class ProviderDuplicateError(ProviderRegistrationError):
    """Raised when attempting to register a duplicate provider name."""
    pass


class ProviderDisabledError(NonRetryableError):
    """Raised when attempting to execute a disabled provider."""
    pass


# ─────────────────────────────────────────────────────────────────────────────
# Scraping Errors
# ─────────────────────────────────────────────────────────────────────────────

class ScrapingError(ProviderError):
    """Base class for all scraping-layer failures."""
    pass


class SessionError(ScrapingError):
    """Raised when the HTTP session cannot be created or refreshed."""
    pass


class AuthenticationError(NonRetryableError):
    """Raised when authentication fails (credentials invalid, not expired)."""
    pass


class RateLimitError(ScrapingError):
    """Raised when the provider's rate limit is exceeded."""
    pass


class ProxyError(ScrapingError):
    """Raised when proxy connectivity or rotation fails."""
    pass


# ─────────────────────────────────────────────────────────────────────────────
# Browser Errors
# ─────────────────────────────────────────────────────────────────────────────

class BrowserError(ScrapingError):
    """Base class for browser-automation failures."""
    pass


class BrowserLaunchError(BrowserError):
    """Raised when the browser process cannot be launched."""
    pass


class BrowserCrashError(BrowserError):
    """Raised when the browser process crashes during execution."""
    pass


class BrowserPoolExhaustedError(BrowserError):
    """Raised when no browser instances are available in the pool."""
    pass


class PageError(BrowserError):
    """Raised when a browser page encounters an error."""
    pass


class PageCrashError(PageError):
    """Raised when a browser page crashes."""
    pass


class NavigationError(BrowserError):
    """Raised when browser navigation to a URL fails."""
    pass


class NavigationTimeoutError(NavigationError):
    """Raised when page navigation exceeds the configured timeout."""
    pass


class ResourceInterceptionError(BrowserError):
    """Raised when resource interception setup fails."""
    pass


# ─────────────────────────────────────────────────────────────────────────────
# Extraction Errors
# ─────────────────────────────────────────────────────────────────────────────

class ExtractionError(ProviderError):
    """Base class for data extraction failures."""
    pass


class NetworkExtractionError(ExtractionError):
    """Raised when network response extraction produces no usable result."""
    pass


class JsonExtractionError(ExtractionError):
    """Raised when embedded JSON extraction fails."""
    pass


class DomExtractionError(ExtractionError):
    """Raised when DOM/CSS selector extraction fails."""
    pass


class SelectorChangedError(DomExtractionError):
    """Raised when a previously working CSS selector no longer matches."""
    pass


class EmptyResponseError(ExtractionError):
    """Raised when the provider response is empty or contains no usable data."""
    pass


class PartialExtractionError(ExtractionError):
    """Raised when extraction succeeded but produced incomplete data."""
    pass


class AllStrategiesFailedError(ExtractionError):
    """Raised when all configured extraction strategies produce no result."""
    pass


# ─────────────────────────────────────────────────────────────────────────────
# Discovery / Fetch / Parse / Validate Errors
# ─────────────────────────────────────────────────────────────────────────────

class DiscoveryError(ProviderError):
    """Raised when the discovery stage fails."""
    pass


class FetchError(ProviderError):
    """Raised when the fetch stage fails to obtain raw data."""
    pass


class ParseError(NonRetryableError):
    """Raised when the parse stage cannot interpret the raw response."""
    pass


class ProviderValidationError(NonRetryableError):
    """Raised when parsed objects fail provider-level validation."""
    pass


# ─────────────────────────────────────────────────────────────────────────────
# Retry & Recovery Errors
# ─────────────────────────────────────────────────────────────────────────────

class RetryExhaustedError(ProviderError):
    """Raised when all configured retry attempts have been consumed."""

    def __init__(self, message: str, attempt_count: int, last_error: Exception, details: dict = None):
        super().__init__(message, details)
        self.attempt_count = attempt_count
        self.last_error = last_error


class RecoveryFailedError(ProviderError):
    """Raised when a recovery strategy cannot recover from an error."""
    pass


class ProviderTimeoutError(ProviderError):
    """Raised when a provider stage exceeds its configured timeout."""

    def __init__(self, message: str, stage: str, timeout_seconds: float, details: dict = None):
        super().__init__(message, details)
        self.stage = stage
        self.timeout_seconds = timeout_seconds


class CircuitBreakerOpenError(ProviderError):
    """Raised when an operation is rejected because the provider circuit breaker is open."""
    pass


class ExecutionCancelledError(ProviderError):
    """Raised when a provider execution is cancelled externally."""
    pass


# ─────────────────────────────────────────────────────────────────────────────
# Observability & Recording Errors
# ─────────────────────────────────────────────────────────────────────────────

class SnapshotError(ProviderError):
    """Raised when saving or loading a HTML/network snapshot fails."""
    pass


class ReplayNotFoundError(ProviderError):
    """Raised when an requested endpoint is missing from offline replay manifest."""
    pass


class ScreenshotError(ProviderError):
    """Raised when capturing a screenshot fails."""
    pass


class DiagnosticsError(ProviderError):
    """Raised when the diagnostics system itself encounters an error."""
    pass
