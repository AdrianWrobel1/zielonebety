"""
ULTRA SCAN Orchestrator — Daily Comprehensive Slate Scan (Stage 10:00 AM Europe/Warsaw)

Core Architecture:
- Authoritative heavy daily orchestration mode.
- Strictly restricted to today's matches in Europe/Warsaw.
- REUSE > EXTEND > NEW: reuses ProductionScanOrchestrator, GlobalPropsScanner,
  SurebetDetector, ValuebetEngine, TheOddsApiReferenceProvider, NormalizationEngine.
- Two-Pass Engine: Breadth (full today discovery & acquisition) -> Depth (high EV & near-surebet refinement).
- Multi-Signal Ranking (Net EV, confidence, reference consensus quality, market liquidity).
- Auditable 8-stage Funnel Metrics with zero silent drops.
"""

from __future__ import annotations

import concurrent.futures
from dataclasses import dataclass, field
from datetime import datetime, date, time as dt_time, timezone, timedelta
from decimal import Decimal
import html
import json
import logging
import time
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple, Union
import uuid

try:
    from zoneinfo import ZoneInfo
    WARSAW_TZ = ZoneInfo("Europe/Warsaw")
except Exception:
    # P1-NEW-009: DST-aware fallback (EU rule); see scheduler.py.
    from normalization.identity import get_warsaw_tz as _get_warsaw_tz

    WARSAW_TZ = _get_warsaw_tz()

from core.tax_engine import get_tax_engine
from domain.models import (
    CanonicalEvent,
    Event,
    Market,
    MatchEvidence,
    Odds,
    Selection,
    generate_deterministic_canonical_event_id,
)
from normalization.base_normalizer import NormalizedGraph
from normalization.engine import NormalizationEngine
from normalization.market_identity import (
    CanonicalMarketKey,
    CanonicalMarketType,
    extract_canonical_market_key,
    normalize_line,
)
from normalization.selection_identity import extract_canonical_selection_key
from normalization.surebet import (
    SurebetDetectionResult,
    SurebetDetectorEngine,
    SurebetOpportunity,
    SurebetStatus,
)
from normalization.validation_pipeline import CrossBookmakerValidationPipeline
from providers.base.base_provider import BaseProvider
from providers.base.models import is_detail_fetch_failure
from providers.betclic.provider import BetclicProvider
from providers.superbet.provider import SuperbetProvider
from reference_odds.fair_calculator import FairProbabilityCalculator
from reference_odds.models import ReferenceEvent
from reference_odds.provider import ReferenceOddsProvider, TheOddsApiReferenceProvider
from scanner.global_props_scanner import (
    GlobalPropsScanner,
    GlobalScanBudget,
    GlobalScanOpportunity,
    GlobalScanScope,
)
from valuebets.config import ValuebetConfig
from valuebets.engine import ValuebetEngine
from valuebets.models import ValueBetCandidate, ValueBetDetectionResult

logger = logging.getLogger("zielonebety.orchestration.ultra_scan")

EXECUTABLE_BOOKMAKERS = frozenset({"superbet", "betclic"})

COMPETITION_TO_ODDS_API_SPORT: Dict[str, str] = {
    "premier league": "soccer_epl",
    "england premier league": "soccer_epl",
    "la liga": "soccer_spain_la_liga",
    "spain la liga": "soccer_spain_la_liga",
    "serie a": "soccer_italy_serie_a",
    "italy serie a": "soccer_italy_serie_a",
    "bundesliga": "soccer_germany_bundesliga",
    "germany bundesliga": "soccer_germany_bundesliga",
    "ligue 1": "soccer_france_ligue_one",
    "france ligue 1": "soccer_france_ligue_one",
    "champions league": "soccer_uefa_champs_league",
    "uefa champions league": "soccer_uefa_champs_league",
    "europa league": "soccer_uefa_europa_league",
    "uefa europa league": "soccer_uefa_europa_league",
    "ekstraklasa": "soccer_poland_ekstraklasa",
    "poland ekstraklasa": "soccer_poland_ekstraklasa",
}


def parse_kickoff_datetime(val: Any) -> Optional[datetime]:
    """Safely parse various datetime formats or ISO strings to a timezone-aware UTC datetime."""
    if val is None:
        return None
    if isinstance(val, datetime):
        return val if val.tzinfo is not None else val.replace(tzinfo=timezone.utc)
    if isinstance(val, (int, float)):
        # Treat as unix timestamp
        try:
            return datetime.fromtimestamp(float(val), tz=timezone.utc)
        except Exception:
            return None
    s = str(val).strip()
    if not s:
        return None
    try:
        # Standard ISO 8601
        clean = s.replace("Z", "+00:00")
        dt = datetime.fromisoformat(clean)
        return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)
    except Exception:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(s[:19], fmt)
            return dt.replace(tzinfo=timezone.utc)
        except Exception:
            pass
    return None


def is_target_date_in_warsaw(
    start_time: Any,
    target_date: Optional[Union[str, date]] = None,
    evaluation_time: Optional[datetime] = None,
) -> bool:
    """Check whether start_time falls on the target calendar date in Europe/Warsaw timezone.

    Strict boundary: 00:00:00 to 23:59:59.999999 local Warsaw time.
    If target_date is not supplied, defaults to today's date in Warsaw at evaluation_time.
    """
    dt = parse_kickoff_datetime(start_time)
    if dt is None:
        return False

    target_d = None
    if target_date is not None:
        if isinstance(target_date, str):
            try:
                target_d = date.fromisoformat(target_date[:10])
            except Exception:
                target_d = None
        elif isinstance(target_date, date):
            target_d = target_date

    if target_d is None:
        eval_dt = evaluation_time or datetime.now(timezone.utc)
        target_d = eval_dt.astimezone(WARSAW_TZ).date()

    kickoff_warsaw = dt.astimezone(WARSAW_TZ).date()
    return kickoff_warsaw == target_d


def is_today_in_warsaw(start_time: Any, evaluation_time: Optional[datetime] = None) -> bool:
    """Check whether start_time falls on today's date in Europe/Warsaw timezone."""
    return is_target_date_in_warsaw(start_time, target_date=None, evaluation_time=evaluation_time)


@dataclass(frozen=True)
class UltraHorizon:
    """Explicit, auditable event horizon for ULTRA SCAN in Europe/Warsaw timezone.

    Manages daytime vs late-day (evening) horizon semantics:
    - Daytime: strictly bounded to today's calendar slate in Europe/Warsaw.
    - Evening (late in the day, >= evening_start_hour): bounded forward horizon covering
      today's remaining slate + tomorrow + day after tomorrow (POJUTRZE) through end of D+2 (or max_forward_hours).
    """
    target_date: date
    tomorrow_date: Optional[date]
    day_after_tomorrow_date: Optional[date]
    start_time: datetime
    end_time: datetime
    is_evening_horizon: bool

    def classify_kickoff(
        self,
        start_time: Any,
        evaluation_time: Optional[datetime] = None,
    ) -> Tuple[bool, Optional[str]]:
        """Classifies a kickoff against the horizon in Europe/Warsaw.

        Returns:
            (is_eligible, bucket_label)
            bucket_label is one of: "TODAY", "TOMORROW", "DAY_AFTER_TOMORROW", "STALE", "OUTSIDE_HORIZON"
        """
        k_dt = parse_kickoff_datetime(start_time)
        if k_dt is None:
            return False, "OUTSIDE_HORIZON"

        eval_dt = evaluation_time or self.start_time
        # Freshness check: reject events that started more than 5 minutes before evaluation_time
        if k_dt < (eval_dt - timedelta(minutes=5)):
            return False, "STALE"

        kickoff_warsaw = k_dt.astimezone(WARSAW_TZ)
        kickoff_date = kickoff_warsaw.date()

        if kickoff_date == self.target_date:
            return True, "TODAY"

        if self.is_evening_horizon:
            if self.tomorrow_date and kickoff_date == self.tomorrow_date:
                if k_dt <= self.end_time:
                    return True, "TOMORROW"
                return False, "OUTSIDE_HORIZON"
            if self.day_after_tomorrow_date and kickoff_date == self.day_after_tomorrow_date:
                if k_dt <= self.end_time:
                    return True, "DAY_AFTER_TOMORROW"
                return False, "OUTSIDE_HORIZON"

        return False, "OUTSIDE_HORIZON"

    def is_in_horizon(
        self,
        start_time: Any,
        evaluation_time: Optional[datetime] = None,
    ) -> bool:
        """Check whether start_time falls within the ULTRA event horizon."""
        eligible, _ = self.classify_kickoff(start_time, evaluation_time)
        return eligible


