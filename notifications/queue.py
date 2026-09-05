"""
Priority Notification Delivery Queue

.. deprecated::
    Legacy notification stack (see notifications.notification_engine).
    Kept only for backward compatibility with existing tests.
"""

from typing import List, Optional
import heapq
from notifications.models import NotificationMessage, NotificationPriority


class NotificationQueue:
    """Priority queue storing pending notifications sorted by priority level."""

    def __init__(self):
        self._heap: List[tuple] = []
        self._counter: int = 0

    def push(self, message: NotificationMessage) -> None:
        """Enqueue message. Priority value negated so highest priority pops first."""
        self._counter += 1
        priority_key = -message.priority.value
        heapq.heappush(self._heap, (priority_key, self._counter, message))

    def pop(self) -> Optional[NotificationMessage]:
        """Dequeue highest priority message."""
        if not self._heap:
            return None
        _, _, message = heapq.heappop(self._heap)
        return message

    def is_empty(self) -> bool:
        return len(self._heap) == 0

    def size(self) -> int:
        return len(self._heap)
