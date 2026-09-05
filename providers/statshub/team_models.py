"""
StatsHub Team Domain Models
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Union


@dataclass
class StatsHubTeamFixture:
    fixture_id: str
    home_team: str
    away_team: str
    competition: str = ""
    kickoff: Optional[str] = None
    venue: Optional[str] = None
    event_internal_id: Optional[Union[int, str]] = None
    slug: Optional[str] = None
    home_team_slug: Optional[str] = None
    away_team_slug: Optional[str] = None
    home_team_id: Optional[Union[int, str]] = None
    away_team_id: Optional[Union[int, str]] = None
    tournament_id: Optional[Union[int, str]] = None
    unique_tournament_id: Optional[Union[int, str]] = None
    deep_link: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None

    def get_fixture_url(self) -> Optional[str]:
        """Deterministically generates the StatsHub fixture URL."""
        if self.deep_link:
            return self.deep_link
        if self.event_internal_id:
            slug_str = self.slug
            if not slug_str and self.home_team_slug and self.away_team_slug:
                slug_str = f"{self.home_team_slug}-vs-{self.away_team_slug}"
            elif not slug_str and self.home_team and self.away_team:
                h_clean = self.home_team.strip().lower().replace(" ", "-")
                a_clean = self.away_team.strip().lower().replace(" ", "-")
                slug_str = f"{h_clean}-vs-{a_clean}"
            slug_str = slug_str or "fixture"
            return f"https://www.statshub.com/fixture/{slug_str}/{self.event_internal_id}"
        return None



@dataclass
class StatsHubTeamHistoricalMatch:
    opponent: str = ""
    opponent_slug: Optional[str] = None
    opponent_id: Optional[Union[int, str]] = None
    date: str = ""
    timestamp: Optional[int] = None
    stat_value: int = 0
    venue: str = ""  # 'H', 'A', 'N'
    home_away: str = ""
    competition: str = ""
    team_score: Optional[int] = None
    opponent_score: Optional[int] = None
    is_hit: Optional[bool] = None
    event_id: Optional[Union[int, str]] = None

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
    bookmaker_id: Optional[int] = None
    metadata: Optional[Dict[str, Any]] = None


@dataclass
class StatsHubTeamStat:
    team_name: str
    opponent_name: str
    fixture: StatsHubTeamFixture
    stat_type: str
    team_id: Optional[Union[int, str]] = None
    team_slug: Optional[str] = None
    opponent_team_id: Optional[Union[int, str]] = None
    opponent_team_slug: Optional[str] = None
    stat_display: Optional[str] = None
    odds_type: str = "over"
    line: float = 0.5
    participant_role: str = "HOME"  # 'HOME' or 'AWAY'

    stat_value: int = 0
    average: float = 0.0
    hit_rate_count: int = 0
    sample_size: int = 0
    hit_rate_pct: float = 0.0

    # Trend metadata (from Trends API)
    trend_hits: Optional[int] = None
    trend_window: Optional[int] = None
    trend_total: Optional[int] = None
    trend_avg: Optional[float] = None
    opponent_hit_rate: Optional[float] = None
    league_name: Optional[str] = None

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

