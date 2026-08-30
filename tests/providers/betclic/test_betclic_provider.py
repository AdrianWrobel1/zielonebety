"""
Unit Tests for BetclicProvider (Task 026)
"""

import pytest
from providers.base.models import ExtractionStrategy
from providers.base.provider_factory import ProviderFactory
from providers.betclic.provider import BetclicProvider


def test_betclic_provider_factory_instantiation():
    provider = ProviderFactory.create_provider("betclic")
    assert isinstance(provider, BetclicProvider)
    assert provider.metadata.name == "betclic"
    assert provider.metadata.code in ("btcl", "betc")
    assert provider.scraping_strategy == ExtractionStrategy.NETWORK_RESPONSE


def test_betclic_provider_config():
    provider = BetclicProvider()
    assert "betclic.pl" in provider.betclic_config.base_url
    assert provider.betclic_config.football_sport_id == 1
