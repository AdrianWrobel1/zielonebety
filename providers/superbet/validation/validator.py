"""
Superbet Model Validator Module
"""

from typing import List, Dict, Any
from providers.base.models import ValidationReport
from providers.superbet.models import SuperbetEvent


class SuperbetValidator:
    """Validates parsed Superbet domain models for mandatory field completeness and betting integrity."""

    def validate_events(self, events: List[SuperbetEvent]) -> ValidationReport:
        """Validates events list and produces structured ValidationReport."""
        total_objects = len(events)
        valid_objects = 0
        invalid_objects = 0
        rejection_reasons: List[Dict[str, Any]] = []

        for ev in events:
            reasons: List[str] = []

            if not ev.event_id:
                reasons.append("Missing event_id")
            if not ev.name:
                reasons.append("Missing event name")
            if not ev.home_team:
                reasons.append("Missing home_team")
            if not ev.away_team:
                reasons.append("Missing away_team")

            for m in ev.markets:
                if not m.market_id:
                    reasons.append(f"Market missing ID in event {ev.event_id}")
                if not m.name:
                    reasons.append(f"Market missing name in event {ev.event_id}")
                for s in m.selections:
                    if not s.selection_id:
                        reasons.append(f"Selection missing ID in market {m.market_id}")
                    if not s.name:
                        reasons.append(f"Selection missing name in market {m.market_id}")
                    # Only active selections require open decimal odds > 1.0
                    if s.is_active and (s.odds is None or s.odds.decimal_odds <= 1.0):
                        reasons.append(
                            f"Invalid decimal odds ({s.odds.decimal_odds if s.odds else None}) in active selection {s.selection_id}"
                        )

            if reasons:
                invalid_objects += 1
                rejection_reasons.append({
                    "event_id": ev.event_id,
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
