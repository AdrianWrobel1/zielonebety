"""
Provider Lifecycle State Machine

Enforces strict lifecycle transitions and history recording for scraping execution runs.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum, auto
import logging
import threading
from typing import Dict, List, Set, Tuple

from providers.base.exceptions import ProviderStateError

logger = logging.getLogger("framework.provider_state")


class ProviderState(Enum):
    UNINITIALIZED = auto()
    INITIALIZING = auto()
    READY = auto()
    EXECUTING = auto()
    COMPLETED = auto()
    DEGRADED = auto()
    FAILED = auto()
    CANCELLED = auto()

    def can_transition_to(self, target_state: "ProviderState") -> bool:
        """Determines if a state transition is valid."""
        allowed: Set["ProviderState"] = ALLOWED_TRANSITIONS.get(self, set())
        return target_state in allowed


ALLOWED_TRANSITIONS: Dict[ProviderState, Set[ProviderState]] = {
    ProviderState.UNINITIALIZED: {ProviderState.INITIALIZING, ProviderState.FAILED, ProviderState.CANCELLED},
    ProviderState.INITIALIZING: {ProviderState.READY, ProviderState.FAILED, ProviderState.CANCELLED},
    ProviderState.READY: {ProviderState.EXECUTING, ProviderState.FAILED, ProviderState.CANCELLED},
    ProviderState.EXECUTING: {ProviderState.COMPLETED, ProviderState.DEGRADED, ProviderState.FAILED, ProviderState.CANCELLED},
    ProviderState.DEGRADED: {ProviderState.COMPLETED, ProviderState.FAILED, ProviderState.CANCELLED},
    ProviderState.COMPLETED: {ProviderState.READY, ProviderState.INITIALIZING},
    ProviderState.FAILED: {ProviderState.READY, ProviderState.INITIALIZING},
    ProviderState.CANCELLED: {ProviderState.READY, ProviderState.INITIALIZING},
}


class ProviderStateMachine:
    """
    Thread-safe lifecycle state machine for provider executions.
    """

    def __init__(self, initial_state: ProviderState = ProviderState.UNINITIALIZED) -> None:
        self._lock = threading.Lock()
        self._state = initial_state
        self._history: List[Tuple[ProviderState, ProviderState, str]] = []

    @property
    def current_state(self) -> ProviderState:
        with self._lock:
            return self._state

    @property
    def history(self) -> List[Tuple[ProviderState, ProviderState, str]]:
        with self._lock:
            return list(self._history)

    def transition_to(self, target_state: ProviderState) -> None:
        """
        Attempt transition to target_state.
        Raises ProviderStateError if transition is illegal.
        """
        with self._lock:
            if not self._state.can_transition_to(target_state):
                raise ProviderStateError(
                    f"Illegal state transition from {self._state.name} to {target_state.name}",
                    details={"from": self._state.name, "to": target_state.name}
                )

            timestamp = datetime.now(timezone.utc).isoformat()
            self._history.append((self._state, target_state, timestamp))
            logger.debug(f"ProviderStateMachine: Transition {self._state.name} -> {target_state.name}")
            self._state = target_state

    def reset(self) -> None:
        """Reset state machine to UNINITIALIZED."""
        with self._lock:
            self._state = ProviderState.UNINITIALIZED
            self._history.clear()

