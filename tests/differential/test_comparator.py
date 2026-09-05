"""
Unit Tests for DifferentialComparator (Phase 1 / Level 1)
"""

import pytest
from differential.comparator import DifferentialComparator
from differential.models import (
    DiffCategory,
    PipelineSnapshot,
    StageSnapshot,
)


def _make_dummy_snapshot(stage_name: str, records: dict, funnel: dict = None) -> PipelineSnapshot:
    stage = StageSnapshot(stage_name=stage_name, record_count=len(records), records=records)
    return PipelineSnapshot(
        dataset_id="test_ds",
        dataset_version="1.0",
        stages={stage_name: stage},
        cardinality_funnel=funnel or {},
        checksum="dummy_checksum",
    )


def test_comparator_identical_snapshots():
    comparator = DifferentialComparator()
    base = _make_dummy_snapshot("parsing", {"superbet:101": {"home": "A", "away": "B"}}, {"parsed_events": 1})
    cand = _make_dummy_snapshot("parsing", {"superbet:101": {"home": "A", "away": "B"}}, {"parsed_events": 1})

    report = comparator.compare_snapshots(base, cand)

    assert report.is_identical is True
    assert report.first_divergence is None
    assert len(report.object_diffs) == 0
    assert report.stage_diff_counts["parsing"]["UNCHANGED"] == 1


def test_comparator_added_and_removed_objects():
    comparator = DifferentialComparator()
    base = _make_dummy_snapshot(
        "parsing",
        {"superbet:101": {"home": "A", "away": "B"}},
        {"parsed_events": 1},
    )
    cand = _make_dummy_snapshot(
        "parsing",
        {"superbet:102": {"home": "C", "away": "D"}},
        {"parsed_events": 1},
    )

    report = comparator.compare_snapshots(base, cand)

    assert report.is_identical is False
    assert report.first_divergence is not None
    assert report.first_divergence.stage == "parsing"
    assert report.stage_diff_counts["parsing"]["REMOVED"] == 1
    assert report.stage_diff_counts["parsing"]["ADDED"] == 1


def test_comparator_changed_object_field_isolation():
    comparator = DifferentialComparator()
    base = _make_dummy_snapshot(
        "normalization",
        {"superbet:101": {"home_participant": "Arsenal", "away_participant": "Chelsea", "odds": 2.1}},
        {"normalized_graphs": 1},
    )
    cand = _make_dummy_snapshot(
        "normalization",
        {"superbet:101": {"home_participant": "Arsenal FC", "away_participant": "Chelsea", "odds": 2.1}},
        {"normalized_graphs": 1},
    )

    report = comparator.compare_snapshots(base, cand)

    assert report.is_identical is False
    assert report.first_divergence is not None
    assert report.first_divergence.stage == "normalization"
    assert report.first_divergence.field == "home_participant"
    assert report.first_divergence.baseline_value == "Arsenal"
    assert report.first_divergence.candidate_value == "Arsenal FC"


def test_comparator_funnel_deltas():
    comparator = DifferentialComparator()
    base_funnel = {"discovered_events": 100, "parsed_events": 90, "matched_events": 10}
    cand_funnel = {"discovered_events": 120, "parsed_events": 90, "matched_events": 8}

    base = _make_dummy_snapshot("final", {}, base_funnel)
    cand = _make_dummy_snapshot("final", {}, cand_funnel)

    report = comparator.compare_snapshots(base, cand)

    f_map = {f.stage_name: f for f in report.funnel_diffs}
    assert f_map["discovered_events"].delta == 20
    assert f_map["discovered_events"].percentage_delta == 20.0
    assert f_map["parsed_events"].delta == 0
    assert f_map["matched_events"].delta == -2
    assert f_map["matched_events"].percentage_delta == -20.0
