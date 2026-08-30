"""
Configuration for Valuebet Detection Engine
"""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional, Sequence, Tuple


@dataclass(frozen=True)
class ValuebetConfig:
    """Configurable parameters for valuebet calculation and threshold qualification."""
    min_value_percent: Decimal = Decimal("3.0")
    max_freshness_seconds: int = 1800  # 30 minutes
    reference_source: str = "the_odds_api"
    preferred_bookmakers: Tuple[str, ...] = ("pinnacle", "betfair_ex_uk", "betonlineag", "lowvig")
    strict_event_matching: bool = True
    min_event_match_score: float = 0.80
    enable_negative_value_candidates: bool = False  # If True, candidate records with EV <= 0 are retained for diagnostics
