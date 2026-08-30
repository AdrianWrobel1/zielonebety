"""
Superbet Custom Exceptions Taxonomy
"""

from providers.base.exceptions import ProviderError


class SuperbetError(ProviderError):

    """Base exception for all Superbet provider operations."""
    pass


class SuperbetDiscoveryError(SuperbetError):
    """Raised when Superbet event/competition discovery fails."""
    pass


class SuperbetFetchError(SuperbetError):
    """Raised when Superbet event payload fetching fails."""
    pass


class SuperbetParsingError(SuperbetError):
    """Raised when Superbet raw JSON parsing fails."""
    pass


class SuperbetValidationError(SuperbetError):
    """Raised when Superbet domain model validation fails."""
    pass
