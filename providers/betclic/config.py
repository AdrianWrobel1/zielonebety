"""
Betclic Configuration Model (Overview & Tier 2 Full Market Acquisition)
"""

from enum import Enum
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional, Sequence, Tuple
from providers.betclic.constants import (
    DEFAULT_BASE_URL,
    DEFAULT_USER_AGENT,
    DEFAULT_TIMEOUT_SECONDS,
    DEFAULT_RETRY_LIMIT,
    FOOTBALL_SPORT_ID,
    DEFAULT_BETCLIC_HEADERS,
    DEFAULT_BETCLIC_DISCOVERY_URLS,
)


class EventSelectionMode(str, Enum):
    """Event selection mode for full market detail acquisition."""
    OVERVIEW_ONLY = "OVERVIEW_ONLY"
    SELECTED = "SELECTED"
    ALL = "ALL"


@dataclass
class BetclicConfig:
    base_url: str = DEFAULT_BASE_URL
    user_agent: str = DEFAULT_USER_AGENT
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    retry_limit: int = DEFAULT_RETRY_LIMIT
    football_sport_id: int = FOOTBALL_SPORT_ID
    hours_ahead: int = 168
    max_discovered_events: int = 600
    max_pages: int = 60
    enable_dynamic_discovery: bool = True
    discovery_urls: Optional[List[str]] = None
    selection_mode: str = "SELECTED"
    selected_event_ids: List[str] = field(default_factory=list)
    max_detail_requests: int = 15
    rate_limit_per_sec: float = 5.0
    rate_limit_burst: int = 3
    rate_limit_cooldown_ms: int = 200
    detail_rate_limit_per_sec: float = 25.0
    detail_rate_limit_burst: int = 15
    detail_rate_limit_cooldown_ms: int = 50
    preferred_competitions: Sequence[str] = field(default_factory=tuple)
    use_grpc_detail: bool = True
    grpc_endpoint_url: str = "https://offering.begmedia.com/web/offering.access.api/offering.access.api.MatchService/GetMatchWithNotification"
    grpc_categories: Tuple[str, ...] = ("", "ca_ftb_rslt", "ca_ftb_goa", "ca_ftb_prp", "ca_ftb_gsc")
    tier2_grpc_categories: Tuple[str, ...] = ("", "ca_ftb_rslt", "ca_ftb_goa")
    selective_categories: bool = True
    parallel_categories: bool = True
    fallback_to_html: bool = True
    headers: Dict[str, str] = field(default_factory=lambda: dict(DEFAULT_BETCLIC_HEADERS))
    detail_workers: int = 6
    discovery_workers: int = 4
    discovery_cache_ttl_seconds: float = 120.0

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BetclicConfig":
        return cls(
            base_url=data.get("base_url", DEFAULT_BASE_URL),
            user_agent=data.get("user_agent", DEFAULT_USER_AGENT),
            timeout_seconds=float(data.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)),
            retry_limit=int(data.get("retry_limit", DEFAULT_RETRY_LIMIT)),
            football_sport_id=int(data.get("football_sport_id", FOOTBALL_SPORT_ID)),
            hours_ahead=int(data.get("hours_ahead", 168)),
            max_discovered_events=int(data.get("max_discovered_events", 600)),
            max_pages=int(data.get("max_pages", 25)),
            enable_dynamic_discovery=bool(data.get("enable_dynamic_discovery", True)),
            discovery_urls=data.get("discovery_urls"),
            selection_mode=data.get("selection_mode", "SELECTED"),
            selected_event_ids=list(data.get("selected_event_ids", [])),
            max_detail_requests=int(data.get("max_detail_requests", 15)),
            rate_limit_per_sec=float(data.get("rate_limit_per_sec", 5.0)),
            rate_limit_burst=int(data.get("rate_limit_burst", 3)),
            rate_limit_cooldown_ms=int(data.get("rate_limit_cooldown_ms", 200)),
            detail_rate_limit_per_sec=float(data.get("detail_rate_limit_per_sec", 25.0)),
            detail_rate_limit_burst=int(data.get("detail_rate_limit_burst", 15)),
            detail_rate_limit_cooldown_ms=int(data.get("detail_rate_limit_cooldown_ms", 50)),
            preferred_competitions=tuple(data.get("preferred_competitions", ())),
            use_grpc_detail=bool(data.get("use_grpc_detail", True)),
            grpc_endpoint_url=str(data.get("grpc_endpoint_url", "https://offering.begmedia.com/web/offering.access.api/offering.access.api.MatchService/GetMatchWithNotification")),
            grpc_categories=tuple(data.get("grpc_categories", ("", "ca_ftb_rslt", "ca_ftb_goa", "ca_ftb_prp", "ca_ftb_gsc"))),
            tier2_grpc_categories=tuple(data.get("tier2_grpc_categories", ("", "ca_ftb_rslt", "ca_ftb_goa"))),
            selective_categories=bool(data.get("selective_categories", True)),
            parallel_categories=bool(data.get("parallel_categories", True)),
            fallback_to_html=bool(data.get("fallback_to_html", True)),
            headers=data.get("headers") or dict(DEFAULT_BETCLIC_HEADERS),
            detail_workers=int(data.get("detail_workers", 6)),
            discovery_workers=int(data.get("discovery_workers", 4)),
            discovery_cache_ttl_seconds=float(data.get("discovery_cache_ttl_seconds", 120.0)),
        )

