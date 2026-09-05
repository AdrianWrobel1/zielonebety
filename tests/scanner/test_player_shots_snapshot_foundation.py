"""
Targeted tests for Player Shots Over 0.5 Pre-Match Snapshot Foundation (Stage A.10).
"""

from datetime import datetime, timezone, timedelta
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.models import BaseORM, PlayerPropSnapshotORM
from database.repositories.player_prop_snapshot_repository import PlayerPropSnapshotRepository
from scanner.player_shots_snapshot_recorder import (
    PlayerShotsSnapshotRecorder,
    TemporalLeakageError,
    InvalidPropScopeError,
)


@pytest.fixture
def db_session():
    """In-memory SQLite database session for targeted tests."""
    engine = create_engine("sqlite:///:memory:")
    BaseORM.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    session = session_factory()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture
def repository(db_session):
    return PlayerPropSnapshotRepository(db_session)


@pytest.fixture
def recorder(repository):
    return PlayerShotsSnapshotRecorder(repository=repository)


def test_snapshot_records_valid_pre_match_fields(recorder, repository):
    """1. Snapshot correctly maps all pre-match observation, estimation, and execution fields."""
    now = datetime(2026, 9, 2, 12, 0, 0, tzinfo=timezone.utc)
    kickoff = now + timedelta(hours=6)

    res = recorder.record_snapshot(
        player_name="Cian Ashford",
        home_team="Cardiff City",
        away_team="Queens Park Rangers",
        competition="Championship",
        stat_type="SHOTS",
        line=0.5,
        direction="OVER",
        kickoff=kickoff,
        observed_at=now,
        hit_rate=100.0,
        hits=10,
        sample_size=10,
        stat_average=3.2,
        position_role="F",
        recent_matches=[{"date": "2026-08-25", "stat_value": 3, "minutes_played": 85}],
        reference_probability=0.813,
        reference_fair_odds=1.23,
        reference_consensus_odds=1.182,
        reference_bookmaker_count=6,
        reference_odds_sources=[{"bookmaker": "Bet365", "odds": 1.18}],
        betclic_odds=1.18,
    )

    assert res.is_saved_new is True
    assert res.is_rejected is False
    assert res.canonical_prop_id.startswith("cpp_")

    saved = repository.get_by_canonical_prop_id(res.canonical_prop_id)
    assert saved is not None
    assert saved.player_name == "Cian Ashford"
    assert saved.stat_type == "SHOTS"
    assert saved.line == 0.5
    assert saved.direction == "OVER"
    assert saved.hit_rate == 100.0
    assert saved.sample_size == 10
    assert saved.reference_probability == 0.813
    assert saved.reference_fair_odds == 1.23
    assert saved.reference_bookmaker_count == 6
    assert saved.betclic_odds == 1.18
    assert saved.outcome_status == "PENDING"
    assert saved.actual_shots is None
    assert saved.actual_outcome is None


def test_snapshot_rejects_temporal_leakage_at_or_after_kickoff(recorder):
    """2. Snapshot strictly rejects observation at or after kickoff."""
    kickoff = datetime(2026, 9, 2, 18, 0, 0, tzinfo=timezone.utc)
    after_kickoff = kickoff + timedelta(minutes=10)

    # Direct builder should raise error
    with pytest.raises(TemporalLeakageError):
        recorder.build_snapshot(
            player_name="Riley McGree",
            home_team="Burnley",
            away_team="Middlesbrough",
            competition="Championship",
            stat_type="SHOTS",
            line=0.5,
            direction="OVER",
            kickoff=kickoff,
            observed_at=after_kickoff,
        )

    # Recorder method should capture rejection without crashing
    res = recorder.record_snapshot(
        player_name="Riley McGree",
        home_team="Burnley",
        away_team="Middlesbrough",
        competition="Championship",
        stat_type="SHOTS",
        line=0.5,
        direction="OVER",
        kickoff=kickoff,
        observed_at=after_kickoff,
    )
    assert res.is_rejected is True
    assert "Temporal leakage detected" in res.rejection_reason


