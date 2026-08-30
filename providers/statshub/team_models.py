"""
StatsHub Team Domain Models
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any


@dataclass
class StatsHubTeamFixture:
    fixture_id: str
    home_team: str
    away_team: str
    competition: str = ""
    kickoff: Optional[str] = None
    venue: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


@dataclass
class StatsHubTeamHistoricalMatch:
    opponent: str = ""
    date: str = ""
    stat_value: int = 0
    venue: str = ""  # 'H', 'A', 'N'
    home_away: str = ""
    competition: str = ""
    team_score: Optional[int] = None
    opponent_score: Optional[int] = None

    def __post_init__(self):
        if self.venue and not self.home_away:
            self.home_away = self.venue
        elif self.home_away and not self.venue:
            self.venue = self.home_away


@dataclass
class StatsHubTeamBookmakerOdds:
    bookmaker: str
    line: float
    side: str  # 'over' or 'under'
    decimal_odds: float
    metadata: Optional[Dict[str, Any]] = None


@dataclass
class StatsHubTeamStat:
    team_name: str
    opponent_name: str
    fixture: StatsHubTeamFixture
    stat_type: str
    participant_role: str = "HOME"  # 'HOME' or 'AWAY'

    stat_value: int = 0
    average: float = 0.0
    hit_rate_count: int = 0
    sample_size: int = 0
    hit_rate_pct: float = 0.0

    last_5_avg: Optional[float] = None
    last_10_avg: Optional[float] = None
    last_15_avg: Optional[float] = None

    historical_matches: List[StatsHubTeamHistoricalMatch] = field(default_factory=list)
    bookmaker_odds: List[StatsHubTeamBookmakerOdds] = field(default_factory=list)
    raw_data: Optional[Dict[str, Any]] = None


@dataclass
class StatsHubTeamPropResult:
    team_stat: StatsHubTeamStat
    available_lines: List[float] = field(default_factory=list)
    best_odds_by_line: Dict[str, StatsHubTeamBookmakerOdds] = field(default_factory=dict)
    total_bookmaker_count: int = 0
