"""
Canonical Domain Package
"""

from domain.models import (
    Competition,
    Event,
    Market,
    Selection,
    Odds,
    generate_canonical_id,
    generate_deterministic_canonical_event_id,
    EventSource,
    MatchEvidence,
    CanonicalCompetition,
    CanonicalEvent,
)
from domain.exceptions import DomainError, DomainValidationError, CanonicalIDError

__all__ = [
    "Competition",
    "Event",
    "Market",
    "Selection",
    "Odds",
    "generate_canonical_id",
    "generate_deterministic_canonical_event_id",
    "EventSource",
    "MatchEvidence",
    "CanonicalCompetition",
    "CanonicalEvent",
    "DomainError",
    "DomainValidationError",
    "CanonicalIDError",
]

