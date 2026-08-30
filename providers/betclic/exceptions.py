"""
Betclic Provider Specific Exceptions
"""

from providers.base.exceptions import ProviderError, NonRetryableError


class BetclicError(ProviderError):
    """Base exception for Betclic provider errors."""
    pass


class BetclicConfigurationError(BetclicError):
    """Raised when Betclic configuration is invalid."""
    pass


class BetclicDiscoveryError(BetclicError):
    """Raised when Betclic discovery fails."""
    pass


class BetclicAccessDeniedError(BetclicDiscoveryError, NonRetryableError):
    """Raised when access is denied by WAF / CloudFront (HTTP 403) with no retry."""
    pass


class BetclicFetchError(BetclicError):
    """Raised when Betclic HTTP/browser fetch fails."""
    pass


class BetclicParsingError(BetclicError):
    """Raised when Betclic payload parsing fails."""
    pass


class BetclicValidationError(BetclicError):
    """Raised when Betclic validation fails."""
    pass

