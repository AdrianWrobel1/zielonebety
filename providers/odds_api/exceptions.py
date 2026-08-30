"""
Odds API.io Exceptions
"""

from providers.base.exceptions import ProviderError, NonRetryableError


class OddsApiError(ProviderError):
    """Base exception for Odds API.io provider errors."""
    pass


class OddsApiDiscoveryError(OddsApiError):
    """Raised during event discovery failures."""
    pass


class OddsApiFetchError(OddsApiError):
    """Raised during odds fetch failures."""
    pass


class OddsApiParsingError(OddsApiError):
    """Raised during payload parsing failures."""
    pass


class OddsApiRateLimitError(OddsApiError):
    """Raised when rate limit or quota is exceeded."""
    pass


class OddsApiQuotaExceededError(NonRetryableError):
    """HTTP 429 Too Many Requests — non-retryable for current quota window.

    Inherits from NonRetryableError so the ErrorClassifier immediately
    stops retry attempts (no retry storm on quota exhaustion).
    """
    pass


class OddsApiAccessDeniedError(NonRetryableError):
    """HTTP 403 Forbidden — non-retryable.

    Inherits from NonRetryableError so the ErrorClassifier immediately
    stops retry attempts (no retry storm on access denial).
    """
    pass


class OddsApiQuotaBudgetExhaustedError(NonRetryableError):
    """Internal safety budget exhausted — request blocked before sending.

    Raised when the internal hourly quota budget is exhausted,
    preventing the request from being sent at all.
    """
    pass
