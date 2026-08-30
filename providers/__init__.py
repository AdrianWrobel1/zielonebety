"""
Providers Package
"""

from providers.betclic.provider import BetclicProvider
from providers.superbet.provider import SuperbetProvider
from providers.odds_api.provider import OddsApiProvider
from providers.statshub.provider import StatsHubProvider

__all__ = ["BetclicProvider", "SuperbetProvider", "OddsApiProvider", "StatsHubProvider"]
