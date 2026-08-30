"""
Stage 5.3: Real Cross-Bookmaker Validation & Matching Pipeline Integration

Integrates the completed event, market, and selection matching layers into one
deterministic, provider-independent cross-bookmaker validation pipeline:

    Normalized Graphs / Events (Source + Target)
                        ↓
         Stage 4.3 Candidate Generation
                        ↓
            Stage 4.4 Event Matching
                        ↓
      Stage 4.5 Canonical Event Aggregation
                        ↓
            Stage 5.1 Market Matching
                        ↓
          Stage 5.2 Selection Matching
                        ↓
  Stage 5.3 CrossBookmakerValidationResult (Auditable Lineage)

Architecture Invariants:
- Pure Orchestration: Consumes stage decisions; never re-scores or rematches.
- Strict Gating:
    * Only MATCHED events proceed to canonical aggregation and market matching.
    * Only MATCHED markets proceed to selection matching.
    * Only MATCHED selections become ComparableSelectionPair records.
- Lineage Preservation: Every comparable selection retains full provenance
  (canonical IDs, provider IDs, external IDs, decomposed match evidence).
- Zero Odds Comparison: Preserves native odds snapshots without comparing,
  ranking, or computing EV/arbitrage (reserved for Stage 5.4 / 5.5).
- Deterministic Execution: Identical inputs produce byte-identical output keys.
- Auditable Telemetry: Stage-specific metrics and division-by-zero protection.
"""

import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from domain.models import (
    Competition,
    Event,
    EventSource,
    Market,
    MatchEvidence,
    Odds,
    Selection,
    CanonicalEvent,
)
from normalization.base_normalizer import NormalizedGraph
from normalization.candidate_generator import (
    EventCandidate,
    CandidateGenerationResult,
    EventCandidateGenerator,
)
from normalization.matcher import (
    EventMatcher,
    MatchDecision,
    MatchDecisionType,
    MatchResult,
    MatcherConfig,
    OrientationType,
)
from normalization.aggregator import (
    AggregationConflict,
    CanonicalAggregationResult,
    CanonicalEventAggregator,
)
from normalization.market_identity import (
    CanonicalMarketKey,
    CanonicalMarketType,
    extract_canonical_market_key,
)
from normalization.market_matcher import (
    MarketMatchBatchResult,
    MarketMatchDecision,
    MarketMatchDecisionType,
    MarketMatcher,
)
from normalization.selection_identity import (
    CanonicalSelectionKey,
    CanonicalSelectionType,
    extract_canonical_selection_key,
)
from normalization.selection_matcher import (
    SelectionMatchBatchResult,
    SelectionMatchDecision,
    SelectionMatchDecisionType,
    SelectionMatcher,
)


@dataclass(frozen=True)
class ComparableSelectionPair:
    """Immutable, auditable container representing two matched selections across bookmakers.

    Preserves complete multi-layer lineage from CanonicalSelection down to native provider IDs
    and decomposed match evidence across all matching stages.
    """
    canonical_event_id: str
    canonical_market_key: CanonicalMarketKey
    canonical_selection_key: CanonicalSelectionKey
    source_provider: str
    target_provider: str
    source_event_id: str
    target_event_id: str
    source_internal_event_id: str
    target_internal_event_id: str
    source_market_id: str
    target_market_id: str
    source_selection_id: str
    target_selection_id: str
    source_selection: Selection
    target_selection: Selection
    source_odds: Optional[Odds] = None
    target_odds: Optional[Odds] = None
    event_evidence: Optional[MatchEvidence] = None
    market_evidence: Dict[str, Any] = field(default_factory=dict)
    selection_evidence: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MatchedMarketLineage:
    """Market-level lineage container linking matched provider markets and selection results."""
    canonical_event_id: str
    canonical_market_key: CanonicalMarketKey
    source_market_id: str
    target_market_id: str
    source_market: Market
    target_market: Market
    market_decision: MarketMatchDecision
    selection_batch_result: SelectionMatchBatchResult
    comparable_selections: List[ComparableSelectionPair] = field(default_factory=list)


@dataclass
class CanonicalEventValidationRecord:
    """Event-level validation container containing canonical event and child market match results."""
    canonical_event: CanonicalEvent
    market_batch_result: MarketMatchBatchResult
    matched_markets: List[MatchedMarketLineage] = field(default_factory=list)
    unmatched_source_markets: List[Market] = field(default_factory=list)
    unmatched_target_markets: List[Market] = field(default_factory=list)
    unsupported_markets: List[MarketMatchDecision] = field(default_factory=list)
    ambiguous_markets: List[MarketMatchDecision] = field(default_factory=list)
    total_comparable_selections: int = 0


