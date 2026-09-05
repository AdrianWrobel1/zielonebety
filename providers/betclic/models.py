"""
Betclic Provider Raw Data Models
"""

from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any


@dataclass
class BetclicDiscoveredItem:
    provider_event_id: str
    name: str
    competition_name: str
    url: str
    start_time: str
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class BetclicOdds:
    provider_odds_id: str
    decimal_odds: float
    is_active: bool = True
    timestamp: Optional[str] = None


@dataclass
class BetclicSelection:
    provider_selection_id: str
    name: str
    type_code: str
    odds: BetclicOdds
    handicap: Optional[float] = None


@dataclass
class BetclicMarket:
    provider_market_id: str
    name: str
    market_type_code: str
    is_open: bool
    selections: List[BetclicSelection] = field(default_factory=list)


@dataclass
class BetclicEvent:
    provider_event_id: str
    name: str
    competition_name: str
    sport_name: str = "Football"
    start_time: Optional[str] = None
    home_team: Optional[str] = None
    away_team: Optional[str] = None
    markets: List[BetclicMarket] = field(default_factory=list)
    raw_payload: Dict[str, Any] = field(default_factory=dict)
    # P1-003: explicit detail-acquisition failure state. False for every
    # successful response (including legitimate zero-market responses).
    fetch_failed: bool = False
    fetch_error: Optional[str] = None
    fetch_error_type: Optional[str] = None
    # P1-NEW-010: Tier-1 overview placeholder that was never selected for
    # detail acquisition. Market state unknown; distinct from both a
    # legitimate zero-market detail response and a FETCH_FAILED event.
    overview_only: bool = False
