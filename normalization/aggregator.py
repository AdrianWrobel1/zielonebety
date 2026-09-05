"""
Stage 4.5: Cross-Bookmaker Canonical Event Aggregation Layer

Implements provider-independent aggregation of independently normalized provider events
and verified Stage 4.4 MatchDecisions into single CanonicalEvent representations.

Invariants & Constraints:
- Pure Representation & Aggregation: Consumes Stage 4.4 decisions; does NOT perform new matching.
- Strict Boundary: Only MATCHED decisions trigger aggregation; AMBIGUOUS and REJECTED never aggregate.
- Information Preservation: Retains full source lineage (provider IDs, external IDs, original metadata).
- Deterministic Identity: Canonical event IDs are derived deterministically from sport, canonical normalized participants, and UTC kickoff.
- Conflict Safety: One-to-many MATCHED conflicts are detected and isolated as AggregationConflict objects (no silent merges).
- Auditability: Complete match evidence, decomposed signals, score, and orientation are preserved.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from domain.models import (
    Event,
    Competition,
    EventSource,
    MatchEvidence,
    CanonicalCompetition,
    CanonicalEvent,
    generate_deterministic_canonical_event_id,
)
from normalization.base_normalizer import NormalizedGraph
from normalization.identity import (
    normalize_team_name,
    normalize_competition_name,
    parse_kickoff_to_utc,
)
from normalization.competitions import resolve_canonical_competition
from normalization.matcher import (
    MatchDecision,
    MatchDecisionType,
    MatchResult,
    OrientationType,
)


@dataclass(frozen=True)
class AggregationConflict:
    """Represents an invalid or conflicting aggregation attempt (e.g. one-to-many matches)."""
    event_id: str
    provider: str
    conflicting_event_ids: Tuple[str, ...]
    reasons: Tuple[str, ...]
    decisions: Tuple[MatchDecision, ...] = field(default_factory=tuple)


@dataclass
class CanonicalAggregationResult:
    """Structured result of Stage 4.5 canonical event aggregation."""
    canonical_events: List[CanonicalEvent] = field(default_factory=list)
    unmatched_events: List[Event] = field(default_factory=list)
    ambiguous_events: List[Event] = field(default_factory=list)
    conflicts: List[AggregationConflict] = field(default_factory=list)
    total_canonical_events: int = 0
    total_sources_aggregated: int = 0
    warnings: List[str] = field(default_factory=list)


class CanonicalEventAggregator:
    """Deterministic, provider-independent canonical event aggregator."""

    def _extract_event_and_comp(
        self,
        item: Union[Event, NormalizedGraph],
        comp_map: Optional[Dict[str, Competition]] = None,
    ) -> Tuple[Event, Optional[Competition], Optional[NormalizedGraph]]:
        """Extracts Event, optional Competition, and optional NormalizedGraph."""
        if isinstance(item, NormalizedGraph):
            return item.event, item.competition, item
        elif isinstance(item, Event):
            comp = comp_map.get(item.competition_id) if comp_map else None
            return item, comp, None
        else:
            raise TypeError(f"Unsupported item type for aggregation: {type(item)}")

    def _get_provider_name(self, event: Event) -> str:
        """Extracts the provider identifier from provider_ids or metadata."""
        if event.provider_ids:
            return next(iter(event.provider_ids.keys()))
        if event.metadata and "provider" in event.metadata:
            return str(event.metadata["provider"])
        if event.metadata:
            return next(iter(event.metadata.keys()))
        return "unknown"

    def _get_provider_event_id(self, event: Event, provider_name: str) -> str:
        """Extracts the provider's native event ID."""
        if event.provider_ids and provider_name in event.provider_ids:
            return str(event.provider_ids[provider_name])
        if event.metadata and "provider_event_id" in event.metadata:
            return str(event.metadata["provider_event_id"])
        # Fallback to internal ID if provider event ID is not explicitly separated
        return event.internal_id

    def _reconcile_kickoff(self, timestamps: List[Optional[str]]) -> Tuple[Optional[str], Optional[datetime]]:
        """Deterministically reconciles scheduled start timestamps across sources.

        Selects the earliest valid timezone-aware UTC datetime and formats as ISO-8601 UTC string.
        """
        valid_dts: List[datetime] = []
        for ts in timestamps:
            dt = parse_kickoff_to_utc(ts, default_tz="UTC")
            if dt is not None:
                valid_dts.append(dt)

        if not valid_dts:
            return None, None

        # Deterministic selection: earliest UTC kickoff
        earliest_dt = min(valid_dts)
        iso_str = earliest_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        return iso_str, earliest_dt

    def _reconcile_competition(
        self,
        competitions: List[Tuple[Optional[Competition], str]]
    ) -> Optional[CanonicalCompetition]:
        """Reconciles competition information across multiple sources using canonical registry and confidence scoring."""
        valid_comps = [(c, p) for c, p in competitions if c is not None]
        if not valid_comps:
            return None

        # Merge provider IDs and metadata across all sources
        provider_comp_ids: Dict[str, str] = {}
        merged_ext_ids: Dict[str, str] = {}
        merged_metadata: Dict[str, Any] = {}
        country: Optional[str] = None
        sport = "Football"

        for comp, prov in valid_comps:
            if comp.sport:
                sport = comp.sport
            if not country and comp.country:
                country = comp.country
            for k, v in comp.provider_ids.items():
                provider_comp_ids[k] = str(v)
            for k, v in comp.external_ids.items():
                merged_ext_ids[k] = str(v)
            for k, v in comp.metadata.items():
                merged_metadata[f"{prov}:{k}"] = v

        # Rank competitions by confidence (descending) and tier (ascending, 0 is best)
        def _score_comp(item: Tuple[Competition, str]) -> Tuple[float, int, str]:
            comp, prov = item
            conf = float(comp.metadata.get("confidence", 0.5)) if comp.metadata else 0.5
            tier = int(comp.metadata.get("tier", 2)) if comp.metadata else 2
            # Penalize generic competition names
            cname = (comp.name or "").lower()
            if any(g in cname for g in ("unknown", "superbet football", "tournament", "football")):
                conf = 0.0
                tier = 99
            return (-conf, tier, prov)

        valid_comps.sort(key=_score_comp)
        primary_comp, primary_prov = valid_comps[0]

        # Resolve canonically
        comp_res = resolve_canonical_competition(
            raw_name=primary_comp.name,
            provider_ids=provider_comp_ids,
            country=country or primary_comp.country,
        )

        return CanonicalCompetition(
            name=comp_res.canonical_name,
            sport=sport,
            country=comp_res.country or country,
            competition_id=comp_res.canonical_id,
            competition_type=comp_res.competition_type,
            tier=comp_res.tier,
            provenance=comp_res.provenance,
            confidence=comp_res.confidence,
            provider_competition_ids=provider_comp_ids,
            external_ids=merged_ext_ids,
            metadata=merged_metadata,
        )

    def aggregate(
        self,
        events: Optional[Union[List[Union[Event, NormalizedGraph]], Dict[str, Union[Event, NormalizedGraph]]]] = None,
        decisions: Optional[Union[List[MatchDecision], MatchResult]] = None,
        competition_map: Optional[Dict[str, Competition]] = None,
        source_items: Optional[List[Union[Event, NormalizedGraph]]] = None,
        target_items: Optional[List[Union[Event, NormalizedGraph]]] = None,
    ) -> CanonicalAggregationResult:
        """Aggregates normalized provider events and Stage 4.4 decisions into CanonicalEvents.

        Args:
            events: Unified collection or dictionary of Event / NormalizedGraph objects.
            decisions: List of MatchDecision objects or a MatchResult from Stage 4.4.
            competition_map: Optional mapping of competition_id -> Competition.
            source_items: Optional list of source items (if split from target items).
            target_items: Optional list of target items (if split from source items).

        Returns:
            CanonicalAggregationResult containing aggregated canonical_events, unmatched_events,
            ambiguous_events, and detected conflicts.
        """
        # 1. Index All Input Events and Competitions
        event_map: Dict[str, Event] = {}
        comp_map: Dict[str, Competition] = dict(competition_map or {})
        graph_map: Dict[str, NormalizedGraph] = {}
        warnings: List[str] = []

        all_items: List[Union[Event, NormalizedGraph]] = []
        if isinstance(events, dict):
            all_items.extend(events.values())
        elif isinstance(events, list):
            all_items.extend(events)

        if source_items:
            all_items.extend(source_items)
        if target_items:
            all_items.extend(target_items)

        for item in all_items:
            ev, comp, gr = self._extract_event_and_comp(item, comp_map)
            event_map[ev.internal_id] = ev
            if comp:
                comp_map[ev.competition_id] = comp
            if gr:
                graph_map[ev.internal_id] = gr

        # 2. Extract & Filter Decisions
        raw_decisions: List[MatchDecision] = []
        if isinstance(decisions, MatchResult):
            raw_decisions = decisions.decisions
        elif isinstance(decisions, list):
            raw_decisions = decisions

        matched_decisions: List[MatchDecision] = []
        ambiguous_event_ids: Set[str] = set()

        for d in raw_decisions:
            if d.decision == MatchDecisionType.MATCHED:
                matched_decisions.append(d)
            elif d.decision == MatchDecisionType.AMBIGUOUS:
                ambiguous_event_ids.add(d.source_event_id)
                ambiguous_event_ids.add(d.target_event_id)

        # 3. Deduplicate Decisions
        # Deduplicate identical pairs (A, B) and symmetric pairs (B, A)
        deduped_matched: Dict[Tuple[str, str], MatchDecision] = {}
        for d in matched_decisions:
            pair_key = tuple(sorted([d.source_event_id, d.target_event_id]))
            if pair_key not in deduped_matched:
                deduped_matched[pair_key] = d
            else:
                # Keep higher scoring or existing decision
                if d.total_score > deduped_matched[pair_key].total_score:
                    deduped_matched[pair_key] = d

        # 4. Build Decision Adjacency & Detect Conflicts (One-to-Many Safety)
        adj: Dict[str, List[Tuple[str, MatchDecision]]] = defaultdict(list)
        event_decision_count: Dict[str, int] = defaultdict(int)

        for (s_id, t_id), d in deduped_matched.items():
            adj[d.source_event_id].append((d.target_event_id, d))
            adj[d.target_event_id].append((d.source_event_id, d))
            event_decision_count[d.source_event_id] += 1
            event_decision_count[d.target_event_id] += 1

        # Find connected components of MATCHED decisions
        visited: Set[str] = set()
        components: List[List[str]] = []
        for e_id in adj:
            if e_id not in visited:
                comp_nodes: List[str] = []
                queue = [e_id]
                visited.add(e_id)
                while queue:
                    curr = queue.pop(0)
                    comp_nodes.append(curr)
                    for nxt, _ in adj[curr]:
                        if nxt not in visited:
                            visited.add(nxt)
                            queue.append(nxt)
                components.append(comp_nodes)

        conflicts: List[AggregationConflict] = []
        conflicted_event_ids: Set[str] = set()
        valid_components: List[List[str]] = []

        for comp_nodes in components:
            # Check for multi-provider conflicts (e.g. two Superbet events or two Betclic events in same component)
            providers_in_comp: Dict[str, List[str]] = defaultdict(list)
            for node_id in comp_nodes:
                node_ev = event_map.get(node_id)
                p_name = self._get_provider_name(node_ev) if node_ev else "unknown"
                providers_in_comp[p_name].append(node_id)

            has_conflict = False
            conflict_reasons: List[str] = []

            for p_name, p_event_ids in providers_in_comp.items():
                if len(p_event_ids) > 1:
                    has_conflict = True
                    conflict_reasons.append(
                        f"provider_multiplicity_conflict: provider '{p_name}' has multiple events {p_event_ids} in same matched component"
                    )

            if has_conflict:
                for node_id in comp_nodes:
                    conflicted_event_ids.add(node_id)
                    node_ev = event_map.get(node_id)
                    p_name = self._get_provider_name(node_ev) if node_ev else "unknown"
                    other_nodes = tuple(nid for nid in comp_nodes if nid != node_id)
                    comp_decisions = tuple(d for (s, t), d in deduped_matched.items() if s in comp_nodes and t in comp_nodes)
                    conflicts.append(
                        AggregationConflict(
                            event_id=node_id,
                            provider=p_name,
                            conflicting_event_ids=other_nodes,
                            reasons=tuple(conflict_reasons),
                            decisions=comp_decisions,
                        )
                    )
                warnings.append(
                    f"Conflicting match cluster detected and isolated for events: {comp_nodes}"
                )
            else:
                valid_components.append(comp_nodes)

        # 5. Aggregate Valid Components into CanonicalEvents
        canonical_events: List[CanonicalEvent] = []
        aggregated_event_ids: Set[str] = set()

        for comp_nodes in valid_components:
            # Deterministic sorting of participating events
            comp_events: List[Tuple[Event, str, Optional[Competition]]] = []
            for node_id in comp_nodes:
                node_ev = event_map.get(node_id)
                if node_ev:
                    p_name = self._get_provider_name(node_ev)
                    c = comp_map.get(node_ev.competition_id)
                    comp_events.append((node_ev, p_name, c))

            if not comp_events:
                continue

            # Deterministic primary/reference event selection: sort by provider name
            comp_events.sort(key=lambda x: (x[1], x[0].internal_id))
            ref_ev, ref_prov, ref_comp = comp_events[0]

            # Reconcile sport
            sport = (ref_comp.sport if ref_comp and ref_comp.sport else "Football")

            # Collect decisions and orientation in this component
            comp_decisions: List[MatchDecision] = []
            for (s, t), d in deduped_matched.items():
                if s in comp_nodes and t in comp_nodes:
                    comp_decisions.append(d)

            # Build MatchEvidence list
            evidence_list: List[MatchEvidence] = []
            for d in comp_decisions:
                s_ev = event_map.get(d.source_event_id)
                t_ev = event_map.get(d.target_event_id)
                s_p = self._get_provider_name(s_ev) if s_ev else "unknown"
                t_p = self._get_provider_name(t_ev) if t_ev else "unknown"

                sig_dict: Dict[str, Any] = {}
                for sig_name, sig_obj in d.signals.items():
                    sig_dict[sig_name] = {
                        "raw_score": sig_obj.raw_score,
                        "weight": sig_obj.weight,
                        "weighted_score": sig_obj.weighted_score,
                        "details": sig_obj.details,
                    }

                evidence_list.append(
                    MatchEvidence(
                        source_provider=s_p,
                        target_provider=t_p,
                        source_event_id=d.source_event_id,
                        target_event_id=d.target_event_id,
                        decision=str(d.decision.value if hasattr(d.decision, "value") else d.decision),
                        total_score=d.total_score,
                        orientation=str(d.orientation.value if hasattr(d.orientation, "value") else d.orientation),
                        signals=sig_dict,
                        veto_reasons=tuple(d.veto_reasons),
                        warnings=tuple(d.warnings),
                        blocking_keys=tuple(d.evidence.get("blocking_keys", ())),
                        evidence=dict(d.evidence),
                    )
                )

            # Build EventSource map
            sources: Dict[str, EventSource] = {}
            source_timestamps: List[Optional[str]] = []
            source_comps: List[Tuple[Optional[Competition], str]] = []

            for ev, p_name, c in comp_events:
                p_ev_id = self._get_provider_event_id(ev, p_name)
                source_timestamps.append(ev.scheduled_start)
                source_comps.append((c, p_name))

                # Check if this event has swapped orientation relative to reference event
                is_swapped = False
                for d in comp_decisions:
                    if (
                        (d.source_event_id == ref_ev.internal_id and d.target_event_id == ev.internal_id)
                        or (d.target_event_id == ref_ev.internal_id and d.source_event_id == ev.internal_id)
                    ):
                        if d.orientation == OrientationType.ORIENTATION_SWAP:
                            is_swapped = True

                sources[p_name] = EventSource(
                    provider=p_name,
                    provider_event_id=p_ev_id,
                    internal_event_id=ev.internal_id,
                    home_participant=ev.home_participant,
                    away_participant=ev.away_participant,
                    scheduled_start=ev.scheduled_start,
                    competition_name=c.name if c else None,
                    provider_competition_id=c.provider_ids.get(p_name) if c else None,
                    external_ids=dict(ev.external_ids or {}),
                    metadata=dict(ev.metadata or {}),
                    is_orientation_swapped=is_swapped,
                    original_event=ev,
                )

            # Reconcile Canonical Fields
            canonical_home = ref_ev.home_participant
            canonical_away = ref_ev.away_participant
            canonical_start, _ = self._reconcile_kickoff(source_timestamps)
            canonical_comp = self._reconcile_competition(source_comps)

            # Update match evidence list with reconciled canonical details
            for ev_item in evidence_list:
                object.__setattr__(ev_item, "home_team", canonical_home)
                object.__setattr__(ev_item, "away_team", canonical_away)
                object.__setattr__(ev_item, "competition_name", canonical_comp.name if canonical_comp else None)
                object.__setattr__(ev_item, "start_time", canonical_start)
                ev_item.evidence["home_team"] = canonical_home
                ev_item.evidence["away_team"] = canonical_away
                ev_item.evidence["competition_name"] = canonical_comp.name if canonical_comp else None
                ev_item.evidence["start_time"] = canonical_start

            # Generate Deterministic Canonical ID (v2: 15-min bucket; dateless
            # branch disambiguated by reconciled competition to avoid silent
            # cross-competition collapse).
            norm_h, _ = normalize_team_name(canonical_home)
            norm_a, _ = normalize_team_name(canonical_away)
            canonical_id = generate_deterministic_canonical_event_id(
                sport=sport,
                home_team_norm=norm_h,
                away_team_norm=norm_a,
                scheduled_start_utc=canonical_start,
                competition_norm=canonical_comp.name if canonical_comp else None,
            )

            canonical_ev = CanonicalEvent(
                canonical_event_id=canonical_id,
                sport=sport,
                home_team=canonical_home,
                away_team=canonical_away,
                scheduled_start=canonical_start,
                competition=canonical_comp,
                sources=sources,
                match_evidence=evidence_list,
                status=ref_ev.status,
                metadata={},
            )

            canonical_events.append(canonical_ev)
            for node_id in comp_nodes:
                aggregated_event_ids.add(node_id)

        # 6. Partition Ambiguous & Unmatched Events
        unmatched_events: List[Event] = []
        ambiguous_events: List[Event] = []

        for e_id, ev in event_map.items():
            if e_id in aggregated_event_ids or e_id in conflicted_event_ids:
                continue
            if e_id in ambiguous_event_ids:
                ambiguous_events.append(ev)
            else:
                unmatched_events.append(ev)

        # Deterministic sorting of all result lists
        canonical_events.sort(key=lambda ce: ce.canonical_event_id)
        unmatched_events.sort(key=lambda ue: ue.internal_id)
        ambiguous_events.sort(key=lambda ae: ae.internal_id)
        conflicts.sort(key=lambda cf: cf.event_id)

        total_sources = sum(len(ce.sources) for ce in canonical_events)

        return CanonicalAggregationResult(
            canonical_events=canonical_events,
            unmatched_events=unmatched_events,
            ambiguous_events=ambiguous_events,
            conflicts=conflicts,
            total_canonical_events=len(canonical_events),
            total_sources_aggregated=total_sources,
            warnings=warnings,
        )
