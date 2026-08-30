"""
Unit Tests for Notification Rule Engine & Deduplication
"""

import unittest
from scanner.models import Opportunity, OpportunityType, OpportunityLeg
from notifications.rule_engine import RuleEngine
from notifications.models import NotificationRule, NotificationPriority


class TestRuleEngine(unittest.TestCase):
    def setUp(self):
        self.rule = NotificationRule(min_roi_percentage=2.0)
        self.engine = RuleEngine(self.rule)

    def test_evaluate_accepted_opportunity(self):
        leg = OpportunityLeg(bookmaker="betclic", selection_type="HOME", decimal_odds=2.10, implied_probability=0.47)
        opp = Opportunity(
            opportunity_type=OpportunityType.SUREBET,
            event_id="ev_1",
            market_type="1X2",
            roi_percentage=3.5,
            ev_percentage=3.5,
            legs=[leg]
        )

        priority = self.engine.evaluate(opp)
        self.assertEqual(priority, NotificationPriority.HIGH)

    def test_evaluate_deduplication_prevention(self):
        leg = OpportunityLeg(bookmaker="betclic", selection_type="HOME", decimal_odds=2.10, implied_probability=0.47)
        opp = Opportunity(
            opportunity_type=OpportunityType.SUREBET,
            event_id="ev_1",
            market_type="1X2",
            roi_percentage=3.5,
            ev_percentage=3.5,
            legs=[leg]
        )

        priority1 = self.engine.evaluate(opp)
        priority2 = self.engine.evaluate(opp)  # Duplicate call

        self.assertIsNotNone(priority1)
        self.assertIsNone(priority2)  # Prevented by deduplication filter

    def test_evaluate_low_roi_filtered_out(self):
        leg = OpportunityLeg(bookmaker="betclic", selection_type="HOME", decimal_odds=2.10, implied_probability=0.47)
        opp = Opportunity(
            opportunity_type=OpportunityType.SUREBET,
            event_id="ev_1",
            market_type="1X2",
            roi_percentage=0.5,  # Below min 2.0% threshold
            ev_percentage=0.5,
            legs=[leg]
        )

        priority = self.engine.evaluate(opp)
        self.assertIsNone(priority)


if __name__ == "__main__":
    unittest.main()
