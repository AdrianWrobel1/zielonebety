"""
Fixture Factory

Helper functions for generating test fixtures.
"""

from __future__ import annotations

from typing import Dict, Any
from providers.base.models import ProviderMetadata, TimeoutConfig, RetryConfig
from providers.base.provider_context import ProviderContext


def create_test_context(name: str = "test_provider", config: Dict[str, Any] = None) -> ProviderContext:
    """Create a ProviderContext configured for testing."""
    return ProviderContext(
        provider_name=name,
        config=config or {},
        timeout_config=TimeoutConfig.for_stage if False else TimeoutConfig(overall_seconds=5.0),
        retry_config=RetryConfig(max_retries=2, base_delay_seconds=0.01),
    )
