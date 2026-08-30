"""
Provider Factory for Dependency Injection & Instantiation
"""

from typing import Dict, Any, Optional
from providers.base.base_provider import BaseProvider
from providers.base.provider_registry import ProviderRegistry
from providers.base.provider_context import ProviderContext
from providers.base.models import ProviderMetadata
from providers.base.exceptions import ProviderRegistrationError


class ProviderFactory:
    """Factory owning provider construction and dependency injection."""

    @classmethod
    def create_provider(
        cls,
        name: str,
        config: Optional[Dict[str, Any]] = None,
        enabled: bool = True
    ) -> BaseProvider:
        """Instantiate a provider registered in ProviderRegistry."""
        provider_cls = ProviderRegistry.get(name)
        clean_name = name.strip().lower()
        provider_config = config or {}

        context = ProviderContext(
            provider_name=clean_name,
            config=provider_config,
        )
        metadata = ProviderMetadata(
            name=clean_name,
            code=clean_name[:4].lower(),
            enabled=enabled,
        )

        return provider_cls(context=context, metadata=metadata)
