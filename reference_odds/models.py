"""
Reference Odds Data Models and Canonical Result Structures
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, List, Optional


class ReferenceSource(str, Enum):
    """Authoritative identifier of reference odds sources."""
    THE_ODDS_API = "the_odds_api"
    PINNACLE = "pinnacle"
    BETFAIR = "betfair"
    BETONLINE = "betonlineag"
    CONSENSUS = "consensus"
    MOCK = "mock"
    CUSTOM = "custom"


@dataclass(frozen=True)
class ReferenceSelection:
    """Individual selection outcome within a reference market."""
    selection_type: str
    odds: Decimal
    raw_probability: Optional[Decimal] = None
    fair_probability: Optional[Decimal] = None
    fair_odds: Optional[Decimal] = None
    line: Optional[Decimal] = None
    participant_role: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ReferenceMarket:
    """Market container from an external reference price source."""
    market_type: str
    line: Optional[Decimal] = None
    period: str = "FULL_TIME"
    scope: str = "MATCH"
    metric: str = "GOALS"
    participant_role: Optional[str] = None
    selections: Dict[str, ReferenceSelection] = field(default_factory=dict)
    timestamp: Optional[str] = None
    overround: Optional[Decimal] = None
    bookmaker_name: str = "pinnacle"
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ReferenceEvent:
    """Sporting event container holding external reference odds markets."""
    source: str
    source_event_id: str
    home_team: str
    away_team: str
    scheduled_start: Optional[str] = None
    sport: str = "football"
    competition_name: Optional[str] = None
    markets: List[ReferenceMarket] = field(default_factory=list)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FairProbabilityResult:
    """Outcome of fair probability normalization and margin removal on a reference market."""
    is_valid: bool
    raw_overround: Optional[Decimal] = None
    fair_probabilities: Dict[str, Decimal] = field(default_factory=dict)
    fair_odds: Dict[str, Decimal] = field(default_factory=dict)
    diagnostic: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ReferenceQuotaMetrics:
    """Tracks API credit usage, request counts, and cache performance."""
    total_requests: int = 0
    requests_remaining: Optional[int] = None
    requests_used: Optional[int] = None
    cache_hits: int = 0
    cache_misses: int = 0
    last_request_time: Optional[str] = None
    errors_count: int = 0
