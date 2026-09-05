"""
Real Replay Regression & Determinism Tests (Phase 1 / Level 3)
Verifies that executing Golden Datasets through the real baseline pipeline produces
100% byte-identical, deterministic semantic snapshots against stored baselines.
"""

import json
from pathlib import Path
import pytest

from differential.models import PipelineSnapshot
from differential.runner import DifferentialRunner


@pytest.fixture
def runner():
    return DifferentialRunner()


def test_multi_bookmaker_golden_replay_matches_frozen_baseline(runner):
    baseline_file = Path("golden_data/baseline_snapshots/baseline_v1_multi_bookmaker.json")
    assert baseline_file.exists(), f"Baseline snapshot file not found: {baseline_file}"

    baseline_data = json.loads(baseline_file.read_text(encoding="utf-8"))
    baseline_snap = PipelineSnapshot.from_dict(baseline_data)

    current_snap = runner.run_pipeline(dataset_id="multi_bookmaker_v1")
    report = runner.compare(baseline_snap, current_snap)

    assert report.is_identical is True, f"Divergence detected: {report.first_divergence}"
    assert report.first_divergence is None
    assert len(report.object_diffs) == 0
    assert current_snap.checksum == baseline_snap.checksum


def test_superbet_live_golden_replay_matches_frozen_baseline(runner):
    baseline_file = Path("golden_data/baseline_snapshots/baseline_v1_superbet_live.json")
    assert baseline_file.exists(), f"Baseline snapshot file not found: {baseline_file}"

    baseline_data = json.loads(baseline_file.read_text(encoding="utf-8"))
    baseline_snap = PipelineSnapshot.from_dict(baseline_data)

    current_snap = runner.run_pipeline(dataset_id="superbet_live_v1")
    report = runner.compare(baseline_snap, current_snap)

    assert report.is_identical is True, f"Divergence detected: {report.first_divergence}"
    assert current_snap.checksum == baseline_snap.checksum


def test_superbet_detail_golden_replay_matches_frozen_baseline(runner):
    baseline_file = Path("golden_data/baseline_snapshots/baseline_v1_superbet_detail.json")
    assert baseline_file.exists(), f"Baseline snapshot file not found: {baseline_file}"

    baseline_data = json.loads(baseline_file.read_text(encoding="utf-8"))
    baseline_snap = PipelineSnapshot.from_dict(baseline_data)

    current_snap = runner.run_pipeline(dataset_id="superbet_detail_v1")
    report = runner.compare(baseline_snap, current_snap)

    assert report.is_identical is True, f"Divergence detected: {report.first_divergence}"
    assert current_snap.checksum == baseline_snap.checksum


def test_betclic_live_golden_replay_matches_frozen_baseline(runner):
    baseline_file = Path("golden_data/baseline_snapshots/baseline_v1_betclic_live.json")
    assert baseline_file.exists(), f"Baseline snapshot file not found: {baseline_file}"

    baseline_data = json.loads(baseline_file.read_text(encoding="utf-8"))
    baseline_snap = PipelineSnapshot.from_dict(baseline_data)

    current_snap = runner.run_pipeline(dataset_id="betclic_live_v1")
    report = runner.compare(baseline_snap, current_snap)

    assert report.is_identical is True, f"Divergence detected: {report.first_divergence}"
    assert current_snap.checksum == baseline_snap.checksum


def test_repeated_replay_2x_determinism(runner):
    """Proves same input + same code = identical semantic output without false diffs."""
    snap1 = runner.run_pipeline(dataset_id="multi_bookmaker_v1")
    snap2 = runner.run_pipeline(dataset_id="multi_bookmaker_v1")

    report = runner.compare(snap1, snap2)

    assert report.is_identical is True
    assert report.is_semantic_regression is False
    assert snap1.checksum == snap2.checksum
    assert len(report.object_diffs) == 0
