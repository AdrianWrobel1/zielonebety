"""
Stage 5.1: Canonical Market Identity and Taxonomy Definition

Defines the provider-independent semantic contract for betting markets:
- Canonical market taxonomy (1X2, TOTALS, BTTS, DOUBLE_CHANCE, DRAW_NO_BET, HANDICAP, etc.)
- CanonicalMarketKey dataclass representing immutable semantic market identity
- Line normalization (Decimal precision, deterministic numeric equality)
- Period, Scope, Metric, and Participant Role contracts
- Safe key extraction from normalized Market models
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import Enum
import functools
import re
from typing import Any, Dict, Optional, Tuple, Union
import unicodedata

from domain.models import Market


class CanonicalMarketType(str, Enum):
    """Authoritative Canonical Market Taxonomy for the Zielone Bety platform."""
    ONE_X_TWO = "1X2"
    TOTALS = "TOTALS"
    BTTS = "BTTS"
    DOUBLE_CHANCE = "DOUBLE_CHANCE"
    DRAW_NO_BET = "DRAW_NO_BET"
    HANDICAP = "HANDICAP"
    ASIAN_HANDICAP = "ASIAN_HANDICAP"
    CORRECT_SCORE = "CORRECT_SCORE"
    HALF_TIME_RESULT = "HALF_TIME_RESULT"
    ODD_EVEN = "ODD_EVEN"
    # Player Props
    PLAYER_GOALS = "PLAYER_GOALS"
    PLAYER_SHOTS = "PLAYER_SHOTS"
    PLAYER_SHOTS_ON_TARGET = "PLAYER_SHOTS_ON_TARGET"
    PLAYER_ASSISTS = "PLAYER_ASSISTS"
    PLAYER_CARDS = "PLAYER_CARDS"
    PLAYER_FOULS = "PLAYER_FOULS"
    PLAYER_PASSES = "PLAYER_PASSES"
    PLAYER_TACKLES = "PLAYER_TACKLES"
    UNSUPPORTED = "UNSUPPORTED"


class MarketPeriod(str, Enum):
    """Betting period / time scope."""
    FULL_TIME = "FULL_TIME"
    FIRST_HALF = "FIRST_HALF"
    SECOND_HALF = "SECOND_HALF"
    EXTRA_TIME = "EXTRA_TIME"
    PENALTIES = "PENALTIES"


class MarketScope(str, Enum):
    """Market entity scope."""
    MATCH = "MATCH"
    TEAM = "TEAM"
    PLAYER = "PLAYER"


class MarketMetric(str, Enum):
    """Statistical metric target."""
    GOALS = "GOALS"
    CORNERS = "CORNERS"
    CARDS = "CARDS"
    CARD_POINTS = "CARD_POINTS"
    SHOTS = "SHOTS"
    SHOTS_ON_TARGET = "SHOTS_ON_TARGET"
    ASSISTS = "ASSISTS"
    FOULS = "FOULS"
    PASSES = "PASSES"
    OFFSIDES = "OFFSIDES"
    TACKLES = "TACKLES"


class ParticipantRole(str, Enum):
    """Participant role relative to the event."""
    HOME = "HOME"
    AWAY = "AWAY"
    NONE = "NONE"


class MarketCompletenessStatus(str, Enum):
    """Authoritative qualification status for market evaluation completeness."""
    COMPLETE = "COMPLETE"        # All required mutually exclusive selections present and matched
    PARTIAL = "PARTIAL"          # Some required selections present, but partition incomplete
    INCOMPLETE = "INCOMPLETE"    # Zero selections matched
    UNSUPPORTED = "UNSUPPORTED"  # Market semantics do not establish a proven complete partition
    INVALID = "INVALID"          # Missing necessary market dimensions (e.g. line, scope, player)


# Explicit required canonical selection sets for supported canonical market types.
# Only markets with mathematically proven mutually exclusive and collectively exhaustive
# partitions are admitted here.
SUPPORTED_MARKET_REQUIRED_SELECTIONS: Dict[str, Tuple[str, ...]] = {
    # 1X2 Match Result: Home, Draw, Away (3-way partition)
    CanonicalMarketType.ONE_X_TWO.value: ("HOME", "DRAW", "AWAY"),
    # Both Teams To Score (BTTS): Yes, No (2-way partition)
    CanonicalMarketType.BTTS.value: ("YES", "NO"),
    # Totals (Over / Under): Over, Under (2-way partition bound to parent line)
    CanonicalMarketType.TOTALS.value: ("OVER", "UNDER"),
    # Draw No Bet (DNB): Home, Away (2-way partition for decisive match)
    CanonicalMarketType.DRAW_NO_BET.value: ("HOME", "AWAY"),
    # Half Time Result: Home, Draw, Away (3-way partition for FIRST_HALF period)
    CanonicalMarketType.HALF_TIME_RESULT.value: ("HOME", "DRAW", "AWAY"),
    # Match Goals Odd/Even: Odd, Even (2-way partition)
    CanonicalMarketType.ODD_EVEN.value: ("ODD", "EVEN"),
    # Handicap (2-way partition bound to line: Home, Away)
    CanonicalMarketType.HANDICAP.value: ("HOME", "AWAY"),
    # Asian Handicap (2-way partition bound to line: Home, Away)
    CanonicalMarketType.ASIAN_HANDICAP.value: ("HOME", "AWAY"),
    # Double Chance: 1X (Home/Draw), 12 (Home/Away), X2 (Draw/Away) (3-way partition)
    CanonicalMarketType.DOUBLE_CHANCE.value: ("1X", "12", "X2"),
    # Player Props (Over / Under partitions)
    CanonicalMarketType.PLAYER_SHOTS.value: ("OVER", "UNDER"),
    CanonicalMarketType.PLAYER_SHOTS_ON_TARGET.value: ("OVER", "UNDER"),
    CanonicalMarketType.PLAYER_FOULS.value: ("OVER", "UNDER"),
    CanonicalMarketType.PLAYER_PASSES.value: ("OVER", "UNDER"),
    CanonicalMarketType.PLAYER_TACKLES.value: ("OVER", "UNDER"),
    CanonicalMarketType.PLAYER_GOALS.value: ("YES", "NO"),
    CanonicalMarketType.PLAYER_CARDS.value: ("YES", "NO"),
    CanonicalMarketType.PLAYER_ASSISTS.value: ("YES", "NO"),
}


# Line-dependent canonical market types that strictly require a line
LINE_DEPENDENT_MARKET_TYPES = frozenset({
    CanonicalMarketType.TOTALS.value,
    CanonicalMarketType.HANDICAP.value,
    CanonicalMarketType.ASIAN_HANDICAP.value,
    CanonicalMarketType.PLAYER_SHOTS.value,
    CanonicalMarketType.PLAYER_SHOTS_ON_TARGET.value,
    CanonicalMarketType.PLAYER_FOULS.value,
    CanonicalMarketType.PLAYER_PASSES.value,
    CanonicalMarketType.PLAYER_TACKLES.value,
})

# Canonical market type alias lookup
CANONICAL_MARKET_TYPE_LOOKUP: Dict[str, str] = {
    "1X2": CanonicalMarketType.ONE_X_TWO.value,
    "MATCH_RESULT": CanonicalMarketType.ONE_X_TWO.value,
    "TOTALS": CanonicalMarketType.TOTALS.value,
    "OVER_UNDER": CanonicalMarketType.TOTALS.value,
    "TOTAL_GOALS": CanonicalMarketType.TOTALS.value,
    "BTTS": CanonicalMarketType.BTTS.value,
    "BOTH_TEAMS_TO_SCORE": CanonicalMarketType.BTTS.value,
    "DOUBLE_CHANCE": CanonicalMarketType.DOUBLE_CHANCE.value,
    "DRAW_NO_BET": CanonicalMarketType.DRAW_NO_BET.value,
    "HANDICAP": CanonicalMarketType.HANDICAP.value,
    "ASIAN_HANDICAP": CanonicalMarketType.ASIAN_HANDICAP.value,
    "CORRECT_SCORE": CanonicalMarketType.CORRECT_SCORE.value,
    "HALF_TIME_RESULT": CanonicalMarketType.HALF_TIME_RESULT.value,
    "ODD_EVEN": CanonicalMarketType.ODD_EVEN.value,
    # Player Props
    "PLAYER_GOALS": CanonicalMarketType.PLAYER_GOALS.value,
    "PLAYER_TO_SCORE": CanonicalMarketType.PLAYER_GOALS.value,
    "STRZELEC_GOLA": CanonicalMarketType.PLAYER_GOALS.value,
    "PLAYER_SHOTS": CanonicalMarketType.PLAYER_SHOTS.value,
    "PLAYER_SHOTS_ON_TARGET": CanonicalMarketType.PLAYER_SHOTS_ON_TARGET.value,
    "PLAYER_ASSISTS": CanonicalMarketType.PLAYER_ASSISTS.value,
    "PLAYER_CARDS": CanonicalMarketType.PLAYER_CARDS.value,
    "PLAYER_TO_BE_BOOKED": CanonicalMarketType.PLAYER_CARDS.value,
    "PLAYER_FOULS": CanonicalMarketType.PLAYER_FOULS.value,
    "PLAYER_PASSES": CanonicalMarketType.PLAYER_PASSES.value,
    "PLAYER_TACKLES": CanonicalMarketType.PLAYER_TACKLES.value,
}


import functools

_PLAYER_NAME_TRANS = str.maketrans({
    "ł": "l", "ø": "o", "æ": "ae", "œ": "oe", "ß": "ss", "đ": "d",
})
_RE_PLAYER_WHITESPACE = re.compile(r'\s+')


@functools.lru_cache(maxsize=16384)
def normalize_player_name(raw_name: Optional[str]) -> Optional[str]:
    """Deterministically normalizes a player name string (e.g. 'Lewandowski, Robert' -> 'robert lewandowski')."""
    if not raw_name:
        return None
    clean = str(raw_name).strip()
    if "," in clean:
        parts = [p.strip() for p in clean.split(",") if p.strip()]
        if len(parts) == 2:
            clean = f"{parts[1]} {parts[0]}"
    # Normalize whitespace
    clean = _RE_PLAYER_WHITESPACE.sub(' ', clean).strip().lower()
    # Strip all accents and diacritics via NFKD decomposition
    clean = "".join(c for c in unicodedata.normalize("NFKD", clean) if not unicodedata.combining(c))
    # Replace special Polish/Nordic letters that don't decompose cleanly
    clean = clean.translate(_PLAYER_NAME_TRANS)
    return clean or None


@functools.lru_cache(maxsize=4096)
def normalize_line(line: Optional[Union[float, int, str, Decimal]]) -> Optional[Decimal]:
    """Deterministically normalizes a market line value into a Decimal.

    Handles numeric representations like 2, 2.0, 2.50, -1.5, "-1.50".
    Guarantees that 2.5 and 2.50 yield identical normalized Decimals.
    """
    if line is None:
        return None
    try:
        d = Decimal(str(line).strip())
        if d == 0:
            return Decimal(0)
        # Normalize trailing zeros (e.g. 2.50 -> 2.5)
        normalized = d.normalize()
        # In case normalize() leaves scientific notation for whole numbers (e.g. 1E+1), format cleanly
        sign, digits, exponent = normalized.as_tuple()
        if exponent > 0:
            return Decimal(f"{normalized:f}")
        return normalized
    except (InvalidOperation, TypeError, ValueError):
        return None


@dataclass(frozen=True)
class CanonicalMarketKey:
    """Immutable, provider-independent semantic identifier for betting markets.

    Uniquely identifies a betting market across providers based on its semantic dimensions:
    - sport: Sporting code (e.g. 'football')
    - market_type: Canonical market family (e.g. 'TOTALS', '1X2', 'PLAYER_GOALS')
    - line: Normalized numeric line (e.g. Decimal('2.5'), None)
    - period: Period / time scope (e.g. 'FULL_TIME', 'FIRST_HALF')
    - scope: Market scope ('MATCH', 'TEAM', 'PLAYER')
    - metric: Statistical target ('GOALS', 'CORNERS', 'CARDS', 'SHOTS', etc.)
    - participant_role: Optional participant reference ('HOME', 'AWAY', None)
    - player_name: Optional normalized player name when scope is PLAYER
    """
    market_type: str
    line: Optional[Decimal] = None
    period: str = MarketPeriod.FULL_TIME.value
    scope: str = MarketScope.MATCH.value
    metric: str = MarketMetric.GOALS.value
    participant_role: Optional[str] = None
    sport: str = "football"
    player_name: Optional[str] = None
    canonical_player_id: Optional[str] = None

    def __post_init__(self):
        # Ensure line is normalized Decimal or None
        if self.line is not None and not isinstance(self.line, Decimal):
            object.__setattr__(self, "line", normalize_line(self.line))
        # Ensure uppercase string tokens (handling Enums if passed)
        mkt_type_val = self.market_type.value if hasattr(self.market_type, "value") else self.market_type
        object.__setattr__(self, "market_type", str(mkt_type_val).upper())
        period_val = self.period.value if hasattr(self.period, "value") else self.period
        object.__setattr__(self, "period", str(period_val).upper())
        scope_val = self.scope.value if hasattr(self.scope, "value") else self.scope
        object.__setattr__(self, "scope", str(scope_val).upper())
        metric_val = self.metric.value if hasattr(self.metric, "value") else self.metric
        object.__setattr__(self, "metric", str(metric_val).upper())
        object.__setattr__(self, "sport", str(self.sport).lower())
        if self.participant_role is not None:
            role_val = self.participant_role.value if hasattr(self.participant_role, "value") else self.participant_role
            object.__setattr__(self, "participant_role", str(role_val).upper())
        if self.player_name is not None:
            norm_player = normalize_player_name(self.player_name)
            object.__setattr__(self, "player_name", norm_player)

    def to_key_string(self) -> str:
        """Generates a deterministic string representation of the canonical market key."""
        if self.line is None:
            line_str = "none"
        else:
            line_str = f"{self.line:f}"
            if "." in line_str:
                line_str = line_str.rstrip("0").rstrip(".")

        role_str = (self.participant_role or "all").lower()
        if self.player_name:
            player_str = self.player_name.replace(" ", "_")
            return (
                f"{self.sport}:{self.market_type}:{self.metric}:{self.scope}:"
                f"{role_str}:{player_str}:{self.period}:{line_str}"
            )
        return (
            f"{self.sport}:{self.market_type}:{self.metric}:{self.scope}:"
            f"{role_str}:{self.period}:{line_str}"
        )

    def to_human_readable_label(
        self,
        home_team: Optional[str] = None,
        away_team: Optional[str] = None,
        include_scope: bool = False,
    ) -> str:
        """Formats the CanonicalMarketKey into a concise, human-readable display string."""
        return format_canonical_market_label(
            self,
            home_team=home_team,
            away_team=away_team,
            include_scope=include_scope,
        )


def format_canonical_market_human_readable(
    market_key: Union[CanonicalMarketKey, Dict[str, Any], str],
    home_team: Optional[str] = None,
    away_team: Optional[str] = None,
) -> Dict[str, Any]:
    """Authoritative platform formatter converting canonical market metadata into human-readable representations.

    Returns structured metadata including market_name, line_display, period_display, scope_display,
    label (compact standard representation e.g. 'Total Shots • 30.5'), and full_label.
    """
    m_type = "1X2"
    metric = "GOALS"
    scope = "MATCH"
    role = ""
    period = "FULL_TIME"
    line: Optional[Union[float, Decimal]] = None
    player_name: Optional[str] = None

    def _parse_key_str(raw_k: str):
        nonlocal m_type, metric, scope, role, period, line, player_name
        if not raw_k or ":" not in raw_k:
            if raw_k:
                m_type = raw_k.upper()
            return
        parts = raw_k.split(":")
        # Check if first part is sport
        sports = ("football", "basketball", "tennis", "hockey", "volleyball", "baseball", "esports")
        if parts[0].lower() in sports:
            parts = parts[1:]
        
        # Now parts has market dimensions
        # e.g. ['TOTALS', 'SHOTS', 'MATCH', 'all', 'FULL_TIME', '30.5'] (6 items)
        # e.g. ['BTTS', 'MATCH', 'all', 'FULL_TIME'] (4 items)
        # e.g. ['BTTS', 'GOALS', 'MATCH', 'all', 'FULL_TIME', 'none'] (6 items)
        # e.g. ['PLAYER_SHOTS', 'SHOTS', 'PLAYER', 'all', 'bukayo_saka', 'FULL_TIME', '0.5'] (7 items)
        if len(parts) >= 1:
            m_type = parts[0].upper()
        
        # Check if last element is line
        if len(parts) >= 2:
            last_elem = parts[-1]
            if last_elem.lower() in ("none", "no_line", ""):
                line = None
            else:
                try:
                    line = float(last_elem)
                except (ValueError, TypeError):
                    pass
        
        # Look for period from remaining parts
        known_periods = {"FULL_TIME", "FIRST_HALF", "SECOND_HALF", "REGULAR_TIME", "1ST_HALF", "2ND_HALF", "EXTRA_TIME", "PENALTIES"}
        known_scopes = {"MATCH", "TEAM", "PLAYER"}
        known_metrics = {"GOALS", "SHOTS", "SHOTS_ON_TARGET", "CORNERS", "CARDS", "FOULS", "PASSES", "OFFSIDES", "ASSISTS"}
        
        for p_elem in parts:
            p_upper = p_elem.upper()
            if p_upper in known_periods:
                period = p_upper
            elif p_upper in known_scopes:
                scope = p_upper
            elif p_upper in known_metrics and p_elem != parts[0]:
                metric = p_upper
            elif p_elem.lower() in ("home", "away"):
                role = p_elem.lower()
            elif p_elem.lower() == "all":
                role = ""

    if isinstance(market_key, CanonicalMarketKey):
        m_type = market_key.market_type
        metric = market_key.metric
        scope = market_key.scope
        role = (market_key.participant_role or "").lower()
        period = market_key.period
        line = market_key.line
        player_name = market_key.player_name
    elif isinstance(market_key, dict):
        m_type = str(market_key.get("market_type") or market_key.get("type") or "1X2").upper()
        metric = str(market_key.get("metric") or "GOALS").upper()
        scope = str(market_key.get("scope") or "MATCH").upper()
        role = str(market_key.get("participant_role") or "").lower()
        period = str(market_key.get("period") or "FULL_TIME").upper()
        line = market_key.get("line")
        player_name = market_key.get("player_name")

        key_str = market_key.get("key_string") or ""
        if key_str and ":" in key_str:
            _parse_key_str(key_str)
    elif isinstance(market_key, str):
        _parse_key_str(market_key)

    h_name = home_team or "Home"
    a_name = away_team or "Away"

    team_name = None
    role_suffix = ""
    if role == "home":
        team_name = h_name
        role_suffix = f" ({h_name})" if home_team else " (Home)"
    elif role == "away":
        team_name = a_name
        role_suffix = f" ({a_name})" if away_team else " (Away)"

    period_display = {
        "FULL_TIME": "Full Time",
        "REGULAR_TIME": "Full Time",
        "FIRST_HALF": "1st Half",
        "1ST_HALF": "1st Half",
        "SECOND_HALF": "2nd Half",
        "2ND_HALF": "2nd Half",
    }.get(period, period.replace("_", " ").title())

    if scope == "TEAM":
        scope_display = f"Team ({team_name})" if team_name else "Team"
    elif scope == "PLAYER":
        scope_display = f"Player ({player_name})" if player_name else "Player"
    else:
        scope_display = "Match"

    # Base market name resolution
    if scope == "PLAYER":
        p_name_title = player_name.title() if player_name else "Player"
        if metric == "GOALS" or m_type == "PLAYER_GOALS":
            market_name = f"Player to Score — {p_name_title}"
        elif metric == "SHOTS" or m_type == "PLAYER_SHOTS":
            market_name = f"Player Total Shots — {p_name_title}"
        elif metric == "SHOTS_ON_TARGET" or m_type == "PLAYER_SHOTS_ON_TARGET":
            market_name = f"Player Shots on Target — {p_name_title}"
        elif metric == "CARDS" or m_type == "PLAYER_CARDS":
            market_name = f"Player Cards — {p_name_title}"
        elif metric == "ASSISTS" or m_type == "PLAYER_ASSISTS":
            market_name = f"Player Assists — {p_name_title}"
        elif metric == "FOULS" or m_type == "PLAYER_FOULS":
            market_name = f"Player Fouls — {p_name_title}"
        elif metric == "PASSES" or m_type == "PLAYER_PASSES":
            market_name = f"Player Passes — {p_name_title}"
        elif metric == "TACKLES" or m_type == "PLAYER_TACKLES":
            market_name = f"Player Tackles — {p_name_title}"
        else:
            market_name = f"Player {metric.replace('_', ' ').title()} — {p_name_title}"
    elif metric == "OFFSIDES":
        market_name = f"Total Offsides — {team_name}" if (scope == "TEAM" and team_name) else (f"Team Total Offsides{role_suffix}" if scope == "TEAM" else "Total Offsides")
    elif metric == "CORNERS":
        market_name = f"Total Corners — {team_name}" if (scope == "TEAM" and team_name) else (f"Team Total Corners{role_suffix}" if scope == "TEAM" else "Total Corners")
    elif metric == "CARD_POINTS":
        market_name = f"Total Card Points — {team_name}" if (scope == "TEAM" and team_name) else (f"Team Total Card Points{role_suffix}" if scope == "TEAM" else "Total Card Points")
    elif metric == "CARDS":
        market_name = f"Total Cards — {team_name}" if (scope == "TEAM" and team_name) else (f"Team Total Cards{role_suffix}" if scope == "TEAM" else "Total Cards")
    elif metric == "FOULS":
        market_name = f"Total Fouls — {team_name}" if (scope == "TEAM" and team_name) else (f"Team Total Fouls{role_suffix}" if scope == "TEAM" else "Total Fouls")
    elif metric == "SHOTS":
        market_name = f"Total Shots — {team_name}" if (scope == "TEAM" and team_name) else (f"Team Total Shots{role_suffix}" if scope == "TEAM" else "Total Shots")
    elif metric == "SHOTS_ON_TARGET":
        market_name = f"Shots on Target — {team_name}" if (scope == "TEAM" and team_name) else (f"Team Shots on Target{role_suffix}" if scope == "TEAM" else "Shots on Target")
    elif m_type in ("TOTALS", "OVER_UNDER", "TOTAL_GOALS"):
        market_name = f"Total Goals — {team_name}" if (scope == "TEAM" and team_name) else (f"Team Total Goals{role_suffix}" if scope == "TEAM" else "Total Goals")
    elif m_type in ("1X2", "MATCH_RESULT"):
        market_name = "Match Result" if period == "FULL_TIME" else "1st Half Result"
    elif m_type in ("BTTS", "BOTH_TEAMS_TO_SCORE"):
        market_name = "Both Teams to Score"
    elif m_type in ("DOUBLE_CHANCE",):
        market_name = "Double Chance"
    elif m_type in ("DRAW_NO_BET", "DNB"):
        market_name = "Draw No Bet"
    elif m_type in ("HALF_TIME_RESULT", "HT_RESULT"):
        market_name = "1st Half Result"
    elif m_type in ("HANDICAP", "ASIAN_HANDICAP"):
        market_name = f"Handicap ({line})" if line is not None else "Handicap Spread"
    elif m_type.startswith("PLAYER_"):
        stat_sub = m_type.replace("PLAYER_", "").replace("_", " ").title()
        market_name = f"Player {stat_sub}"
    else:
        market_name = m_type.replace("_", " ").title()

    line_display = f"{float(line):g}" if line is not None else "—"

    # Build concise user-facing label: e.g. "Total Shots • 30.5", "Match Result", "Both Teams to Score"
    if line is not None:
        label = f"{market_name} • {line_display}"
    else:
        label = market_name

    # Full label with period if not Full Time
    if period not in ("FULL_TIME", "REGULAR_TIME") and "1st Half" not in market_name:
        full_label = f"{label} ({period_display})"
    else:
        full_label = label

    return {
        "market_name": market_name,
        "line_display": line_display,
        "period_display": period_display,
        "scope_display": scope_display,
        "line": float(line) if line is not None else None,
        "period": period,
        "scope": scope,
        "metric": metric,
        "label": label,
        "full_label": full_label,
    }


def format_canonical_market_label(
    market_key: Union[CanonicalMarketKey, Dict[str, Any], str],
    home_team: Optional[str] = None,
    away_team: Optional[str] = None,
    include_scope: bool = False,
) -> str:
    """Convenience formatter returning concise human-readable market label string (e.g. 'Total Shots • 30.5')."""
    res = format_canonical_market_human_readable(
        market_key=market_key,
        home_team=home_team,
        away_team=away_team,
    )
    if include_scope and res["scope"] == "MATCH" and res["period"] == "FULL_TIME":
        if res["line"] is not None:
            return f"{res['market_name']} — Match • {res['line_display']}"
    return res["label"]


def extract_canonical_market_key(
    market: Market,
    default_sport: str = "football",
) -> Optional[CanonicalMarketKey]:
    """Extracts a CanonicalMarketKey from a normalized Market model.

    Returns:
        CanonicalMarketKey if the market can be safely canonicalized.
        None if the market is unsupported or lacks mandatory semantic fields (e.g. line for Totals).
    """
    if not isinstance(market, Market):
        return None

    raw_mkt_type = market.market_type.strip().upper() if market.market_type else ""
    canonical_type = CANONICAL_MARKET_TYPE_LOOKUP.get(raw_mkt_type)
    if not canonical_type:
        return None

    meta = market.metadata or {}

    # Extract & normalize period
    period = meta.get("period", MarketPeriod.FULL_TIME.value).upper()
    if canonical_type == CanonicalMarketType.HALF_TIME_RESULT.value:
        canonical_type = CanonicalMarketType.ONE_X_TWO.value
        period = MarketPeriod.FIRST_HALF.value

    # Extract scope & participant role
    scope = meta.get("scope", MarketScope.MATCH.value).upper()
    role = meta.get("participant_role")
    if role:
        role = str(role).upper()

    player_name = meta.get("player_name") or meta.get("player")

    # Set scope & metric for player prop market families
    if canonical_type in (
        CanonicalMarketType.PLAYER_GOALS.value,
        CanonicalMarketType.PLAYER_SHOTS.value,
        CanonicalMarketType.PLAYER_SHOTS_ON_TARGET.value,
        CanonicalMarketType.PLAYER_ASSISTS.value,
        CanonicalMarketType.PLAYER_CARDS.value,
        CanonicalMarketType.PLAYER_FOULS.value,
        CanonicalMarketType.PLAYER_PASSES.value,
        CanonicalMarketType.PLAYER_TACKLES.value,
    ):
        scope = MarketScope.PLAYER.value
        if canonical_type == CanonicalMarketType.PLAYER_GOALS.value:
            metric = MarketMetric.GOALS.value
        elif canonical_type == CanonicalMarketType.PLAYER_SHOTS.value:
            metric = MarketMetric.SHOTS.value
        elif canonical_type == CanonicalMarketType.PLAYER_SHOTS_ON_TARGET.value:
            metric = MarketMetric.SHOTS_ON_TARGET.value
        elif canonical_type == CanonicalMarketType.PLAYER_ASSISTS.value:
            metric = MarketMetric.ASSISTS.value
        elif canonical_type == CanonicalMarketType.PLAYER_CARDS.value:
            metric = MarketMetric.CARDS.value
        elif canonical_type == CanonicalMarketType.PLAYER_FOULS.value:
            metric = MarketMetric.FOULS.value
        elif canonical_type == CanonicalMarketType.PLAYER_PASSES.value:
            metric = MarketMetric.PASSES.value
        elif canonical_type == CanonicalMarketType.PLAYER_TACKLES.value:
            metric = MarketMetric.TACKLES.value
        else:
            metric = MarketMetric.GOALS.value

        # For player props, player_name is mandatory
        if not player_name:
            return None
        canonical_player_id = meta.get("canonical_player_id")
    else:
        # Extract metric
        metric = meta.get("metric", MarketMetric.GOALS.value).upper()
        player_name = None
        canonical_player_id = None

    # Extract sport
    sport = meta.get("sport", default_sport).lower()

    # Line validation for line-dependent markets
    norm_line = normalize_line(market.line)
    if canonical_type in LINE_DEPENDENT_MARKET_TYPES:
        if norm_line is None:
            # Line is mandatory for line-dependent markets
            return None
    elif canonical_type != CanonicalMarketType.PLAYER_GOALS.value and canonical_type != CanonicalMarketType.PLAYER_CARDS.value and canonical_type != CanonicalMarketType.PLAYER_ASSISTS.value:
        # Non-line markets should not have a line in identity.
        # P0-NEW-002: PLAYER_GOALS/CARDS/ASSISTS preserve an explicit line
        # when present (OVER/UNDER variants) while still allowing line-less
        # YES/NO binaries — consistent with props_taxonomy is_line_dependent.
        norm_line = None

    return CanonicalMarketKey(
        market_type=canonical_type,
        line=norm_line,
        period=period,
        scope=scope,
        metric=metric,
        participant_role=role,
        sport=sport,
        player_name=player_name,
        canonical_player_id=canonical_player_id,
    )


def classify_market_category(mkt_type_or_name: Any, key_or_mkt: Any = None) -> str:
    """Authoritative platform-wide market category classifier.

    Maps any CanonicalMarketKey, Market domain entity, raw name string, or dictionary
    into one of the 24 standard market breakdown telemetry categories:
    - 1X2, BTTS, TOTALS, TEAM_GOALS
    - CARDS, TEAM_CARDS, CARD_POINTS, TEAM_CARD_POINTS
    - CORNERS, TEAM_CORNERS, OFFSIDES, TEAM_OFFSIDES
    - FOULS, TEAM_FOULS, SHOTS, TEAM_SHOTS, SHOTS_ON_TARGET, TEAM_SHOTS_ON_TARGET
    - DOUBLE_CHANCE, DRAW_NO_BET, HALF_TIME_RESULT, HANDICAP
    - PLAYER_PROPS, OTHER
    """
    # 1. Inspect CanonicalMarketKey or object with metric & scope
    if hasattr(mkt_type_or_name, "metric") and hasattr(mkt_type_or_name, "scope"):
        metric = getattr(mkt_type_or_name, "metric", "GOALS") or "GOALS"
        scope = getattr(mkt_type_or_name, "scope", "MATCH") or "MATCH"
        m_type = getattr(mkt_type_or_name, "market_type", "") or ""
        player_name = getattr(mkt_type_or_name, "player_name", None)

        if scope == "PLAYER" or player_name:
            return "PLAYER_PROPS"

        if scope == "TEAM":
            if metric == "CARD_POINTS":
                return "TEAM_CARD_POINTS"
            if metric == "CARDS":
                return "TEAM_CARDS"
            if metric == "CORNERS":
                return "TEAM_CORNERS"
            if metric == "SHOTS":
                return "TEAM_SHOTS"
            if metric == "SHOTS_ON_TARGET":
                return "TEAM_SHOTS_ON_TARGET"
            if metric == "FOULS":
                return "TEAM_FOULS"
            if metric == "OFFSIDES":
                return "TEAM_OFFSIDES"
            if metric == "GOALS":
                return "TEAM_GOALS"

        if metric == "CARD_POINTS":
            return "CARD_POINTS"
        if metric == "CARDS":
            return "CARDS"
        if metric == "CORNERS":
            return "CORNERS"
        if metric == "SHOTS":
            return "SHOTS"
        if metric == "SHOTS_ON_TARGET":
            return "SHOTS_ON_TARGET"
        if metric == "FOULS":
            return "FOULS"
        if metric == "OFFSIDES":
            return "OFFSIDES"

        if m_type == "TOTALS":
            return "TOTALS"
        if m_type in ("1X2", "MATCH_RESULT"):
            return "1X2"
        if m_type == "BTTS":
            return "BTTS"
        if m_type == "DOUBLE_CHANCE":
            return "DOUBLE_CHANCE"
        if m_type == "DRAW_NO_BET":
            return "DRAW_NO_BET"
        if m_type == "HALF_TIME_RESULT":
            return "HALF_TIME_RESULT"
        if m_type in ("HANDICAP", "ASIAN_HANDICAP"):
            return "HANDICAP"
        mkt_type_or_name = m_type

    # 2. Inspect dict or metadata on key_or_mkt
    target_obj = key_or_mkt if key_or_mkt is not None else mkt_type_or_name
    if isinstance(target_obj, dict):
        metric = target_obj.get("metric", "GOALS")
        scope = target_obj.get("scope", "MATCH")
        if scope == "PLAYER" or target_obj.get("player_name"):
            return "PLAYER_PROPS"
        if scope == "TEAM":
            team_map = {
                "CARD_POINTS": "TEAM_CARD_POINTS",
                "CARDS": "TEAM_CARDS",
                "CORNERS": "TEAM_CORNERS",
                "SHOTS": "TEAM_SHOTS",
                "SHOTS_ON_TARGET": "TEAM_SHOTS_ON_TARGET",
                "FOULS": "TEAM_FOULS",
                "OFFSIDES": "TEAM_OFFSIDES",
                "GOALS": "TEAM_GOALS",
            }
            if metric in team_map:
                return team_map[metric]
        match_map = {
            "CARD_POINTS": "CARD_POINTS",
            "CARDS": "CARDS",
            "CORNERS": "CORNERS",
            "SHOTS": "SHOTS",
            "SHOTS_ON_TARGET": "SHOTS_ON_TARGET",
            "FOULS": "FOULS",
            "OFFSIDES": "OFFSIDES",
        }
        if metric in match_map:
            return match_map[metric]

    if hasattr(target_obj, "metadata") and isinstance(target_obj.metadata, dict):
        metric = target_obj.metadata.get("metric", "GOALS")
        scope = target_obj.metadata.get("scope", "MATCH")
        if scope == "PLAYER" or target_obj.metadata.get("player_name"):
            return "PLAYER_PROPS"
        if scope == "TEAM":
            team_map = {
                "CARD_POINTS": "TEAM_CARD_POINTS",
                "CARDS": "TEAM_CARDS",
                "CORNERS": "TEAM_CORNERS",
                "SHOTS": "TEAM_SHOTS",
                "SHOTS_ON_TARGET": "TEAM_SHOTS_ON_TARGET",
                "FOULS": "TEAM_FOULS",
                "OFFSIDES": "TEAM_OFFSIDES",
                "GOALS": "TEAM_GOALS",
            }
            if metric in team_map:
                return team_map[metric]
        match_map = {
            "CARD_POINTS": "CARD_POINTS",
            "CARDS": "CARDS",
            "CORNERS": "CORNERS",
            "SHOTS": "SHOTS",
            "SHOTS_ON_TARGET": "SHOTS_ON_TARGET",
            "FOULS": "FOULS",
            "OFFSIDES": "OFFSIDES",
        }
        if metric in match_map:
            return match_map[metric]

    m = (str(mkt_type_or_name) if mkt_type_or_name else "").upper().strip()

    # 3. Player Props heuristics
    if any(k in m for k in ("PLAYER_", "STRZELEC", "ZAWODNIK", "GRACZ", "GOALSCORER", "ASYSTY ZAWODNIKA", "CELNE STRZAŁY", "CELNE STRZALY")):
        return "PLAYER_PROPS"

    # 4. Raw string name heuristics
    is_team_hint = any(k in m for k in ("GOSPODARZ", "GOŚC", "GOSC", "DRUŻYN", "DRUZYN", "TEAM"))
    if any(k in m for k in ("RZUTÓW ROŻNYCH", "RZUTOW ROZNYCH", "RZ.ROŻNYCH", "RZ.ROZNYCH", "ROŻNE", "ROZNE", "CORNER")):
        return "TEAM_CORNERS" if is_team_hint else "CORNERS"
    if any(k in m for k in ("PUNKTY KARTKOWE", "PUNKTÓW KARTKOWYCH")):
        return "TEAM_CARD_POINTS" if is_team_hint else "CARD_POINTS"
    if any(k in m for k in ("ŻÓŁTYCH KARTEK", "ZOLTYCH KARTEK", "KARTEK", "KARTK", "KARTKI", "KARTKI ŻÓŁTE", "KARTKI ZOLTE", "CARDS")):
        return "TEAM_CARDS" if is_team_hint else "CARDS"
    if any(k in m for k in ("STRZAŁY CELNE", "STRZALY CELNE", "CELNE STRZAŁY", "CELNE STRZALY", "SHOTS ON TARGET")):
        return "TEAM_SHOTS_ON_TARGET" if is_team_hint else "SHOTS_ON_TARGET"
    if any(k in m for k in ("STRZAŁY", "STRZALY", "SHOTS")):
        return "TEAM_SHOTS" if is_team_hint else "SHOTS"
    if any(k in m for k in ("FAULE", "FAULI", "FOULS")):
        return "TEAM_FOULS" if is_team_hint else "FOULS"
    if any(k in m for k in ("SPALONE", "SPALONYCH", "OFFSIDES")):
        return "TEAM_OFFSIDES" if is_team_hint else "OFFSIDES"

    if is_team_hint and any(k in m for k in ("GOAL", "GOL", "LICZBA GOLI")):
        return "TEAM_GOALS"

    # 5. Core Market Types
    if m in ("1X2", "MATCH_RESULT", "1_X_2") or "WYNIK MECZU" in m:
        return "1X2"
    if m in ("BTTS", "BOTH_TEAMS_TO_SCORE") or "OBIE DRUŻYNY STRZELĄ" in m or "OBIE DRUZYNY STRZELA" in m:
        return "BTTS"
    if m in ("TOTALS", "OVER_UNDER", "TOTAL_GOALS", "O/U") or any(k in m for k in ("LICZBA GOLI", "SUMA GOLI", "PONIŻEJ/POWYŻEJ", "PONIZEJ/POWYZEJ")):
        return "TOTALS"
    if m == "DOUBLE_CHANCE" or "PODWÓJNA SZANSA" in m or "PODWOJNA SZANSA" in m:
        return "DOUBLE_CHANCE"
    if m == "DRAW_NO_BET" or "ZAKŁAD BEZ REMISU" in m or "ZAKLAD BEZ REMISU" in m:
        return "DRAW_NO_BET"
    if m == "HALF_TIME_RESULT" or "1. POŁOWA" in m or "1. POLOWA" in m:
        return "HALF_TIME_RESULT"
    if m in ("HANDICAP", "ASIAN_HANDICAP") or "HANDICAP" in m:
        return "HANDICAP"

    return "OTHER"
