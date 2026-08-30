"""
Unit Tests for ExecutionEngine (Task 024)
"""

import pytest
from providers.base.base_provider import BaseProvider
from providers.base.execution_engine import ExecutionEngine
from providers.base.models import ProviderMetadata, ExtractionStrategy
from providers.base.provider_context import ProviderContext
from providers.base.provider_state import ProviderState


class DummySuccessfulProvider(BaseProvider):
    def discover(self):
        return ["event_1", "event_2"]

    def fetch(self, discovery_items):
        return [{"id": item} for item in discovery_items]

    def parse(self, raw_data):
        return raw_data


class DummyFailingProvider(BaseProvider):
    def discover(self):
        raise RuntimeError("Simulated network crash during discovery")


def test_execution_engine_successful_run():
    meta = ProviderMetadata(name="test_bookmaker", code="test_bookmaker", enabled=True)
    ctx = ProviderContext(execution_id="exec_401", provider_name="test_bookmaker")
    provider = DummySuccessfulProvider(context=ctx, metadata=meta)

    engine = ExecutionEngine()
    result = engine.execute(provider)

    assert result.status == ProviderState.COMPLETED
    assert result.execution_id == "exec_401"
    assert len(result.discovered_objects) == 2
    assert len(result.parsed_objects) == 2
    assert result.quality_report is not None


def test_execution_engine_disabled_provider():
    meta = ProviderMetadata(name="disabled_bookmaker", code="disabled_bookmaker", enabled=False)
    ctx = ProviderContext(execution_id="exec_402", provider_name="disabled_bookmaker")
    provider = BaseProvider(context=ctx, metadata=meta)

    engine = ExecutionEngine()
    result = engine.execute(provider)

    assert result.status == ProviderState.CANCELLED or result.status == ProviderState.DISABLED
    assert result.execution_id == "exec_402"


def test_execution_engine_fault_isolation():
    meta = ProviderMetadata(name="failing_bookmaker", code="failing_bookmaker", enabled=True)
    ctx = ProviderContext(execution_id="exec_403", provider_name="failing_bookmaker")
    provider = DummyFailingProvider(context=ctx, metadata=meta)

    engine = ExecutionEngine()
    # Must NOT raise exception to caller; returns ProviderResult with FAILED status
    result = engine.execute(provider)

    assert result.status == ProviderState.FAILED
    assert len(result.errors) > 0
    assert "Simulated network crash" in result.errors[0]


@pytest.mark.asyncio
async def test_execution_engine_async_execute():
    meta = ProviderMetadata(name="test_bookmaker", code="test_bookmaker", enabled=True)
    ctx = ProviderContext(execution_id="exec_404", provider_name="test_bookmaker")
    provider = DummySuccessfulProvider(context=ctx, metadata=meta)

    engine = ExecutionEngine()
    result = await engine.execute_async(provider)

    assert result.status == ProviderState.COMPLETED
    assert len(result.parsed_objects) == 2
