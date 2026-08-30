"""
Odds API.io Data Models
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class OddsApiDiscoveredItem:
    """Discovered event item from Odds API.io."""
    provider_event_id: str
    name: str
    home_team: str
    away_team: str
    competition_name: str
    start_time: str
    sport_slug: str = "football"
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class OddsApiOdds:
    """Individual odds quotation."""
    decimal_odds: float
    is_active: bool = True
    timestamp: Optional[str] = None


@dataclass
class OddsApiSelection:
    """Selection in a market."""
    provider_selection_id: str
    name: str
    type_code: str
    odds: Optional[OddsApiOdds] = None
    handicap: Optional[float] = None
    participant: Optional[str] = None


@dataclass
class OddsApiMarket:
    """Market returned by a specific bookmaker."""
    provider_market_id: str
    name: str
    market_type_code: str
    is_open: bool = True
    line: Optional[float] = None
    selections: List[OddsApiSelection] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class OddsApiEvent:
    """Event representation for a specific bookmaker (e.g. bet365 or unibet)."""
    provider_event_id: str
    bookmaker_name: str  # 'bet365' or 'unibet'
    name: str
    home_team: str
    away_team: str
    competition_name: str
    start_time: Optional[str] = None
    sport_name: str = "Football"
    markets: List[OddsApiMarket] = field(default_factory=list)
    raw_payload: Dict[str, Any] = field(default_factory=dict)
