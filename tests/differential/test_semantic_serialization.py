"""
Unit Tests for Semantic Serialization & Deterministic Hashing (Phase 1 / Level 1)
"""

from decimal import Decimal
import pytest
from differential.models import PipelineSnapshot, StageSnapshot
from differential.serialization import (
    _round_float,
    _sort_dict_recursive,
    compute_deterministic_hash,
)


def test_round_float():
    assert _round_float(2.10000001, 4) == 2.1
    assert _round_float(Decimal("2.10555"), 4) == 2.1056
    assert _round_float(None) is None
    assert _round_float("invalid") is None


def test_sort_dict_recursive_reorders_keys_and_lists():
    d1 = {"z": 1, "a": [{"b": 2, "a": 1}], "m": {"y": 10, "x": 5}}
    d2 = {"a": [{"a": 1, "b": 2}], "m": {"x": 5, "y": 10}, "z": 1}

    sorted1 = _sort_dict_recursive(d1)
    sorted2 = _sort_dict_recursive(d2)

    assert sorted1 == sorted2
    assert list(sorted1.keys()) == ["a", "m", "z"]
    assert list(sorted1["m"].keys()) == ["x", "y"]


def test_deterministic_hash_is_stable_across_key_order():
    payload1 = {
        "event_id": "ev_101",
        "home": "Arsenal",
        "away": "Chelsea",
        "odds": [2.5, 3.2, 2.9],
        "meta": {"competition": "Premier League", "tier": 1},
    }
    payload2 = {
        "meta": {"tier": 1, "competition": "Premier League"},
        "away": "Chelsea",
        "home": "Arsenal",
        "odds": [2.5, 3.2, 2.9],
        "event_id": "ev_101",
    }

    hash1 = compute_deterministic_hash(payload1)
    hash2 = compute_deterministic_hash(payload2)

    assert hash1 == hash2
    assert len(hash1) == 64


def test_pipeline_snapshot_to_dict_and_from_dict_roundtrip():
    stage_disc = StageSnapshot(
        stage_name="discovery",
        record_count=1,
        records={"superbet:101": {"name": "Arsenal vs Chelsea"}},
        rejections={},
        metadata={"providers": ["superbet"]},
    )
    snapshot = PipelineSnapshot(
        snapshot_version="1.0",
        dataset_id="test_dataset",
        dataset_version="1.0",
        baseline_commit="commit_123",
        created_at="2026-08-17T10:00:00Z",
        checksum="abcd1234efgh5678",
        cardinality_funnel={"discovered_events": 1, "parsed_events": 1},
        stages={"discovery": stage_disc},
        execution_diagnostics={"status": "SUCCESS"},
    )

    data = snapshot.to_dict()
    reconstructed = PipelineSnapshot.from_dict(data)

    assert reconstructed.dataset_id == snapshot.dataset_id
    assert reconstructed.checksum == snapshot.checksum
    assert reconstructed.cardinality_funnel == snapshot.cardinality_funnel
    assert "discovery" in reconstructed.stages
    assert reconstructed.stages["discovery"].records == stage_disc.records
