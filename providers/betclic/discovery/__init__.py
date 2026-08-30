"""
Betclic Discovery Submodule Package
"""

from providers.betclic.discovery.discovery import BetclicDiscovery
from providers.betclic.discovery.acquisition import BetclicDiscoveryAcquisition
from providers.betclic.discovery.discovery_parser import BetclicDiscoveryParser

__all__ = [
    "BetclicDiscovery",
    "BetclicDiscoveryAcquisition",
    "BetclicDiscoveryParser",
]
