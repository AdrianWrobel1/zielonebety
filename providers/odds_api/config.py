"""
Odds API.io Configuration
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple
from providers.odds_api.constants import (
    DEFAULT_BASE_URL,
    DEFAULT_SPORT,
    SUPPORTED_BOOKMAKERS,
    DEFAULT_TIMEOUT_SECONDS,
    DEFAULT_RETRY_LIMIT,
    DEFAULT_RATE_LIMIT_PER_SEC,
    DEFAULT_CACHE_TTL_SECONDS,
)


@dataclass
class OddsApiConfig:
    """Configuration options for Odds API.io provider."""
    api_key: Optional[str] = None
    base_url: str = DEFAULT_BASE_URL
    sport: str = DEFAULT_SPORT
    bookmakers: Tuple[str, ...] = SUPPORTED_BOOKMAKERS  # ('Bet365', 'Unibet')
    request_timeout: float = DEFAULT_TIMEOUT_SECONDS
    max_retries: int = DEFAULT_RETRY_LIMIT
    rate_limit_per_sec: float = DEFAULT_RATE_LIMIT_PER_SEC
    cache_ttl_seconds: int = DEFAULT_CACHE_TTL_SECONDS
    enabled: bool = True
    event_limit: int = 50
    selected_event_ids: List[str] = field(default_factory=list)
    preferred_competitions: Sequence[str] = field(default_factory=tuple)

    def __post_init__(self):
        if not self.api_key:
            self.api_key = (
                os.environ.get("Odds-api_API")
                or os.environ.get("ODDS_API_IO_KEY")
                or os.environ.get("ODDS_API_KEY")
            )
        if not self.api_key:
            from pathlib import Path
            for env_candidate in (Path(".env"), Path(__file__).resolve().parents[2] / ".env"):
                if env_candidate.exists():
                    try:
                        with open(env_candidate, "r", encoding="utf-8") as f:
                            for line in f:
                                line = line.strip()
                                if line and not line.startswith("#") and "=" in line:
                                    k, v = line.split("=", 1)
                                    k_clean = k.strip()
                                    v_clean = v.strip()
                                    if k_clean in ("Odds-api_API", "ODDS_API_IO_KEY", "ODDS_API_KEY") and v_clean:
                                        self.api_key = v_clean
                                        os.environ[k_clean] = v_clean
                                        break
                    except Exception:
                        pass
                if self.api_key:
                    break

        if not self.api_key:
            self.enabled = False

        # Validate and enforce positive integer event_limit (default 50)
        if self.event_limit is None:
            self.event_limit = 50
        elif isinstance(self.event_limit, int) and self.event_limit > 0:
            pass
        else:
            try:
                parsed_val = int(self.event_limit)
                self.event_limit = parsed_val if parsed_val > 0 else 50
            except (ValueError, TypeError):
                self.event_limit = 50

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "OddsApiConfig":
        raw_limit = data.get("event_limit")
        parsed_limit = 50
        if raw_limit is not None:
            try:
                candidate = int(raw_limit)
                if candidate > 0:
                    parsed_limit = candidate
            except (ValueError, TypeError):
                parsed_limit = 50

        return cls(
            api_key=data.get("api_key"),
            base_url=data.get("base_url", DEFAULT_BASE_URL),
            sport=data.get("sport", DEFAULT_SPORT),
            bookmakers=tuple(data.get("bookmakers", SUPPORTED_BOOKMAKERS)),
            request_timeout=float(data.get("request_timeout", DEFAULT_TIMEOUT_SECONDS)),
            max_retries=int(data.get("max_retries", DEFAULT_RETRY_LIMIT)),
            rate_limit_per_sec=float(data.get("rate_limit_per_sec", DEFAULT_RATE_LIMIT_PER_SEC)),
            cache_ttl_seconds=int(data.get("cache_ttl_seconds", DEFAULT_CACHE_TTL_SECONDS)),
            enabled=bool(data.get("enabled", True)),
            event_limit=parsed_limit,
            selected_event_ids=list(data.get("selected_event_ids", [])),
            preferred_competitions=tuple(data.get("preferred_competitions", ())),
        )
