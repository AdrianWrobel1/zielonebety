"""
Superbet Configuration Model
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Optional, Any, List, Sequence
from providers.superbet.constants import (
    SUPERBET_BASE_URL,
    SUPERBET_DETAIL_BASE_URL,
    SUPERBET_FOOTBALL_SPORT_ID,
    DEFAULT_SUPERBET_HEADERS,
)


class EventSelectionMode(str, Enum):
    """Event selection mode for Superbet full market acquisition."""
    OVERVIEW_ONLY = "OVERVIEW_ONLY"  # Tier 1 fast overview (1X2 only)
    SELECTED = "SELECTED"            # Tier 2 detail for selected_event_ids
    ALL = "ALL"                      # Tier 2 detail for all discovered events


@dataclass
class SuperbetConfig:
    """Configuration options for Superbet provider."""
    base_url: str = SUPERBET_BASE_URL
    detail_base_url: str = SUPERBET_DETAIL_BASE_URL
    sport_id: int = SUPERBET_FOOTBALL_SPORT_ID
    active_index: str = "active-prematch"
    hours_ahead: int = 168
    request_timeout: float = 15.0
    max_retries: int = 3
    headers: Dict[str, str] = field(default_factory=lambda: DEFAULT_SUPERBET_HEADERS.copy())

    # Selection policy
    selection_mode: str = EventSelectionMode.OVERVIEW_ONLY.value
    selected_event_ids: List[str] = field(default_factory=list)
    max_detail_requests: int = 15
    preferred_competitions: Sequence[str] = field(default_factory=tuple)

    # Rate limiting
    rate_limit_per_sec: float = 5.0
    rate_limit_burst: int = 10
    rate_limit_cooldown_ms: int = 100
    detail_rate_limit_per_sec: float = 25.0
    detail_rate_limit_burst: int = 15
    detail_rate_limit_cooldown_ms: int = 50
    rate_limiter: Optional[Any] = None

    # Transport & Concurrency
    session_manager: Optional[Any] = None
    detail_workers: int = 6
