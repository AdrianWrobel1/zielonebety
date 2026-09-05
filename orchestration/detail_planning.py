"""
Coordinated Detail Selection Planning Module
Owns deterministic ranking, competition tiering, detail budget allocation, and provider ID pairing.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from normalization.matcher import MatchResult
from orchestration.event_selection import DefaultEventSelectionPolicy, DetailPrioritizationResult
from orchestration.models import ScanConfig

logger = logging.getLogger("zielonebety.orchestration.detail_planning")


@dataclass
class DetailAcquisitionPlan:
    """Explicit acquisition plan produced by CoordinatedDetailSelectionPlanner."""
    selected_event_ids_superbet: List[str] = field(default_factory=list)
    selected_event_ids_betclic: List[str] = field(default_factory=list)
    selected_event_ids_by_provider: Dict[str, List[str]] = field(default_factory=dict)
    overlap_event_ids_superbet: Set[str] = field(default_factory=set)
    overlap_event_ids_betclic: Set[str] = field(default_factory=set)
    ranked_pairs: List[Tuple[int, float, int, float, str, str, str]] = field(default_factory=list)
    prioritization_result_betclic: Optional[DetailPrioritizationResult] = None
    prioritization_result_superbet: Optional[DetailPrioritizationResult] = None
    diagnostics: Dict[str, Any] = field(default_factory=dict)
    resource_metrics_patch: Dict[str, Any] = field(default_factory=dict)


class CoordinatedDetailSelectionPlanner:
    """Owns coordinated detail acquisition planning, pair ranking, and budget enforcement.

    Pure planning component: does NOT perform network HTTP calls, evaluate bets, or detect surebets.
    """

    def __init__(
        self,
        event_selection_policy: Optional[DefaultEventSelectionPolicy] = None,
        normalization_engine: Optional[Any] = None,
        validation_pipeline: Optional[Any] = None,
    ) -> None:
        self.event_selection_policy = event_selection_policy or DefaultEventSelectionPolicy()
        self.normalization_engine = normalization_engine
        self.validation_pipeline = validation_pipeline

    def create_plan(
        self,
        sb_discovered: List[Any],
        bc_discovered: List[Any],
        sb_parser: Any,
        bc_parser: Any,
        config: ScanConfig,
        evaluation_time: Optional[datetime] = None,
    ) -> DetailAcquisitionPlan:
        """Executes coordinated multi-provider overview parsing, pre-matching, and 7-tuple pair ranking."""
        now_dt = evaluation_time or datetime.now(timezone.utc)
        overlap_sb_ids: Set[str] = set()
        overlap_bc_ids: Set[str] = set()
        matched_pair_items: List[Tuple[int, float, int, float, str, str, str]] = []

        # 1. Extract overview payloads from discovery items
        sb_overview_payloads: List[Dict[str, Any]] = []
        for it in sb_discovered:
            if isinstance(getattr(it, "metadata", None), dict) and "raw" in it.metadata:
                sb_overview_payloads.append(it.metadata["raw"])

        bc_overview_payloads: List[Dict[str, Any]] = []
        for it in bc_discovered:
            if isinstance(getattr(it, "metadata", None), dict) and "raw" in it.metadata:
                bc_overview_payloads.append(it.metadata["raw"])
            else:
                bc_overview_payloads.append({
                    "id": getattr(it, "provider_event_id", ""),
                    "name": getattr(it, "name", ""),
                    "competition": getattr(it, "competition_name", ""),
                    "start_date": getattr(it, "start_time", ""),
                    "markets": [],
                })

        # 2. Parse overview payloads (markets not needed for candidate generation & matching)
        if sb_parser:
            try:
                sb_overview_parsed = sb_parser.parse_payloads(sb_overview_payloads, include_markets=False)
            except TypeError:
                sb_overview_parsed = sb_parser.parse_payloads(sb_overview_payloads)
                for ev in sb_overview_parsed:
                    ev.markets = []
        else:
            sb_overview_parsed = []

        if bc_parser:
            try:
                bc_overview_parsed = bc_parser.parse_payloads(bc_overview_payloads, include_markets=False)
            except TypeError:
                bc_overview_parsed = bc_parser.parse_payloads(bc_overview_payloads)
                for ev in bc_overview_parsed:
                    ev.markets = []
        else:
            bc_overview_parsed = []

        # 3. Normalize overview graphs (markets not needed for candidate generation & matching)
        sb_graphs: List[Any] = []
        bc_graphs: List[Any] = []
        if self.normalization_engine:
            sb_overview_norm = self.normalization_engine.normalize("superbet", sb_overview_parsed, include_markets=False)
            bc_overview_norm = self.normalization_engine.normalize("betclic", bc_overview_parsed, include_markets=False)
            sb_graphs = sb_overview_norm.graphs
            bc_graphs = bc_overview_norm.graphs

        # 4. Generate candidate pairs & pre-match
        match_res = MatchResult(
            decisions=[],
            matched_count=0,
            ambiguous_count=0,
            rejected_count=0,
            total_scored=0,
        )
        src_map: Dict[str, Any] = {}
        tgt_map: Dict[str, Any] = {}
        comp_map: Dict[str, Any] = {}

        if sb_graphs and bc_graphs and self.validation_pipeline:
            cand_res = self.validation_pipeline.candidate_generator.generate_candidates(
                source_items=sb_graphs,
                target_items=bc_graphs,
            )
            src_map = {g.event.internal_id: g.event for g in sb_graphs}
            tgt_map = {g.event.internal_id: g.event for g in bc_graphs}
            comp_map = {
                g.event.competition_id: g.competition
                for g in list(sb_graphs) + list(bc_graphs)
                if g.competition
            }
            match_res = self.validation_pipeline.event_matcher.match_candidates(
                candidates=cand_res.candidates,
                source_events_map=src_map,
                target_events_map=tgt_map,
                comp_map=comp_map,
            )

        # 5. Extract matched pairs & calculate exact 7-tuple lexicographical sort key
        for d in match_res.decisions:
            if d.decision.value == "MATCHED":
                src_ev = src_map.get(d.source_event_id)
                tgt_ev = tgt_map.get(d.target_event_id)
                if src_ev and tgt_ev:
                    sb_eid = src_ev.provider_ids.get("superbet")
                    bc_eid = tgt_ev.provider_ids.get("betclic")
                    if sb_eid and bc_eid:
                        sb_eid_str = str(sb_eid).strip()
                        bc_eid_str = str(bc_eid).strip()
                        overlap_sb_ids.add(sb_eid_str)
                        overlap_bc_ids.add(bc_eid_str)

                        # Compute joint pair ranking key
                        comp_sb = comp_map.get(src_ev.competition_id)
                        comp_bc = comp_map.get(tgt_ev.competition_id)
                        tier_sb = self.event_selection_policy.calculate_competition_tier(
                            comp_sb.name if comp_sb else "", config.preferred_competitions
                        )
                        tier_bc = self.event_selection_policy.calculate_competition_tier(
                            comp_bc.name if comp_bc else "", config.preferred_competitions
                        )
                        pair_tier = min(tier_sb, tier_bc)

                        # Match confidence score
                        confidence = float(getattr(d, "total_score", 1.0) or 1.0)

                        # Market count / richness hint
                        mkt_count = len(getattr(src_ev, "markets", [])) + len(getattr(tgt_ev, "markets", []))

                        ko_sb = self.event_selection_policy._extract_item_kickoff(src_ev.scheduled_start)
                        ko_bc = self.event_selection_policy._extract_item_kickoff(tgt_ev.scheduled_start)
                        ko_ts = min(
                            ko_sb.timestamp() if ko_sb else 9999999999.0,
                            ko_bc.timestamp() if ko_bc else 9999999999.0,
                        )
                        pair_name = f"{src_ev.home_participant} vs {src_ev.away_participant}"

                        # 7-Tuple: (pair_tier, ko_ts, -confidence, -mkt_count, pair_name, sb_eid_str, bc_eid_str)
                        matched_pair_items.append((pair_tier, ko_ts, -confidence, -mkt_count, pair_name, sb_eid_str, bc_eid_str))

        # Deterministic sort for pairs
        matched_pair_items.sort(key=lambda x: (x[0], x[1], x[2], x[3], x[4], x[5], x[6]))
        ordered_paired_sb_ids = [p[5] for p in matched_pair_items]
        ordered_paired_bc_ids = [p[6] for p in matched_pair_items]

        # 6. Prioritize detail acquisition strictly favoring matched overlap
        max_reqs = config.effective_max_detail_requests
        scan_mode_str = str(getattr(config, "scan_mode", "NORMAL")).upper()

        prio_bc = self.event_selection_policy.prioritize_detail_events(
            discovered_items=bc_discovered,
            overlap_event_ids=overlap_bc_ids,
            max_detail_requests=max_reqs,
            preferred_competitions=config.preferred_competitions,
            hours_ahead=config.hours_ahead,
            current_time=now_dt,
            forced_ranked_ids=ordered_paired_bc_ids,
        )
        prio_sb = self.event_selection_policy.prioritize_detail_events(
            discovered_items=sb_discovered,
            overlap_event_ids=overlap_sb_ids,
            max_detail_requests=max_reqs,
            preferred_competitions=config.preferred_competitions,
            hours_ahead=config.hours_ahead,
            current_time=now_dt,
            forced_ranked_ids=ordered_paired_sb_ids,
        )

        sb_cnt = len(sb_discovered)
        bc_cnt = len(bc_discovered)
        raw_ovr = len(overlap_bc_ids)
        sel_ovr = prio_bc.events_overlap_selected
        ovr_rate = round(raw_ovr / min(sb_cnt, bc_cnt), 4) if min(sb_cnt, bc_cnt) > 0 else 0.0

        plan = DetailAcquisitionPlan(
            selected_event_ids_superbet=prio_sb.selected_event_ids,
            selected_event_ids_betclic=prio_bc.selected_event_ids,
            selected_event_ids_by_provider={
                "superbet": prio_sb.selected_event_ids,
                "betclic": prio_bc.selected_event_ids,
            },
            overlap_event_ids_superbet=overlap_sb_ids,
            overlap_event_ids_betclic=overlap_bc_ids,
            ranked_pairs=matched_pair_items,
            prioritization_result_betclic=prio_bc,
            prioritization_result_superbet=prio_sb,
            diagnostics={
                "scan_mode": scan_mode_str,
                "detail_budget": max_reqs,
                "matched_events_eligible": raw_ovr,
                "matched_events_selected": sel_ovr,
                "overview_only_matched_events": max(0, raw_ovr - sel_ovr),
                "full_detail_matched_events": sel_ovr,
                "candidates_available": prio_bc.candidates_available,
                "candidates_overlap": len(overlap_bc_ids),
                "events_selected": prio_bc.events_selected,
                "events_overlap_selected": prio_bc.events_overlap_selected,
                "overlap_selection_rate": prio_bc.overlap_selection_rate,
                "multi_market_expected_events": prio_bc.multi_market_expected_events,
                "selected_event_ids": prio_bc.selected_event_ids,
                "selected_event_ids_betclic": prio_bc.selected_event_ids,
                "selected_event_ids_superbet": prio_sb.selected_event_ids,
                "tier_0_available": prio_bc.tier_0_available,
                "tier_0_selected": prio_bc.tier_0_selected,
                "tier_1_available": prio_bc.tier_1_available,
                "tier_1_selected": prio_bc.tier_1_selected,
                "tier_2_available": prio_bc.tier_2_available,
                "tier_2_selected": prio_bc.tier_2_selected,
                "tier_samples": prio_bc.tier_samples,
            },
            resource_metrics_patch={
                "superbet_events_count": sb_cnt,
                "betclic_events_count": bc_cnt,
                "raw_overlap_count": raw_ovr,
                "selected_overlap_count": sel_ovr,
                "overlap_rate": ovr_rate,
                "cross_bookmaker_overlap_rate": ovr_rate,
                "provider_overlap_summary": {
                    "superbet_events": sb_cnt,
                    "betclic_events": bc_cnt,
                    "raw_overlap": raw_ovr,
                    "selected_overlap": sel_ovr,
                    "overlap_rate": f"{ovr_rate * 100:.1f}%",
                },
                "detail_candidates_available": prio_bc.candidates_available,
                "detail_candidates_overlap": len(overlap_bc_ids),
                "detail_events_selected": prio_bc.events_selected,
                "detail_events_overlap_selected": prio_bc.events_overlap_selected,
                "detail_overlap_selection_rate": prio_bc.overlap_selection_rate,
                "multi_market_expected_events": prio_bc.multi_market_expected_events,
                "matched_events_eligible_for_detail": raw_ovr,
                "matched_events_selected_for_detail": sel_ovr,
                "overview_only_matched_events": max(0, raw_ovr - sel_ovr),
                "full_detail_matched_events": sel_ovr,
                "detail_budget_allocated": max_reqs,
                "scan_mode": scan_mode_str,
            },
        )

        return plan