@dataclass
class PipelineMetrics:
    """Comprehensive performance and quality metrics for a cross-bookmaker validation run."""
    source_event_count: int = 0
    target_event_count: int = 0
    candidate_count: int = 0
    matched_event_count: int = 0
    ambiguous_event_count: int = 0
    rejected_event_count: int = 0
    canonical_event_count: int = 0
    conflict_count: int = 0

    source_market_count: int = 0
    target_market_count: int = 0
    matched_market_count: int = 0
    rejected_market_count: int = 0
    unsupported_market_count: int = 0
    ambiguous_market_count: int = 0

    source_selection_count: int = 0
    target_selection_count: int = 0
    matched_selection_count: int = 0
    rejected_selection_count: int = 0
    unsupported_selection_count: int = 0
    ambiguous_selection_count: int = 0

    pipeline_duration_ms: float = 0.0
    candidate_generation_ms: float = 0.0
    event_matching_ms: float = 0.0
    aggregation_ms: float = 0.0
    market_matching_ms: float = 0.0
    selection_matching_ms: float = 0.0
    rejection_reasons_breakdown: Dict[str, int] = field(default_factory=dict)
    provider_pair_coverage: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    @property
    def event_match_rate(self) -> Optional[float]:
        """Matched events / candidates (or None if 0 candidates)."""
        if self.candidate_count == 0:
            return None
        return round(self.matched_event_count / self.candidate_count, 4)

    @property
    def market_match_rate(self) -> Optional[float]:
        """Matched market pairs / total compared market pairs (or None if 0 compared)."""
        total_compared = self.matched_market_count + self.rejected_market_count + self.ambiguous_market_count
        if total_compared == 0:
            return None
        return round(self.matched_market_count / total_compared, 4)

    @property
    def selection_match_rate(self) -> Optional[float]:
        """Matched selection pairs / total compared selection pairs (or None if 0 compared)."""
        total_compared = self.matched_selection_count + self.rejected_selection_count + self.ambiguous_selection_count
        if total_compared == 0:
            return None
        return round(self.matched_selection_count / total_compared, 4)

    @property
    def supported_market_rate(self) -> Optional[float]:
        """Proportion of total provider markets that are canonically supported."""
        total_markets = self.source_market_count + self.target_market_count
        if total_markets == 0:
            return None
        supported = total_markets - self.unsupported_market_count
        return round(max(0.0, supported) / total_markets, 4)

    @property
    def supported_selection_rate(self) -> Optional[float]:
        """Proportion of total provider selections that are canonically supported."""
        total_selections = self.source_selection_count + self.target_selection_count
        if total_selections == 0:
            return None
        supported = total_selections - self.unsupported_selection_count
        return round(max(0.0, supported) / total_selections, 4)


