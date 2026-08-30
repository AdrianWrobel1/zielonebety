"""
Unit Tests for ProviderRegistry
"""

import unittest
from providers.base.provider_registry import ProviderRegistry
from providers.base.base_provider import BaseProvider
from providers.base.models import ValidationReport
from providers.base.exceptions import ProviderDuplicateError, ProviderNotFoundError


class DummyProvider(BaseProvider):
    def discover(self):
        return ["event1"]

    def fetch(self, items):
        return [{"raw": "data"}]

    def parse(self, raw):
        return [{"parsed": "data"}]

    def validate(self, parsed):
        return ValidationReport(is_valid=True, total_objects=1, valid_objects=1)


class TestProviderRegistry(unittest.TestCase):
    def setUp(self):
        ProviderRegistry.clear()

    def tearDown(self):
        ProviderRegistry.clear()

    def test_register_and_get_provider(self):
        ProviderRegistry.register("dummy", DummyProvider)
        self.assertTrue(ProviderRegistry.is_registered("dummy"))
        self.assertEqual(ProviderRegistry.get("dummy"), DummyProvider)
        self.assertEqual(ProviderRegistry.list_providers(), ["dummy"])

    def test_register_duplicate_provider(self):
        ProviderRegistry.register("dummy", DummyProvider)
        with self.assertRaises(ProviderDuplicateError):
            ProviderRegistry.register("dummy", DummyProvider)

    def test_get_unregistered_provider(self):
        with self.assertRaises(ProviderNotFoundError):
            ProviderRegistry.get("nonexistent")

    def test_unregister_provider(self):
        ProviderRegistry.register("dummy", DummyProvider)
        ProviderRegistry.unregister("dummy")
        self.assertFalse(ProviderRegistry.is_registered("dummy"))


if __name__ == "__main__":
    unittest.main()