def test_snapshot_rejects_out_of_scope_props(recorder):
    """3. Rejects non-SHOTS or non-Over 0.5 props."""
    now = datetime(2026, 9, 2, 12, 0, 0, tzinfo=timezone.utc)
    kickoff = now + timedelta(hours=4)

    with pytest.raises(InvalidPropScopeError):
        recorder.build_snapshot(
            player_name="Test Player",
            home_team="Team A",
            away_team="Team B",
            competition="Premier League",
            stat_type="FOULS",  # Out of scope
            line=0.5,
            direction="OVER",
            kickoff=kickoff,
            observed_at=now,
        )

    with pytest.raises(InvalidPropScopeError):
        recorder.build_snapshot(
            player_name="Test Player",
            home_team="Team A",
            away_team="Team B",
            competition="Premier League",
            stat_type="SHOTS",
            line=1.5,  # Out of scope line
            direction="OVER",
            kickoff=kickoff,
            observed_at=now,
        )


def test_idempotency_and_immutability(recorder, repository):
    """4. Repeated snapshot recording preserves original values without duplicate records."""
    now = datetime(2026, 9, 2, 12, 0, 0, tzinfo=timezone.utc)
    kickoff = now + timedelta(hours=4)

    # Initial snapshot
    res1 = recorder.record_snapshot(
        player_name="Ollie Tanner",
        home_team="Cardiff City",
        away_team="Queens Park Rangers",
        competition="Championship",
        stat_type="SHOTS",
        line=0.5,
        direction="OVER",
        kickoff=kickoff,
        observed_at=now,
        hit_rate=80.0,
        reference_probability=0.849,
    )
    assert res1.is_saved_new is True

    # Attempt second snapshot with modified hit_rate
    res2 = recorder.record_snapshot(
        player_name="Ollie Tanner",
        home_team="Cardiff City",
        away_team="Queens Park Rangers",
        competition="Championship",
        stat_type="SHOTS",
        line=0.5,
        direction="OVER",
        kickoff=kickoff,
        observed_at=now + timedelta(minutes=15),
        hit_rate=90.0,  # Modified
        reference_probability=0.899,  # Modified
    )
    assert res2.is_saved_new is False
    assert res2.rejection_reason == "ALREADY_EXISTS_PRESERVED"

    # Verify that stored record still has original values
    saved = repository.get_by_canonical_prop_id(res1.canonical_prop_id)
    assert saved.hit_rate == 80.0
    assert saved.reference_probability == 0.849
    assert len(repository.list_all()) == 1


def test_settlement_transitions(repository):
    """5. Settlement transitions to SETTLED_WIN (shots >= 1), SETTLED_LOSS (shots == 0), and VOID."""
    snapshot = PlayerPropSnapshotORM(
        id="snap_test_123",
        canonical_prop_id="cpp_test_123",
        canonical_event_id="cev_test_123",
        canonical_player_id="cplr_test_123",
        player_name="Test Player",
        home_team="Team A",
        away_team="Team B",
        competition="Championship",
        stat_type="SHOTS",
        line=0.5,
        direction="OVER",
        kickoff_at=datetime.now(timezone.utc),
        observed_at=datetime.now(timezone.utc) - timedelta(hours=2),
        outcome_status="PENDING",
    )
    repository.save_snapshot(snapshot)

    # Test Win: shots = 2 -> SETTLED_WIN (y=1)
    settled_win = repository.record_settlement(
        canonical_prop_id="cpp_test_123",
        actual_shots=2,
        settlement_source="OPTA_FEED",
    )
    assert settled_win.outcome_status == "SETTLED_WIN"
    assert settled_win.actual_shots == 2
    assert settled_win.actual_outcome == 1
    assert settled_win.settled_at is not None

    # Test Loss: shots = 0 -> SETTLED_LOSS (y=0)
    settled_loss = repository.record_settlement(
        canonical_prop_id="cpp_test_123",
        actual_shots=0,
        settlement_source="OPTA_FEED",
    )
    assert settled_loss.outcome_status == "SETTLED_LOSS"
    assert settled_loss.actual_shots == 0
    assert settled_loss.actual_outcome == 0

    # Test VOID (e.g. player did not play / match abandoned)
    settled_void = repository.record_settlement(
        canonical_prop_id="cpp_test_123",
        actual_shots=None,
        outcome_status="VOID",
        settlement_notes="Player remained on bench for full 90 minutes",
    )
    assert settled_void.outcome_status == "VOID"
    assert settled_void.actual_outcome is None
    assert settled_void.settlement_notes == "Player remained on bench for full 90 minutes"
