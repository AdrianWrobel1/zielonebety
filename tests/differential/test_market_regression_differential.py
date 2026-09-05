"""
Market Regression Differential Tests (Phase 1 / Section 13)
Tests that market family, line, metric, and scope regressions are detected and isolated.
"""

from differential.comparator import DifferentialComparator
from differential.models import PipelineSnapshot, StageSnapshot


def test_market_family_removal_and_addition():
    comparator = DifferentialComparator()

    base_stage = StageSnapshot("market_matching", 1, {
        "cev_1:TOTALS_2.5": {
            "canonical_event_id": "cev_1",
            "canonical_market_key": "TOTALS_2.5",
            "market_type": "TOTALS",
            "line": 2.5,
            "metric": "GOALS",
            "scope": "MATCH",
        }
    })

    cand_stage = StageSnapshot("market_matching", 1, {
        "cev_1:BTTS": {
            "canonical_event_id": "cev_1",
            "canonical_market_key": "BTTS",
            "market_type": "BTTS",
            "line": None,
            "metric": "GOALS",
            "scope": "MATCH",
        }
    })

    base = PipelineSnapshot(dataset_id="mkt_test", stages={"market_matching": base_stage})
    cand = PipelineSnapshot(dataset_id="mkt_test", stages={"market_matching": cand_stage})

    report = comparator.compare_snapshots(base, cand)

    assert len(report.market_family_diffs) == 2
    types = {m.diff_type for m in report.market_family_diffs}
    assert "MARKET_REMOVED" in types
    assert "MARKET_ADDED" in types

    removed = next(m for m in report.market_family_diffs if m.diff_type == "MARKET_REMOVED")
    assert removed.market_family == "TOTALS"
    assert removed.line == 2.5


def test_market_line_mutation_detection():
    comparator = DifferentialComparator()

    base_stage = StageSnapshot("market_matching", 1, {
        "cev_1:TOTALS": {
            "canonical_event_id": "cev_1",
            "canonical_market_key": "TOTALS",
            "market_type": "TOTALS",
            "line": 2.5,
            "metric": "GOALS",
            "scope": "MATCH",
        }
    })

    cand_stage = StageSnapshot("market_matching", 1, {
        "cev_1:TOTALS": {
            "canonical_event_id": "cev_1",
            "canonical_market_key": "TOTALS",
            "market_type": "TOTALS",
            "line": 3.5,  # Line shifted from 2.5 to 3.5
            "metric": "GOALS",
            "scope": "MATCH",
        }
    })

    base = PipelineSnapshot(dataset_id="mkt_test", stages={"market_matching": base_stage})
    cand = PipelineSnapshot(dataset_id="mkt_test", stages={"market_matching": cand_stage})

    report = comparator.compare_snapshots(base, cand)

    assert len(report.market_family_diffs) == 1
    assert report.market_family_diffs[0].diff_type == "LINE_CHANGED"
    assert report.market_family_diffs[0].baseline_value == 2.5
    assert report.market_family_diffs[0].candidate_value == 3.5
