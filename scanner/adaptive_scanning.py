"""
Stage B.2: Adaptive Scanning Engine

Implements dynamic scan intervals and adaptive priority based on:
1. Kickoff Proximity Phasing (T-60m, T-2h, T-6h, T-24h, post-kickoff STOP).
2. Next-Day Evening Discovery Window (19:00 - 23:00) for props market availability.
3. Provider / API Budget Ceilings (StatsHub, Superbet, Betclic request safety).
4. Composite Fixture Priority scoring incorporating tier, market coverage, and kickoff proximity.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone, timedelta, time
import logging
from typing import Any, Dict, List, Optional, Sequence, Tuple

from normalization.identity import parse_kickoff_to_utc
from scanner.global_props_scanner import get_tier_weight

logger = logging.getLogger("zielonebety.scanner.adaptive")


try:
    from zoneinfo import ZoneInfo
    WARSAW_TZ = ZoneInfo("Europe/Warsaw")
except Exception:
    WARSAW_TZ = timezone(timedelta(hours=2), name="Europe/Warsaw")


@dataclass(frozen=True)
class AdaptiveScanDecision:
    """Decision output for an adaptive scan evaluation cycle."""
    phase: str  # "POST_KICKOFF", "T_MINUS_60M", "T_MINUS_2H", "T_MINUS_6H", "T_MINUS_24H", "T_PLUS_24H", "IDLE"
    next_interval_minutes: int
    active_fixtures_count: int
    is_idle: bool
    is_evening_discovery_window: bool
    should_trigger_evening_digest: bool
    is_budget_throttled: bool
    closest_kickoff_minutes: Optional[float]
    recommended_scope: str = "ALL"  # "ALL" or "POPULAR" when throttled
    is_high_activity_window: bool = False


def calculate_adaptive_fixture_priority(
    tier: int,
    market_coverage: int,
    kickoff: Optional[str] = None,
    now: Optional[datetime] = None,
) -> float:
    """Calculates deterministic composite fixture priority incorporating league tier,

    market coverage, and kickoff proximity.

    Guarantees:
    - Imminent matches (T-60m, T-2h) receive proximity multiplier boost.
    - Matches past kickoff drop to 0 priority.
    - Popular competitions have priority (via tier weight), but lower tiers with
      high market coverage and imminent kickoff remain eligible (no artificial whitelist).
    """
    base_tier_weight = get_tier_weight(tier)
    curr_time = now or datetime.now(timezone.utc)

    proximity_multiplier = 1.0
    if kickoff:
        dt = parse_kickoff_to_utc(kickoff)
        if dt:
            diff_seconds = (dt - curr_time).total_seconds()
            if diff_seconds <= 0:
                return 0.0  # Post-kickoff -> stop pre-match scanning
            diff_hours = diff_seconds / 3600.0

            if diff_hours <= 1.0:
                proximity_multiplier = 2.00  # T-60m very high priority
            elif diff_hours <= 2.0:
                proximity_multiplier = 1.75  # T-2h high priority
            elif diff_hours <= 6.0:
                proximity_multiplier = 1.40  # T-6h medium-high
            elif diff_hours <= 12.0:
                proximity_multiplier = 1.20  # T-12h medium
            elif diff_hours <= 24.0:
                proximity_multiplier = 1.00  # T-24h discovery
            else:
                proximity_multiplier = 0.80  # Far future

    base_score = float(market_coverage) * base_tier_weight
    return round(base_score * proximity_multiplier, 4)


class AdaptiveScanningEngine:
    """Evaluates temporal, budget, and market conditions to determine optimal scan cadence.

    Business Timing Guarantees:
    - All business scheduling hours use Europe/Warsaw with DST awareness.
    - High-activity window: 16:00–22:00 Europe/Warsaw (higher readiness, line monitoring).
    - Next-day discovery: discovers and refreshes upcoming matches for tomorrow during 16:00–22:00.
    - Quiet hours / IDLE: relaxes scan cadence (90m+) to conserve provider budgets.
    """

    def __init__(
        self,
        high_activity_start_hour: int = 16,
        high_activity_end_hour: int = 22,
        timezone_name: str = "Europe/Warsaw",
        evening_window_start_hour: Optional[int] = None,
        evening_window_end_hour: Optional[int] = None,
    ) -> None:
        self.high_activity_start_hour = (
            evening_window_start_hour if evening_window_start_hour is not None else high_activity_start_hour
        )
        self.high_activity_end_hour = (
            evening_window_end_hour if evening_window_end_hour is not None else high_activity_end_hour
        )
        self.timezone_name = timezone_name
        try:
            from zoneinfo import ZoneInfo
            self.tz = ZoneInfo(timezone_name)
        except Exception:
            self.tz = WARSAW_TZ

    def evaluate_scan_cycle(
        self,
        current_time: Optional[datetime] = None,
        upcoming_kickoffs: Optional[Sequence[str]] = None,
        last_scan_at: Optional[str] = None,
        hourly_requests_used: int = 0,
        hourly_budget_ceiling: int = 100,
    ) -> AdaptiveScanDecision:
        """Determines the next scan interval and phase based on upcoming fixtures and budgets."""
        now = current_time or datetime.now(timezone.utc)
        if now.tzinfo is None:
            now_utc = now.replace(tzinfo=timezone.utc)
        else:
            now_utc = now.astimezone(timezone.utc)
        now_local = now_utc.astimezone(self.tz)

        # 1. Parse and filter pre-match kickoffs
        future_diffs_min: List[float] = []
        has_next_day_matches = False
        tomorrow_local_date = (now_local + timedelta(days=1)).date()

        if upcoming_kickoffs:
            for k_str in upcoming_kickoffs:
                if not k_str:
                    continue
                dt_utc = parse_kickoff_to_utc(k_str)
                if not dt_utc:
                    continue
                diff_sec = (dt_utc - now_utc).total_seconds()
                if diff_sec > 0:
                    diff_min = diff_sec / 60.0
                    future_diffs_min.append(diff_min)
                    dt_local = dt_utc.astimezone(self.tz)
                    if dt_local.date() == tomorrow_local_date:
                        has_next_day_matches = True

        future_diffs_min.sort()
        active_count = len(future_diffs_min)
        closest_min = future_diffs_min[0] if future_diffs_min else None

        # 2. Check High-Activity Window (16:00 - 22:00 Europe/Warsaw) & Next-Day Discovery
        start_t = time(self.high_activity_start_hour, 0)
        end_t = time(self.high_activity_end_hour, 0)
        is_high_activity = start_t <= now_local.time() < end_t
        is_next_day_discovery = is_high_activity and has_next_day_matches
        should_trigger_digest = is_next_day_discovery

        # 3. Check Budget Exhaustion / Throttling
        is_budget_throttled = False
        if hourly_budget_ceiling > 0 and (hourly_requests_used / float(hourly_budget_ceiling)) >= 0.90:
            is_budget_throttled = True

        # 4. Determine Phase & Base Interval
        if active_count == 0:
            phase = "IDLE"
            interval = 90
            is_idle = True
        elif closest_min is not None and closest_min <= 60.0:
            phase = "T_MINUS_60M"
            interval = 15  # Frequent polling as kickoff approaches and lines move
            is_idle = False
        elif closest_min is not None and closest_min <= 120.0:
            phase = "T_MINUS_2H"
            interval = 30
            is_idle = False
        elif closest_min is not None and closest_min <= 360.0:
            phase = "T_MINUS_6H"
            interval = 35 if is_high_activity else 45
            is_idle = False
        elif closest_min is not None and closest_min <= 1440.0:
            phase = "T_MINUS_24H"
            interval = 35 if is_high_activity else 90
            is_idle = False
        else:
            phase = "T_PLUS_24H"
            interval = 35 if is_high_activity else 120
            is_idle = False

        # 5. Next-Day Discovery / High-Activity Window Override
        if is_next_day_discovery or (is_high_activity and active_count > 0):
            # During high activity window (16:00-22:00 Europe/Warsaw), tighten interval to discover newly posted props
            interval = min(interval, 35)

        # 6. Budget Throttling Override
        if is_budget_throttled:
            interval = max(interval, 45)

        recommended_scope = "POPULAR" if is_budget_throttled else "ALL"

        return AdaptiveScanDecision(
            phase=phase,
            next_interval_minutes=interval,
            active_fixtures_count=active_count,
            is_idle=is_idle,
            is_evening_discovery_window=is_next_day_discovery,
            should_trigger_evening_digest=should_trigger_digest,
            is_budget_throttled=is_budget_throttled,
            closest_kickoff_minutes=round(closest_min, 1) if closest_min is not None else None,
            recommended_scope=recommended_scope,
            is_high_activity_window=is_high_activity,
        )
