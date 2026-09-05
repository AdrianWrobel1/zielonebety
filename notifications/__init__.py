"""
Notification Package

Authoritative production path: normalization.dispatcher /
normalization.lifecycle / telegram_consumer / HttpTelegramClient
(via props_notification_manager).

P1-005: the legacy stack (NotificationEngine, RuleEngine,
NotificationQueue, TelegramNotificationProvider) is deprecated, is not
imported by any production runtime path, and is therefore no longer
re-exported here. Import it from its module (tests-only) if needed.
"""

from notifications.models import (
    NotificationPriority,
    NotificationStatus,
    NotificationRule,
    NotificationMessage,
)
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
