"""
Player Prop Execution Bookmaker Matcher (Stage 17)

Integrates StatsHub player prop intelligence with ZieloneBety's execution bookmakers
(Superbet and Betclic). Compares reference odds (Bet365, Paddy Power, etc.) against
actionable Polish execution odds, clearly flagging unavailable markets without guessing.
"""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple, Set
import logging
import re
import unicodedata

from normalization.market_identity import normalize_player_name, normalize_line
from normalization.identity import normalize_team_name
from scanner.execution_providers import NormalizedExecutionQuote

logger = logging.getLogger("scanner.prop_execution_matcher")


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
        norm_t, _ = normalize_team_name(self.team)
        norm_o, _ = normalize_team_name(self.opponent)
        norm_st = STAT_TYPE_CANONICAL_MAP.get(self.stat_type.lower().replace(" ", "_"), self.stat_type.upper())
        norm_line = normalize_line(self.line) or Decimal("0.5")

        object.__setattr__(self, "player_name", norm_p)
        object.__setattr__(self, "team", norm_t)
        object.__setattr__(self, "opponent", norm_o)
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
class ExecutionBookmakerOdds:
    """Execution bookmaker quote for a player proposition."""
    bookmaker: str  # "Superbet", "Betclic"
    status: str  # "AVAILABLE", "NO_ODDS", "UNAVAILABLE", "UNCERTAIN"
    decimal_odds: Optional[float] = None
    line: Optional[float] = None
    side: Optional[str] = None
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
            "selection_name": self.selection_name,
            "event_id": self.event_id,
            "event_name": self.event_name,
            "reason": self.reason,
            "match_confidence": self.match_confidence,
            "captured_at": self.captured_at,
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
    match_confidence: float = 0.0
    canonical_prop_key: Optional[str] = None
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
            "canonical_prop_key": self.canonical_prop_key,
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
        return re.sub(r"\s+", " ", s).strip()

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
        """Checks if fixture matches target teams deterministically."""
        norm_t_team, t_tokens = normalize_team_name(target_team)
        norm_t_opp, o_tokens = normalize_team_name(target_opp)

        norm_c_home, h_tokens = normalize_team_name(candidate_home)
        norm_c_away, a_tokens = normalize_team_name(candidate_away)

        # 1. Exact canonical normalized match
        if (norm_t_team == norm_c_home and norm_t_opp == norm_c_away) or \
           (norm_t_team == norm_c_away and norm_t_opp == norm_c_home):
            return True, 1.0

        # 2. Token subset match
        t_set = set(t_tokens)
        o_set = set(o_tokens)
        h_set = set(h_tokens)
        a_set = set(a_tokens)

        if (t_set.issubset(h_set) and o_set.issubset(a_set)) or \
           (t_set.issubset(a_set) and o_set.issubset(h_set)):
            return True, 0.95

        if (h_set.issubset(t_set) and a_set.issubset(o_set)) or \
           (h_set.issubset(o_set) and a_set.issubset(t_set)):
            return True, 0.92

        # 3. String containment
        if (norm_t_team in norm_c_home and norm_t_opp in norm_c_away) or \
           (norm_t_team in norm_c_away and norm_t_opp in norm_c_home):
            return True, 0.90

        if (norm_c_home in norm_t_team and norm_c_away in norm_t_opp) or \
           (norm_c_home in norm_t_opp and norm_c_away in norm_t_team):
            return True, 0.90

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
            event_id=event_id,
        )

        # 1. Best Reference Odds for Target Line and Side (Strict Exact Line & Side Match)
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
            rejection_reason = None

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

                    # Player match
                    p_match, p_conf = self.is_player_match(player_name, q.player)
                    if not p_match:
                        continue

                    # Stat match
                    if q.stat_type.upper() != canonical_stat:
                        continue

                    # Period check (must be FULL_TIME)
                    if getattr(q, "period", "FULL_TIME") != "FULL_TIME":
                        continue

                    # Market name check against first/last/half goal or special markets
                    q_mkt_name = (q.market_name or "").lower()
                    if canonical_stat == "GOALS":
                        if any(k in q_mkt_name for k in ("1. gola", "pierwszego gola", "pierwszy gol", "ostatniego gola", "ostatni gol", "w 1. połowie", "w 1. polowie", "w 2. połowie", "w 2. polowie")):
                            continue
                        if getattr(q, "market_type", None) in ("PLAYER_FIRST_GOAL", "PLAYER_LAST_GOAL", "PLAYER_GOALS_FIRST_HALF", "PLAYER_GOALS_SECOND_HALF"):
                            continue

                    # Line match
                    if abs(q.line - target_line) >= 0.01:
                        continue

                    # Side match
                    if q.side.upper() != target_side:
                        continue

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
                        decimal_odds=best_match_q.odds,
                        line=target_line,
                        side=target_side,
                        selection_name=best_match_q.selection_name or best_match_q.player,
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
                    prop_diag["events_evaluated"] += 1
                    raw_home = ev.get("home_team", "") if isinstance(ev, dict) else getattr(ev, "home_team", getattr(getattr(ev, "event", None), "home_participant", ""))
                    raw_away = ev.get("away_team", "") if isinstance(ev, dict) else getattr(ev, "away_team", getattr(getattr(ev, "event", None), "away_participant", ""))
                    ev_id = str(ev.get("id") or ev.get("canonical_event_id") or "" if isinstance(ev, dict) else getattr(ev, "canonical_event_id", getattr(getattr(ev, "event", None), "internal_id", "")))

                    is_exact_id = bool(event_id and ev_id and str(event_id) == str(ev_id))
                    f_match, f_conf = self.is_fixture_match(team, opponent, raw_home, raw_away)

                    if is_exact_id or f_match:
                        matching_event_candidates.append((ev, raw_home, raw_away, ev_id, f_conf))

                if len(matching_event_candidates) > 1:
                    matching_ambiguous = True

                phase_b_quotes: List[Tuple[float, float, str, str, str]] = []
                for ev, raw_home, raw_away, ev_id_str, f_conf in matching_event_candidates:
                    ev_name = f"{raw_home} vs {raw_away}"
                    markets = ev.get("markets", []) if isinstance(ev, dict) else getattr(ev, "markets", [])

                    for mkt in markets:
                        m_type = str(mkt.get("market_type", "") if isinstance(mkt, dict) else getattr(mkt, "market_type", "")).upper()
                        m_raw = str(mkt.get("name", "") if isinstance(mkt, dict) else getattr(mkt, "name", getattr(getattr(mkt, "metadata", {}), "get", lambda k, d=None: "")("raw_name", "")))
                        m_meta = mkt.get("metadata", {}) if isinstance(mkt, dict) else getattr(mkt, "metadata", {}) or {}
                        m_line = float(mkt.get("line") or 0.0) if isinstance(mkt, dict) else float(getattr(mkt, "line", 0.0) or 0.0)

                        if m_meta.get("period", "FULL_TIME") != "FULL_TIME":
                            continue

                        if not self._is_stat_market_match(canonical_stat, m_type, m_raw, target_period="FULL_TIME"):
                            continue

                        sels = mkt.get("selections", []) if isinstance(mkt, dict) else getattr(mkt, "selections", [])
                        for s in sels:
                            s_name = str(s.get("selection_type", "") if isinstance(s, dict) else getattr(s, "selection_type", ""))
                            s_raw_name = str(s.get("name", "") if isinstance(s, dict) else getattr(s, "name", ""))
                            s_part = str(s.get("participant", "") if isinstance(s, dict) else getattr(s, "participant", ""))
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

                            # Strict line match
                            if abs(effective_line - target_line) >= 0.01:
                                continue

                            # Player identity check
                            candidate_player = s_part or m_meta.get("player_name") or m_meta.get("player") or s_raw_name
                            p_matched, p_conf = self.is_player_match(player_name, candidate_player)
                            if not p_matched:
                                continue

                            # Side check
                            if not self._is_side_match(target_side, s_name, s_raw_name):
                                continue

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
                                        phase_b_quotes.append((b_price_val, comb_conf, s_raw_name or s_name, ev_id_str, ev_name))
                                    else:
                                        found_market_no_odds = True
                                except (ValueError, TypeError):
                                    found_market_no_odds = True
                            else:
                                found_market_no_odds = True

                if phase_b_quotes:
                    best_b_price, best_b_conf, best_b_sel, best_b_id, best_b_name = max(phase_b_quotes, key=lambda x: (x[0], x[1]))
                    matched_quote = ExecutionBookmakerOdds(
                        bookmaker=bookmaker,
                        status="AVAILABLE",
                        decimal_odds=best_b_price,
                        line=target_line,
                        side=target_side,
                        selection_name=best_b_sel,
                        event_id=best_b_id,
                        event_name=best_b_name,
                        match_confidence=best_b_conf,
                    )
                    best_confidence = max(best_confidence, best_b_conf)

            # Construct execution record per bookmaker
            if matched_quote:
                exec_odds_map[bookmaker] = matched_quote
            elif matching_ambiguous:
                exec_odds_map[bookmaker] = ExecutionBookmakerOdds(
                    bookmaker=bookmaker,
                    status="UNCERTAIN",
                    reason=f"Ambiguous event match in {bookmaker} data for {team} vs {opponent}.",
                    match_confidence=0.5,
                )
            elif found_market_no_odds:
                exec_odds_map[bookmaker] = ExecutionBookmakerOdds(
                    bookmaker=bookmaker,
                    status="NO_ODDS",
                    reason=f"{bookmaker} market found for {player_name} ({canonical_stat} {target_line}), but odds are currently inactive/suspended.",
                    match_confidence=0.8,
                )
            else:
                exec_odds_map[bookmaker] = ExecutionBookmakerOdds(
                    bookmaker=bookmaker,
                    status="UNAVAILABLE",
                    reason=f"No matching {bookmaker} player-prop market found for {player_name} ({canonical_stat} {target_line}).",
                    match_confidence=0.0,
                )

        # 3. Overall Execution Status Determination & Invariant Enforcement
        avail_quotes = [q for q in exec_odds_map.values() if q.status == "AVAILABLE" and q.decimal_odds and q.decimal_odds > 1.0]

        best_exec_odds: Optional[float] = None
        best_exec_bookie: Optional[str] = None

        if avail_quotes:
            best_q = max(avail_quotes, key=lambda x: (x.decimal_odds or 0.0, x.match_confidence))
            best_exec_odds = best_q.decimal_odds
            best_exec_bookie = best_q.bookmaker
            overall_status = "BETTABLE"
            match_confidence = max((q.match_confidence for q in avail_quotes), default=1.0)
            self.telemetry["bettable"] += 1
            self.telemetry["active_execution_odds"] += len(avail_quotes)
        elif any(q.status == "UNCERTAIN" for q in exec_odds_map.values()):
            overall_status = "MATCH_UNCERTAIN"
            match_confidence = 0.5
            best_exec_odds = None
            best_exec_bookie = None
            self.telemetry["match_uncertain"] += 1
        elif any(q.status == "NO_ODDS" for q in exec_odds_map.values()):
            overall_status = "NO_EXECUTION_ODDS"
            match_confidence = 0.8
            best_exec_odds = None
            best_exec_bookie = None
            self.telemetry["no_execution_odds"] += 1
        elif best_ref_odds and best_ref_odds > 1.0:
            overall_status = "REFERENCE_ONLY"
            match_confidence = 0.0
            best_exec_odds = None
            best_exec_bookie = None
            self.telemetry["reference_only"] += 1
        else:
            overall_status = "NO_EXECUTION_MARKET"
            match_confidence = 0.0
            best_exec_odds = None
            best_exec_bookie = None
            self.telemetry["no_execution_market"] += 1

        self.telemetry["execution_candidates"] += 1

        return PropOddsComparison(
            reference_best_odds=best_ref_odds,
            reference_best_bookmaker=best_ref_bookie,
            reference_odds_list=matched_ref_list,
            execution_odds=exec_odds_map,
            best_executable_odds=best_exec_odds,
            best_executable_bookmaker=best_exec_bookie,
            execution_status=overall_status,
            match_confidence=match_confidence,
            canonical_prop_key=canonical_prop.to_key_string(),
            diagnostics=prop_diag,
        )
