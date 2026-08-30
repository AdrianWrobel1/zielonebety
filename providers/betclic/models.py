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
