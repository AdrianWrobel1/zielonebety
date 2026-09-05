"""
Telegram Notification Delivery Provider

.. deprecated::
    Legacy notification stack. Not used by any production runtime path.
    The authoritative production path is ``normalization.dispatcher`` /
    ``normalization.lifecycle`` / ``notifications.telegram_consumer`` /
    ``notifications.telegram_client.HttpTelegramClient`` (via
    ``notifications.props_notification_manager``). This module is kept only
    for backward compatibility with existing tests and must not be imported
    by production code.
"""

import warnings
from datetime import datetime, timezone
from notifications.models import NotificationMessage, NotificationStatus
from notifications.exceptions import NotificationDeliveryError
from scanner.models import Opportunity

warnings.warn(
    "notifications.telegram_provider is a deprecated legacy module; "
    "use notifications.telegram_consumer / HttpTelegramClient instead.",
    DeprecationWarning,
    stacklevel=2,
)


class TelegramNotificationProvider:
    """Handles Telegram message formatting and delivery dispatch."""

    def __init__(self, bot_token: str = "MOCK_TOKEN"):
        self.bot_token = bot_token

    def format_opportunity_message(self, opportunity: Opportunity) -> str:
        """Formats an Opportunity object into structured Telegram Markdown text."""
        header = f"🚀 *{opportunity.opportunity_type.name} ALERT*"
        info = f"• *Market*: {opportunity.market_type}\n• *ROI/EV*: +{opportunity.roi_percentage:.2f}%"

        legs_str = []
        for idx, leg in enumerate(opportunity.legs, start=1):
            legs_str.append(
                f"  {idx}\\. *{leg.bookmaker.upper()}* \\| {leg.selection_type} @ `{leg.decimal_odds:.2f}`"
            )

        legs_text = "\n".join(legs_str)
        return f"{header}\n{info}\n\n*Legs*:\n{legs_text}"

    def send_message(self, message: NotificationMessage, mock_dispatch=None) -> NotificationMessage:
        """Delivers notification message via Telegram API (or mock function).

        P1-005: without a transport (no ``mock_dispatch`` and no real HTTP
        client) delivery is impossible, so the message is marked FAILED with
        an explicit reason instead of a simulated DELIVERED.
        """
        try:
            if mock_dispatch is not None:
                mock_dispatch(message)
            else:
                raise NotificationDeliveryError(
                    "No Telegram transport configured: legacy simulated delivery is disabled."
                )

            message.status = NotificationStatus.DELIVERED
            message.delivered_at = datetime.now(timezone.utc).isoformat()
            return message
        except Exception as e:
            message.status = NotificationStatus.FAILED
            message.retry_count += 1
            if not getattr(message, "failure_reason", None):
                message.failure_reason = str(e)
            raise NotificationDeliveryError(f"Telegram delivery failed: {e}") from e
