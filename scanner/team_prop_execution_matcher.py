"""
Team Prop Execution Bookmaker Matcher (Stage 30)

Integrates team prop intelligence with ZieloneBety's execution bookmakers
(Superbet and Betclic). Compares reference odds (Bet365, Paddy Power, etc.) against
actionable Polish execution odds, strictly requiring exact team, fixture, metric,
participant role (HOME/AWAY), period, direction, and exact line without cross-market fallback.
"""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple, Set
import logging
import re
import unicodedata

from normalization.market_identity import normalize_line
from normalization.identity import normalize_team_name
from scanner.execution_providers import NormalizedExecutionQuote

logger = logging.getLogger("scanner.team_prop_execution_matcher")


STAT_TYPE_CANONICAL_MAP: Dict[str, str] = {
    "shots": "SHOTS",
    "shot": "SHOTS",
    "team_shots": "SHOTS",
    "shotsontarget": "SHOTS_ON_TARGET",
    "shots_on_target": "SHOTS_ON_TARGET",
    "team_shots_on_target": "SHOTS_ON_TARGET",
    "fouls": "FOULS",
    "foul": "FOULS",
    "team_fouls": "FOULS",
    "cards": "CARDS",
    "card": "CARDS",
    "team_cards": "CARDS",
    "corners": "CORNERS",
    "corner": "CORNERS",
    "team_corners": "CORNERS",
    "goals": "GOALS",
    "goal": "GOALS",
    "team_goals": "GOALS",
    "offsides": "OFFSIDES",
    "offside": "OFFSIDES",
    "team_offsides": "OFFSIDES",
    "passes": "PASSES",
    "pass": "PASSES",
    "team_passes": "PASSES",
}


def _clean_team_str(name: str) -> str:
    if not name:
        return ""
    norm, _ = normalize_team_name(name)
    s = norm.lower().strip()
    words = s.split()
    filtered = [w for w in words if w not in ("fc", "cf", "afc", "sc", "ac", "club", "ks")]
    return "_".join(filtered) if filtered else "_".join(words)


@dataclass(frozen=True)
class CanonicalTeamPropKey:
    """Deterministic, provider-independent canonical key for a team proposition."""
    team: str
    opponent: str
    stat_type: str
    line: Decimal
    participant_role: str = "HOME"  # "HOME" or "AWAY"
    side: str = "OVER"
    period: str = "FULL_TIME"
    event_id: Optional[str] = None

    def __post_init__(self):
        norm_t, _ = normalize_team_name(self.team)
        norm_o, _ = normalize_team_name(self.opponent)
        norm_st = STAT_TYPE_CANONICAL_MAP.get(self.stat_type.lower().replace(" ", "_"), self.stat_type.upper())
        norm_line = normalize_line(self.line) or Decimal("0.5")
        norm_role = str(self.participant_role or "HOME").upper()

        object.__setattr__(self, "team", norm_t)
        object.__setattr__(self, "opponent", norm_o)
        object.__setattr__(self, "stat_type", norm_st)
        object.__setattr__(self, "line", norm_line)
        object.__setattr__(self, "participant_role", norm_role)
        object.__setattr__(self, "side", self.side.upper())
        object.__setattr__(self, "period", self.period.upper())

    def to_key_string(self) -> str:
        """Returns deterministic canonical string representation."""
        line_str = f"{self.line:f}".rstrip("0").rstrip(".") if "." in f"{self.line:f}" else f"{self.line:f}"
        t_str = _clean_team_str(self.team)
        o_str = _clean_team_str(self.opponent)
        return f"team_prop:{t_str}:{o_str}:{self.participant_role}:{self.stat_type}:{self.side}:{self.period}:{line_str}"



