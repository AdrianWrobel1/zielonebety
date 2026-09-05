"""P1-005 regression: legacy notification stack cannot fake delivery.

Usage audit (2026-09-04): no production runtime path (api/, orchestration/,
normalization/, scanner/, providers/) imports NotificationEngine,
RuleEngine, NotificationQueue or TelegramNotificationProvider. The
authoritative production path is dispatcher / lifecycle /
telegram_consumer / HttpTelegramClient (via PropsNotificationManager).
The legacy stack is therefore deprecated, not rewritten, with two
minimum truthfulness guarantees:

  - no transport success != DELIVERED (a simulated no-op send must fail
    loudly, never report DELIVERED)
  - delivery exceptions are never swallowed silently (explicit log +
    FAILED status with a failure reason)
"""

import logging

import pytest

from notifications.exceptions import NotificationDeliveryError
from notifications.models import NotificationMessage, NotificationPriority, NotificationStatus
from notifications.notification_engine import NotificationEngine
from notifications.telegram_provider import TelegramNotificationProvider


def _message():
    return NotificationMessage(
        opportunity_id="opp_p1005",
        recipient="-100123",
        priority=NotificationPriority.NORMAL,
        content="test",
    )


def test_simulated_send_without_transport_is_not_delivered():
    provider = TelegramNotificationProvider()
    msg = _message()
    with pytest.raises(NotificationDeliveryError):
        provider.send_message(msg)
    assert msg.status != NotificationStatus.DELIVERED
    assert msg.status == NotificationStatus.FAILED
    assert msg.failure_reason


def test_flush_queue_reports_delivery_failure_explicitly(caplog):
    engine = NotificationEngine()

    def failing_dispatch(msg):
        raise RuntimeError("smtp-down")

    msg = _message()
    engine.queue.push(msg)
    with caplog.at_level(logging.WARNING, logger="zielonebety.notifications.legacy"):
        processed = engine.flush_queue(mock_dispatch=failing_dispatch)

    assert len(processed) == 1
    assert processed[0].status == NotificationStatus.FAILED
    assert processed[0].failure_reason
    assert "smtp-down" in caplog.text
    metrics = engine.get_metrics()
    assert metrics["failed"] == 1
    assert metrics["delivered"] == 0


def test_legacy_stack_not_exported_as_production_api():
    import notifications

    for name in ("NotificationEngine", "RuleEngine", "NotificationQueue", "TelegramNotificationProvider"):
        assert not hasattr(notifications, name), f"legacy {name} must not be a package-level export"
    assert "NotificationEngine" not in getattr(notifications, "__all__", [])


def test_authoritative_notification_path_stays_available():
    import notifications

    for name in ("TelegramOpportunityConsumer", "HttpTelegramClient", "FakeTelegramClient", "TelegramConfig"):
        assert hasattr(notifications, name), f"authoritative export {name} must remain available"
