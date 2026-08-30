"""
Unit Tests for BaseProvider Execution & Lifecycle
"""

import unittest
from providers.base.base_provider import BaseProvider
from providers.base.provider_context import ProviderContext
from providers.base.models import ProviderMetadata, ValidationReport
from providers.base.provider_state import ProviderState


class MockSuccessProvider(BaseProvider):
    def discover(self):
        return ["event_1", "event_2"]

    def fetch(self, discovery_items):
        return [{"id": item} for item in discovery_items]

    def parse(self, raw_data):
        return [{"id": item["id"], "parsed": True} for item in raw_data]

    def validate(self, parsed_data):
        return ValidationReport(is_valid=True, total_objects=len(parsed_data), valid_objects=len(parsed_data))


class MockFailingProvider(BaseProvider):
    def discover(self):
        raise RuntimeError("Network failure during discovery")

    def fetch(self, discovery_items):
        return []

    def parse(self, raw_data):
        return []

    def validate(self, parsed_data):
        return ValidationReport()


class TestBaseProvider(unittest.TestCase):
    def test_base_provider_successful_run(self):
        ctx = ProviderContext(provider_name="mock_success")
        meta = ProviderMetadata(name="mock_success", code="mock")
        provider = MockSuccessProvider(context=ctx, metadata=meta)

        result = provider.run()

        self.assertEqual(result.provider_name, "mock_success")
        self.assertEqual(result.status, ProviderState.COMPLETED)
        self.assertEqual(len(result.discovered_objects), 2)
        self.assertEqual(len(result.parsed_objects), 2)
        self.assertTrue(result.validation_report.is_valid)
        self.assertEqual(len(result.errors), 0)

    def test_base_provider_failure_run(self):
        ctx = ProviderContext(provider_name="mock_fail")
        meta = ProviderMetadata(name="mock_fail", code="mock")
        provider = MockFailingProvider(context=ctx, metadata=meta)

        result = provider.run()

        self.assertEqual(result.provider_name, "mock_fail")
        self.assertEqual(result.status, ProviderState.FAILED)
        self.assertEqual(len(result.errors), 1)
        self.assertIn("Network failure during discovery", result.errors[0])
        self.assertEqual(result.metrics.failure_count, 1)

    def test_disabled_provider_run(self):
        ctx = ProviderContext(provider_name="disabled_provider")
        meta = ProviderMetadata(name="disabled_provider", code="mock", enabled=False)
        provider = MockSuccessProvider(context=ctx, metadata=meta)

        result = provider.run()

        self.assertEqual(result.status, ProviderState.CANCELLED)
        self.assertEqual(len(result.warnings), 1)
        self.assertIn("Provider is disabled", result.warnings[0])



if __name__ == "__main__":
    unittest.main()