@dataclass
class TeamExecutionOdds:
    """Execution bookmaker quote for a team proposition."""
    bookmaker: str  # "Superbet", "Betclic"
    status: str  # "AVAILABLE", "NO_ODDS", "UNAVAILABLE", "UNCERTAIN"
    decimal_odds: Optional[float] = None
    line: Optional[float] = None
    side: Optional[str] = None
    participant_role: Optional[str] = None
    selection_name: Optional[str] = None
    event_id: Optional[str] = None
    event_name: Optional[str] = None
    reason: Optional[str] = None
    match_confidence: float = 0.0
    captured_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "bookmaker": self.bookmaker,
            "status": self.status,
            "decimal_odds": self.decimal_odds,
            "line": self.line,
            "side": self.side,
            "participant_role": self.participant_role,
            "selection_name": self.selection_name,
            "event_id": self.event_id,
            "event_name": self.event_name,
            "reason": self.reason,
            "match_confidence": self.match_confidence,
            "captured_at": self.captured_at,
        }


@dataclass
class TeamPropOddsComparison:
    """Consolidated reference vs execution odds comparison for Team Props."""
    reference_best_odds: Optional[float]
    reference_best_bookmaker: Optional[str]
    reference_odds_list: List[Dict[str, Any]] = field(default_factory=list)
    execution_odds: Dict[str, TeamExecutionOdds] = field(default_factory=dict)
    best_executable_odds: Optional[float] = None
    best_executable_bookmaker: Optional[str] = None
    execution_status: str = "REFERENCE_ONLY"  # "BETTABLE", "NO_EXECUTION_MARKET", "NO_EXECUTION_ODDS", "MATCH_UNCERTAIN", "REFERENCE_ONLY"
    match_confidence: float = 0.0
    canonical_team_prop_key: Optional[str] = None
    diagnostics: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "reference_best_odds": self.reference_best_odds,
            "reference_best_bookmaker": self.reference_best_bookmaker,
            "reference_odds_list": self.reference_odds_list,
            "execution_odds": {k: v.to_dict() for k, v in self.execution_odds.items()},
            "best_executable_odds": self.best_executable_odds,
            "best_executable_bookmaker": self.best_executable_bookmaker,
            "execution_status": self.execution_status,
            "match_confidence": self.match_confidence,
            "canonical_team_prop_key": self.canonical_team_prop_key,
            "diagnostics": self.diagnostics,
        }