@dataclass
class CrossBookmakerValidationResult:
    """The authoritative, auditable result of an end-to-end validation pipeline run."""
    source_events: List[Event] = field(default_factory=list)
    target_events: List[Event] = field(default_factory=list)
    source_graphs: List[NormalizedGraph] = field(default_factory=list)
    target_graphs: List[NormalizedGraph] = field(default_factory=list)
    event_candidates: List[EventCandidate] = field(default_factory=list)
    event_decisions: List[MatchDecision] = field(default_factory=list)
    canonical_events: List[CanonicalEvent] = field(default_factory=list)
    event_validation_records: List[CanonicalEventValidationRecord] = field(default_factory=list)
    market_match_results: Dict[str, MarketMatchBatchResult] = field(default_factory=dict)
    selection_match_results: Dict[str, Dict[str, SelectionMatchBatchResult]] = field(default_factory=dict)
    comparable_selections: List[ComparableSelectionPair] = field(default_factory=list)
    unmatched_events: List[Event] = field(default_factory=list)
    ambiguous_events: List[Event] = field(default_factory=list)
    rejected_events: List[Event] = field(default_factory=list)
    rejection_reasons_breakdown: Dict[str, int] = field(default_factory=dict)
    provider_pair_coverage: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    unsupported_markets: List[MarketMatchDecision] = field(default_factory=list)
    unsupported_selections: List[SelectionMatchDecision] = field(default_factory=list)
    conflicts: List[AggregationConflict] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    metrics: PipelineMetrics = field(default_factory=PipelineMetrics)

    def generate_audit_report(self, detailed: bool = False) -> str:
        """Generates a human-readable, auditable summary report of the validation run."""
        lines: List[str] = []
        lines.append("=" * 70)
        lines.append("CROSS-BOOKMAKER VALIDATION PIPELINE AUDIT REPORT")
        lines.append("=" * 70)
        lines.append(f"Source Events: {self.metrics.source_event_count} | Target Events: {self.metrics.target_event_count}")
        lines.append(f"Candidates Generated: {self.metrics.candidate_count}")
        lines.append(
            f"Event Decisions: MATCHED={self.metrics.matched_event_count}, "
            f"AMBIGUOUS={self.metrics.ambiguous_event_count}, REJECTED={self.metrics.rejected_event_count}"
        )
        lines.append(f"Canonical Events Formed: {self.metrics.canonical_event_count}")
        lines.append(f"Aggregation Conflicts: {self.metrics.conflict_count}")
        lines.append(
            f"Markets: Source={self.metrics.source_market_count}, Target={self.metrics.target_market_count} | "
            f"MATCHED={self.metrics.matched_market_count}, REJECTED={self.metrics.rejected_market_count}, "
            f"UNSUPPORTED={self.metrics.unsupported_market_count}, AMBIGUOUS={self.metrics.ambiguous_market_count}"
        )
        lines.append(
            f"Selections: Source={self.metrics.source_selection_count}, Target={self.metrics.target_selection_count} | "
            f"MATCHED={self.metrics.matched_selection_count}, REJECTED={self.metrics.rejected_selection_count}, "
            f"UNSUPPORTED={self.metrics.unsupported_selection_count}, AMBIGUOUS={self.metrics.ambiguous_selection_count}"
        )
        lines.append(f"Final Comparable Selection Pairs: {len(self.comparable_selections)}")
        lines.append(f"Total Pipeline Duration: {self.metrics.pipeline_duration_ms:.2f} ms")
        lines.append("-" * 70)

        # Coverage and Rates
        ev_rate_str = f"{self.metrics.event_match_rate * 100:.1f}%" if self.metrics.event_match_rate is not None else "N/A"
        mkt_rate_str = f"{self.metrics.market_match_rate * 100:.1f}%" if self.metrics.market_match_rate is not None else "N/A"
        sel_rate_str = f"{self.metrics.selection_match_rate * 100:.1f}%" if self.metrics.selection_match_rate is not None else "N/A"
        supp_mkt_str = f"{self.metrics.supported_market_rate * 100:.1f}%" if self.metrics.supported_market_rate is not None else "N/A"
        supp_sel_str = f"{self.metrics.supported_selection_rate * 100:.1f}%" if self.metrics.supported_selection_rate is not None else "N/A"

        lines.append(
            f"Rates: EventMatch={ev_rate_str}, MarketMatch={mkt_rate_str}, SelectionMatch={sel_rate_str} | "
            f"SupportedMarkets={supp_mkt_str}, SupportedSelections={supp_sel_str}"
        )

        if detailed and self.event_validation_records:
            lines.append("-" * 70)
            lines.append("DETAILED CANONICAL EVENT VALIDATION RECORDS:")
            for rec in self.event_validation_records:
                ce = rec.canonical_event
                lines.append(f"\n[CANONICAL EVENT: {ce.canonical_event_id}]")
                lines.append(f"  Participants: {ce.home_team} vs {ce.away_team} | Kickoff: {ce.scheduled_start}")
                lines.append(f"  Contributing Sources: {list(ce.sources.keys())}")
                for p_name, src in ce.sources.items():
                    lines.append(f"    - Provider '{p_name}': ProviderEventID={src.provider_event_id}, InternalID={src.internal_event_id}")

                lines.append(f"  Matched Markets: {len(rec.matched_markets)}")
                for m_lineage in rec.matched_markets:
                    m_key = m_lineage.canonical_market_key
                    lines.append(f"    * MARKET: {m_key.to_key_string()}")
                    lines.append(
                        f"      SourceMarketID={m_lineage.source_market_id} ↔ TargetMarketID={m_lineage.target_market_id}"
                    )
                    lines.append(f"      Matched Selections ({len(m_lineage.comparable_selections)}):")
                    for sel_pair in m_lineage.comparable_selections:
                        s_key = sel_pair.canonical_selection_key
                        lines.append(
                            f"        - SELECTION: {s_key.selection_type} "
                            f"(SourceSelID={sel_pair.source_selection_id} ↔ TargetSelID={sel_pair.target_selection_id})"
                        )

        lines.append("=" * 70)
        return "\n".join(lines)