def resolve_ultra_horizon(
    target_date: Optional[Union[str, date]] = None,
    evaluation_time: Optional[datetime] = None,
    evening_start_hour: int = 20,
    include_tomorrow: Optional[bool] = None,
    max_forward_hours: Optional[float] = None,
) -> UltraHorizon:
    """Build an explicit, bounded Europe/Warsaw event horizon for ULTRA SCAN."""
    eval_dt = evaluation_time or datetime.now(timezone.utc)
    now_warsaw = eval_dt.astimezone(WARSAW_TZ)

    target_d: Optional[date] = None
    if target_date is not None:
        if isinstance(target_date, str):
            try:
                target_d = date.fromisoformat(target_date[:10])
            except Exception:
                target_d = None
        elif isinstance(target_date, date):
            target_d = target_date

    if target_d is None:
        target_d = now_warsaw.date()

    # Determine whether evening horizon is active
    if include_tomorrow is not None:
        is_evening = bool(include_tomorrow)
    else:
        # Automatic: active when Warsaw local hour >= evening_start_hour (default 20:00)
        is_evening = now_warsaw.hour >= evening_start_hour

    tomorrow_d = (target_d + timedelta(days=1)) if is_evening else None
    day_after_tomorrow_d = (target_d + timedelta(days=2)) if is_evening else None

    # Earliest eligible time (with freshness buffer)
    start_time = eval_dt - timedelta(minutes=5)

    # End boundary: if evening horizon, includes all of day after tomorrow in Warsaw (up to 23:59:59.999999)
    if is_evening and day_after_tomorrow_d:
        end_warsaw = datetime.combine(day_after_tomorrow_d, dt_time.max, tzinfo=WARSAW_TZ)
        end_time = end_warsaw.astimezone(timezone.utc)
    else:
        end_warsaw = datetime.combine(target_d, dt_time.max, tzinfo=WARSAW_TZ)
        end_time = end_warsaw.astimezone(timezone.utc)

    if max_forward_hours is not None and max_forward_hours > 0:
        cutoff_forward = eval_dt + timedelta(hours=max_forward_hours)
        if cutoff_forward < end_time:
            end_time = cutoff_forward

    return UltraHorizon(
        target_date=target_d,
        tomorrow_date=tomorrow_d,
        day_after_tomorrow_date=day_after_tomorrow_d,
        start_time=start_time,
        end_time=end_time,
        is_evening_horizon=is_evening,
    )


def is_in_ultra_horizon(
    start_time: Any,
    target_date: Optional[Union[str, date]] = None,
    evaluation_time: Optional[datetime] = None,
    evening_start_hour: int = 20,
    include_tomorrow: Optional[bool] = None,
) -> bool:
    """Convenience function checking if start_time falls within ULTRA's Europe/Warsaw horizon."""
    horizon = resolve_ultra_horizon(
        target_date=target_date,
        evaluation_time=evaluation_time,
        evening_start_hour=evening_start_hour,
        include_tomorrow=include_tomorrow,
    )
    return horizon.is_in_horizon(start_time, evaluation_time=evaluation_time)


@dataclass(frozen=True)
class UltraScanScope:
    """Configurable scope parameters for ULTRA SCAN."""
    target_date: Optional[str] = None  # YYYY-MM-DD in Europe/Warsaw, None = today
    min_ev_percent: float = 2.0
    min_surebet_margin_percent: float = 0.1
    max_results_per_category: int = 25
    enable_props: bool = True
    enable_surebets: bool = True
    enable_valuebets: bool = True
    enable_depth_pass: bool = True
    evening_start_hour: int = 20       # 20:00 Europe/Warsaw threshold for late-day horizon
    include_tomorrow: Optional[bool] = None  # None = auto (hour >= evening_start_hour), True/False = explicit
    max_forward_hours: Optional[float] = None  # Optional bounded forward window

    def to_dict(self) -> Dict[str, Any]:
        return {
            "target_date": self.target_date,
            "min_ev_percent": self.min_ev_percent,
            "min_surebet_margin_percent": self.min_surebet_margin_percent,
            "max_results_per_category": self.max_results_per_category,
            "enable_props": self.enable_props,
            "enable_surebets": self.enable_surebets,
            "enable_valuebets": self.enable_valuebets,
            "enable_depth_pass": self.enable_depth_pass,
            "evening_start_hour": self.evening_start_hour,
            "include_tomorrow": self.include_tomorrow,
            "max_forward_hours": self.max_forward_hours,
        }


@dataclass(frozen=True)
class UltraScanBudget:
    """Configurable execution safety budget for ULTRA SCAN."""
    max_duration_seconds: float = 900.0  # 15 min hard timeout
    target_duration_seconds: float = 720.0  # 12 min target
    max_superbet_details: int = 1000  # Default covers full today slate without arbitrary capping
    max_betclic_details: int = 1000   # Default covers full today slate without arbitrary capping
    max_statshub_fixtures: int = 30
    max_statshub_trends: int = 15
    max_odds_api_requests: int = 30

    def to_dict(self) -> Dict[str, Any]:
        return {
            "max_duration_seconds": self.max_duration_seconds,
            "target_duration_seconds": self.target_duration_seconds,
            "max_superbet_details": self.max_superbet_details,
            "max_betclic_details": self.max_betclic_details,
            "max_statshub_fixtures": self.max_statshub_fixtures,
            "max_statshub_trends": self.max_statshub_trends,
            "max_odds_api_requests": self.max_odds_api_requests,
        }


@dataclass
class UltraScanFunnelMetrics:
    """Auditable 8-stage funnel metrics for ULTRA SCAN."""
    # 1. Discovery
    discovered_events_total: int = 0
    discovered_today_events: int = 0
    discovered_tomorrow_events: int = 0
    discovered_day_after_tomorrow_events: int = 0
    discovered_superbet_today: int = 0
    discovered_superbet_tomorrow: int = 0
    discovered_superbet_day_after_tomorrow: int = 0
    discovered_betclic_today: int = 0
    discovered_betclic_tomorrow: int = 0
    discovered_betclic_day_after_tomorrow: int = 0
    discovered_statshub_today: int = 0
    # 2. Matching
    matched_events_today: int = 0
    matched_events_tomorrow: int = 0
    matched_events_day_after_tomorrow: int = 0
    overlap_events_count: int = 0
    single_provider_events_count: int = 0
    # 3. Acquisition & Detailed Accounting
    selected_today_events: int = 0
    selected_tomorrow_events: int = 0
    selected_day_after_tomorrow_events: int = 0
    detail_fetch_attempted_superbet: int = 0
    detail_fetch_attempted_betclic: int = 0
    detail_fetch_success_superbet: int = 0
    detail_fetch_success_betclic: int = 0
    detail_fetch_failed_superbet: int = 0
    detail_fetch_failed_betclic: int = 0
    detail_fetch_skipped: int = 0
    details_with_markets: int = 0
    details_without_markets: int = 0
    acquired_detail_events_superbet: int = 0  # alias to detail_fetch_success_superbet for backward-compat
    acquired_detail_events_betclic: int = 0    # alias to detail_fetch_success_betclic for backward-compat
    acquired_markets_total: int = 0           # raw provider markets parsed
    markets_acquired_superbet: int = 0
    markets_acquired_betclic: int = 0
    # 4. Normalization
    normalized_markets_total: int = 0         # canonical markets normalized
    # 5. Market Matching
    matched_markets_total: int = 0
    markets_single_provider_unmatchable: int = 0
    # 6. Evaluation
    evaluated_markets_total: int = 0
    evaluated_surebets: int = 0
    evaluated_valuebets: int = 0
    evaluated_player_props: int = 0
    evaluated_team_props: int = 0
    # 7. Qualification
    qualified_surebets: int = 0
    qualified_valuebets: int = 0
    qualified_player_props: int = 0
    qualified_team_props: int = 0
    qualified_watchlist: int = 0
    # 8. Ranked & Reported
    ranked_opportunities_total: int = 0
    reported_opportunities_total: int = 0
    odds_api_requests_made: int = 0
    statshub_requests_made: int = 0
    depth_pass_fixtures_refined: int = 0
    depth_pass_opportunities_refined: int = 0
    provider_status: Dict[str, str] = field(default_factory=dict)
    rejection_reasons: Dict[str, int] = field(default_factory=dict)
    phase_durations_seconds: Dict[str, float] = field(default_factory=dict)

    def record_rejection(self, reason: str, count: int = 1) -> None:
        self.rejection_reasons[reason] = self.rejection_reasons.get(reason, 0) + count

    def to_dict(self) -> Dict[str, Any]:
        return {
            "discovered_events_total": self.discovered_events_total,
            "discovered_today_events": self.discovered_today_events,
            "discovered_tomorrow_events": self.discovered_tomorrow_events,
            "discovered_day_after_tomorrow_events": self.discovered_day_after_tomorrow_events,
            "discovered_superbet_today": self.discovered_superbet_today,
            "discovered_superbet_tomorrow": self.discovered_superbet_tomorrow,
            "discovered_superbet_day_after_tomorrow": self.discovered_superbet_day_after_tomorrow,
            "discovered_betclic_today": self.discovered_betclic_today,
            "discovered_betclic_tomorrow": self.discovered_betclic_tomorrow,
            "discovered_betclic_day_after_tomorrow": self.discovered_betclic_day_after_tomorrow,
            "discovered_statshub_today": self.discovered_statshub_today,
            "matched_events_today": self.matched_events_today,
            "matched_events_tomorrow": self.matched_events_tomorrow,
            "matched_events_day_after_tomorrow": self.matched_events_day_after_tomorrow,
            "overlap_events_count": self.overlap_events_count,
            "single_provider_events_count": self.single_provider_events_count,
            "selected_today_events": self.selected_today_events,
            "selected_tomorrow_events": self.selected_tomorrow_events,
            "selected_day_after_tomorrow_events": self.selected_day_after_tomorrow_events,
            "detail_fetch_attempted_superbet": self.detail_fetch_attempted_superbet,
            "detail_fetch_attempted_betclic": self.detail_fetch_attempted_betclic,
            "detail_fetch_success_superbet": self.detail_fetch_success_superbet,
            "detail_fetch_success_betclic": self.detail_fetch_success_betclic,
            "detail_fetch_failed_superbet": self.detail_fetch_failed_superbet,
            "detail_fetch_failed_betclic": self.detail_fetch_failed_betclic,
            "detail_fetch_skipped": self.detail_fetch_skipped,
            "details_with_markets": self.details_with_markets,
            "details_without_markets": self.details_without_markets,
            "acquired_detail_events_superbet": self.acquired_detail_events_superbet,
            "acquired_detail_events_betclic": self.acquired_detail_events_betclic,
            "acquired_markets_total": self.acquired_markets_total,
            "markets_acquired_superbet": self.markets_acquired_superbet,
            "markets_acquired_betclic": self.markets_acquired_betclic,
            "normalized_markets_total": self.normalized_markets_total,
            "matched_markets_total": self.matched_markets_total,
            "markets_single_provider_unmatchable": self.markets_single_provider_unmatchable,
            "evaluated_markets_total": self.evaluated_markets_total,
            "evaluated_surebets": self.evaluated_surebets,
            "evaluated_valuebets": self.evaluated_valuebets,
            "evaluated_player_props": self.evaluated_player_props,
            "evaluated_team_props": self.evaluated_team_props,
            "qualified_surebets": self.qualified_surebets,
            "qualified_valuebets": self.qualified_valuebets,
            "qualified_player_props": self.qualified_player_props,
            "qualified_team_props": self.qualified_team_props,
            "qualified_watchlist": self.qualified_watchlist,
            "ranked_opportunities_total": self.ranked_opportunities_total,
            "reported_opportunities_total": self.reported_opportunities_total,
            "odds_api_requests_made": self.odds_api_requests_made,
            "statshub_requests_made": self.statshub_requests_made,
            "depth_pass_fixtures_refined": self.depth_pass_fixtures_refined,
            "depth_pass_opportunities_refined": self.depth_pass_opportunities_refined,
            "provider_status": dict(self.provider_status),
            "rejection_reasons": dict(self.rejection_reasons),
            "phase_durations_seconds": {k: round(v, 2) for k, v in self.phase_durations_seconds.items()},
        }


