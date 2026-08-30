"""
StatsHub Provider Configuration
"""

import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from providers.statshub.constants import (
    STATSHUB_BASE_URL,
    DEFAULT_STATSHUB_HEADERS,
)


@dataclass
class StatsHubConfig:
    """Configuration options for StatsHub provider."""

    # Core
    base_url: str = STATSHUB_BASE_URL
    enabled: bool = field(default_factory=lambda: os.environ.get("STATSHUB_ENABLED", "true").lower() in ("true", "1", "yes"))
    request_timeout: float = 20.0
    max_retries: int = 3
    headers: Dict[str, str] = field(default_factory=lambda: DEFAULT_STATSHUB_HEADERS.copy())

    # Query parameters — stat filter
    stat: str = "shots"
    positions: str = "D,M,F"
    last_games: int = 10

    # Query parameters — date range (unix timestamps, None = today)
    start_of_day: Optional[int] = None
    end_of_day: Optional[int] = None
    days_ahead: int = field(default_factory=lambda: int(os.environ.get("STATSHUB_DAYS_AHEAD", "7")))

    # Query parameters — tournaments/fixtures
    tournaments: str = ""
    fixture_ids: str = ""

    # Query parameters — filters
    min_minutes_played: int = 0
    venue_filter: str = "both"
    selected_leagues_only: bool = False
    started_match: bool = True
    super_sub: bool = False
    hit_rate_threshold: int = 50
    stat_threshold: int = 1
    min_games_played: int = 0
    use_min_last_game_with_team: bool = False
    min_last_game_with_team: int = 0
    lineup_filter_type: str = "all"
    average_threshold: float = 0
    average_comparison: str = "above"
    show_trend: bool = False

    # Query parameters — sort/pagination
    sort_by: str = "default"
    sort_direction: str = "desc"
    page: int = 1
    limit: int = 50

    # UI-level minimum odds filter (applied post-fetch)
    min_odds: float = 1.0

    # Multi-page acquisition & safety limits
    auto_paginate: bool = True
    max_pages: int = field(default_factory=lambda: int(os.environ.get("STATSHUB_MAX_PAGES", "20")))
    max_prop_results: int = field(default_factory=lambda: int(os.environ.get("STATSHUB_MAX_PROP_RESULTS", "500")))

    # Rate limiting
    rate_limit_per_sec: float = 2.0
    rate_limit_burst: int = 3
    rate_limit_cooldown_ms: int = 250

    def build_query_params(self) -> Dict[str, str]:
        """Build URL query parameters for the StatsHub API request."""
        params: Dict[str, str] = {
            "stat": self.stat,
            "lastGames": str(self.last_games),
            "positions": self.positions,
            "minMinutesPlayed": str(self.min_minutes_played),
            "venueFilter": self.venue_filter,
            "selectedLeaguesOnly": str(self.selected_leagues_only).lower(),
            "startedMatch": str(self.started_match).lower(),
            "superSub": str(self.super_sub).lower(),
            "hitRateThreshold": str(self.hit_rate_threshold),
            "statThreshold": str(self.stat_threshold),
            "minGamesPlayed": str(self.min_games_played),
            "useMinLastGameWithTeam": str(self.use_min_last_game_with_team).lower(),
            "minLastGameWithTeam": str(self.min_last_game_with_team),
            "lineupFilterType": self.lineup_filter_type,
            "averageThreshold": str(self.average_threshold),
            "averageComparison": self.average_comparison,
            "showTrend": str(self.show_trend).lower(),
            "sortBy": self.sort_by,
            "sortDirection": self.sort_direction,
            "page": str(self.page),
            "limit": str(self.limit),
        }

        if self.start_of_day is not None:
            params["startOfDay"] = str(self.start_of_day)
        if self.end_of_day is not None:
            params["endOfDay"] = str(self.end_of_day)
        if self.tournaments:
            params["tournaments"] = self.tournaments
        if self.fixture_ids:
            params["fixtureIds"] = self.fixture_ids

        return params
