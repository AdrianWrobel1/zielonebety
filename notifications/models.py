"""
Notification System Data Models
"""

from enum import Enum, auto
from dataclasses import dataclass, field
from typing import Optional, Dict, Any
from datetime import datetime, timezone
import uuid


class NotificationPriority(Enum):
    LOW = 1
    NORMAL = 2
    HIGH = 3
    CRITICAL = 4


class NotificationStatus(Enum):
    QUEUED = auto()
    DELIVERED = auto()
    FAILED = auto()
    DISCARDED = auto()
    DEDUPLICATED = auto()


@dataclass
class NotificationRule:
    min_roi_percentage: float = 1.0
    min_ev_percentage: float = 2.0
    enabled_types: list = field(default_factory=lambda: ["SUREBET", "VALUEBET"])
    recipient_chat_id: str = "default_chat"


@dataclass
class NotificationMessage:
    opportunity_id: str
    recipient: str
    priority: NotificationPriority
    content: str
    notification_id: str = field(default_factory=lambda: f"notif_{uuid.uuid4()}")
    status: NotificationStatus = NotificationStatus.QUEUED
    retry_count: int = 0
    failure_reason: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    delivered_at: Optional[str] = None
