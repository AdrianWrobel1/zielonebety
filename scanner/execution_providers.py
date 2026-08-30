"""
Execution Market Provider Adapters (Stage 17)

Provides clean, normalized adapter interfaces for Polish execution bookmakers
(Superbet and Betclic) to extract and evaluate player prop markets without
coupling the decision layer or matcher to scraper-specific schemas.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Set, Tuple
import logging
import re

from domain.models import Event, Market, Selection, Odds
from normalization.base_normalizer import NormalizedGraph
from normalization.identity import normalize_team_name
from normalization.market_identity import normalize_player_name, normalize_line

logger = logging.getLogger("scanner.execution_providers")


@dataclass
class NormalizedExecutionQuote:
    """Canonical representation of an execution bookmaker price for a player prop or team prop."""
    bookmaker: str  # "Superbet", "Betclic"
    player: str = ""  # Normalized player name (empty for team props)
    team: Optional[str] = None  # Normalized team name for team props
    participant_role: Optional[str] = None  # "HOME", "AWAY"
    fixture: str = ""  # e.g. "Real Madrid vs Barcelona"
    stat_type: str = "SHOTS"  # "SHOTS", "SHOTS_ON_TARGET", "FOULS", "CARDS", "GOALS", "ASSISTS", "PASSES", "TACKLES", "CORNERS", "OFFSIDES"
    line: float = 0.5  # e.g. 0.5, 1.5, 2.5
    side: str = "OVER"  # "OVER", "UNDER", "YES"
    odds: float = 0.0  # Decimal odds e.g. 1.70
    active: bool = True
    captured_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    event_id: Optional[str] = None
    market_name: Optional[str] = None
    selection_name: Optional[str] = None
    market_type: Optional[str] = None  # e.g. "PLAYER_GOALS", "PLAYER_SHOTS", "TOTALS"
    period: str = "FULL_TIME"  # "FULL_TIME", "FIRST_HALF", "SECOND_HALF"
    scope: str = "PLAYER"  # "PLAYER", "TEAM", "MATCH"
    raw_metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "bookmaker": self.bookmaker,
            "player": self.player,
            "team": self.team,
            "participant_role": self.participant_role,
            "fixture": self.fixture,
            "stat_type": self.stat_type,
            "line": self.line,
            "side": self.side,
            "odds": self.odds,
            "active": self.active,
            "captured_at": self.captured_at,
            "event_id": self.event_id,
            "market_name": self.market_name,
            "selection_name": self.selection_name,
            "market_type": self.market_type,
            "period": self.period,
            "scope": self.scope,
        }


class ExecutionMarketProvider(ABC):
    """Abstract interface for bookmaker execution market adapters."""

    @property
    @abstractmethod
    def bookmaker_name(self) -> str:
        """Return bookmaker display name e.g. 'Superbet', 'Betclic'."""
        pass

    @abstractmethod
    def get_player_prop_markets(
        self,
        fixture_name: str,
        kickoff: Optional[str] = None,
        player_name: Optional[str] = None,
        stat_type: Optional[str] = None,
        normalized_events: Optional[List[NormalizedGraph]] = None,
    ) -> List[NormalizedExecutionQuote]:
        """Extracts normalized execution quotes for the target fixture, player, and stat."""
        pass

    @abstractmethod
    def get_team_prop_markets(
        self,
        fixture_name: Optional[str] = None,
        kickoff: Optional[str] = None,
        team_name: Optional[str] = None,
        stat_type: Optional[str] = None,
        normalized_events: Optional[List[NormalizedGraph]] = None,
    ) -> List[NormalizedExecutionQuote]:
        """Extracts normalized execution quotes for team proposition markets."""
        pass


class SuperbetExecutionProvider(ExecutionMarketProvider):
    """Superbet execution market adapter extracting normalized player props."""

    @property
    def bookmaker_name(self) -> str:
        return "Superbet"

    def get_player_prop_markets(
        self,
        fixture_name: str,
        kickoff: Optional[str] = None,
        player_name: Optional[str] = None,
        stat_type: Optional[str] = None,
        normalized_events: Optional[List[NormalizedGraph]] = None,
    ) -> List[NormalizedExecutionQuote]:
        """Extracts quotes from normalized Superbet graph instances."""
        quotes: List[NormalizedExecutionQuote] = []
        if not normalized_events:
            return quotes

        norm_fix, _ = normalize_team_name(fixture_name)
        target_player_norm = normalize_player_name(player_name) if player_name else None

        for graph in normalized_events:
            ev = graph.event
            ev_fixture = f"{ev.home_participant} vs {ev.away_participant}"
            ev_id = str(ev.provider_ids.get("superbet") or ev.internal_id)

            # Map selection odds
            odds_by_sel_id = {o.selection_id: o for o in graph.odds_list if o.bookmaker.lower() == "superbet"}
            sels_by_mkt_id: Dict[str, List[Selection]] = {}
            for sel in graph.selections:
                sels_by_mkt_id.setdefault(sel.market_id, []).append(sel)

            for mkt in graph.markets:
                m_type = mkt.market_type
                if not m_type.startswith("PLAYER_"):
                    continue

                # Strictly skip non-standard player prop sub-markets (first goalscorer, red card, half-time, etc.)
                if m_type in ("PLAYER_FIRST_GOAL", "PLAYER_LAST_GOAL", "PLAYER_RED_CARDS", "PLAYER_GOALS_FIRST_HALF", "PLAYER_GOALS_SECOND_HALF"):
                    continue

                mkt_raw_name = (mkt.metadata.get("raw_name") or "").lower()
                mkt_period = mkt.metadata.get("period") or "FULL_TIME"
                mkt_scope = mkt.metadata.get("scope") or "PLAYER"

                # Guard against unclassified combo/special markets
                if any(k in mkt_raw_name for k in ("1. gola", "pierwszego gola", "ostatniego gola", "w 1. połowie", "w 1. polowie", "w 2. połowie", "w 2. polowie", "czerwoną kartkę", "czerwona kartke", "spoza pola karnego", "głową", "glowa", "nogą", "noga", "z rzutu karnego", "strzeli i wygra")):
                    continue

                canonical_stat = m_type.replace("PLAYER_", "")
                if stat_type and canonical_stat != stat_type.upper():
                    continue

                mkt_player = mkt.metadata.get("player_name") or mkt.metadata.get("player")
                mkt_line = mkt.line

                for sel in sels_by_mkt_id.get(mkt.internal_id, []):
                    p_name = sel.participant or mkt_player or sel.metadata.get("player_name")
                    if not p_name:
                        continue

                    sel_raw_name = (sel.metadata.get("raw_name") or getattr(sel, "name", "") or sel.selection_type or "").lower()
                    if any(k in sel_raw_name for k in ("1. gola", "pierwszego gola", "ostatniego gola", "w 1. połowie", "w 1. polowie", "w 2. połowie", "w 2. polowie", "czerwona", "czerwoną")):
                        continue

                    sel_line = sel.line if sel.line is not None else mkt_line
                    if sel_line is None:
                        if canonical_stat == "GOALS":
                            if "2+" in (mkt_raw_name + " " + sel_raw_name) or "2 lub więcej" in (mkt_raw_name + " " + sel_raw_name):
                                sel_line = 1.5
                            elif "3+" in (mkt_raw_name + " " + sel_raw_name) or "3 lub więcej" in (mkt_raw_name + " " + sel_raw_name):
                                sel_line = 2.5
                            else:
                                sel_line = 0.5
                        elif canonical_stat in ("CARDS", "ASSISTS"):
                            sel_line = 0.5

                    if sel_line is None:
                        continue

                    sel_side = sel.selection_type.upper()
                    if sel_side in ("TAK", "YES", "OVER", "POWYŻEJ", "POWYZEJ"):
                        side = "OVER"
                    elif sel_side in ("NIE", "NO", "UNDER", "PONIŻEJ", "PONIZEJ"):
                        side = "UNDER"
                    else:
                        side = sel_side

                    odds_obj = odds_by_sel_id.get(sel.internal_id)
                    price = float(odds_obj.decimal_odds) if odds_obj and odds_obj.decimal_odds > 1.0 else 0.0
                    is_active = (mkt.status == "OPEN") and (price > 1.0)

                    quotes.append(
                        NormalizedExecutionQuote(
                            bookmaker="Superbet",
                            player=p_name,
                            fixture=ev_fixture,
                            stat_type=canonical_stat,
                            line=float(sel_line),
                            side=side,
                            odds=price,
                            active=is_active,
                            event_id=ev_id,
                            market_name=mkt.metadata.get("raw_name"),
                            selection_name=sel.selection_type,
                            market_type=m_type,
                            period=mkt_period,
                            scope=mkt_scope,
                        )
                    )

        return quotes

    def get_team_prop_markets(
        self,
        fixture_name: Optional[str] = None,
        kickoff: Optional[str] = None,
        team_name: Optional[str] = None,
        stat_type: Optional[str] = None,
        normalized_events: Optional[List[NormalizedGraph]] = None,
    ) -> List[NormalizedExecutionQuote]:
        """Extracts quotes from normalized Superbet graph instances for team props."""
        quotes: List[NormalizedExecutionQuote] = []
        if not normalized_events:
            return quotes

        target_team_norm, _ = normalize_team_name(team_name) if team_name else (None, None)

        for graph in normalized_events:
            ev = graph.event
            ev_fixture = f"{ev.home_participant} vs {ev.away_participant}"
            ev_id = str(ev.provider_ids.get("superbet") or ev.internal_id)

            odds_by_sel_id = {o.selection_id: o for o in graph.odds_list if o.bookmaker.lower() == "superbet"}
            sels_by_mkt_id: Dict[str, List[Selection]] = {}
            for sel in graph.selections:
                sels_by_mkt_id.setdefault(sel.market_id, []).append(sel)

            for mkt in graph.markets:
                mkt_scope = str(mkt.metadata.get("scope") or "MATCH").upper()
                if mkt_scope != "TEAM":
                    continue

                mkt_period = str(mkt.metadata.get("period") or "FULL_TIME").upper()
                canonical_stat = str(mkt.metadata.get("metric") or "GOALS").upper()

                if stat_type and canonical_stat != stat_type.upper():
                    continue

                role = str(mkt.metadata.get("participant_role") or "HOME").upper()
                curr_team = ev.home_participant if role == "HOME" else ev.away_participant

                if target_team_norm:
                    norm_curr, _ = normalize_team_name(curr_team)
                    if norm_curr != target_team_norm:
                        continue

                mkt_line = mkt.line

                for sel in sels_by_mkt_id.get(mkt.internal_id, []):
                    sel_line = sel.line if sel.line is not None else mkt_line
                    if sel_line is None:
                        continue

                    sel_side = sel.selection_type.upper()
                    if sel_side in ("TAK", "YES", "OVER", "POWYŻEJ", "POWYZEJ", "+"):
                        side = "OVER"
                    elif sel_side in ("NIE", "NO", "UNDER", "PONIŻEJ", "PONIZEJ", "-"):
                        side = "UNDER"
                    else:
                        side = sel_side

                    odds_obj = odds_by_sel_id.get(sel.internal_id)
                    price = float(odds_obj.decimal_odds) if odds_obj and odds_obj.decimal_odds > 1.0 else 0.0
                    is_active = (mkt.status == "OPEN") and (price > 1.0)

                    quotes.append(
                        NormalizedExecutionQuote(
                            bookmaker="Superbet",
                            player="",
                            team=curr_team,
                            participant_role=role,
                            fixture=ev_fixture,
                            stat_type=canonical_stat,
                            line=float(sel_line),
                            side=side,
                            odds=price,
                            active=is_active,
                            event_id=ev_id,
                            market_name=mkt.metadata.get("raw_name"),
                            selection_name=sel.selection_type,
                            market_type=mkt.market_type,
                            period=mkt_period,
                            scope="TEAM",
                        )
                    )

        return quotes


class BetclicExecutionProvider(ExecutionMarketProvider):
    """Betclic execution market adapter extracting normalized player props and team props."""

    @property
    def bookmaker_name(self) -> str:
        return "Betclic"

    def get_player_prop_markets(
        self,
        fixture_name: str,
        kickoff: Optional[str] = None,
        player_name: Optional[str] = None,
        stat_type: Optional[str] = None,
        normalized_events: Optional[List[NormalizedGraph]] = None,
    ) -> List[NormalizedExecutionQuote]:
        """Extracts quotes from normalized Betclic graph instances."""
        quotes: List[NormalizedExecutionQuote] = []
        if not normalized_events:
            return quotes

        norm_fix, _ = normalize_team_name(fixture_name)
        target_player_norm = normalize_player_name(player_name) if player_name else None

        for graph in normalized_events:
            ev = graph.event
            ev_fixture = f"{ev.home_participant} vs {ev.away_participant}"
            ev_id = str(ev.provider_ids.get("betclic") or ev.internal_id)

            odds_by_sel_id = {o.selection_id: o for o in graph.odds_list if o.bookmaker.lower() == "betclic"}
            sels_by_mkt_id: Dict[str, List[Selection]] = {}
            for sel in graph.selections:
                sels_by_mkt_id.setdefault(sel.market_id, []).append(sel)

            for mkt in graph.markets:
                m_type = mkt.market_type
                if not m_type.startswith("PLAYER_"):
                    continue

                if m_type in ("PLAYER_FIRST_GOAL", "PLAYER_LAST_GOAL", "PLAYER_RED_CARDS", "PLAYER_GOALS_FIRST_HALF", "PLAYER_GOALS_SECOND_HALF"):
                    continue

                mkt_raw_name = (mkt.metadata.get("raw_name") or "").lower()
                mkt_period = mkt.metadata.get("period") or "FULL_TIME"
                mkt_scope = mkt.metadata.get("scope") or "PLAYER"

                if any(k in mkt_raw_name for k in ("1. gola", "pierwszego gola", "ostatniego gola", "w 1. połowie", "w 1. polowie", "w 2. połowie", "w 2. polowie", "czerwoną kartkę", "czerwona kartke", "spoza pola karnego", "głową", "glowa", "nogą", "noga", "z rzutu karnego", "strzeli i wygra")):
                    continue

                canonical_stat = m_type.replace("PLAYER_", "")
                if stat_type and canonical_stat != stat_type.upper():
                    continue

                mkt_player = mkt.metadata.get("player_name") or mkt.metadata.get("player")
                mkt_line = mkt.line

                for sel in sels_by_mkt_id.get(mkt.internal_id, []):
                    p_name = sel.participant or mkt_player
                    if not p_name:
                        continue

                    sel_raw_name = (sel.metadata.get("raw_name") or getattr(sel, "name", "") or sel.selection_type or "").lower()
                    if any(k in sel_raw_name for k in ("1. gola", "pierwszego gola", "ostatniego gola", "w 1. połowie", "w 1. polowie", "w 2. połowie", "w 2. polowie", "czerwona", "czerwoną")):
                        continue

                    sel_line = sel.line if sel.line is not None else mkt_line
                    if sel_line is None:
                        if canonical_stat == "GOALS":
                            if "2+" in (mkt_raw_name + " " + sel_raw_name) or "2 lub więcej" in (mkt_raw_name + " " + sel_raw_name):
                                sel_line = 1.5
                            elif "3+" in (mkt_raw_name + " " + sel_raw_name) or "3 lub więcej" in (mkt_raw_name + " " + sel_raw_name):
                                sel_line = 2.5
                            else:
                                sel_line = 0.5
                        elif canonical_stat in ("CARDS", "ASSISTS"):
                            sel_line = 0.5

                    if sel_line is None:
                        continue

                    sel_side = sel.selection_type.upper()
                    if sel_side in ("TAK", "YES", "OVER", "POWYŻEJ", "POWYZEJ"):
                        side = "OVER"
                    elif sel_side in ("NIE", "NO", "UNDER", "PONIŻEJ", "PONIZEJ"):
                        side = "UNDER"
                    else:
                        side = sel_side

                    odds_obj = odds_by_sel_id.get(sel.internal_id)
                    price = float(odds_obj.decimal_odds) if odds_obj and odds_obj.decimal_odds > 1.0 else 0.0
                    is_active = (mkt.status == "OPEN") and (price > 1.0)

                    quotes.append(
                        NormalizedExecutionQuote(
                            bookmaker="Betclic",
                            player=p_name,
                            fixture=ev_fixture,
                            stat_type=canonical_stat,
                            line=float(sel_line),
                            side=side,
                            odds=price,
                            active=is_active,
                            event_id=ev_id,
                            market_name=mkt.metadata.get("raw_name"),
                            selection_name=sel.selection_type,
                            market_type=m_type,
                            period=mkt_period,
                            scope=mkt_scope,
                        )
                    )

        return quotes

    def get_team_prop_markets(
        self,
        fixture_name: Optional[str] = None,
        kickoff: Optional[str] = None,
        team_name: Optional[str] = None,
        stat_type: Optional[str] = None,
        normalized_events: Optional[List[NormalizedGraph]] = None,
    ) -> List[NormalizedExecutionQuote]:
        """Extracts quotes from normalized Betclic graph instances for team props."""
        quotes: List[NormalizedExecutionQuote] = []
        if not normalized_events:
            return quotes

        target_team_norm, _ = normalize_team_name(team_name) if team_name else (None, None)

        for graph in normalized_events:
            ev = graph.event
            ev_fixture = f"{ev.home_participant} vs {ev.away_participant}"
            ev_id = str(ev.provider_ids.get("betclic") or ev.internal_id)

            odds_by_sel_id = {o.selection_id: o for o in graph.odds_list if o.bookmaker.lower() == "betclic"}
            sels_by_mkt_id: Dict[str, List[Selection]] = {}
            for sel in graph.selections:
                sels_by_mkt_id.setdefault(sel.market_id, []).append(sel)

            for mkt in graph.markets:
                mkt_scope = str(mkt.metadata.get("scope") or "MATCH").upper()
                if mkt_scope != "TEAM":
                    continue

                mkt_period = str(mkt.metadata.get("period") or "FULL_TIME").upper()
                canonical_stat = str(mkt.metadata.get("metric") or "GOALS").upper()

                if stat_type and canonical_stat != stat_type.upper():
                    continue

                role = str(mkt.metadata.get("participant_role") or "HOME").upper()
                curr_team = ev.home_participant if role == "HOME" else ev.away_participant

                if target_team_norm:
                    norm_curr, _ = normalize_team_name(curr_team)
                    if norm_curr != target_team_norm:
                        continue

                mkt_line = mkt.line

                for sel in sels_by_mkt_id.get(mkt.internal_id, []):
                    sel_line = sel.line if sel.line is not None else mkt_line
                    if sel_line is None:
                        continue

                    sel_side = sel.selection_type.upper()
                    if sel_side in ("TAK", "YES", "OVER", "POWYŻEJ", "POWYZEJ", "+"):
                        side = "OVER"
                    elif sel_side in ("NIE", "NO", "UNDER", "PONIŻEJ", "PONIZEJ", "-"):
                        side = "UNDER"
                    else:
                        side = sel_side

                    odds_obj = odds_by_sel_id.get(sel.internal_id)
                    price = float(odds_obj.decimal_odds) if odds_obj and odds_obj.decimal_odds > 1.0 else 0.0
                    is_active = (mkt.status == "OPEN") and (price > 1.0)

                    quotes.append(
                        NormalizedExecutionQuote(
                            bookmaker="Betclic",
                            player="",
                            team=curr_team,
                            participant_role=role,
                            fixture=ev_fixture,
                            stat_type=canonical_stat,
                            line=float(sel_line),
                            side=side,
                            odds=price,
                            active=is_active,
                            event_id=ev_id,
                            market_name=mkt.metadata.get("raw_name"),
                            selection_name=sel.selection_type,
                            market_type=mkt.market_type,
                            period=mkt_period,
                            scope="TEAM",
                        )
                    )

        return quotes


class ExecutionMarketEngine:
    """Coordinates execution adapters and acquisition across Polish bookmakers."""

    def __init__(self):
        self.providers: Dict[str, ExecutionMarketProvider] = {
            "Superbet": SuperbetExecutionProvider(),
            "Betclic": BetclicExecutionProvider(),
        }

    def extract_quotes_from_graphs(
        self,
        normalized_graphs: List[NormalizedGraph],
        stat_type: Optional[str] = None,
    ) -> List[NormalizedExecutionQuote]:
        """Extracts all normalized player prop quotes across provided bookmaker entity graphs."""
        quotes: List[NormalizedExecutionQuote] = []
        for g in normalized_graphs:
            is_superbet = "superbet" in g.event.provider_ids or any(o.bookmaker.lower() == "superbet" for o in g.odds_list)
            is_betclic = "betclic" in g.event.provider_ids or any(o.bookmaker.lower() == "betclic" for o in g.odds_list)

            if is_superbet:
                quotes.extend(self.providers["Superbet"].get_player_prop_markets(
                    fixture_name=f"{g.event.home_participant} vs {g.event.away_participant}",
                    stat_type=stat_type,
                    normalized_events=[g],
                ))
            if is_betclic:
                quotes.extend(self.providers["Betclic"].get_player_prop_markets(
                    fixture_name=f"{g.event.home_participant} vs {g.event.away_participant}",
                    stat_type=stat_type,
                    normalized_events=[g],
                ))

        return quotes

    def extract_team_quotes_from_graphs(
        self,
        normalized_graphs: List[NormalizedGraph],
        stat_type: Optional[str] = None,
    ) -> List[NormalizedExecutionQuote]:
        """Extracts all normalized team prop quotes across provided bookmaker entity graphs."""
        quotes: List[NormalizedExecutionQuote] = []
        for g in normalized_graphs:
            is_superbet = "superbet" in g.event.provider_ids or any(o.bookmaker.lower() == "superbet" for o in g.odds_list)
            is_betclic = "betclic" in g.event.provider_ids or any(o.bookmaker.lower() == "betclic" for o in g.odds_list)

            if is_superbet:
                quotes.extend(self.providers["Superbet"].get_team_prop_markets(
                    fixture_name=f"{g.event.home_participant} vs {g.event.away_participant}",
                    stat_type=stat_type,
                    normalized_events=[g],
                ))
            if is_betclic:
                quotes.extend(self.providers["Betclic"].get_team_prop_markets(
                    fixture_name=f"{g.event.home_participant} vs {g.event.away_participant}",
                    stat_type=stat_type,
                    normalized_events=[g],
                ))

        return quotes

