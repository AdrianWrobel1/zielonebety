"""
Unit Tests for ProviderState State Machine
"""

import unittest
from providers.base.provider_state import ProviderState


class TestProviderState(unittest.TestCase):
    def test_valid_state_transitions(self):
        self.assertTrue(ProviderState.UNINITIALIZED.can_transition_to(ProviderState.INITIALIZING))
        self.assertTrue(ProviderState.INITIALIZING.can_transition_to(ProviderState.READY))
        self.assertTrue(ProviderState.READY.can_transition_to(ProviderState.EXECUTING))
        self.assertTrue(ProviderState.EXECUTING.can_transition_to(ProviderState.COMPLETED))
        self.assertTrue(ProviderState.EXECUTING.can_transition_to(ProviderState.DEGRADED))

    def test_invalid_state_transitions(self):
        self.assertFalse(ProviderState.UNINITIALIZED.can_transition_to(ProviderState.COMPLETED))
        self.assertFalse(ProviderState.UNINITIALIZED.can_transition_to(ProviderState.EXECUTING))
        self.assertFalse(ProviderState.COMPLETED.can_transition_to(ProviderState.EXECUTING))




if __name__ == "__main__":
    unittest.main()

