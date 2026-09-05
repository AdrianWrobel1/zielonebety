"""
Stage 5.2: Canonical Selection Identity and Taxonomy Definition

Defines the provider-independent semantic contract for betting selections:
- Canonical selection taxonomy (HOME, DRAW, AWAY, OVER, UNDER, YES, NO, HOME_DRAW, DRAW_AWAY, HOME_AWAY, SCORE, ODD, EVEN)
- CanonicalSelectionKey dataclass representing immutable semantic selection identity
- Strict parent CanonicalMarketKey binding (prevents cross-market matching)
- Participant role resolution (HOME/AWAY relative to canonical event)
- Score outcome and handicap selection line normalization
"""

import re
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, Optional, Union

from domain.models import Event, Selection
from normalization.market_identity import (
    CanonicalMarketKey,
    CanonicalMarketType,
    normalize_line,
)


class CanonicalSelectionType(str, Enum):
    """Authoritative Canonical Selection Outcomes for the Zielone Bety platform."""
    # 1X2, DNB, Handicap
    HOME = "HOME"
    DRAW = "DRAW"
    AWAY = "AWAY"

    # Totals
    OVER = "OVER"
    UNDER = "UNDER"

    # Both Teams to Score (BTTS)
    YES = "YES"
    NO = "NO"

    # Double Chance
    HOME_DRAW = "HOME_DRAW"  # 1X
    DRAW_AWAY = "DRAW_AWAY"  # X2
    HOME_AWAY = "HOME_AWAY"  # 12

    # Correct Score
    SCORE = "SCORE"

    # Odd/Even
    ODD = "ODD"
    EVEN = "EVEN"

    # Unsupported
    UNSUPPORTED = "UNSUPPORTED"


# Selection aliases mapping
SELECTION_TYPE_ALIASES: Dict[str, str] = {
    # 1X2 / DNB
    "1": CanonicalSelectionType.HOME.value,
    "HOME": CanonicalSelectionType.HOME.value,
    "GOSPODARZ": CanonicalSelectionType.HOME.value,
    "X": CanonicalSelectionType.DRAW.value,
    "DRAW": CanonicalSelectionType.DRAW.value,
    "REMIS": CanonicalSelectionType.DRAW.value,
    "2": CanonicalSelectionType.AWAY.value,
    "AWAY": CanonicalSelectionType.AWAY.value,
    "GOSC": CanonicalSelectionType.AWAY.value,
    "GOŚĆ": CanonicalSelectionType.AWAY.value,

    # Totals
    "OVER": CanonicalSelectionType.OVER.value,
    "POWYŻEJ": CanonicalSelectionType.OVER.value,
    "POWYZEJ": CanonicalSelectionType.OVER.value,
    "+": CanonicalSelectionType.OVER.value,
    "UNDER": CanonicalSelectionType.UNDER.value,
    "PONIŻEJ": CanonicalSelectionType.UNDER.value,
    "PONIZEJ": CanonicalSelectionType.UNDER.value,
    "-": CanonicalSelectionType.UNDER.value,

    # BTTS
    "YES": CanonicalSelectionType.YES.value,
    "TAK": CanonicalSelectionType.YES.value,
    "NO": CanonicalSelectionType.NO.value,
    "NIE": CanonicalSelectionType.NO.value,

    # Double Chance
    "1X": CanonicalSelectionType.HOME_DRAW.value,
    "HOME_DRAW": CanonicalSelectionType.HOME_DRAW.value,
    "1X_HOME_DRAW": CanonicalSelectionType.HOME_DRAW.value,
    "X2": CanonicalSelectionType.DRAW_AWAY.value,
    "2X": CanonicalSelectionType.DRAW_AWAY.value,
    "DRAW_AWAY": CanonicalSelectionType.DRAW_AWAY.value,
    "X2_DRAW_AWAY": CanonicalSelectionType.DRAW_AWAY.value,
    "12": CanonicalSelectionType.HOME_AWAY.value,
    "HOME_AWAY": CanonicalSelectionType.HOME_AWAY.value,
    "12_HOME_AWAY": CanonicalSelectionType.HOME_AWAY.value,

    # Odd/Even
    "ODD": CanonicalSelectionType.ODD.value,
    "NIEPARZYSTE": CanonicalSelectionType.ODD.value,
    "EVEN": CanonicalSelectionType.EVEN.value,
    "PARZYSTE": CanonicalSelectionType.EVEN.value,
}


