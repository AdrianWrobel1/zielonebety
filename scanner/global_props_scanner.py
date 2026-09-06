"""
Stage 5: Global Player / Team Props Scanner

Orchestrates multi-fixture search across upcoming matches to find the best
realistically available Player Props and Team Props valuebets on Superbet and Betclic.

Core Architecture:
1. Scan Scope & Budget Enforcement:
   - Configurable time horizon, tournaments, stat types, and props scope (PLAYER / TEAM / ALL).
   - Strict bounded execution budget (max fixtures, max trends requests, max execution events).
2. Multi-Fixture Trend Discovery & Deduplication:
   - Discovers upcoming fixtures and retrieves StatsHub player/team trends.
   - Deduplicates trends by canonical prop key before heavy execution matching.
3. Bounded Execution Market Acquisition:
   - Queries Polish execution bookmakers (Superbet, Betclic) strictly for selected fixtures.
   - Reuses existing providers and caches without bypassing rate limits.
4. Exact Matching & Reference Value Evaluation:
   - Reuses PropExecutionMatcher, TeamPropExecutionMatcher, and PropsValueEvaluator.
   - Strict separation of funnel stages (DISCOVERED -> MATCHED -> REFERENCE_VALID -> EVALUATED -> QUALIFIED / REJECTED).
5. Deterministic Ranking & Provenance:
   - Ranks qualified opportunities primarily by Net EV % DESC with deterministic secondary tie-breaking.
   - Preserves complete provenance and reason codes for all evaluated and rejected candidates.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
import logging
import time
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from core.tax_engine import TaxEngine, get_tax_engine
from normalization.base_normalizer import NormalizedGraph
from normalization.betclic_normalizer import BetclicNormalizer
from normalization.competitions import resolve_canonical_competition
from normalization.superbet_normalizer import SuperbetNormalizer
from normalization.market_identity import normalize_player_name
from normalization.identity import normalize_team_name
from normalization.aliases import resolve_canonical_team_name
from normalization.props_taxonomy import (
    PropScope,
    PropMetric,
    CanonicalPropDefinition,
    CANONICAL_PROPS_REGISTRY,
    PLAYER_PROPS_DEFINITIONS,
    TEAM_PROPS_DEFINITIONS,
    resolve_prop_stat,
)
from domain.models import (
    generate_deterministic_player_prop_id,
    generate_deterministic_team_prop_id,
)
from providers.betclic.provider import BetclicProvider
from providers.statshub.client import StatsHubClient
from providers.statshub.config import StatsHubConfig
from providers.statshub.models import (
    StatsHubFixture,
    StatsHubPlayerStat,
    StatsHubBookmakerOdds,
    StatsHubPropResult,
)
from providers.statshub.provider import StatsHubProvider
from providers.statshub.team_models import (
    StatsHubTeamFixture,
    StatsHubTeamStat,
    StatsHubTeamBookmakerOdds,
    StatsHubTeamPropResult,
)

from providers.statshub.team_provider import StatsHubTeamPropsProvider
from providers.superbet.provider import SuperbetProvider
from scanner.execution_providers import ExecutionMarketEngine, NormalizedExecutionQuote
from scanner.prop_execution_matcher import (
    PropExecutionMatcher,
    PropOddsComparison,
    MatchingReasonCode as PlayerMatchingReasonCode,
)
from scanner.props_value_evaluator import (
    PropsValueEvaluator,
    PropsEvaluationConfig,
    ValueEvaluationReasonCode,
    PropsValueEvaluationResult,
)
from scanner.team_prop_execution_matcher import (
    TeamPropExecutionMatcher,
    TeamPropOddsComparison,
    MatchingReasonCode as TeamMatchingReasonCode,
)

logger = logging.getLogger("scanner.global_props_scanner")


@dataclass(frozen=True)
class GlobalScanScope:
    """Configurable scope parameters for global props scanning."""
    time_horizon_days: int = 7
    tournaments: Optional[List[str]] = None
    props_scope: str = "ALL"  # "PLAYER", "TEAM", "ALL"
    stat_types: Optional[List[str]] = None  # e.g. ["shots", "shots_on_target", "fouls", "corners", "cards"]
    min_ev_percent: float = 3.0
    max_results: int = 50
    venue_filter: Optional[str] = None
    start_of_day: Optional[int] = None
    end_of_day: Optional[int] = None
    fixture_ids: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "time_horizon_days": self.time_horizon_days,
            "tournaments": self.tournaments,
            "props_scope": self.props_scope,
            "stat_types": self.stat_types,
            "min_ev_percent": self.min_ev_percent,
            "max_results": self.max_results,
            "venue_filter": self.venue_filter,
            "start_of_day": self.start_of_day,
            "end_of_day": self.end_of_day,
            "fixture_ids": self.fixture_ids,
        }


@dataclass(frozen=True)
class GlobalScanBudget:
    """Configurable budget and safety ceilings for global scan execution."""
    max_fixtures: int = 30
    max_trends_requests: int = 20
    max_execution_events: int = 20
    auto_paginate_statshub: bool = True
    max_prop_results_per_stat: int = 500

    def to_dict(self) -> Dict[str, Any]:
        return {
            "max_fixtures": self.max_fixtures,
            "max_trends_requests": self.max_trends_requests,
            "max_execution_events": self.max_execution_events,
            "auto_paginate_statshub": self.auto_paginate_statshub,
            "max_prop_results_per_stat": self.max_prop_results_per_stat,
        }


@dataclass
class GlobalScanFunnelMetrics:
    """Auditable funnel and observability telemetry for the scan cycle."""
    fixtures_discovered: int = 0
    fixtures_selected: int = 0
    trends_discovered: int = 0
    trends_scoped_to_selected: int = 0
    trends_deduplicated: int = 0
    matched_props: int = 0
    match_uncertain: int = 0
    unmatched_events: int = 0
    unmatched_players_teams: int = 0
    unmatched_markets: int = 0
    line_mismatches: int = 0
    selection_mismatches: int = 0
    unavailable_polish_odds: int = 0
    invalid_stale_reference: int = 0
    evaluated_count: int = 0
    qualified_count: int = 0
    rejected_count: int = 0
    rejection_breakdown: Dict[str, int] = field(default_factory=dict)
    execution_time_ms: float = 0.0
    cache_hits: int = 0
    cache_misses: int = 0

    def increment_rejection(self, reason_code: str) -> None:
        self.rejection_breakdown[reason_code] = self.rejection_breakdown.get(reason_code, 0) + 1

    def to_dict(self) -> Dict[str, Any]:
        return {
            "fixtures_discovered": self.fixtures_discovered,
            "fixtures_selected": self.fixtures_selected,
            "trends_discovered": self.trends_discovered,
            "trends_scoped_to_selected": self.trends_scoped_to_selected,
            "trends_deduplicated": self.trends_deduplicated,
            "matched_props": self.matched_props,
            "match_uncertain": self.match_uncertain,
            "unmatched_events": self.unmatched_events,
            "unmatched_players_teams": self.unmatched_players_teams,
            "unmatched_markets": self.unmatched_markets,
            "line_mismatches": self.line_mismatches,
            "selection_mismatches": self.selection_mismatches,
            "unavailable_polish_odds": self.unavailable_polish_odds,
            "invalid_stale_reference": self.invalid_stale_reference,
            "evaluated_count": self.evaluated_count,
            "qualified_count": self.qualified_count,
            "rejected_count": self.rejected_count,
            "rejection_breakdown": self.rejection_breakdown,
            "execution_time_ms": self.execution_time_ms,
            "cache_hits": self.cache_hits,
            "cache_misses": self.cache_misses,
        }


@dataclass
class GlobalScanOpportunity:
    """Represents a fully evaluated and ranked prop opportunity."""
    canonical_prop_key: str
    prop_type: str  # "PLAYER" or "TEAM"
    player_name: Optional[str]
    team: str
    opponent: str
    match_name: str
    fixture_id: str
    competition: str
    kickoff: str
    stat_type: str
    line: float
    side: str
    period: str
    scope: str
    participant_role: Optional[str]

    # Reference Calculation
    reference_consensus_odds: Optional[float]
    reference_fair_probability: Optional[float]
    reference_fair_odds: Optional[float]
    reference_sources_count: int
    reference_odds: List[Dict[str, Any]]

    # Polish Execution Odds
    best_bookmaker: Optional[str]
    best_raw_odds: Optional[float]
    best_effective_odds: Optional[float]
    net_ev_pct: Optional[float]
    gross_ev_pct: Optional[float]
    value_edge_pp: Optional[float]
    is_valuebet: bool
    status: str  # "QUALIFIED", "POSITIVE_EDGE", "EVALUATED", "REFERENCE_VALID", "REJECTED"
    reason_code: str
    reason: Optional[str]

    # Trend Metadata (Preserved for auxiliary UI context without changing EV)
    trend_hits: Optional[int]
    trend_window: Optional[int]
    hit_rate_pct: float
    stat_average: Optional[float]
    last_5_avg: Optional[float]
    last_10_avg: Optional[float]
    execution_odds: Dict[str, Any]
    provenance: Dict[str, Any]

    # ValueBet Contract Fields (Etap A)
    confidence: Optional[str] = "MEDIUM"  # "HIGH", "MEDIUM", "LOW", or None if missing data
    superbet_odds: Optional[float] = None
    betclic_odds: Optional[float] = None
    superbet_status: str = "UNAVAILABLE"
    betclic_status: str = "UNAVAILABLE"
    reference_probability_pct: Optional[float] = None
    action: str = "NO VALUE"  # "VALUE BET", "POSITIVE EDGE", "REFERENCE ONLY", "NO VALUE"
    tier: int = 2
    position: Optional[str] = None

    # Polish Quote Discrepancy Opportunity Fields
    is_discrepancy: bool = False
    relative_price_difference_pct: Optional[float] = None
    odds_difference: Optional[float] = None
    lower_executable_odds: Optional[float] = None
    lower_executable_bookmaker: Optional[str] = None
    discrepancy_details: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "canonical_prop_key": self.canonical_prop_key,
            "prop_type": self.prop_type,
            "player_name": self.player_name,
            "team": self.team,
            "opponent": self.opponent,
            "match_name": self.match_name,
            "fixture_id": self.fixture_id,
            "competition": self.competition,
            "kickoff": self.kickoff,
            "stat_type": self.stat_type,
            "line": self.line,
            "side": self.side,
            "period": self.period,
            "scope": self.scope,
            "participant_role": self.participant_role,
            "position": self.position,
            "reference_consensus_odds": self.reference_consensus_odds,
            "reference_fair_probability": self.reference_fair_probability,
            "reference_fair_odds": self.reference_fair_odds,
            "reference_sources_count": self.reference_sources_count,
            "reference_odds": self.reference_odds,
            "best_bookmaker": self.best_bookmaker,
            "best_raw_odds": self.best_raw_odds,
            "best_effective_odds": self.best_effective_odds,
            "net_ev_pct": self.net_ev_pct,
            "gross_ev_pct": self.gross_ev_pct,
            "value_edge_pp": self.value_edge_pp,
            "is_valuebet": self.is_valuebet,
            "status": self.status,
            "reason_code": self.reason_code,
            "reason": self.reason,
            "trend_hits": self.trend_hits,
            "trend_window": self.trend_window,
            "hit_rate_pct": self.hit_rate_pct,
            "stat_average": self.stat_average,
            "last_5_avg": self.last_5_avg,
            "last_10_avg": self.last_10_avg,
            "execution_odds": self.execution_odds,
            "provenance": self.provenance,
            "confidence": self.confidence,
            "superbet_odds": self.superbet_odds,
            "betclic_odds": self.betclic_odds,
            "superbet_status": self.superbet_status,
            "betclic_status": self.betclic_status,
            "reference_probability_pct": self.reference_probability_pct,
            "action": self.action,
            "fair_odds": self.reference_fair_odds,
            "tier": self.tier,
            "is_discrepancy": self.is_discrepancy,
            "relative_price_difference_pct": self.relative_price_difference_pct,
            "odds_difference": self.odds_difference,
            "lower_executable_odds": self.lower_executable_odds,
            "lower_executable_bookmaker": self.lower_executable_bookmaker,
            "discrepancy_details": self.discrepancy_details,
        }


@dataclass
class DiscoveredFixtureCandidate:
    """Represents a discovered fixture candidate with market coverage from Polish bookmakers."""
    home_team: str
    away_team: str
    match_name: str
    competition: str = ""
    kickoff: Optional[str] = None
    superbet_market_count: int = 0
    betclic_market_count: int = 0
    total_market_coverage: int = 0
    bookmaker_count: int = 0
    tier: int = 2
    priority_score: float = 0.0
    superbet_item: Optional[Any] = None
    betclic_item: Optional[Any] = None
    normalized_graphs: List[NormalizedGraph] = field(default_factory=list)
    fixture_id: Optional[str] = None


def get_tier_weight(tier: int) -> float:
    """Returns deterministic weighting factor for competition tier.

    Tier 0 (Top UEFA/Intl cups): 1.50
    Tier 1 (Big 5 European leagues + Ekstraklasa): 1.25
    Tier 2 (Standard leagues & cups): 1.00
    Tier 3: 0.75
    Tier 4+: 0.50 (minimum floor 0.50)
    """
    if tier <= 0:
        return 1.50
    elif tier == 1:
        return 1.25
    elif tier == 2:
        return 1.00
    elif tier == 3:
        return 0.75
    else:
        return max(0.50, 1.00 - (tier - 2) * 0.25)


def calculate_fixture_priority_score(
    tier: int,
    total_market_coverage: int,
    kickoff: Optional[str] = None,
    now: Optional[datetime] = None,
) -> float:
    """Calculates deterministic composite priority score combining league tier, market coverage, and kickoff proximity."""
    try:
        from scanner.adaptive_scanning import calculate_adaptive_fixture_priority
        return calculate_adaptive_fixture_priority(
            tier=tier,
            market_coverage=total_market_coverage,
            kickoff=kickoff,
            now=now,
        )
    except Exception:
        return round(float(total_market_coverage) * get_tier_weight(tier), 4)


def _extract_teams_from_name(name: str) -> Tuple[str, str]:
    if not name:
        return "", ""
    for sep in ("·", " - ", " – ", " — ", " vs ", " VS "):
        if sep in name:
            parts = name.split(sep, 1)
            return parts[0].strip(), parts[1].strip()
    return name.strip(), ""


def _extract_market_count_from_raw(raw: Any) -> int:
    if not isinstance(raw, dict):
        return 0
    for k in ("totalMarkets", "marketsCount", "total_markets_count", "openMarketCount", "markets_count"):
        val = raw.get(k)
        if val is not None:
            try:
                cnt = int(val)
                if cnt > 0:
                    return cnt
            except (ValueError, TypeError):
                pass
    if "markets" in raw and isinstance(raw["markets"], list):
        return len(raw["markets"])
    if "odds" in raw and isinstance(raw["odds"], list):
        return len(raw["odds"])
    return 0


def _extract_market_count(item: Any) -> int:
    if hasattr(item, "markets") and isinstance(item.markets, list) and item.markets:
        return len(item.markets)
    if hasattr(item, "metadata") and isinstance(item.metadata, dict):
        raw = item.metadata.get("raw", {})
        cnt = _extract_market_count_from_raw(raw)
        if cnt > 0:
            return cnt
    if isinstance(item, dict):
        cnt = _extract_market_count_from_raw(item)
        if cnt > 0:
            return cnt
    return 0


def allocate_shared_execution_budget(
    sb_candidates: Sequence[Any],
    bc_candidates: Sequence[Any],
    max_budget: int,
) -> Tuple[List[Any], List[Any]]:
    """Deterministically allocates shared execution event slots between Superbet and Betclic.

    Guarantees:
    1. Global Ceiling: len(sb_alloc) + len(bc_alloc) <= max_budget.
    2. Shared Participation: If both providers have candidates, neither starves the other.
    3. No Artificial Reservation: If one provider has 0 or fewer candidates than its base quota,
       the remaining budget is fully available to the other provider up to max_budget.
    4. Determinism: Allocation is pure, symmetrical, and repeatable.
    """
    if max_budget <= 0:
        return [], []

    total_sb = len(sb_candidates)
    total_bc = len(bc_candidates)

    if total_sb == 0:
        return [], list(bc_candidates[:max_budget])
    if total_bc == 0:
        return list(sb_candidates[:max_budget]), []

    # Both providers have candidates: compute base quotas
    half = max_budget // 2
    remainder = max_budget % 2

    # Give any odd remainder slot to Superbet
    sb_target = half + remainder
    bc_target = half

    # If Superbet has fewer candidates than its target, surplus goes to Betclic
    if total_sb < sb_target:
        sb_allocated = total_sb
        bc_target += (sb_target - total_sb)
    else:
        sb_allocated = sb_target

    # Betclic takes up to its target
    bc_allocated = min(total_bc, bc_target)

    # If Betclic didn't use its full quota, remaining budget goes to Superbet
    remaining_for_sb = max_budget - bc_allocated
    sb_allocated = min(total_sb, remaining_for_sb)

    return list(sb_candidates[:sb_allocated]), list(bc_candidates[:bc_allocated])


@dataclass
class GlobalScanResult:
    """Complete result of a global props scan cycle."""
    scope: Dict[str, Any]
    budget: Dict[str, Any]
    status: str  # "SUCCESS", "PARTIAL", "EMPTY", "FAILED"
    funnel_metrics: GlobalScanFunnelMetrics
    qualified_opportunities: List[GlobalScanOpportunity]
    diagnostic_candidates: List[GlobalScanOpportunity]
    duration_ms: float
    selected_fixtures: List[Dict[str, Any]] = field(default_factory=list)
    scanned_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    scan_trace: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "scope": self.scope,
            "budget": self.budget,
            "status": self.status,
            "funnel_metrics": self.funnel_metrics.to_dict(),
            "qualified_count": len(self.qualified_opportunities),
            "diagnostic_count": len(self.diagnostic_candidates),
            "total_qualified_matching_filter": len(self.qualified_opportunities),
            "selected_fixtures": self.selected_fixtures,
            "qualified_opportunities": [o.to_dict() for o in self.qualified_opportunities],
            "diagnostic_candidates": [o.to_dict() for o in self.diagnostic_candidates],
            "duration_ms": self.duration_ms,
            "scanned_at": self.scanned_at,
            "scan_trace": self.scan_trace,
        }


def _build_ranking_sort_key(opp: GlobalScanOpportunity, tier: Optional[int] = None) -> Tuple[Any, ...]:
    """Multi-factor transparent ranking sort key per Section 14:
    1. Valid valuation boolean (valid valuation before un-evaluated/invalid)
    2. Net EV % DESC
    3. Quality / Confidence DESC (HIGH > MEDIUM > LOW > None)
    4. Reference quality DESC (reference sources count DESC)
    5. Competition priority (Tier 0, 1, 2, ...)
    6. Deterministic tie-breakers (kickoff, match, player/team, stat, line, best_bookmaker, canonical_key)
    """
    has_valid_val = 0 if (opp.net_ev_pct is not None and opp.reference_fair_probability is not None) else 1
    ev_neg = -opp.net_ev_pct if opp.net_ev_pct is not None else 9999.0
    conf_map = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    conf_rank = conf_map.get(str(opp.confidence).upper(), 3) if opp.confidence else 3
    ref_neg = -opp.reference_sources_count
    c_tier = tier if tier is not None else getattr(opp, "tier", 2)
    return (
        has_valid_val,
        ev_neg,
        conf_rank,
        ref_neg,
        c_tier,
        opp.kickoff or "",
        opp.match_name or "",
        opp.player_name or opp.team or "",
        opp.stat_type,
        opp.line,
        opp.best_bookmaker or "",
        opp.canonical_prop_key,
    )


def _build_opportunity_contract_data(
    val_res: PropsValueEvaluationResult,
    odds_comparison: Any,
    trend_window: int,
) -> Dict[str, Any]:
    """Derives auditable ValueBet Contract fields for execution and quality dimensions."""
    sb_quote = odds_comparison.execution_odds.get("Superbet") if odds_comparison and hasattr(odds_comparison, "execution_odds") and odds_comparison.execution_odds else None
    bc_quote = odds_comparison.execution_odds.get("Betclic") if odds_comparison and hasattr(odds_comparison, "execution_odds") and odds_comparison.execution_odds else None

    sb_status = getattr(sb_quote, "status", "UNAVAILABLE") if sb_quote else "UNAVAILABLE"
    bc_status = getattr(bc_quote, "status", "UNAVAILABLE") if bc_quote else "UNAVAILABLE"

    sb_odds = float(sb_quote.decimal_odds) if sb_quote and sb_status == "AVAILABLE" and sb_quote.decimal_odds and float(sb_quote.decimal_odds) > 1.0 else None
    bc_odds = float(bc_quote.decimal_odds) if bc_quote and bc_status == "AVAILABLE" and bc_quote.decimal_odds and float(bc_quote.decimal_odds) > 1.0 else None

    fair_p = val_res.reference_calculation.fair_probability if val_res and val_res.reference_calculation else None
    ref_prob_pct = round(fair_p * 100.0, 1) if fair_p is not None else None

    ref_count = len(val_res.reference_calculation.used_sources) if val_res and val_res.reference_calculation else 0
    match_conf = getattr(odds_comparison, "match_confidence", 1.0) or 0.0

    has_polish_odds = (sb_odds is not None and sb_status == "AVAILABLE") or (bc_odds is not None and bc_status == "AVAILABLE")

    if not val_res or not val_res.reference_calculation.is_valid or not has_polish_odds:
        confidence = None
    elif ref_count >= 2 and trend_window >= 7 and match_conf >= 0.85:
        confidence = "HIGH"
    elif ref_count >= 1 and trend_window >= 4 and match_conf >= 0.70:
        confidence = "MEDIUM"
    else:
        confidence = "LOW"

    if val_res.overall_status == "QUALIFIED":
        action = "VALUE BET"
    elif val_res.overall_status == "POSITIVE_EDGE":
        action = "POSITIVE EDGE"
    elif val_res.overall_status == "REFERENCE_VALID":
        action = "REFERENCE ONLY"
    else:
        action = "NO VALUE"

    is_disc = bool(getattr(odds_comparison, "is_discrepancy", False)) if odds_comparison else False
    rel_diff = getattr(odds_comparison, "relative_price_difference_pct", None) if odds_comparison else None
    odds_diff = getattr(odds_comparison, "odds_difference", None) if odds_comparison else None
    lower_odds = getattr(odds_comparison, "lower_executable_odds", None) if odds_comparison else None
    lower_bm = getattr(odds_comparison, "lower_executable_bookmaker", None) if odds_comparison else None
    disc_details = getattr(odds_comparison, "discrepancy_details", None) if odds_comparison else None

    return {
        "confidence": confidence,
        "superbet_odds": sb_odds,
        "betclic_odds": bc_odds,
        "superbet_status": sb_status,
        "betclic_status": bc_status,
        "reference_probability_pct": ref_prob_pct,
        "action": action,
        "is_discrepancy": is_disc,
        "relative_price_difference_pct": rel_diff,
        "odds_difference": odds_diff,
        "lower_executable_odds": lower_odds,
        "lower_executable_bookmaker": lower_bm,
        "discrepancy_details": disc_details,
    }


class GlobalPropsScanner:
    """Unified multi-fixture Global Props Scanner Engine."""

    def __init__(
        self,
        value_evaluator: Optional[PropsValueEvaluator] = None,
        tax_engine: Optional[TaxEngine] = None,
        cached_execution_events: Optional[List[NormalizedGraph]] = None,
        snapshot_recorder: Optional[Any] = None,
    ) -> None:
        self.tax_engine = tax_engine or get_tax_engine()
        self.value_evaluator = value_evaluator or PropsValueEvaluator(tax_engine=self.tax_engine)
        self.cached_execution_events = cached_execution_events or []
        self.snapshot_recorder = snapshot_recorder

    def execute_scan(
        self,
        scope: Optional[GlobalScanScope] = None,
        budget: Optional[GlobalScanBudget] = None,
    ) -> GlobalScanResult:
        """Executes end-to-end bounded global scan across Player Props and Team Props."""
        start_time = time.perf_counter()
        scan_scope = scope or GlobalScanScope()
        scan_budget = budget or GlobalScanBudget()
        funnel = GlobalScanFunnelMetrics()

        from orchestration.profiler import (
            ScanExecutionProfiler,
            get_current_scan_profiler,
            set_current_scan_profiler,
        )

        scan_scope_mode = str(scan_scope.props_scope).upper()
        mode_tag = "team_props" if scan_scope_mode == "TEAM" else ("player_props" if scan_scope_mode == "PLAYER" else "team_props")

        profiler = ScanExecutionProfiler(scan_mode="NORMAL", scan_type=mode_tag)
        set_current_scan_profiler(profiler, set_global=False)

        profiler.start_phase("fixture_discovery", counters={"tournaments": len(scan_scope.tournaments or [])})

        # Configure Value Evaluator threshold from scope
        eval_cfg = PropsEvaluationConfig(
            min_value_percent=Decimal(str(scan_scope.min_ev_percent)),
        )
        self.value_evaluator.config = eval_cfg

        # 1. Bookmaker Event Discovery & Market Coverage Assessment
        candidate_fixtures: List[DiscoveredFixtureCandidate] = []

        def _add_or_merge_candidate(
            home: str,
            away: str,
            comp: str = "",
            kickoff: Optional[str] = None,
            sb_item: Optional[Any] = None,
            bc_item: Optional[Any] = None,
            graph: Optional[NormalizedGraph] = None,
            market_count: int = 0,
            provider_name: str = "",
            fixture_id: Optional[str] = None,
        ) -> Optional[DiscoveredFixtureCandidate]:
            if not home and not away:
                return None

            comp_res = resolve_canonical_competition(raw_name=comp, home_team=home, away_team=away)
            cand_tier = comp_res.tier if comp_res.confidence > 0.0 else 2
            if scan_scope.tournaments:
                for t_filter in scan_scope.tournaments:
                    if t_filter.lower() in (comp or "").lower() or t_filter.lower() in comp_res.canonical_name.lower():
                        cand_tier = 0
                        break

            for c in candidate_fixtures:
                f_match, _ = PropExecutionMatcher.is_fixture_match(
                    home, away, c.home_team, c.away_team, kickoff, c.kickoff)
                if f_match:
                    if provider_name == "superbet":
                        c.superbet_market_count = max(c.superbet_market_count, market_count)
                        if sb_item:
                            c.superbet_item = sb_item
                    elif provider_name == "betclic":
                        c.betclic_market_count = max(c.betclic_market_count, market_count)
                        if bc_item:
                            c.betclic_item = bc_item
                    if graph:
                        c.normalized_graphs.append(graph)
                    if not c.kickoff and kickoff:
                        c.kickoff = kickoff
                    if not c.competition and comp:
                        c.competition = comp
                    if not c.fixture_id and fixture_id:
                        c.fixture_id = fixture_id

                    if comp and (not c.competition or c.tier == 2):
                        c.competition = comp
                        comp_res_update = resolve_canonical_competition(raw_name=comp, home_team=c.home_team, away_team=c.away_team)
                        if comp_res_update.confidence > 0.0:
                            c.tier = min(c.tier, comp_res_update.tier)
                    if cand_tier < c.tier:
                        c.tier = cand_tier

                    sb_active = 1 if (c.superbet_market_count > 0 or c.superbet_item is not None) else 0
                    bc_active = 1 if (c.betclic_market_count > 0 or c.betclic_item is not None) else 0
                    c.bookmaker_count = sb_active + bc_active if (sb_active or bc_active) else (len(c.normalized_graphs) if c.normalized_graphs else 1)
                    c.total_market_coverage = c.superbet_market_count + c.betclic_market_count + sum(len(g.markets) for g in c.normalized_graphs if not c.superbet_item and not c.betclic_item)
                    c.priority_score = calculate_fixture_priority_score(c.tier, c.total_market_coverage, kickoff=c.kickoff)
                    return c

            # Create new candidate
            sb_active = 1 if (market_count > 0 or sb_item is not None) and provider_name == "superbet" else 0
            bc_active = 1 if (market_count > 0 or bc_item is not None) and provider_name == "betclic" else 0
            new_c = DiscoveredFixtureCandidate(
                home_team=home,
                away_team=away,
                match_name=f"{home} vs {away}",
                competition=comp or "",
                kickoff=kickoff,
                superbet_market_count=market_count if provider_name == "superbet" else 0,
                betclic_market_count=market_count if provider_name == "betclic" else 0,
                total_market_coverage=market_count,
                bookmaker_count=sb_active + bc_active if (sb_active or bc_active) else (1 if (graph or provider_name in ("superbet", "betclic")) else 0),
                tier=cand_tier,
                priority_score=calculate_fixture_priority_score(cand_tier, market_count, kickoff=kickoff),
                superbet_item=sb_item,
                betclic_item=bc_item,
                normalized_graphs=[graph] if graph else [],
                fixture_id=fixture_id,
            )
            if graph and not new_c.superbet_market_count and not new_c.betclic_market_count:
                new_c.total_market_coverage = len(graph.markets) if hasattr(graph, "markets") and graph.markets else 0
                new_c.priority_score = calculate_fixture_priority_score(cand_tier, new_c.total_market_coverage, kickoff=new_c.kickoff)
            candidate_fixtures.append(new_c)
            return new_c

        # A. Collect from cached execution events if present
        if self.cached_execution_events:
            for g in self.cached_execution_events:
                g_home = getattr(g.event, "home_participant", "") if hasattr(g, "event") and g.event else ""
                g_away = getattr(g.event, "away_participant", "") if hasattr(g, "event") and g.event else ""
                g_comp = getattr(g.competition, "name", "") if hasattr(g, "competition") and g.competition else ""
                g_kickoff = getattr(g.event, "scheduled_start", None) if hasattr(g, "event") and g.event else None
                g_mkt_cnt = len(g.markets) if hasattr(g, "markets") and g.markets else 0
                _add_or_merge_candidate(
                    home=g_home,
                    away=g_away,
                    comp=g_comp,
                    kickoff=str(g_kickoff) if g_kickoff else None,
                    graph=g,
                    market_count=g_mkt_cnt,
                    provider_name="cached",
                )
        else:
            # B. Live Bookmaker Event Discovery
            # 1. Superbet Discovery
            try:
                sb_p = SuperbetProvider()
                sb_disc = sb_p.discover()
                for it in sb_disc:
                    it_home = it.home_team if hasattr(it, "home_team") and it.home_team else _extract_teams_from_name(getattr(it, "match_name", ""))[0]
                    it_away = it.away_team if hasattr(it, "away_team") and it.away_team else _extract_teams_from_name(getattr(it, "match_name", ""))[1]
                    mkt_cnt = _extract_market_count(it)
                    _add_or_merge_candidate(
                        home=it_home,
                        away=it_away,
                        comp=getattr(it, "competition_name", "") or "",
                        kickoff=getattr(it, "start_time", None),
                        sb_item=it,
                        market_count=mkt_cnt,
                        provider_name="superbet",
                    )
            except Exception as exc:
                logger.warning(f"Global scan Superbet discovery notice: {exc}")

            # 2. Betclic Discovery
            try:
                bc_p = BetclicProvider()
                bc_disc = bc_p.discover()
                for it in bc_disc:
                    it_home, it_away = _extract_teams_from_name(getattr(it, "name", ""))
                    mkt_cnt = _extract_market_count(it)
                    _add_or_merge_candidate(
                        home=it_home,
                        away=it_away,
                        comp=getattr(it, "competition_name", "") or "",
                        kickoff=getattr(it, "start_time", None),
                        bc_item=it,
                        market_count=mkt_cnt,
                        provider_name="betclic",
                    )
            except Exception as exc:
                logger.warning(f"Global scan Betclic discovery notice: {exc}")

        # Deterministic Ranking of Candidate Fixtures by Composite Priority Score (Tier + Market Coverage)
        def _fixture_sort_key(c: DiscoveredFixtureCandidate) -> Tuple[float, int, int, int, float, str]:
            kickoff_ts = 9999999999.0
            if c.kickoff:
                try:
                    from normalization.identity import parse_kickoff_to_utc
                    dt = parse_kickoff_to_utc(c.kickoff)
                    if dt:
                        kickoff_ts = dt.timestamp()
                except Exception:
                    pass
            return (
                -c.priority_score,
                c.tier,
                -c.total_market_coverage,
                -c.bookmaker_count,
                kickoff_ts,
                c.match_name,
            )

        # Resolve StatsHub fixture IDs for candidate fixtures prior to trend discovery
        try:
            sh_client = StatsHubClient()
            sh_upcoming_fixtures = sh_client.discover_upcoming_fixtures(days_ahead=scan_scope.time_horizon_days)
            if sh_upcoming_fixtures:
                for sh_fix in sh_upcoming_fixtures:
                    fid = sh_fix.get("fixture_id")
                    f_home = sh_fix.get("home_team", "")
                    f_away = sh_fix.get("away_team", "")
                    f_comp = sh_fix.get("competition", "")
                    f_kickoff = sh_fix.get("kickoff")
                    if not fid or not f_home or not f_away:
                        continue

                    matched_cand = None
                    for c in candidate_fixtures:
                        if c.fixture_id and c.fixture_id == fid:
                            matched_cand = c
                            break
                        f_match, _ = PropExecutionMatcher.is_fixture_match(
                            f_home, f_away, c.home_team, c.away_team, f_kickoff, c.kickoff)
                        if f_match:
                            matched_cand = c
                            break

                    if matched_cand:
                        matched_cand.fixture_id = fid
                        if not matched_cand.competition and f_comp:
                            matched_cand.competition = f_comp
                        if not matched_cand.kickoff and f_kickoff:
                            matched_cand.kickoff = str(f_kickoff)
        except Exception as sh_disc_err:
            logger.warning(f"StatsHub fixture discovery notice: {sh_disc_err}")

        ranked_candidates = sorted(candidate_fixtures, key=_fixture_sort_key)
        selected_candidates = ranked_candidates[:scan_budget.max_fixtures]

        # Collect resolved fixture IDs for match-scoped trends discovery
        resolved_fids = [c.fixture_id for c in selected_candidates if c.fixture_id]
        if scan_scope.fixture_ids:
            for fid in scan_scope.fixture_ids.split(","):
                f_clean = fid.strip()
                if f_clean and f_clean not in resolved_fids:
                    resolved_fids.append(f_clean)
        fids_param = ",".join(resolved_fids) if resolved_fids else ""
        profiler.finish_phase("fixture_discovery", counters={"candidates_discovered": len(candidate_fixtures)})

        # 2. StatsHub Player / Team Trends Discovery
        profiler.start_phase("trends_discovery", counters={"scope": scan_scope.props_scope})
        raw_player_trends: List[StatsHubPropResult] = []
        raw_team_trends: List[StatsHubTeamPropResult] = []
        discovered_fixtures: Dict[str, StatsHubFixture] = {}
        trends_requests_made = 0

        # A. Player Props Discovery (scoped across full canonical taxonomy)
        if scan_scope.props_scope in ("PLAYER", "ALL"):
            try:
                target_player_stats: List[str] = []
                if scan_scope.stat_types:
                    for st in scan_scope.stat_types:
                        resolved = resolve_prop_stat(st, scope=PropScope.PLAYER)
                        if resolved and resolved.scope == PropScope.PLAYER:
                            if resolved.statshub_stat_type not in target_player_stats:
                                target_player_stats.append(resolved.statshub_stat_type)
                        else:
                            clean_st = st.lower().replace("player_", "").replace(" ", "_")
                            if clean_st in ("shots", "shots_on_target", "goals", "assists", "fouls", "cards", "passes", "tackles"):
                                if clean_st not in target_player_stats:
                                    target_player_stats.append(clean_st)

                if not target_player_stats:
                    target_player_stats = [d.statshub_stat_type for d in PLAYER_PROPS_DEFINITIONS]

                hunter_supported = {"shots", "fouls", "goals", "wasfouled"}

                # If no fixture IDs available initially, use Hunter for supported stats as discovery anchor
                if not fids_param:
                    hunter_stats = [s for s in target_player_stats if s in hunter_supported] or ["shots", "fouls", "goals"]
                    for st in hunter_stats:
                        if trends_requests_made >= scan_budget.max_trends_requests:
                            break
                        trends_requests_made += 1
                        cfg = StatsHubConfig(
                            stat=st,
                            stat_type=st,
                            days_ahead=scan_scope.time_horizon_days,
                            auto_paginate=scan_budget.auto_paginate_statshub,
                            max_prop_results=scan_budget.max_prop_results_per_stat,
                        )
                        if scan_scope.start_of_day:
                            cfg.start_of_day = scan_scope.start_of_day
                        if scan_scope.end_of_day:
                            cfg.end_of_day = scan_scope.end_of_day
                        if scan_scope.tournaments:
                            cfg.tournaments = ",".join(scan_scope.tournaments)
                        if scan_scope.venue_filter:
                            cfg.venue_filter = scan_scope.venue_filter

                        p_provider = StatsHubProvider(config=cfg)
                        p_res = p_provider.run()
                        raw_player_trends.extend(p_res.parsed_objects)

                        for obj in p_res.parsed_objects:
                            fix = obj.player_stat.fixture
                            if fix and fix.fixture_id and fix.fixture_id not in discovered_fixtures:
                                discovered_fixtures[fix.fixture_id] = fix

                    if discovered_fixtures:
                        fids_param = ",".join(str(fid) for fid in list(discovered_fixtures.keys())[:scan_budget.max_fixtures])

                # Fetch remaining player stats via player_trends API with fixture IDs
                if fids_param:
                    already_fetched_stats = set(p.player_stat.stat_type.lower() for p in raw_player_trends) if raw_player_trends else set()
                    stats_for_trends = [s for s in target_player_stats if s.lower() not in already_fetched_stats]
                    for st in stats_for_trends:
                        if trends_requests_made >= scan_budget.max_trends_requests:
                            break
                        trends_requests_made += 1
                        cfg = StatsHubConfig(
                            mode="player_trends",
                            games=fids_param,
                            stat=st,
                            stat_type=st,
                            days_ahead=scan_scope.time_horizon_days,
                            auto_paginate=False,
                            max_prop_results=scan_budget.max_prop_results_per_stat,
                        )
                        if scan_scope.tournaments:
                            cfg.tournaments = ",".join(scan_scope.tournaments)

                        p_provider = StatsHubProvider(config=cfg)
                        p_res = p_provider.run()
                        raw_player_trends.extend(p_res.parsed_objects)

                        for obj in p_res.parsed_objects:
                            fix = obj.player_stat.fixture
                            if fix and fix.fixture_id and fix.fixture_id not in discovered_fixtures:
                                discovered_fixtures[fix.fixture_id] = fix
            except Exception as exc:
                logger.error(f"Global scan player trends acquisition error: {exc}")

        # B. Team Props Discovery (true match-level discovery via team-trends API)
        if scan_scope.props_scope in ("TEAM", "ALL"):
            try:
                team_games_param = fids_param or (scan_scope.fixture_ids or "")
                target_team_stats: List[str] = []
                if scan_scope.stat_types:
                    for st in scan_scope.stat_types:
                        resolved = resolve_prop_stat(st, scope=PropScope.TEAM)
                        if resolved and resolved.scope == PropScope.TEAM:
                            if resolved.statshub_stat_type not in target_team_stats:
                                target_team_stats.append(resolved.statshub_stat_type)
                        else:
                            clean_st = st.lower().replace("team_", "").replace(" ", "_")
                            if clean_st in ("shots", "shots_on_target", "goals", "fouls", "cards", "corners", "offsides"):
                                if clean_st not in target_team_stats:
                                    target_team_stats.append(clean_st)

                if not target_team_stats:
                    target_team_stats = [d.statshub_stat_type for d in TEAM_PROPS_DEFINITIONS]

                if team_games_param:
                    for st in target_team_stats:
                        if trends_requests_made >= scan_budget.max_trends_requests:
                            break
                        trends_requests_made += 1
                        t_cfg = StatsHubConfig(
                            mode="team_trends",
                            games=team_games_param,
                            stat=st,
                            stat_type=st,
                            days_ahead=scan_scope.time_horizon_days,
                            auto_paginate=False,
                            max_prop_results=scan_budget.max_prop_results_per_stat,
                        )
                        t_provider = StatsHubTeamPropsProvider(config=t_cfg)
                        t_res = t_provider.run()
                        raw_team_trends.extend(t_res.parsed_objects)

                        for obj in t_res.parsed_objects:
                            t_fix = obj.team_stat.fixture
                            if t_fix and t_fix.fixture_id and t_fix.fixture_id not in discovered_fixtures:
                                discovered_fixtures[t_fix.fixture_id] = StatsHubFixture(
                                    fixture_id=t_fix.fixture_id,
                                    event_internal_id=t_fix.event_internal_id,
                                    home_team=t_fix.home_team,
                                    away_team=t_fix.away_team,
                                    competition=t_fix.competition,
                                    kickoff=t_fix.kickoff,
                                    slug=t_fix.slug,
                                )
            except Exception as exc:
                logger.error(f"Global scan team trends acquisition error: {exc}")

        profiler.finish_phase("trends_discovery", counters={"requests_made": trends_requests_made, "trends_found": len(raw_player_trends) + len(raw_team_trends)})

        # Target Candidates Construction & Market Coverage Prioritization
        profiler.start_phase("fixture_prioritization", counters={"max_fixtures": scan_budget.max_fixtures})
        target_candidates: List[DiscoveredFixtureCandidate] = []
        if discovered_fixtures:
            seen_cand_ids = set()
            for fix in discovered_fixtures.values():
                matched_cand = None
                for c in candidate_fixtures:
                    if c.fixture_id and c.fixture_id == fix.fixture_id:
                        matched_cand = c
                        break
                    f_match, _ = PropExecutionMatcher.is_fixture_match(
                        fix.home_team, fix.away_team, c.home_team, c.away_team,
                        fix.kickoff, c.kickoff)
                    if f_match:
                        matched_cand = c
                        break

                if matched_cand:
                    if id(matched_cand) in seen_cand_ids:
                        continue
                    seen_cand_ids.add(id(matched_cand))
                    matched_cand.fixture_id = fix.fixture_id
                    matched_cand.home_team = fix.home_team
                    matched_cand.away_team = fix.away_team
                    matched_cand.match_name = f"{fix.home_team} vs {fix.away_team}"
                    matched_cand.competition = fix.competition or matched_cand.competition
                    matched_cand.kickoff = fix.kickoff or matched_cand.kickoff
                    comp_res_fix = resolve_canonical_competition(
                        raw_name=matched_cand.competition,
                        home_team=matched_cand.home_team,
                        away_team=matched_cand.away_team,
                    )
                    if comp_res_fix.confidence > 0.0:
                        matched_cand.tier = min(matched_cand.tier, comp_res_fix.tier)
                    if scan_scope.tournaments:
                        for t_filter in scan_scope.tournaments:
                            if t_filter.lower() in (matched_cand.competition or "").lower() or t_filter.lower() in comp_res_fix.canonical_name.lower():
                                matched_cand.tier = 0
                                break
                    matched_cand.priority_score = calculate_fixture_priority_score(matched_cand.tier, matched_cand.total_market_coverage, kickoff=matched_cand.kickoff)
                    target_candidates.append(matched_cand)
                else:
                    comp_res_fix = resolve_canonical_competition(
                        raw_name=fix.competition or "",
                        home_team=fix.home_team,
                        away_team=fix.away_team,
                    )
                    tier_val = comp_res_fix.tier if comp_res_fix.confidence > 0.0 else 2
                    if scan_scope.tournaments:
                        for t_filter in scan_scope.tournaments:
                            if t_filter.lower() in (fix.competition or "").lower() or t_filter.lower() in comp_res_fix.canonical_name.lower():
                                tier_val = 0
                                break
                    new_c = DiscoveredFixtureCandidate(
                        home_team=fix.home_team,
                        away_team=fix.away_team,
                        match_name=f"{fix.home_team} vs {fix.away_team}",
                        competition=fix.competition or "",
                        kickoff=fix.kickoff,
                        fixture_id=fix.fixture_id,
                        total_market_coverage=0,
                        bookmaker_count=0,
                        tier=tier_val,
                        priority_score=calculate_fixture_priority_score(tier_val, 0, kickoff=fix.kickoff),
                    )
                    target_candidates.append(new_c)
        else:
            target_candidates = list(candidate_fixtures)

        ranked_candidates = sorted(target_candidates, key=_fixture_sort_key)
        selected_candidates = ranked_candidates[:scan_budget.max_fixtures]

        funnel.fixtures_discovered = len(target_candidates)
        funnel.fixtures_selected = len(selected_candidates)
        funnel.trends_discovered = len(raw_player_trends) + len(raw_team_trends)

        # 3. Filter trends strictly to selected TOP fixtures
        bounded_player_trends: List[StatsHubPropResult] = []
        for p in raw_player_trends:
            ps = p.player_stat
            if ps.fixture:
                for c in selected_candidates:
                    if c.fixture_id and ps.fixture.fixture_id and str(c.fixture_id) == str(ps.fixture.fixture_id):
                        bounded_player_trends.append(p)
                        break
                    f_match, _ = PropExecutionMatcher.is_fixture_match(
                        ps.fixture.home_team, ps.fixture.away_team, c.home_team, c.away_team,
                        getattr(ps.fixture, "kickoff", None), c.kickoff)
                    if f_match:
                        bounded_player_trends.append(p)
                        break

        bounded_team_trends: List[StatsHubTeamPropResult] = []
        for t in raw_team_trends:
            ts = t.team_stat
            if ts.fixture:
                for c in selected_candidates:
                    if c.fixture_id and ts.fixture.fixture_id and str(c.fixture_id) == str(ts.fixture.fixture_id):
                        bounded_team_trends.append(t)
                        break
                    f_match, _ = PropExecutionMatcher.is_fixture_match(
                        ts.fixture.home_team, ts.fixture.away_team, c.home_team, c.away_team,
                        getattr(ts.fixture, "kickoff", None), c.kickoff)
                    if f_match:
                        bounded_team_trends.append(t)
                        break

        funnel.trends_scoped_to_selected = len(bounded_player_trends) + len(bounded_team_trends)

        # 4. Deduplication across acquisition paths
        dedup_player_map: Dict[str, StatsHubPropResult] = {}
        for p in bounded_player_trends:
            ps = p.player_stat
            norm_p = normalize_player_name(ps.player_name) or (ps.player_name or "").strip().lower()
            key = generate_deterministic_player_prop_id(
                event_id=ps.fixture.fixture_id if ps.fixture else "0",
                player_name_norm=norm_p,
                stat_type=ps.stat_type,
                line=float(ps.line) if ps.line is not None else 0.5,
                side=(ps.odds_type or "OVER").upper(),
            )
            if key not in dedup_player_map:
                dedup_player_map[key] = p

        dedup_team_map: Dict[str, StatsHubTeamPropResult] = {}
        for t in bounded_team_trends:
            ts = t.team_stat
            raw_t_norm, t_tok = normalize_team_name(ts.team_name)
            canon_t, _ = resolve_canonical_team_name(raw_t_norm, t_tok)
            norm_t = canon_t or raw_t_norm or (ts.team_name or "").strip().lower()
            key = generate_deterministic_team_prop_id(
                event_id=ts.fixture.fixture_id if ts.fixture else "0",
                team_name_norm=norm_t,
                stat_type=ts.stat_type,
                line=float(ts.line) if ts.line is not None else 0.5,
                side=(ts.odds_type or "OVER").upper(),
                participant_role=ts.participant_role,
            )
            if key not in dedup_team_map:
                dedup_team_map[key] = t

        funnel.trends_deduplicated = len(dedup_player_map) + len(dedup_team_map)
        profiler.finish_phase("fixture_prioritization", counters={"fixtures_selected": len(selected_candidates), "trends_deduplicated": funnel.trends_deduplicated})

        # 5. Bounded Polish Execution Acquisition (Superbet & Betclic)
        profiler.start_phase("execution_acquisition", counters={"max_events": scan_budget.max_execution_events})
        exec_engine = ExecutionMarketEngine()
        normalized_graphs: List[NormalizedGraph] = []

        if self.cached_execution_events:
            for c in selected_candidates:
                for g in c.normalized_graphs:
                    normalized_graphs.append(g)
                    if len(normalized_graphs) >= scan_budget.max_execution_events:
                        break
                if len(normalized_graphs) >= scan_budget.max_execution_events:
                    break
        else:
            # Live acquisition for selected candidates under balanced shared global execution budget
            sb_all_cand = [c.superbet_item for c in selected_candidates if c.superbet_item is not None]
            bc_all_cand = [c.betclic_item for c in selected_candidates if c.betclic_item is not None]
            sb_matched_items, bc_matched_items = allocate_shared_execution_budget(
                sb_candidates=sb_all_cand,
                bc_candidates=bc_all_cand,
                max_budget=scan_budget.max_execution_events,
            )

            # A. Superbet
            try:
                sb_matched_ids = [it.event_id for it in sb_matched_items if hasattr(it, "event_id") and it.event_id]
                if sb_matched_items:
                    sb_p = SuperbetProvider()
                    sb_p.configure_full_market_acquisition(event_ids=sb_matched_ids)
                    sb_p.set_discovered_items(sb_matched_items)
                    sb_run = sb_p.run()
                    sb_norm = SuperbetNormalizer()
                    for ev in sb_run.parsed_objects:
                        if ev.event_id in sb_matched_ids:
                            normalized_graphs.append(sb_norm.normalize_event(ev))
            except Exception as exc:
                logger.warning(f"Global scan Superbet acquisition notice: {exc}")

            # B. Betclic
            try:
                bc_matched_ids = [it.provider_event_id for it in bc_matched_items if hasattr(it, "provider_event_id") and it.provider_event_id]
                if bc_matched_items:
                    bc_p = BetclicProvider()
                    bc_p.configure_full_market_acquisition(event_ids=bc_matched_ids)
                    bc_p.set_discovered_items(bc_matched_items)
                    bc_run = bc_p.run()
                    bc_norm = BetclicNormalizer()
                    for ev in bc_run.parsed_objects:
                        if ev.provider_event_id in bc_matched_ids:
                            normalized_graphs.append(bc_norm.normalize_event(ev))
            except Exception as exc:
                logger.warning(f"Global scan Betclic acquisition notice: {exc}")

        # Enforce strict ceiling on normalized graphs passed to downstream processing
        normalized_graphs = normalized_graphs[:scan_budget.max_execution_events]
        profiler.finish_phase("execution_acquisition", counters={"normalized_graphs": len(normalized_graphs)})

        # Extract normalized execution quotes (Player Props & Team Props)
        profiler.start_phase("quote_extraction")
        all_player_quotes = exec_engine.extract_quotes_from_graphs(normalized_graphs)
        all_team_quotes = exec_engine.extract_team_quotes_from_graphs(normalized_graphs)
        profiler.finish_phase("quote_extraction", counters={"player_quotes": len(all_player_quotes), "team_quotes": len(all_team_quotes)})

        player_matcher = PropExecutionMatcher(
            canonical_events=normalized_graphs,
            normalized_quotes=all_player_quotes,
        )
        team_matcher = TeamPropExecutionMatcher(
            canonical_events=normalized_graphs,
            normalized_quotes=all_team_quotes,
        )

        qualified_opportunities: List[GlobalScanOpportunity] = []
        diagnostic_candidates: List[GlobalScanOpportunity] = []

        # 4. Process Player Props
        profiler.start_phase("matching_and_evaluation", counters={"player_trends": len(dedup_player_map), "team_trends": len(dedup_team_map)})
        for canonical_key, p_res in dedup_player_map.items():
            ps = p_res.player_stat
            fix = ps.fixture
            target_line = float(ps.line) if ps.line is not None else 0.5
            target_side = (ps.odds_type or "OVER").upper()

            odds_comparison = player_matcher.match_statshub_prop(
                statshub_prop=p_res,
                cached_execution_events=normalized_graphs,
                normalized_quotes=all_player_quotes,
            )

            # Funnel Matching Telemetry
            if odds_comparison.execution_status == "BETTABLE":
                funnel.matched_props += 1
            elif odds_comparison.execution_status == "MATCH_UNCERTAIN" or odds_comparison.primary_reason_code in (
                PlayerMatchingReasonCode.EVENT_AMBIGUOUS.value,
                PlayerMatchingReasonCode.PLAYER_AMBIGUOUS.value,
            ):
                funnel.match_uncertain += 1
            else:
                if odds_comparison.primary_reason_code == PlayerMatchingReasonCode.EVENT_UNMATCHED.value:
                    funnel.unmatched_events += 1
                elif odds_comparison.primary_reason_code == PlayerMatchingReasonCode.PLAYER_UNMATCHED.value:
                    funnel.unmatched_players_teams += 1
                elif odds_comparison.primary_reason_code == PlayerMatchingReasonCode.MARKET_UNMATCHED.value:
                    funnel.unmatched_markets += 1
                elif odds_comparison.primary_reason_code == PlayerMatchingReasonCode.LINE_MISMATCH.value:
                    funnel.line_mismatches += 1
                elif odds_comparison.primary_reason_code == PlayerMatchingReasonCode.SELECTION_MISMATCH.value:
                    funnel.selection_mismatches += 1

            # Value Evaluation
            val_res = self.value_evaluator.evaluate_player_prop(
                statshub_prop=p_res,
                odds_comparison=odds_comparison,
            )

            # Determine precise candidate lifecycle status and reason code (Stage B.1)
            if not val_res.reference_calculation.is_valid:
                effective_status = "REJECTED"
                effective_reason_code = "REFERENCE_GAP"
                effective_reason = val_res.primary_reason or "Reference odds missing or insufficient"
                funnel.invalid_stale_reference += 1
            elif odds_comparison.execution_status != "BETTABLE":
                effective_status = "REJECTED"
                effective_reason_code = "MATCHING_FAILURE"
                effective_reason = f"Execution matching failed: {odds_comparison.primary_reason_code}"
            elif val_res.overall_status == "QUALIFIED":
                effective_status = "QUALIFIED"
                effective_reason_code = "QUALIFIED"
                effective_reason = val_res.primary_reason
            elif val_res.primary_reason_code == ValueEvaluationReasonCode.POLISH_ODDS_UNAVAILABLE.value:
                effective_status = val_res.overall_status
                effective_reason_code = "POLISH_ODDS_UNAVAILABLE"
                effective_reason = val_res.primary_reason
                funnel.unavailable_polish_odds += 1
            elif val_res.primary_reason_code == ValueEvaluationReasonCode.BELOW_VALUE_THRESHOLD.value or val_res.overall_status in ("POSITIVE_EDGE", "EVALUATED"):
                effective_status = val_res.overall_status
                effective_reason_code = "BELOW_VALUE_THRESHOLD"
                effective_reason = val_res.primary_reason
            else:
                effective_status = val_res.overall_status
                effective_reason_code = val_res.primary_reason_code
                effective_reason = val_res.primary_reason

            funnel.evaluated_count += 1
            if effective_status == "QUALIFIED":
                funnel.qualified_count += 1
            else:
                funnel.rejected_count += 1
                funnel.increment_rejection(effective_reason_code)

            contract_data = _build_opportunity_contract_data(
                val_res=val_res,
                odds_comparison=odds_comparison,
                trend_window=ps.trend_window or ps.sample_size or 10,
            )

            # Resolve fixture competition tier
            fix_cand = None
            if fix:
                for c in selected_candidates:
                    if (c.fixture_id and c.fixture_id == fix.fixture_id) or PropExecutionMatcher.is_fixture_match(
                            fix.home_team, fix.away_team, c.home_team, c.away_team,
                            getattr(fix, "kickoff", None), c.kickoff)[0]:
                        fix_cand = c
                        break
            p_tier = fix_cand.tier if fix_cand else 2

            opportunity = GlobalScanOpportunity(
                canonical_prop_key=canonical_key,
                prop_type="PLAYER",
                player_name=ps.player_name,
                team=ps.team,
                opponent=ps.opponent,
                match_name=f"{fix.home_team} vs {fix.away_team}" if fix else "N/A",
                fixture_id=fix.fixture_id if fix else "0",
                competition=fix.competition if fix else "N/A",
                kickoff=fix.kickoff or "TBD" if fix else "TBD",
                stat_type=ps.stat_type.upper(),
                line=target_line,
                side=target_side,
                period="FULL_TIME",
                scope="PLAYER",
                participant_role=None,
                position=ps.position or None,
                reference_consensus_odds=val_res.reference_calculation.consensus_odds,
                reference_fair_probability=val_res.reference_calculation.fair_probability,
                reference_fair_odds=val_res.reference_calculation.fair_odds,
                reference_sources_count=len(val_res.reference_calculation.used_sources),
                reference_odds=val_res.reference_calculation.used_sources,
                best_bookmaker=val_res.best_bookmaker,
                best_raw_odds=val_res.best_raw_odds,
                best_effective_odds=val_res.best_effective_odds,
                net_ev_pct=val_res.best_net_ev_pct,
                gross_ev_pct=val_res.best_gross_ev_pct,
                value_edge_pp=round(val_res.bookmaker_evaluations[val_res.best_bookmaker].value_edge_pp, 2) if val_res.best_bookmaker and val_res.best_bookmaker in val_res.bookmaker_evaluations and val_res.bookmaker_evaluations[val_res.best_bookmaker].value_edge_pp is not None else None,
                is_valuebet=val_res.is_valuebet,
                status=effective_status,
                reason_code=effective_reason_code,
                reason=effective_reason,
                trend_hits=ps.trend_hits or ps.hit_rate_count,
                trend_window=ps.trend_window or ps.sample_size,
                hit_rate_pct=ps.hit_rate_pct,
                stat_average=ps.average,
                last_5_avg=ps.last_5_avg,
                last_10_avg=ps.last_10_avg,
                execution_odds={k: v.to_dict() for k, v in odds_comparison.execution_odds.items()},
                provenance=val_res.provenance,
                confidence=contract_data["confidence"],
                superbet_odds=contract_data["superbet_odds"],
                betclic_odds=contract_data["betclic_odds"],
                superbet_status=contract_data["superbet_status"],
                betclic_status=contract_data["betclic_status"],
                reference_probability_pct=contract_data["reference_probability_pct"],
                action=contract_data["action"],
                tier=p_tier,
                is_discrepancy=contract_data.get("is_discrepancy", False),
                relative_price_difference_pct=contract_data.get("relative_price_difference_pct"),
                odds_difference=contract_data.get("odds_difference"),
                lower_executable_odds=contract_data.get("lower_executable_odds"),
                lower_executable_bookmaker=contract_data.get("lower_executable_bookmaker"),
                discrepancy_details=contract_data.get("discrepancy_details"),
            )

            if effective_status == "QUALIFIED":
                qualified_opportunities.append(opportunity)
            else:
                diagnostic_candidates.append(opportunity)

            # Stage A.10 Pre-Match Snapshot Recording for Player Shots Over 0.5
            if (
                self.snapshot_recorder is not None
                and ps.stat_type.upper() == "SHOTS"
                and abs(target_line - 0.5) < 0.01
                and target_side == "OVER"
            ):
                try:
                    self.snapshot_recorder.record_snapshot(
                        player_name=ps.player_name,
                        home_team=fix.home_team if fix and fix.home_team else "",
                        away_team=fix.away_team if fix and fix.away_team else "",
                        competition=fix.competition if fix and fix.competition else "",
                        stat_type="SHOTS",
                        line=0.5,
                        direction="OVER",
                        kickoff=fix.kickoff if fix else None,
                        observed_at=datetime.now(timezone.utc),
                        hit_rate=ps.hit_rate_pct,
                        hits=ps.trend_hits or ps.hit_rate_count,
                        sample_size=ps.trend_window or ps.sample_size,
                        stat_average=ps.average,
                        position_role=ps.position,
                        recent_matches=ps.historical_matches,
                        reference_probability=val_res.reference_calculation.fair_probability,
                        reference_fair_odds=val_res.reference_calculation.fair_odds,
                        reference_consensus_odds=val_res.reference_calculation.consensus_odds,
                        reference_bookmaker_count=len(val_res.reference_calculation.used_sources),
                        reference_odds_sources=val_res.reference_calculation.used_sources,
                        superbet_odds=contract_data.get("superbet_odds"),
                        betclic_odds=contract_data.get("betclic_odds"),
                        execution_odds={k: v.to_dict() for k, v in odds_comparison.execution_odds.items()} if odds_comparison and odds_comparison.execution_odds else None,
                    )
                except Exception as snap_err:
                    logger.warning(f"Player shots snapshot recording notice: {snap_err}")

        # 5. Process Team Props
        for canonical_key, t_res in dedup_team_map.items():
            ts = t_res.team_stat
            fix = ts.fixture
            target_line = float(ts.line) if ts.line is not None else 0.5
            target_side = (ts.odds_type or "OVER").upper()

            odds_comparison = team_matcher.match_statshub_team_prop(
                statshub_team_prop=t_res,
                cached_execution_events=normalized_graphs,
                normalized_quotes=all_team_quotes,
            )


            if odds_comparison.execution_status == "BETTABLE":
                funnel.matched_props += 1
            elif odds_comparison.execution_status == "MATCH_UNCERTAIN" or odds_comparison.primary_reason_code in (
                TeamMatchingReasonCode.EVENT_AMBIGUOUS.value,
                TeamMatchingReasonCode.TEAM_AMBIGUOUS.value,
            ):
                funnel.match_uncertain += 1
            else:
                if odds_comparison.primary_reason_code == TeamMatchingReasonCode.EVENT_UNMATCHED.value:
                    funnel.unmatched_events += 1
                elif odds_comparison.primary_reason_code == TeamMatchingReasonCode.TEAM_UNMATCHED.value:
                    funnel.unmatched_players_teams += 1
                elif odds_comparison.primary_reason_code == TeamMatchingReasonCode.MARKET_UNMATCHED.value:
                    funnel.unmatched_markets += 1
                elif odds_comparison.primary_reason_code == TeamMatchingReasonCode.LINE_MISMATCH.value:
                    funnel.line_mismatches += 1
                elif odds_comparison.primary_reason_code == TeamMatchingReasonCode.SELECTION_MISMATCH.value:
                    funnel.selection_mismatches += 1


            val_res = self.value_evaluator.evaluate_team_prop(
                statshub_team_prop=t_res,
                odds_comparison=odds_comparison,
            )

            # Determine precise candidate lifecycle status and reason code (Stage B.1)
            if not val_res.reference_calculation.is_valid:
                effective_status = "REJECTED"
                effective_reason_code = "REFERENCE_GAP"
                effective_reason = val_res.primary_reason or "Reference odds missing or insufficient"
                funnel.invalid_stale_reference += 1
            elif odds_comparison.execution_status != "BETTABLE":
                effective_status = "REJECTED"
                effective_reason_code = "MATCHING_FAILURE"
                effective_reason = f"Execution matching failed: {odds_comparison.primary_reason_code}"
            elif val_res.overall_status == "QUALIFIED":
                effective_status = "QUALIFIED"
                effective_reason_code = "QUALIFIED"
                effective_reason = val_res.primary_reason
            elif val_res.primary_reason_code == ValueEvaluationReasonCode.POLISH_ODDS_UNAVAILABLE.value:
                effective_status = val_res.overall_status
                effective_reason_code = "POLISH_ODDS_UNAVAILABLE"
                effective_reason = val_res.primary_reason
                funnel.unavailable_polish_odds += 1
            elif val_res.primary_reason_code == ValueEvaluationReasonCode.BELOW_VALUE_THRESHOLD.value or val_res.overall_status in ("POSITIVE_EDGE", "EVALUATED"):
                effective_status = val_res.overall_status
                effective_reason_code = "BELOW_VALUE_THRESHOLD"
                effective_reason = val_res.primary_reason
            else:
                effective_status = val_res.overall_status
                effective_reason_code = val_res.primary_reason_code
                effective_reason = val_res.primary_reason

            funnel.evaluated_count += 1
            if effective_status == "QUALIFIED":
                funnel.qualified_count += 1
            else:
                funnel.rejected_count += 1
                funnel.increment_rejection(effective_reason_code)

            t_contract_data = _build_opportunity_contract_data(
                val_res=val_res,
                odds_comparison=odds_comparison,
                trend_window=ts.trend_window or ts.sample_size or 10,
            )

            # Resolve fixture competition tier
            fix_cand = None
            if fix:
                for c in selected_candidates:
                    if (c.fixture_id and c.fixture_id == fix.fixture_id) or PropExecutionMatcher.is_fixture_match(
                            fix.home_team, fix.away_team, c.home_team, c.away_team,
                            getattr(fix, "kickoff", None), c.kickoff)[0]:
                        fix_cand = c
                        break
            t_tier = fix_cand.tier if fix_cand else 2

            opportunity = GlobalScanOpportunity(
                canonical_prop_key=canonical_key,
                prop_type="TEAM",
                player_name=None,
                team=ts.team_name,
                opponent=ts.opponent_name,
                match_name=f"{fix.home_team} vs {fix.away_team}" if fix else "N/A",
                fixture_id=fix.fixture_id if fix else "0",
                competition=fix.competition if fix else "N/A",
                kickoff=fix.kickoff or "TBD" if fix else "TBD",
                stat_type=ts.stat_type.upper(),
                line=target_line,
                side=target_side,
                period="FULL_TIME",
                scope="TEAM",
                participant_role=ts.participant_role,
                position=None,
                reference_consensus_odds=val_res.reference_calculation.consensus_odds,
                reference_fair_probability=val_res.reference_calculation.fair_probability,
                reference_fair_odds=val_res.reference_calculation.fair_odds,
                reference_sources_count=len(val_res.reference_calculation.used_sources),
                reference_odds=val_res.reference_calculation.used_sources,
                best_bookmaker=val_res.best_bookmaker,
                best_raw_odds=val_res.best_raw_odds,
                best_effective_odds=val_res.best_effective_odds,
                net_ev_pct=val_res.best_net_ev_pct,
                gross_ev_pct=val_res.best_gross_ev_pct,
                value_edge_pp=round(val_res.bookmaker_evaluations[val_res.best_bookmaker].value_edge_pp, 2) if val_res.best_bookmaker and val_res.best_bookmaker in val_res.bookmaker_evaluations and val_res.bookmaker_evaluations[val_res.best_bookmaker].value_edge_pp is not None else None,
                is_valuebet=val_res.is_valuebet,
                status=effective_status,
                reason_code=effective_reason_code,
                reason=effective_reason,
                trend_hits=ts.trend_hits or ts.hit_rate_count,
                trend_window=ts.trend_window or ts.sample_size,
                hit_rate_pct=ts.hit_rate_pct,
                stat_average=ts.average,
                last_5_avg=ts.last_5_avg,
                last_10_avg=ts.last_10_avg,
                execution_odds={k: v.to_dict() for k, v in odds_comparison.execution_odds.items()},
                provenance=val_res.provenance,
                confidence=t_contract_data["confidence"],
                superbet_odds=t_contract_data["superbet_odds"],
                betclic_odds=t_contract_data["betclic_odds"],
                superbet_status=t_contract_data["superbet_status"],
                betclic_status=t_contract_data["betclic_status"],
                reference_probability_pct=t_contract_data["reference_probability_pct"],
                action=t_contract_data["action"],
                tier=t_tier,
                is_discrepancy=t_contract_data.get("is_discrepancy", False),
                relative_price_difference_pct=t_contract_data.get("relative_price_difference_pct"),
                odds_difference=t_contract_data.get("odds_difference"),
                lower_executable_odds=t_contract_data.get("lower_executable_odds"),
                lower_executable_bookmaker=t_contract_data.get("lower_executable_bookmaker"),
                discrepancy_details=t_contract_data.get("discrepancy_details"),
            )

            if effective_status == "QUALIFIED":
                qualified_opportunities.append(opportunity)
            else:
                diagnostic_candidates.append(opportunity)

        profiler.finish_phase("matching_and_evaluation", counters={"matched_props": funnel.matched_props, "evaluated": funnel.evaluated_count, "qualified": funnel.qualified_count})

        # 6. Deterministic Multi-Factor Ranking (Stage B.1)
        profiler.start_phase("ranking", counters={"qualified": len(qualified_opportunities), "diagnostic": len(diagnostic_candidates)})
        qualified_ranked = sorted(qualified_opportunities, key=_build_ranking_sort_key)
        diagnostic_ranked = sorted(diagnostic_candidates, key=_build_ranking_sort_key)
        profiler.finish_phase("ranking", counters={"qualified_ranked": len(qualified_ranked), "diagnostic_ranked": len(diagnostic_ranked)})

        elapsed_ms = (time.perf_counter() - start_time) * 1000.0
        funnel.execution_time_ms = round(elapsed_ms, 2)

        status_str = "SUCCESS" if qualified_ranked else ("EMPTY" if not candidate_fixtures and not discovered_fixtures else "PARTIAL")

        selected_fixtures_info = [
            {
                "rank": idx + 1,
                "match_name": c.match_name,
                "home_team": c.home_team,
                "away_team": c.away_team,
                "competition": c.competition,
                "tier": c.tier,
                "superbet_market_count": c.superbet_market_count,
                "betclic_market_count": c.betclic_market_count,
                "total_market_coverage": c.total_market_coverage,
                "priority_score": c.priority_score,
                "fixture_id": c.fixture_id,
                "kickoff": c.kickoff,
            }
            for idx, c in enumerate(selected_candidates)
        ]

        # Commit persisted snapshots if recorder has an active repository session
        if self.snapshot_recorder is not None and getattr(self.snapshot_recorder, "repository", None) is not None:
            repo = self.snapshot_recorder.repository
            if hasattr(repo, "session") and repo.session is not None:
                try:
                    repo.session.commit()
                except Exception as commit_err:
                    logger.warning(f"Failed to commit snapshot repository session: {commit_err}")

        scan_trace_report = profiler.finish_scan()
        set_current_scan_profiler(None, set_global=False)

        return GlobalScanResult(
            scope=scan_scope.to_dict(),
            budget=scan_budget.to_dict(),
            status=status_str,
            funnel_metrics=funnel,
            qualified_opportunities=qualified_ranked,
            diagnostic_candidates=diagnostic_ranked,
            duration_ms=round(elapsed_ms, 2),
            selected_fixtures=selected_fixtures_info,
            scan_trace=scan_trace_report,
        )
