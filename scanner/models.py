"""
Scanner Opportunity Data Models
"""

from enum import Enum, auto
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone
import uuid


class OpportunityType(Enum):
    SUREBET = auto()
    VALUEBET = auto()


@dataclass(frozen=True)
class OpportunityLeg:
    bookmaker: str
    selection_type: str
    decimal_odds: float
    implied_probability: float
    stake_percentage: float = 0.0
    selection_id: Optional[str] = None


@dataclass(frozen=True)
class Opportunity:
    opportunity_type: OpportunityType
    event_id: str
    market_type: str
    roi_percentage: float
    ev_percentage: float
    legs: List[OpportunityLeg]
    opportunity_id: str = field(default_factory=lambda: f"opp_{uuid.uuid4()}")
    detected_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @property
    def fingerprint(self) -> str:
        """Deterministic fingerprint for deduplication."""
        leg_str = "_".join(sorted([f"{l.bookmaker}:{l.selection_type}:{l.decimal_odds}" for l in self.legs]))
        return f"{self.opportunity_type.name}_{self.event_id}_{self.market_type}_{leg_str}"
