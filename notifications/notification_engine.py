"""
Notification Engine Main Orchestrator

.. deprecated::
    Legacy notification stack. Not used by any production runtime path.
    The authoritative production path is ``normalization.dispatcher`` /
    ``normalization.lifecycle`` / ``notifications.telegram_consumer`` /
    ``notifications.telegram_client.HttpTelegramClient`` (via
    ``notifications.props_notification_manager``). This module is kept only
    for backward compatibility with existing tests and must not be imported
    by production code.
"""

import logging
from typing import List, Optional, Dict, Any
from scanner.models import Opportunity
from notifications.models import (
    NotificationMessage,
    NotificationRule,
    NotificationStatus,
    NotificationPriority,
)
from notifications.rule_engine import RuleEngine
from notifications.queue import NotificationQueue
from notifications.telegram_provider import TelegramNotificationProvider

logger = logging.getLogger("zielonebety.notifications.legacy")


class NotificationEngine:
    """Orchestrates notification filtering, queuing, priority delivery, and statistics."""

    def __init__(
        self,
        rule_engine: Optional[RuleEngine] = None,
        queue: Optional[NotificationQueue] = None,
        telegram_provider: Optional[TelegramNotificationProvider] = None
    ):
        self.rule_engine = rule_engine or RuleEngine()
        self.queue = queue or NotificationQueue()
        self.telegram_provider = telegram_provider or TelegramNotificationProvider()
        self.delivery_history: List[NotificationMessage] = []

    def process_opportunities(self, opportunities: List[Opportunity]) -> int:
        """Evaluates opportunities, constructs messages for accepted items, and pushes to queue."""
        queued_count = 0

        for opp in opportunities:
            priority = self.rule_engine.evaluate(opp)
            if priority is None:
                continue

            content = self.telegram_provider.format_opportunity_message(opp)
            msg = NotificationMessage(
                opportunity_id=opp.opportunity_id,
                recipient=self.rule_engine.rule.recipient_chat_id,
                priority=priority,
                content=content,
            )
            self.queue.push(msg)
            queued_count += 1

        return queued_count

    def flush_queue(self, mock_dispatch=None) -> List[NotificationMessage]:
        """Flushes all queued messages in priority order.

        P1-005: delivery failures are never swallowed. A failed message keeps
        FAILED status with an explicit reason, is recorded in history, and
        the failure is logged with its cause.
        """
        processed: List[NotificationMessage] = []

        while not self.queue.is_empty():
            msg = self.queue.pop()
            if not msg:
                break
            try:
                self.telegram_provider.send_message(msg, mock_dispatch=mock_dispatch)
            except Exception as e:
                if msg.status != NotificationStatus.FAILED:
                    msg.status = NotificationStatus.FAILED
                if not getattr(msg, "failure_reason", None):
                    msg.failure_reason = str(e)
                logger.warning(
                    "Legacy notification delivery failed for opportunity '%s': %s",
                    getattr(msg, "opportunity_id", "?"),
                    msg.failure_reason,
                )
            self.delivery_history.append(msg)
            processed.append(msg)

        return processed

    def get_metrics(self) -> Dict[str, Any]:
        """Returns notification delivery statistics."""
        total = len(self.delivery_history)
        delivered = sum(1 for m in self.delivery_history if m.status == NotificationStatus.DELIVERED)
        failed = sum(1 for m in self.delivery_history if m.status == NotificationStatus.FAILED)

        return {
            "total_sent": total,
            "delivered": delivered,
            "failed": failed,
            "pending_in_queue": self.queue.size(),
        }
