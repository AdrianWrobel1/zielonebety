"""
StatsHub Domain Models
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any


@dataclass
class StatsHubFixture:
    """Fixture/event reference from StatsHub."""
    fixture_id: str
    home_team: str
    away_team: str
    competition: str = ""
    kickoff: Optional[str] = None
    venue: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


@dataclass
class StatsHubHistoricalMatch:
    """A single historical match result for a player stat."""
    opponent: str = ""
    date: str = ""
    minutes_played: int = 0
    stat_value: int = 0
    home_away: str = ""
    started: bool = True
    competition: str = ""


@dataclass
class StatsHubBookmakerOdds:
    """Bookmaker odds for a specific player prop line."""
    bookmaker: str
    line: float
    side: str  # "over" or "under"
    decimal_odds: float
    metadata: Optional[Dict[str, Any]] = None


@dataclass
class StatsHubPlayerStat:
    """Individual player stat entry from StatsHub."""
    player_name: str
    team: str
    opponent: str
    fixture: StatsHubFixture
    stat_type: str
    position: str = ""
    minutes_played: int = 0
    substituted_in: bool = False

    # Statistical data
    stat_value: int = 0
    average: float = 0.0
    hit_rate_count: int = 0
    sample_size: int = 0
    hit_rate_pct: float = 0.0

    # Recent form splits
    last_5_avg: Optional[float] = None
    last_10_avg: Optional[float] = None
    last_15_avg: Optional[float] = None

    # Historical match data
    historical_matches: List[StatsHubHistoricalMatch] = field(default_factory=list)

    # Bookmaker odds (per line)
    bookmaker_odds: List[StatsHubBookmakerOdds] = field(default_factory=list)

    # Raw data
    raw_data: Optional[Dict[str, Any]] = None


@dataclass
class StatsHubPropResult:
    """Aggregated prop result combining stat, player, fixture, and odds."""
    player_stat: StatsHubPlayerStat
    available_lines: List[float] = field(default_factory=list)
    best_odds_by_line: Dict[str, StatsHubBookmakerOdds] = field(default_factory=dict)
    total_bookmaker_count: int = 0