class TeamPropExecutionMatcher:
    """Matches Team Props intelligence with Superbet and Betclic execution pipelines."""

    SUPPORTED_EXECUTION_BOOKMAKERS = ("Superbet", "Betclic")

    def __init__(
        self,
        canonical_events: Optional[List[Any]] = None,
        normalized_quotes: Optional[List[NormalizedExecutionQuote]] = None,
    ):
        self.canonical_events = canonical_events or []
        self.normalized_quotes = normalized_quotes or []

        self.telemetry = {
            "execution_candidates": 0,
            "fixture_matches": 0,
            "team_matches": 0,
            "market_matches": 0,
            "active_execution_odds": 0,
            "bettable": 0,
            "reference_only": 0,
            "no_execution_market": 0,
            "no_execution_odds": 0,
            "match_uncertain": 0,
        }

    @staticmethod
    def strip_accents(s: str) -> str:
        """Removes diacritics and accents."""
        if not s:
            return ""
        nfkd = unicodedata.normalize("NFKD", s)
        return "".join([c for c in nfkd if not unicodedata.combining(c)])

    @staticmethod
    def is_team_match(target_team: str, candidate_name: str) -> Tuple[bool, float]:
        """Checks if candidate team name represents the target team with deterministic confidence."""
        if not target_team or not candidate_name:
            return False, 0.0

        norm_target, _ = normalize_team_name(target_team)
        norm_candidate, _ = normalize_team_name(candidate_name)

        if norm_target == norm_candidate:
            return True, 1.0

        t_clean = TeamPropExecutionMatcher.strip_accents(norm_target.lower())
        c_clean = TeamPropExecutionMatcher.strip_accents(norm_candidate.lower())

        if t_clean == c_clean:
            return True, 0.98

        t_words = set(t_clean.split())
        c_words = set(c_clean.split())
        stop_words = {"fc", "cf", "afc", "sc", "ac", "united", "city", "de", "la", "real", "sporting", "club"}
        t_core = t_words - stop_words
        c_core = c_words - stop_words

        if t_core and c_core and t_core == c_core:
            return True, 0.92

        if t_core and c_core and (t_core.issubset(c_core) or c_core.issubset(t_core)):
            return True, 0.85

        return False, 0.0

    @staticmethod
    def is_fixture_match(
        target_home: str,
        target_away: str,
        cand_home: str,
        cand_away: str,
    ) -> Tuple[bool, float]:
        """Deterministic fixture pairing verification with inverted match rejection."""
        home_match, h_conf = TeamPropExecutionMatcher.is_team_match(target_home, cand_home)
        away_match, a_conf = TeamPropExecutionMatcher.is_team_match(target_away, cand_away)

        if home_match and away_match:
            return True, min(h_conf, a_conf)

        # Reject inverted home/away fixtures
        inv_home_match, _ = TeamPropExecutionMatcher.is_team_match(target_home, cand_away)
        inv_away_match, _ = TeamPropExecutionMatcher.is_team_match(target_away, cand_home)
        if inv_home_match and inv_away_match:
            return False, 0.0

        return False, 0.0

    def match_execution_odds(
        self,
        team: str,
        opponent: str,
        stat_type: str,
        line: float,
        side: str = "OVER",
        participant_role: str = "HOME",
        period: str = "FULL_TIME",
        reference_odds: Optional[List[Dict[str, Any]]] = None,
        cached_execution_events: Optional[List[Any]] = None,
        normalized_quotes: Optional[List[NormalizedExecutionQuote]] = None,
        event_id: Optional[str] = None,
    ) -> TeamPropOddsComparison:
        """Matches a team proposition against Superbet and Betclic execution quotes with strict invariants."""
        self.telemetry["execution_candidates"] += 1

        canon_key = CanonicalTeamPropKey(
            team=team,
            opponent=opponent,
            stat_type=stat_type,
            line=Decimal(str(line)),
            participant_role=participant_role,
            side=side,
            period=period,
            event_id=event_id,
        )

        canonical_stat = canon_key.stat_type
        target_role = canon_key.participant_role
        target_side = canon_key.side
        target_line = float(canon_key.line)
        target_period = canon_key.period

        ref_list = reference_odds or []
        ref_best_odds: Optional[float] = None
        ref_best_bookie: Optional[str] = None

        for ro in ref_list:
            ro_line = float(ro.get("line") or 0.0)
            ro_side = str(ro.get("side") or "OVER").upper()
            ro_odds = float(ro.get("decimal_odds") or ro.get("odds") or 0.0)
            ro_book = str(ro.get("bookmaker") or "Reference")

            if abs(ro_line - target_line) < 0.01 and ro_side == target_side and ro_odds > 1.0:
                if ref_best_odds is None or ro_odds > ref_best_odds:
                    ref_best_odds = ro_odds
                    ref_best_bookie = ro_book

        quotes_pool = normalized_quotes if normalized_quotes is not None else self.normalized_quotes

        execution_results: Dict[str, TeamExecutionOdds] = {}
        best_exec_odds: Optional[float] = None
        best_exec_bookie: Optional[str] = None
        overall_confidence: float = 0.0

        for bm_name in self.SUPPORTED_EXECUTION_BOOKMAKERS:
            bm_quotes = [q for q in quotes_pool if q.bookmaker.lower() == bm_name.lower()]
            if not bm_quotes:
                execution_results[bm_name] = TeamExecutionOdds(
                    bookmaker=bm_name,
                    status="UNAVAILABLE",
                    reason=f"No active {bm_name} data in execution pipeline",
                )
                continue

            matched_quote: Optional[NormalizedExecutionQuote] = None
            max_conf = 0.0

            for q in bm_quotes:
                if str(q.scope).upper() != "TEAM":
                    continue

                if str(q.period).upper() != target_period:
                    continue

                q_stat = STAT_TYPE_CANONICAL_MAP.get(q.stat_type.lower().replace(" ", "_"), q.stat_type.upper())
                if q_stat != canonical_stat:
                    continue

                q_home = q.fixture.split(" vs ")[0] if " vs " in q.fixture else (q.fixture.split(" - ")[0] if " - " in q.fixture else "")
                q_away = q.fixture.split(" vs ")[1] if " vs " in q.fixture else (q.fixture.split(" - ")[1] if " - " in q.fixture else "")

                t_home = team if target_role == "HOME" else opponent
                t_away = opponent if target_role == "HOME" else team

                fix_match, fix_conf = self.is_fixture_match(t_home, t_away, q_home, q_away)
                if not fix_match:
                    continue

                self.telemetry["fixture_matches"] += 1

                q_role = str(q.participant_role or "").upper()
                if not q_role:
                    if q.team and self.is_team_match(t_home, q.team)[0]:
                        q_role = "HOME"
                    elif q.team and self.is_team_match(t_away, q.team)[0]:
                        q_role = "AWAY"

                if q_role != target_role:
                    continue

                self.telemetry["team_matches"] += 1

                q_side = q.side.upper()
                if q_side != target_side:
                    continue

                # STRICT EXACT LINE CHECK: 1.5 strictly matches 1.5, never 2.5 or 0.5. No fallback!
                if abs(float(q.line) - target_line) >= 0.01:
                    continue

                self.telemetry["market_matches"] += 1

                if q.active and q.odds > 1.0:
                    matched_quote = q
                    max_conf = fix_conf
                    break
                elif not q.active or q.odds <= 1.0:
                    matched_quote = q
                    max_conf = fix_conf

            if matched_quote:
                overall_confidence = max(overall_confidence, max_conf)
                if matched_quote.active and matched_quote.odds > 1.0:
                    execution_results[bm_name] = TeamExecutionOdds(
                        bookmaker=bm_name,
                        status="AVAILABLE",
                        decimal_odds=matched_quote.odds,
                        line=matched_quote.line,
                        side=matched_quote.side,
                        participant_role=target_role,
                        selection_name=matched_quote.selection_name,
                        event_id=matched_quote.event_id,
                        event_name=matched_quote.fixture,
                        match_confidence=max_conf,
                    )
                    self.telemetry["active_execution_odds"] += 1
                    if best_exec_odds is None or matched_quote.odds > best_exec_odds:
                        best_exec_odds = matched_quote.odds
                        best_exec_bookie = bm_name
                else:
                    execution_results[bm_name] = TeamExecutionOdds(
                        bookmaker=bm_name,
                        status="NO_ODDS",
                        line=matched_quote.line,
                        side=matched_quote.side,
                        participant_role=target_role,
                        reason="Market mapped but bookmaker price inactive/suspended",
                        match_confidence=max_conf,
                    )
            else:
                execution_results[bm_name] = TeamExecutionOdds(
                    bookmaker=bm_name,
                    status="UNAVAILABLE",
                    reason=f"No exact line ({target_line}) {canonical_stat} market found at {bm_name}",
                )

        has_bettable = any(v.status == "AVAILABLE" and v.decimal_odds and v.decimal_odds > 1.0 for v in execution_results.values())
        has_inactive = any(v.status == "NO_ODDS" for v in execution_results.values())

        if has_bettable:
            status = "BETTABLE"
            self.telemetry["bettable"] += 1
        elif has_inactive:
            status = "NO_EXECUTION_ODDS"
            self.telemetry["no_execution_odds"] += 1
        elif ref_best_odds is not None and ref_best_odds > 1.0:
            status = "REFERENCE_ONLY"
            self.telemetry["reference_only"] += 1
        else:
            status = "NO_EXECUTION_MARKET"
            self.telemetry["no_execution_market"] += 1

        return TeamPropOddsComparison(
            reference_best_odds=ref_best_odds,
            reference_best_bookmaker=ref_best_bookie,
            reference_odds_list=ref_list,
            execution_odds=execution_results,
            best_executable_odds=best_exec_odds,
            best_executable_bookmaker=best_exec_bookie,
            execution_status=status,
            match_confidence=round(overall_confidence, 2),
            canonical_team_prop_key=canon_key.to_key_string(),
            diagnostics={
                "canonical_key": canon_key.to_key_string(),
                "target_team": team,
                "target_opponent": opponent,
                "target_role": target_role,
                "target_metric": canonical_stat,
                "target_line": target_line,
                "target_side": target_side,
                "target_period": target_period,
            },
        )