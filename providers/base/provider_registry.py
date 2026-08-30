"""
Provider Registry for Explicit Provider Discovery and Management
"""

from typing import Dict, Type, List
from providers.base.base_provider import BaseProvider
from providers.base.exceptions import ProviderNotFoundError, ProviderDuplicateError, ProviderRegistrationError


class ProviderRegistry:
    """Registry owning provider registration and resolution."""

    _registry: Dict[str, Type[BaseProvider]] = {}

    @classmethod
    def register(cls, name: str, provider_cls: Type[BaseProvider]) -> None:
        """Register a provider class under a unique identifier."""
        if not issubclass(provider_cls, BaseProvider):
            raise ProviderRegistrationError(
                f"Provider class '{provider_cls.__name__}' must inherit from BaseProvider"
            )
        clean_name = name.strip().lower()
        if not clean_name:
            raise ProviderRegistrationError("Provider name cannot be empty")

        if clean_name in cls._registry:
            raise ProviderDuplicateError(f"Provider '{clean_name}' is already registered")

        cls._registry[clean_name] = provider_cls

    @classmethod
    def unregister(cls, name: str) -> None:
        """Unregister a provider by name."""
        clean_name = name.strip().lower()
        if clean_name not in cls._registry:
            raise ProviderNotFoundError(f"Provider '{clean_name}' is not registered")
        del cls._registry[clean_name]

    @classmethod
    def get(cls, name: str) -> Type[BaseProvider]:
        """Resolve a registered provider class."""
        clean_name = name.strip().lower()
        if clean_name not in cls._registry:
            if clean_name == "betclic":
                from providers.betclic.provider import BetclicProvider
                cls.register("betclic", BetclicProvider)
            elif clean_name == "superbet":
                from providers.superbet.provider import SuperbetProvider
                cls.register("superbet", SuperbetProvider)
            elif clean_name in ("odds_api", "bet365", "unibet"):
                from providers.odds_api.provider import OddsApiProvider
                cls.register("odds_api", OddsApiProvider)
            elif clean_name == "statshub":
                from providers.statshub.provider import StatsHubProvider
                cls.register("statshub", StatsHubProvider)

        if clean_name not in cls._registry:
            raise ProviderNotFoundError(f"Provider '{clean_name}' is not registered")
        return cls._registry[clean_name]



    @classmethod
    def _ensure_defaults(cls) -> None:
        """Ensure standard built-in providers are loaded if registry is empty."""
        if not cls._registry:
            for name in ("superbet", "betclic", "odds_api", "statshub"):
                if name not in cls._registry:
                    try:
                        cls.get(name)
                    except Exception:
                        pass

    @classmethod
    def list_providers(cls) -> List[str]:
        """List all registered provider names."""
        return sorted(list(cls._registry.keys()))

    @classmethod
    def is_registered(cls, name: str) -> bool:
        """Check if a provider name is registered."""
        clean_name = name.strip().lower()
        if clean_name not in cls._registry:
            try:
                cls.get(clean_name)
            except Exception:
                pass
        return clean_name in cls._registry

    @classmethod
    def clear(cls) -> None:
        """Clear all registered providers (useful for testing)."""
        cls._registry.clear()


def register_provider(name: str):
    """Decorator to register a provider class with ProviderRegistry."""
    def decorator(cls: Type[BaseProvider]):
        ProviderRegistry.register(name, cls)
        return cls
    return decorator

