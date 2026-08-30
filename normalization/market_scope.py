"""
Stage 50: Centralized Market Allowlist and Taxonomy Scope

Defines the platform-wide market allowlist and categorization engine.
Scanner accepts exclusively:
- 1X2 / Match Result
- Double Chance
- BTTS
- BTTS + O/U 2.5
- Over/Under
- Essential Handicaps for matching (HANDICAP, ASIAN_HANDICAP, DRAW_NO_BET)
- Goals / Team Goals
- Player Goalscorer
- Corners / Team Corners
- Shots / Team Shots
- Shots on Target / Team SOT
- Cards / Team Cards
- Fouls / Team Fouls
- Offsides / Team Offsides
- Tackles / Player Tackles

All other market families are ignored / discarded early.
"""

from typing import Optional, Set


# Canonical Market Types strictly allowed in the pipeline
ALLOWED_CANONICAL_TYPES: Set[str] = {
    "1X2",
    "DOUBLE_CHANCE",
    "BTTS",
    "TOTALS",
    "OVER_UNDER",
    "TOTAL_GOALS",
    "DRAW_NO_BET",
    "HANDICAP",
    "ASIAN_HANDICAP",
    "HALF_TIME_RESULT",
    # Player Props
    "PLAYER_GOALS",
    "PLAYER_FIRST_GOAL",
    "PLAYER_LAST_GOAL",
    "PLAYER_GOALS_FIRST_HALF",
    "PLAYER_GOALS_SECOND_HALF",
    "PLAYER_SHOTS",
    "PLAYER_SHOTS_ON_TARGET",
    "PLAYER_CARDS",
    "PLAYER_RED_CARDS",
    "PLAYER_FOULS",
    "PLAYER_TACKLES",
    "PLAYER_ASSISTS",
    # Combo BTTS + O/U 2.5
    "COMBO_BTTS_TOTALS",
}

# Allowed statistical metrics across all scopes (MATCH, TEAM, PLAYER)
ALLOWED_METRICS: Set[str] = {
    "GOALS",
    "CORNERS",
    "CARDS",
    "CARD_POINTS",
    "SHOTS",
    "SHOTS_ON_TARGET",
    "FOULS",
    "OFFSIDES",
    "TACKLES",
    "ASSISTS",
}

# Disallowed metrics / player props that should be filtered out
DISALLOWED_METRICS: Set[str] = {
    "PASSES",
    "SAVES",
    "WOODWORK",
}

# Disallowed canonical types
DISALLOWED_CANONICAL_TYPES: Set[str] = {
    "CORRECT_SCORE",
    "ODD_EVEN",
    "HALF_TIME_FULL_TIME",
    "PENALTY_SPECIAL",
    "FREE_KICK_BTTS",
    "BOTH_HALVES_BTTS",
    "BTTS_2_PLUS",
    "COMBO_1X2_BTTS",
    "COMBO_1X2_TOTALS",
    "COMBO_DC_BTTS",
    "COMBO_DC_TOTALS",
    "DOUBLE_CHANCE_COMBO",
    "PLAYER_PASSES",
    "UNSUPPORTED",
}


def is_allowed_market_family(
    canonical_type: Optional[str],
    metric: Optional[str] = "GOALS",
    scope: Optional[str] = "MATCH",
    raw_name: Optional[str] = None,
) -> bool:
    """
    Evaluates whether a market belongs to the central platform allowlist.
    Returns True if allowed, False if discarded.
    """
    c_type = (canonical_type or "").strip().upper()
    met = (metric or "GOALS").strip().upper()

    # 1. Reject explicit disallowed metrics or types
    if met in DISALLOWED_METRICS:
        return False
    if c_type in DISALLOWED_CANONICAL_TYPES:
        return False

    # 2. Check if canonical type is directly allowed
    if c_type in ALLOWED_CANONICAL_TYPES:
        # Check metric compatibility
        if met in ALLOWED_METRICS:
            return True
        return False

    return False


def get_market_family_name(
    canonical_type: Optional[str],
    metric: Optional[str] = "GOALS",
    scope: Optional[str] = "MATCH",
    raw_name: Optional[str] = None,
) -> str:
    """
    Returns a normalized, human-readable family name for telemetry and diagnostic tallying.
    """
    c_type = (canonical_type or "").strip().upper()
    met = (metric or "GOALS").strip().upper()
    sc = (scope or "MATCH").strip().upper()

    if sc == "PLAYER":
        if c_type in ("PLAYER_GOALS", "PLAYER_FIRST_GOAL", "PLAYER_LAST_GOAL", "PLAYER_GOALS_FIRST_HALF", "PLAYER_GOALS_SECOND_HALF"):
            return "Player Goalscorer"
        if c_type == "PLAYER_SHOTS" or met == "SHOTS":
            return "Player Shots"
        if c_type == "PLAYER_SHOTS_ON_TARGET" or met == "SHOTS_ON_TARGET":
            return "Player Shots on Target"
        if c_type in ("PLAYER_CARDS", "PLAYER_RED_CARDS") or met in ("CARDS", "CARD_POINTS"):
            return "Player Cards"
        if c_type == "PLAYER_FOULS" or met == "FOULS":
            return "Player Fouls"
        if c_type == "PLAYER_TACKLES" or met == "TACKLES":
            return "Player Tackles"
        if c_type == "PLAYER_PASSES" or met == "PASSES":
            return "Player Passes"
        if c_type == "PLAYER_ASSISTS" or met == "ASSISTS":
            return "Player Assists"
        return f"Player {c_type}"

    if sc == "TEAM":
        if met == "GOALS":
            return "Team Goals"
        if met == "CORNERS":
            return "Team Corners"
        if met in ("CARDS", "CARD_POINTS"):
            return "Team Cards"
        if met == "SHOTS":
            return "Team Shots"
        if met == "SHOTS_ON_TARGET":
            return "Team Shots on Target"
        if met == "FOULS":
            return "Team Fouls"
        if met == "OFFSIDES":
            return "Team Offsides"
        if met == "TACKLES":
            return "Team Tackles"
        return f"Team {met.replace('_', ' ').title()}"

    # MATCH scope
    if met == "CORNERS":
        return "Corners"
    if met in ("CARDS", "CARD_POINTS"):
        return "Cards"
    if met == "SHOTS":
        return "Shots"
    if met == "SHOTS_ON_TARGET":
        return "Shots on Target"
    if met == "FOULS":
        return "Fouls"
    if met == "OFFSIDES":
        return "Offsides"
    if met == "TACKLES":
        return "Tackles"

    if c_type in ("1X2", "MATCH_RESULT"):
        return "1X2 / Match Result"
    if c_type == "HALF_TIME_RESULT":
        return "Half Time Result"
    if c_type == "DOUBLE_CHANCE":
        return "Double Chance"
    if c_type == "BTTS":
        return "BTTS"
    if c_type in ("TOTALS", "OVER_UNDER", "TOTAL_GOALS"):
        return "Over/Under"
    if c_type in ("HANDICAP", "ASIAN_HANDICAP"):
        return "Handicap"
    if c_type == "DRAW_NO_BET":
        return "Draw No Bet"
    if c_type == "COMBO_BTTS_TOTALS":
        return "BTTS + O/U 2.5"

    if raw_name:
        clean_raw = raw_name.strip()
        if len(clean_raw) > 40:
            clean_raw = clean_raw[:37] + "..."
        return clean_raw

    return c_type or "Unknown Family"