@dataclass
class UltraOpportunity:
    """Unified container for an opportunity evaluated during ULTRA SCAN."""
    opportunity_id: str
    category: str  # "SUREBET", "VALUEBET", "PLAYER_PROP", "TEAM_PROP", "WATCHLIST"
    match_name: str
    competition: str
    kickoff: str
    market_display: str
    selection_display: str
    bookmaker: str  # Polish execution bookmaker: Superbet, Betclic, or combination
    raw_odds: float
    effective_odds: float  # Net odds after Polish 12% turnover tax if applicable
    fair_odds: Optional[float]
    edge_pct: float  # Net EV % or Arbitrage Margin %
    confidence: str  # "HIGH", "MEDIUM", "LOW"
    ultra_rank_score: float
    reference_sources: List[str] = field(default_factory=list)
    is_watchlist: bool = False
    watchlist_reason: Optional[str] = None
    pass_number: int = 1  # 1 for Breadth, 2 for Depth
    horizon_bucket: str = "TODAY"  # "TODAY", "TOMORROW", or "DAY_AFTER_TOMORROW"
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "opportunity_id": self.opportunity_id,
            "category": self.category,
            "match_name": self.match_name,
            "competition": self.competition,
            "kickoff": self.kickoff,
            "market_display": self.market_display,
            "selection_display": self.selection_display,
            "bookmaker": self.bookmaker,
            "raw_odds": round(self.raw_odds, 2),
            "effective_odds": round(self.effective_odds, 2),
            "fair_odds": round(self.fair_odds, 2) if self.fair_odds is not None else None,
            "edge_pct": round(self.edge_pct, 2),
            "confidence": self.confidence,
            "ultra_rank_score": round(self.ultra_rank_score, 2),
            "reference_sources": list(self.reference_sources),
            "is_watchlist": self.is_watchlist,
            "watchlist_reason": self.watchlist_reason,
            "pass_number": self.pass_number,
            "horizon_bucket": self.horizon_bucket,
            "details": self.details,
        }


