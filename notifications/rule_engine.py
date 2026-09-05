"""
Notification Rule Engine & Deduplication Filter

.. deprecated::
    Legacy notification stack (see notifications.notification_engine).
    Kept only for backward compatibility with existing tests.
"""

from typing import Optional, Set
from scanner.models import Opportunity, OpportunityType
from notifications.models import NotificationRule, NotificationPriority


class RuleEngine:
    """Evaluates opportunities against user rules and deduplication filters."""

    def __init__(self, rule: Optional[NotificationRule] = None):
        self.rule = rule or NotificationRule()
        self._sent_fingerprints: Set[str] = set()

    def evaluate(self, opportunity: Opportunity) -> Optional[NotificationPriority]:
        """Evaluates if an opportunity satisfies user rules and returns priority level, or None if filtered out."""
        # 1. Deduplication Check
        fp = opportunity.fingerprint
        if fp in self._sent_fingerprints:
            return None

        # 2. Enabled Type Check
        if opportunity.opportunity_type.name not in self.rule.enabled_types:
            return None

        # 3. ROI / EV Threshold Check
        if opportunity.opportunity_type == OpportunityType.SUREBET:
            if opportunity.roi_percentage < self.rule.min_roi_percentage:
                return None
            priority = NotificationPriority.HIGH if opportunity.roi_percentage >= 3.0 else NotificationPriority.NORMAL
            if opportunity.roi_percentage >= 5.0:
                priority = NotificationPriority.CRITICAL
        else:
            if opportunity.ev_percentage < self.rule.min_ev_percentage:
                return None
            priority = NotificationPriority.HIGH if opportunity.ev_percentage >= 5.0 else NotificationPriority.NORMAL
            if opportunity.ev_percentage >= 10.0:
                priority = NotificationPriority.CRITICAL

        # Mark as sent for deduplication
        self._sent_fingerprints.add(fp)
        return priority

    def clear_deduplication_history(self) -> None:
        """Clear sent fingerprint history."""
        self._sent_fingerprints.clear()
