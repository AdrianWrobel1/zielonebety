"""
Team Prop Execution Bookmaker Matcher (Stage 30)

Integrates team prop intelligence with ZieloneBety's execution bookmakers
(Superbet and Betclic). Compares reference odds (Bet365, Paddy Power, etc.) against
actionable Polish execution odds, strictly requiring exact team, fixture, metric,
participant role (HOME/AWAY), period, direction, and exact line without cross-market fallback.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple, Set, Union
import logging
import re
import unicodedata

from normalization.market_identity import normalize_line
from normalization.identity import normalize_team_name
from normalization.aliases import resolve_canonical_team_name
from scanner.execution_providers import NormalizedExecutionQuote

logger = logging.getLogger("scanner.team_prop_execution_matcher")


class MatchingReasonCode(str, Enum):
    """Standardized taxonomy for team prop matching decisions and rejections."""
    MATCHED = "MATCHED"
    EVENT_UNMATCHED = "EVENT_UNMATCHED"
    EVENT_AMBIGUOUS = "EVENT_AMBIGUOUS"
    TEAM_UNMATCHED = "TEAM_UNMATCHED"
    TEAM_AMBIGUOUS = "TEAM_AMBIGUOUS"
    MARKET_UNMATCHED = "MARKET_UNMATCHED"
    LINE_MISMATCH = "LINE_MISMATCH"
    SELECTION_MISMATCH = "SELECTION_MISMATCH"
    SCOPE_MISMATCH = "SCOPE_MISMATCH"
    ODDS_UNAVAILABLE = "ODDS_UNAVAILABLE"
    ODDS_INACTIVE = "ODDS_INACTIVE"


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
    "cornerkicks": "CORNERS",
    "corner_kicks": "CORNERS",
    "totalshotsongoal": "SHOTS",
    "total_shots_on_goal": "SHOTS",
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
class TeamPropMatchProvenance:
    """Lightweight audit trail tracing StatsHub source to Polish bookmaker team prop quote."""
    statshub_fixture_id: Optional[str] = None
    statshub_event_internal_id: Optional[Union[int, str]] = None
    statshub_team_id: Optional[Union[int, str]] = None
    canonical_team_prop_key: Optional[str] = None
    target_stat: str = ""
    target_line: float = 0.5
    target_side: str = "OVER"
    target_role: str = "HOME"
    matched_bookmakers: List[str] = field(default_factory=list)
    unmatched_bookmakers: List[str] = field(default_factory=list)
    reasons_by_bookmaker: Dict[str, str] = field(default_factory=dict)
    reason_codes_by_bookmaker: Dict[str, str] = field(default_factory=dict)
    matched_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "statshub_fixture_id": self.statshub_fixture_id,
            "statshub_event_internal_id": self.statshub_event_internal_id,
            "statshub_team_id": self.statshub_team_id,
            "canonical_team_prop_key": self.canonical_team_prop_key,
            "target_stat": self.target_stat,
            "target_line": self.target_line,
            "target_side": self.target_side,
            "target_role": self.target_role,
            "matched_bookmakers": self.matched_bookmakers,
            "unmatched_bookmakers": self.unmatched_bookmakers,
            "reasons_by_bookmaker": self.reasons_by_bookmaker,
            "reason_codes_by_bookmaker": self.reason_codes_by_bookmaker,
            "matched_at": self.matched_at,
        }


@dataclass
class TeamExecutionOdds:
    """Execution bookmaker quote for a team proposition."""
    bookmaker: str  # "Superbet", "Betclic"
    status: str  # "AVAILABLE", "NO_ODDS", "UNAVAILABLE", "UNCERTAIN"
    reason_code: str = MatchingReasonCode.MARKET_UNMATCHED.value
    reason: Optional[str] = None
    decimal_odds: Optional[float] = None
    line: Optional[float] = None
    side: Optional[str] = None
    participant_role: Optional[str] = None
    selection_id: Optional[str] = None
    selection_name: Optional[str] = None
    market_id: Optional[str] = None
    market_name: Optional[str] = None
    event_id: Optional[str] = None
    event_name: Optional[str] = None
    match_confidence: float = 0.0
    captured_at: Optional[str] = None
    provenance: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "bookmaker": self.bookmaker,
            "status": self.status,
            "reason_code": self.reason_code,
            "reason": self.reason,
            "decimal_odds": self.decimal_odds,
            "line": self.line,
            "side": self.side,
            "participant_role": self.participant_role,
            "selection_id": self.selection_id,
            "selection_name": self.selection_name,
            "market_id": self.market_id,
            "market_name": self.market_name,
            "event_id": self.event_id,
            "event_name": self.event_name,
            "match_confidence": self.match_confidence,
            "captured_at": self.captured_at,
            "provenance": self.provenance,
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
    primary_reason_code: str = MatchingReasonCode.MARKET_UNMATCHED.value
    match_confidence: float = 0.0
    canonical_team_prop_key: Optional[str] = None
    provenance: Dict[str, Any] = field(default_factory=dict)
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
            "primary_reason_code": self.primary_reason_code,
            "match_confidence": self.match_confidence,
            "canonical_team_prop_key": self.canonical_team_prop_key,
            "provenance": self.provenance,
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

        raw_target, t_tok_raw = normalize_team_name(target_team)
        raw_candidate, c_tok_raw = normalize_team_name(candidate_name)

        norm_target, t_toks = resolve_canonical_team_name(raw_target, t_tok_raw)
        norm_candidate, c_toks = resolve_canonical_team_name(raw_candidate, c_tok_raw)

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
        target_kickoff: Optional[str] = None,
        candidate_kickoff: Optional[str] = None,
    ) -> Tuple[bool, float]:
        """Deterministic fixture pairing verification with inverted match rejection.

        Inversion policy (explicit): swapped home/away orientation is REJECTED
        here because team props are role-sensitive — attributing HOME team
        totals to the away listing would corrupt valuation. This differs
        deliberately from ``PropExecutionMatcher``, which accepts inversion
        since the player side is resolved independently.

        Kickoff rule (P1-NEW-001): same shared ±24h compatibility window as
        the player matcher and settlement. Both kickoffs present+parseable
        but apart -> no match. Either missing -> names-only (backward
        compatible with all existing call sites).
        """
        from normalization.identity import are_kickoffs_compatible

        home_match, h_conf = TeamPropExecutionMatcher.is_team_match(target_home, cand_home)
        away_match, a_conf = TeamPropExecutionMatcher.is_team_match(target_away, cand_away)

        if home_match and away_match:
            if are_kickoffs_compatible(target_kickoff, candidate_kickoff) is False:
                return False, 0.0
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
        statshub_fixture_id: Optional[str] = None,
        statshub_event_internal_id: Optional[Union[int, str]] = None,
        statshub_team_id: Optional[Union[int, str]] = None,
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
            event_id=event_id or statshub_fixture_id,
        )

        canonical_stat = canon_key.stat_type
        target_role = canon_key.participant_role
        target_side = canon_key.side
        target_line = float(canon_key.line)
        target_period = canon_key.period

        ref_list = reference_odds or []
        ref_best_odds: Optional[float] = None
        ref_best_bookie: Optional[str] = None
        matched_ref_list: List[Dict[str, Any]] = []

        for ro in ref_list:
            ro_line = float(ro.get("line") or 0.0)
            ro_side = str(ro.get("side") or "OVER").upper()
            ro_odds = float(ro.get("decimal_odds") or ro.get("odds") or 0.0)
            ro_book = str(ro.get("bookmaker") or "Reference")

            if abs(ro_line - target_line) < 0.01 and ro_side == target_side:
                matched_ref_list.append(ro)
                if ro_odds > 1.0 and (ref_best_odds is None or ro_odds > ref_best_odds):
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
                    reason_code=MatchingReasonCode.EVENT_UNMATCHED.value,
                    reason=f"No active {bm_name} data in execution pipeline",
                )
                continue

            matched_quote: Optional[NormalizedExecutionQuote] = None
            max_conf = 0.0

            # Detailed diagnostic triage flags
            event_matched = False
            team_matched = False
            stat_matched = False
            scope_matched = False
            line_matched = False
            side_matched = False
            offered_lines: Set[float] = set()

            t_home = team if target_role == "HOME" else opponent
            t_away = opponent if target_role == "HOME" else team

            for q in bm_quotes:
                q_home = q.fixture.split(" vs ")[0] if " vs " in q.fixture else (q.fixture.split(" - ")[0] if " - " in q.fixture else "")
                q_away = q.fixture.split(" vs ")[1] if " vs " in q.fixture else (q.fixture.split(" - ")[1] if " - " in q.fixture else "")

                fix_match, fix_conf = self.is_fixture_match(t_home, t_away, q_home, q_away)
                if not fix_match:
                    continue
                event_matched = True

                q_role = str(q.participant_role or "").upper()
                if not q_role:
                    if q.team and self.is_team_match(t_home, q.team)[0]:
                        q_role = "HOME"
                    elif q.team and self.is_team_match(t_away, q.team)[0]:
                        q_role = "AWAY"

                if q_role != target_role:
                    continue
                team_matched = True

                if str(q.scope).upper() != "TEAM":
                    continue

                if str(q.period).upper() != target_period:
                    continue
                scope_matched = True

                q_stat = STAT_TYPE_CANONICAL_MAP.get(q.stat_type.lower().replace(" ", "_"), q.stat_type.upper())
                if q_stat != canonical_stat:
                    continue
                stat_matched = True

                offered_lines.add(float(q.line))

                # STRICT EXACT LINE CHECK: 1.5 strictly matches 1.5, never 2.5 or 0.5. No fallback!
                if abs(float(q.line) - target_line) >= 0.01:
                    continue
                line_matched = True

                q_side = q.side.upper()
                if q_side != target_side:
                    continue
                side_matched = True

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
                        reason_code=MatchingReasonCode.MATCHED.value,
                        reason=f"Matched {bm_name} team prop for {team} ({canonical_stat} {target_line} {target_side}).",
                        decimal_odds=matched_quote.odds,
                        line=matched_quote.line,
                        side=matched_quote.side,
                        participant_role=target_role,
                        selection_id=getattr(matched_quote, "selection_name", None),
                        selection_name=matched_quote.selection_name,
                        market_id=getattr(matched_quote, "market_type", None),
                        market_name=matched_quote.market_name or f"TEAM_{canonical_stat}",
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
                        reason_code=MatchingReasonCode.ODDS_UNAVAILABLE.value,
                        line=matched_quote.line,
                        side=matched_quote.side,
                        participant_role=target_role,
                        selection_id=getattr(matched_quote, "selection_name", None),
                        selection_name=matched_quote.selection_name,
                        market_id=getattr(matched_quote, "market_type", None),
                        market_name=matched_quote.market_name or f"TEAM_{canonical_stat}",
                        reason="Market mapped but bookmaker price inactive/suspended",
                        match_confidence=max_conf,
                    )
            elif not event_matched:
                execution_results[bm_name] = TeamExecutionOdds(
                    bookmaker=bm_name,
                    status="UNAVAILABLE",
                    reason_code=MatchingReasonCode.EVENT_UNMATCHED.value,
                    reason=f"No {bm_name} event found matching {t_home} vs {t_away}",
                )
            elif not team_matched:
                execution_results[bm_name] = TeamExecutionOdds(
                    bookmaker=bm_name,
                    status="UNAVAILABLE",
                    reason_code=MatchingReasonCode.TEAM_UNMATCHED.value,
                    reason=f"Team {team} ({target_role}) not matched in {bm_name} fixture",
                )
            elif not scope_matched:
                execution_results[bm_name] = TeamExecutionOdds(
                    bookmaker=bm_name,
                    status="UNAVAILABLE",
                    reason_code=MatchingReasonCode.SCOPE_MISMATCH.value,
                    reason=f"{bm_name} offers markets for {team}, but not with scope=TEAM or period={target_period}",
                )
            elif not stat_matched:
                execution_results[bm_name] = TeamExecutionOdds(
                    bookmaker=bm_name,
                    status="UNAVAILABLE",
                    reason_code=MatchingReasonCode.MARKET_UNMATCHED.value,
                    reason=f"No {canonical_stat} team market found at {bm_name} for {team}",
                )
            elif not line_matched:
                lines_fmt = ", ".join(str(l) for l in sorted(list(offered_lines))) if offered_lines else "other"
                execution_results[bm_name] = TeamExecutionOdds(
                    bookmaker=bm_name,
                    status="UNAVAILABLE",
                    reason_code=MatchingReasonCode.LINE_MISMATCH.value,
                    reason=f"{bm_name} offers {canonical_stat} for {team} at line(s) {lines_fmt}, but target line is {target_line}",
                )
            elif not side_matched:
                execution_results[bm_name] = TeamExecutionOdds(
                    bookmaker=bm_name,
                    status="UNAVAILABLE",
                    reason_code=MatchingReasonCode.SELECTION_MISMATCH.value,
                    reason=f"{bm_name} offers line {target_line}, but side {target_side} is not available",
                )
            else:
                execution_results[bm_name] = TeamExecutionOdds(
                    bookmaker=bm_name,
                    status="UNAVAILABLE",
                    reason_code=MatchingReasonCode.MARKET_UNMATCHED.value,
                    reason=f"No exact line ({target_line}) {canonical_stat} market found at {bm_name}",
                )

        has_bettable = any(v.status == "AVAILABLE" and v.decimal_odds and v.decimal_odds > 1.0 for v in execution_results.values())
        has_inactive = any(v.status == "NO_ODDS" for v in execution_results.values())
        primary_reason = MatchingReasonCode.MARKET_UNMATCHED.value

        if has_bettable:
            status = "BETTABLE"
            primary_reason = MatchingReasonCode.MATCHED.value
            self.telemetry["bettable"] += 1
        elif has_inactive:
            status = "NO_EXECUTION_ODDS"
            primary_reason = MatchingReasonCode.ODDS_UNAVAILABLE.value
            self.telemetry["no_execution_odds"] += 1
        elif ref_best_odds is not None and ref_best_odds > 1.0:
            status = "REFERENCE_ONLY"
            for q in execution_results.values():
                if q.reason_code != MatchingReasonCode.MATCHED.value:
                    primary_reason = q.reason_code
                    break
            self.telemetry["reference_only"] += 1
        else:
            status = "NO_EXECUTION_MARKET"
            for q in execution_results.values():
                if q.reason_code != MatchingReasonCode.MATCHED.value:
                    primary_reason = q.reason_code
                    break
            self.telemetry["no_execution_market"] += 1

        # Build Team Prop Match Provenance
        provenance_obj = TeamPropMatchProvenance(
            statshub_fixture_id=statshub_fixture_id or event_id,
            statshub_event_internal_id=statshub_event_internal_id,
            statshub_team_id=statshub_team_id,
            canonical_team_prop_key=canon_key.to_key_string(),
            target_stat=canonical_stat,
            target_line=target_line,
            target_side=target_side,
            target_role=target_role,
            matched_bookmakers=[b for b, q in execution_results.items() if q.status == "AVAILABLE"],
            unmatched_bookmakers=[b for b, q in execution_results.items() if q.status != "AVAILABLE"],
            reasons_by_bookmaker={b: q.reason or "" for b, q in execution_results.items()},
            reason_codes_by_bookmaker={b: q.reason_code for b, q in execution_results.items()},
        )

        for q in execution_results.values():
            q.provenance = provenance_obj.to_dict()

        return TeamPropOddsComparison(
            reference_best_odds=ref_best_odds,
            reference_best_bookmaker=ref_best_bookie,
            reference_odds_list=ref_list,
            execution_odds=execution_results,
            best_executable_odds=best_exec_odds,
            best_executable_bookmaker=best_exec_bookie,
            execution_status=status,
            primary_reason_code=primary_reason,
            match_confidence=round(overall_confidence, 2),
            canonical_team_prop_key=canon_key.to_key_string(),
            provenance=provenance_obj.to_dict(),
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

    def match_statshub_team_prop(
        self,
        statshub_team_prop: Any,
        cached_execution_events: Optional[List[Any]] = None,
        normalized_quotes: Optional[List[NormalizedExecutionQuote]] = None,
    ) -> TeamPropOddsComparison:
        """High-level integration method taking a StatsHubTeamPropResult directly."""
        ts = getattr(statshub_team_prop, "team_stat", None)
        if not ts:
            raise ValueError("Invalid StatsHubTeamPropResult: missing team_stat")

        fix = ts.fixture
        target_line = float(ts.line) if ts.line is not None else 0.5
        target_side = (ts.odds_type or "OVER").upper()
        stat_type = ts.stat_type or "CORNERS"
        participant_role = (getattr(ts, "participant_role", None) or ("HOME" if getattr(ts, "is_home", True) else "AWAY")).upper()

        all_ref_odds = []
        for o in (ts.bookmaker_odds or []):
            all_ref_odds.append({
                "bookmaker": o.bookmaker,
                "line": float(o.line),
                "side": o.side.upper(),
                "decimal_odds": float(o.decimal_odds),
                "bookmaker_id": getattr(o, "bookmaker_id", None),
                "metadata": getattr(o, "metadata", {}),
            })

        return self.match_execution_odds(
            team=ts.team_name,
            opponent=ts.opponent_name,
            stat_type=stat_type,
            line=target_line,
            side=target_side,
            participant_role=participant_role,
            period="FULL_TIME",
            reference_odds=all_ref_odds,
            cached_execution_events=cached_execution_events,
            normalized_quotes=normalized_quotes,
            event_id=fix.fixture_id if fix else None,
            statshub_fixture_id=fix.fixture_id if fix else None,
            statshub_event_internal_id=getattr(fix, "event_internal_id", None) if fix else None,
            statshub_team_id=getattr(ts, "team_id", None),
        )