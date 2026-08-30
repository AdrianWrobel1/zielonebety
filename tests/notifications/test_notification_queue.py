"""
Unit Tests for Priority Notification Queue
"""

import unittest
from notifications.queue import NotificationQueue
from notifications.models import NotificationMessage, NotificationPriority


class TestNotificationQueue(unittest.TestCase):
    def setUp(self):
        self.queue = NotificationQueue()

    def test_priority_queue_ordering(self):
        msg_low = NotificationMessage(opportunity_id="1", recipient="chat", priority=NotificationPriority.LOW, content="low")
        msg_crit = NotificationMessage(opportunity_id="2", recipient="chat", priority=NotificationPriority.CRITICAL, content="critical")
        msg_high = NotificationMessage(opportunity_id="3", recipient="chat", priority=NotificationPriority.HIGH, content="high")

        self.queue.push(msg_low)
        self.queue.push(msg_crit)
        self.queue.push(msg_high)

        self.assertEqual(self.queue.size(), 3)
        self.assertEqual(self.queue.pop().priority, NotificationPriority.CRITICAL)
        self.assertEqual(self.queue.pop().priority, NotificationPriority.HIGH)
        self.assertEqual(self.queue.pop().priority, NotificationPriority.LOW)
        self.assertTrue(self.queue.is_empty())


if __name__ == "__main__":
    unittest.main()
