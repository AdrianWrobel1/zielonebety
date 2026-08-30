"""
Unit Tests for Superbet Provider Assembly & Factory Creation (Task 040 / Stage 2.1)
"""

from providers.base.provider_factory import ProviderFactory
from providers.base.provider_registry import ProviderRegistry
from providers.superbet.provider import SuperbetProvider


def test_superbet_provider_factory_instantiation():
    provider = ProviderFactory.create_provider("superbet")
    assert isinstance(provider, SuperbetProvider)
    assert provider.metadata.name == "superbet"
    assert provider.metadata.code == "supr"


def test_superbet_provider_config():
    provider = SuperbetProvider()
    assert provider.superbet_config.base_url == "https://production-superbet-offer-pl.freetls.fastly.net/v3/pl-PL"
    assert provider.superbet_config.sport_id == 5
    assert ProviderRegistry.is_registered("superbet") is True


def test_superbet_provider_mock_setters():
    provider = SuperbetProvider()
    assert provider._raw_discovery_payload is None
    assert provider._mock_fetch_provider is None

    test_payload = [{"test": 123}]
    provider.set_mock_discovery_payload(test_payload)
    assert provider._raw_discovery_payload == test_payload

    dummy_fn = lambda x: {}
    provider.set_mock_fetch_provider(dummy_fn)
    assert provider._mock_fetch_provider == dummy_fn
