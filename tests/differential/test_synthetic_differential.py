"""
Synthetic Differential Tests (Phase 1 / Level 2)
Injects single-change mutations at every individual pipeline stage and verifies
that the DifferentialComparator identifies the EXACT earliest divergent stage and field.
"""

import copy
import pytest
from differential.comparator import DifferentialComparator
from differential.models import PipelineSnapshot, StageSnapshot

STAGE_NAMES = [
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
]


def _build_synthetic_clean_pipeline() -> PipelineSnapshot:
    """Builds a complete, consistent 10-stage synthetic pipeline snapshot."""
    stages = {
        "discovery": StageSnapshot("discovery", 2, {
            "superbet:sb_1": {"provider": "superbet", "event_name": "Arsenal vs Chelsea"},
            "betclic:bc_1": {"provider": "betclic", "event_name": "Arsenal - Chelsea"},
        }),
        "selection": StageSnapshot("selection", 2, {
            "superbet:sb_1": {"provider": "superbet", "selected_for_detail": True},
            "betclic:bc_1": {"provider": "betclic", "selected_for_detail": True},
        }),
        "acquisition": StageSnapshot("acquisition", 2, {
            "superbet": {"provider": "superbet", "status": "COMPLETED", "parsed_count": 1},
            "betclic": {"provider": "betclic", "status": "COMPLETED", "parsed_count": 1},
        }),
        "parsing": StageSnapshot("parsing", 2, {
            "superbet:sb_1": {"provider": "superbet", "home_team": "Arsenal", "away_team": "Chelsea", "market_count": 1},
            "betclic:bc_1": {"provider": "betclic", "home_team": "Arsenal", "away_team": "Chelsea", "market_count": 1},
        }),
        "normalization": StageSnapshot("normalization", 2, {
            "superbet:sb_1": {"home_participant": "Arsenal", "away_participant": "Chelsea", "markets": [{"market_type": "1X2"}]},
            "betclic:bc_1": {"home_participant": "Arsenal", "away_participant": "Chelsea", "markets": [{"market_type": "1X2"}]},
        }),
        "event_matching": StageSnapshot("event_matching", 1, {
            "cev_arsenal_chelsea": {"home_participant": "Arsenal", "away_participant": "Chelsea", "sources": {"superbet": "sb_1", "betclic": "bc_1"}},
        }),
        "market_matching": StageSnapshot("market_matching", 1, {
            "cev_arsenal_chelsea:1X2": {"canonical_market_key": "1X2", "market_type": "1X2", "line": None, "source_provider": "superbet", "target_provider": "betclic"},
        }),
        "evaluation": StageSnapshot("evaluation", 1, {
            "cev_arsenal_chelsea:1X2": {"state": "EVALUATED", "reason": None, "arbitrage_margin": -0.045},
        }),
        "detection": StageSnapshot("detection", 0, {}),
        "final": StageSnapshot("final", 5, {
            "discovered_events": 2,
            "parsed_events": 2,
            "normalized_graphs": 2,
            "matched_events": 1,
            "evaluated_markets_total": 1,
        }),
    }
    return PipelineSnapshot(
        dataset_id="synthetic_fixture",
        dataset_version="1.0",
        stages=stages,
        cardinality_funnel=stages["final"].records,
        checksum="synthetic_clean_hash",
    )


def test_first_divergence_caught_at_discovery():
    base = _build_synthetic_clean_pipeline()
    cand = copy.deepcopy(base)
    cand.stages["discovery"].records["superbet:sb_2"] = {"provider": "superbet", "event_name": "Liverpool vs Everton"}

    comparator = DifferentialComparator()
    report = comparator.compare_snapshots(base, cand)

    assert report.first_divergence is not None
    assert report.first_divergence.stage == "discovery"
    assert report.first_divergence.provider == "superbet"
    assert "superbet:sb_2" in report.first_divergence.event_id


def test_first_divergence_caught_at_parsing():
    base = _build_synthetic_clean_pipeline()
    cand = copy.deepcopy(base)
    cand.stages["parsing"].records["superbet:sb_1"]["home_team"] = "Arsenal FC"

    comparator = DifferentialComparator()
    report = comparator.compare_snapshots(base, cand)

    assert report.first_divergence is not None
    assert report.first_divergence.stage == "parsing"
    assert report.first_divergence.field == "home_team"
    assert report.first_divergence.baseline_value == "Arsenal"
    assert report.first_divergence.candidate_value == "Arsenal FC"


def test_first_divergence_caught_at_normalization():
    base = _build_synthetic_clean_pipeline()
    cand = copy.deepcopy(base)
    cand.stages["normalization"].records["superbet:sb_1"]["markets"] = [{"market_type": "TOTALS", "line": 2.5}]

    comparator = DifferentialComparator()
    report = comparator.compare_snapshots(base, cand)

    assert report.first_divergence is not None
    assert report.first_divergence.stage == "normalization"
    assert report.first_divergence.field == "markets"


def test_first_divergence_caught_at_event_matching():
    base = _build_synthetic_clean_pipeline()
    cand = copy.deepcopy(base)
    # Event match fails in candidate
    cand.stages["event_matching"].records.clear()
    cand.stages["event_matching"].rejections["sb_1:bc_1"] = {"rejection_reason": "LOW_MATCH_SCORE"}

    comparator = DifferentialComparator()
    report = comparator.compare_snapshots(base, cand)

    assert report.first_divergence is not None
    assert report.first_divergence.stage == "event_matching"


def test_first_divergence_caught_at_evaluation():
    base = _build_synthetic_clean_pipeline()
    cand = copy.deepcopy(base)
    # Evaluation state changes from EVALUATED to REJECTED due to INVALID_ODDS
    cand.stages["evaluation"].records["cev_arsenal_chelsea:1X2"]["state"] = "REJECTED"
    cand.stages["evaluation"].records["cev_arsenal_chelsea:1X2"]["reason"] = "INVALID_ODDS"

    comparator = DifferentialComparator()
    report = comparator.compare_snapshots(base, cand)

    assert report.first_divergence is not None
    assert report.first_divergence.stage == "evaluation"
    assert report.first_divergence.field in ("reason", "state")
    assert len(report.evaluation_diffs) == 1
    assert report.evaluation_diffs[0].candidate_reason == "INVALID_ODDS"


def test_first_divergence_caught_at_detection():
    base = _build_synthetic_clean_pipeline()
    cand = copy.deepcopy(base)
    cand.stages["detection"].records["surebet:cev_arsenal_chelsea:1X2"] = {
        "type": "SUREBET",
        "opportunity_id": "opp_1",
        "arbitrage_margin": 0.035,
        "legs": [{"selection_type": "HOME", "provider": "superbet", "odds": 2.1}],
    }

    comparator = DifferentialComparator()
    report = comparator.compare_snapshots(base, cand)

    assert report.first_divergence is not None
    assert report.first_divergence.stage == "detection"
    assert len(report.detection_diffs) == 1
    assert report.detection_diffs[0].diff_type == "OPPORTUNITY_ADDED"
    assert report.detection_diffs[0].candidate_margin == 0.035
