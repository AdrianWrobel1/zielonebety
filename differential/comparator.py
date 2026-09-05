"""
Semantic Differential Comparator & First-Divergence Detection Engine
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Set, Tuple

from differential.models import (
    DetectionDiff,
    DiffCategory,
    DifferentialReport,
    EvaluationDiff,
    FirstDivergence,
    FunnelDiff,
    MarketFamilyDiff,
    ObjectDiff,
    PipelineSnapshot,
    PlayerIdentityDiff,
    StageSnapshot,
)

logger = logging.getLogger("zielonebety.differential.comparator")

STAGE_ORDER: Tuple[str, ...] = (
    "discovery",
    "selection",
    "acquisition",
    "parsing",
    "normalization",
    "event_matching",
    "market_matching",
    "evaluation",
    "detection",
    "final",
)


class DifferentialComparator:
    """Compares two PipelineSnapshots deterministically, isolating first divergence,

    cardinality shifts, market regressions, player identity changes, and evaluation transitions.
    """

    def __init__(self, float_tolerance: float = 1e-4) -> None:
        self.float_tolerance = float_tolerance

    def _values_are_equal(self, v1: Any, v2: Any) -> bool:
        if v1 is None and v2 is None:
            return True
        if v1 is None or v2 is None:
            return False
        if isinstance(v1, (int, float)) and isinstance(v2, (int, float)):
            return abs(float(v1) - float(v2)) <= self.float_tolerance
        if isinstance(v1, dict) and isinstance(v2, dict):
            if set(v1.keys()) != set(v2.keys()):
                return False
            return all(self._values_are_equal(v1[k], v2[k]) for k in v1)
        if isinstance(v1, list) and isinstance(v2, list):
            if len(v1) != len(v2):
                return False
            return all(self._values_are_equal(a, b) for a, b in zip(v1, v2))
        return v1 == v2

    def compare_snapshots(
        self,
        baseline: PipelineSnapshot,
        candidate: PipelineSnapshot,
    ) -> DifferentialReport:
        """Executes full differential comparison between Baseline and Candidate snapshots."""
        first_divergence: Optional[FirstDivergence] = None
        all_object_diffs: List[ObjectDiff] = []
        stage_diff_counts: Dict[str, Dict[str, int]] = {}
        unknown_diffs: List[ObjectDiff] = []

        # 1. Stage-by-Stage Chronological Comparison
        for stage_name in STAGE_ORDER:
            base_stage = baseline.stages.get(stage_name, StageSnapshot(stage_name=stage_name))
            cand_stage = candidate.stages.get(stage_name, StageSnapshot(stage_name=stage_name))

            stage_diffs, stage_counts = self._compare_stage(stage_name, base_stage, cand_stage)
            stage_diff_counts[stage_name] = stage_counts
            all_object_diffs.extend(stage_diffs)

            # Detect earliest first divergence
            if first_divergence is None and (stage_counts.get("ADDED", 0) > 0 or stage_counts.get("REMOVED", 0) > 0 or stage_counts.get("CHANGED", 0) > 0):
                first_obj_diff = next((d for d in stage_diffs if d.category in (DiffCategory.ADDED, DiffCategory.REMOVED, DiffCategory.CHANGED)), None)
                if first_obj_diff:
                    field_name = next(iter(first_obj_diff.differences.keys())) if first_obj_diff.differences else first_obj_diff.category.value
                    diff_detail = first_obj_diff.differences.get(field_name, {})
                    b_val = diff_detail.get("baseline") if isinstance(diff_detail, dict) else first_obj_diff.baseline_payload
                    c_val = diff_detail.get("candidate") if isinstance(diff_detail, dict) else first_obj_diff.candidate_payload

                    first_divergence = FirstDivergence(
                        stage=stage_name,
                        provider=first_obj_diff.provider,
                        event_id=first_obj_diff.object_key,
                        field=field_name,
                        baseline_value=b_val,
                        candidate_value=c_val,
                        description=f"First divergence detected in stage '{stage_name}' on object '{first_obj_diff.object_key}' [{first_obj_diff.category.value}]",
                    )

            # Check for unexplained removals
            for d in stage_diffs:
                if d.category == DiffCategory.REMOVED and d.explanation == "UNKNOWN":
                    unknown_diffs.append(d)

        # 2. Cardinality Funnel Deltas
        funnel_diffs = self._compute_funnel_diffs(baseline.cardinality_funnel, candidate.cardinality_funnel)

        # 3. Domain Specific Regressions (Player Identity, Markets, Evaluation, Detection)
        player_diffs = self._extract_player_identity_diffs(baseline, candidate)
        market_diffs = self._extract_market_family_diffs(baseline, candidate)
        eval_diffs = self._extract_evaluation_diffs(baseline, candidate)
        det_diffs = self._extract_detection_diffs(baseline, candidate)

        is_identical = (
            len(all_object_diffs) == 0
            and all(f.delta == 0 for f in funnel_diffs)
            and baseline.checksum == candidate.checksum
        )

        is_semantic_regression = (
            not is_identical
            and (
                any(f.delta != 0 for f in funnel_diffs if f.stage_name in ("matched_events", "evaluated_markets_total", "valid_surebets"))
                or len(eval_diffs) > 0
                or len(det_diffs) > 0
            )
        )

        return DifferentialReport(
            baseline_dataset_id=baseline.dataset_id,
            candidate_dataset_id=candidate.dataset_id,
            baseline_version=baseline.dataset_version,
            candidate_version=candidate.dataset_version,
            is_identical=is_identical,
            is_semantic_regression=is_semantic_regression,
            first_divergence=first_divergence,
            funnel_diffs=funnel_diffs,
            stage_diff_counts=stage_diff_counts,
            object_diffs=all_object_diffs,
            player_identity_diffs=player_diffs,
            market_family_diffs=market_diffs,
            evaluation_diffs=eval_diffs,
            detection_diffs=det_diffs,
            unknown_diffs=unknown_diffs,
            diagnostics={
                "baseline_checksum": baseline.checksum,
                "candidate_checksum": candidate.checksum,
                "total_differences_count": len(all_object_diffs),
            },
        )

    def _compare_stage(
        self,
        stage_name: str,
        base_stage: StageSnapshot,
        cand_stage: StageSnapshot,
    ) -> Tuple[List[ObjectDiff], Dict[str, int]]:
        stage_diffs: List[ObjectDiff] = []
        counts: Dict[str, int] = {c.value: 0 for c in DiffCategory}

        base_keys: Set[str] = set(base_stage.records.keys())
        cand_keys: Set[str] = set(cand_stage.records.keys())

        # Removed objects
        for k in sorted(base_keys - cand_keys):
            b_val = base_stage.records[k]
            provider = b_val.get("provider") if isinstance(b_val, dict) else (k.split(":")[0] if ":" in k else None)
            
            # Check if rejection lineage exists
            rej = base_stage.rejections.get(k) or cand_stage.rejections.get(k)
            explanation = rej.get("reason", "UNKNOWN") if isinstance(rej, dict) else "UNKNOWN"

            diff = ObjectDiff(
                category=DiffCategory.REMOVED,
                object_type=stage_name,
                object_key=k,
                provider=provider,
                stage=stage_name,
                baseline_payload=b_val,
                candidate_payload=None,
                explanation=explanation,
            )
            stage_diffs.append(diff)
            counts[DiffCategory.REMOVED.value] += 1

        # Added objects
        for k in sorted(cand_keys - base_keys):
            c_val = cand_stage.records[k]
            provider = c_val.get("provider") if isinstance(c_val, dict) else (k.split(":")[0] if ":" in k else None)

            diff = ObjectDiff(
                category=DiffCategory.ADDED,
                object_type=stage_name,
                object_key=k,
                provider=provider,
                stage=stage_name,
                baseline_payload=None,
                candidate_payload=c_val,
            )
            stage_diffs.append(diff)
            counts[DiffCategory.ADDED.value] += 1

        # Changed or Unchanged objects
        for k in sorted(base_keys & cand_keys):
            b_val = base_stage.records[k]
            c_val = cand_stage.records[k]
            provider = b_val.get("provider") if isinstance(b_val, dict) else (k.split(":")[0] if ":" in k else None)

            if self._values_are_equal(b_val, c_val):
                counts[DiffCategory.UNCHANGED.value] += 1
                continue

            # Compute detailed field differences
            field_diffs: Dict[str, Any] = {}
            if isinstance(b_val, dict) and isinstance(c_val, dict):
                all_fields = set(b_val.keys()) | set(c_val.keys())
                for f in sorted(all_fields):
                    fv1 = b_val.get(f)
                    fv2 = c_val.get(f)
                    if not self._values_are_equal(fv1, fv2):
                        field_diffs[f] = {"baseline": fv1, "candidate": fv2}
            else:
                field_diffs["value"] = {"baseline": b_val, "candidate": c_val}

            diff = ObjectDiff(
                category=DiffCategory.CHANGED,
                object_type=stage_name,
                object_key=k,
                provider=provider,
                stage=stage_name,
                baseline_payload=b_val,
                candidate_payload=c_val,
                differences=field_diffs,
            )
            stage_diffs.append(diff)
            counts[DiffCategory.CHANGED.value] += 1

        return stage_diffs, counts

    def _compute_funnel_diffs(
        self,
        base_funnel: Dict[str, int],
        cand_funnel: Dict[str, int],
    ) -> List[FunnelDiff]:
        all_metrics = list(dict.fromkeys(list(base_funnel.keys()) + list(cand_funnel.keys())))
        results: List[FunnelDiff] = []

        for m in all_metrics:
            b_cnt = base_funnel.get(m, 0)
            c_cnt = cand_funnel.get(m, 0)
            delta = c_cnt - b_cnt
            pct = round((delta / b_cnt) * 100.0, 2) if b_cnt > 0 else (0.0 if delta == 0 else 100.0)
            results.append(
                FunnelDiff(
                    stage_name=m,
                    baseline_count=b_cnt,
                    candidate_count=c_cnt,
                    delta=delta,
                    percentage_delta=pct,
                )
            )

        return results

    def _extract_player_identity_diffs(
        self,
        baseline: PipelineSnapshot,
        candidate: PipelineSnapshot,
    ) -> List[PlayerIdentityDiff]:
        diffs: List[PlayerIdentityDiff] = []
        base_norm = baseline.stages.get("normalization", StageSnapshot("normalization")).records
        cand_norm = candidate.stages.get("normalization", StageSnapshot("normalization")).records

        all_keys = set(base_norm.keys()) | set(cand_norm.keys())
        for k in sorted(all_keys):
            b_rec = base_norm.get(k, {})
            c_rec = cand_norm.get(k, {})

            b_mkts = b_rec.get("markets", [])
            c_mkts = c_rec.get("markets", [])

            # Extract player participants
            b_players = {
                s.get("participant")
                for m in b_mkts
                for s in m.get("selections", [])
                if s.get("participant")
            }
            c_players = {
                s.get("participant")
                for m in c_mkts
                for s in m.get("selections", [])
                if s.get("participant")
            }

            for p in sorted(b_players - c_players):
                diffs.append(
                    PlayerIdentityDiff(
                        event_id=k,
                        provider=b_rec.get("provider", ""),
                        player_id=None,
                        raw_name=p,
                        normalized_name=p,
                        canonical_player=p,
                        diff_type="PLAYER_REMOVED",
                        baseline_value=p,
                        candidate_value=None,
                    )
                )
            for p in sorted(c_players - b_players):
                diffs.append(
                    PlayerIdentityDiff(
                        event_id=k,
                        provider=c_rec.get("provider", ""),
                        player_id=None,
                        raw_name=p,
                        normalized_name=p,
                        canonical_player=p,
                        diff_type="PLAYER_ADDED",
                        baseline_value=None,
                        candidate_value=p,
                    )
                )

        return diffs

    def _extract_market_family_diffs(
        self,
        baseline: PipelineSnapshot,
        candidate: PipelineSnapshot,
    ) -> List[MarketFamilyDiff]:
        diffs: List[MarketFamilyDiff] = []
        base_mkts = baseline.stages.get("market_matching", StageSnapshot("market_matching")).records
        cand_mkts = candidate.stages.get("market_matching", StageSnapshot("market_matching")).records

        all_keys = set(base_mkts.keys()) | set(cand_mkts.keys())
        for k in sorted(all_keys):
            b_rec = base_mkts.get(k)
            c_rec = cand_mkts.get(k)

            if b_rec and not c_rec:
                diffs.append(
                    MarketFamilyDiff(
                        event_id=b_rec.get("canonical_event_id", ""),
                        market_family=b_rec.get("market_type", ""),
                        line=b_rec.get("line"),
                        metric=b_rec.get("metric", "GOALS"),
                        scope=b_rec.get("scope", "MATCH"),
                        diff_type="MARKET_REMOVED",
                        baseline_value=b_rec,
                        candidate_value=None,
                    )
                )
            elif c_rec and not b_rec:
                diffs.append(
                    MarketFamilyDiff(
                        event_id=c_rec.get("canonical_event_id", ""),
                        market_family=c_rec.get("market_type", ""),
                        line=c_rec.get("line"),
                        metric=c_rec.get("metric", "GOALS"),
                        scope=c_rec.get("scope", "MATCH"),
                        diff_type="MARKET_ADDED",
                        baseline_value=None,
                        candidate_value=c_rec,
                    )
                )
            elif b_rec and c_rec:
                if not self._values_are_equal(b_rec.get("line"), c_rec.get("line")):
                    diffs.append(
                        MarketFamilyDiff(
                            event_id=b_rec.get("canonical_event_id", ""),
                            market_family=b_rec.get("market_type", ""),
                            line=c_rec.get("line"),
                            metric=b_rec.get("metric", "GOALS"),
                            scope=b_rec.get("scope", "MATCH"),
                            diff_type="LINE_CHANGED",
                            baseline_value=b_rec.get("line"),
                            candidate_value=c_rec.get("line"),
                        )
                    )

        return diffs

    def _extract_evaluation_diffs(
        self,
        baseline: PipelineSnapshot,
        candidate: PipelineSnapshot,
    ) -> List[EvaluationDiff]:
        diffs: List[EvaluationDiff] = []
        base_evals = baseline.stages.get("evaluation", StageSnapshot("evaluation")).records
        cand_evals = candidate.stages.get("evaluation", StageSnapshot("evaluation")).records

        all_keys = set(base_evals.keys()) | set(cand_evals.keys())
        for k in sorted(all_keys):
            b_rec = base_evals.get(k)
            c_rec = cand_evals.get(k)

            b_state = b_rec.get("state") if b_rec else "MISSING"
            c_state = c_rec.get("state") if c_rec else "MISSING"
            b_reason = b_rec.get("reason") if b_rec else None
            c_reason = c_rec.get("reason") if c_rec else None

            if b_state != c_state or b_reason != c_reason:
                b_margin = b_rec.get("arbitrage_margin") if b_rec else None
                c_margin = c_rec.get("arbitrage_margin") if c_rec else None
                margin_delta = (c_margin - b_margin) if (b_margin is not None and c_margin is not None) else None

                ev_id = (b_rec or c_rec).get("canonical_event_id", "")
                mkt_key = (b_rec or c_rec).get("canonical_market_key", k)

                diffs.append(
                    EvaluationDiff(
                        event_id=ev_id,
                        canonical_market_key=mkt_key,
                        baseline_state=b_state,
                        candidate_state=c_state,
                        baseline_reason=b_reason,
                        candidate_reason=c_reason,
                        margin_delta=margin_delta,
                    )
                )

        return diffs

    def _extract_detection_diffs(
        self,
        baseline: PipelineSnapshot,
        candidate: PipelineSnapshot,
    ) -> List[DetectionDiff]:
        diffs: List[DetectionDiff] = []
        base_dets = baseline.stages.get("detection", StageSnapshot("detection")).records
        cand_dets = candidate.stages.get("detection", StageSnapshot("detection")).records

        all_keys = set(base_dets.keys()) | set(cand_dets.keys())
        for k in sorted(all_keys):
            b_rec = base_dets.get(k)
            c_rec = cand_dets.get(k)

            if b_rec and not c_rec:
                diffs.append(
                    DetectionDiff(
                        opportunity_id=k,
                        opportunity_type=b_rec.get("type", "SUREBET"),
                        diff_type="OPPORTUNITY_REMOVED",
                        baseline_margin=b_rec.get("arbitrage_margin"),
                        candidate_margin=None,
                        legs_diff={"baseline": b_rec.get("legs", []), "candidate": []},
                    )
                )
            elif c_rec and not b_rec:
                diffs.append(
                    DetectionDiff(
                        opportunity_id=k,
                        opportunity_type=c_rec.get("type", "SUREBET"),
                        diff_type="OPPORTUNITY_ADDED",
                        baseline_margin=None,
                        candidate_margin=c_rec.get("arbitrage_margin"),
                        legs_diff={"baseline": [], "candidate": c_rec.get("legs", [])},
                    )
                )
            elif b_rec and c_rec:
                b_m = b_rec.get("arbitrage_margin")
                c_m = c_rec.get("arbitrage_margin")
                if not self._values_are_equal(b_m, c_m) or not self._values_are_equal(b_rec.get("legs"), c_rec.get("legs")):
                    diffs.append(
                        DetectionDiff(
                            opportunity_id=k,
                            opportunity_type=b_rec.get("type", "SUREBET"),
                            diff_type="MARGIN_OR_LEGS_CHANGED",
                            baseline_margin=b_m,
                            candidate_margin=c_m,
                            legs_diff={"baseline": b_rec.get("legs", []), "candidate": c_rec.get("legs", [])},
                        )
                    )

        return diffs
