"""
Unit Tests for ProviderRegistry & ProviderFactory (Task 025)
"""

import pytest
from providers.base.base_provider import BaseProvider
from providers.base.exceptions import ProviderDuplicateError, ProviderNotFoundError, ProviderRegistrationError
from providers.base.provider_factory import ProviderFactory
from providers.base.provider_registry import ProviderRegistry, register_provider


@pytest.fixture(autouse=True)
def clean_registry():
    saved = dict(ProviderRegistry._registry)
    yield
    ProviderRegistry._registry = saved



class MockBookmaker(BaseProvider):
    pass


def test_provider_registry_register_and_get():
    ProviderRegistry.register("mock_bookmaker", MockBookmaker)
    assert ProviderRegistry.is_registered("mock_bookmaker") is True
    assert ProviderRegistry.get("mock_bookmaker") == MockBookmaker
    assert "mock_bookmaker" in ProviderRegistry.list_providers()



def test_provider_registry_decorator():
    @register_provider("dec_bookmaker")
    class DecoratedBookmaker(BaseProvider):
        pass

    assert ProviderRegistry.is_registered("dec_bookmaker") is True
    assert ProviderRegistry.get("dec_bookmaker") == DecoratedBookmaker


def test_provider_registry_duplicate():
    ProviderRegistry.register("dupe", MockBookmaker)
    with pytest.raises(ProviderDuplicateError, match="already registered"):
        ProviderRegistry.register("dupe", MockBookmaker)


def test_provider_registry_not_found():
    with pytest.raises(ProviderNotFoundError, match="is not registered"):
        ProviderRegistry.get("unknown_provider")


def test_provider_factory_create():
    ProviderRegistry.register("factory_bookmaker", MockBookmaker)
    instance = ProviderFactory.create_provider("factory_bookmaker", config={"rate": 10}, enabled=True)

    assert isinstance(instance, MockBookmaker)
    assert instance.metadata.name == "factory_bookmaker"
    assert instance.context.config == {"rate": 10}
