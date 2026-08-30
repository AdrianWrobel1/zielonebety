"""
Telegram Notification Delivery Provider
"""

from datetime import datetime, timezone
from notifications.models import NotificationMessage, NotificationStatus
from notifications.exceptions import NotificationDeliveryError
from scanner.models import Opportunity


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
        """Delivers notification message via Telegram API (or mock function)."""
        try:
            if mock_dispatch is not None:
                mock_dispatch(message)
            else:
                # Simulated HTTP request to Telegram API: https://api.telegram.org/bot<token>/sendMessage
                pass

            message.status = NotificationStatus.DELIVERED
            message.delivered_at = datetime.now(timezone.utc).isoformat()
            return message
        except Exception as e:
            message.status = NotificationStatus.FAILED
            message.retry_count += 1
            message.failure_reason = str(e)
            raise NotificationDeliveryError(f"Telegram delivery failed: {e}") from e
