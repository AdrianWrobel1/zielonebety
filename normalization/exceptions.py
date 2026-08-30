"""
Normalization Engine Exceptions
"""

from core.exceptions import BaseApplicationError


class NormalizationError(BaseApplicationError):
    """Base exception for normalization failures."""
    pass


class MarketMappingError(NormalizationError):
    """Raised when provider market mapping fails."""
    pass


class SelectionMappingError(NormalizationError):
    """Raised when provider selection mapping fails."""
    pass
