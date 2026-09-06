"""
Unified Opportunity Explorer Models & Presentation DTO Contract (Stage 20E + Stage 29 Value Bet Engine)

Implements the unified read-only aggregation layer:
SCAN -> COLLECT -> NORMALIZE -> COMPARE -> PRESENT

Standardizes representation across:
- PLAYER_PROP
- TEAM_PROP
- VALUEBET
- SUREBET
- BOOSTER

Core Principles:
- Single canonical presentation DTO for the unified explorer.
- Zero mathematical recalculation (underlying engines are single source of truth).
- Null fields are kept None/null and NOT artificially fabricated.
- Pure presentation and filtering layer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence, Union


class OpportunityType(str, Enum):
    """Canonical opportunity categories recognized by Unified Explorer."""
    PLAYER_PROP = "PLAYER_PROP"
    TEAM_PROP = "TEAM_PROP"
    VALUEBET = "VALUEBET"
    SUREBET = "SUREBET"
    BOOSTER = "BOOSTER"
    WATCHLIST = "WATCHLIST"
    QUOTE_COMPARISON = "QUOTE_COMPARISON"
    QUOTE_DISCREPANCY = "QUOTE_DISCREPANCY"


class UnifiedExecutionStatus(str, Enum):
    """Normalized execution status across engines."""
    VALUEBET = "VALUEBET"
    BETTABLE = "BETTABLE"
    AVAILABLE = "AVAILABLE"
    MARKET_AVAILABLE = "MARKET_AVAILABLE"
    QUOTE_COMPARISON = "QUOTE_COMPARISON"
    QUOTE_DISCREPANCY = "QUOTE_DISCREPANCY"
    REFERENCE_ONLY = "REFERENCE_ONLY"
    NO_EXECUTION_MARKET = "NO_EXECUTION_MARKET"
    NO_EXECUTION_ODDS = "NO_EXECUTION_ODDS"
    MATCH_UNCERTAIN = "MATCH_UNCERTAIN"
    EXPIRED = "EXPIRED"
    UNSUPPORTED = "UNSUPPORTED"


@dataclass(frozen=True)
class UnifiedOpportunityDTO:
    """Canonical Presentation DTO for Unified Opportunity Explorer.

    Strictly preserves engine-level truth:
    - Missing/non-applicable fields are None.
    - Raw bookmaker odds and net EV are distinguished cleanly.
    """
    id: str
    type: str  # PLAYER_PROP, TEAM_PROP, VALUEBET, SUREBET, BOOSTER
    source: str  # statshub, valuebets, surebets, etc.

    # Event & Entity dimensions (None if not applicable)
    player: Optional[str] = None
    team: Optional[str] = None
    opponent: Optional[str] = None
    event: Optional[str] = None
    kickoff: Optional[str] = None
    sport: str = "football"
    competition: Optional[str] = None
    market: Optional[str] = None
    line: Optional[float] = None
    side: Optional[str] = None

    # Pricing & Bookmakers
    reference_odds: Optional[float] = None
    execution_odds: Optional[float] = None
    best_bookmaker: Optional[str] = None
    all_bookmakers: List[str] = field(default_factory=list)

    # Specific Engine Edge metrics (None if not applicable)
    statistical_edge_pct: Optional[float] = None  # e.g. Hit rate vs implied prob (Player Prop)
    execution_edge_pct: Optional[float] = None    # e.g. Executable odds vs reference implied
    gross_ev_pct: Optional[float] = None          # Valuebet / Surebet gross edge / EV%
    net_ev_pct: Optional[float] = None            # Tax-adjusted net edge

    # Stage 29 Value Bet Engine Presentation Fields
    fair_odds: Optional[float] = None             # 1 / P_model
    model_probability_pct: Optional[float] = None # P_model * 100
    value_edge_pp: Optional[float] = None         # P_model * 100 - (1 / execution_odds) * 100
    is_valuebet: bool = False                     # True iff verified execution odds yield positive EV

    # Polish Bookmaker Quote Discrepancy Presentation Fields
    price_discrepancy_pct: Optional[float] = None # Relative price difference ((best / lower) - 1) * 100
    odds_difference: Optional[float] = None       # best_odds - lower_odds
    lower_execution_odds: Optional[float] = None  # Lower alternative executable price
    lower_bookmaker: Optional[str] = None         # Alternative bookmaker offering lower price

    # Quality & Ranking (Provided by source engine, None for unrated raw quotes)
    score: Optional[float] = None
    status: str = "AVAILABLE"
    quality_flags: List[str] = field(default_factory=list)

    # Canonical Competition Metadata & Top 5 Classification
    canonical_competition_id: Optional[str] = None
    is_top_5: bool = False

    # Lifecycle timestamps
    created_at: Optional[str] = None
    expires_at: Optional[str] = None

    # Engine-specific detailed payload
    details: Dict[str, Any] = field(default_factory=dict)

    @property
    def opportunity_type(self) -> str:
        """Alias for type to maintain dual contract between backend and frontend."""
        return self.type

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "opportunity_type": self.type,
            "source": self.source,
            "player": self.player,
            "team": self.team,
            "opponent": self.opponent,
            "event": self.event,
            "kickoff": self.kickoff,
            "sport": self.sport,
            "competition": self.competition,
            "canonical_competition_id": self.canonical_competition_id,
            "is_top_5": self.is_top_5,
            "market": self.market,
            "line": self.line,
            "side": self.side,
            "reference_odds": self.reference_odds,
            "execution_odds": self.execution_odds,
            "best_bookmaker": self.best_bookmaker,
            "all_bookmakers": self.all_bookmakers,
            "statistical_edge_pct": self.statistical_edge_pct,
            "execution_edge_pct": self.execution_edge_pct,
            "gross_ev_pct": self.gross_ev_pct,
            "net_ev_pct": self.net_ev_pct,
            "fair_odds": self.fair_odds,
            "model_probability_pct": self.model_probability_pct,
            "value_edge_pp": self.value_edge_pp,
            "is_valuebet": self.is_valuebet,
            "price_discrepancy_pct": self.price_discrepancy_pct,
            "odds_difference": self.odds_difference,
            "lower_execution_odds": self.lower_execution_odds,
            "lower_bookmaker": self.lower_bookmaker,
            "score": self.score,
            "status": self.status,
            "quality_flags": self.quality_flags,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "details": self.details,
        }


@dataclass
class ExplorerResponse:
    """Standardized envelope response for GET /api/v1/opportunities/explorer."""
    items: List[Dict[str, Any]]
    total: int
    counts_by_type: Dict[str, int]
    counts_by_status: Dict[str, int]
    scan_timestamp: Optional[str]
    metadata: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "items": self.items,
            "total": self.total,
            "counts_by_type": self.counts_by_type,
            "counts_by_status": self.counts_by_status,
            "scan_timestamp": self.scan_timestamp,
            "metadata": self.metadata,
        }


class OpportunityExplorerAdapter:
    """Pure translation layer adapting disparate engine domain models into UnifiedOpportunityDTO."""

    @staticmethod
    def _resolve_competition_metadata(
        raw_comp: Optional[str] = None,
        home_team: Optional[str] = None,
        away_team: Optional[str] = None,
        provider_ids: Optional[Dict[str, str]] = None,
        existing_can_id: Optional[str] = None,
        explicit_is_top_5: Optional[bool] = None,
    ) -> Tuple[Optional[str], Optional[str], bool]:
        """Resolves canonical competition ID, display name, and Top 5 status."""
        from normalization.competitions import resolve_canonical_competition, TOP_5_LEAGUE_IDS_SET

        if explicit_is_top_5 is not None and existing_can_id:
            return existing_can_id, raw_comp, explicit_is_top_5

        if existing_can_id and existing_can_id in TOP_5_LEAGUE_IDS_SET:
            return existing_can_id, raw_comp, True

        target = existing_can_id or raw_comp
        res = resolve_canonical_competition(
            raw_name=target,
            home_team=home_team,
            away_team=away_team,
            provider_ids=provider_ids,
        )
        is_top5 = res.canonical_id in TOP_5_LEAGUE_IDS_SET
        display_name = raw_comp or res.canonical_name
        return res.canonical_id, display_name, is_top5

    @staticmethod
    def from_player_prop(prop: Dict[str, Any]) -> UnifiedOpportunityDTO:
        """Adapts a Player Prop record into UnifiedOpportunityDTO."""
        edges = prop.get("edges", {})
        stats = prop.get("statistics", {})
        exec_odds_dict = prop.get("execution_odds", {})
        books = list(exec_odds_dict.keys()) if isinstance(exec_odds_dict, dict) else []

        stat_edge_pct = prop.get("raw_edge_pct") or edges.get("statistical_pct")
        exec_edge_pct = prop.get("execution_edge_pct") or edges.get("execution_pct")
        gross_ev = prop.get("execution_ev_pct") or edges.get("execution_ev_pct") or prop.get("reference_ev_pct")
        # P1-NEW-004: net EV (tax-adjusted) comes from the source engine —
        # zero recalculation here, per this layer's contract.
        net_ev = prop.get("net_ev_pct") or edges.get("net_execution_pct") or edges.get("net_pct")

        # Extract Value Bet fields (Strict: no silent hit rate fallback)
        fair_odds_val = prop.get("fair_odds")

        model_p_pct = None
        if prop.get("model_probability_pct") is not None:
            model_p_pct = float(prop["model_probability_pct"])
        elif prop.get("model_probability") is not None:
            model_p_pct = round(float(prop["model_probability"]) * 100.0, 1)

        val_edge_pp = prop.get("value_edge_pp")
        if val_edge_pp is None and prop.get("execution_edge_pct") is not None:
            val_edge_pp = prop.get("execution_edge_pct")

        is_val = bool(
            prop.get("is_valuebet")
            or prop.get("actionability") == "VALUEBET"
            or prop.get("execution_status") == "VALUEBET"
            # BETTABLE promotes only when net EV is not known-negative:
            # gross-positive / net-negative (e.g. taxed Superbet) stays BETTABLE.
            or (prop.get("execution_status") == "BETTABLE" and gross_ev is not None and float(gross_ev) > 0.0
                and (net_ev is None or float(net_ev) > 0.0))
        )

        # Check Polish Bookmaker Price Discrepancy
        disc_details = prop.get("discrepancy_details") or prop.get("discrepancy")
        is_disc = bool(
            prop.get("is_discrepancy")
            or (disc_details and disc_details.get("is_discrepancy"))
            or (prop.get("relative_price_difference_pct") is not None and float(prop["relative_price_difference_pct"]) >= 10.0)
        )
        odds_diff = prop.get("odds_difference")
        rel_diff_pct = prop.get("relative_price_difference_pct")
        lower_odds = prop.get("lower_executable_odds")
        lower_bm = prop.get("lower_executable_bookmaker")

        if disc_details:
            if odds_diff is None:
                odds_diff = disc_details.get("odds_difference")
            if rel_diff_pct is None:
                rel_diff_pct = disc_details.get("relative_price_difference_pct")
            if lower_odds is None:
                lower_odds = disc_details.get("lower_odds")
            if lower_bm is None:
                lower_bm = disc_details.get("lower_bookmaker")

        # Fallback check on exec_odds_dict if not already evaluated
        if not is_disc and isinstance(exec_odds_dict, dict) and len(exec_odds_dict) >= 2:
            extracted_prices = []
            for bm_name, q_val in exec_odds_dict.items():
                p_val = None
                if isinstance(q_val, dict):
                    p_val = q_val.get("decimal_odds") or q_val.get("odds")
                elif isinstance(q_val, (int, float)):
                    p_val = float(q_val)
                if p_val and float(p_val) > 1.0:
                    extracted_prices.append((bm_name, float(p_val)))
            if len(extracted_prices) >= 2:
                extracted_prices.sort(key=lambda x: x[1], reverse=True)
                high_bm, high_price = extracted_prices[0]
                low_bm, low_price = extracted_prices[-1]
                diff = round(high_price - low_price, 4)
                pct = round(((high_price / low_price) - 1.0) * 100.0, 2)
                if pct >= 10.0:
                    is_disc = True
                    odds_diff = diff
                    rel_diff_pct = pct
                    lower_odds = low_price
                    lower_bm = low_bm
                    disc_details = {
                        "is_discrepancy": True,
                        "threshold_pct": 10.0,
                        "best_bookmaker": high_bm,
                        "best_odds": high_price,
                        "lower_bookmaker": low_bm,
                        "lower_odds": low_price,
                        "odds_difference": diff,
                        "relative_price_difference_pct": pct,
                        "implied_prob_best_pct": round((1.0 / high_price) * 100.0, 2),
                        "implied_prob_lower_pct": round((1.0 / low_price) * 100.0, 2),
                        "implied_prob_diff_pp": round(((1.0 / low_price) - (1.0 / high_price)) * 100.0, 2),
                        "is_surebet": False,
                        "is_guaranteed_profit": False,
                        "note": "Polish bookmaker price discrepancy on identical canonical outcome.",
                    }

        dto_type = OpportunityType.QUOTE_DISCREPANCY.value if is_disc else OpportunityType.PLAYER_PROP.value

        status_str = str(prop.get("actionability") or prop.get("execution_status") or prop.get("status") or "REFERENCE_ONLY")
        if is_disc and status_str in ("AVAILABLE", "BETTABLE"):
            status_str = "BETTABLE"
        elif is_val and status_str in ("BETTABLE", "AVAILABLE"):
            status_str = "VALUEBET"

        details_dict = dict(prop)
        if disc_details:
            details_dict["discrepancy"] = disc_details

        can_comp_id, comp_name, is_top5 = OpportunityExplorerAdapter._resolve_competition_metadata(
            raw_comp=prop.get("competition"),
            home_team=prop.get("team"),
            away_team=prop.get("opponent"),
            existing_can_id=prop.get("canonical_competition_id") or prop.get("competition_id"),
            explicit_is_top_5=prop.get("is_top_5"),
        )

        return UnifiedOpportunityDTO(
            id=str(prop.get("prop_id") or prop.get("canonical_prop_key") or "prop_unknown"),
            type=dto_type,
            source="statshub",
            player=prop.get("player_name"),
            team=prop.get("team"),
            opponent=prop.get("opponent"),
            event=prop.get("fixture") or prop.get("match_name"),
            kickoff=prop.get("kickoff"),
            sport="football",
            competition=comp_name,
            canonical_competition_id=can_comp_id,
            is_top_5=is_top5,
            market=prop.get("market") or prop.get("stat_type") or prop.get("canonical_market_key"),
            line=float(prop["line"]) if prop.get("line") is not None else None,
            side=str(prop.get("side", "OVER")).upper(),
            reference_odds=float(prop["best_odds"]) if prop.get("best_odds") is not None else None,
            execution_odds=float(prop["best_execution_odds"]) if prop.get("best_execution_odds") is not None else None,
            best_bookmaker=prop.get("best_execution_bookmaker") or prop.get("best_bookmaker"),
            all_bookmakers=books,
            statistical_edge_pct=float(stat_edge_pct) if stat_edge_pct is not None else None,
            execution_edge_pct=float(exec_edge_pct) if exec_edge_pct is not None else None,
            gross_ev_pct=float(gross_ev) if gross_ev is not None else None,
            net_ev_pct=float(net_ev) if net_ev is not None else None,
            fair_odds=float(fair_odds_val) if fair_odds_val is not None else None,
            model_probability_pct=float(model_p_pct) if model_p_pct is not None else None,
            value_edge_pp=float(val_edge_pp) if val_edge_pp is not None else None,
            is_valuebet=is_val,
            price_discrepancy_pct=float(rel_diff_pct) if rel_diff_pct is not None else None,
            odds_difference=float(odds_diff) if odds_diff is not None else None,
            lower_execution_odds=float(lower_odds) if lower_odds is not None else None,
            lower_bookmaker=lower_bm,
            score=float(prop["score"]) if prop.get("score") is not None else None,
            status=status_str,
            quality_flags=list(prop.get("data_quality_flags") or []),
            created_at=prop.get("created_at") or prop.get("detected_at"),
            expires_at=None,
            details=details_dict,
        )

    @staticmethod
    def from_team_prop(prop: Dict[str, Any]) -> UnifiedOpportunityDTO:
        """Adapts a Team Prop record into UnifiedOpportunityDTO."""
        edges = prop.get("edges", {})
        stats = prop.get("statistics", {})
        exec_odds_dict = prop.get("execution_odds", {})
        books = list(exec_odds_dict.keys()) if isinstance(exec_odds_dict, dict) else []

        stat_edge_pct = prop.get("raw_edge_pct") or edges.get("statistical_pct")
        exec_edge_pct = prop.get("execution_edge_pct") or edges.get("execution_pct")
        gross_ev = prop.get("execution_ev_pct") or edges.get("execution_ev_pct") or prop.get("reference_ev_pct")
        # P1-NEW-004: net EV from the source engine; no recalculation here.
        net_ev = prop.get("net_ev_pct") or edges.get("net_execution_pct") or edges.get("net_pct")

        # Extract Value Bet fields (Strict: no silent hit rate fallback)
        fair_odds_val = prop.get("fair_odds")

        model_p_pct = None
        if prop.get("model_probability_pct") is not None:
            model_p_pct = float(prop["model_probability_pct"])
        elif prop.get("model_probability") is not None:
            model_p_pct = round(float(prop["model_probability"]) * 100.0, 1)

        val_edge_pp = prop.get("value_edge_pp")
        if val_edge_pp is None and prop.get("execution_edge_pct") is not None:
            val_edge_pp = prop.get("execution_edge_pct")

        is_val = bool(
            prop.get("is_valuebet")
            or prop.get("actionability") == "VALUEBET"
            or prop.get("execution_status") == "VALUEBET"
            # BETTABLE promotes only when net EV is not known-negative.
            or (prop.get("execution_status") == "BETTABLE" and gross_ev is not None and float(gross_ev) > 0.0
                and (net_ev is None or float(net_ev) > 0.0))
        )

        # Check Polish Bookmaker Price Discrepancy
        disc_details = prop.get("discrepancy_details") or prop.get("discrepancy")
        is_disc = bool(
            prop.get("is_discrepancy")
            or (disc_details and disc_details.get("is_discrepancy"))
            or (prop.get("relative_price_difference_pct") is not None and float(prop["relative_price_difference_pct"]) >= 10.0)
        )
        odds_diff = prop.get("odds_difference")
        rel_diff_pct = prop.get("relative_price_difference_pct")
        lower_odds = prop.get("lower_executable_odds")
        lower_bm = prop.get("lower_executable_bookmaker")

        if disc_details:
            if odds_diff is None:
                odds_diff = disc_details.get("odds_difference")
            if rel_diff_pct is None:
                rel_diff_pct = disc_details.get("relative_price_difference_pct")
            if lower_odds is None:
                lower_odds = disc_details.get("lower_odds")
            if lower_bm is None:
                lower_bm = disc_details.get("lower_bookmaker")

        # Fallback check on exec_odds_dict if not already evaluated
        if not is_disc and isinstance(exec_odds_dict, dict) and len(exec_odds_dict) >= 2:
            extracted_prices = []
            for b_name, b_val in exec_odds_dict.items():
                price = None
                if isinstance(b_val, dict):
                    price = b_val.get("decimal_odds") or b_val.get("odds")
                elif isinstance(b_val, (int, float)):
                    price = b_val
                if price is not None:
                    try:
                        p_flt = float(price)
                        if p_flt > 1.0:
                            extracted_prices.append((b_name, p_flt))
                    except (ValueError, TypeError):
                        pass

            if len(extracted_prices) >= 2:
                extracted_prices.sort(key=lambda x: x[1], reverse=True)
                high_bm, high_price = extracted_prices[0]
                low_bm, low_price = extracted_prices[-1]
                calc_rel_diff = round(((high_price / low_price) - 1.0) * 100.0, 2)
                if calc_rel_diff >= 10.0:
                    is_disc = True
                    rel_diff_pct = calc_rel_diff
                    odds_diff = round(high_price - low_price, 4)
                    lower_odds = low_price
                    lower_bm = low_bm
                    disc_details = {
                        "is_discrepancy": True,
                        "threshold_pct": 10.0,
                        "best_bookmaker": high_bm,
                        "best_odds": high_price,
                        "lower_bookmaker": low_bm,
                        "lower_odds": low_price,
                        "odds_difference": odds_diff,
                        "relative_price_difference_pct": rel_diff_pct,
                        "implied_prob_best_pct": round((1.0 / high_price) * 100.0, 2),
                        "implied_prob_lower_pct": round((1.0 / low_price) * 100.0, 2),
                        "implied_prob_diff_pp": round(((1.0 / low_price) - (1.0 / high_price)) * 100.0, 2),
                        "is_surebet": False,
                        "is_guaranteed_profit": False,
                        "note": "Polish bookmaker price discrepancy on identical canonical outcome.",
                    }

        dto_type = OpportunityType.QUOTE_DISCREPANCY.value if is_disc else OpportunityType.TEAM_PROP.value

        status_str = str(prop.get("actionability") or prop.get("execution_status") or prop.get("status") or "REFERENCE_ONLY")
        if is_disc and status_str in ("AVAILABLE", "BETTABLE"):
            status_str = "BETTABLE"
        elif is_val and status_str in ("BETTABLE", "AVAILABLE"):
            status_str = "VALUEBET"

        details_dict = dict(prop)
        if disc_details:
            details_dict["discrepancy"] = disc_details

        can_comp_id, comp_name, is_top5 = OpportunityExplorerAdapter._resolve_competition_metadata(
            raw_comp=prop.get("competition"),
            home_team=prop.get("team") or prop.get("team_name"),
            away_team=prop.get("opponent") or prop.get("opponent_name"),
            existing_can_id=prop.get("canonical_competition_id") or prop.get("competition_id"),
            explicit_is_top_5=prop.get("is_top_5"),
        )

        return UnifiedOpportunityDTO(
            id=str(prop.get("prop_id") or prop.get("canonical_prop_key") or "team_prop_unknown"),
            type=dto_type,
            source="statshub_team",
            player=None,
            team=prop.get("team") or prop.get("team_name"),
            opponent=prop.get("opponent") or prop.get("opponent_name"),
            event=prop.get("fixture") or prop.get("match_name"),
            kickoff=prop.get("kickoff"),
            sport="football",
            competition=comp_name,
            canonical_competition_id=can_comp_id,
            is_top_5=is_top5,
            market=prop.get("market") or prop.get("stat_type") or prop.get("canonical_market_key"),
            line=float(prop["line"]) if prop.get("line") is not None else None,
            side=str(prop.get("side", "OVER")).upper(),
            reference_odds=float(prop["best_odds"]) if prop.get("best_odds") is not None else None,
            execution_odds=float(prop["best_execution_odds"]) if prop.get("best_execution_odds") is not None else None,
            best_bookmaker=prop.get("best_execution_bookmaker") or prop.get("best_bookmaker"),
            all_bookmakers=books,
            statistical_edge_pct=float(stat_edge_pct) if stat_edge_pct is not None else None,
            execution_edge_pct=float(exec_edge_pct) if exec_edge_pct is not None else None,
            gross_ev_pct=float(gross_ev) if gross_ev is not None else None,
            net_ev_pct=float(net_ev) if net_ev is not None else None,
            fair_odds=float(fair_odds_val) if fair_odds_val is not None else None,
            model_probability_pct=float(model_p_pct) if model_p_pct is not None else None,
            value_edge_pp=float(val_edge_pp) if val_edge_pp is not None else None,
            is_valuebet=is_val,
            price_discrepancy_pct=float(rel_diff_pct) if rel_diff_pct is not None else None,
            odds_difference=float(odds_diff) if odds_diff is not None else None,
            lower_execution_odds=float(lower_odds) if lower_odds is not None else None,
            lower_bookmaker=lower_bm,
            score=float(prop["score"]) if prop.get("score") is not None else None,
            status=status_str,
            quality_flags=list(prop.get("data_quality_flags") or []),
            created_at=prop.get("created_at") or prop.get("detected_at"),
            expires_at=None,
            details=details_dict,
        )

    @staticmethod
    def from_matched_team_market(
        event: Dict[str, Any],
        market: Dict[str, Any],
        selection: Dict[str, Any],
        discrepancy_threshold_pct: float = 10.0,
    ) -> UnifiedOpportunityDTO:
        """Adapts a matched Team Prop market selection from scan results into UnifiedOpportunityDTO."""
        home = event.get("home_team") or ""
        away = event.get("away_team") or ""
        part = str(selection.get("participant") or "").lower()
        if part in ("home", "h"):
            team = home
            opp = away
        elif part in ("away", "a"):
            team = away
            opp = home
        else:
            team = selection.get("participant") or home
            opp = away if team == home else home

        ev_name = f"{home} vs {away}" if home and away else str(event.get("match_name") or "Match")
        m_type = str(market.get("market_type") or "TEAM_STAT")
        line = selection.get("line") if selection.get("line") is not None else market.get("line")
        side = str(selection.get("selection_type") or "OVER").upper()

        odds_map = selection.get("odds") or {}
        books = list(odds_map.keys()) if isinstance(odds_map, dict) else list(market.get("participating_bookmakers") or [])

        # Parse quotes and detect discrepancy
        valid_quotes = []
        if isinstance(odds_map, dict):
            for b, o in odds_map.items():
                try:
                    ov = float(o)
                    if ov > 1.0:
                        valid_quotes.append((b, ov))
                except (ValueError, TypeError):
                    continue

        best_bm = None
        best_odds = None
        lower_bm = None
        lower_odds = None
        odds_diff = None
        rel_diff_pct = None
        is_discrepancy = False
        discrepancy_details = None

        if valid_quotes:
            valid_quotes.sort(key=lambda x: x[1], reverse=True)
            best_bm, best_odds = valid_quotes[0]
            if len(valid_quotes) >= 2:
                lower_bm, lower_odds = valid_quotes[-1]
                odds_diff = round(best_odds - lower_odds, 4)
                rel_diff_pct = round(((best_odds / lower_odds) - 1.0) * 100.0, 2)

                imp_prob_best = round((1.0 / best_odds) * 100.0, 2)
                imp_prob_lower = round((1.0 / lower_odds) * 100.0, 2)
                prob_diff_pp = round(imp_prob_lower - imp_prob_best, 2)

                if rel_diff_pct >= discrepancy_threshold_pct:
                    is_discrepancy = True

                discrepancy_details = {
                    "is_discrepancy": is_discrepancy,
                    "threshold_pct": discrepancy_threshold_pct,
                    "best_bookmaker": best_bm,
                    "best_odds": best_odds,
                    "lower_bookmaker": lower_bm,
                    "lower_odds": lower_odds,
                    "odds_difference": odds_diff,
                    "relative_price_difference_pct": rel_diff_pct,
                    "implied_prob_best_pct": imp_prob_best,
                    "implied_prob_lower_pct": imp_prob_lower,
                    "implied_prob_diff_pp": prob_diff_pp,
                    "is_surebet": False,
                    "is_guaranteed_profit": False,
                    "note": "Polish bookmaker price discrepancy on identical canonical outcome.",
                }
        else:
            best_o = selection.get("best_odds") or {}
            best_bm = best_o.get("bookmaker") or (books[0] if books else None)
            best_odds = float(best_o.get("odds")) if best_o.get("odds") else (float(odds_map[best_bm]) if best_bm and best_bm in odds_map else None)

        status_str = "AVAILABLE" if best_odds is not None else "NO_EXECUTION_ODDS"
        dto_type = OpportunityType.QUOTE_DISCREPANCY.value if is_discrepancy else OpportunityType.QUOTE_COMPARISON.value
        can_id = f"ctp_scan_{event.get('id') or event.get('canonical_event_id')}_{team}_{m_type}_{line}_{side}"

        details_dict = {
            "event": {
                "id": event.get("id") or event.get("canonical_event_id"),
                "home_team": home,
                "away_team": away,
                "competition": event.get("competition"),
                "kickoff": event.get("kickoff"),
                "sport": event.get("sport", "football"),
            },
            "market": {
                "market_type": m_type,
                "line": line,
                "period": market.get("period", "FULL_TIME"),
                "scope": market.get("scope", "TEAM"),
            },
            "selection": {
                "participant": team,
                "selection_type": side,
                "line": line,
                "odds": odds_map,
                "best_odds": {"bookmaker": best_bm, "odds": best_odds} if best_bm else {},
            },
        }
        if discrepancy_details:
            details_dict["discrepancy"] = discrepancy_details

        can_comp_id, comp_name, is_top5 = OpportunityExplorerAdapter._resolve_competition_metadata(
            raw_comp=event.get("competition"),
            home_team=home,
            away_team=away,
            existing_can_id=event.get("canonical_competition_id") or event.get("competition_id"),
            explicit_is_top_5=event.get("is_top_5"),
        )

        return UnifiedOpportunityDTO(
            id=can_id,
            type=dto_type,
            source="scanner",
            player=None,
            team=team,
            opponent=opp,
            event=ev_name,
            kickoff=event.get("kickoff"),
            sport=event.get("sport", "football"),
            competition=comp_name,
            canonical_competition_id=can_comp_id,
            is_top_5=is_top5,
            market=m_type,
            line=float(line) if line is not None else None,
            side=side,
            reference_odds=None,
            execution_odds=best_odds,
            best_bookmaker=best_bm,
            all_bookmakers=books,
            statistical_edge_pct=None,
            execution_edge_pct=None,
            gross_ev_pct=None,
            net_ev_pct=None,
            fair_odds=None,
            model_probability_pct=None,
            value_edge_pp=None,
            is_valuebet=False,
            price_discrepancy_pct=float(rel_diff_pct) if rel_diff_pct is not None else None,
            odds_difference=float(odds_diff) if odds_diff is not None else None,
            lower_execution_odds=float(lower_odds) if lower_odds is not None else None,
            lower_bookmaker=lower_bm,
            score=None,
            status=status_str,
            quality_flags=[],
            details=details_dict,
        )

    @staticmethod
    def from_matched_prop_market(
        event: Dict[str, Any],
        market: Dict[str, Any],
        selection: Dict[str, Any],
        discrepancy_threshold_pct: float = 10.0,
    ) -> UnifiedOpportunityDTO:
        """Adapts a matched Player Prop market selection from scan results into UnifiedOpportunityDTO.

        If a large price difference between Polish bookmaker executable quotes exists (>= threshold),
        it is classified as QUOTE_DISCREPANCY. Otherwise, it is classified as PLAYER_PROP.
        """
        home = event.get("home_team") or ""
        away = event.get("away_team") or ""
        player_name = selection.get("player_name") or selection.get("player") or selection.get("participant") or ""
        team = selection.get("team") or home
        opp = away if team == home else home

        ev_name = f"{home} vs {away}" if home and away else str(event.get("match_name") or "Match")
        m_type = str(market.get("market_type") or "PLAYER_PROP")
        line = selection.get("line") if selection.get("line") is not None else market.get("line")
        side = str(selection.get("selection_type") or selection.get("side") or "OVER").upper()

        odds_map = selection.get("odds") or {}
        books = list(odds_map.keys()) if isinstance(odds_map, dict) else list(market.get("participating_bookmakers") or [])

        # Parse quotes and detect discrepancy
        valid_quotes = []
        if isinstance(odds_map, dict):
            for b, o in odds_map.items():
                try:
                    ov = float(o)
                    if ov > 1.0:
                        valid_quotes.append((b, ov))
                except (ValueError, TypeError):
                    continue

        best_bm = None
        best_odds = None
        lower_bm = None
        lower_odds = None
        odds_diff = None
        rel_diff_pct = None
        is_discrepancy = False
        discrepancy_details = None

        if valid_quotes:
            valid_quotes.sort(key=lambda x: x[1], reverse=True)
            best_bm, best_odds = valid_quotes[0]
            if len(valid_quotes) >= 2:
                lower_bm, lower_odds = valid_quotes[-1]
                odds_diff = round(best_odds - lower_odds, 4)
                rel_diff_pct = round(((best_odds / lower_odds) - 1.0) * 100.0, 2)

                imp_prob_best = round((1.0 / best_odds) * 100.0, 2)
                imp_prob_lower = round((1.0 / lower_odds) * 100.0, 2)
                prob_diff_pp = round(imp_prob_lower - imp_prob_best, 2)

                if rel_diff_pct >= discrepancy_threshold_pct:
                    is_discrepancy = True

                discrepancy_details = {
                    "is_discrepancy": is_discrepancy,
                    "threshold_pct": discrepancy_threshold_pct,
                    "best_bookmaker": best_bm,
                    "best_odds": best_odds,
                    "lower_bookmaker": lower_bm,
                    "lower_odds": lower_odds,
                    "odds_difference": odds_diff,
                    "relative_price_difference_pct": rel_diff_pct,
                    "implied_prob_best_pct": imp_prob_best,
                    "implied_prob_lower_pct": imp_prob_lower,
                    "implied_prob_diff_pp": prob_diff_pp,
                    "is_surebet": False,
                    "is_guaranteed_profit": False,
                    "note": "Polish bookmaker price discrepancy on identical canonical outcome.",
                }

        opp_type = OpportunityType.QUOTE_DISCREPANCY.value if is_discrepancy else OpportunityType.PLAYER_PROP.value
        status_str = "AVAILABLE" if best_odds is not None else "NO_EXECUTION_ODDS"
        p_slug = player_name.replace(" ", "_") if player_name else "prop"
        can_id = f"cpp_scan_{event.get('id') or event.get('canonical_event_id')}_{p_slug}_{m_type}_{line}_{side}"

        can_comp_id, comp_name, is_top5 = OpportunityExplorerAdapter._resolve_competition_metadata(
            raw_comp=event.get("competition"),
            home_team=home,
            away_team=away,
            existing_can_id=event.get("canonical_competition_id") or event.get("competition_id"),
            explicit_is_top_5=event.get("is_top_5"),
        )

        return UnifiedOpportunityDTO(
            id=can_id,
            type=opp_type,
            source="scanner_props",
            player=player_name,
            team=team,
            opponent=opp,
            event=ev_name,
            kickoff=event.get("kickoff"),
            sport=event.get("sport", "football"),
            competition=comp_name,
            canonical_competition_id=can_comp_id,
            is_top_5=is_top5,
            market=m_type,
            line=float(line) if line is not None else None,
            side=side,
            reference_odds=None,
            execution_odds=best_odds,
            best_bookmaker=best_bm,
            all_bookmakers=books,
            statistical_edge_pct=None,
            execution_edge_pct=None,
            gross_ev_pct=None,
            net_ev_pct=None,
            fair_odds=None,
            model_probability_pct=None,
            value_edge_pp=None,
            is_valuebet=False,
            price_discrepancy_pct=float(rel_diff_pct) if rel_diff_pct is not None else None,
            odds_difference=float(odds_diff) if odds_diff is not None else None,
            lower_execution_odds=float(lower_odds) if lower_odds is not None else None,
            lower_bookmaker=lower_bm,
            score=None,
            status=status_str,
            quality_flags=[],
            details={
                "event": {
                    "id": event.get("id") or event.get("canonical_event_id"),
                    "home_team": home,
                    "away_team": away,
                    "competition": event.get("competition"),
                    "kickoff": event.get("kickoff"),
                    "sport": event.get("sport", "football"),
                },
                "market": {
                    "market_type": m_type,
                    "line": line,
                    "period": market.get("period", "FULL_TIME"),
                    "scope": market.get("scope", "PLAYER"),
                },
                "selection": {
                    "player": player_name,
                    "participant": player_name,
                    "selection_type": side,
                    "line": line,
                    "odds": odds_map,
                },
                "discrepancy": discrepancy_details,
            },
        )

    @staticmethod
    def from_valuebet(val: Dict[str, Any]) -> UnifiedOpportunityDTO:
        """Adapts a ValueBet candidate / record into UnifiedOpportunityDTO."""
        ev_data = val.get("event") or {}
        home = ev_data.get("home_participant") or ev_data.get("home_team") if isinstance(ev_data, dict) else getattr(ev_data, "home_participant", None)
        away = ev_data.get("away_participant") or ev_data.get("away_team") if isinstance(ev_data, dict) else getattr(ev_data, "away_participant", None)
        ev_name = f"{home} vs {away}" if home and away else (val.get("event_name") or val.get("event"))

        bm_odds = val.get("bookmaker_odds")
        eff_net_odds = val.get("effective_net_odds")
        val_pct = val.get("value_percent") or val.get("value_edge_pct") or val.get("margin_pct")
        net_val_pct = val.get("net_value_percent")

        bm = val.get("bookmaker") or (val.get("bookmakers", ["superbet"])[0] if val.get("bookmakers") else "superbet")
        fair_odds_val = val.get("fair_odds")
        ref_raw_val = val.get("reference_raw_odds")
        ref_odds = ref_raw_val if ref_raw_val is not None else fair_odds_val

        mkt_dict = val.get("market") if isinstance(val.get("market"), dict) else {}
        scope_val = val.get("market_scope") or mkt_dict.get("scope") or (val.get("market_key", {}) or {}).get("scope", "MATCH")
        val_type = OpportunityType.TEAM_PROP.value if str(scope_val).upper() == "TEAM" else OpportunityType.VALUEBET.value

        mkt_name = val.get("market_type") or mkt_dict.get("type") or str(val.get("market", ""))
        line_val = val.get("line") if val.get("line") is not None else mkt_dict.get("line")

        fair_p = val.get("fair_probability") or val.get("reference_fair_probability")
        model_pct = round(float(fair_p) * 100.0, 1) if fair_p is not None else None

        # P1-NEW-004: the engine's net-gated qualification is authoritative.
        # Prefer the explicit flag; fall back to net EV, then (legacy dicts
        # without net fields) to gross EV.
        is_q = val.get("is_qualified", None)
        if is_q is None:
            if net_val_pct is not None:
                is_q = float(net_val_pct) > 0.0
            else:
                is_q = bool(val_pct and float(val_pct) > 0.0)

        can_comp_id, comp_name, is_top5 = OpportunityExplorerAdapter._resolve_competition_metadata(
            raw_comp=val.get("competition_name") or val.get("competition"),
            home_team=home,
            away_team=away,
            existing_can_id=val.get("canonical_competition_id") or (ev_data.get("canonical_competition_id") if isinstance(ev_data, dict) else getattr(ev_data, "canonical_competition_id", None)),
            explicit_is_top_5=val.get("is_top_5"),
        )

        return UnifiedOpportunityDTO(
            id=str(val.get("candidate_id") or val.get("opportunity_id") or val.get("id") or "vbc_unknown"),
            type=val_type,
            source="valuebets",
            player=val.get("player_name"),
            team=home,
            opponent=away,
            event=ev_name,
            kickoff=val.get("kickoff"),
            sport=val.get("sport", "football"),
            competition=comp_name,
            canonical_competition_id=can_comp_id,
            is_top_5=is_top5,
            market=mkt_name,
            line=float(line_val) if line_val is not None else None,
            side=str(val.get("selection_type", "")).upper() if val.get("selection_type") else None,
            reference_odds=float(ref_odds) if ref_odds is not None else None,
            execution_odds=float(bm_odds) if bm_odds is not None else None,
            best_bookmaker=bm,
            all_bookmakers=[bm] if bm else [],
            statistical_edge_pct=None,
            execution_edge_pct=None,
            gross_ev_pct=float(val_pct) if val_pct is not None else None,
            net_ev_pct=float(net_val_pct) if net_val_pct is not None else None,
            fair_odds=float(fair_odds_val) if fair_odds_val is not None else None,
            model_probability_pct=model_pct,
            value_edge_pp=float(val_pct) if val_pct is not None else None,
            is_valuebet=bool(is_q),
            score=float(val["quality_score"]) if val.get("quality_score") is not None else (float(val_pct) * 5.0 if val_pct is not None else None),
            status="VALUEBET" if val.get("is_qualified", True) else "REFERENCE_ONLY",
            quality_flags=list(val.get("quality_flags") or []),
            created_at=val.get("detected_at") or val.get("first_seen_at"),
            expires_at=val.get("expired_at"),
            details=val,
        )

    @staticmethod
    def from_surebet(sb: Dict[str, Any]) -> UnifiedOpportunityDTO:
        """Adapts a Surebet opportunity / record into UnifiedOpportunityDTO."""
        ev_data = sb.get("event") or {}
        home = ev_data.get("home_participant") or ev_data.get("home_team") if isinstance(ev_data, dict) else getattr(ev_data, "home_participant", None)
        away = ev_data.get("away_participant") or ev_data.get("away_team") if isinstance(ev_data, dict) else getattr(ev_data, "away_participant", None)
        ev_name = f"{home} vs {away}" if home and away else (sb.get("event_name") or sb.get("event"))

        legs = sb.get("legs", []) or sb.get("selections", [])
        margin_pct = sb.get("arbitrage_margin_pct") or sb.get("margin_pct") or sb.get("margin")
        bookies = sb.get("bookmakers", [])
        if not bookies and legs:
            bookies = list({l.get("provider") or l.get("bookmaker") for l in legs if l.get("provider") or l.get("bookmaker")})

        first_leg_odds = float(legs[0].get("odds") or legs[0].get("decimal_odds") or 0.0) if legs else None

        mkt_dict = sb.get("market") if isinstance(sb.get("market"), dict) else {}
        scope_val = sb.get("market_scope") or mkt_dict.get("scope") or (sb.get("market_key", {}) or {}).get("scope", "MATCH")
        opp_type = OpportunityType.TEAM_PROP.value if str(scope_val).upper() == "TEAM" else OpportunityType.SUREBET.value
        mkt_name = sb.get("market_type") or mkt_dict.get("type") or str(sb.get("market", ""))
        line_val = sb.get("line") if sb.get("line") is not None else mkt_dict.get("line")

        # If TEAM scope, inspect participant_role from market key string or legs to assign the correct team entity
        team_entity = home
        opponent_entity = away
        mkt_key_str = sb.get("canonical_market_key") or mkt_dict.get("key_string") or ""
        role = None
        if "TEAM:away" in mkt_key_str.lower():
            role = "AWAY"
        elif "TEAM:home" in mkt_key_str.lower():
            role = "HOME"
        elif legs:
            for l in legs:
                p_role = l.get("participant")
                if str(p_role).upper() in ("AWAY", "HOME"):
                    role = str(p_role).upper()
                    break

        if role == "AWAY" and away:
            team_entity = away
            opponent_entity = home

        # Side for first leg if available
        first_side = str(legs[0].get("selection_type", "")).upper() if legs and legs[0].get("selection_type") else None

        can_comp_id, comp_name, is_top5 = OpportunityExplorerAdapter._resolve_competition_metadata(
            raw_comp=sb.get("competition"),
            home_team=team_entity,
            away_team=opponent_entity,
            existing_can_id=sb.get("canonical_competition_id") or (ev_data.get("canonical_competition_id") if isinstance(ev_data, dict) else getattr(ev_data, "canonical_competition_id", None)),
            explicit_is_top_5=sb.get("is_top_5"),
        )

        return UnifiedOpportunityDTO(
            id=str(sb.get("opportunity_id") or sb.get("id") or "sb_unknown"),
            type=opp_type,
            source="surebets",
            player=None,
            team=team_entity,
            opponent=opponent_entity,
            event=ev_name,
            kickoff=sb.get("kickoff"),
            sport=sb.get("sport", "football"),
            competition=comp_name,
            canonical_competition_id=can_comp_id,
            is_top_5=is_top5,
            market=mkt_name,
            line=float(line_val) if line_val is not None else None,
            side=first_side,
            reference_odds=None,
            execution_odds=first_leg_odds,
            best_bookmaker=", ".join(bookies) if bookies else None,
            all_bookmakers=bookies,
            statistical_edge_pct=None,
            execution_edge_pct=None,
            gross_ev_pct=float(margin_pct) if margin_pct is not None else None,
            net_ev_pct=None,
            fair_odds=None,
            model_probability_pct=None,
            value_edge_pp=None,
            is_valuebet=False,
            score=float(sb["quality_score"]) if sb.get("quality_score") is not None else (float(margin_pct) * 10.0 if margin_pct is not None else None),
            status="AVAILABLE" if sb.get("is_qualified", True) else "EXPIRED",
            quality_flags=list(sb.get("quality_flags") or []),
            created_at=sb.get("detected_at") or sb.get("first_seen_at"),
            expires_at=sb.get("expired_at"),
            details=sb,
        )

    @staticmethod
    def from_booster(b: Dict[str, Any]) -> UnifiedOpportunityDTO:
        """Adapts an enhanced odds booster into UnifiedOpportunityDTO."""
        can_comp_id, comp_name, is_top5 = OpportunityExplorerAdapter._resolve_competition_metadata(
            raw_comp=b.get("competition"),
            home_team=b.get("team"),
            away_team=b.get("opponent"),
            existing_can_id=b.get("canonical_competition_id"),
            explicit_is_top_5=b.get("is_top_5"),
        )

        return UnifiedOpportunityDTO(
            id=str(b.get("booster_id") or b.get("id") or "booster_unknown"),
            type=OpportunityType.BOOSTER.value,
            source=b.get("source", "boosters"),
            player=b.get("player"),
            team=b.get("team"),
            opponent=b.get("opponent"),
            event=b.get("event"),
            kickoff=b.get("kickoff"),
            sport=b.get("sport", "football"),
            competition=comp_name,
            canonical_competition_id=can_comp_id,
            is_top_5=is_top5,
            market=b.get("market"),
            line=float(b["line"]) if b.get("line") is not None else None,
            side=b.get("side"),
            reference_odds=float(b["regular_odds"]) if b.get("regular_odds") is not None else None,
            execution_odds=float(b["boosted_odds"]) if b.get("boosted_odds") is not None else None,
            best_bookmaker=b.get("bookmaker"),
            all_bookmakers=[b.get("bookmaker")] if b.get("bookmaker") else [],
            statistical_edge_pct=None,
            execution_edge_pct=float(b["boost_pct"]) if b.get("boost_pct") is not None else None,
            gross_ev_pct=float(b.get("ev_pct")) if b.get("ev_pct") is not None else None,
            net_ev_pct=None,
            fair_odds=float(b["regular_odds"]) if b.get("regular_odds") is not None else None,
            model_probability_pct=None,
            value_edge_pp=float(b["boost_pct"]) if b.get("boost_pct") is not None else None,
            is_valuebet=True if (b.get("boost_pct") and float(b["boost_pct"]) > 0.0) else False,
            score=float(b.get("score") or 80.0),
            status=b.get("status", "AVAILABLE"),
            quality_flags=list(b.get("quality_flags") or []),
            created_at=b.get("created_at"),
            expires_at=b.get("expires_at"),
            details=b,
        )

    @staticmethod
    def from_ultra_opportunity(opp: Union[Dict[str, Any], Any]) -> UnifiedOpportunityDTO:
        """Adapts an UltraOpportunity dataclass or serialized dict into UnifiedOpportunityDTO."""
        if hasattr(opp, "to_dict"):
            d = opp.to_dict()
        elif isinstance(opp, dict):
            d = opp
        else:
            d = getattr(opp, "__dict__", {})

        cat = str(d.get("category", "")).upper()
        if cat == "PLAYER_PROP":
            dto_type = OpportunityType.PLAYER_PROP.value
        elif cat == "TEAM_PROP":
            dto_type = OpportunityType.TEAM_PROP.value
        elif cat == "VALUEBET":
            dto_type = OpportunityType.VALUEBET.value
        elif cat == "SUREBET":
            dto_type = OpportunityType.SUREBET.value
        elif cat in ("WATCHLIST", "NEAR_SUREBET"):
            dto_type = OpportunityType.WATCHLIST.value
        else:
            dto_type = OpportunityType.VALUEBET.value

        match_name = d.get("match_name") or ""
        home, away = None, None
        if " vs " in match_name:
            parts = match_name.split(" vs ", 1)
            home, away = parts[0].strip(), parts[1].strip()
        elif " - " in match_name:
            parts = match_name.split(" - ", 1)
            home, away = parts[0].strip(), parts[1].strip()

        details = d.get("details") or {}
        player_name = details.get("player_name") or details.get("player")

        bm = d.get("bookmaker") or "Superbet"
        ref_sources = list(d.get("reference_sources") or [])
        all_bms = [bm]
        for src in ref_sources:
            if src and src not in all_bms:
                all_bms.append(src)

        edge = d.get("edge_pct")
        raw_odds = d.get("raw_odds")
        eff_odds = d.get("effective_odds")
        fair_odds = d.get("fair_odds")

        is_val = cat == "VALUEBET" or (cat in ("PLAYER_PROP", "TEAM_PROP") and edge is not None and float(edge) > 0.0)
        if cat in ("WATCHLIST", "NEAR_SUREBET"):
            status = "WATCHLIST"
        else:
            status = "VALUEBET" if is_val else "AVAILABLE"

        line_val = None
        if details.get("line") is not None:
            try:
                line_val = float(details["line"])
            except (ValueError, TypeError):
                line_val = None

        raw_score = d.get("ultra_rank_score")
        if cat in ("WATCHLIST", "NEAR_SUREBET"):
            score_val = float(raw_score) if raw_score is not None else 10.0
        else:
            score_val = float(raw_score) if raw_score is not None else (float(edge) * 5.0 if edge is not None else None)

        fair_odds_val = float(fair_odds) if fair_odds is not None else None
        model_prob = round(100.0 / fair_odds_val, 1) if (fair_odds_val and fair_odds_val > 0) else None

        can_comp_id, comp_name, is_top5 = OpportunityExplorerAdapter._resolve_competition_metadata(
            raw_comp=d.get("competition"),
            home_team=home,
            away_team=away,
            existing_can_id=d.get("canonical_competition_id"),
            explicit_is_top_5=d.get("is_top_5"),
        )

        return UnifiedOpportunityDTO(
            id=str(d.get("opportunity_id") or "ultra_unknown"),
            type=dto_type,
            source="ultra_scan",
            player=player_name,
            team=home,
            opponent=away,
            event=match_name,
            kickoff=d.get("kickoff"),
            sport="football",
            competition=comp_name,
            canonical_competition_id=can_comp_id,
            is_top_5=is_top5,
            market=d.get("market_display"),
            line=line_val,
            side=d.get("selection_display"),
            reference_odds=fair_odds_val,
            execution_odds=float(eff_odds or raw_odds) if (eff_odds or raw_odds) is not None else None,
            best_bookmaker=bm,
            all_bookmakers=all_bms,
            statistical_edge_pct=float(edge) if cat in ("PLAYER_PROP", "TEAM_PROP") and edge is not None else None,
            execution_edge_pct=float(edge) if edge is not None else None,
            gross_ev_pct=float(edge) if edge is not None else None,
            net_ev_pct=float(edge) if edge is not None else None,
            fair_odds=fair_odds_val,
            model_probability_pct=model_prob,
            value_edge_pp=float(edge) if edge is not None else None,
            is_valuebet=is_val,
            score=score_val,
            status=status,
            quality_flags=[],
            created_at=details.get("detected_at"),
            expires_at=None,
            details=details,
        )

