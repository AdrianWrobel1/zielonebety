"""
Unit Tests for ProviderManager Orchestration & Aggregation
"""

import unittest
from providers.base.provider_registry import ProviderRegistry
from providers.base.provider_manager import ProviderManager
from providers.base.base_provider import BaseProvider
from providers.base.models import ValidationReport
from providers.base.provider_state import ProviderState


class ProviderAlpha(BaseProvider):
    def discover(self):
        return ["alpha_event"]

    def fetch(self, items):
        return [items]

    def parse(self, raw):
        return [raw]

    def validate(self, parsed):
        return ValidationReport(is_valid=True, total_objects=1, valid_objects=1)


class ProviderBeta(BaseProvider):
    def discover(self):
        raise RuntimeError("Beta provider network down")

    def fetch(self, items):
        return []

    def parse(self, raw):
        return []

    def validate(self, parsed):
        return ValidationReport()


class TestProviderManager(unittest.TestCase):
    def setUp(self):
        self._saved_registry = dict(ProviderRegistry._registry)
        ProviderRegistry.register("alpha", ProviderAlpha)
        ProviderRegistry.register("beta", ProviderBeta)

    def tearDown(self):
        ProviderRegistry._registry = self._saved_registry


    def test_execute_single_provider(self):
        manager = ProviderManager()
        result = manager.execute_provider("alpha")
        self.assertEqual(result.provider_name, "alpha")
        self.assertEqual(result.status, ProviderState.COMPLETED)

    def test_execute_multi_providers_fault_isolation(self):
        manager = ProviderManager()
        aggregated = manager.execute_providers()

        self.assertEqual(aggregated.total_providers, 2)
        self.assertEqual(aggregated.successful_providers, 1)
        self.assertEqual(aggregated.failed_providers, 1)

        self.assertEqual(aggregated.results["alpha"].status, ProviderState.COMPLETED)
        self.assertEqual(aggregated.results["beta"].status, ProviderState.FAILED)
        self.assertTrue(len(aggregated.errors) > 0)

    def test_health_check_aggregation(self):
        manager = ProviderManager()
        health_report = manager.check_health()

        self.assertIn("alpha", health_report["provider_health"])
        self.assertIn("beta", health_report["provider_health"])
        self.assertIn(health_report["overall_status"], ["HEALTHY", "FAILED", "DEGRADED"])


if __name__ == "__main__":
    unittest.main()
