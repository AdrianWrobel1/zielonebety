"""
Event Selection and Prioritization Policy

Provides a clean, modular, extensible boundary for selecting, prioritizing,
and bounding which discovered events proceed through normalization and
expensive detail acquisition in production scan cycles.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
import logging
import re
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple, Union

from domain.models import Competition, Event
from normalization.base_normalizer import NormalizedGraph
from normalization.identity import parse_kickoff_to_utc, normalize_competition_name
from normalization.competitions import resolve_canonical_competition

logger = logging.getLogger("zielonebety.orchestration.event_selection")


@dataclass(frozen=True)
class DetailPrioritizationResult:
    """Encapsulates deterministic detail selection output with full telemetry metrics."""
    selected_event_ids: List[str]
    candidates_available: int
    candidates_overlap: int
    events_selected: int
    events_overlap_selected: int
    overlap_selection_rate: float
    multi_market_expected_events: int
    ranked_event_ids: List[str] = field(default_factory=list)
    # Stage 24C Smart Prioritization Telemetry
    tier_0_available: int = 0
    tier_0_selected: int = 0
    tier_1_available: int = 0
    tier_1_selected: int = 0
    tier_2_available: int = 0
    tier_2_selected: int = 0
    tier_samples: Dict[str, List[str]] = field(default_factory=dict)



DEFAULT_PREFERRED_COMPETITIONS: Tuple[str, ...] = (
    "Premier League",
    "LaLiga",
    "Serie A",
    "Bundesliga",
    "Ligue 1",
    "Ekstraklasa",
    "Champions League",
    "Europa League",
    "Conference League",
)

# Tier 0: Major continental & international senior tournaments and qualifiers
TOP_TIER_COMPETITIONS: Tuple[str, ...] = (
    # Major UEFA Tournaments & Qualifiers
    "Champions League",
    "UEFA Champions League",
    "UEFA Champions League (Qualifiers)",
    "Liga Mistrzów",
    "Liga Mistrzów UEFA",
    "UEFA Liga Mistrzów",
    "Liga Mistrzów - Kwalifikacje",
    "Eliminacje Ligi Mistrzów",
    "Europa League",
    "UEFA Europa League",
    "UEFA Europa League (Qualifiers)",
    "Liga Europy",
    "Liga Europy UEFA",
    "UEFA Liga Europy",
    "Liga Europy - Kwalifikacje",
    "Eliminacje Ligi Europy",
    "Conference League",
    "UEFA Conference League",
    "UEFA Conference League (Qualifiers)",
    "Liga Konferencji",
    "Liga Konferencji UEFA",
    "UEFA Liga Konferencji",
    "Liga Konferencji - Kwalifikacje",
    "Eliminacje Ligi Konferencji",
    # Major International Senior Tournaments
    "World Cup",
    "FIFA World Cup",
    "Mistrzostwa Świata",
    "Euro",
    "UEFA Euro",
    "Mistrzostwa Europy",
    "Nations League",
    "Liga Narodów",
    "Copa America",
)

# Tier 1: Big 5 European leagues, Ekstraklasa, Eredivisie, Primeira Liga
SECONDARY_TIER_COMPETITIONS: Tuple[str, ...] = (
    # England Premier League
    "Premier League",
    "English Premier League",
    "EPL",
    "Anglia 1",
    "Anglia - Premier League",
    "Anglia - 1. liga",
    "Anglia - 1 liga",
    "Anglia: Premier League",
    # Spain La Liga
    "LaLiga",
    "La Liga",
    "La Liga EA Sports",
    "Primera Division",
    "Hiszpania 1",
    "Hiszpania - LaLiga",
    "Hiszpania - 1. liga",
    "Hiszpania - 1 liga",
    "Hiszpania: LaLiga",
    "Hiszpania - Primera Division",
    # Italy Serie A
    "Serie A",
    "Italy Serie A",
    "Italia Serie A",
    "Włochy 1",
    "Wlochy 1",
    "Włochy - Serie A",
    "Wlochy - Serie A",
    "Włochy - 1. liga",
    "Wlochy - 1. liga",
    "Włochy - 1 liga",
    "Wlochy - 1 liga",
    "Włochy: Serie A",
    "Wlochy: Serie A",
    # Germany Bundesliga
    "Bundesliga",
    "German Bundesliga",
    "1. Bundesliga",
    "1 Bundesliga",
    "Niemcy 1",
    "Niemcy - Bundesliga",
    "Niemcy - 1. liga",
    "Niemcy - 1 liga",
    "Niemcy: Bundesliga",
    # France Ligue 1
    "Ligue 1",
    "French Ligue 1",
    "Ligue 1 McDonald's",
    "Francja 1",
    "Francja - Ligue 1",
    "Francja - 1. liga",
    "Francja - 1 liga",
    "Francja: Ligue 1",
    # Poland Ekstraklasa
    "Ekstraklasa",
    "PKO BP Ekstraklasa",
    "PKO Ekstraklasa",
    "Polska - Ekstraklasa",
    "Polska Ekstraklasa",
    "Polska 1",
    "Polska 1 liga",
    "Polska 1. liga",
    # Netherlands & Portugal
    "Eredivisie",
    "Holandia 1",
    "Holandia - Eredivisie",
    "Holandia - 1. liga",
    "Primeira Liga",
    "Liga Portugal",
    "Portugalia 1",
    "Portugalia - Primeira Liga",
    "Portugalia - 1. liga",
)


class EventSelectionPolicy(ABC):
    """Abstract base class for event selection and prioritization policies."""

    @abstractmethod
    def filter_and_rank_discovered_items(
        self,
        items: Sequence[Any],
        limit: Optional[int] = None,
        preferred_competitions: Optional[Sequence[str]] = None,
        hours_ahead: Optional[int] = None,
        current_time: Optional[datetime] = None,
    ) -> List[Any]:
        """Filters and ranks discovered provider items (e.g. SuperbetDiscoveredItem, BetclicDiscoveredItem).

        Args:
            items: Discovered event items from provider discovery.
            limit: Maximum number of events to retain (None for no cap).
            preferred_competitions: Sequence of prioritized competition names/substrings.
            hours_ahead: Maximum hours ahead from current_time to include events.
            current_time: Reference timestamp (default: UTC now).

        Returns:
            Filtered and prioritized list of discovered items.
        """
        pass

    @abstractmethod
    def select_events_for_detail(
        self,
        items: Sequence[Any],
        max_detail_requests: Optional[int] = None,
        preferred_competitions: Optional[Sequence[str]] = None,
    ) -> List[str]:
        """Selects provider event IDs that should undergo Tier 2 full market detail acquisition.

        Args:
            items: Discovered event items.
            max_detail_requests: Maximum number of detail requests allowed.
            preferred_competitions: Sequence of prioritized competition names/substrings.

        Returns:
            List of provider event IDs selected for detail payload download.
        """
        pass

    @abstractmethod
    def prioritize_detail_events(
        self,
        discovered_items: Sequence[Any],
        overlap_event_ids: Optional[Set[str]] = None,
        max_detail_requests: Optional[int] = None,
        preferred_competitions: Optional[Sequence[str]] = None,
        hours_ahead: Optional[int] = None,
        current_time: Optional[datetime] = None,
    ) -> DetailPrioritizationResult:
        """Deterministically prioritizes discovered events for Tier 2 detail acquisition, favoring overlap."""
        pass

    @abstractmethod
    def filter_normalized_graphs(
        self,
        graphs: Sequence[NormalizedGraph],
        limit: Optional[int] = None,
        preferred_competitions: Optional[Sequence[str]] = None,
        overlap_event_ids: Optional[Set[str]] = None,
    ) -> List[NormalizedGraph]:
        """Filters normalized event graphs before cross-bookmaker matching."""
        pass


class DefaultEventSelectionPolicy(EventSelectionPolicy):
    """Deterministic, production-ready event selection policy prioritizing high-liquidity competitions and upcoming kickoff times."""

    def __init__(
        self,
        default_preferred_competitions: Optional[Sequence[str]] = None,
    ) -> None:
        self.default_preferred_competitions: Tuple[str, ...] = tuple(
            default_preferred_competitions or ()
        )

    def _extract_item_competition(self, item: Any) -> str:
        """Helper to extract competition name from various item formats."""
        if hasattr(item, "competition_name") and item.competition_name:
            return str(item.competition_name)
        if hasattr(item, "competition") and item.competition:
            if isinstance(item.competition, str):
                return item.competition
            if hasattr(item.competition, "name"):
                return str(item.competition.name)
        if isinstance(item, dict):
            comp = item.get("competition")
            if isinstance(comp, dict):
                return str(comp.get("name", ""))
            if isinstance(comp, str):
                return comp
            return str(item.get("competition_name", ""))
        return ""

    def _extract_item_kickoff(self, item: Any) -> Optional[datetime]:
        """Helper to extract kickoff datetime in UTC from various item formats."""
        raw_start = None
        if hasattr(item, "start_time") and item.start_time:
            raw_start = item.start_time
        elif hasattr(item, "scheduled_start") and item.scheduled_start:
            raw_start = item.scheduled_start
        elif isinstance(item, dict):
            raw_start = item.get("start_time") or item.get("start_date") or item.get("scheduled_start")
        elif isinstance(item, str):
            raw_start = item

        if raw_start is None:
            return None

        if isinstance(raw_start, datetime):
            return raw_start if raw_start.tzinfo else raw_start.replace(tzinfo=timezone.utc)

        try:
            return parse_kickoff_to_utc(str(raw_start))
        except Exception:
            return None

    def _extract_item_id(self, item: Any) -> str:
        """Helper to extract provider event ID from item."""
        if hasattr(item, "event_id") and item.event_id:
            return str(item.event_id)
        if hasattr(item, "provider_event_id") and item.provider_event_id:
            return str(item.provider_event_id)
        if isinstance(item, dict):
            return str(item.get("event_id") or item.get("id") or item.get("provider_event_id", ""))
        return ""

    def _extract_item_name(self, item: Any) -> str:
        """Helper to extract event name for deterministic tie-breaking."""
        if hasattr(item, "match_name") and item.match_name:
            return str(item.match_name)
        if hasattr(item, "name") and item.name:
            return str(item.name)
        if isinstance(item, dict):
            return str(item.get("match_name") or item.get("name") or item.get("event_name", ""))
        return ""

    def calculate_competition_tier(
        self,
        comp_name: str,
        preferred_competitions: Optional[Sequence[str]] = None,
    ) -> int:
        """Calculates deterministic competition priority tier.

        Returns:
            0: Top Tier (Preferred/Top European league or Champions League)
            1: Secondary Tier (Domestic cups / high-value leagues)
            2: Standard Tier (All other competitions / youth leagues)
        """
        if not comp_name or not str(comp_name).strip():
            return 2

        # Normalize competition name (NFKD diacritic stripping, lowercase, punctuation removal)
        norm_name, tokens = normalize_competition_name(str(comp_name))
        if not norm_name:
            return 2

        prefs = preferred_competitions if (preferred_competitions is not None and len(preferred_competitions) > 0) else self.default_preferred_competitions

        # 1. Youth, Reserve, and Non-Senior Safeguard:
        # Any tournament containing youth/reserve markers is strictly relegated to Tier 2
        # unless the exact name was explicitly configured in preferred_competitions.
        YOUTH_RESERVE_TOKENS = {
            "u15", "u16", "u17", "u18", "u19", "u20", "u21", "u22", "u23",
            "youth", "juniors", "juniorzy", "juniorow", "rezerwy", "reserves", "primavera",
            "kobiety", "women", "femmes", "frauen", "femenino",
        }

        is_explicit_pref = any(pref.lower().strip() == norm_name for pref in (prefs or ()))
        if not is_explicit_pref:
            if (
                any(t in YOUTH_RESERVE_TOKENS for t in tokens)
                or re.search(r"\bu\s*(?:1[5-9]|2[0-3])\b", norm_name)
                or "premier league 2" in norm_name
            ):
                return 2

        # Country prefixes that denote other countries when combined with "Premier League"
        NON_ENGLISH_PL_PREFIXES = (
            "uganda", "ghana", "egypt", "malta", "kenya", "nigeria", "tanzania", "rwanda",
            "zambia", "zimbabwe", "india", "hong kong", "singapore", "jamaica", "iceland",
            "faroe", "kuwait", "bahrain", "qatar", "uae", "jordan", "lebanon", "oman",
            "armenia", "azerbaijan", "kazakhstan", "belarus", "ukraine", "russia", "israel",
            "ireland", "northern ireland", "wales", "scotland", "south africa", "austria", "belgium",
        )

        def _matches_pattern(target_norm: str, pattern: str) -> bool:
            p_norm, _ = normalize_competition_name(pattern)
            if not p_norm:
                return False

            if p_norm in ("premier league", "anglia 1", "anglia 1 liga", "anglia premier league"):
                if "premier league 2" in target_norm or "anglia 2" in target_norm:
                    return False
                for prefix in NON_ENGLISH_PL_PREFIXES:
                    if prefix in target_norm:
                        return False
                return (target_norm == "premier league") or (
                    "premier league" in target_norm
                    and (
                        "england" in target_norm
                        or "anglia" in target_norm
                        or "english" in target_norm
                        or "uk" in target_norm
                        or target_norm.startswith("premier league")
                    )
                ) or target_norm in ("anglia 1", "anglia 1 liga", "anglia premier league")

            if p_norm in ("laliga", "la liga", "primera division", "hiszpania 1", "hiszpania 1 liga"):
                if "laliga 2" in target_norm or "la liga 2" in target_norm or "segunda" in target_norm or "hiszpania 2" in target_norm:
                    return False
                return (
                    "laliga" in target_norm
                    or "la liga" in target_norm
                    or "primera division" in target_norm
                    or target_norm in ("hiszpania 1", "hiszpania 1 liga", "spain 1", "spain 1 liga")
                )

            if p_norm in ("bundesliga", "1 bundesliga", "niemcy 1", "niemcy 1 liga"):
                if "2 bundesliga" in target_norm or "2. bundesliga" in target_norm or "austria" in target_norm or "niemcy 2" in target_norm:
                    return False
                return "bundesliga" in target_norm or target_norm in ("niemcy 1", "niemcy 1 liga", "germany 1")

            if p_norm in ("serie a", "wlochy 1", "wlochy 1 liga"):
                if "serie a2" in target_norm or "serie b" in target_norm or "wlochy 2" in target_norm:
                    return False
                return "serie a" in target_norm or target_norm in ("wlochy 1", "wlochy 1 liga", "italy 1", "italia 1")

            if p_norm in ("ligue 1", "francja 1", "francja 1 liga"):
                if "ligue 2" in target_norm or "francja 2" in target_norm:
                    return False
                return "ligue 1" in target_norm or target_norm in ("francja 1", "francja 1 liga", "france 1")

            # Exact match or word-bounded token match
            if (
                target_norm == p_norm
                or target_norm.startswith(p_norm + " ")
                or target_norm.endswith(" " + p_norm)
                or (" " + p_norm + " ") in target_norm
                or (p_norm in target_norm and len(p_norm) >= 6)
            ):
                return True

            return False

        # Check canonical competition registry
        comp_res = resolve_canonical_competition(comp_name)
        if preferred_competitions is not None and len(preferred_competitions) > 0:
            for pref in preferred_competitions:
                if _matches_pattern(comp_res.canonical_name, pref) or _matches_pattern(norm_name, pref):
                    return 0

        if comp_res.confidence > 0.0:
            return comp_res.tier

        # Check secondary tier
        for sec in SECONDARY_TIER_COMPETITIONS:
            if _matches_pattern(norm_name, sec):
                return 1

        return 2

    def _matches_preferred_competition(
        self,
        comp_name: str,
        preferred_competitions: Sequence[str],
    ) -> bool:
        """Check if competition name matches any preferred competition pattern."""
        return self.calculate_competition_tier(comp_name, preferred_competitions) == 0

    def filter_and_rank_discovered_items(
        self,
        items: Sequence[Any],
        limit: Optional[int] = None,
        preferred_competitions: Optional[Sequence[str]] = None,
        hours_ahead: Optional[int] = None,
        current_time: Optional[datetime] = None,
    ) -> List[Any]:
        """Filters items by kickoff window and prioritizes preferred competitions and nearest start times."""
        if not items:
            return []

        now_dt = current_time or datetime.now(timezone.utc)
        active_prefs = preferred_competitions if (preferred_competitions is not None and len(preferred_competitions) > 0) else self.default_preferred_competitions

        filtered_items: List[Any] = []
        for item in items:
            kickoff = self._extract_item_kickoff(item)
            if hours_ahead is not None and kickoff is not None:
                max_future = now_dt + timedelta(hours=hours_ahead)
                # Keep events that start between now - 2h (in-flight/recent) and max_future
                min_past = now_dt - timedelta(hours=2)
                if kickoff < min_past or kickoff > max_future:
                    continue
            filtered_items.append(item)

        # Deterministic ranking key:
        # Priority 1: Competition Tier (0 = top-tier, 1 = secondary, 2 = standard)
        # Priority 2: Kickoff timestamp (earlier/upcoming = lower timestamp)
        # Priority 3: Stable event name tie-breaker (alphabetical)
        def _sort_key(it: Any) -> Tuple[int, float, str]:
            comp_name = self._extract_item_competition(it)
            tier = self.calculate_competition_tier(comp_name, active_prefs)
            kickoff = self._extract_item_kickoff(it)
            kickoff_ts = kickoff.timestamp() if kickoff else 9999999999.0
            name_str = self._extract_item_name(it) or self._extract_item_id(it)
            return (tier, kickoff_ts, name_str)

        ranked = sorted(filtered_items, key=_sort_key)

        if limit is not None and limit > 0:
            return ranked[:limit]
        return ranked

    def select_events_for_detail(
        self,
        items: Sequence[Any],
        max_detail_requests: Optional[int] = None,
        preferred_competitions: Optional[Sequence[str]] = None,
    ) -> List[str]:
        """Selects high-priority event IDs for Tier 2 detail acquisition up to max_detail_requests."""
        if not items:
            return []

        ranked = self.filter_and_rank_discovered_items(
            items=items,
            limit=max_detail_requests,
            preferred_competitions=preferred_competitions,
        )

        selected_ids: List[str] = []
        for item in ranked:
            eid = self._extract_item_id(item)
            if eid and eid not in selected_ids:
                selected_ids.append(eid)

        return selected_ids

    def prioritize_detail_events(
        self,
        discovered_items: Sequence[Any],
        overlap_event_ids: Optional[Set[str]] = None,
        max_detail_requests: Optional[int] = None,
        preferred_competitions: Optional[Sequence[str]] = None,
        hours_ahead: Optional[int] = None,
        current_time: Optional[datetime] = None,
        forced_ranked_ids: Optional[Sequence[str]] = None,
    ) -> DetailPrioritizationResult:
        """Deterministically prioritizes discovered events for Tier 2 detail acquisition, favoring overlap.

        Priority Order:
        1. Overlapping fixtures (in overlap_event_ids or forced_ranked_ids) come first.
        2. Competition Tier (0 = top-flight/preferred, 1 = secondary, 2 = standard).
        3. Kickoff proximity (earlier upcoming matches first).
        4. Deterministic string tie-breaker (event name / ID).

        If the number of overlapping events is less than max_detail_requests,
        the remaining budget falls back cleanly to the highest-priority non-overlapping events.
        """
        if not discovered_items:
            return DetailPrioritizationResult(
                selected_event_ids=[],
                candidates_available=0,
                candidates_overlap=0,
                events_selected=0,
                events_overlap_selected=0,
                overlap_selection_rate=0.0,
                multi_market_expected_events=0,
                ranked_event_ids=[],
            )

        now_dt = current_time or datetime.now(timezone.utc)
        active_prefs = (
            preferred_competitions
            if (preferred_competitions is not None and len(preferred_competitions) > 0)
            else self.default_preferred_competitions
        )
        overlap_set = set(str(eid).strip() for eid in (overlap_event_ids or set()))

        # Filter by kickoff window if requested
        filtered_items: List[Any] = []
        for item in discovered_items:
            kickoff = self._extract_item_kickoff(item)
            if hours_ahead is not None and kickoff is not None:
                max_future = now_dt + timedelta(hours=hours_ahead)
                min_past = now_dt - timedelta(hours=2)
                if kickoff < min_past or kickoff > max_future:
                    continue
            filtered_items.append(item)

        # Multi-factor deterministic ranking:
        # Priority 1: Overlap status (0 = cross-bookmaker matched overlap, 1 = non-overlap)
        # Priority 2: Competition Tier (0 = top-tier UEFA/international cups, 1 = major leagues, 2 = standard)
        # Priority 3: Potential markets count (richer market offerings first)
        # Priority 4: Kickoff timestamp (earlier first)
        # Priority 5: Event name tie-breaker
        def _sort_key(it: Any) -> Tuple[int, int, int, float, str, str]:
            eid = self._extract_item_id(it)
            is_overlap = 0 if eid in overlap_set else 1
            comp_name = self._extract_item_competition(it)
            tier = self.calculate_competition_tier(comp_name, active_prefs)
            m_count = 0
            if hasattr(it, "markets") and isinstance(it.markets, list):
                m_count = len(it.markets)
            elif hasattr(it, "metadata") and isinstance(it.metadata, dict):
                raw_m = it.metadata.get("raw", {})
                if isinstance(raw_m, dict):
                    m_count = len(raw_m.get("markets", []))
            kickoff = self._extract_item_kickoff(it)
            kickoff_ts = kickoff.timestamp() if kickoff else 9999999999.0
            name_str = self._extract_item_name(it) or eid
            return (is_overlap, tier, -m_count, kickoff_ts, name_str, eid)

        ranked = sorted(filtered_items, key=_sort_key)

        ranked_ids: List[str] = []
        seen_ids: Set[str] = set()

        # If explicit paired ordering is supplied, prepend valid discovered items in that exact order
        if forced_ranked_ids:
            discovered_id_map = {self._extract_item_id(it): it for it in filtered_items if self._extract_item_id(it)}
            for fid in forced_ranked_ids:
                fid_str = str(fid).strip()
                if fid_str in discovered_id_map and fid_str not in seen_ids:
                    seen_ids.add(fid_str)
                    ranked_ids.append(fid_str)

        for it in ranked:
            eid = self._extract_item_id(it)
            if eid and eid not in seen_ids:
                seen_ids.add(eid)
                ranked_ids.append(eid)

        limit = max_detail_requests if (max_detail_requests is not None and max_detail_requests > 0) else len(ranked_ids)
        selected_ids = ranked_ids[:limit]

        # Stage 24C: Tier breakdown telemetry
        tier_0_avail = 0
        tier_1_avail = 0
        tier_2_avail = 0
        tier_samples: Dict[str, List[str]] = {
            "Tier 0": [],
            "Tier 1": [],
            "Tier 2": [],
        }

        item_tier_map: Dict[str, int] = {}
        for it in filtered_items:
            eid = self._extract_item_id(it)
            if eid and eid not in item_tier_map:
                comp_name = self._extract_item_competition(it)
                t = self.calculate_competition_tier(comp_name, active_prefs)
                item_tier_map[eid] = t
                if t == 0:
                    tier_0_avail += 1
                    if comp_name and comp_name not in tier_samples["Tier 0"] and len(tier_samples["Tier 0"]) < 5:
                        tier_samples["Tier 0"].append(comp_name)
                elif t == 1:
                    tier_1_avail += 1
                    if comp_name and comp_name not in tier_samples["Tier 1"] and len(tier_samples["Tier 1"]) < 5:
                        tier_samples["Tier 1"].append(comp_name)
                else:
                    tier_2_avail += 1
                    if comp_name and comp_name not in tier_samples["Tier 2"] and len(tier_samples["Tier 2"]) < 5:
                        tier_samples["Tier 2"].append(comp_name)

        tier_0_sel = sum(1 for eid in selected_ids if item_tier_map.get(eid) == 0)
        tier_1_sel = sum(1 for eid in selected_ids if item_tier_map.get(eid) == 1)
        tier_2_sel = sum(1 for eid in selected_ids if item_tier_map.get(eid) == 2)

        # Calculate telemetry metrics
        candidates_available = len(discovered_items)
        candidates_overlap = len(overlap_set)
        events_selected = len(selected_ids)
        events_overlap_selected = sum(1 for eid in selected_ids if eid in overlap_set)
        overlap_selection_rate = (
            round(events_overlap_selected / events_selected, 4) if events_selected > 0 else 0.0
        )
        multi_market_expected_events = events_overlap_selected

        return DetailPrioritizationResult(
            selected_event_ids=selected_ids,
            candidates_available=candidates_available,
            candidates_overlap=candidates_overlap,
            events_selected=events_selected,
            events_overlap_selected=events_overlap_selected,
            overlap_selection_rate=overlap_selection_rate,
            multi_market_expected_events=multi_market_expected_events,
            ranked_event_ids=ranked_ids,
            tier_0_available=tier_0_avail,
            tier_0_selected=tier_0_sel,
            tier_1_available=tier_1_avail,
            tier_1_selected=tier_1_sel,
            tier_2_available=tier_2_avail,
            tier_2_selected=tier_2_sel,
            tier_samples=tier_samples,
        )

    def filter_normalized_graphs(
        self,
        graphs: Sequence[NormalizedGraph],
        limit: Optional[int] = None,
        preferred_competitions: Optional[Sequence[str]] = None,
        overlap_event_ids: Optional[Set[str]] = None,
    ) -> List[NormalizedGraph]:
        """Filters and ranks normalized event graphs, prioritizing cross-provider overlapping fixtures."""
        if not graphs:
            return []

        active_prefs = (
            preferred_competitions
            if (preferred_competitions is not None and len(preferred_competitions) > 0)
            else self.default_preferred_competitions
        )
        overlap_set = set(str(eid).strip() for eid in (overlap_event_ids or set()))

        def _sort_key(g: NormalizedGraph) -> Tuple[int, int, float, str]:
            provider_eids = set(str(eid).strip() for eid in g.event.provider_ids.values()) if g.event.provider_ids else set()
            is_overlap = 0 if (overlap_set and provider_eids.intersection(overlap_set)) else 1
            comp_name = g.competition.name if g.competition else ""
            tier = self.calculate_competition_tier(comp_name, active_prefs)
            kickoff = self._extract_item_kickoff(g.event.scheduled_start)
            kickoff_ts = kickoff.timestamp() if kickoff else 9999999999.0
            ev_name = f"{g.event.home_participant} vs {g.event.away_participant}"
            return (is_overlap, tier, kickoff_ts, ev_name)

        ranked = sorted(graphs, key=_sort_key)
        if limit is not None and limit > 0:
            return ranked[:limit]
        return ranked
