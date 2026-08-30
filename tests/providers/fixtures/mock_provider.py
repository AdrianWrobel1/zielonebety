"""
Mock Provider Fixture

Configurable MockProvider implementing BaseProvider for unit, integration, and replay testing.
"""

from __future__ import annotations

from typing import List, Any, Dict, Optional, Callable
from providers.base.base_provider import BaseProvider
from providers.base.provider_context import ProviderContext
from providers.base.models import ProviderMetadata, ValidationReport
from providers.base.provider_registry import ProviderRegistry


class MockProvider(BaseProvider):
    """
    Configurable mock provider for testing.
    """

    def __init__(
        self,
        context: Optional[ProviderContext] = None,
        metadata: Optional[ProviderMetadata] = None,
        fail_at_stage: Optional[str] = None,
        fail_count: int = 0,
        exception_to_raise: Optional[Exception] = None,
    ) -> None:
        ctx = context or ProviderContext(provider_name="mock_provider")
        meta = metadata or ProviderMetadata(name="mock_provider", code="mock")
        super().__init__(context=ctx, metadata=meta)

        self.fail_at_stage = fail_at_stage
        self.fail_count = fail_count
        self.exception_to_raise = exception_to_raise or RuntimeError("Simulated provider failure")

        self.discover_data: List[Any] = [{"id": "ev1", "name": "Team A vs Team B"}]
        self.fetch_data: List[Any] = [{"id": "ev1", "raw": "data"}]
        self.parsed_data: List[Any] = [{"id": "ev1", "name": "Team A vs Team B", "markets": []}]

        self.calls: Dict[str, int] = {
            "initialize": 0,
            "discover": 0,
            "fetch": 0,
            "parse": 0,
            "validate": 0,
            "shutdown": 0,
        }

    def _check_failure(self, stage: str) -> None:
        self.calls[stage] += 1
        if self.fail_at_stage == stage and self.calls[stage] <= self.fail_count:
            raise self.exception_to_raise

    def initialize(self) -> None:
        super().initialize()
        self._check_failure("initialize")

    def discover(self) -> List[Any]:
        self._check_failure("discover")
        return self.discover_data

    def fetch(self, discovery_items: List[Any]) -> List[Any]:
        self._check_failure("fetch")
        return self.fetch_data

    def parse(self, raw_data: List[Any]) -> List[Any]:
        self._check_failure("parse")
        return self.parsed_data

    def validate(self, parsed_data: List[Any]) -> ValidationReport:
        self._check_failure("validate")
        return ValidationReport(is_valid=True, total_objects=len(parsed_data), valid_objects=len(parsed_data))

    def shutdown(self) -> None:
        self.calls["shutdown"] += 1
        super().shutdown()


# Register in registry if not present
try:
    if not ProviderRegistry.is_registered("mock_provider"):
        ProviderRegistry.register("mock_provider", MockProvider)
except Exception:
    pass