def normalize_score_outcome(raw_score: Optional[str]) -> Optional[str]:
    """Deterministically normalizes a scoreline string into 'H-A' format (e.g. '1:0' -> '1-0')."""
    if not raw_score:
        return None
    s = raw_score.strip().replace(":", "-").replace(" ", "")
    parts = s.split("-")
    if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
        return f"{int(parts[0])}-{int(parts[1])}"
    return s.upper()


@dataclass(frozen=True)
class CanonicalSelectionKey:
    """Immutable, provider-independent semantic identifier for a betting selection outcome.

    Bound strictly to its parent CanonicalMarketKey to prevent out-of-context cross-matching:
    - market_key: The parent CanonicalMarketKey
    - selection_type: Canonical outcome token ('HOME', 'OVER', 'YES', etc.)
    - participant_role: Event participant role ('HOME', 'AWAY', None)
    - selection_line: Normalized line if selection-specific (e.g. Handicap spread)
    - score_outcome: Normalized scoreline outcome for CORRECT_SCORE (e.g. '1-0')
    """
    market_key: CanonicalMarketKey
    selection_type: str
    participant_role: Optional[str] = None
    selection_line: Optional[Decimal] = None
    score_outcome: Optional[str] = None
    canonical_participant_id: Optional[str] = None

    def __post_init__(self):
        if self.selection_line is not None and not isinstance(self.selection_line, Decimal):
            object.__setattr__(self, "selection_line", normalize_line(self.selection_line))
        sel_type_val = self.selection_type.value if hasattr(self.selection_type, "value") else self.selection_type
        object.__setattr__(self, "selection_type", str(sel_type_val).upper())
        if self.participant_role is not None:
            role_val = self.participant_role.value if hasattr(self.participant_role, "value") else self.participant_role
            object.__setattr__(self, "participant_role", str(role_val).upper())
        if self.score_outcome is not None:
            score_val = self.score_outcome.value if hasattr(self.score_outcome, "value") else self.score_outcome
            object.__setattr__(self, "score_outcome", str(score_val).upper())

    def to_key_string(self) -> str:
        """Generates a deterministic string representation of the canonical selection key."""
        mkt_str = self.market_key.to_key_string()
        role_str = (self.participant_role or "none").lower()
        if self.selection_line is None:
            line_str = "none"
        else:
            line_str = f"{self.selection_line:f}"
            if "." in line_str:
                line_str = line_str.rstrip("0").rstrip(".")
        score_str = self.score_outcome or "none"
        return f"{mkt_str}:{self.selection_type}:{role_str}:{line_str}:{score_str}"


