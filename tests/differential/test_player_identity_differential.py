"""
Player Identity Differential Tests (Phase 1 / Section 12)
Tests that regressions in player name extraction, diacritics, and cross-bookmaker player matching
are surfaced explicitly in the DifferentialReport.
"""

from differential.comparator import DifferentialComparator
from differential.models import PipelineSnapshot, StageSnapshot


def test_player_identity_addition_and_removal_detection():
    comparator = DifferentialComparator()

    base_stage = StageSnapshot("normalization", 1, {
        "superbet:ev_1": {
            "provider": "superbet",
            "markets": [
                {
                    "market_type": "PLAYER_GOALS",
                    "selections": [
                        {"selection_type": "YES", "participant": "robert lewandowski"},
                        {"selection_type": "YES", "participant": "krzysztof piatek"},
                    ],
                }
            ],
        }
    })

    cand_stage = StageSnapshot("normalization", 1, {
        "superbet:ev_1": {
            "provider": "superbet",
            "markets": [
                {
                    "market_type": "PLAYER_GOALS",
                    "selections": [
                        {"selection_type": "YES", "participant": "robert lewandowski"},
                        # Piątek removed, Milik added
                        {"selection_type": "YES", "participant": "arkadiusz milik"},
                    ],
                }
            ],
        }
    })

    base = PipelineSnapshot(dataset_id="p_test", stages={"normalization": base_stage})
    cand = PipelineSnapshot(dataset_id="p_test", stages={"normalization": cand_stage})

    report = comparator.compare_snapshots(base, cand)

    assert len(report.player_identity_diffs) == 2
    types = {p.diff_type for p in report.player_identity_diffs}
    assert "PLAYER_REMOVED" in types
    assert "PLAYER_ADDED" in types

    removed = next(p for p in report.player_identity_diffs if p.diff_type == "PLAYER_REMOVED")
    assert removed.raw_name == "krzysztof piatek"

    added = next(p for p in report.player_identity_diffs if p.diff_type == "PLAYER_ADDED")
    assert added.raw_name == "arkadiusz milik"


def test_player_identity_diacritic_normalization_shift():
    comparator = DifferentialComparator()

    base_stage = StageSnapshot("normalization", 1, {
        "betclic:ev_2": {
            "provider": "betclic",
            "markets": [
                {
                    "market_type": "PLAYER_SHOTS",
                    "selections": [{"selection_type": "OVER", "participant": "erling haaland"}],
                }
            ],
        }
    })

    cand_stage = StageSnapshot("normalization", 1, {
        "betclic:ev_2": {
            "provider": "betclic",
            "markets": [
                {
                    "market_type": "PLAYER_SHOTS",
                    "selections": [{"selection_type": "OVER", "participant": "erling håland"}],
                }
            ],
        }
    })

    base = PipelineSnapshot(dataset_id="p_test", stages={"normalization": base_stage})
    cand = PipelineSnapshot(dataset_id="p_test", stages={"normalization": cand_stage})

    report = comparator.compare_snapshots(base, cand)

    assert len(report.player_identity_diffs) == 2
    assert any(p.raw_name == "erling haaland" and p.diff_type == "PLAYER_REMOVED" for p in report.player_identity_diffs)
    assert any(p.raw_name == "erling håland" and p.diff_type == "PLAYER_ADDED" for p in report.player_identity_diffs)
