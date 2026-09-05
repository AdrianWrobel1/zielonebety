"""
Targeted tests for Telegram Admin and Health Telemetry API.

Validates:
1. Test message dispatch uses existing PropsNotificationManager transport.
2. Error status and secret protection (tokens never exposed, safe error messages).
3. Configuration toggles for instant notifications and evening digest.
4. REST API endpoints (/api/v1/telegram/health, test, configure).
"""

from datetime import datetime, timezone
import unittest
from unittest.mock import MagicMock

from notifications.props_notification_manager import (
    NotificationAction,
    PropsNotificationManager,
)
from notifications.telegram_client import FakeTelegramClient, TelegramSendResult
from notifications.telegram_consumer import TelegramConfig
from api.services import PlatformAPIService
from database.connection import DatabaseManager
from database.config import DatabaseConfig


class TestTelegramAdminAPI(unittest.TestCase):

    def setUp(self):
        self.db_mgr = DatabaseManager(DatabaseConfig.default_sqlite_in_memory())
        self.db_mgr.create_tables()

    def test_admin_test_message_uses_existing_transport(self):
        """1. Test message dispatches via PropsNotificationManager and updates telemetry."""
        fake_client = FakeTelegramClient(simulate_message_id="9999")
        cfg = TelegramConfig(bot_token="secret_token_123", chat_id="-100123456789")
        mgr = PropsNotificationManager(config=cfg, client=fake_client, chat_id="-100123456789")

        res = mgr.send_test_message()
        self.assertTrue(res.delivered)
        self.assertEqual(res.telegram_message_id, "9999")
        self.assertEqual(len(fake_client.sent_messages), 1)
        self.assertIn("ZieloneBety Telegram Health Check", fake_client.sent_messages[0]["text"])

        # Check telemetry updated
        health = mgr.get_health_status()
        self.assertEqual(health["telegram_status"], "CONNECTED")
        self.assertEqual(health["last_successful_message_id"], "9999")
        self.assertIsNotNone(health["last_successful_message_at"])
        self.assertEqual(health["daily_sent_count"], 1)
        self.assertIsNone(health["last_error"])

    def test_admin_error_status_and_secret_safety(self):
        """2. When transport fails, status is ERROR, safe message returned, secret NEVER exposed."""
        secret_token = "secret_super_token_xyz"
        failing_client = MagicMock()
        failing_client.send_message.return_value = TelegramSendResult(
            success=False,
            error="Telegram API error (500): Internal Server Error",
            http_status=500,
        )
        cfg = TelegramConfig(bot_token=secret_token, chat_id="-100987654321")
        mgr = PropsNotificationManager(config=cfg, client=failing_client, chat_id="-100987654321")

        res = mgr.send_test_message()
        self.assertFalse(res.delivered)

        health = mgr.get_health_status()
        self.assertEqual(health["telegram_status"], "ERROR")
        self.assertIn("500", health["last_error"])
        self.assertIn("Telegram unavailable", health["error_message"])

        # Strict secret check: secret_token MUST NOT appear in any health output
        health_str = str(health)
        self.assertNotIn(secret_token, health_str)
        self.assertNotIn("bot_token", health)
        # Chat ID masked
        self.assertTrue(health["safe_config"]["chat_id_masked"].startswith("***"))

    def test_admin_configure_toggles(self):
        """3. Instant alerts and evening digest can be safely enabled/disabled."""
        fake_client = FakeTelegramClient()
        mgr = PropsNotificationManager(client=fake_client, chat_id="12345")

        # Initial: both true
        h1 = mgr.get_health_status()
        self.assertTrue(h1["instant_alerts_enabled"])
        self.assertTrue(h1["evening_digest_enabled"])

        # Disable instant alerts
        mgr.configure(instant_alerts_enabled=False)
        h2 = mgr.get_health_status()
        self.assertFalse(h2["instant_alerts_enabled"])
        self.assertTrue(h2["evening_digest_enabled"])

        # Disable evening digest
        mgr.configure(evening_digest_enabled=False)
        h3 = mgr.get_health_status()
        self.assertFalse(h3["instant_alerts_enabled"])
        self.assertFalse(h3["evening_digest_enabled"])

    def test_api_endpoints_integration(self):
        """4. PlatformAPIService and APIRouter correctly route telegram admin calls."""
        service = PlatformAPIService(db_manager=self.db_mgr)
        fake_client = FakeTelegramClient(simulate_message_id="7777")
        service.scheduler._props_notification_manager = PropsNotificationManager(
            client=fake_client,
            chat_id="12345",
            config=TelegramConfig(bot_token="test_tok", chat_id="12345"),
        )

        from api.routes import APIRouter
        router = APIRouter(service=service)

        # GET health
        health_resp = router.handle_get_telegram_health()
        self.assertEqual(health_resp.status_code, 200)
        self.assertIn("telegram_status", health_resp.data)

        # POST test message
        test_resp = router.handle_post_telegram_test()
        self.assertEqual(test_resp.status_code, 200)
        self.assertTrue(test_resp.data["delivered"])
        self.assertEqual(test_resp.data["telegram_message_id"], "7777")

        # POST configure
        cfg_resp = router.handle_post_telegram_configure({"instant_alerts_enabled": False})
        self.assertEqual(cfg_resp.status_code, 200)
        self.assertFalse(cfg_resp.data["instant_alerts_enabled"])

    def test_admin_test_message_logs_activity(self):
        """5. Test message dispatch logs an activity record in recent_activity."""
        fake_client = FakeTelegramClient(simulate_message_id="8888")
        mgr = PropsNotificationManager(
            client=fake_client,
            chat_id="-10012345",
            config=TelegramConfig(bot_token="tok_123", chat_id="-10012345"),
        )
        self.assertEqual(len(mgr._recent_activity), 0)

        res = mgr.send_test_message()
        self.assertTrue(res.delivered)
        self.assertEqual(len(mgr._recent_activity), 1)

        act = mgr._recent_activity[0]
        self.assertEqual(act["event_type"], "TEST_MESSAGE")
        self.assertEqual(act["status"], "DELIVERED")
        self.assertEqual(act["telegram_message_id"], "8888")
        self.assertIn("Diagnostic", act["title"])

        # Health includes recent activity
        health = mgr.get_health_status()
        self.assertIn("recent_activity", health)
        self.assertEqual(len(health["recent_activity"]), 1)
        self.assertEqual(health["recent_activity"][0]["id"], act["id"])


if __name__ == "__main__":
    unittest.main()

