"""
Authoritative Canonical Props Registry and Taxonomy (Stage 50 / Global Props Repair)

Single source of truth defining supported Player Props and Team Props across:
- Scope: PLAYER, TEAM, MATCH
- Metric: SHOTS, SHOTS_ON_TARGET, GOALS, ASSISTS, FOULS, CARDS, PASSES, TACKLES, CORNERS, OFFSIDES
- End-to-end pipeline verification (StatsHub discovery, Superbet extraction, Betclic extraction,
  canonicalization, matching, evaluation, scanner exposure)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from normalization.market_identity import (
    CanonicalMarketType,
    MarketMetric,
    MarketScope,
)


class PropScope(str, Enum):
    """Entity scope for props."""
    PLAYER = "PLAYER"
    TEAM = "TEAM"
    MATCH = "MATCH"


class PropMetric(str, Enum):
    """Statistical metric target for props."""
    SHOTS = "SHOTS"
    SHOTS_ON_TARGET = "SHOTS_ON_TARGET"
    GOALS = "GOALS"
    ASSISTS = "ASSISTS"
    FOULS = "FOULS"
    CARDS = "CARDS"
    PASSES = "PASSES"
    TACKLES = "TACKLES"
    CORNERS = "CORNERS"
    OFFSIDES = "OFFSIDES"


@dataclass(frozen=True)
class CanonicalPropDefinition:
    """Authoritative semantic definition of a canonical betting proposition."""
    scope: PropScope
    metric: PropMetric
    stat_code: str  # URL/API identifier e.g. 'shots', 'shots_on_target', 'team_corners'
    statshub_stat_type: str  # StatsHub parameter e.g. 'shots', 'shots_on_target'
    canonical_market_type: str  # CanonicalMarketType value or 'TOTALS'
    display_name: str
    is_line_dependent: bool = True
    default_line: float = 0.5
    supported_sides: Tuple[str, ...] = ("OVER", "UNDER")
    superbet_supported: bool = True
    betclic_supported: bool = True
    statshub_hunter_supported: bool = False
    statshub_trends_supported: bool = True

    @property
    def canonical_id(self) -> str:
        return f"{self.scope.value}_{self.metric.value}"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "canonical_id": self.canonical_id,
            "scope": self.scope.value,
            "metric": self.metric.value,
            "stat_code": self.stat_code,
            "statshub_stat_type": self.statshub_stat_type,
            "canonical_market_type": self.canonical_market_type,
            "display_name": self.display_name,
            "is_line_dependent": self.is_line_dependent,
            "default_line": self.default_line,
            "supported_sides": list(self.supported_sides),
            "superbet_supported": self.superbet_supported,
            "betclic_supported": self.betclic_supported,
            "statshub_hunter_supported": self.statshub_hunter_supported,
            "statshub_trends_supported": self.statshub_trends_supported,
        }


# Authoritative definitions for the 8 target Player Props
PLAYER_PROPS_DEFINITIONS: Tuple[CanonicalPropDefinition, ...] = (
    CanonicalPropDefinition(
        scope=PropScope.PLAYER,
        metric=PropMetric.SHOTS,
        stat_code="shots",
        statshub_stat_type="shots",
        canonical_market_type=CanonicalMarketType.PLAYER_SHOTS.value,
        display_name="Player Shots",
        is_line_dependent=True,
        default_line=1.5,
        supported_sides=("OVER", "UNDER"),
        superbet_supported=True,
        betclic_supported=True,
        statshub_hunter_supported=True,
        statshub_trends_supported=True,
    ),
    CanonicalPropDefinition(
        scope=PropScope.PLAYER,
        metric=PropMetric.SHOTS_ON_TARGET,
        stat_code="shots_on_target",
        statshub_stat_type="shots_on_target",
        canonical_market_type=CanonicalMarketType.PLAYER_SHOTS_ON_TARGET.value,
        display_name="Player Shots on Target",
        is_line_dependent=True,
        default_line=0.5,
        supported_sides=("OVER", "UNDER"),
        superbet_supported=True,
        betclic_supported=True,
        statshub_hunter_supported=False,  # Hunter returns 400 for shotsOnTarget
        statshub_trends_supported=True,
    ),
    CanonicalPropDefinition(
        scope=PropScope.PLAYER,
        metric=PropMetric.GOALS,
        stat_code="goals",
        statshub_stat_type="goals",
        canonical_market_type=CanonicalMarketType.PLAYER_GOALS.value,
        display_name="Player Goals",
        is_line_dependent=True,
        default_line=0.5,
        supported_sides=("OVER", "UNDER", "YES", "NO"),
        superbet_supported=True,
        betclic_supported=True,
        statshub_hunter_supported=True,
        statshub_trends_supported=True,
    ),
    CanonicalPropDefinition(
        scope=PropScope.PLAYER,
        metric=PropMetric.ASSISTS,
        stat_code="assists",
        statshub_stat_type="assists",
        canonical_market_type=CanonicalMarketType.PLAYER_ASSISTS.value,
        display_name="Player Assists",
        is_line_dependent=True,
        default_line=0.5,
        supported_sides=("OVER", "UNDER", "YES", "NO"),
        superbet_supported=True,
        betclic_supported=True,
        statshub_hunter_supported=False,
        statshub_trends_supported=True,
    ),
    CanonicalPropDefinition(
        scope=PropScope.PLAYER,
        metric=PropMetric.FOULS,
        stat_code="fouls",
        statshub_stat_type="fouls",
        canonical_market_type=CanonicalMarketType.PLAYER_FOULS.value,
        display_name="Player Fouls",
        is_line_dependent=True,
        default_line=1.5,
        supported_sides=("OVER", "UNDER"),
        superbet_supported=True,
        betclic_supported=True,
        statshub_hunter_supported=True,
        statshub_trends_supported=True,
    ),
    CanonicalPropDefinition(
        scope=PropScope.PLAYER,
        metric=PropMetric.CARDS,
        stat_code="cards",
        statshub_stat_type="cards",
        canonical_market_type=CanonicalMarketType.PLAYER_CARDS.value,
        display_name="Player Cards",
        is_line_dependent=True,
        default_line=0.5,
        supported_sides=("OVER", "UNDER", "YES", "NO"),
        superbet_supported=True,
        betclic_supported=True,
        statshub_hunter_supported=False,
        statshub_trends_supported=True,
    ),
    CanonicalPropDefinition(
        scope=PropScope.PLAYER,
        metric=PropMetric.PASSES,
        stat_code="passes",
        statshub_stat_type="passes",
        canonical_market_type=CanonicalMarketType.PLAYER_PASSES.value,
        display_name="Player Passes",
        is_line_dependent=True,
        default_line=35.5,
        supported_sides=("OVER", "UNDER"),
        superbet_supported=True,
        betclic_supported=True,
        statshub_hunter_supported=False,
        statshub_trends_supported=True,
    ),
    CanonicalPropDefinition(
        scope=PropScope.PLAYER,
        metric=PropMetric.TACKLES,
        stat_code="tackles",
        statshub_stat_type="tackles",
        canonical_market_type=CanonicalMarketType.PLAYER_TACKLES.value,
        display_name="Player Tackles",
        is_line_dependent=True,
        default_line=1.5,
        supported_sides=("OVER", "UNDER"),
        superbet_supported=True,
        betclic_supported=True,
        statshub_hunter_supported=False,
        statshub_trends_supported=True,
    ),
)


# Authoritative definitions for the 7 target Team Props
TEAM_PROPS_DEFINITIONS: Tuple[CanonicalPropDefinition, ...] = (
    CanonicalPropDefinition(
        scope=PropScope.TEAM,
        metric=PropMetric.SHOTS,
        stat_code="team_shots",
        statshub_stat_type="shots",
        canonical_market_type=CanonicalMarketType.TOTALS.value,
        display_name="Team Shots",
        is_line_dependent=True,
        default_line=10.5,
        supported_sides=("OVER", "UNDER"),
        superbet_supported=True,
        betclic_supported=True,
        statshub_hunter_supported=False,
        statshub_trends_supported=True,
    ),
    CanonicalPropDefinition(
        scope=PropScope.TEAM,
        metric=PropMetric.SHOTS_ON_TARGET,
        stat_code="team_shots_on_target",
        statshub_stat_type="shots_on_target",
        canonical_market_type=CanonicalMarketType.TOTALS.value,
        display_name="Team Shots on Target",
        is_line_dependent=True,
        default_line=3.5,
        supported_sides=("OVER", "UNDER"),
        superbet_supported=True,
        betclic_supported=True,
        statshub_hunter_supported=False,
        statshub_trends_supported=True,
    ),
    CanonicalPropDefinition(
        scope=PropScope.TEAM,
        metric=PropMetric.GOALS,
        stat_code="team_goals",
        statshub_stat_type="goals",
        canonical_market_type=CanonicalMarketType.TOTALS.value,
        display_name="Team Goals",
        is_line_dependent=True,
        default_line=1.5,
        supported_sides=("OVER", "UNDER"),
        superbet_supported=True,
        betclic_supported=True,
        statshub_hunter_supported=False,
        statshub_trends_supported=True,
    ),
    CanonicalPropDefinition(
        scope=PropScope.TEAM,
        metric=PropMetric.FOULS,
        stat_code="team_fouls",
        statshub_stat_type="fouls",
        canonical_market_type=CanonicalMarketType.TOTALS.value,
        display_name="Team Fouls",
        is_line_dependent=True,
        default_line=11.5,
        supported_sides=("OVER", "UNDER"),
        superbet_supported=True,
        betclic_supported=True,
        statshub_hunter_supported=False,
        statshub_trends_supported=True,
    ),
    CanonicalPropDefinition(
        scope=PropScope.TEAM,
        metric=PropMetric.CARDS,
        stat_code="team_cards",
        statshub_stat_type="cards",
        canonical_market_type=CanonicalMarketType.TOTALS.value,
        display_name="Team Cards",
        is_line_dependent=True,
        default_line=2.5,
        supported_sides=("OVER", "UNDER"),
        superbet_supported=True,
        betclic_supported=True,
        statshub_hunter_supported=False,
        statshub_trends_supported=True,
    ),
    CanonicalPropDefinition(
        scope=PropScope.TEAM,
        metric=PropMetric.CORNERS,
        stat_code="team_corners",
        statshub_stat_type="corners",
        canonical_market_type=CanonicalMarketType.TOTALS.value,
        display_name="Team Corners",
        is_line_dependent=True,
        default_line=4.5,
        supported_sides=("OVER", "UNDER"),
        superbet_supported=True,
        betclic_supported=True,
        statshub_hunter_supported=False,
        statshub_trends_supported=True,
    ),
    CanonicalPropDefinition(
        scope=PropScope.TEAM,
        metric=PropMetric.OFFSIDES,
        stat_code="team_offsides",
        statshub_stat_type="offsides",
        canonical_market_type=CanonicalMarketType.TOTALS.value,
        display_name="Team Offsides",
        is_line_dependent=True,
        default_line=1.5,
        supported_sides=("OVER", "UNDER"),
        superbet_supported=True,
        betclic_supported=True,
        statshub_hunter_supported=False,
        statshub_trends_supported=True,
    ),
)


# Authoritative Global Registry indexed by (Scope, Metric)
CANONICAL_PROPS_REGISTRY: Dict[Tuple[str, str], CanonicalPropDefinition] = {
    (d.scope.value, d.metric.value): d for d in PLAYER_PROPS_DEFINITIONS + TEAM_PROPS_DEFINITIONS
}

# Fast lookup by stat_code / alias
_PROP_ALIAS_MAP: Dict[str, CanonicalPropDefinition] = {}
for d in PLAYER_PROPS_DEFINITIONS + TEAM_PROPS_DEFINITIONS:
    _PROP_ALIAS_MAP[d.stat_code.lower()] = d
    _PROP_ALIAS_MAP[d.canonical_id.lower()] = d
    if d.scope == PropScope.PLAYER:
        _PROP_ALIAS_MAP[f"player_{d.stat_code.lower()}"] = d
        _PROP_ALIAS_MAP[f"player_{d.metric.value.lower()}"] = d
    elif d.scope == PropScope.TEAM:
        _PROP_ALIAS_MAP[f"team_{d.metric.value.lower()}"] = d


def get_canonical_prop_definition(
    scope: Union[PropScope, str],
    metric: Union[PropMetric, str],
) -> Optional[CanonicalPropDefinition]:
    """Retrieve CanonicalPropDefinition by scope and metric."""
    s_val = (scope.value if hasattr(scope, "value") else str(scope)).strip().upper()
    m_val = (metric.value if hasattr(metric, "value") else str(metric)).strip().upper()
    return CANONICAL_PROPS_REGISTRY.get((s_val, m_val))


def resolve_prop_stat(
    stat_str: str,
    scope: Optional[Union[PropScope, str]] = None,
) -> Optional[CanonicalPropDefinition]:
    """Resolves arbitrary stat string (e.g. 'shots', 'team_corners', 'sot') to CanonicalPropDefinition."""
    if not stat_str:
        return None
    cleaned = stat_str.strip().lower()

    # Direct alias lookup
    if cleaned in _PROP_ALIAS_MAP:
        cand = _PROP_ALIAS_MAP[cleaned]
        if scope is None:
            return cand
        s_val = (scope.value if hasattr(scope, "value") else str(scope)).strip().upper()
        if cand.scope.value == s_val:
            return cand

    # Specific common shortcuts
    shortcuts = {
        "sot": "shots_on_target",
        "shotsontarget": "shots_on_target",
        "corners": "team_corners",
        "offsides": "team_offsides",
    }
    if cleaned in shortcuts and shortcuts[cleaned] in _PROP_ALIAS_MAP:
        cand = _PROP_ALIAS_MAP[shortcuts[cleaned]]
        if scope is None:
            return cand
        s_val = (scope.value if hasattr(scope, "value") else str(scope)).strip().upper()
        if cand.scope.value == s_val:
            return cand

    # Search with scope disambiguation
    if scope is not None:
        s_val = (scope.value if hasattr(scope, "value") else str(scope)).strip().upper()
        for d in CANONICAL_PROPS_REGISTRY.values():
            if d.scope.value == s_val:
                if d.metric.value.lower() == cleaned or d.stat_code.lower() == cleaned or d.statshub_stat_type.lower() == cleaned:
                    return d

    # Fallback search
    for d in CANONICAL_PROPS_REGISTRY.values():
        if d.metric.value.lower() == cleaned or d.stat_code.lower() == cleaned or d.statshub_stat_type.lower() == cleaned:
            return d

    return None


def get_player_props() -> List[CanonicalPropDefinition]:
    """Returns all supported Player Prop definitions."""
    return list(PLAYER_PROPS_DEFINITIONS)


def get_team_props() -> List[CanonicalPropDefinition]:
    """Returns all supported Team Prop definitions."""
    return list(TEAM_PROPS_DEFINITIONS)


def get_all_canonical_props() -> List[CanonicalPropDefinition]:
    """Returns all canonical prop definitions across all scopes."""
    return list(PLAYER_PROPS_DEFINITIONS + TEAM_PROPS_DEFINITIONS)


def get_props_coverage_matrix() -> Dict[str, Any]:
    """Machine-readable coverage matrix tracking all stages of the Props pipeline.

    Stages:
    1. discovery: StatsHub acquisition supported (hunter or trends)
    2. raw_parsing: Parsed by StatsHub parser / team parser
    3. normalization: Platform allowlist and bookmaker normalizer support
    4. canonicalization: CanonicalPropKey / CanonicalTeamPropKey representation
    5. superbet_extraction: NormalizedExecutionQuote extraction supported
    6. betclic_extraction: NormalizedExecutionQuote extraction supported
    7. matching: Exact semantic matching without cross-scope pollution
    8. evaluation: Margin removal, tax, EV calculation
    9. global_scanner: Discovered & evaluated in GlobalPropsScanner
    10. ui_exposed: Exposed in UI dropdown under correct scope
    """
    matrix: Dict[str, Dict[str, Any]] = {}
    for d in PLAYER_PROPS_DEFINITIONS + TEAM_PROPS_DEFINITIONS:
        matrix[d.canonical_id] = {
            "canonical_id": d.canonical_id,
            "scope": d.scope.value,
            "metric": d.metric.value,
            "display_name": d.display_name,
            "stat_code": d.stat_code,
            "pipeline_stages": {
                "discovery": True,
                "raw_parsing": True,
                "normalization": True,
                "canonicalization": True,
                "superbet_extraction": d.superbet_supported,
                "betclic_extraction": d.betclic_supported,
                "matching": True,
                "evaluation": True,
                "global_scanner": True,
                "ui_exposed": True,
            },
            "status": "SUPPORTED" if (d.superbet_supported and d.betclic_supported) else "PARTIAL",
        }
    return {
        "total_categories": len(matrix),
        "player_categories_count": len(PLAYER_PROPS_DEFINITIONS),
        "team_categories_count": len(TEAM_PROPS_DEFINITIONS),
        "matrix": matrix,
    }


def get_ui_props_taxonomy() -> Dict[str, Any]:
    """Authoritative API/UI taxonomy structure with optgroups for the frontend."""
    return {
        "groups": [
            {
                "scope": "PLAYER",
                "label": "Player Props",
                "options": [
                    {
                        "value": d.stat_code,
                        "label": d.display_name.replace("Player ", ""),
                        "metric": d.metric.value,
                        "scope": d.scope.value,
                        "canonical_id": d.canonical_id,
                    }
                    for d in PLAYER_PROPS_DEFINITIONS
                ],
            },
            {
                "scope": "TEAM",
                "label": "Team Props",
                "options": [
                    {
                        "value": d.stat_code,
                        "label": d.display_name.replace("Team ", ""),
                        "metric": d.metric.value,
                        "scope": d.scope.value,
                        "canonical_id": d.canonical_id,
                    }
                    for d in TEAM_PROPS_DEFINITIONS
                ],
            },
        ],
        "all_options": [
            {"value": "", "label": "All Stat Types", "scope": "ALL"}
        ]
        + [
            {"value": d.stat_code, "label": f"{d.display_name}", "scope": d.scope.value}
            for d in PLAYER_PROPS_DEFINITIONS + TEAM_PROPS_DEFINITIONS
        ],
    }
