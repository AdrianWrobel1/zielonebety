"""
Superbet Domain Models
"""

from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any


@dataclass
class SuperbetDiscoveredItem:
    """Discovered Superbet match reference."""
    event_id: str
    match_name: str
    competition_id: Optional[str] = None
    competition_name: Optional[str] = None
    start_time: Optional[str] = None
    url: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


@dataclass
class SuperbetOdds:
    """Superbet decimal odds value."""
    decimal_odds: float
    raw_odds_text: Optional[str] = None


@dataclass
class SuperbetSelection:
    """Superbet market selection choice."""
    selection_id: str
    name: str
    odds: SuperbetOdds
    outcome_id: Optional[str] = None
    specifiers: Optional[Dict[str, Any]] = None
    special_bet_value: Optional[str] = None
    is_active: bool = True
    raw_metadata: Optional[Dict[str, Any]] = None


@dataclass
class SuperbetMarket:
    """Superbet betting market."""
    market_id: str
    name: str
    market_type_id: Optional[str] = None
    specifiers: Optional[Dict[str, Any]] = None
    selections: List[SuperbetSelection] = field(default_factory=list)
    is_active: bool = True
    raw_metadata: Optional[Dict[str, Any]] = None


@dataclass
class SuperbetEvent:
    """Superbet parsed event domain model."""
    event_id: str
    name: str
    home_team: str
    away_team: str
    sport_name: str = "Football"
    competition_name: Optional[str] = None
    start_time: Optional[str] = None
    betradar_id: Optional[str] = None
    home_team_id: Optional[str] = None
    away_team_id: Optional[str] = None
    tournament_id: Optional[str] = None
    category_id: Optional[str] = None
    markets: List[SuperbetMarket] = field(default_factory=list)
    raw_metadata: Optional[Dict[str, Any]] = None
