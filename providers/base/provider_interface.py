"""
Mandatory Public Provider Interface Contract
"""

from abc import ABC, abstractmethod
from typing import List, Any
from providers.base.provider_result import ProviderResult
from providers.base.provider_state import ProviderState
from providers.base.models import ValidationReport, ProviderMetadata


class ProviderInterface(ABC):
    """Mandatory public interface that every provider MUST implement."""

    @abstractmethod
    def initialize(self) -> None:
        """Initialize provider resources (HTTP session, configuration)."""
        pass

    @abstractmethod
    def shutdown(self) -> None:
        """Release all allocated resources."""
        pass

    @abstractmethod
    def health(self) -> ProviderState:
        """Return current provider health state."""
        pass

    @abstractmethod
    def discover(self) -> List[Any]:
        """Discover available competitions and events. Does not download market data."""
        pass

    @abstractmethod
    def fetch(self, discovery_items: List[Any]) -> List[Any]:
        """Download raw provider data for discovered resources."""
        pass

    @abstractmethod
    def parse(self, raw_data: List[Any]) -> List[Any]:
        """Transform raw responses into provider domain models."""
        pass

    @abstractmethod
    def validate(self, parsed_data: List[Any]) -> ValidationReport:
        """Validate parsed provider domain models."""
        pass

    @abstractmethod
    def run(self) -> ProviderResult:
        """Execute the complete deterministic provider lifecycle pipeline."""
        pass
