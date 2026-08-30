"""
Notification Package
"""

from notifications.models import (
    NotificationPriority,
    NotificationStatus,
    NotificationRule,
    NotificationMessage,
)
from notifications.rule_engine import RuleEngine
from notifications.queue import NotificationQueue
from notifications.telegram_provider import TelegramNotificationProvider
from notifications.notification_engine import NotificationEngine
from notifications.exceptions import (
    NotificationError,
    NotificationDeliveryError,
    NotificationRuleError,
)
from notifications.telegram_client import (
    TelegramSendResult,
    TelegramClient,
    HttpTelegramClient,
    FakeTelegramClient,
)
from notifications.telegram_consumer import (
    TelegramConfig,
    TelegramOpportunityConsumer,
    format_telegram_surebet_message,
)

__all__ = [
    "NotificationPriority",
    "NotificationStatus",
    "NotificationRule",
    "NotificationMessage",
    "RuleEngine",
    "NotificationQueue",
    "TelegramNotificationProvider",
    "NotificationEngine",
    "NotificationError",
    "NotificationDeliveryError",
    "NotificationRuleError",
    "TelegramSendResult",
    "TelegramClient",
    "HttpTelegramClient",
    "FakeTelegramClient",
    "TelegramConfig",
    "TelegramOpportunityConsumer",
    "format_telegram_surebet_message",
]
