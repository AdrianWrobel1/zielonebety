"""
Unit Tests for BaseProvider (Task 023)
"""

import pytest
from providers.base.base_provider import BaseProvider
from providers.base.models import ProviderMetadata, ExtractionStrategy
from providers.base.provider_context import ProviderContext
from providers.base.provider_state import ProviderState


class SampleProvider(BaseProvider):
    def discover(self):
        return ["event1", "event2"]


def test_base_provider_initialization():
    meta = ProviderMetadata(name="test_bookmaker", code="test_bookmaker", enabled=True, scraping_strategy=ExtractionStrategy.NETWORK_RESPONSE)
    ctx = ProviderContext(execution_id="exec_301", provider_name="test_bookmaker")

    provider = SampleProvider(context=ctx, metadata=meta)
    assert provider.state == ProviderState.UNINITIALIZED

    provider.initialize()
    assert provider.state == ProviderState.READY
    assert provider.scraping_strategy == ExtractionStrategy.NETWORK_RESPONSE


def test_base_provider_lifecycle_hooks():
    meta = ProviderMetadata(name="test_bookmaker", code="test_bookmaker", enabled=True)
    ctx = ProviderContext(execution_id="exec_302", provider_name="test_bookmaker")
    provider = SampleProvider(context=ctx, metadata=meta)

    discovered = provider.discover()
    assert len(discovered) == 2

    fetched = provider.fetch(discovered)
    assert fetched == ["event1", "event2"]

    parsed = provider.parse(fetched)
    assert parsed == ["event1", "event2"]

    val_report = provider.validate(parsed)
    assert val_report.is_valid is True
    assert val_report.valid_objects == 2


def test_base_provider_disabled():
    meta = ProviderMetadata(name="disabled_bookmaker", code="disabled_bookmaker", enabled=False)
    ctx = ProviderContext(execution_id="exec_303", provider_name="disabled_bookmaker")
    provider = BaseProvider(context=ctx, metadata=meta)

    provider.initialize()
    assert provider.state == ProviderState.CANCELLED


def test_base_provider_shutdown():
    meta = ProviderMetadata(name="test_bookmaker", code="test_bookmaker", enabled=True)
    ctx = ProviderContext(execution_id="exec_304", provider_name="test_bookmaker")
    provider = BaseProvider(context=ctx, metadata=meta)

    provider.initialize()
    provider.shutdown()
    assert provider.health() == ProviderState.READY

