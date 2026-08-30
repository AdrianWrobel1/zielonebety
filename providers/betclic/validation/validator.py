"""
Betclic Model Validator Module
"""

from typing import List, Dict, Any
from providers.base.models import ValidationReport
from providers.betclic.models import BetclicEvent


class BetclicValidator:
    """Validates parsed Betclic domain models for mandatory field completeness and integrity."""

    def validate_events(self, events: List[BetclicEvent]) -> ValidationReport:
        """Validates events list and produces structured ValidationReport."""
        total_objects = len(events)
        valid_objects = 0
        invalid_objects = 0
        rejection_reasons: List[Dict[str, Any]] = []

        for ev in events:
            reasons: List[str] = []

            if not ev.provider_event_id:
                reasons.append("Missing provider_event_id")
            if not ev.name:
                reasons.append("Missing event name")

            for m in ev.markets:
                if not m.provider_market_id:
                    reasons.append(f"Market missing ID in event {ev.provider_event_id}")
                for s in m.selections:
                    if not s.provider_selection_id:
                        reasons.append(f"Selection missing ID in market {m.provider_market_id}")
                    # Only active selections require open decimal odds > 1.0
                    if s.odds and s.odds.is_active and s.odds.decimal_odds <= 1.0:
                        reasons.append(f"Invalid decimal odds ({s.odds.decimal_odds}) in active selection {s.provider_selection_id}")

            if reasons:
                invalid_objects += 1
                rejection_reasons.append({
                    "provider_event_id": ev.provider_event_id,
                    "reasons": reasons,
                })
            else:
                valid_objects += 1

        is_valid = (invalid_objects == 0)

        return ValidationReport(
            is_valid=is_valid,
            total_objects=total_objects,
            valid_objects=valid_objects,
            invalid_objects=invalid_objects,
            rejection_reasons=rejection_reasons,
        )
