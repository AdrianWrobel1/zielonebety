"""
Unit Tests for ProviderFactory
"""

import unittest
from providers.base.provider_registry import ProviderRegistry
from providers.base.provider_factory import ProviderFactory
from providers.base.base_provider import BaseProvider
from providers.base.models import ValidationReport
from providers.base.exceptions import ProviderNotFoundError


class DummyProvider(BaseProvider):
    def discover(self):
        return []

    def fetch(self, items):
        return []

    def parse(self, raw):
        return []

    def validate(self, parsed):
        return ValidationReport()


class TestProviderFactory(unittest.TestCase):
    def setUp(self):
        ProviderRegistry.clear()
        ProviderRegistry.register("dummy", DummyProvider)

    def tearDown(self):
        ProviderRegistry.clear()

    def test_create_registered_provider(self):
        provider = ProviderFactory.create_provider("dummy", config={"param": "value"})
        self.assertIsInstance(provider, DummyProvider)
        self.assertEqual(provider.metadata.name, "dummy")
        self.assertEqual(provider.context.config["param"], "value")

    def test_create_unregistered_provider(self):
        with self.assertRaises(ProviderNotFoundError):
            ProviderFactory.create_provider("unknown")


if __name__ == "__main__":
    unittest.main()
