"""
Player Prop Execution Bookmaker Matcher (Stage 17)

Integrates StatsHub player prop intelligence with ZieloneBety's execution bookmakers
(Superbet and Betclic). Compares reference odds (Bet365, Paddy Power, etc.) against
actionable Polish execution odds, clearly flagging unavailable markets without guessing.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple, Set, Union
import logging
import re
import unicodedata

from normalization.market_identity import normalize_player_name, normalize_line
from normalization.identity import normalize_team_name
from normalization.aliases import resolve_canonical_team_name
from scanner.execution_providers import NormalizedExecutionQuote

logger = logging.getLogger("scanner.prop_execution_matcher")


class MatchingReasonCode(str, Enum):
    """Standardized taxonomy for prop matching decisions and rejections."""
    MATCHED = "MATCHED"
    EVENT_UNMATCHED = "EVENT_UNMATCHED"
    EVENT_AMBIGUOUS = "EVENT_AMBIGUOUS"
    PLAYER_UNMATCHED = "PLAYER_UNMATCHED"
    PLAYER_AMBIGUOUS = "PLAYER_AMBIGUOUS"
    TEAM_UNMATCHED = "TEAM_UNMATCHED"
    MARKET_UNMATCHED = "MARKET_UNMATCHED"
    LINE_MISMATCH = "LINE_MISMATCH"
    SELECTION_MISMATCH = "SELECTION_MISMATCH"
    SCOPE_MISMATCH = "SCOPE_MISMATCH"
    ODDS_UNAVAILABLE = "ODDS_UNAVAILABLE"
    ODDS_INACTIVE = "ODDS_INACTIVE"


# Canonical Stat Type mappings across providers
STAT_TYPE_CANONICAL_MAP: Dict[str, str] = {
    "shots": "SHOTS",
    "shot": "SHOTS",
    "player_shots": "SHOTS",
    "shotsontarget": "SHOTS_ON_TARGET",
    "shots_on_target": "SHOTS_ON_TARGET",
    "player_shots_on_target": "SHOTS_ON_TARGET",
    "fouls": "FOULS",
    "foul": "FOULS",
    "player_fouls": "FOULS",
    "cards": "CARDS",
    "card": "CARDS",
    "player_cards": "CARDS",
    "assists": "ASSISTS",
    "assist": "ASSISTS",
    "player_assists": "ASSISTS",
    "goals": "GOALS",
    "goal": "GOALS",
    "player_goals": "GOALS",
    "passes": "PASSES",
    "pass": "PASSES",
    "player_passes": "PASSES",
    "tackles": "TACKLES",
    "tackle": "TACKLES",
    "player_tackles": "TACKLES",
}


@dataclass(frozen=True)
class CanonicalPropKey:
    """Deterministic, provider-independent canonical key for a player proposition."""
    player_name: str
    team: str
    opponent: str
    stat_type: str
    line: Decimal
    side: str = "OVER"
    event_id: Optional[str] = None

    def __post_init__(self):
        norm_p = normalize_player_name(self.player_name) or self.player_name.lower().strip()
        norm_t, t_tok = normalize_team_name(self.team)
        norm_o, o_tok = normalize_team_name(self.opponent)
        canon_t, _ = resolve_canonical_team_name(norm_t, t_tok)
        canon_o, _ = resolve_canonical_team_name(norm_o, o_tok)
        norm_st = STAT_TYPE_CANONICAL_MAP.get(self.stat_type.lower().replace(" ", "_"), self.stat_type.upper())
        norm_line = normalize_line(self.line) or Decimal("0.5")

        object.__setattr__(self, "player_name", norm_p)
        object.__setattr__(self, "team", canon_t or norm_t)
        object.__setattr__(self, "opponent", canon_o or norm_o)
        object.__setattr__(self, "stat_type", norm_st)
        object.__setattr__(self, "line", norm_line)
        object.__setattr__(self, "side", self.side.upper())

    def to_key_string(self) -> str:
        """Returns deterministic canonical string representation."""
        line_str = f"{self.line:f}".rstrip("0").rstrip(".") if "." in f"{self.line:f}" else f"{self.line:f}"
        p_str = self.player_name.replace(" ", "_")
        t_str = self.team.replace(" ", "_")
        o_str = self.opponent.replace(" ", "_")
        return f"prop:{t_str}:{o_str}:{p_str}:{self.stat_type}:{self.side}:{line_str}"


@dataclass
class PropMatchProvenance:
    """Lightweight audit trail tracing StatsHub source to Polish bookmaker quote."""
    statshub_fixture_id: Optional[str] = None
    statshub_event_internal_id: Optional[Union[int, str]] = None
    statshub_player_id: Optional[Union[int, str]] = None
    canonical_prop_key: Optional[str] = None
    target_stat: str = ""
    target_line: float = 0.5
    target_side: str = "OVER"
    matched_bookmakers: List[str] = field(default_factory=list)
    unmatched_bookmakers: List[str] = field(default_factory=list)
    reasons_by_bookmaker: Dict[str, str] = field(default_factory=dict)
    reason_codes_by_bookmaker: Dict[str, str] = field(default_factory=dict)
    matched_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "statshub_fixture_id": self.statshub_fixture_id,
            "statshub_event_internal_id": self.statshub_event_internal_id,
            "statshub_player_id": self.statshub_player_id,
            "canonical_prop_key": self.canonical_prop_key,
            "target_stat": self.target_stat,
            "target_line": self.target_line,
            "target_side": self.target_side,
            "matched_bookmakers": self.matched_bookmakers,
            "unmatched_bookmakers": self.unmatched_bookmakers,
            "reasons_by_bookmaker": self.reasons_by_bookmaker,
            "reason_codes_by_bookmaker": self.reason_codes_by_bookmaker,
            "matched_at": self.matched_at,
        }


@dataclass
class ExecutionBookmakerOdds:
    """Execution bookmaker quote for a player proposition."""
    bookmaker: str  # "Superbet", "Betclic"
    status: str  # "AVAILABLE", "NO_ODDS", "UNAVAILABLE", "UNCERTAIN"
    reason_code: str = MatchingReasonCode.MARKET_UNMATCHED.value
    reason: Optional[str] = None
    decimal_odds: Optional[float] = None
    line: Optional[float] = None
    side: Optional[str] = None
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
class PropOddsComparison:
    """Consolidated reference vs execution odds comparison."""
    reference_best_odds: Optional[float]
    reference_best_bookmaker: Optional[str]
    reference_odds_list: List[Dict[str, Any]] = field(default_factory=list)
    execution_odds: Dict[str, ExecutionBookmakerOdds] = field(default_factory=dict)
    best_executable_odds: Optional[float] = None
    best_executable_bookmaker: Optional[str] = None
    execution_status: str = "REFERENCE_ONLY"  # "BETTABLE", "NO_EXECUTION_MARKET", "NO_EXECUTION_ODDS", "MATCH_UNCERTAIN", "REFERENCE_ONLY"
    primary_reason_code: str = MatchingReasonCode.MARKET_UNMATCHED.value
    match_confidence: float = 0.0
    canonical_prop_key: Optional[str] = None
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
            "canonical_prop_key": self.canonical_prop_key,
            "provenance": self.provenance,
            "diagnostics": self.diagnostics,
        }



class PropExecutionMatcher:
    """Matches StatsHub player props with Superbet and Betclic execution pipelines."""

    SUPPORTED_EXECUTION_BOOKMAKERS = ("Superbet", "Betclic")

    def __init__(
        self,
        canonical_events: Optional[List[Any]] = None,
        normalized_quotes: Optional[List[NormalizedExecutionQuote]] = None,
    ):
        self.canonical_events = canonical_events or []
        self.normalized_quotes = normalized_quotes or []

        # Diagnostics telemetry
        self.telemetry = {
            "execution_candidates": 0,
            "fixture_matches": 0,
            "player_matches": 0,
            "market_matches": 0,
            "active_execution_odds": 0,
            "bettable": 0,
            "reference_only": 0,
            "no_execution_market": 0,
            "no_execution_odds": 0,
            "match_uncertain": 0,
        }

    @staticmethod
    def _is_event_for_bookmaker(ev: Any, bookmaker: str) -> bool:
        """Determines if a candidate event belongs to the target execution bookmaker."""
        bm_lower = bookmaker.lower()
        if hasattr(ev, "event") and hasattr(ev, "odds_list"):  # NormalizedGraph
            provider_ids = getattr(ev.event, "provider_ids", {}) or {}
            if bm_lower in provider_ids or any(k.lower() == bm_lower for k in provider_ids):
                return True
            if any(getattr(o, "bookmaker", "").lower() == bm_lower for o in ev.odds_list):
                return True
            # If provider_ids is empty and odds_list is empty, treat as generic candidate
            if not provider_ids and not ev.odds_list:
                return True
            return False
        if isinstance(ev, dict):
            provider = ev.get("bookmaker") or ev.get("provider") or ev.get("source")
            if provider:
                return str(provider).lower() == bm_lower
            provider_ids = ev.get("provider_ids") or {}
            if provider_ids:
                return bm_lower in provider_ids or any(k.lower() == bm_lower for k in provider_ids)
            # Check odds inside markets if present
            markets = ev.get("markets", [])
            for m in markets:
                for s in m.get("selections", []):
                    s_odds = s.get("odds", {})
                    if isinstance(s_odds, dict) and (bm_lower in s_odds or bookmaker in s_odds):
                        return True
            # If no provider info found at all, treat as generic candidate
            return True
        provider = getattr(ev, "bookmaker", None) or getattr(ev, "provider", None) or getattr(ev, "source", None)
        if provider:
            return str(provider).lower() == bm_lower
        provider_ids = getattr(ev, "provider_ids", {}) or {}
        if provider_ids:
            return bm_lower in provider_ids or any(k.lower() == bm_lower for k in provider_ids)
        return True

    @staticmethod
    def strip_accents(s: str) -> str:
        """Removes diacritics and accents (e.g. Raúl -> Raul, Touré -> Toure)."""
        if not s:
            return ""
        nfkd = unicodedata.normalize("NFKD", s)
        return "".join([c for c in nfkd if not unicodedata.combining(c)])

    @staticmethod
    def normalize_name(name: str) -> str:
        """Deterministic name normalization for matching."""
        if not name:
            return ""
        norm = normalize_player_name(name)
        if norm:
            s = norm
        else:
            s = name.lower().strip()

        s = PropExecutionMatcher.strip_accents(s)
        # Remove apostrophes completely so N'Djoli -> NDjoli, O'Connor -> OConnor
        s = s.replace("'", "").replace("’", "").replace("-", " ").replace(".", " ")
        s = re.sub(r"[^\w\s]", "", s)
        words = re.sub(r"\s+", " ", s).strip().split()
        normalized_words = []
        for w in words:
            if w in ("jr", "jnr"):
                normalized_words.append("junior")
            elif w in ("sr", "snr"):
                normalized_words.append("senior")
            else:
                normalized_words.append(w)
        return " ".join(normalized_words)


    @staticmethod
    def is_player_match(target_player: str, candidate_name: str) -> Tuple[bool, float]:
        """Checks if candidate name represents the target player with deterministic confidence."""
        t_norm = PropExecutionMatcher.normalize_name(target_player)
        c_norm = PropExecutionMatcher.normalize_name(candidate_name)

        if not t_norm or not c_norm:
            return False, 0.0

        if t_norm == c_norm:
            return True, 1.0

        t_tokens = [t for t in t_norm.split() if len(t) > 1 or t.isalpha()]
        c_tokens = [c for c in c_norm.split() if len(c) > 1 or c.isalpha()]

        t_set = set(t_tokens)
        c_set = set(c_tokens)

        # Exact token set match regardless of order (e.g. "Mbappe Kylian" vs "Kylian Mbappe")
        if t_set == c_set and len(t_set) >= 2:
            return True, 0.98

        # Target full tokens are completely contained in candidate (e.g. "Tai Abed" in "Tai Abed Kassus")
        if t_set.issubset(c_set) and len(t_set) >= 2:
            return True, 0.95

        if c_set.issubset(t_set) and len(c_set) >= 2:
            return True, 0.92

        # Single word name match (e.g. "Murilo" in "Murilo Costa" or "Cassiano")
        if len(t_tokens) == 1 and len(c_tokens) >= 2:
            if t_tokens[0] == c_tokens[0] or t_tokens[0] == c_tokens[-1]:
                return True, 0.90

        if len(c_tokens) == 1 and len(t_tokens) >= 2:
            if c_tokens[0] == t_tokens[0] or c_tokens[0] == t_tokens[-1]:
                return True, 0.90

        # Last name match + first name initial (e.g. "K. Mbappe" vs "Kylian Mbappe")
        if len(t_tokens) >= 2 and len(c_tokens) >= 2:
            if t_tokens[-1] == c_tokens[-1] and t_tokens[0][0] == c_tokens[0][0]:
                return True, 0.85

        return False, 0.0

    @staticmethod
    def is_fixture_match(
        target_team: str,
        target_opp: str,
        candidate_home: str,
        candidate_away: str,
        target_kickoff: Optional[str] = None,
        candidate_kickoff: Optional[str] = None,
    ) -> Tuple[bool, float]:
        """Checks if fixture matches target teams deterministically.

        Inversion policy (explicit): swapped home/away orientation is ACCEPTED
        here — providers may list the same fixture reversed, and the player
        side is resolved independently by ``is_player_match`` downstream.
        This differs deliberately from ``TeamPropExecutionMatcher``, which
        rejects inversion because team props are role-sensitive (HOME team
        totals attributed to the wrong side would corrupt valuation).

        Kickoff rule (P1-NEW-001): names must match first; when BOTH kickoffs
        are present and parseable they must additionally fall within the
        shared ±24h fixture window (``are_kickoffs_compatible``, the same
        time model as settlement). Same teams with clearly different
        kickoffs (e.g. league + cup rematch) do NOT match. When either
        kickoff is missing/unparseable, names-only logic applies (no recall
        loss for kickoff-less call sites).
        """
        from normalization.identity import are_kickoffs_compatible

        raw_t_team, t_tok_raw = normalize_team_name(target_team)
        raw_t_opp, o_tok_raw = normalize_team_name(target_opp)
        raw_c_home, h_tok_raw = normalize_team_name(candidate_home)
        raw_c_away, a_tok_raw = normalize_team_name(candidate_away)

        norm_t_team, t_tokens = resolve_canonical_team_name(raw_t_team, t_tok_raw)
        norm_t_opp, o_tokens = resolve_canonical_team_name(raw_t_opp, o_tok_raw)
        norm_c_home, h_tokens = resolve_canonical_team_name(raw_c_home, h_tok_raw)
        norm_c_away, a_tokens = resolve_canonical_team_name(raw_c_away, a_tok_raw)

        # 1. Exact canonical normalized match
        if (norm_t_team == norm_c_home and norm_t_opp == norm_c_away) or \
           (norm_t_team == norm_c_away and norm_t_opp == norm_c_home):
            if are_kickoffs_compatible(target_kickoff, candidate_kickoff) is False:
                return False, 0.0
            return True, 1.0

        # 2. Token subset match
        t_set = set(t_tokens)
        o_set = set(o_tokens)
        h_set = set(h_tokens)
        a_set = set(a_tokens)

        if (t_set.issubset(h_set) and o_set.issubset(a_set)) or \
           (t_set.issubset(a_set) and o_set.issubset(h_set)):
            if are_kickoffs_compatible(target_kickoff, candidate_kickoff) is False:
                return False, 0.0
            return True, 0.95

        if (h_set.issubset(t_set) and a_set.issubset(o_set)) or \
           (h_set.issubset(o_set) and a_set.issubset(t_set)):
            if are_kickoffs_compatible(target_kickoff, candidate_kickoff) is False:
                return False, 0.0
            return True, 0.92

        return False, 0.0

    @staticmethod
    def _is_stat_market_match(stat_type: str, market_type: str, raw_market_name: str = "") -> bool:
        """Checks if event market corresponds to the canonical prop stat type."""
        canonical_stat = STAT_TYPE_CANONICAL_MAP.get(stat_type.lower().replace(" ", "_"), stat_type.upper())
        mkt_upper = market_type.upper()
        raw_lower = raw_market_name.lower()

        # Reject period mismatches: full-time prop must not match 1st half / 2nd half markets
        is_half_time_market = any(k in mkt_upper for k in ("1ST_HALF", "2ND_HALF", "1_POLOWA", "2_POLOWA", "HALF_TIME", "HT_")) or any(k in raw_lower for k in ("1. połow", "1. polow", "2. połow", "2. polow", "1st half", "2nd half", "do przerwy"))
        if is_half_time_market and not any(k in stat_type.upper() for k in ("1ST_HALF", "HALF", "1_POLOWA")):
            return False

        if canonical_stat == "SHOTS":
            # Must be total shots, not shots on target
            if "ON_TARGET" in mkt_upper or "TARGET" in mkt_upper or "CELNYCH" in mkt_upper or "celnych" in raw_lower or "on target" in raw_lower:
                return False
            return (
                "PLAYER_SHOTS" in mkt_upper
                or "STRZAŁÓW" in mkt_upper
                or "STRZALOW" in mkt_upper
                or "STRZAŁY" in mkt_upper
                or "STRZALY" in mkt_upper
                or "LICZBA STRZAŁÓW" in raw_lower
                or "liczba strzalow" in raw_lower
                or (mkt_upper == "SHOTS" and "PLAYER" in mkt_upper)
            )
        elif canonical_stat == "SHOTS_ON_TARGET":
            return (
                "PLAYER_SHOTS_ON_TARGET" in mkt_upper
                or "CELNYCH STRZAŁÓW" in mkt_upper
                or "CELNYCH STRZALOW" in mkt_upper
                or "CELNE STRZAŁY" in mkt_upper
                or "celnych strzałów" in raw_lower
                or "celnych strzalow" in raw_lower
                or "celne strzały" in raw_lower
                or "shots on target" in raw_lower
            )
        elif canonical_stat == "FOULS":
            return (
                "PLAYER_FOULS" in mkt_upper
                or "FAULI" in mkt_upper
                or "FAULE" in mkt_upper
                or "fauli" in raw_lower
                or "faule" in raw_lower
            )
        elif canonical_stat == "CARDS":
            return (
                "PLAYER_CARDS" in mkt_upper
                or "KARTK" in mkt_upper
                or "kartk" in raw_lower
                or "card" in raw_lower
            )
        elif canonical_stat == "ASSISTS":
            return (
                "PLAYER_ASSISTS" in mkt_upper
                or "ASYST" in mkt_upper
                or "asyst" in raw_lower
                or "assist" in raw_lower
            )
    @staticmethod
    def _is_stat_market_match(canonical_stat: str, market_type: str, raw_market_name: str, target_period: str = "FULL_TIME") -> bool:
        """Determines if a candidate market corresponds 1:1 to the requested player stat."""
        mkt_upper = (market_type or "").upper()
        raw_lower = (raw_market_name or "").lower()

        # 1. Period check: target is FULL_TIME -> reject half-time / extra-time markets
        if target_period == "FULL_TIME":
            if any(k in raw_lower for k in ("1. połow", "1. polow", "1.połow", "1.polow", "1st half", "first half", "do przerwy", "pierwsza połow", "pierwsza polow", "w 1. połowie", "w 1. polowie", "2. połow", "2. polow", "2.połow", "2.polow", "2nd half", "second half", "druga połow", "druga polow", "w 2. połowie", "w 2. polowie")):
                return False
            if mkt_upper in ("PLAYER_GOALS_FIRST_HALF", "PLAYER_GOALS_SECOND_HALF"):
                return False

        # 2. Reject First / Last / Special goal markets when canonical_stat == "GOALS"
        if canonical_stat == "GOALS":
            if any(k in raw_lower for k in ("1. gola", "pierwszego gola", "pierwszy gol", "1. gol", "ostatniego gola", "ostatni gol", "gola głową", "gola glowa", "spoza pola karnego", "z rzutu karnego", "strzeli i wygra")):
                return False
            if mkt_upper in ("PLAYER_FIRST_GOAL", "PLAYER_LAST_GOAL"):
                return False
            return (
                mkt_upper == "PLAYER_GOALS"
                or "strzeli gola" in raw_lower
                or "strzeli przynajmniej" in raw_lower
                or "to score" in raw_lower
                or "liczba goli" in raw_lower
            )

        # 3. Reject Red card when canonical_stat == "CARDS" (standard cards)
        if canonical_stat == "CARDS":
            if "czerwon" in raw_lower or "red" in raw_lower or mkt_upper == "PLAYER_RED_CARDS":
                return False
            return (
                mkt_upper in ("PLAYER_CARDS", "PLAYER_YELLOW_CARDS")
                or "kartk" in raw_lower
                or "card" in raw_lower
            )

        # 4. Other stats: strict 1:1 match
        if canonical_stat == "SHOTS":
            if "celnych" in raw_lower or "on target" in raw_lower or mkt_upper == "PLAYER_SHOTS_ON_TARGET":
                return False
            return mkt_upper == "PLAYER_SHOTS" or "strzał" in raw_lower or "strzal" in raw_lower

        if canonical_stat == "SHOTS_ON_TARGET":
            if any(k in raw_lower for k in ("spoza pola karnego", "głową", "glowa", "nogą", "noga")):
                return False
            return mkt_upper == "PLAYER_SHOTS_ON_TARGET" or "celnych strza" in raw_lower

        if canonical_stat == "ASSISTS":
            return mkt_upper == "PLAYER_ASSISTS" or "asyst" in raw_lower or "assist" in raw_lower

        if canonical_stat == "FOULS":
            return mkt_upper == "PLAYER_FOULS" or "faul" in raw_lower or "foul" in raw_lower

        if canonical_stat == "PASSES":
            return mkt_upper == "PLAYER_PASSES" or "poda" in raw_lower or "pass" in raw_lower

        if canonical_stat == "TACKLES":
            return mkt_upper == "PLAYER_TACKLES" or "odbior" in raw_lower or "tackle" in raw_lower

        return canonical_stat in mkt_upper

    @staticmethod
    def _is_side_match(target_side: str, selection_type: str, selection_name: str = "") -> bool:
        """Checks if selection matches OVER or UNDER side."""
        s_norm = (selection_type or "").strip().upper()
        n_lower = (selection_name or "").strip().lower()

        # Reject selections that explicitly represent first/last goal or contradictory sides
        if any(k in n_lower for k in ("1. gol", "pierwsz", "ostatn")):
            return False

        if target_side == "OVER":
            return (
                s_norm in ("OVER", "POWYŻEJ", "POWYZEJ", "TAK", "YES")
                or "powyżej" in n_lower
                or "powyzej" in n_lower
                or "over" in n_lower
            )
        elif target_side == "UNDER":
            return (
                s_norm in ("UNDER", "PONIŻEJ", "PONIZEJ", "NIE", "NO")
                or "poniżej" in n_lower
                or "ponizej" in n_lower
                or "under" in n_lower
            )
        return False

    def match_execution_odds(
        self,
        player_name: str,
        team: str,
        opponent: str,
        stat_type: str,
        line: float,
        side: str = "OVER",
        reference_odds: Optional[List[Dict[str, Any]]] = None,
        cached_execution_events: Optional[List[Any]] = None,
        normalized_quotes: Optional[List[NormalizedExecutionQuote]] = None,
        event_id: Optional[str] = None,
        statshub_fixture_id: Optional[str] = None,
        statshub_event_internal_id: Optional[Union[int, str]] = None,
        statshub_player_id: Optional[Union[int, str]] = None,
    ) -> PropOddsComparison:
        """Attempt deterministic matching against Superbet and Betclic execution pipelines."""
        ref_list = reference_odds or []
        target_line = float(line)
        target_side = side.upper()
        canonical_stat = STAT_TYPE_CANONICAL_MAP.get(stat_type.lower().replace(" ", "_"), stat_type.upper())

        # Build Canonical Prop Key
        canonical_prop = CanonicalPropKey(
            player_name=player_name,
            team=team,
            opponent=opponent,
            stat_type=canonical_stat,
            line=Decimal(str(round(target_line, 2))),
            side=target_side,
            event_id=event_id or statshub_fixture_id,
        )

        # 1. Reference Odds: build complete multi-bookmaker list & find best reference odds
        best_ref_odds: Optional[float] = None
        best_ref_bookie = "N/A"
        matched_ref_list: List[Dict[str, Any]] = []
        for o in ref_list:
            o_line = float(o.get("line") or 0.0)
            o_side = str(o.get("side") or "OVER").upper()
            o_price = float(o.get("decimal_odds") or 0.0)
            if abs(o_line - target_line) < 0.01 and o_side == target_side:
                matched_ref_list.append(o)
                if o_price > 1.0 and (best_ref_odds is None or o_price > best_ref_odds):
                    best_ref_odds = o_price
                    best_ref_bookie = o.get("bookmaker", "Unknown")

        exec_odds_map: Dict[str, ExecutionBookmakerOdds] = {}
        all_quotes = normalized_quotes or self.normalized_quotes
        candidate_events = cached_execution_events or self.canonical_events

        prop_diag = {
            "player": player_name,
            "fixture": f"{team} vs {opponent}",
            "stat_type": canonical_stat,
            "line": target_line,
            "side": target_side,
            "quotes_evaluated": 0,
            "events_evaluated": 0,
        }

        # 2. Match each execution bookmaker independently
        for bookmaker in self.SUPPORTED_EXECUTION_BOOKMAKERS:
            matched_quote: Optional[ExecutionBookmakerOdds] = None
            found_market_no_odds = False
            matching_ambiguous = False
            best_confidence = 0.0

            # Diagnostic triage flags per bookmaker
            event_matched = False
            player_matched = False
            stat_matched = False
            scope_matched = False
            line_matched = False
            side_matched = False
            offered_lines: Set[float] = set()

            # Phase A: Match from normalized quotes (if available)
            if all_quotes:
                bm_quotes = [q for q in all_quotes if q.bookmaker.lower() == bookmaker.lower()]
                matching_active_quotes: List[Tuple[NormalizedExecutionQuote, float]] = []
                for q in bm_quotes:
                    prop_diag["quotes_evaluated"] += 1

                    # Fixture match
                    q_home, q_away = q.fixture.split(" vs ") if " vs " in q.fixture else (q.fixture, "")
                    f_match, f_conf = self.is_fixture_match(team, opponent, q_home, q_away)
                    if not f_match:
                        continue
                    event_matched = True

                    # Player match
                    p_match, p_conf = self.is_player_match(player_name, q.player)
                    if not p_match:
                        continue
                    player_matched = True

                    # Stat match
                    if q.stat_type.upper() != canonical_stat:
                        continue
                    stat_matched = True

                    # Period check (must be FULL_TIME)
                    if getattr(q, "period", "FULL_TIME") != "FULL_TIME":
                        continue
                    scope_matched = True

                    # Market name check against first/last/half goal or special markets
                    q_mkt_name = (q.market_name or "").lower()
                    if canonical_stat == "GOALS":
                        if any(k in q_mkt_name for k in ("1. gola", "pierwszego gola", "pierwszy gol", "ostatniego gola", "ostatni gol", "w 1. połowie", "w 1. polowie", "w 2. połowie", "w 2. polowie")):
                            continue
                        if getattr(q, "market_type", None) in ("PLAYER_FIRST_GOAL", "PLAYER_LAST_GOAL", "PLAYER_GOALS_FIRST_HALF", "PLAYER_GOALS_SECOND_HALF"):
                            continue

                    offered_lines.add(q.line)
                    # Line match
                    if abs(q.line - target_line) >= 0.01:
                        continue
                    line_matched = True

                    # Side match
                    if q.side.upper() != target_side:
                        continue
                    side_matched = True

                    comb_conf = round((f_conf * 0.4) + (p_conf * 0.6), 2)
                    if q.active and q.odds > 1.0:
                        matching_active_quotes.append((q, comb_conf))
                    else:
                        found_market_no_odds = True

                if matching_active_quotes:
                    # Deterministic deduplication: pick active quote with highest decimal odds
                    best_match_q, best_conf = max(matching_active_quotes, key=lambda x: (x[0].odds, x[1]))
                    matched_quote = ExecutionBookmakerOdds(
                        bookmaker=bookmaker,
                        status="AVAILABLE",
                        reason_code=MatchingReasonCode.MATCHED.value,
                        reason=f"Matched {bookmaker} player prop for {player_name} ({canonical_stat} {target_line} {target_side}).",
                        decimal_odds=best_match_q.odds,
                        line=target_line,
                        side=target_side,
                        selection_id=getattr(best_match_q, "selection_name", None),
                        selection_name=best_match_q.selection_name or best_match_q.player,
                        market_id=getattr(best_match_q, "market_type", None),
                        market_name=best_match_q.market_name or f"PLAYER_{canonical_stat}",
                        event_id=best_match_q.event_id,
                        event_name=best_match_q.fixture,
                        match_confidence=best_conf,
                        captured_at=best_match_q.captured_at,
                    )
                    best_confidence = max(best_confidence, best_conf)

            # Phase B: Match from candidate events / graphs (if not matched via quotes)
            if not matched_quote and candidate_events:
                matching_event_candidates = []
                for ev in candidate_events:
                    if not self._is_event_for_bookmaker(ev, bookmaker):
                        continue
                    prop_diag["events_evaluated"] += 1
                    raw_home = ev.get("home_team", "") if isinstance(ev, dict) else getattr(ev, "home_team", getattr(getattr(ev, "event", None), "home_participant", ""))
                    raw_away = ev.get("away_team", "") if isinstance(ev, dict) else getattr(ev, "away_team", getattr(getattr(ev, "event", None), "away_participant", ""))
                    ev_id = str(ev.get("id") or ev.get("canonical_event_id") or "" if isinstance(ev, dict) else getattr(ev, "canonical_event_id", getattr(getattr(ev, "event", None), "internal_id", "")))

                    is_exact_id = bool(event_id and ev_id and str(event_id) == str(ev_id))
                    f_match, f_conf = self.is_fixture_match(team, opponent, raw_home, raw_away)

                    if is_exact_id or f_match:
                        event_matched = True
                        matching_event_candidates.append((ev, raw_home, raw_away, ev_id, f_conf))

                if len(matching_event_candidates) > 1:
                    matching_ambiguous = True

                phase_b_quotes: List[Tuple[float, float, str, str, str, Optional[str], Optional[str]]] = []
                for ev, raw_home, raw_away, ev_id_str, f_conf in matching_event_candidates:
                    ev_name = f"{raw_home} vs {raw_away}"
                    markets = ev.get("markets", []) if isinstance(ev, dict) else getattr(ev, "markets", [])

                    for mkt in markets:
                        m_type = str(mkt.get("market_type", "") if isinstance(mkt, dict) else getattr(mkt, "market_type", "")).upper()
                        m_raw = str(mkt.get("name", "") if isinstance(mkt, dict) else getattr(mkt, "name", getattr(getattr(mkt, "metadata", {}), "get", lambda k, d=None: "")("raw_name", "")))
                        m_meta = mkt.get("metadata", {}) if isinstance(mkt, dict) else getattr(mkt, "metadata", {}) or {}
                        m_line = float(mkt.get("line") or 0.0) if isinstance(mkt, dict) else float(getattr(mkt, "line", 0.0) or 0.0)
                        mkt_id = str(mkt.get("id") or mkt.get("market_id") or "") if isinstance(mkt, dict) else str(getattr(mkt, "id", getattr(mkt, "market_id", "")))

                        sels = mkt.get("selections", []) if isinstance(mkt, dict) else getattr(mkt, "selections", [])
                        for s in sels:
                            s_part = str(s.get("participant", "") if isinstance(s, dict) else getattr(s, "participant", ""))
                            candidate_player = s_part or m_meta.get("player_name") or m_meta.get("player") or str(s.get("name", "") if isinstance(s, dict) else getattr(s, "name", ""))
                            p_matched, p_conf = self.is_player_match(player_name, candidate_player)
                            if p_matched:
                                player_matched = True

                        if not self._is_stat_market_match(canonical_stat, m_type, m_raw, target_period="FULL_TIME"):
                            continue
                        stat_matched = True

                        if m_meta.get("period", "FULL_TIME") != "FULL_TIME":
                            continue
                        scope_matched = True

                        for s in sels:
                            s_name = str(s.get("selection_type", "") if isinstance(s, dict) else getattr(s, "selection_type", ""))
                            s_raw_name = str(s.get("name", "") if isinstance(s, dict) else getattr(s, "name", ""))
                            s_part = str(s.get("participant", "") if isinstance(s, dict) else getattr(s, "participant", ""))
                            s_id = str(s.get("id") or s.get("selection_id") or "") if isinstance(s, dict) else str(getattr(s, "id", getattr(s, "selection_id", "")))
                            s_line_val = float(s.get("line") or 0.0) if isinstance(s, dict) else float(getattr(s, "line", 0.0) or 0.0)
                            effective_line = s_line_val if s_line_val > 0 else m_line

                            if canonical_stat == "GOALS":
                                if "2+" in (m_raw.lower() + " " + s_raw_name.lower()) or "2 lub więcej" in (m_raw.lower() + " " + s_raw_name.lower()):
                                    effective_line = 1.5
                                elif "3+" in (m_raw.lower() + " " + s_raw_name.lower()) or "3 lub więcej" in (m_raw.lower() + " " + s_raw_name.lower()):
                                    effective_line = 2.5
                                elif effective_line <= 0:
                                    effective_line = 0.5
                            elif canonical_stat in ("CARDS", "ASSISTS") and effective_line <= 0:
                                effective_line = 0.5

                            candidate_player = s_part or m_meta.get("player_name") or m_meta.get("player") or s_raw_name
                            p_matched, p_conf = self.is_player_match(player_name, candidate_player)
                            if not p_matched:
                                continue
                            player_matched = True

                            offered_lines.add(effective_line)
                            # Strict line match
                            if abs(effective_line - target_line) >= 0.01:
                                continue
                            line_matched = True

                            # Side check
                            if not self._is_side_match(target_side, s_name, s_raw_name):
                                continue
                            side_matched = True

                            # Extract odds
                            s_odds_dict = s.get("odds", {}) if isinstance(s, dict) else getattr(s, "odds", {})
                            if isinstance(s_odds_dict, dict):
                                b_price = s_odds_dict.get(bookmaker.lower()) or s_odds_dict.get(bookmaker)
                            elif hasattr(s_odds_dict, "decimal_odds"):
                                b_price = s_odds_dict.decimal_odds
                            else:
                                b_price = None

                            comb_conf = round((f_conf * 0.4) + (p_conf * 0.6), 2)
                            if b_price is not None:
                                try:
                                    b_price_val = float(b_price)
                                    if b_price_val > 1.0:
                                        phase_b_quotes.append((b_price_val, comb_conf, s_raw_name or s_name, ev_id_str, ev_name, mkt_id, s_id))
                                    else:
                                        found_market_no_odds = True
                                except (ValueError, TypeError):
                                    found_market_no_odds = True
                            else:
                                found_market_no_odds = True

                if phase_b_quotes:
                    best_b_price, best_b_conf, best_b_sel, best_b_id, best_b_name, best_mkt_id, best_s_id = max(phase_b_quotes, key=lambda x: (x[0], x[1]))
                    matched_quote = ExecutionBookmakerOdds(
                        bookmaker=bookmaker,
                        status="AVAILABLE",
                        reason_code=MatchingReasonCode.MATCHED.value,
                        reason=f"Matched {bookmaker} player prop for {player_name} ({canonical_stat} {target_line} {target_side}).",
                        decimal_odds=best_b_price,
                        line=target_line,
                        side=target_side,
                        selection_id=best_s_id or best_b_sel,
                        selection_name=best_b_sel,
                        market_id=best_mkt_id,
                        market_name=f"PLAYER_{canonical_stat}",
                        event_id=best_b_id,
                        event_name=best_b_name,
                        match_confidence=best_b_conf,
                    )
                    best_confidence = max(best_confidence, best_b_conf)

            # Construct execution record with exact reason code per bookmaker
            if matched_quote:
                exec_odds_map[bookmaker] = matched_quote
            elif matching_ambiguous:
                exec_odds_map[bookmaker] = ExecutionBookmakerOdds(
                    bookmaker=bookmaker,
                    status="UNCERTAIN",
                    reason_code=MatchingReasonCode.EVENT_AMBIGUOUS.value,
                    reason=f"Ambiguous event match in {bookmaker} data for {team} vs {opponent}.",
                    match_confidence=0.5,
                )
            elif found_market_no_odds:
                exec_odds_map[bookmaker] = ExecutionBookmakerOdds(
                    bookmaker=bookmaker,
                    status="NO_ODDS",
                    reason_code=MatchingReasonCode.ODDS_UNAVAILABLE.value,
                    reason=f"{bookmaker} market found for {player_name} ({canonical_stat} {target_line}), but odds are currently inactive/suspended.",
                    match_confidence=0.8,
                )
            elif not event_matched:
                exec_odds_map[bookmaker] = ExecutionBookmakerOdds(
                    bookmaker=bookmaker,
                    status="UNAVAILABLE",
                    reason_code=MatchingReasonCode.EVENT_UNMATCHED.value,
                    reason=f"No {bookmaker} event found matching {team} vs {opponent}.",
                    match_confidence=0.0,
                )
            elif not player_matched:
                exec_odds_map[bookmaker] = ExecutionBookmakerOdds(
                    bookmaker=bookmaker,
                    status="UNAVAILABLE",
                    reason_code=MatchingReasonCode.PLAYER_UNMATCHED.value,
                    reason=f"Player '{player_name}' not found in {bookmaker} markets for {team} vs {opponent}.",
                    match_confidence=0.0,
                )
            elif not stat_matched:
                exec_odds_map[bookmaker] = ExecutionBookmakerOdds(
                    bookmaker=bookmaker,
                    status="UNAVAILABLE",
                    reason_code=MatchingReasonCode.MARKET_UNMATCHED.value,
                    reason=f"No {canonical_stat} market found in {bookmaker} for {player_name}.",
                    match_confidence=0.0,
                )
            elif not scope_matched:
                exec_odds_map[bookmaker] = ExecutionBookmakerOdds(
                    bookmaker=bookmaker,
                    status="UNAVAILABLE",
                    reason_code=MatchingReasonCode.SCOPE_MISMATCH.value,
                    reason=f"{bookmaker} offers {canonical_stat} for {player_name}, but not for FULL_TIME scope.",
                    match_confidence=0.0,
                )
            elif not line_matched:
                lines_fmt = ", ".join(str(l) for l in sorted(list(offered_lines))) if offered_lines else "other"
                exec_odds_map[bookmaker] = ExecutionBookmakerOdds(
                    bookmaker=bookmaker,
                    status="UNAVAILABLE",
                    reason_code=MatchingReasonCode.LINE_MISMATCH.value,
                    reason=f"{bookmaker} offers {canonical_stat} for {player_name} at line(s) {lines_fmt}, but target line is {target_line}.",
                    match_confidence=0.0,
                )
            elif not side_matched:
                exec_odds_map[bookmaker] = ExecutionBookmakerOdds(
                    bookmaker=bookmaker,
                    status="UNAVAILABLE",
                    reason_code=MatchingReasonCode.SELECTION_MISMATCH.value,
                    reason=f"{bookmaker} offers line {target_line}, but side {target_side} is not available.",
                    match_confidence=0.0,
                )
            else:
                exec_odds_map[bookmaker] = ExecutionBookmakerOdds(
                    bookmaker=bookmaker,
                    status="UNAVAILABLE",
                    reason_code=MatchingReasonCode.MARKET_UNMATCHED.value,
                    reason=f"No matching {bookmaker} player-prop market found for {player_name} ({canonical_stat} {target_line}).",
                    match_confidence=0.0,
                )

        # 3. Overall Execution Status Determination & Invariant Enforcement
        avail_quotes = [q for q in exec_odds_map.values() if q.status == "AVAILABLE" and q.decimal_odds and q.decimal_odds > 1.0]

        best_exec_odds: Optional[float] = None
        best_exec_bookie: Optional[str] = None
        primary_reason = MatchingReasonCode.MARKET_UNMATCHED.value

        if avail_quotes:
            best_q = max(avail_quotes, key=lambda x: (x.decimal_odds or 0.0, x.match_confidence))
            best_exec_odds = best_q.decimal_odds
            best_exec_bookie = best_q.bookmaker
            overall_status = "BETTABLE"
            primary_reason = MatchingReasonCode.MATCHED.value
            match_confidence = max((q.match_confidence for q in avail_quotes), default=1.0)
            self.telemetry["bettable"] += 1
            self.telemetry["active_execution_odds"] += len(avail_quotes)
        elif any(q.status == "UNCERTAIN" for q in exec_odds_map.values()):
            overall_status = "MATCH_UNCERTAIN"
            primary_reason = MatchingReasonCode.EVENT_AMBIGUOUS.value
            match_confidence = 0.5
            best_exec_odds = None
            best_exec_bookie = None
            self.telemetry["match_uncertain"] += 1
        elif any(q.status == "NO_ODDS" for q in exec_odds_map.values()):
            overall_status = "NO_EXECUTION_ODDS"
            primary_reason = MatchingReasonCode.ODDS_UNAVAILABLE.value
            match_confidence = 0.8
            best_exec_odds = None
            best_exec_bookie = None
            self.telemetry["no_execution_odds"] += 1
        elif best_ref_odds and best_ref_odds > 1.0:
            overall_status = "REFERENCE_ONLY"
            # Pick first available specific reason code
            for q in exec_odds_map.values():
                if q.reason_code != MatchingReasonCode.MATCHED.value:
                    primary_reason = q.reason_code
                    break
            match_confidence = 0.0
            best_exec_odds = None
            best_exec_bookie = None
            self.telemetry["reference_only"] += 1
        else:
            overall_status = "NO_EXECUTION_MARKET"
            for q in exec_odds_map.values():
                if q.reason_code != MatchingReasonCode.MATCHED.value:
                    primary_reason = q.reason_code
                    break
            match_confidence = 0.0
            best_exec_odds = None
            best_exec_bookie = None
            self.telemetry["no_execution_market"] += 1

        self.telemetry["execution_candidates"] += 1

        # Construct Provenance
        provenance_obj = PropMatchProvenance(
            statshub_fixture_id=statshub_fixture_id or event_id,
            statshub_event_internal_id=statshub_event_internal_id,
            statshub_player_id=statshub_player_id,
            canonical_prop_key=canonical_prop.to_key_string(),
            target_stat=canonical_stat,
            target_line=target_line,
            target_side=target_side,
            matched_bookmakers=[b for b, q in exec_odds_map.items() if q.status == "AVAILABLE"],
            unmatched_bookmakers=[b for b, q in exec_odds_map.items() if q.status != "AVAILABLE"],
            reasons_by_bookmaker={b: q.reason or "" for b, q in exec_odds_map.items()},
            reason_codes_by_bookmaker={b: q.reason_code for b, q in exec_odds_map.items()},
        )

        for q in exec_odds_map.values():
            q.provenance = provenance_obj.to_dict()

        return PropOddsComparison(
            reference_best_odds=best_ref_odds,
            reference_best_bookmaker=best_ref_bookie,
            reference_odds_list=matched_ref_list,
            execution_odds=exec_odds_map,
            best_executable_odds=best_exec_odds,
            best_executable_bookmaker=best_exec_bookie,
            execution_status=overall_status,
            primary_reason_code=primary_reason,
            match_confidence=match_confidence,
            canonical_prop_key=canonical_prop.to_key_string(),
            provenance=provenance_obj.to_dict(),
            diagnostics=prop_diag,
        )

    def match_statshub_prop(
        self,
        statshub_prop: Any,
        cached_execution_events: Optional[List[Any]] = None,
        normalized_quotes: Optional[List[NormalizedExecutionQuote]] = None,
    ) -> PropOddsComparison:
        """High-level integration method taking a StatsHubPropResult directly."""
        ps = getattr(statshub_prop, "player_stat", None)
        if not ps:
            raise ValueError("Invalid StatsHubPropResult: missing player_stat")

        fix = ps.fixture
        target_line = float(ps.line) if ps.line is not None else 0.5
        target_side = (ps.odds_type or "OVER").upper()
        stat_type = ps.stat_type or "SHOTS"

        # Build full reference odds list preserving all foreign bookmaker details
        all_ref_odds = []
        for o in (ps.bookmaker_odds or []):
            all_ref_odds.append({
                "bookmaker": o.bookmaker,
                "line": float(o.line),
                "side": o.side.upper(),
                "decimal_odds": float(o.decimal_odds),
                "bookmaker_id": getattr(o, "bookmaker_id", None),
                "metadata": getattr(o, "metadata", {}),
            })

        return self.match_execution_odds(
            player_name=ps.player_name,
            team=ps.team,
            opponent=ps.opponent,
            stat_type=stat_type,
            line=target_line,
            side=target_side,
            reference_odds=all_ref_odds,
            cached_execution_events=cached_execution_events,
            normalized_quotes=normalized_quotes,
            event_id=fix.fixture_id if fix else None,
            statshub_fixture_id=fix.fixture_id if fix else None,
            statshub_event_internal_id=getattr(fix, "event_internal_id", None) if fix else None,
            statshub_player_id=getattr(ps, "player_id", None),
        )
