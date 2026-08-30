"""
Unit Tests for NotificationEngine Orchestration & Dispatch
"""

import unittest
from scanner.models import Opportunity, OpportunityType, OpportunityLeg
from notifications.notification_engine import NotificationEngine
from notifications.models import NotificationStatus, NotificationPriority


class TestNotificationEngine(unittest.TestCase):
    def setUp(self):
        self.engine = NotificationEngine()

    def test_process_and_flush_notifications(self):
        leg = OpportunityLeg(bookmaker="betclic", selection_type="HOME", decimal_odds=2.50, implied_probability=0.40)
        opp = Opportunity(
            opportunity_type=OpportunityType.SUREBET,
            event_id="ev_100",
            market_type="1X2",
            roi_percentage=6.0,  # Critical priority (>5.0%)
            ev_percentage=6.0,
            legs=[leg]
        )

        queued = self.engine.process_opportunities([opp])
        self.assertEqual(queued, 1)

        dispatched = []

        def mock_dispatch(msg):
            dispatched.append(msg)

        delivered_messages = self.engine.flush_queue(mock_dispatch=mock_dispatch)
        self.assertEqual(len(delivered_messages), 1)
        self.assertEqual(delivered_messages[0].status, NotificationStatus.DELIVERED)
        self.assertEqual(delivered_messages[0].priority, NotificationPriority.CRITICAL)

        metrics = self.engine.get_metrics()
        self.assertEqual(metrics["total_sent"], 1)
        self.assertEqual(metrics["delivered"], 1)
        self.assertEqual(metrics["pending_in_queue"], 0)


if __name__ == "__main__":
    unittest.main()
