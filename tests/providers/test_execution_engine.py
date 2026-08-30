"""
Unit Tests for ExecutionEngine
"""

import unittest
from providers.base.execution_engine import ExecutionEngine
from providers.base.base_provider import BaseProvider
from providers.base.provider_context import ProviderContext
from providers.base.models import ProviderMetadata, ValidationReport
from providers.base.provider_state import ProviderState


class StandardProvider(BaseProvider):
    def discover(self):
        return [1]

    def fetch(self, items):
        return [2]

    def parse(self, raw):
        return [3]

    def validate(self, parsed):
        return ValidationReport(is_valid=True, total_objects=1, valid_objects=1)


class TestExecutionEngine(unittest.TestCase):
    def test_execute_success(self):
        engine = ExecutionEngine()
        ctx = ProviderContext(provider_name="standard")
        meta = ProviderMetadata(name="standard", code="std")
        provider = StandardProvider(context=ctx, metadata=meta)

        result = engine.execute(provider)
        self.assertEqual(result.provider_name, "standard")
        self.assertEqual(result.status, ProviderState.COMPLETED)
        self.assertEqual(result.discovered_objects, [1])

    def test_execute_isolation_on_crash(self):
        engine = ExecutionEngine()
        ctx = ProviderContext(provider_name="crash")
        meta = ProviderMetadata(name="crash", code="crsh")
        provider = StandardProvider(context=ctx, metadata=meta)

        # Force unhandled method break
        provider.discover = lambda: (_ for _ in ()).throw(ValueError("Catastrophic error"))

        result = engine.execute(provider)
        self.assertEqual(result.status, ProviderState.FAILED)
        self.assertTrue(len(result.errors) > 0)


if __name__ == "__main__":
    unittest.main()
