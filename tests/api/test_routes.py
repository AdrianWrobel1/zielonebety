"""
Unit Tests for REST API Endpoints & Application Services
"""

import unittest
from api.app import create_api_app
from api.services import PlatformAPIService
from database.connection import DatabaseManager
from database.config import DatabaseConfig
from providers.base.provider_registry import ProviderRegistry
from providers.betclic.provider import BetclicProvider


class TestAPIRoutes(unittest.TestCase):
    def setUp(self):
        ProviderRegistry.clear()
        ProviderRegistry.register("betclic", BetclicProvider)
        db_mgr = DatabaseManager(DatabaseConfig.default_sqlite_in_memory())
        db_mgr.create_tables()

        self.service = PlatformAPIService(db_manager=db_mgr)
        self.router = create_api_app(service=self.service)

    def tearDown(self):
        ProviderRegistry.clear()

    def test_get_health_endpoint(self):
        response = self.router.handle_get_health()
        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(response.data)
        self.assertTrue(response.data["database_connected"])
        self.assertGreaterEqual(response.execution_time_ms, 0.0)

    def test_get_providers_endpoint(self):
        response = self.router.handle_get_providers()
        self.assertEqual(response.status_code, 200)
        self.assertIn("betclic", response.data["provider_health"])

    def test_get_events_endpoint(self):
        response = self.router.handle_get_events(limit=10, offset=0)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.metadata["limit"], 10)
        self.assertEqual(response.metadata["count"], 0)

    def test_trigger_provider_run_endpoint(self):
        response = self.router.handle_post_trigger_provider("betclic")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["provider"], "betclic")
        self.assertIn(response.data["status"], ["COMPLETED", "DEGRADED", "FAILED"])


if __name__ == "__main__":
    unittest.main()
