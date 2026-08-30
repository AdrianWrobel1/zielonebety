"""
Abstract Base Provider Implementation

Base class for every bookmaker provider implementation.
Owns provider metadata, context access, state machine transitions, and lifecycle hooks.
Delegates execution orchestration to ExecutionEngine.
"""

from __future__ import annotations

import logging
from typing import Any, List, Optional

from providers.base.provider_interface import ProviderInterface
from providers.base.provider_context import ProviderContext
from providers.base.provider_result import ProviderResult
from providers.base.provider_state import ProviderState, ProviderStateMachine
from providers.base.models import ProviderMetadata, ValidationReport, ProviderMetrics, ExtractionStrategy
from providers.base.exceptions import ProviderStateError, ProviderInitializationError

logger = logging.getLogger("framework.base_provider")


class BaseProvider(ProviderInterface):
    """
    Abstract base provider class.
    All bookmaker implementations MUST inherit from this class.
    """

    def __init__(self, context: ProviderContext, metadata: ProviderMetadata) -> None:
        self.context = context
        self.metadata = metadata
        self._state_machine = ProviderStateMachine(initial_state=ProviderState.UNINITIALIZED)
        self.warnings: List[str] = []
        self.errors: List[str] = []

    @property
    def state_machine(self) -> ProviderStateMachine:
        return self._state_machine

    @property
    def state(self) -> ProviderState:
        return self._state_machine.current_state

    @property
    def scraping_strategy(self) -> ExtractionStrategy:
        """Declared preferred scraping strategy for this provider."""
        return self.metadata.scraping_strategy

    def initialize(self) -> None:
        """Standard provider initialization hook."""
        try:
            self._state_machine.transition_to(ProviderState.INITIALIZING)
            if not self.metadata.enabled:
                self._state_machine.transition_to(ProviderState.CANCELLED)
                return
            self._state_machine.transition_to(ProviderState.READY)
        except Exception as e:
            if self._state_machine.current_state.can_transition_to(ProviderState.FAILED):
                self._state_machine.transition_to(ProviderState.FAILED)
            raise ProviderInitializationError(f"Failed to initialize provider '{self.metadata.name}': {e}") from e

    def discover(self) -> List[Any]:
        """Default discovery hook - return empty list if not overridden."""
        return []

    def fetch(self, discovery_items: List[Any]) -> List[Any]:
        """Default fetch hook - return raw items if not overridden."""
        return discovery_items

    def parse(self, raw_data: List[Any]) -> List[Any]:
        """Default parse hook - return items if not overridden."""
        return raw_data

    def validate(self, parsed_data: List[Any]) -> ValidationReport:
        """Default validation hook."""
        return ValidationReport(
            is_valid=True,
            total_objects=len(parsed_data),
            valid_objects=len(parsed_data),
            invalid_objects=0,
        )

    def health(self) -> ProviderState:
        return self.state

    def shutdown(self) -> None:
        """Graceful resource release."""
        try:
            if self.state in (ProviderState.UNINITIALIZED, ProviderState.READY, ProviderState.COMPLETED, ProviderState.FAILED):
                logger.info(f"BaseProvider: Shutting down '{self.metadata.name}'")
        except Exception as e:
            logger.error(f"Error during shutdown of '{self.metadata.name}': {e}")

    def run(self) -> ProviderResult:
        """
        Execute provider lifecycle via ExecutionEngine.
        """
        from providers.base.execution_engine import ExecutionEngine
        engine = ExecutionEngine()
        return engine.execute(self)

