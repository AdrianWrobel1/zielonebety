"""
Data Models for Differential Validation & Pipeline Snapshots
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union


class DiffCategory(str, Enum):
    """Authoritative classification for differential object comparison."""
    UNCHANGED = "UNCHANGED"
    ADDED = "ADDED"
    REMOVED = "REMOVED"
    CHANGED = "CHANGED"
    REORDERED_ONLY = "REORDERED_ONLY"
    NON_SEMANTIC = "NON_SEMANTIC"
    UNKNOWN = "UNKNOWN"


@dataclass
class StageSnapshot:
    """Deterministic, stably sorted semantic snapshot of a single pipeline stage."""
    stage_name: str
    record_count: int = 0
    records: Dict[str, Any] = field(default_factory=dict)
    rejections: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "stage_name": self.stage_name,
            "record_count": self.record_count,
            "records": self.records,
            "rejections": self.rejections,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> StageSnapshot:
        return cls(
            stage_name=data.get("stage_name", ""),
            record_count=data.get("record_count", 0),
            records=data.get("records", {}),
            rejections=data.get("rejections", {}),
            metadata=data.get("metadata", {}),
        )


@dataclass
class PipelineSnapshot:
    """Comprehensive, deterministic, frozen semantic snapshot across all pipeline stages."""
    snapshot_version: str = "1.0"
    dataset_id: str = ""
    dataset_version: str = "1.0"
    baseline_commit: Optional[str] = None
    created_at: str = ""
    checksum: str = ""
    cardinality_funnel: Dict[str, int] = field(default_factory=dict)
    stages: Dict[str, StageSnapshot] = field(default_factory=dict)
    execution_diagnostics: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "snapshot_version": self.snapshot_version,
            "dataset_id": self.dataset_id,
            "dataset_version": self.dataset_version,
            "baseline_commit": self.baseline_commit,
            "created_at": self.created_at,
            "checksum": self.checksum,
            "cardinality_funnel": self.cardinality_funnel,
            "stages": {k: v.to_dict() for k, v in self.stages.items()},
            "execution_diagnostics": self.execution_diagnostics,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> PipelineSnapshot:
        stages_data = data.get("stages", {})
        stages = {k: StageSnapshot.from_dict(v) for k, v in stages_data.items()}
        return cls(
            snapshot_version=data.get("snapshot_version", "1.0"),
            dataset_id=data.get("dataset_id", ""),
            dataset_version=data.get("dataset_version", "1.0"),
            baseline_commit=data.get("baseline_commit"),
            created_at=data.get("created_at", ""),
            checksum=data.get("checksum", ""),
            cardinality_funnel=data.get("cardinality_funnel", {}),
            stages=stages,
            execution_diagnostics=data.get("execution_diagnostics", {}),
        )


@dataclass
class FirstDivergence:
    """Earliest stage and location where semantic execution diverged from baseline."""
    stage: str
    provider: Optional[str] = None
    event_id: Optional[str] = None
    market_key: Optional[str] = None
    selection_key: Optional[str] = None
    field: Optional[str] = None
    baseline_value: Optional[Any] = None
    candidate_value: Optional[Any] = None
    description: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "stage": self.stage,
            "provider": self.provider,
            "event_id": self.event_id,
            "market_key": self.market_key,
            "selection_key": self.selection_key,
            "field": self.field,
            "baseline_value": self.baseline_value,
            "candidate_value": self.candidate_value,
            "description": self.description,
        }


@dataclass
class ObjectDiff:
    """Fine-grained semantic difference for an individual domain object."""
    category: DiffCategory
    object_type: str
    object_key: str
    provider: Optional[str] = None
    stage: str = ""
    baseline_payload: Optional[Any] = None
    candidate_payload: Optional[Any] = None
    differences: Dict[str, Any] = field(default_factory=dict)
    explanation: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "category": self.category.value,
            "object_type": self.object_type,
            "object_key": self.object_key,
            "provider": self.provider,
            "stage": self.stage,
            "baseline_payload": self.baseline_payload,
            "candidate_payload": self.candidate_payload,
            "differences": self.differences,
            "explanation": self.explanation,
        }


@dataclass
class FunnelDiff:
    """Cardinality comparison entry for a single funnel step."""
    stage_name: str
    baseline_count: int
    candidate_count: int
    delta: int
    percentage_delta: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "stage_name": self.stage_name,
            "baseline_count": self.baseline_count,
            "candidate_count": self.candidate_count,
            "delta": self.delta,
            "percentage_delta": self.percentage_delta,
        }


@dataclass
class PlayerIdentityDiff:
    """Detailed diff for player identity regressions across bookmakers."""
    event_id: str
    provider: str
    player_id: Optional[str]
    raw_name: Optional[str]
    normalized_name: Optional[str]
    canonical_player: Optional[str]
    diff_type: str  # "NAME_CHANGED", "ID_CHANGED", "MATCHING_LOST", etc.
    baseline_value: Any
    candidate_value: Any


@dataclass
class MarketFamilyDiff:
    """Detailed diff for market family and line mutations."""
    event_id: str
    market_family: str
    line: Optional[float]
    metric: str
    scope: str
    diff_type: str  # "MARKET_ADDED", "MARKET_REMOVED", "LINE_CHANGED", "SELECTION_MISMATCH"
    baseline_value: Any
    candidate_value: Any


@dataclass
class EvaluationDiff:
    """Detailed diff for transition states between Matched, Evaluated, and Opportunities."""
    event_id: str
    canonical_market_key: str
    baseline_state: str
    candidate_state: str
    baseline_reason: Optional[str] = None
    candidate_reason: Optional[str] = None
    margin_delta: Optional[float] = None


@dataclass
class DetectionDiff:
    """Detailed diff for arbitrage and valuebet opportunities."""
    opportunity_id: str
    opportunity_type: str  # "SUREBET" or "VALUEBET"
    diff_type: str  # "OPPORTUNITY_ADDED", "OPPORTUNITY_REMOVED", "MARGIN_CHANGED"
    baseline_margin: Optional[float]
    candidate_margin: Optional[float]
    legs_diff: Dict[str, Any] = field(default_factory=dict)


@dataclass
class DifferentialReport:
    """Authoritative complete report comparing Baseline vs Candidate pipeline execution."""
    baseline_dataset_id: str
    candidate_dataset_id: str
    baseline_version: str
    candidate_version: str
    is_identical: bool
    is_semantic_regression: bool
    first_divergence: Optional[FirstDivergence] = None
    funnel_diffs: List[FunnelDiff] = field(default_factory=list)
    stage_diff_counts: Dict[str, Dict[str, int]] = field(default_factory=dict)
    object_diffs: List[ObjectDiff] = field(default_factory=list)
    player_identity_diffs: List[PlayerIdentityDiff] = field(default_factory=list)
    market_family_diffs: List[MarketFamilyDiff] = field(default_factory=list)
    evaluation_diffs: List[EvaluationDiff] = field(default_factory=list)
    detection_diffs: List[DetectionDiff] = field(default_factory=list)
    unknown_diffs: List[ObjectDiff] = field(default_factory=list)
    diagnostics: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "baseline_dataset_id": self.baseline_dataset_id,
            "candidate_dataset_id": self.candidate_dataset_id,
            "baseline_version": self.baseline_version,
            "candidate_version": self.candidate_version,
            "is_identical": self.is_identical,
            "is_semantic_regression": self.is_semantic_regression,
            "first_divergence": self.first_divergence.to_dict() if self.first_divergence else None,
            "funnel_diffs": [f.to_dict() for f in self.funnel_diffs],
            "stage_diff_counts": self.stage_diff_counts,
            "object_diffs": [o.to_dict() for o in self.object_diffs],
            "player_identity_diffs": [
                {
                    "event_id": p.event_id,
                    "provider": p.provider,
                    "player_id": p.player_id,
                    "raw_name": p.raw_name,
                    "normalized_name": p.normalized_name,
                    "canonical_player": p.canonical_player,
                    "diff_type": p.diff_type,
                    "baseline_value": p.baseline_value,
                    "candidate_value": p.candidate_value,
                }
                for p in self.player_identity_diffs
            ],
            "market_family_diffs": [
                {
                    "event_id": m.event_id,
                    "market_family": m.market_family,
                    "line": m.line,
                    "metric": m.metric,
                    "scope": m.scope,
                    "diff_type": m.diff_type,
                    "baseline_value": m.baseline_value,
                    "candidate_value": m.candidate_value,
                }
                for m in self.market_family_diffs
            ],
            "evaluation_diffs": [
                {
                    "event_id": e.event_id,
                    "canonical_market_key": e.canonical_market_key,
                    "baseline_state": e.baseline_state,
                    "candidate_state": e.candidate_state,
                    "baseline_reason": e.baseline_reason,
                    "candidate_reason": e.candidate_reason,
                    "margin_delta": e.margin_delta,
                }
                for e in self.evaluation_diffs
            ],
            "detection_diffs": [
                {
                    "opportunity_id": d.opportunity_id,
                    "opportunity_type": d.opportunity_type,
                    "diff_type": d.diff_type,
                    "baseline_margin": d.baseline_margin,
                    "candidate_margin": d.candidate_margin,
                    "legs_diff": d.legs_diff,
                }
                for d in self.detection_diffs
            ],
            "unknown_diffs": [u.to_dict() for u in self.unknown_diffs],
            "diagnostics": self.diagnostics,
        }