def extract_canonical_selection_key(
    selection: Selection,
    market_key: CanonicalMarketKey,
    event: Optional[Event] = None,
) -> Optional[CanonicalSelectionKey]:
    """Extracts a CanonicalSelectionKey from a normalized Selection model under a CanonicalMarketKey.

    Args:
        selection: Domain Selection model.
        market_key: Matched parent CanonicalMarketKey.
        event: Optional domain Event model for participant name resolution.

    Returns:
        CanonicalSelectionKey if safely canonicalized, or None if unsupported / invalid.
    """
    if not isinstance(selection, Selection) or not isinstance(market_key, CanonicalMarketKey):
        return None

    raw_sel_type = selection.selection_type.strip().upper() if selection.selection_type else ""
    mkt_family = market_key.market_type

    # 1. 1X2 and DRAW_NO_BET
    if mkt_family in (CanonicalMarketType.ONE_X_TWO.value, CanonicalMarketType.DRAW_NO_BET.value):
        # Resolve Home / Draw / Away
        mapped = SELECTION_TYPE_ALIASES.get(raw_sel_type)
        if not mapped and event:
            h_norm = event.home_participant.strip().upper() if event.home_participant else ""
            a_norm = event.away_participant.strip().upper() if event.away_participant else ""
            if raw_sel_type == h_norm or selection.participant == event.home_participant:
                mapped = CanonicalSelectionType.HOME.value
            elif raw_sel_type == a_norm or selection.participant == event.away_participant:
                mapped = CanonicalSelectionType.AWAY.value

        if mapped == CanonicalSelectionType.HOME.value:
            return CanonicalSelectionKey(
                market_key=market_key,
                selection_type=CanonicalSelectionType.HOME.value,
                participant_role="HOME",
            )
        elif mapped == CanonicalSelectionType.AWAY.value:
            return CanonicalSelectionKey(
                market_key=market_key,
                selection_type=CanonicalSelectionType.AWAY.value,
                participant_role="AWAY",
            )
        elif mapped == CanonicalSelectionType.DRAW.value:
            if mkt_family == CanonicalMarketType.DRAW_NO_BET.value:
                # Draw is not a valid outcome under Draw No Bet
                return None
            return CanonicalSelectionKey(
                market_key=market_key,
                selection_type=CanonicalSelectionType.DRAW.value,
                participant_role=None,
            )
        return None

    # 2. TOTALS
    if mkt_family == CanonicalMarketType.TOTALS.value:
        mapped = SELECTION_TYPE_ALIASES.get(raw_sel_type)
        if mapped in (CanonicalSelectionType.OVER.value, CanonicalSelectionType.UNDER.value):
            return CanonicalSelectionKey(
                market_key=market_key,
                selection_type=mapped,
                participant_role=market_key.participant_role,
            )
        # Prefix check for strings like "OVER 2.5" / "PONIŻEJ 2.5"
        if raw_sel_type.startswith(("OVER", "POWYŻEJ", "POWYZEJ")):
            return CanonicalSelectionKey(
                market_key=market_key,
                selection_type=CanonicalSelectionType.OVER.value,
                participant_role=market_key.participant_role,
            )
        if raw_sel_type.startswith(("UNDER", "PONIŻEJ", "PONIZEJ")):
            return CanonicalSelectionKey(
                market_key=market_key,
                selection_type=CanonicalSelectionType.UNDER.value,
                participant_role=market_key.participant_role,
            )
        return None

    # 3. BTTS (Both Teams to Score)
    if mkt_family == CanonicalMarketType.BTTS.value:
        mapped = SELECTION_TYPE_ALIASES.get(raw_sel_type)
        if mapped in (CanonicalSelectionType.YES.value, CanonicalSelectionType.NO.value):
            return CanonicalSelectionKey(
                market_key=market_key,
                selection_type=mapped,
            )
        return None

    # 4. DOUBLE_CHANCE
    if mkt_family == CanonicalMarketType.DOUBLE_CHANCE.value:
        mapped = SELECTION_TYPE_ALIASES.get(raw_sel_type)
        if mapped in (
            CanonicalSelectionType.HOME_DRAW.value,
            CanonicalSelectionType.DRAW_AWAY.value,
            CanonicalSelectionType.HOME_AWAY.value,
        ):
            return CanonicalSelectionKey(
                market_key=market_key,
                selection_type=mapped,
            )
        return None

    # 5. HANDICAP & ASIAN_HANDICAP
    if mkt_family in (CanonicalMarketType.HANDICAP.value, CanonicalMarketType.ASIAN_HANDICAP.value):
        mapped = SELECTION_TYPE_ALIASES.get(raw_sel_type)
        if not mapped and event:
            h_norm = event.home_participant.strip().upper() if event.home_participant else ""
            a_norm = event.away_participant.strip().upper() if event.away_participant else ""
            if raw_sel_type.startswith(h_norm) or selection.participant == event.home_participant:
                mapped = CanonicalSelectionType.HOME.value
            elif raw_sel_type.startswith(a_norm) or selection.participant == event.away_participant:
                mapped = CanonicalSelectionType.AWAY.value

        if mapped in (CanonicalSelectionType.HOME.value, CanonicalSelectionType.AWAY.value):
            sel_line = normalize_line(selection.line) if selection.line is not None else market_key.line
            role = "HOME" if mapped == CanonicalSelectionType.HOME.value else "AWAY"
            return CanonicalSelectionKey(
                market_key=market_key,
                selection_type=mapped,
                participant_role=role,
                selection_line=sel_line,
            )
        return None

    # 6. CORRECT_SCORE
    if mkt_family == CanonicalMarketType.CORRECT_SCORE.value:
        score_norm = normalize_score_outcome(raw_sel_type)
        if score_norm:
            return CanonicalSelectionKey(
                market_key=market_key,
                selection_type=CanonicalSelectionType.SCORE.value,
                score_outcome=score_norm,
            )
        return None

    # 7. ODD_EVEN
    if mkt_family == CanonicalMarketType.ODD_EVEN.value:
        mapped = SELECTION_TYPE_ALIASES.get(raw_sel_type)
        if mapped in (CanonicalSelectionType.ODD.value, CanonicalSelectionType.EVEN.value):
            return CanonicalSelectionKey(
                market_key=market_key,
                selection_type=mapped,
            )
        return None

    # 8. PLAYER PROPS
    if mkt_family in (
        CanonicalMarketType.PLAYER_GOALS.value,
        CanonicalMarketType.PLAYER_SHOTS.value,
        CanonicalMarketType.PLAYER_SHOTS_ON_TARGET.value,
        CanonicalMarketType.PLAYER_ASSISTS.value,
        CanonicalMarketType.PLAYER_CARDS.value,
        CanonicalMarketType.PLAYER_FOULS.value,
        CanonicalMarketType.PLAYER_PASSES.value,
        CanonicalMarketType.PLAYER_TACKLES.value,
    ):
        mapped = SELECTION_TYPE_ALIASES.get(raw_sel_type)
        if mapped in (
            CanonicalSelectionType.OVER.value,
            CanonicalSelectionType.UNDER.value,
            CanonicalSelectionType.YES.value,
            CanonicalSelectionType.NO.value,
        ):
            return CanonicalSelectionKey(
                market_key=market_key,
                selection_type=mapped,
            )
        # Threshold player props (Over / Under)
        if any(k in raw_sel_type for k in ("OVER", "POWYŻEJ", "POWYZEJ", "+")):
            return CanonicalSelectionKey(
                market_key=market_key,
                selection_type=CanonicalSelectionType.OVER.value,
            )
        if any(k in raw_sel_type for k in ("UNDER", "PONIŻEJ", "PONIZEJ", "-")):
            return CanonicalSelectionKey(
                market_key=market_key,
                selection_type=CanonicalSelectionType.UNDER.value,
            )
        if raw_sel_type in ("TAK", "YES"):
            return CanonicalSelectionKey(
                market_key=market_key,
                selection_type=CanonicalSelectionType.YES.value,
            )
        if raw_sel_type in ("NIE", "NO"):
            return CanonicalSelectionKey(
                market_key=market_key,
                selection_type=CanonicalSelectionType.NO.value,
            )
        if mkt_family in (
            CanonicalMarketType.PLAYER_GOALS.value,
            CanonicalMarketType.PLAYER_CARDS.value,
            CanonicalMarketType.PLAYER_ASSISTS.value,
        ):
            from normalization.market_identity import normalize_player_name
            norm_sel_p = normalize_player_name(selection.participant or raw_sel_type)
            if norm_sel_p and market_key.player_name and norm_sel_p == market_key.player_name:
                return CanonicalSelectionKey(
                    market_key=market_key,
                    selection_type=CanonicalSelectionType.YES.value,
                )
        return None

    # Unsupported or unrecognized market family for selection extraction
    return None
