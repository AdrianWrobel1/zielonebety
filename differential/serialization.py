"""
Deterministic Semantic Serialization for Pipeline Stages & Snapshots
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Dict, List, Optional, Tuple, Union
from decimal import Decimal

from differential.models import PipelineSnapshot, StageSnapshot
from orchestration.models import ScanCycleResult

logger = logging.getLogger("zielonebety.differential.serialization")


def _round_float(val: Any, decimals: int = 4) -> Optional[float]:
    if val is None:
        return None
    try:
        return round(float(val), decimals)
    except (ValueError, TypeError):
        return None


def _sort_dict_recursive(d: Any) -> Any:
    """Recursively sorts dictionary keys and nested lists to guarantee deterministic serialization."""
    if isinstance(d, dict):
        return {k: _sort_dict_recursive(v) for k, v in sorted(d.items(), key=lambda x: str(x[0]))}
    elif isinstance(d, list):
        # If list contains dicts with an 'id' or 'key' or 'name', sort deterministically
        try:
            return sorted([_sort_dict_recursive(x) for x in d], key=lambda x: json.dumps(x, sort_keys=True, default=str))
        except Exception:
            return [_sort_dict_recursive(x) for x in d]
    elif isinstance(d, (set, tuple)):
        return sorted([_sort_dict_recursive(x) for x in d], key=lambda x: str(x))
    elif isinstance(d, Decimal):
        return float(d)
    return d


def compute_deterministic_hash(payload: Any) -> str:
    """Generates SHA256 hex digest of a deterministically serialized Python dictionary/object."""
    sorted_payload = _sort_dict_recursive(payload)
    serialized = json.dumps(sorted_payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


class SemanticSnapshotSerializer:
    """Extracts immutable, sorted, purely semantic representations from scan cycle objects."""

    @classmethod
    def extract_discovery_snapshot(cls, scan_result: ScanCycleResult) -> StageSnapshot:
        records: Dict[str, Any] = {}
        for p_name, p_res in scan_result.provider_results.items():
            for item in getattr(p_res, "discovered_objects", []):
                p_id = (
                    getattr(item, "event_id", None)
                    or getattr(item, "provider_event_id", None)
                    or getattr(item, "match_id", None)
                    or (item.get("id") if isinstance(item, dict) else None)
                    or ""
                )
                name = (
                    getattr(item, "match_name", None)
                    or getattr(item, "name", None)
                    or (item.get("name") if isinstance(item, dict) else "")
                    or ""
                )
                comp = (
                    getattr(item, "competition_name", None)
                    or getattr(item, "tournament_name", None)
                    or (item.get("competition") if isinstance(item, dict) else "")
                    or ""
                )
                start = (
                    getattr(item, "start_time", None)
                    or getattr(item, "utc_date", None)
                    or (item.get("start_date") if isinstance(item, dict) else "")
                    or ""
                )
                sport = getattr(item, "sport", "football") or "football"
                item_key = f"{p_name}:{p_id}"
                records[item_key] = {
                    "provider": p_name,
                    "provider_event_id": str(p_id),
                    "event_name": str(name),
                    "competition_name": str(comp),
                    "start_time": str(start),
                    "sport": str(sport),
                }

        sorted_records = _sort_dict_recursive(records)
        return StageSnapshot(
            stage_name="discovery",
            record_count=len(sorted_records),
            records=sorted_records,
            metadata={"providers": sorted(list(scan_result.provider_results.keys()))},
        )

    @classmethod
    def extract_selection_snapshot(cls, scan_result: ScanCycleResult) -> StageSnapshot:
        records: Dict[str, Any] = {}
        prio_diag = scan_result.diagnostics.get("detail_prioritization", {})
        selected_sb = prio_diag.get("selected_event_ids_superbet", []) or []
        selected_bc = prio_diag.get("selected_event_ids_betclic", []) or []

        for eid in selected_sb:
            records[f"superbet:{eid}"] = {
                "provider": "superbet",
                "event_id": str(eid),
                "selected_for_detail": True,
            }
        for eid in selected_bc:
            records[f"betclic:{eid}"] = {
                "provider": "betclic",
                "event_id": str(eid),
                "selected_for_detail": True,
            }

        sorted_records = _sort_dict_recursive(records)
        return StageSnapshot(
            stage_name="selection",
            record_count=len(sorted_records),
            records=sorted_records,
            metadata={
                "scan_mode": prio_diag.get("scan_mode", scan_result.scan_mode),
                "detail_budget": prio_diag.get("detail_budget", scan_result.detail_budget_allocated),
                "matched_events_eligible": prio_diag.get("matched_events_eligible", 0),
                "matched_events_selected": prio_diag.get("matched_events_selected", 0),
            },
        )

    @classmethod
    def extract_acquisition_snapshot(cls, scan_result: ScanCycleResult) -> StageSnapshot:
        records: Dict[str, Any] = {}
        for p_name, p_res in scan_result.provider_results.items():
            records[p_name] = {
                "provider": p_name,
                "status": getattr(p_res.status, "value", str(p_res.status)),
                "discovered_count": len(getattr(p_res, "discovered_objects", [])),
                "parsed_count": len(getattr(p_res, "parsed_objects", [])),
                "errors": getattr(p_res, "errors", []),
                "warnings": getattr(p_res, "warnings", []),
            }
        sorted_records = _sort_dict_recursive(records)
        return StageSnapshot(
            stage_name="acquisition",
            record_count=len(sorted_records),
            records=sorted_records,
            metadata={
                "total_http_requests": scan_result.resource_metrics.total_http_requests,
                "detail_http_requests": scan_result.resource_metrics.detail_http_requests,
            },
        )

    @classmethod
    def extract_parsing_snapshot(cls, scan_result: ScanCycleResult) -> StageSnapshot:
        records: Dict[str, Any] = {}
        for p_name, p_res in scan_result.provider_results.items():
            for p_obj in getattr(p_res, "parsed_objects", []):
                eid = (
                    getattr(p_obj, "event_id", None)
                    or getattr(p_obj, "provider_event_id", None)
                    or getattr(p_obj, "matchId", None)
                    or getattr(p_obj, "internal_id", None)
                    or ""
                )
                home = getattr(p_obj, "home_team", "") or getattr(p_obj, "home_participant", "")
                away = getattr(p_obj, "away_team", "") or getattr(p_obj, "away_participant", "")
                mkts = getattr(p_obj, "markets", [])
                
                market_entries = []
                for m in mkts:
                    m_name = getattr(m, "name", "") or getattr(m, "market_type", "") or getattr(m, "market_type_code", "")
                    m_line = _round_float(getattr(m, "line", None))
                    sels = getattr(m, "selections", [])
                    sel_entries = []
                    for s in sels:
                        s_name = getattr(s, "name", "") or getattr(s, "selection_type", "")
                        odds_val = None
                        if hasattr(s, "odds"):
                            odds_val = getattr(s.odds, "decimal_odds", None) or getattr(s, "odds", None)
                        elif hasattr(s, "decimal_odds"):
                            odds_val = getattr(s, "decimal_odds", None)
                        elif hasattr(s, "price"):
                            odds_val = getattr(s, "price", None)
                        
                        sel_entries.append({
                            "selection_name": str(s_name),
                            "line": _round_float(getattr(s, "line", None)),
                            "odds": _round_float(odds_val),
                            "participant": getattr(s, "participant", None),
                        })
                    sel_entries.sort(key=lambda x: (str(x.get("selection_name")), str(x.get("line")), str(x.get("participant"))))

                    market_entries.append({
                        "market_name": str(m_name),
                        "line": m_line,
                        "selections_count": len(sel_entries),
                        "selections": sel_entries,
                    })

                market_entries.sort(key=lambda x: (str(x.get("market_name")), str(x.get("line"))))

                item_key = f"{p_name}:{eid}"
                records[item_key] = {
                    "provider": p_name,
                    "provider_event_id": str(eid),
                    "home_team": str(home),
                    "away_team": str(away),
                    "market_count": len(market_entries),
                    "markets": market_entries,
                }

        sorted_records = _sort_dict_recursive(records)
        return StageSnapshot(
            stage_name="parsing",
            record_count=len(sorted_records),
            records=sorted_records,
        )

    @classmethod
    def extract_normalization_snapshot(cls, scan_result: ScanCycleResult) -> StageSnapshot:
        records: Dict[str, Any] = {}
        for p_name, norm_res in scan_result.normalization_results.items():
            for graph in getattr(norm_res, "graphs", []):
                ev = graph.event
                comp = graph.competition.name if graph.competition else ""
                eid = ev.provider_ids.get(p_name) or ev.internal_id

                # Build lookup for odds
                odds_by_sel_id: Dict[str, float] = {}
                for o in graph.odds_list:
                    odds_by_sel_id[o.selection_id] = float(o.decimal_odds)

                # Group selections by market_id
                sels_by_mkt: Dict[str, List[Any]] = {}
                for s in graph.selections:
                    sels_by_mkt.setdefault(s.market_id, []).append(s)

                norm_markets = []
                for m in graph.markets:
                    m_type = str(m.market_type)
                    m_line = _round_float(m.line)
                    m_meta = m.metadata if isinstance(m.metadata, dict) else {}
                    m_metric = str(m_meta.get("metric", "GOALS") or "GOALS")
                    m_scope = str(m_meta.get("scope", "MATCH") or "MATCH")

                    m_sels = []
                    for s in sels_by_mkt.get(m.internal_id, []):
                        s_odds = _round_float(odds_by_sel_id.get(s.internal_id))
                        m_sels.append({
                            "selection_type": str(s.selection_type),
                            "line": _round_float(s.line),
                            "participant": str(s.participant) if s.participant else None,
                            "odds": s_odds,
                        })
                    m_sels.sort(key=lambda x: (str(x.get("selection_type")), str(x.get("participant")), str(x.get("line"))))

                    norm_markets.append({
                        "market_type": m_type,
                        "line": m_line,
                        "metric": m_metric,
                        "scope": m_scope,
                        "selections": m_sels,
                    })

                norm_markets.sort(key=lambda x: (str(x.get("market_type")), str(x.get("metric")), str(x.get("scope")), str(x.get("line"))))

                item_key = f"{p_name}:{eid}"
                records[item_key] = {
                    "provider": p_name,
                    "provider_event_id": str(eid),
                    "home_participant": str(ev.home_participant),
                    "away_participant": str(ev.away_participant),
                    "scheduled_start": str(ev.scheduled_start),
                    "competition": str(comp),
                    "market_count": len(norm_markets),
                    "markets": norm_markets,
                }

        sorted_records = _sort_dict_recursive(records)
        return StageSnapshot(
            stage_name="normalization",
            record_count=len(sorted_records),
            records=sorted_records,
            metadata={
                "normalized_graphs_count": scan_result.normalized_graphs_count,
                "normalization_failed_count": scan_result.normalization_failed_count,
                "markets_normalized_count": scan_result.markets_normalized_count,
            },
        )

    @classmethod
    def extract_event_matching_snapshot(cls, scan_result: ScanCycleResult) -> StageSnapshot:
        records: Dict[str, Any] = {}
        rejections: Dict[str, Any] = {}
        vr = scan_result.validation_result

        if vr:
            for ce in getattr(vr, "canonical_events", []):
                # Extract provider mapping
                sources_map = {}
                for p_name, ev_obj in ce.sources.items():
                    p_id = getattr(ev_obj, "provider_ids", {}).get(p_name) or getattr(ev_obj, "provider_event_id", None) or getattr(ev_obj, "event_id", None) or getattr(ev_obj, "internal_id", "")
                    sources_map[p_name] = str(p_id)

                home_name = getattr(ce, "home_team", None) or getattr(ce, "home_participant", "") or ""
                away_name = getattr(ce, "away_team", None) or getattr(ce, "away_participant", "") or ""
                records[ce.canonical_event_id] = {
                    "canonical_event_id": ce.canonical_event_id,
                    "home_participant": str(home_name),
                    "away_participant": str(away_name),
                    "scheduled_start": str(ce.scheduled_start),
                    "sources": sources_map,
                    "sport": str(getattr(ce, "sport", "football")),
                }

            # Rejected event decisions
            for d in getattr(vr, "event_decisions", []):
                if getattr(d.decision, "value", str(d.decision)) != "MATCHED":
                    src_name = d.evidence.get("source_name", str(d.source_event_id)) if hasattr(d, "evidence") and isinstance(d.evidence, dict) else str(d.source_event_id)
                    tgt_name = d.evidence.get("target_name", str(d.target_event_id)) if hasattr(d, "evidence") and isinstance(d.evidence, dict) else str(d.target_event_id)
                    pair_key = f"{src_name}:{tgt_name}"
                    rejections[pair_key] = {
                        "source_name": str(src_name),
                        "target_name": str(tgt_name),
                        "decision": getattr(d.decision, "value", str(d.decision)),
                        "score": _round_float(getattr(d, "total_score", 0.0)),
                        "rejection_reason": getattr(d, "rejection_reason_code", None) or "LOW_MATCH_SCORE",
                        "veto_reasons": list(getattr(d, "veto_reasons", [])),
                    }

        sorted_records = _sort_dict_recursive(records)
        sorted_rejections = _sort_dict_recursive(rejections)
        return StageSnapshot(
            stage_name="event_matching",
            record_count=len(sorted_records),
            records=sorted_records,
            rejections=sorted_rejections,
            metadata={
                "matched_events_count": scan_result.matched_events_count,
                "unmatched_events_count": scan_result.unmatched_events_count,
                "cross_bookmaker_overlap_rate": scan_result.cross_bookmaker_overlap_rate,
            },
        )

    @classmethod
    def extract_market_matching_snapshot(cls, scan_result: ScanCycleResult) -> StageSnapshot:
        records: Dict[str, Any] = {}
        rejections: Dict[str, Any] = {}
        vr = scan_result.validation_result

        if vr and getattr(vr, "event_validation_records", None):
            for ev_rec in vr.event_validation_records:
                cev_id = ev_rec.canonical_event.canonical_event_id
                for m_lineage in ev_rec.matched_markets:
                    mkt_key_str = m_lineage.canonical_market_key.to_key_string()
                    record_key = f"{cev_id}:{mkt_key_str}"

                    # Determine source & target provider names
                    src_prov_ids = getattr(m_lineage.source_market, "provider_ids", {}) or {}
                    tgt_prov_ids = getattr(m_lineage.target_market, "provider_ids", {}) or {}
                    src_p = list(src_prov_ids.keys())[0] if src_prov_ids else "superbet"
                    tgt_p = list(tgt_prov_ids.keys())[0] if tgt_prov_ids else "betclic"

                    comparable_sels = []
                    for pair in m_lineage.comparable_selections:
                        sel_k = pair.canonical_selection_key
                        part_val = getattr(sel_k, "participant_role", None) or getattr(getattr(sel_k, "market_key", None), "participant_id", None)
                        comparable_sels.append({
                            "selection_type": str(sel_k.selection_type),
                            "participant": str(part_val) if part_val else None,
                            "source_provider": pair.source_provider,
                            "source_odds": _round_float(pair.source_odds.decimal_odds if pair.source_odds else None),
                            "target_provider": pair.target_provider,
                            "target_odds": _round_float(pair.target_odds.decimal_odds if pair.target_odds else None),
                        })
                    comparable_sels.sort(key=lambda x: (str(x.get("selection_type")), str(x.get("participant"))))

                    src_prov_market_id = src_prov_ids.get(src_p) or getattr(m_lineage.source_market, "market_type", "")
                    tgt_prov_market_id = tgt_prov_ids.get(tgt_p) or getattr(m_lineage.target_market, "market_type", "")

                    records[record_key] = {
                        "canonical_event_id": cev_id,
                        "canonical_market_key": mkt_key_str,
                        "market_type": str(m_lineage.canonical_market_key.market_type),
                        "line": _round_float(m_lineage.canonical_market_key.line),
                        "metric": str(m_lineage.canonical_market_key.metric),
                        "scope": str(m_lineage.canonical_market_key.scope),
                        "source_provider": src_p,
                        "target_provider": tgt_p,
                        "source_provider_market_id": str(src_prov_market_id),
                        "target_provider_market_id": str(tgt_prov_market_id),
                        "comparable_selections_count": len(comparable_sels),
                        "comparable_selections": comparable_sels,
                    }

        sorted_records = _sort_dict_recursive(records)
        sorted_rejections = _sort_dict_recursive(rejections)
        return StageSnapshot(
            stage_name="market_matching",
            record_count=len(sorted_records),
            records=sorted_records,
            rejections=sorted_rejections,
            metadata={
                "markets_matched_count": scan_result.markets_matched_count,
                "evaluation_candidates_total": scan_result.evaluation_candidates_total,
            },
        )

    @classmethod
    def extract_evaluation_snapshot(cls, scan_result: ScanCycleResult) -> StageSnapshot:
        records: Dict[str, Any] = {}
        rejections: Dict[str, Any] = {}

        for rec in scan_result.market_evaluation_records:
            record_key = f"{rec.canonical_event_id}:{rec.canonical_market_key}"
            rec_entry = {
                "canonical_event_id": rec.canonical_event_id,
                "canonical_market_key": rec.canonical_market_key,
                "state": rec.state.value if hasattr(rec.state, "value") else str(rec.state),
                "reason": rec.reason.value if (rec.reason and hasattr(rec.reason, "value")) else (str(rec.reason) if rec.reason else None),
                "market_type": rec.market_type,
                "source_provider": rec.source_provider,
                "target_provider": rec.target_provider,
                "opportunity_id": rec.opportunity_id,
                "implied_probability_sum": _round_float(rec.details.get("implied_probability_sum"), 6),
                "arbitrage_margin": _round_float(rec.details.get("arbitrage_margin"), 6),
            }
            records[record_key] = rec_entry
            if rec_entry["reason"]:
                rejections[record_key] = {
                    "state": rec_entry["state"],
                    "reason": rec_entry["reason"],
                    "details": rec.details,
                }

        sorted_records = _sort_dict_recursive(records)
        sorted_rejections = _sort_dict_recursive(rejections)
        return StageSnapshot(
            stage_name="evaluation",
            record_count=len(sorted_records),
            records=sorted_records,
            rejections=sorted_rejections,
            metadata={
                "evaluated_markets_total": scan_result.evaluated_markets_total,
                "evaluation_candidates_total": scan_result.evaluation_candidates_total,
                "rejected_markets_count": scan_result.rejected_markets_count,
                "not_evaluated_markets_count": scan_result.not_evaluated_markets_count,
                "rejection_reasons_breakdown": scan_result.rejection_reasons_breakdown,
            },
        )

    @classmethod
    def extract_detection_snapshot(cls, scan_result: ScanCycleResult) -> StageSnapshot:
        records: Dict[str, Any] = {}

        # 1. Surebet opportunities
        if scan_result.detection_result and getattr(scan_result.detection_result, "opportunities", None):
            for opp in scan_result.detection_result.opportunities:
                cev_id = getattr(opp, "canonical_event_id", None) or (opp.canonical_event.canonical_event_id if hasattr(opp, "canonical_event") else "")
                mkt_key_str = opp.canonical_market_key.to_key_string()
                opp_key = f"surebet:{cev_id}:{mkt_key_str}"

                legs = []
                for leg in opp.legs:
                    legs.append({
                        "selection_type": str(leg.selection_type),
                        "provider": str(leg.provider),
                        "odds": _round_float(leg.odds),
                    })
                legs.sort(key=lambda x: (str(x.get("selection_type")), str(x.get("provider"))))

                records[opp_key] = {
                    "type": "SUREBET",
                    "opportunity_id": str(opp.opportunity_id),
                    "canonical_event_id": cev_id,
                    "canonical_market_key": mkt_key_str,
                    "arbitrage_margin": _round_float(opp.arbitrage_margin, 6),
                    "implied_probability_sum": _round_float(opp.implied_probability_sum, 6),
                    "legs": legs,
                }

        # 2. Valuebet opportunities
        if scan_result.valuebet_result and getattr(scan_result.valuebet_result, "candidates", None):
            for vb in scan_result.valuebet_result.candidates:
                ev_id = getattr(vb, "event_id", "") or getattr(vb, "canonical_event_id", "")
                m_type = getattr(vb, "market_type", "")
                line = _round_float(getattr(vb, "line", None))
                sel = getattr(vb, "selection_type", "")
                vb_key = f"valuebet:{ev_id}:{m_type}:{line}:{sel}"

                records[vb_key] = {
                    "type": "VALUEBET",
                    "event_id": str(ev_id),
                    "bookmaker": getattr(vb, "bookmaker", ""),
                    "market_type": str(m_type),
                    "line": line,
                    "selection_type": str(sel),
                    "bookmaker_odds": _round_float(getattr(vb, "bookmaker_odds", None)),
                    "fair_odds": _round_float(getattr(vb, "fair_odds", None)),
                    "edge_percentage": _round_float(getattr(vb, "edge_percentage", None), 4),
                }

        sorted_records = _sort_dict_recursive(records)
        return StageSnapshot(
            stage_name="detection",
            record_count=len(sorted_records),
            records=sorted_records,
            metadata={
                "detected_opportunities_count": scan_result.detected_opportunities_count,
                "valid_surebets_count": scan_result.valid_surebets_count,
                "valuebets_qualified_count": scan_result.valuebets_qualified_count,
            },
        )

    @classmethod
    def extract_final_snapshot(cls, scan_result: ScanCycleResult) -> StageSnapshot:
        funnel = {
            "discovered_events": scan_result.discovered_events_count,
            "popular_events_discovered": scan_result.popular_events_discovered_count,
            "popular_events_selected": scan_result.popular_events_selected_count,
            "parsed_events": scan_result.parsed_events_count,
            "normalized_graphs": scan_result.normalized_graphs_count,
            "normalization_failed": scan_result.normalization_failed_count,
            "markets_discovered": scan_result.markets_discovered_count,
            "markets_normalized": scan_result.markets_normalized_count,
            "markets_matched": scan_result.markets_matched_count,
            "matched_events": scan_result.matched_events_count,
            "unmatched_events": scan_result.unmatched_events_count,
            "evaluation_candidates_total": scan_result.evaluation_candidates_total,
            "evaluated_markets_total": scan_result.evaluated_markets_total,
            "rejected_markets_count": scan_result.rejected_markets_count,
            "not_evaluated_markets_count": scan_result.not_evaluated_markets_count,
            "detected_opportunities": scan_result.detected_opportunities_count,
            "valid_surebets": scan_result.valid_surebets_count,
            "valuebets_qualified": scan_result.valuebets_qualified_count,
        }
        sorted_funnel = _sort_dict_recursive(funnel)
        return StageSnapshot(
            stage_name="final",
            record_count=len(sorted_funnel),
            records=sorted_funnel,
            metadata={
                "cycle_status": scan_result.cycle_status.value if hasattr(scan_result.cycle_status, "value") else str(scan_result.cycle_status),
                "is_success": scan_result.is_success,
                "errors_count": len(scan_result.errors),
                "warnings_count": len(scan_result.warnings),
            },
        )

    @classmethod
    def build_pipeline_snapshot(
        cls,
        scan_result: ScanCycleResult,
        dataset_id: str,
        dataset_version: str = "1.0",
        baseline_commit: Optional[str] = "5c70a6c178e286897ab295deaa1abc2d0112fea0",
    ) -> PipelineSnapshot:
        """Assembles a complete, multi-stage semantic snapshot from a scan execution result."""
        stages: Dict[str, StageSnapshot] = {
            "discovery": cls.extract_discovery_snapshot(scan_result),
            "selection": cls.extract_selection_snapshot(scan_result),
            "acquisition": cls.extract_acquisition_snapshot(scan_result),
            "parsing": cls.extract_parsing_snapshot(scan_result),
            "normalization": cls.extract_normalization_snapshot(scan_result),
            "event_matching": cls.extract_event_matching_snapshot(scan_result),
            "market_matching": cls.extract_market_matching_snapshot(scan_result),
            "evaluation": cls.extract_evaluation_snapshot(scan_result),
            "detection": cls.extract_detection_snapshot(scan_result),
            "final": cls.extract_final_snapshot(scan_result),
        }

        funnel = stages["final"].records

        snapshot = PipelineSnapshot(
            snapshot_version="1.0",
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            baseline_commit=baseline_commit,
            created_at=scan_result.started_at,
            cardinality_funnel=funnel,
            stages=stages,
            execution_diagnostics={
                "cycle_status": scan_result.cycle_status.value if hasattr(scan_result.cycle_status, "value") else str(scan_result.cycle_status),
                "scan_mode": scan_result.scan_mode,
                "errors": scan_result.errors,
                "warnings": scan_result.warnings,
            },
        )
        semantic_dict = {
            "snapshot_version": snapshot.snapshot_version,
            "dataset_id": snapshot.dataset_id,
            "dataset_version": snapshot.dataset_version,
            "baseline_commit": snapshot.baseline_commit,
            "cardinality_funnel": snapshot.cardinality_funnel,
            "stages": {k: v.to_dict() for k, v in snapshot.stages.items()},
        }
        snapshot.checksum = compute_deterministic_hash(semantic_dict)
        return snapshot
