"""
Betclic Provider Package
"""

from providers.betclic.provider import BetclicProvider
from providers.betclic.config import BetclicConfig
from providers.betclic.exceptions import (
    BetclicError,
    BetclicConfigurationError,
    BetclicDiscoveryError,
    BetclicFetchError,
    BetclicParsingError,
    BetclicValidationError,
)

__all__ = [
    "BetclicProvider",
    "BetclicConfig",
    "BetclicError",
    "BetclicConfigurationError",
    "BetclicDiscoveryError",
    "BetclicFetchError",
    "BetclicParsingError",
    "BetclicValidationError",
]