class CrossBookmakerValidationPipeline:
    """Deterministic, provider-independent cross-bookmaker validation and matching pipeline.

    Orchestrates candidate generation, event matching, canonical aggregation,
    market matching, and selection matching without cross-stage recomputation.
    """

    def __init__(
        self,
        candidate_generator: Optional[EventCandidateGenerator] = None,
        event_matcher: Optional[EventMatcher] = None,
        aggregator: Optional[CanonicalEventAggregator] = None,
        market_matcher: Optional[MarketMatcher] = None,
        selection_matcher: Optional[SelectionMatcher] = None,
    ) -> None:
        self.candidate_generator = candidate_generator or EventCandidateGenerator()
        self.event_matcher = event_matcher or EventMatcher()
        self.aggregator = aggregator or CanonicalEventAggregator()
        self.market_matcher = market_matcher or MarketMatcher()
        self.selection_matcher = selection_matcher or SelectionMatcher()

    def _extract_event_and_comp(
        self,
        item: Union[Event, NormalizedGraph],
        comp_map: Optional[Dict[str, Competition]] = None,
    ) -> Tuple[Event, Optional[Competition], Optional[NormalizedGraph]]:
        """Extracts Event, Competition, and NormalizedGraph from input item."""
        if isinstance(item, NormalizedGraph):
            return item.event, item.competition, item
        elif isinstance(item, Event):
            comp = comp_map.get(item.competition_id) if comp_map else None
            return item, comp, None
        else:
            raise TypeError(f"Unsupported item type for validation pipeline: {type(item)}")

    def run_n_way(
        self,
        all_items: List[Union[Event, NormalizedGraph]],
        competition_map: Optional[Dict[str, Competition]] = None,
    ) -> CrossBookmakerValidationResult:
        """Executes the N-way cross-bookmaker validation pipeline across all provider graphs."""
        return self.run(source_items=all_items, target_items=None, competition_map=competition_map)

    def run(
        self,
        source_items: List[Union[Event, NormalizedGraph]],
        target_items: Optional[List[Union[Event, NormalizedGraph]]] = None,
        competition_map: Optional[Dict[str, Competition]] = None,
    ) -> CrossBookmakerValidationResult:
        """Executes the cross-bookmaker validation pipeline in bipartite (source vs target) or N-way mode.

        Args:
            source_items: NormalizedGraphs or Events from source provider (or all providers in N-way mode).
            target_items: Optional NormalizedGraphs or Events from target provider. If None, runs N-way matching.
            competition_map: Optional mapping of competition_id -> Competition.

        Returns:
            CrossBookmakerValidationResult containing all decisions, lineage records, and metrics.
        """
        pipeline_t0 = time.perf_counter()
        comp_map = dict(competition_map or {})
        warnings: List[str] = []
        errors: List[str] = []
        is_n_way = target_items is None

        # 1. Unpack and Index Items
        source_events_map: Dict[str, Event] = {}
        target_events_map: Dict[str, Event] = {}
        source_graph_map: Dict[str, NormalizedGraph] = {}
        target_graph_map: Dict[str, NormalizedGraph] = {}
        all_events_map: Dict[str, Event] = {}
        all_graph_map: Dict[str, NormalizedGraph] = {}

        source_events: List[Event] = []
        target_events: List[Event] = []
        source_graphs: List[NormalizedGraph] = []
        target_graphs: List[NormalizedGraph] = []

        for item in source_items:
            ev, comp, gr = self._extract_event_and_comp(item, comp_map)
            source_events.append(ev)
            source_events_map[ev.internal_id] = ev
            all_events_map[ev.internal_id] = ev
            if comp:
                comp_map[ev.competition_id] = comp
            if gr:
                source_graphs.append(gr)
                source_graph_map[ev.internal_id] = gr
                all_graph_map[ev.internal_id] = gr

        if not is_n_way and target_items:
            for item in target_items:
                ev, comp, gr = self._extract_event_and_comp(item, comp_map)
                target_events.append(ev)
                target_events_map[ev.internal_id] = ev
                all_events_map[ev.internal_id] = ev
                if comp:
                    comp_map[ev.competition_id] = comp
                if gr:
                    target_graphs.append(gr)
                    target_graph_map[ev.internal_id] = gr
                    all_graph_map[ev.internal_id] = gr

        # 2. Stage 4.3 Candidate Generation
        t_cand_0 = time.perf_counter()
        if is_n_way:
            cand_res = self.candidate_generator.generate_candidates_n_way(
                items=source_items,
                competition_map=comp_map,
            )
        else:
            cand_res = self.candidate_generator.generate_candidates(
                source_items=source_items,
                target_items=target_items or [],
                competition_map=comp_map,
            )
        t_cand_ms = (time.perf_counter() - t_cand_0) * 1000.0

        # 3. Stage 4.4 Event Matching
        t_ev_0 = time.perf_counter()
        match_res = self.event_matcher.match_candidates(
            candidates=cand_res.candidates,
            source_events_map=all_events_map,
            target_events_map=all_events_map,
            comp_map=comp_map,
        )
        t_ev_ms = (time.perf_counter() - t_ev_0) * 1000.0

        # 4. Stage 4.5 Canonical Event Aggregation
        t_agg_0 = time.perf_counter()
        all_items: List[Union[Event, NormalizedGraph]] = list(source_items) if is_n_way else (list(source_items) + list(target_items or []))
        agg_res = self.aggregator.aggregate(
            events=all_items,
            decisions=match_res.decisions,
            competition_map=comp_map,
        )
        t_agg_ms = (time.perf_counter() - t_agg_0) * 1000.0

        # 5. Partition Rejected Events (evaluated in candidates but rejected)
        evaluated_pairs: Set[Tuple[str, str]] = set()
        matched_event_ids: Set[str] = set()
        ambiguous_event_ids: Set[str] = set()
        rejected_event_ids: Set[str] = set()

        for d in match_res.decisions:
            evaluated_pairs.add((d.source_event_id, d.target_event_id))
            if d.decision == MatchDecisionType.MATCHED:
                matched_event_ids.add(d.source_event_id)
                matched_event_ids.add(d.target_event_id)
            elif d.decision == MatchDecisionType.AMBIGUOUS:
                ambiguous_event_ids.add(d.source_event_id)
                ambiguous_event_ids.add(d.target_event_id)
            elif d.decision == MatchDecisionType.REJECTED:
                rejected_event_ids.add(d.source_event_id)
                rejected_event_ids.add(d.target_event_id)

        # Rejected events are those involved in rejected decisions and not matched/ambiguous in any other decision
        final_rejected_ids = rejected_event_ids - matched_event_ids - ambiguous_event_ids
        rejected_events: List[Event] = []
        for e_id in final_rejected_ids:
            if e_id in all_events_map:
                rejected_events.append(all_events_map[e_id])
        rejected_events.sort(key=lambda e: e.internal_id)

        # Compute granular provider-pair coverage telemetry
        provider_pair_coverage: Dict[str, Dict[str, Any]] = {}
        for d in match_res.decisions:
            s_ev = all_events_map.get(d.source_event_id)
            t_ev = all_events_map.get(d.target_event_id)
            pA = self.candidate_generator._get_provider_name(s_ev) if s_ev else "unknown"
            pB = self.candidate_generator._get_provider_name(t_ev) if t_ev else "unknown"
            pair_key = f"{min(pA, pB)}:{max(pA, pB)}"
            if pair_key not in provider_pair_coverage:
                provider_pair_coverage[pair_key] = {
                    "status": "EVALUATED",
                    "candidate_count": 0,
                    "matched_count": 0,
                    "ambiguous_count": 0,
                    "team_identity_mismatch_count": 0,
                    "suffix_veto_count": 0,
                    "low_match_score_count": 0,
                }
            cov = provider_pair_coverage[pair_key]
            cov["candidate_count"] += 1
            if d.decision == MatchDecisionType.MATCHED:
                cov["matched_count"] += 1
            elif d.decision == MatchDecisionType.AMBIGUOUS:
                cov["ambiguous_count"] += 1
            else:
                if d.rejection_reason_code == "TEAM_IDENTITY_MISMATCH":
                    cov["team_identity_mismatch_count"] += 1
                elif d.rejection_reason_code == "SUFFIX_VETO":
                    cov["suffix_veto_count"] += 1
                elif d.rejection_reason_code == "LOW_MATCH_SCORE":
                    cov["low_match_score_count"] += 1

        # 6. Stage 5.1 Market Matching & Stage 5.2 Selection Matching per Canonical Event
        t_mkt_0 = time.perf_counter()
        total_market_match_ms = 0.0
        total_selection_match_ms = 0.0

        event_validation_records: List[CanonicalEventValidationRecord] = []
        market_match_results: Dict[str, MarketMatchBatchResult] = {}
        selection_match_results: Dict[str, Dict[str, SelectionMatchBatchResult]] = {}
        comparable_selections: List[ComparableSelectionPair] = []

        unsupported_markets_all: List[MarketMatchDecision] = []
        unsupported_selections_all: List[SelectionMatchDecision] = []

        total_source_markets = sum(len(g.markets) for g in source_graphs)
        total_target_markets = sum(len(g.markets) for g in target_graphs)
        total_matched_markets = 0
        total_rejected_markets = 0
        total_unsupported_markets = 0
        total_ambiguous_markets = 0

        total_source_selections = sum(len(g.selections) for g in source_graphs)
        total_target_selections = sum(len(g.selections) for g in target_graphs)
        total_matched_selections = 0
        total_rejected_selections = 0
        total_unsupported_selections = 0
        total_ambiguous_selections = 0

        for ce in agg_res.canonical_events:
            # Check if canonical event has 2+ distinct participating provider sources
            source_events_in_ce: List[Tuple[str, EventSource, Optional[NormalizedGraph]]] = []
            for prov_name, src in ce.sources.items():
                gr = all_graph_map.get(src.internal_event_id)
                source_events_in_ce.append((prov_name, src, gr))

            # If fewer than 2 sources or no graphs, cannot perform cross-bookmaker market matching
            if len(source_events_in_ce) < 2:
                continue

            # Deterministic source vs target ordering for market matching across all provider pairs
            source_events_in_ce.sort(key=lambda x: x[0])

            matched_markets_lineage: List[MatchedMarketLineage] = []
            ce_selection_batch_results: Dict[str, SelectionMatchBatchResult] = {}
            ce_comparable_selections: List[ComparableSelectionPair] = []
            primary_mkt_batch_res: Optional[MarketMatchBatchResult] = None
            unmatched_sm: List[Market] = []
            unmatched_tm: List[Market] = []

            for pair_i in range(len(source_events_in_ce)):
                for pair_j in range(pair_i + 1, len(source_events_in_ce)):
                    prov_a, src_a, gr_a = source_events_in_ce[pair_i]
                    prov_b, src_b, gr_b = source_events_in_ce[pair_j]

                    if not gr_a or not gr_b:
                        continue

                    # Extract markets, selections, and odds maps for both graphs
                    mkt_a_list = gr_a.markets
                    mkt_b_list = gr_b.markets
                    ev_a = gr_a.event
                    ev_b = gr_b.event

                    sel_a_by_mkt: Dict[str, List[Selection]] = defaultdict(list)
                    for s in gr_a.selections:
                        sel_a_by_mkt[s.market_id].append(s)

                    sel_b_by_mkt: Dict[str, List[Selection]] = defaultdict(list)
                    for s in gr_b.selections:
                        sel_b_by_mkt[s.market_id].append(s)

                    odds_a_by_sel: Dict[str, Odds] = {o.selection_id: o for o in gr_a.odds_list}
                    odds_b_by_sel: Dict[str, Odds] = {o.selection_id: o for o in gr_b.odds_list}

                    mkt_a_map: Dict[str, Market] = {m.internal_id: m for m in mkt_a_list}
                    mkt_b_map: Dict[str, Market] = {m.internal_id: m for m in mkt_b_list}

                    sel_a_map: Dict[str, Selection] = {s.internal_id: s for s in gr_a.selections}
                    sel_b_map: Dict[str, Selection] = {s.internal_id: s for s in gr_b.selections}

                    # 6a. Stage 5.1 Market Matching
                    mkt_match_t0 = time.perf_counter()
                    mkt_batch_res = self.market_matcher.match_markets(
                        source_markets=mkt_a_list,
                        target_markets=mkt_b_list,
                        default_sport=ce.sport,
                    )
                    total_market_match_ms += (time.perf_counter() - mkt_match_t0) * 1000.0

                    if primary_mkt_batch_res is None:
                        primary_mkt_batch_res = mkt_batch_res
                        market_match_results[ce.canonical_event_id] = mkt_batch_res

                    total_matched_markets += len(mkt_batch_res.matched_pairs)
                    total_rejected_markets += len(mkt_batch_res.rejected_pairs)
                    total_unsupported_markets += len(mkt_batch_res.unsupported_markets)
                    total_ambiguous_markets += len(mkt_batch_res.ambiguous_markets)
                    unsupported_markets_all.extend(mkt_batch_res.unsupported_markets)

                    # Find matching event evidence for lineage
                    primary_evidence: Optional[MatchEvidence] = None
                    if ce.match_evidence:
                        primary_evidence = ce.match_evidence[0]

                    # 6b. Stage 5.2 Selection Matching per MATCHED Market Pair
                    for mkt_dec in mkt_batch_res.matched_pairs:
                        sm = mkt_a_map.get(mkt_dec.source_market_id)
                        tm = mkt_b_map.get(mkt_dec.target_market_id)
                        mkt_key = mkt_dec.canonical_market_key

                        if not sm or not tm or not mkt_key:
                            continue

                        s_selections = sel_a_by_mkt.get(sm.internal_id, [])
                        t_selections = sel_b_by_mkt.get(tm.internal_id, [])

                        sel_match_t0 = time.perf_counter()
                        sel_batch_res = self.selection_matcher.match_market_selections(
                            source_selections=s_selections,
                            target_selections=t_selections,
                            source_market_key=mkt_key,
                            target_market_key=mkt_key,
                            source_event=ev_a,
                            target_event=ev_b,
                        )
                        total_selection_match_ms += (time.perf_counter() - sel_match_t0) * 1000.0

                        mkt_key_str = mkt_key.to_key_string()
                        ce_selection_batch_results[mkt_key_str] = sel_batch_res

                        total_matched_selections += len(sel_batch_res.matched_pairs)
                        total_rejected_selections += len(sel_batch_res.rejected_pairs)
                        total_unsupported_selections += len(sel_batch_res.unsupported_selections)
                        total_ambiguous_selections += len(sel_batch_res.ambiguous_selections)
                        unsupported_selections_all.extend(sel_batch_res.unsupported_selections)

                        market_comparable_pairs: List[ComparableSelectionPair] = []

                        # Build ComparableSelectionPair records for MATCHED selections
                        for sel_dec in sel_batch_res.matched_pairs:
                            ss = sel_a_map.get(sel_dec.source_selection_id)
                            ts = sel_b_map.get(sel_dec.target_selection_id)
                            sel_key = sel_dec.canonical_selection_key

                            if not ss or not ts or not sel_key:
                                continue

                            # Retrieve provider IDs and odds without comparing odds values
                            s_p_ev_id = src_a.provider_event_id
                            t_p_ev_id = src_b.provider_event_id
                            s_p_mkt_id = sm.provider_ids.get(prov_a) or sm.metadata.get("provider_market_id") or sm.internal_id
                            t_p_mkt_id = tm.provider_ids.get(prov_b) or tm.metadata.get("provider_market_id") or tm.internal_id
                            s_p_sel_id = ss.provider_ids.get(prov_a) or ss.metadata.get("provider_selection_id") or ss.internal_id
                            t_p_sel_id = ts.provider_ids.get(prov_b) or ts.metadata.get("provider_selection_id") or ts.internal_id

                            s_odds = odds_a_by_sel.get(ss.internal_id)
                            t_odds = odds_b_by_sel.get(ts.internal_id)

                            pair = ComparableSelectionPair(
                                canonical_event_id=ce.canonical_event_id,
                                canonical_market_key=mkt_key,
                                canonical_selection_key=sel_key,
                                source_provider=prov_a,
                                target_provider=prov_b,
                                source_event_id=str(s_p_ev_id),
                                target_event_id=str(t_p_ev_id),
                                source_internal_event_id=ev_a.internal_id,
                                target_internal_event_id=ev_b.internal_id,
                                source_market_id=str(s_p_mkt_id),
                                target_market_id=str(t_p_mkt_id),
                                source_selection_id=str(s_p_sel_id),
                                target_selection_id=str(t_p_sel_id),
                                source_selection=ss,
                                target_selection=ts,
                                source_odds=s_odds,
                                target_odds=t_odds,
                                event_evidence=primary_evidence,
                                market_evidence=dict(mkt_dec.evidence),
                                selection_evidence=dict(sel_dec.evidence),
                            )

                            market_comparable_pairs.append(pair)
                            ce_comparable_selections.append(pair)
                            comparable_selections.append(pair)

                        matched_markets_lineage.append(
                            MatchedMarketLineage(
                                canonical_event_id=ce.canonical_event_id,
                                canonical_market_key=mkt_key,
                                source_market_id=sm.internal_id,
                                target_market_id=tm.internal_id,
                                source_market=sm,
                                target_market=tm,
                                market_decision=mkt_dec,
                                selection_batch_result=sel_batch_res,
                                comparable_selections=market_comparable_pairs,
                            )
                        )

                    # Track unmatched markets for primary pair
                    if pair_i == 0 and pair_j == 1:
                        matched_sm_ids = {m.source_market_id for m in mkt_batch_res.matched_pairs}
                        matched_tm_ids = {m.target_market_id for m in mkt_batch_res.matched_pairs}
                        unmatched_sm = [m for m in mkt_a_list if m.internal_id not in matched_sm_ids]
                        unmatched_tm = [m for m in mkt_b_list if m.internal_id not in matched_tm_ids]

            selection_match_results[ce.canonical_event_id] = ce_selection_batch_results

            if primary_mkt_batch_res:
                val_record = CanonicalEventValidationRecord(
                    canonical_event=ce,
                    market_batch_result=primary_mkt_batch_res,
                    matched_markets=matched_markets_lineage,
                    unmatched_source_markets=unmatched_sm,
                    unmatched_target_markets=unmatched_tm,
                    unsupported_markets=list(primary_mkt_batch_res.unsupported_markets),
                    ambiguous_markets=list(primary_mkt_batch_res.ambiguous_markets),
                    total_comparable_selections=len(ce_comparable_selections),
                )
                event_validation_records.append(val_record)

        total_pipeline_ms = (time.perf_counter() - pipeline_t0) * 1000.0

        # Sort all result collections deterministically
        comparable_selections.sort(
            key=lambda p: (
                p.canonical_event_id,
                p.canonical_market_key.to_key_string(),
                p.canonical_selection_key.to_key_string(),
            )
        )
        event_validation_records.sort(key=lambda r: r.canonical_event.canonical_event_id)

        # Assemble Telemetry Metrics
        metrics = PipelineMetrics(
            source_event_count=len(source_items),
            target_event_count=len(target_items) if target_items else 0,
            candidate_count=len(cand_res.candidates),
            matched_event_count=match_res.matched_count,
            ambiguous_event_count=match_res.ambiguous_count,
            rejected_event_count=match_res.rejected_count,
            canonical_event_count=len(agg_res.canonical_events),
            conflict_count=len(agg_res.conflicts),
            source_market_count=total_source_markets,
            target_market_count=total_target_markets,
            matched_market_count=total_matched_markets,
            rejected_market_count=total_rejected_markets,
            unsupported_market_count=total_unsupported_markets,
            ambiguous_market_count=total_ambiguous_markets,
            source_selection_count=total_source_selections,
            target_selection_count=total_target_selections,
            matched_selection_count=total_matched_selections,
            rejected_selection_count=total_rejected_selections,
            unsupported_selection_count=total_unsupported_selections,
            ambiguous_selection_count=total_ambiguous_selections,
            pipeline_duration_ms=round(total_pipeline_ms, 2),
            candidate_generation_ms=round(t_cand_ms, 2),
            event_matching_ms=round(t_ev_ms, 2),
            aggregation_ms=round(t_agg_ms, 2),
            market_matching_ms=round(total_market_match_ms, 2),
            selection_matching_ms=round(total_selection_match_ms, 2),
            rejection_reasons_breakdown=match_res.rejection_reasons_breakdown,
            provider_pair_coverage=provider_pair_coverage,
        )

        return CrossBookmakerValidationResult(
            source_events=source_events,
            target_events=target_events,
            source_graphs=source_graphs,
            target_graphs=target_graphs,
            event_candidates=cand_res.candidates,
            event_decisions=match_res.decisions,
            canonical_events=agg_res.canonical_events,
            event_validation_records=event_validation_records,
            market_match_results=market_match_results,
            selection_match_results=selection_match_results,
            comparable_selections=comparable_selections,
            unmatched_events=agg_res.unmatched_events,
            ambiguous_events=agg_res.ambiguous_events,
            rejected_events=rejected_events,
            rejection_reasons_breakdown=match_res.rejection_reasons_breakdown,
            provider_pair_coverage=provider_pair_coverage,
            unsupported_markets=unsupported_markets_all,
            unsupported_selections=unsupported_selections_all,
            conflicts=agg_res.conflicts,
            errors=errors,
            metrics=metrics,
        )
