"""
Unit Tests for ProviderStateMachine (Task 022)
"""

import pytest
from providers.base.exceptions import ProviderStateError
from providers.base.provider_state import ProviderState, ProviderStateMachine


def test_provider_state_machine_valid_flow():
    sm = ProviderStateMachine()
    assert sm.current_state == ProviderState.UNINITIALIZED

    sm.transition_to(ProviderState.INITIALIZING)
    assert sm.current_state == ProviderState.INITIALIZING

    sm.transition_to(ProviderState.READY)
    assert sm.current_state == ProviderState.READY

    sm.transition_to(ProviderState.EXECUTING)
    assert sm.current_state == ProviderState.EXECUTING

    sm.transition_to(ProviderState.COMPLETED)
    assert sm.current_state == ProviderState.COMPLETED


def test_provider_state_machine_illegal_transition():
    sm = ProviderStateMachine()
    assert sm.current_state == ProviderState.UNINITIALIZED

    with pytest.raises(ProviderStateError, match="Illegal state transition"):
        sm.transition_to(ProviderState.EXECUTING)

    # State remains unchanged
    assert sm.current_state == ProviderState.UNINITIALIZED


def test_provider_state_machine_degraded_flow():
    sm = ProviderStateMachine(initial_state=ProviderState.EXECUTING)
    sm.transition_to(ProviderState.DEGRADED)
    assert sm.current_state == ProviderState.DEGRADED

    sm.transition_to(ProviderState.COMPLETED)
    assert sm.current_state == ProviderState.COMPLETED


def test_provider_state_machine_failure_and_reset():
    sm = ProviderStateMachine(initial_state=ProviderState.EXECUTING)
    sm.transition_to(ProviderState.FAILED)
    assert sm.current_state == ProviderState.FAILED

    sm.reset()
    assert sm.current_state == ProviderState.UNINITIALIZED
    assert len(sm.history) == 0


def test_provider_state_machine_history():
    sm = ProviderStateMachine()
    sm.transition_to(ProviderState.INITIALIZING)
    sm.transition_to(ProviderState.READY)

    hist = sm.history
    assert len(hist) == 2
    assert hist[0][0] == ProviderState.UNINITIALIZED
    assert hist[0][1] == ProviderState.INITIALIZING
    assert hist[1][0] == ProviderState.INITIALIZING
    assert hist[1][1] == ProviderState.READY
