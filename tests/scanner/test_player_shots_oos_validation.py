"""
Targeted tests for Player Shots Over 0.5 Historical OOS Validation / Calibration (Stage A.10).

Scope strictly covers:
1. Brier Score & Log Loss on a hand-verified calculation.
2. Calibration binning on edge and boundary probability values.
3. OOS Filtering: excluding PENDING, UNKNOWN, VOID, missing probabilities, temporal violations.
"""

from datetime import datetime, timezone, timedelta
import math
import pytest

from database.models import PlayerPropSnapshotORM
from scanner.player_shots_oos_validator import (
    calculate_brier_score,
    calculate_log_loss,
    compute_calibration_table,
    filter_qualified_oos_records,
)


def test_brier_and_log_loss_manual_case():
    """1. Brier Score and Log Loss on a small, hand-verifiable worked example."""
    # Hand-verified data points:
    # p = [0.8, 0.6, 0.9, 0.4]
    # y = [1,   0,   1,   0]
    # Brier:
    #   (0.8 - 1)^2 = 0.04
    #   (0.6 - 0)^2 = 0.36
    #   (0.9 - 1)^2 = 0.01
    #   (0.4 - 0)^2 = 0.16
    # Mean Brier = 0.57 / 4 = 0.1425
    #
    # Log loss:
    #   - [ ln(0.8) + ln(1 - 0.6) + ln(0.9) + ln(1 - 0.4) ] / 4
    #   ln(0.8) ≈ -0.22314355
    #   ln(0.4) ≈ -0.91629073
    #   ln(0.9) ≈ -0.10536052
    #   ln(0.6) ≈ -0.51082562
    #   Sum = -1.75562042
    #   Mean Log Loss = 1.75562042 / 4 ≈ 0.438905
    probs = [0.8, 0.6, 0.9, 0.4]
    outcomes = [1, 0, 1, 0]

    brier = calculate_brier_score(probs, outcomes)
    assert pytest.approx(brier, abs=1e-4) == 0.1425

    logloss = calculate_log_loss(probs, outcomes)
    expected_logloss = -(
        math.log(0.8) + math.log(0.4) + math.log(0.9) + math.log(0.6)
    ) / 4.0
    assert pytest.approx(logloss, abs=1e-4) == expected_logloss

    # Numerical boundary protection (p=0.0 with y=1 or p=1.0 with y=0 doesn't raise ZeroDivision/ValueError)
    extreme_probs = [0.0, 1.0]
    extreme_outcomes = [1, 0]
    loss_extreme = calculate_log_loss(extreme_probs, extreme_outcomes, eps=1e-15)
    assert loss_extreme > 0.0
    assert not math.isnan(loss_extreme)
    assert not math.isinf(loss_extreme)


def test_calibration_binning_boundaries():
    """2. Calibration binning on hand-picked probabilities at bin boundaries."""
    # Standard bins: [0.50-0.60), [0.60-0.70), [0.70-0.80), [0.80-0.90), [0.90-1.00]
    probs = [0.50, 0.58, 0.60, 0.75, 0.85, 0.90, 1.00]
    outcomes = [1, 0, 1, 1, 0, 1, 1]

    bins = compute_calibration_table(probs, outcomes)

    # We expect 5 bins
    assert len(bins) == 5

    # Bin 0: [0.50, 0.60) -> 0.50, 0.58 (N=2, outcomes: 1, 0 -> empirical rate = 0.50)
    bin_0 = bins[0]
    assert bin_0.lower_bound == 0.50
    assert bin_0.upper_bound == 0.60
    assert bin_0.count == 2
    assert pytest.approx(bin_0.mean_predicted_prob, abs=1e-3) == 0.54
    assert pytest.approx(bin_0.empirical_hit_rate, abs=1e-3) == 0.50
    assert pytest.approx(bin_0.calibration_gap, abs=1e-3) == 0.04  # 0.54 - 0.50

    # Bin 1: [0.60, 0.70) -> 0.60 (N=1, outcome=1 -> rate=1.0)
    bin_1 = bins[1]
    assert bin_1.count == 1
    assert pytest.approx(bin_1.mean_predicted_prob, abs=1e-3) == 0.60
    assert bin_1.empirical_hit_rate == 1.0

    # Bin 4: [0.90, 1.00] -> 0.90, 1.00 (N=2, outcomes: 1, 1 -> rate=1.0)
    bin_4 = bins[4]
    assert bin_4.count == 2
    assert pytest.approx(bin_4.mean_predicted_prob, abs=1e-3) == 0.95
    assert bin_4.empirical_hit_rate == 1.0


