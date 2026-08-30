"""
Notification System Exceptions
"""

from core.exceptions import BaseApplicationError


class NotificationError(BaseApplicationError):
    """Base exception for notification delivery failures."""
    pass


class NotificationDeliveryError(NotificationError):
    """Raised when delivery to notification provider fails."""
    pass


class NotificationRuleError(NotificationError):
    """Raised when rule engine evaluation fails."""
    pass
