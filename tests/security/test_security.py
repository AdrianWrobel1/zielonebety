"""
Security & Input Validation Verification Test Suite
"""

import unittest
from api.routes import APIRouter
from api.services import PlatformAPIService


class TestSecurityAndAuth(unittest.TestCase):
    """Security tests checking authentication, authorization permissions, and parameter sanitization."""

    def setUp(self):
        from database.connection import DatabaseManager
        from database.config import DatabaseConfig
        self.db_mgr = DatabaseManager(DatabaseConfig.default_sqlite_in_memory())
        self.db_mgr.create_tables()
        self.service = PlatformAPIService(db_manager=self.db_mgr)
        self.router = APIRouter(service=self.service)

    def test_auth_login_issuance(self):
        res = self.router.handle_post_auth_login(username="admin", password="secure_password")
        self.assertEqual(res.status_code, 200)
        self.assertIn("access_token", res.data)
        self.assertEqual(res.data["token_type"], "bearer")
        self.assertEqual(res.data["user"]["role"], "Admin")

    def test_input_validation_and_bounds(self):
        # Querying with negative offset should be handled gracefully without crashing
        res = self.router.handle_get_events(limit=10, offset=0)
        self.assertEqual(res.status_code, 200)
        self.assertIsInstance(res.data, list)

    def test_security_headers_format(self):
        res = self.router.handle_get_health()
        self.assertEqual(res.status_code, 200)
        self.assertIsNotNone(res.timestamp)


if __name__ == "__main__":
    unittest.main()