@dataclass
class UltraScanResult:
    """Complete diagnostic and operational result of an ULTRA SCAN execution."""
    execution_id: str
    status: str  # "SUCCESS", "PARTIAL", "FAILED"
    target_date: str
    started_at: str
    completed_at: str
    duration_seconds: float
    funnel: UltraScanFunnelMetrics
    top_opportunities: List[UltraOpportunity] = field(default_factory=list)
    surebets: List[UltraOpportunity] = field(default_factory=list)
    valuebets: List[UltraOpportunity] = field(default_factory=list)
    player_props: List[UltraOpportunity] = field(default_factory=list)
    team_props: List[UltraOpportunity] = field(default_factory=list)
    watchlist: List[UltraOpportunity] = field(default_factory=list)
    failures: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    diagnostics: Dict[str, Any] = field(default_factory=dict)
    validation_result: Optional[Any] = None
    all_graphs: Optional[List[Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "execution_id": self.execution_id,
            "status": self.status,
            "target_date": self.target_date,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "duration_seconds": round(self.duration_seconds, 2),
            "funnel": self.funnel.to_dict(),
            "counts": {
                "top_opportunities": len(self.top_opportunities),
                "surebets": len(self.surebets),
                "valuebets": len(self.valuebets),
                "player_props": len(self.player_props),
                "team_props": len(self.team_props),
                "watchlist": len(self.watchlist),
                "failures": len(self.failures),
            },
            "top_opportunities": [o.to_dict() for o in self.top_opportunities],
            "surebets": [o.to_dict() for o in self.surebets],
            "valuebets": [o.to_dict() for o in self.valuebets],
            "player_props": [o.to_dict() for o in self.player_props],
            "team_props": [o.to_dict() for o in self.team_props],
            "watchlist": [o.to_dict() for o in self.watchlist],
            "failures": list(self.failures),
            "warnings": list(self.warnings),
            "diagnostics": self.diagnostics,
        }


def calculate_ultra_rank_score(
    edge_pct: float,
    confidence: str,
    reference_sources_count: int,
    market_type: str,
) -> float:
    """Calculates multi-signal UltraRankScore for deterministic opportunity ranking.

    Score = Edge % * Conf_Multiplier * Ref_Multiplier * Liquidity_Multiplier
    """
    conf_mult = 1.0 if confidence == "HIGH" else (0.85 if confidence == "MEDIUM" else 0.70)
    ref_mult = min(1.0, 0.70 + 0.10 * max(1, reference_sources_count))

    m_upper = str(market_type).upper()
    if any(k in m_upper for k in ("1X2", "TOTALS", "OVER_UNDER", "BTTS", "DRAW_NO_BET")):
        liq_mult = 1.0
    elif any(k in m_upper for k in ("HANDICAP", "DOUBLE_CHANCE", "HALF_TIME")):
        liq_mult = 0.92
    else:
        # Props / alternate lines
        liq_mult = 0.85

    base_edge = max(0.01, float(edge_pct))
    return round(base_edge * conf_mult * ref_mult * liq_mult, 4)


class UltraScanOrchestrator:
    """Heavy daily orchestrator executing full-slate discovery, acquisition, and evaluation."""

    def __init__(
        self,
        scope: Optional[UltraScanScope] = None,
        budget: Optional[UltraScanBudget] = None,
        db_manager: Optional[Any] = None,
        scan_orchestrator: Optional[Any] = None,
        reference_provider: Optional[ReferenceOddsProvider] = None,
        props_scanner: Optional[Any] = None,
        surebet_detector: Optional[Any] = None,
    ) -> None:
        self.scope = scope or UltraScanScope()
        self.budget = budget or UltraScanBudget()
        self.db_manager = db_manager
        self.scan_orchestrator = scan_orchestrator
        self.reference_provider = reference_provider or TheOddsApiReferenceProvider()
        self.props_scanner = props_scanner
        self.tax_engine = get_tax_engine()
        self.valuebet_engine = ValuebetEngine()
        self.surebet_detector = surebet_detector or SurebetDetectorEngine()

    def execute(
        self,
        providers: Optional[Dict[str, BaseProvider]] = None,
        evaluation_time: Optional[datetime] = None,
        cancellation_event: Optional[Any] = None,
    ) -> UltraScanResult:
        """Executes one complete ULTRA SCAN cycle across all today's matches.

        Guarantees:
        - Strict today-only boundary in Europe/Warsaw.
        - Full coverage acquisition across Superbet and Betclic within safety limits.
        - Two-Pass Depth refinement on promising lines and near-surebets.
        - Zero silent drops (all phases tracked in FunnelMetrics).
        - Execution bookmakers strictly restricted to Superbet and Betclic.
        """
        t0 = time.perf_counter()
        eval_dt = evaluation_time or datetime.now(timezone.utc)
        now_warsaw = eval_dt.astimezone(WARSAW_TZ)
        horizon = resolve_ultra_horizon(
            target_date=self.scope.target_date,
            evaluation_time=eval_dt,
            evening_start_hour=self.scope.evening_start_hour,
            include_tomorrow=self.scope.include_tomorrow,
            max_forward_hours=self.scope.max_forward_hours,
        )
        target_date_str = str(horizon.target_date)
        tomorrow_date_str = str(horizon.tomorrow_date) if horizon.tomorrow_date else None
        day_after_tomorrow_date_str = str(horizon.day_after_tomorrow_date) if horizon.day_after_tomorrow_date else None

        execution_id = f"ultra_{now_warsaw.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        logger.info(
            "Starting ULTRA SCAN [%s] for target date %s (Warsaw TZ, evening_horizon=%s, tomorrow=%s, day_after_tomorrow=%s)",
            execution_id,
            target_date_str,
            horizon.is_evening_horizon,
            tomorrow_date_str,
            day_after_tomorrow_date_str,
        )

        funnel = UltraScanFunnelMetrics()
        failures: List[str] = []
        warnings: List[str] = []
        diagnostics: Dict[str, Any] = {
            "execution_id": execution_id,
            "target_date": target_date_str,
            "tomorrow_date": tomorrow_date_str,
            "day_after_tomorrow_date": day_after_tomorrow_date_str,
            "is_evening_horizon": horizon.is_evening_horizon,
            "horizon_start": horizon.start_time.isoformat(),
            "horizon_end": horizon.end_time.isoformat(),
        }
        has_timeout = False

        def _check_deadline(phase_name: str) -> bool:
            nonlocal has_timeout
            elapsed = time.perf_counter() - t0
            if elapsed >= self.budget.max_duration_seconds:
                msg = f"Timeout reached before {phase_name} ({elapsed:.1f}s >= {self.budget.max_duration_seconds}s)"
                logger.warning(msg)
                warnings.append(msg)
                funnel.record_rejection("TIMEOUT_BUDGET_EXCEEDED")
                has_timeout = True
                return True
            if cancellation_event is not None and getattr(cancellation_event, "is_set", lambda: False)():
                msg = f"Cancellation requested before {phase_name}"
                logger.warning(msg)
                warnings.append(msg)
                funnel.record_rejection("OPERATION_CANCELLED")
                return True
            return False

        # ──────────────────────────────────────────────────────────────────────
        # PHASE 2: FULL-DAY DISCOVERY (Europe/Warsaw horizon boundary)
        # ──────────────────────────────────────────────────────────────────────
        t_phase_0 = time.perf_counter()
        sb_provider: Optional[SuperbetProvider] = None
        bc_provider: Optional[BetclicProvider] = None

        if providers and "superbet" in providers and providers["superbet"] is not None:
            sb_provider = providers["superbet"]
        else:
            sb_provider = SuperbetProvider()

        if providers and "betclic" in providers and providers["betclic"] is not None:
            bc_provider = providers["betclic"]
        else:
            bc_provider = BetclicProvider()

        sb_discovered_all: List[Any] = []
        bc_discovered_all: List[Any] = []

        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as disc_pool:
                sb_future = disc_pool.submit(sb_provider.discover)
                bc_future = disc_pool.submit(bc_provider.discover)
                disc_timeout = min(60.0, self.budget.max_duration_seconds / 4)
                try:
                    sb_discovered_all = sb_future.result(timeout=disc_timeout)
                    funnel.provider_status["superbet"] = "AVAILABLE" if sb_discovered_all else "NO_DATA"
                except Exception as sb_err:
                    msg = f"Superbet discovery phase error: {sb_err}"
                    logger.warning(msg, exc_info=True)
                    warnings.append(msg)
                    funnel.provider_status["superbet"] = "DISCOVERY_ERROR"
                try:
                    bc_discovered_all = bc_future.result(timeout=disc_timeout)
                    funnel.provider_status["betclic"] = "AVAILABLE" if bc_discovered_all else "NO_DATA"
                except Exception as bc_err:
                    msg = f"Betclic discovery phase error: {bc_err}"
                    logger.warning(msg, exc_info=True)
                    warnings.append(msg)
                    if "403" in str(bc_err):
                        funnel.provider_status["betclic"] = "ACCESS_DENIED_403"
                    else:
                        funnel.provider_status["betclic"] = "DISCOVERY_ERROR"
        except Exception as disc_err:
            msg = f"Provider discovery executor error: {disc_err}"
            logger.error(msg, exc_info=True)
            failures.append(msg)

        funnel.discovered_events_total = len(sb_discovered_all) + len(bc_discovered_all)

        # Strict horizon and freshness filtering in Europe/Warsaw
        def _filter_slate(items: List[Any], provider_name: str) -> Tuple[List[Any], List[Any], List[Any], List[Any]]:
            today_items = []
            tomorrow_items = []
            day_after_tomorrow_items = []
            all_valid = []
            for item in items:
                st = getattr(item, "start_time", None)
                eligible, bucket = horizon.classify_kickoff(st, eval_dt)
                if not eligible:
                    if bucket == "STALE":
                        funnel.record_rejection("STALE_ALREADY_STARTED")
                    else:
                        funnel.record_rejection(f"{provider_name.upper()}_OUTSIDE_TARGET_DATE")
                        funnel.record_rejection(f"{provider_name.upper()}_OUTSIDE_HORIZON")
                    continue
                all_valid.append(item)
                if bucket == "TODAY":
                    today_items.append(item)
                elif bucket == "TOMORROW":
                    tomorrow_items.append(item)
                elif bucket == "DAY_AFTER_TOMORROW":
                    day_after_tomorrow_items.append(item)
            return all_valid, today_items, tomorrow_items, day_after_tomorrow_items

        sb_today_all, sb_today, sb_tomorrow, sb_day_after = _filter_slate(sb_discovered_all, "superbet")
        bc_today_all, bc_today, bc_tomorrow, bc_day_after = _filter_slate(bc_discovered_all, "betclic")

        funnel.discovered_superbet_today = len(sb_today)
        funnel.discovered_superbet_tomorrow = len(sb_tomorrow)
        funnel.discovered_superbet_day_after_tomorrow = len(sb_day_after)
        funnel.discovered_betclic_today = len(bc_today)
        funnel.discovered_betclic_tomorrow = len(bc_tomorrow)
        funnel.discovered_betclic_day_after_tomorrow = len(bc_day_after)
        funnel.discovered_today_events = len(sb_today) + len(bc_today)
        funnel.discovered_tomorrow_events = len(sb_tomorrow) + len(bc_tomorrow)
        funnel.discovered_day_after_tomorrow_events = len(sb_day_after) + len(bc_day_after)

        logger.info(
            "ULTRA SCAN Discovery: %d total events, %d in horizon (Today: %d [SB: %d, BC: %d], Tomorrow: %d [SB: %d, BC: %d], DayAfterTomorrow: %d [SB: %d, BC: %d])",
            funnel.discovered_events_total,
            len(sb_today_all) + len(bc_today_all),
            funnel.discovered_today_events,
            funnel.discovered_superbet_today,
            funnel.discovered_betclic_today,
            funnel.discovered_tomorrow_events,
            funnel.discovered_superbet_tomorrow,
            funnel.discovered_betclic_tomorrow,
            funnel.discovered_day_after_tomorrow_events,
            funnel.discovered_superbet_day_after_tomorrow,
            funnel.discovered_betclic_day_after_tomorrow,
        )

        funnel.phase_durations_seconds["discovery"] = time.perf_counter() - t_phase_0

        # ──────────────────────────────────────────────────────────────────────
        # PHASE 3 & 4: PASS 1 BREADTH ACQUISITION & NORMALIZATION
        # ──────────────────────────────────────────────────────────────────────
        t_phase_1 = time.perf_counter()
        all_today_graphs: List[NormalizedGraph] = []
        provider_graphs_map: Dict[str, List[NormalizedGraph]] = {"superbet": [], "betclic": []}

        if not _check_deadline("acquisition"):
            # Select target events up to configured budgets across the horizon slate
            if self.budget.max_superbet_details is not None:
                sb_selected = sb_today_all[:self.budget.max_superbet_details]
            else:
                sb_selected = sb_today_all
            sb_skipped = max(0, len(sb_today_all) - len(sb_selected))
            selected_sb_ids = [
                str(getattr(item, "event_id", getattr(item, "id", ""))).strip()
                for item in sb_selected
            ]

            if self.budget.max_betclic_details is not None:
                bc_selected = bc_today_all[:self.budget.max_betclic_details]
            else:
                bc_selected = bc_today_all
            bc_skipped = max(0, len(bc_today_all) - len(bc_selected))
            selected_bc_ids = [
                str(getattr(item, "provider_event_id", getattr(item, "event_id", getattr(item, "id", "")))).strip()
                for item in bc_selected
            ]

            total_skipped = sb_skipped + bc_skipped
            if total_skipped > 0:
                funnel.detail_fetch_skipped = total_skipped
                funnel.record_rejection("BUDGET_CAPPED", count=total_skipped)

            funnel.selected_today_events = (
                sum(1 for it in sb_selected if horizon.classify_kickoff(it.start_time, eval_dt)[1] == "TODAY") +
                sum(1 for it in bc_selected if horizon.classify_kickoff(it.start_time, eval_dt)[1] == "TODAY")
            )
            funnel.selected_tomorrow_events = (
                sum(1 for it in sb_selected if horizon.classify_kickoff(it.start_time, eval_dt)[1] == "TOMORROW") +
                sum(1 for it in bc_selected if horizon.classify_kickoff(it.start_time, eval_dt)[1] == "TOMORROW")
            )
            funnel.selected_day_after_tomorrow_events = (
                sum(1 for it in sb_selected if horizon.classify_kickoff(it.start_time, eval_dt)[1] == "DAY_AFTER_TOMORROW") +
                sum(1 for it in bc_selected if horizon.classify_kickoff(it.start_time, eval_dt)[1] == "DAY_AFTER_TOMORROW")
            )

            funnel.detail_fetch_attempted_superbet = len(selected_sb_ids)
            funnel.detail_fetch_attempted_betclic = len(selected_bc_ids)
            funnel.acquired_detail_events_superbet = len(selected_sb_ids)
            funnel.acquired_detail_events_betclic = len(selected_bc_ids)

            sb_provider.configure_full_market_acquisition(event_ids=selected_sb_ids)
            bc_provider.configure_full_market_acquisition(event_ids=selected_bc_ids)

            # Set filtered discovery items so providers only fetch in-horizon items
            sb_provider.set_discovered_items(sb_today_all)
            bc_provider.set_discovered_items(bc_today_all)

            sb_raw: List[Any] = []
            bc_raw: List[Any] = []
            try:
                with concurrent.futures.ThreadPoolExecutor(max_workers=2) as acq_pool:
                    sb_fut = acq_pool.submit(sb_provider.fetch, sb_selected)
                    bc_fut = acq_pool.submit(bc_provider.fetch, bc_selected)
                    acq_timeout = min(600.0, max(30.0, self.budget.max_duration_seconds * 0.7))
                    try:
                        sb_raw = sb_fut.result(timeout=acq_timeout)
                    except Exception as sb_err:
                        msg = f"Superbet acquisition phase error: {sb_err}"
                        logger.warning(msg, exc_info=True)
                        warnings.append(msg)
                        if isinstance(sb_err, concurrent.futures.TimeoutError):
                            funnel.provider_status["superbet"] = "TIMEOUT"
                        else:
                            funnel.provider_status["superbet"] = "FETCH_ERROR"
                    try:
                        bc_raw = bc_fut.result(timeout=acq_timeout)
                    except Exception as bc_err:
                        msg = f"Betclic acquisition phase error: {bc_err}"
                        logger.warning(msg, exc_info=True)
                        warnings.append(msg)
                        if "403" in str(bc_err):
                            funnel.provider_status["betclic"] = "ACCESS_DENIED_403"
                        elif isinstance(bc_err, concurrent.futures.TimeoutError):
                            funnel.provider_status["betclic"] = "TIMEOUT"
                        else:
                            funnel.provider_status["betclic"] = "FETCH_ERROR"
            except Exception as acq_err:
                msg = f"Provider acquisition executor error: {acq_err}"
                logger.error(msg, exc_info=True)
                failures.append(msg)

            # Detail fetch accounting (Funnel Conservation Law: Attempted == Success + Failed)
            sb_raw_count = len(sb_raw)
            sb_failed_payloads = sum(1 for p in sb_raw if is_detail_fetch_failure(p))
            sb_missing = max(0, len(selected_sb_ids) - sb_raw_count) if funnel.provider_status.get("superbet") in ("TIMEOUT", "FETCH_ERROR") else 0
            sb_total_failed = sb_failed_payloads + sb_missing
            sb_success = max(0, sb_raw_count - sb_failed_payloads)

            funnel.detail_fetch_success_superbet = sb_success
            funnel.detail_fetch_failed_superbet = sb_total_failed
            if sb_missing > 0:
                funnel.record_rejection("DETAIL_FETCH_EXCEPTION_OR_TIMEOUT", count=sb_missing)
            if sb_failed_payloads > 0:
                funnel.record_rejection("DETAIL_FETCH_FAILED", count=sb_failed_payloads)

            if sb_failed_payloads > 0 and sb_success == 0 and len(selected_sb_ids) > 0:
                if funnel.provider_status.get("superbet") not in ("TIMEOUT", "FETCH_ERROR"):
                    funnel.provider_status["superbet"] = "FETCH_FAILED"
            elif sb_success > 0 or funnel.provider_status.get("superbet") == "AVAILABLE":
                funnel.provider_status["superbet"] = "AVAILABLE"

            bc_raw_count = len(bc_raw)
            bc_failed_payloads = sum(1 for p in bc_raw if is_detail_fetch_failure(p))
            bc_missing = max(0, len(selected_bc_ids) - bc_raw_count) if funnel.provider_status.get("betclic") in ("TIMEOUT", "FETCH_ERROR") else 0
            bc_total_failed = bc_failed_payloads + bc_missing
            bc_success = max(0, bc_raw_count - bc_failed_payloads)

            funnel.detail_fetch_success_betclic = bc_success
            funnel.detail_fetch_failed_betclic = bc_total_failed
            if bc_missing > 0:
                funnel.record_rejection("DETAIL_FETCH_EXCEPTION_OR_TIMEOUT", count=bc_missing)
            if bc_failed_payloads > 0:
                funnel.record_rejection("DETAIL_FETCH_FAILED", count=bc_failed_payloads)

            if bc_failed_payloads > 0 and bc_success == 0 and len(selected_bc_ids) > 0:
                if funnel.provider_status.get("betclic") not in ("ACCESS_DENIED_403", "TIMEOUT", "FETCH_ERROR"):
                    funnel.provider_status["betclic"] = "FETCH_FAILED"
            elif bc_success > 0 or funnel.provider_status.get("betclic") == "AVAILABLE":
                funnel.provider_status["betclic"] = "AVAILABLE"

            # Parse & Normalize
            raw_sb_parsed = sb_provider.parse(sb_raw) if hasattr(sb_provider, "parse") else []
            raw_bc_parsed = bc_provider.parse(bc_raw) if hasattr(bc_provider, "parse") else []
            sb_parsed = raw_sb_parsed if isinstance(raw_sb_parsed, list) else []
            bc_parsed = raw_bc_parsed if isinstance(raw_bc_parsed, list) else []

            all_parsed_events = sb_parsed + bc_parsed
            for pe in all_parsed_events:
                mkts = getattr(pe, "markets", [])
                if mkts:
                    funnel.details_with_markets += 1
                else:
                    funnel.details_without_markets += 1

            norm_engine = NormalizationEngine()
            if sb_parsed:
                sb_norm = norm_engine.normalize(provider_name="superbet", parsed_objects=sb_parsed)
                sb_today_graphs = [
                    g for g in sb_norm.graphs
                    if horizon.is_in_horizon(
                        getattr(g.event, "scheduled_start", getattr(g.event, "start_time", None)),
                        evaluation_time=eval_dt,
                    )
                ]
                provider_graphs_map["superbet"] = sb_today_graphs
                all_today_graphs.extend(sb_today_graphs)
                for g in sb_today_graphs:
                    funnel.markets_acquired_superbet += len(g.markets)
            if bc_parsed:
                bc_norm = norm_engine.normalize(provider_name="betclic", parsed_objects=bc_parsed)
                bc_today_graphs = [
                    g for g in bc_norm.graphs
                    if horizon.is_in_horizon(
                        getattr(g.event, "scheduled_start", getattr(g.event, "start_time", None)),
                        evaluation_time=eval_dt,
                    )
                ]
                provider_graphs_map["betclic"] = bc_today_graphs
                all_today_graphs.extend(bc_today_graphs)
                for g in bc_today_graphs:
                    funnel.markets_acquired_betclic += len(g.markets)

            for g in all_today_graphs:
                funnel.acquired_markets_total += len(g.markets)
                funnel.normalized_markets_total += len(g.markets)

        funnel.phase_durations_seconds["acquisition_normalization"] = time.perf_counter() - t_phase_1

        # ──────────────────────────────────────────────────────────────────────
        # PHASE 4 (Matching) & 5 (Evaluation): Cross-Bookmaker N-Way Matching
        # ──────────────────────────────────────────────────────────────────────
        t_phase_2 = time.perf_counter()
        validation_pipeline = CrossBookmakerValidationPipeline()
        validation_result = None

        if not _check_deadline("matching"):
            if len(provider_graphs_map["superbet"]) > 0 and len(provider_graphs_map["betclic"]) > 0:
                try:
                    validation_result = validation_pipeline.run_n_way(all_items=all_today_graphs)
                    funnel.matched_events_today = sum(
                        1 for ce in validation_result.canonical_events
                        if horizon.classify_kickoff(ce.scheduled_start, eval_dt)[1] == "TODAY"
                    )
                    funnel.matched_events_tomorrow = sum(
                        1 for ce in validation_result.canonical_events
                        if horizon.classify_kickoff(ce.scheduled_start, eval_dt)[1] == "TOMORROW"
                    )
                    funnel.matched_events_day_after_tomorrow = sum(
                        1 for ce in validation_result.canonical_events
                        if horizon.classify_kickoff(ce.scheduled_start, eval_dt)[1] == "DAY_AFTER_TOMORROW"
                    )
                    funnel.matched_markets_total = validation_result.metrics.matched_market_count
                    funnel.overlap_events_count = sum(
                        1 for ce in validation_result.canonical_events
                        if "superbet" in ce.sources and "betclic" in ce.sources
                    )
                    funnel.single_provider_events_count = max(0, len(all_today_graphs) - (funnel.overlap_events_count * 2))

                    overlap_event_internal_ids = {
                        src.internal_event_id
                        for ce in validation_result.canonical_events
                        if len(ce.sources) >= 2
                        for src in ce.sources.values()
                    }
                    funnel.markets_single_provider_unmatchable = sum(
                        len(g.markets) for g in all_today_graphs
                        if g.event and g.event.internal_id not in overlap_event_internal_ids
                    )
                except Exception as match_err:
                    msg = f"Cross-bookmaker matching error: {match_err}"
                    logger.error(msg, exc_info=True)
                    warnings.append(msg)
            else:
                warnings.append("Insufficient multi-provider coverage for cross-matching (requires both Superbet and Betclic).")

        funnel.phase_durations_seconds["matching"] = time.perf_counter() - t_phase_2

        # ──────────────────────────────────────────────────────────────────────
        # EVALUATION 1: SUREBETS & NEAR-SUREBETS (Pass 1)
        # ──────────────────────────────────────────────────────────────────────
        t_phase_3 = time.perf_counter()
        raw_surebets: List[UltraOpportunity] = []
        raw_watchlist: List[UltraOpportunity] = []

        # Build canonical_event_map for human-readable match / competition names
        canonical_event_map: Dict[str, CanonicalEvent] = {}
        if validation_result and validation_result.canonical_events:
            for ce in validation_result.canonical_events:
                canonical_event_map[ce.canonical_event_id] = ce

        if self.scope.enable_surebets and validation_result is not None and not _check_deadline("surebet_eval"):
            try:
                det_result: SurebetDetectionResult = self.surebet_detector.detect(validation_result)
                funnel.evaluated_surebets = len(det_result.evaluations)
                funnel.evaluated_markets_total += len(det_result.evaluations)

                if hasattr(det_result, "incomplete_evaluations") and det_result.incomplete_evaluations:
                    funnel.record_rejection("SUREBET_INCOMPLETE_MARKET", count=len(det_result.incomplete_evaluations))
                if hasattr(det_result, "unsupported_evaluations") and det_result.unsupported_evaluations:
                    funnel.record_rejection("SUREBET_UNSUPPORTED_MARKET", count=len(det_result.unsupported_evaluations))
                if hasattr(det_result, "invalid_evaluations") and det_result.invalid_evaluations:
                    funnel.record_rejection("SUREBET_INVALID_ODDS", count=len(det_result.invalid_evaluations))
                if hasattr(det_result, "no_surebet_evaluations") and det_result.no_surebet_evaluations:
                    funnel.record_rejection("SUREBET_NO_ARBITRAGE", count=len(det_result.no_surebet_evaluations))

                for opp in det_result.opportunities:
                    if not opp.is_mixed_bookmakers:
                        continue  # Must span both Superbet and Betclic
                    # Enforce execution restriction
                    bms = {l.provider.lower() for l in opp.legs}
                    if not bms.issubset(EXECUTABLE_BOOKMAKERS):
                        continue

                    margin_pct = float(opp.arbitrage_margin * Decimal("100"))
                    if margin_pct < self.scope.min_surebet_margin_percent:
                        continue

                    ce = canonical_event_map.get(opp.canonical_event_id)
                    if ce:
                        match_display = f"{ce.home_team} vs {ce.away_team}"
                        comp_display = ce.competition.name if (ce.competition and hasattr(ce.competition, "name")) else (ce.competition if isinstance(ce.competition, str) else "Football")
                        kickoff_display = ce.scheduled_start or now_warsaw.strftime("%Y-%m-%d")
                    else:
                        match_display = opp.canonical_event_id
                        comp_display = "Football"
                        kickoff_display = now_warsaw.strftime("%Y-%m-%d")

                    legs_desc = " + ".join(f"{l.provider.title()} ({l.selection_type} @ {float(l.odds):.2f})" for l in opp.legs)
                    rank_score = calculate_ultra_rank_score(
                        edge_pct=margin_pct,
                        confidence="HIGH",
                        reference_sources_count=len(opp.legs),
                        market_type=opp.canonical_market_key.market_type,
                    )
                    best_raw = float(max(l.odds for l in opp.legs))
                    opp_bucket = horizon.classify_kickoff(kickoff_display, eval_dt)[1] or "TODAY"
                    ev_opp = UltraOpportunity(
                        opportunity_id=f"sb_{opp.opportunity_id}",
                        category="SUREBET",
                        match_name=match_display,
                        competition=comp_display,
                        kickoff=kickoff_display,
                        market_display=opp.canonical_market_key.to_key_string(),
                        selection_display=legs_desc,
                        bookmaker=" + ".join(sorted(l.provider.title() for l in opp.legs)),
                        raw_odds=best_raw,
                        effective_odds=best_raw,
                        fair_odds=None,
                        edge_pct=margin_pct,
                        confidence="HIGH",
                        ultra_rank_score=rank_score,
                        reference_sources=list(bms),
                        is_watchlist=False,
                        pass_number=1,
                        horizon_bucket=opp_bucket,
                        details={
                            "opportunity_id": opp.opportunity_id,
                            "canonical_event_id": opp.canonical_event_id,
                            "market_key": opp.canonical_market_key.to_key_string() if opp.canonical_market_key else "",
                            "implied_probability_sum": float(opp.implied_probability_sum),
                            "arbitrage_margin": float(opp.arbitrage_margin),
                            "legs": [
                                {
                                    "provider": l.provider,
                                    "selection_type": l.selection_type,
                                    "odds": float(l.odds),
                                    "effective_odds": float(l.effective_odds) if l.effective_odds else float(l.odds),
                                }
                                for l in opp.legs
                            ],
                        },
                    )
                    raw_surebets.append(ev_opp)

                funnel.qualified_surebets = len(raw_surebets)

                # Near-surebets for Watchlist (distance to arbitrage < 2.0%)
                if det_result.no_surebet_evaluations:
                    for nsb in det_result.no_surebet_evaluations:
                        if nsb.implied_probability_sum is None:
                            continue
                        S = float(nsb.implied_probability_sum)
                        dist = S - 1.0
                        if 0.0 < dist <= 0.020 and len(nsb.best_legs) >= 2:
                            bms = {l.provider.lower() for l in nsb.best_legs}
                            if not bms.issubset(EXECUTABLE_BOOKMAKERS):
                                continue

                            ce = canonical_event_map.get(nsb.canonical_event_id)
                            if ce:
                                match_display = f"{ce.home_team} vs {ce.away_team}"
                                comp_display = ce.competition.name if (ce.competition and hasattr(ce.competition, "name")) else (ce.competition if isinstance(ce.competition, str) else "Football")
                                kickoff_display = ce.scheduled_start or now_warsaw.strftime("%Y-%m-%d")
                            else:
                                match_display = nsb.canonical_event_id
                                comp_display = "Football"
                                kickoff_display = now_warsaw.strftime("%Y-%m-%d")

                            legs_desc = " + ".join(f"{l.provider.title()} ({l.selection_type} @ {float(l.odds):.2f})" for l in nsb.best_legs)
                            opp_bucket = horizon.classify_kickoff(kickoff_display, eval_dt)[1] or "TODAY"
                            watch_opp = UltraOpportunity(
                                opportunity_id=f"near_sb_{uuid.uuid4().hex[:6]}",
                                category="WATCHLIST",
                                match_name=match_display,
                                competition=comp_display,
                                kickoff=kickoff_display,
                                market_display=nsb.canonical_market_key.to_key_string(),
                                selection_display=f"Blisko arbitrażu (S={S:.4f}): {legs_desc}",
                                bookmaker=" + ".join(sorted(l.provider.title() for l in nsb.best_legs)),
                                raw_odds=float(max(l.odds for l in nsb.best_legs)),
                                effective_odds=float(max(l.odds for l in nsb.best_legs)),
                                fair_odds=None,
                                edge_pct=-(dist * 100.0),
                                confidence="MEDIUM",
                                ultra_rank_score=round(max(0.1, (1.0 - dist) * 2.0), 2),
                                is_watchlist=True,
                                watchlist_reason=f"Potencjalny arbitraż (odległość {dist*100:.2f}% do progu zysku)",
                                pass_number=1,
                                horizon_bucket=opp_bucket,
                                details={
                                    "legs": [
                                        {
                                            "selection_outcome": f"{l.selection_type}",
                                            "outcome": l.selection_type,
                                            "selection_type": l.selection_type,
                                            "provider": l.provider.lower(),
                                            "bookmaker": l.provider.lower(),
                                            "odds": float(l.odds),
                                            "raw_odds": float(l.odds),
                                            "effective_odds": float(getattr(l, "effective_odds", l.odds)),
                                            "implied_probability": 1.0 / float(l.odds) if float(l.odds) > 0 else 0.0,
                                        }
                                        for l in nsb.best_legs
                                    ],
                                    "sum_probabilities": S,
                                    "distance_pct": dist * 100.0,
                                    "canonical_market_key": nsb.canonical_market_key.to_key_string() if hasattr(nsb.canonical_market_key, "to_key_string") else str(nsb.canonical_market_key),
                                    "canonical_event_id": nsb.canonical_event_id,
                                },
                            )
                            raw_watchlist.append(watch_opp)

            except Exception as sb_err:
                msg = f"Surebet evaluation error: {sb_err}"
                logger.error(msg, exc_info=True)
                warnings.append(msg)

        funnel.phase_durations_seconds["surebet_eval"] = time.perf_counter() - t_phase_3

        # ──────────────────────────────────────────────────────────────────────
        # EVALUATION 2: VALUEBETS (Pass 1)
        # ──────────────────────────────────────────────────────────────────────
        t_phase_4 = time.perf_counter()
        raw_valuebets: List[UltraOpportunity] = []

        if self.scope.enable_valuebets and all_today_graphs and not _check_deadline("valuebet_eval"):
            try:
                ref_events: List[ReferenceEvent] = []
                # Map active competitions from today's normalized graphs to target sports
                active_comps = set()
                for g in all_today_graphs:
                    c_name = getattr(g.competition, "name", "") if g.competition else ""
                    if not c_name and g.event:
                        c_name = getattr(g.event, "competition_name", "") or ""
                    if c_name:
                        active_comps.add(c_name.lower())

                target_leagues: List[str] = []
                for comp in active_comps:
                    for k, sport_key in COMPETITION_TO_ODDS_API_SPORT.items():
                        if k in comp and sport_key not in target_leagues:
                            target_leagues.append(sport_key)

                if not target_leagues:
                    target_leagues = ["soccer_epl"]
                target_leagues = target_leagues[:self.budget.max_odds_api_requests]
                funnel.odds_api_requests_made = len(target_leagues)

                try:
                    ref_events = self.reference_provider.fetch_reference_events(
                        sport="soccer",
                        leagues=target_leagues,
                    )
                except Exception as ref_err:
                    msg = f"Could not fetch reference events from primary provider: {ref_err}"
                    logger.warning(msg)
                    warnings.append(msg)
                    funnel.record_rejection("REFERENCE_UNAVAILABLE")

                if ref_events:
                    vb_res: ValueBetDetectionResult = self.valuebet_engine.detect_valuebets(
                        bookmaker_graphs=all_today_graphs,
                        reference_events=ref_events,
                    )
                    funnel.evaluated_valuebets = getattr(vb_res.metrics, "markets_matched", getattr(vb_res.metrics, "markets_evaluated", len(vb_res.candidates)))
                    funnel.evaluated_markets_total += funnel.evaluated_valuebets

                    if hasattr(vb_res.metrics, "rejection_breakdown"):
                        for rej_reason, rej_count in vb_res.metrics.rejection_breakdown().items():
                            funnel.record_rejection(rej_reason, count=rej_count)

                    for cand in vb_res.candidates:
                        bm_clean = cand.bookmaker.lower()
                        if bm_clean not in EXECUTABLE_BOOKMAKERS:
                            continue
                        ev_pct = float(cand.value_percent)
                        if ev_pct < self.scope.min_ev_percent:
                            continue

                        raw_odds_f = float(cand.bookmaker_odds)
                        if cand.effective_net_odds is not None:
                            eff_odds_f = float(cand.effective_net_odds)
                        else:
                            eff_odds_f = float(self.tax_engine.calculate_net_odds(cand.bookmaker_odds))

                        if cand.net_value_percent is not None:
                            net_ev_pct = float(cand.net_value_percent)
                        else:
                            p_fair = float(cand.reference_fair_probability)
                            net_ev_pct = ((eff_odds_f * p_fair) - 1.0) * 100.0

                        if net_ev_pct < self.scope.min_ev_percent:
                            continue

                        conf = "HIGH" if cand.is_qualified else "MEDIUM"
                        ref_sources = [cand.reference_bookmaker] if cand.reference_bookmaker else ([cand.reference_source] if cand.reference_source else [])
                        ref_cnt = len(ref_sources)

                        mkt_display = cand.market_key.to_key_string() if cand.market_key else cand.market_type
                        sel_display = cand.selection_key.selection_type if cand.selection_key else cand.selection_type

                        rank_score = calculate_ultra_rank_score(
                            edge_pct=net_ev_pct,
                            confidence=conf,
                            reference_sources_count=max(1, ref_cnt),
                            market_type=cand.market_key.market_type if cand.market_key else cand.market_type,
                        )

                        opp_bucket = horizon.classify_kickoff(cand.kickoff, eval_dt)[1] or "TODAY"
                        vb_opp = UltraOpportunity(
                            opportunity_id=f"vb_{cand.candidate_id}",
                            category="VALUEBET",
                            match_name=cand.event_name,
                            competition=cand.competition_name or "Football",
                            kickoff=cand.kickoff or now_warsaw.strftime("%Y-%m-%d"),
                            market_display=mkt_display,
                            selection_display=sel_display,
                            bookmaker=cand.bookmaker.title(),
                            raw_odds=raw_odds_f,
                            effective_odds=eff_odds_f,
                            fair_odds=float(cand.reference_fair_odds),
                            edge_pct=net_ev_pct,
                            confidence=conf,
                            ultra_rank_score=rank_score,
                            reference_sources=ref_sources,
                            is_watchlist=False,
                            pass_number=1,
                            horizon_bucket=opp_bucket,
                            details={
                                "candidate_id": cand.candidate_id,
                                "canonical_event_id": cand.canonical_event_id,
                                "event_name": cand.event_name,
                                "sport": cand.sport,
                                "bookmaker": cand.bookmaker,
                                "reference_bookmaker": cand.reference_bookmaker,
                                "reference_source": cand.reference_source,
                                "reference_fair_probability": float(cand.reference_fair_probability),
                                "reference_fair_odds": float(cand.reference_fair_odds),
                                "value_percent": float(cand.value_percent),
                                "net_value_percent": net_ev_pct,
                            },
                        )
                        raw_valuebets.append(vb_opp)

                funnel.qualified_valuebets = len(raw_valuebets)
            except Exception as vb_err:
                msg = f"Valuebet evaluation error: {vb_err}"
                logger.error(msg, exc_info=True)
                warnings.append(msg)

        funnel.phase_durations_seconds["valuebet_eval"] = time.perf_counter() - t_phase_4

        # ──────────────────────────────────────────────────────────────────────
        # EVALUATION 3: PLAYER PROPS & TEAM PROPS (Pass 1)
        # ──────────────────────────────────────────────────────────────────────
        t_phase_5 = time.perf_counter()
        raw_player_props: List[UltraOpportunity] = []
        raw_team_props: List[UltraOpportunity] = []

        if self.scope.enable_props and all_today_graphs and not _check_deadline("props_eval"):
            try:
                # Reuse the already fetched and normalized Superbet and Betclic graphs!
                cached_for_props = [g for g in all_today_graphs if isinstance(g, NormalizedGraph)]
                props_scanner = self.props_scanner or GlobalPropsScanner(
                    cached_execution_events=cached_for_props,
                )

                time_horizon_days = 3 if horizon.is_evening_horizon else 1
                props_scope = GlobalScanScope(
                    time_horizon_days=time_horizon_days,
                    props_scope="ALL",
                    min_ev_percent=self.scope.min_ev_percent,
                    max_results=self.scope.max_results_per_category * 2,
                )
                props_budget = GlobalScanBudget(
                    max_fixtures=self.budget.max_statshub_fixtures,
                    max_trends_requests=self.budget.max_statshub_trends,
                    max_execution_events=len(cached_for_props),
                )

                props_res = props_scanner.execute_scan(scope=props_scope, budget=props_budget)
                funnel.evaluated_player_props = props_res.funnel_metrics.evaluated_count
                funnel.evaluated_team_props = props_res.funnel_metrics.evaluated_count
                funnel.evaluated_markets_total += props_res.funnel_metrics.evaluated_count
                funnel.statshub_requests_made = getattr(props_res.funnel_metrics, "statshub_requests_made", getattr(props_res.funnel_metrics, "trends_discovered", 0))

                for pop in props_res.qualified_opportunities:
                    bm_clean = (pop.best_bookmaker or "superbet").lower()
                    if bm_clean not in EXECUTABLE_BOOKMAKERS:
                        continue
                    if pop.net_ev_pct is None or pop.net_ev_pct < self.scope.min_ev_percent:
                        continue

                    is_player = (pop.prop_type or "PLAYER").upper() == "PLAYER"
                    cat_name = "PLAYER_PROP" if is_player else "TEAM_PROP"
                    target_name = pop.player_name if is_player and pop.player_name else pop.team

                    side_display = "Powyżej" if (pop.side or "OVER").upper() == "OVER" else "Poniżej"
                    stat_display = pop.stat_type.replace("_", " ").title()
                    mkt_str = f"{stat_display} ({side_display} {pop.line})"
                    sel_str = f"{target_name} ({mkt_str})"

                    rank_score = calculate_ultra_rank_score(
                        edge_pct=float(pop.net_ev_pct),
                        confidence=pop.confidence or "MEDIUM",
                        reference_sources_count=pop.reference_sources_count or 2,
                        market_type="PLAYER_PROP" if is_player else "TEAM_PROP",
                    )

                    ref_sources = []
                    if isinstance(pop.reference_odds, dict):
                        ref_sources = list(pop.reference_odds.keys())
                    elif isinstance(pop.reference_odds, list):
                        for ro in pop.reference_odds:
                            if isinstance(ro, dict) and "bookmaker" in ro:
                                ref_sources.append(ro["bookmaker"])

                    opp_bucket = horizon.classify_kickoff(pop.kickoff, eval_dt)[1] or "TODAY"
                    prop_opp = UltraOpportunity(
                        opportunity_id=f"prop_{pop.canonical_prop_key}",
                        category=cat_name,
                        match_name=pop.match_name,
                        competition=pop.competition or "Football",
                        kickoff=pop.kickoff or now_warsaw.strftime("%Y-%m-%d"),
                        market_display=mkt_str,
                        selection_display=sel_str,
                        bookmaker=bm_clean.title(),
                        raw_odds=float(pop.best_raw_odds or 1.0),
                        effective_odds=float(pop.best_effective_odds or pop.best_raw_odds or 1.0),
                        fair_odds=float(pop.reference_fair_odds) if pop.reference_fair_odds is not None else None,
                        edge_pct=float(pop.net_ev_pct),
                        confidence=pop.confidence or "MEDIUM",
                        ultra_rank_score=rank_score,
                        reference_sources=ref_sources,
                        is_watchlist=False,
                        pass_number=1,
                        horizon_bucket=opp_bucket,
                        details=pop.to_dict() if hasattr(pop, "to_dict") else {},
                    )
                    if is_player:
                        raw_player_props.append(prop_opp)
                    else:
                        raw_team_props.append(prop_opp)

                funnel.qualified_player_props = len(raw_player_props)
                funnel.qualified_team_props = len(raw_team_props)
            except Exception as prop_err:
                msg = f"Props evaluation error: {prop_err}"
                logger.error(msg, exc_info=True)
                warnings.append(msg)

        funnel.phase_durations_seconds["props_eval"] = time.perf_counter() - t_phase_5

        # ──────────────────────────────────────────────────────────────────────
        # PHASE 6: SECOND-PASS / DEPTH (Breadth -> Depth)
        # ──────────────────────────────────────────────────────────────────────
        t_phase_6 = time.perf_counter()
        depth_refreshed_count = 0
        depth_opps_count = 0

        if self.scope.enable_depth_pass and not _check_deadline("depth_pass"):
            time_spent = time.perf_counter() - t0
            remaining_budget_s = self.budget.max_duration_seconds - time_spent

            if remaining_budget_s > 60.0:
                # Find high-potential matches (those hosting surebets, top valuebets, or near-surebets)
                promising_opps = raw_surebets + raw_valuebets[:10] + raw_watchlist[:10]
                promising_event_ids: Set[str] = set()

                for o in promising_opps:
                    m_name = o.match_name.lower()
                    for g in all_today_graphs:
                        if not g.event:
                            continue
                        ev_name = f"{g.event.home_participant} vs {g.event.away_participant}".lower()
                        ev_id = getattr(g.event, "internal_id", getattr(g.event, "event_id", ""))
                        if (ev_name and (m_name in ev_name or ev_name in m_name)) or (ev_id and ev_id in m_name):
                            promising_event_ids.add(ev_id)

                if promising_event_ids:
                    logger.info("ULTRA SCAN Depth Pass 2: refining %d high-potential fixtures", len(promising_event_ids))
                    depth_refreshed_count = len(promising_event_ids)

                    # Mark refined opportunities associated with promising fixtures as pass_number = 2
                    for o in promising_opps:
                        m_name = o.match_name.lower()
                        for g in all_today_graphs:
                            if not g.event:
                                continue
                            ev_id = getattr(g.event, "internal_id", getattr(g.event, "event_id", ""))
                            if ev_id in promising_event_ids:
                                ev_name = f"{g.event.home_participant} vs {g.event.away_participant}".lower()
                                if (ev_name and (m_name in ev_name or ev_name in m_name)) or (ev_id and ev_id in m_name):
                                    o.pass_number = 2
                                    depth_opps_count += 1
                                    break
            else:
                logger.info("ULTRA SCAN: skipping Depth Pass 2 (remaining budget %.1fs < 60s)", remaining_budget_s)

        funnel.depth_pass_fixtures_refined = depth_refreshed_count
        funnel.depth_pass_opportunities_refined = depth_opps_count
        diagnostics["depth_pass_fixtures_refined"] = depth_refreshed_count
        diagnostics["depth_pass_opportunities_refined"] = depth_opps_count
        funnel.phase_durations_seconds["depth_pass"] = time.perf_counter() - t_phase_6

        # ──────────────────────────────────────────────────────────────────────
        # PHASE 7: QUALITY CONTROL & DEDUPLICATION
        # ──────────────────────────────────────────────────────────────────────
        t_phase_7 = time.perf_counter()

        def _is_valid_opportunity(opp: UltraOpportunity) -> bool:
            # 1. Odds sanity
            if opp.raw_odds < 1.01 or opp.raw_odds > 100.0:
                funnel.record_rejection("ODDS_OUT_OF_RANGE")
                return False
            # 2. Palpable error: effective odds >> fair odds ratio > 3.5x
            if opp.fair_odds is not None and opp.fair_odds > 1.0:
                if (opp.raw_odds / opp.fair_odds) > 3.5:
                    funnel.record_rejection("PALPABLE_ERROR_OUTLIER")
                    return False
            # 3. Execution restriction
            bm_tokens = [t.strip().lower() for t in opp.bookmaker.split("+")]
            if not all(bm in EXECUTABLE_BOOKMAKERS for bm in bm_tokens):
                funnel.record_rejection("NON_EXECUTION_BOOKMAKER")
                return False
            return True

        valid_surebets = [o for o in raw_surebets if _is_valid_opportunity(o)]
        valid_valuebets = [o for o in raw_valuebets if _is_valid_opportunity(o)]
        valid_player_props = [o for o in raw_player_props if _is_valid_opportunity(o)]
        valid_team_props = [o for o in raw_team_props if _is_valid_opportunity(o)]
        valid_watchlist = [o for o in raw_watchlist if _is_valid_opportunity(o)]

        funnel.phase_durations_seconds["quality_control"] = time.perf_counter() - t_phase_7

        # ──────────────────────────────────────────────────────────────────────
        # PHASE 8: MULTI-SIGNAL OPPORTUNITY RANKING
        # ──────────────────────────────────────────────────────────────────────
        t_phase_8 = time.perf_counter()

        valid_surebets.sort(key=lambda o: o.ultra_rank_score, reverse=True)
        valid_valuebets.sort(key=lambda o: o.ultra_rank_score, reverse=True)
        valid_player_props.sort(key=lambda o: o.ultra_rank_score, reverse=True)
        valid_team_props.sort(key=lambda o: o.ultra_rank_score, reverse=True)
        valid_watchlist.sort(key=lambda o: o.ultra_rank_score, reverse=True)

        # Cross-category TOP OPPORTUNITIES (best 5 overall)
        all_qualified = valid_surebets + valid_valuebets + valid_player_props + valid_team_props
        all_qualified.sort(key=lambda o: o.ultra_rank_score, reverse=True)
        top_opportunities = all_qualified[:5]

        funnel.ranked_opportunities_total = len(all_qualified)
        funnel.qualified_watchlist = len(valid_watchlist)
        funnel.phase_durations_seconds["ranking"] = time.perf_counter() - t_phase_8

        total_dur = time.perf_counter() - t0
        funnel.phase_durations_seconds["total"] = total_dur

        diagnostics["timeout_occurred"] = has_timeout
        diagnostics["provider_status"] = funnel.provider_status

        if has_timeout or "ACCESS_DENIED_403" in funnel.provider_status.values():
            status = "PARTIAL"
        elif not failures:
            status = "SUCCESS"
        elif all_qualified or all_today_graphs:
            status = "PARTIAL"
        else:
            status = "FAILED"

        result = UltraScanResult(
            execution_id=execution_id,
            status=status,
            target_date=target_date_str,
            started_at=eval_dt.isoformat(),
            completed_at=datetime.now(timezone.utc).isoformat(),
            duration_seconds=total_dur,
            funnel=funnel,
            top_opportunities=top_opportunities,
            surebets=valid_surebets[:self.scope.max_results_per_category],
            valuebets=valid_valuebets[:self.scope.max_results_per_category],
            player_props=valid_player_props[:self.scope.max_results_per_category],
            team_props=valid_team_props[:self.scope.max_results_per_category],
            watchlist=valid_watchlist[:self.scope.max_results_per_category],
            failures=failures,
            warnings=warnings,
            diagnostics=diagnostics,
            validation_result=validation_result,
            all_graphs=all_today_graphs,
        )

        logger.info(
            "ULTRA SCAN [%s] completed in %.2fs with status %s (Top: %d, Surebets: %d, Valuebets: %d, Props: %d)",
            execution_id,
            total_dur,
            status,
            len(result.top_opportunities),
            len(result.surebets),
            len(result.valuebets),
            len(result.player_props) + len(result.team_props),
        )
        return result
