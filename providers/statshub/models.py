"""
StatsHub Domain Models
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Union


@dataclass
class StatsHubFixture:
    """Fixture/event reference from StatsHub."""
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
class StatsHubHistoricalMatch:
    """A single historical match result for a player stat."""
    opponent: str = ""
    opponent_slug: Optional[str] = None
    opponent_id: Optional[Union[int, str]] = None
    date: str = ""
    timestamp: Optional[int] = None
    minutes_played: int = 0
    stat_value: int = 0
    home_away: str = ""
    started: bool = True
    competition: str = ""
    is_hit: Optional[bool] = None
    has_super_sub: Optional[bool] = None
    event_id: Optional[Union[int, str]] = None


@dataclass
class StatsHubBookmakerOdds:
    """Bookmaker odds for a specific player prop line."""
    bookmaker: str
    line: float
    side: str  # "over" or "under"
    decimal_odds: float
    bookmaker_id: Optional[int] = None
    metadata: Optional[Dict[str, Any]] = None


@dataclass
class StatsHubPlayerStat:
    """Individual player stat entry from StatsHub."""
    player_name: str
    team: str
    opponent: str
    fixture: StatsHubFixture
    stat_type: str
    player_id: Optional[Union[int, str]] = None
    player_slug: Optional[str] = None
    team_id: Optional[Union[int, str]] = None
    team_slug: Optional[str] = None
    opponent_team_id: Optional[Union[int, str]] = None
    opponent_team_slug: Optional[str] = None
    market_name: Optional[str] = None
    odds_type: str = "over"
    line: float = 0.5
    position: str = ""
    minutes_played: int = 0
    substituted_in: bool = False

    # Statistical data
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
    opponent_rank: Optional[int] = None
    total_ranks: Optional[int] = None
    league_average: Optional[float] = None
    opponent_average: Optional[float] = None
    p90: Optional[float] = None

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

