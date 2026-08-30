"""
Odds API.io Validator Module
"""

from typing import List, Any
from providers.base.models import ValidationReport
from providers.odds_api.models import OddsApiEvent


class OddsApiValidator:
    """Validates parsed OddsApiEvent models."""

    def validate_events(self, events: List[Any]) -> ValidationReport:
        valid_count = 0
        invalid_count = 0
        rejection_reasons = []

        for ev in events:
            if not isinstance(ev, OddsApiEvent):
                invalid_count += 1
                rejection_reasons.append({"event": str(ev), "reasons": ["Not an OddsApiEvent instance"]})
                continue

            errors = []
            if not ev.provider_event_id:
                errors.append("Missing provider_event_id")
            if not ev.home_team or not ev.away_team:
                errors.append("Missing team participants")
            if not ev.markets:
                errors.append("Event has no markets")

            if errors:
                invalid_count += 1
                rejection_reasons.append({"event": ev.name, "reasons": errors})
            else:
                valid_count += 1

        return ValidationReport(
            total_objects=len(events),
            valid_objects=valid_count,
            invalid_objects=invalid_count,
            rejection_reasons=rejection_reasons,
        )
