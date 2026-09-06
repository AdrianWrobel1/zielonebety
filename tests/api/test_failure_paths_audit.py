import unittest
from api.app import create_api_app
from api.services import PlatformAPIService
from database.connection import DatabaseManager
from database.config import DatabaseConfig
from api.auth import clear_login_attempts, is_login_rate_limited, register_login_attempt, verify_credentials

class TestFailurePathsAudit(unittest.TestCase):
    def setUp(self):
        clear_login_attempts()
        db_mgr = DatabaseManager(DatabaseConfig.default_sqlite_in_memory())
        db_mgr.create_tables()
        self.service = PlatformAPIService(db_manager=db_mgr)
        self.router = create_api_app(service=self.service)

    def tearDown(self):
        clear_login_attempts()

    def test_nonexistent_event_returns_404(self):
        res = self.router.handle_get_event_detail("nonexistent-event-999999")
        self.assertEqual(res.status_code, 404)
        self.assertIn("not found", res.errors[0].lower())

    def test_nonexistent_opportunity_returns_404(self):
        res = self.router.handle_get_opportunity_detail("nonexistent-opp-999999")
        self.assertEqual(res.status_code, 404)
        self.assertIn("not found", res.errors[0].lower())

    def test_nonexistent_prop_returns_404(self):
        res = self.router.handle_get_prop_detail("nonexistent-prop-999999")
        self.assertEqual(res.status_code, 404)
        self.assertIn("not found", res.errors[0].lower())

    def test_nonexistent_trace_returns_404(self):
        res = self.router.handle_get_trace_by_id("nonexistent-trace-999999")
        self.assertEqual(res.status_code, 404)
        self.assertIn("not found", res.errors[0].lower())

    def test_invalid_login_credentials_rejected(self):
        res = self.router.handle_post_auth_login("admin", "definitely-wrong-password-xyz")
        self.assertEqual(res.status_code, 401)
        self.assertIn("invalid", res.errors[0].lower())

    def test_login_brute_force_throttling(self):
        class MockRequest:
            client = type("Client", (), {"host": "192.168.1.100"})()

        req = MockRequest()
        for i in range(10):
            self.assertFalse(is_login_rate_limited(req))
            register_login_attempt(req, success=False)

        # 11th attempt must be throttled
        self.assertTrue(is_login_rate_limited(req))

    def test_malformed_pagination_handled_gracefully(self):
        res = self.router.handle_get_events(limit=0, offset=-5)
        # Router clamps or normalizes pagination
        self.assertIn(res.status_code, [200, 422])