def test_oos_filtering_excludes_invalid_records():
    """3. OOS filtering: PENDING, UNKNOWN, VOID, missing prob, temporal violations excluded."""
    now = datetime(2026, 9, 2, 18, 0, 0, tzinfo=timezone.utc)
    kickoff = now + timedelta(hours=2)
    observed = now - timedelta(hours=1)
    settled = kickoff + timedelta(hours=2)

    # 1. Qualified valid WIN record
    rec_valid = PlayerPropSnapshotORM(
        id="snap_1",
        canonical_prop_id="cpp_1",
        canonical_event_id="cev_1",
        canonical_player_id="cplr_1",
        player_name="Player 1",
        home_team="Team A",
        away_team="Team B",
        competition="League",
        stat_type="SHOTS",
        line=0.5,
        direction="OVER",
        kickoff_at=kickoff,
        observed_at=observed,
        reference_probability=0.82,
        outcome_status="SETTLED_WIN",
        actual_shots=2,
        actual_outcome=1,
        settled_at=settled,
    )

    # 2. PENDING record
    rec_pending = PlayerPropSnapshotORM(
        id="snap_2",
        canonical_prop_id="cpp_2",
        canonical_event_id="cev_2",
        canonical_player_id="cplr_2",
        player_name="Player 2",
        home_team="Team A",
        away_team="Team B",
        competition="League",
        stat_type="SHOTS",
        line=0.5,
        direction="OVER",
        kickoff_at=kickoff,
        observed_at=observed,
        reference_probability=0.75,
        outcome_status="PENDING",
        actual_shots=None,
        actual_outcome=None,
    )

    # 3. VOID record (DNP)
    rec_void = PlayerPropSnapshotORM(
        id="snap_3",
        canonical_prop_id="cpp_3",
        canonical_event_id="cev_3",
        canonical_player_id="cplr_3",
        player_name="Player 3",
        home_team="Team A",
        away_team="Team B",
        competition="League",
        stat_type="SHOTS",
        line=0.5,
        direction="OVER",
        kickoff_at=kickoff,
        observed_at=observed,
        reference_probability=0.70,
        outcome_status="VOID",
        actual_shots=None,
        actual_outcome=None,
        settled_at=settled,
    )

    # 4. UNKNOWN record
    rec_unknown = PlayerPropSnapshotORM(
        id="snap_4",
        canonical_prop_id="cpp_4",
        canonical_event_id="cev_4",
        canonical_player_id="cplr_4",
        player_name="Player 4",
        home_team="Team A",
        away_team="Team B",
        competition="League",
        stat_type="SHOTS",
        line=0.5,
        direction="OVER",
        kickoff_at=kickoff,
        observed_at=observed,
        reference_probability=0.65,
        outcome_status="UNKNOWN",
        actual_shots=None,
        actual_outcome=None,
    )

    # 5. Missing probability
    rec_no_prob = PlayerPropSnapshotORM(
        id="snap_5",
        canonical_prop_id="cpp_5",
        canonical_event_id="cev_5",
        canonical_player_id="cplr_5",
        player_name="Player 5",
        home_team="Team A",
        away_team="Team B",
        competition="League",
        stat_type="SHOTS",
        line=0.5,
        direction="OVER",
        kickoff_at=kickoff,
        observed_at=observed,
        reference_probability=None,  # Missing
        outcome_status="SETTLED_WIN",
        actual_shots=1,
        actual_outcome=1,
        settled_at=settled,
    )

    # 6. Temporal violation (observed_at after kickoff -> leakage)
    rec_leakage = PlayerPropSnapshotORM(
        id="snap_6",
        canonical_prop_id="cpp_6",
        canonical_event_id="cev_6",
        canonical_player_id="cplr_6",
        player_name="Player 6",
        home_team="Team A",
        away_team="Team B",
        competition="League",
        stat_type="SHOTS",
        line=0.5,
        direction="OVER",
        kickoff_at=kickoff,
        observed_at=kickoff + timedelta(minutes=15),  # Violation
        reference_probability=0.88,
        outcome_status="SETTLED_WIN",
        actual_shots=3,
        actual_outcome=1,
        settled_at=settled,
    )

    records = [rec_valid, rec_pending, rec_void, rec_unknown, rec_no_prob, rec_leakage]
    qualified, audit = filter_qualified_oos_records(records)

    assert len(qualified) == 1
    assert qualified[0].canonical_prop_id == "cpp_1"

    assert audit["total"] == 6
    assert audit["qualified"] == 1
    assert audit["excluded_pending"] == 1
    assert audit["excluded_void"] == 1
    assert audit["excluded_unknown"] == 1
    assert audit["excluded_missing_probability"] == 1
    assert audit["excluded_temporal_violation"] == 1
