"""
Targeted tests for Player Shots Over 0.5 Outcome Collection & Settlement (Stage A.10.1).

Scope strictly covers the 4 required settlement behaviors:
1. actual_shots >= 1 -> SETTLED_WIN -> y = 1
2. actual_shots == 0 -> SETTLED_LOSS -> y = 0
3. Missing/uncertain actual_shots -> PENDING/UNKNOWN (never LOSS), DNP -> VOID
4. Repeated settlement of the same snapshot -> no duplicate, strict idempotency preserved
"""

from datetime import datetime, timezone, timedelta
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.models import BaseORM, PlayerPropSnapshotORM
from database.repositories.player_prop_snapshot_repository import PlayerPropSnapshotRepository
from scanner.player_shots_settler import PlayerShotsSettler


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
def settler(repository):
    return PlayerShotsSettler(repository=repository)


def _create_test_snapshot(repository, prop_id="cpp_test_001", kickoff_offset_hours=-2):
    """Helper creating a pre-match snapshot in PENDING state."""
    kickoff = datetime.now(timezone.utc) + timedelta(hours=kickoff_offset_hours)
    observed = kickoff - timedelta(hours=3)
    snapshot = PlayerPropSnapshotORM(
        id=f"snap_{prop_id}",
        canonical_prop_id=prop_id,
        canonical_event_id="cev_test_event_1",
        canonical_player_id="cplr_test_player_1",
        player_name="Lars Timo Gindorf",
        home_team="Hannover 96",
        away_team="Karlsruher SC",
        competition="2. Bundesliga",
        stat_type="SHOTS",
        line=0.5,
        direction="OVER",
        kickoff_at=kickoff,
        observed_at=observed,
        created_at=observed,
        hit_rate=80.0,
        sample_size=10,
        reference_probability=0.78,
        reference_fair_odds=1.28,
        betclic_odds=1.35,
        outcome_status="PENDING",
        actual_shots=None,
        actual_outcome=None,
        is_locked=True,
    )
    saved, _ = repository.save_snapshot(snapshot)
    return saved


def test_settlement_win_when_shots_ge_1(settler, repository):
    """1. actual_shots >= 1 -> SETTLED_WIN -> y = 1."""
    snap = _create_test_snapshot(repository, prop_id="cpp_win_test")

    res = settler.settle_snapshot(
        snapshot=snap,
        actual_shots=3,
        minutes_played=75,
        settlement_source="STATSHUB",
        resolved_at=datetime.now(timezone.utc),
    )

    assert res.is_settled is True
    assert res.outcome_status == "SETTLED_WIN"
    assert res.actual_shots == 3
    assert res.actual_outcome == 1  # y = 1

    # Verify DB persistence and pre-match feature immutability
    db_record = repository.get_by_canonical_prop_id("cpp_win_test")
    assert db_record.outcome_status == "SETTLED_WIN"
    assert db_record.actual_outcome == 1
    assert db_record.actual_shots == 3
    assert db_record.hit_rate == 80.0  # Pre-match feature untouched
    assert db_record.reference_probability == 0.78  # Pre-match feature untouched


def test_settlement_loss_when_shots_eq_0(settler, repository):
    """2. actual_shots == 0 -> SETTLED_LOSS -> y = 0."""
    snap = _create_test_snapshot(repository, prop_id="cpp_loss_test")

    res = settler.settle_snapshot(
        snapshot=snap,
        actual_shots=0,
        minutes_played=90,
        settlement_source="STATSHUB",
        resolved_at=datetime.now(timezone.utc),
    )

    assert res.is_settled is True
    assert res.outcome_status == "SETTLED_LOSS"
    assert res.actual_shots == 0
    assert res.actual_outcome == 0  # y = 0

    # Verify DB record
    db_record = repository.get_by_canonical_prop_id("cpp_loss_test")
    assert db_record.outcome_status == "SETTLED_LOSS"
    assert db_record.actual_outcome == 0
    assert db_record.actual_shots == 0


def test_missing_data_never_defaults_to_loss(settler, repository):
    """3. Missing/uncertain actual_shots -> PENDING/UNKNOWN (never LOSS); DNP -> VOID."""
    # Case A: Missing data remains PENDING
    snap_pending = _create_test_snapshot(repository, prop_id="cpp_missing_test")
    res_pending = settler.settle_snapshot(
        snapshot=snap_pending,
        actual_shots=None,
        minutes_played=None,
    )
    assert res_pending.is_settled is False
    assert res_pending.outcome_status == "PENDING"
    assert res_pending.actual_outcome is None  # NEVER 0 / LOSS

    # Case B: Unresolvable / feed error -> UNKNOWN
    res_unknown = settler.settle_snapshot(
        snapshot=snap_pending,
        actual_shots=None,
        outcome_status="UNKNOWN",
        settlement_notes="Provider feed missing match statistics",
    )
    assert res_unknown.outcome_status == "UNKNOWN"
    assert res_unknown.actual_outcome is None  # NEVER 0 / LOSS

    # Case C: Player did not play (minutes = 0) -> VOID
    snap_dnp = _create_test_snapshot(repository, prop_id="cpp_dnp_test")
    res_void = settler.settle_snapshot(
        snapshot=snap_dnp,
        actual_shots=None,
        minutes_played=0,
    )
    assert res_void.outcome_status == "VOID"
    assert res_void.actual_outcome is None  # NEVER 0 / LOSS


def test_idempotency_preserves_settled_outcome_without_duplication(settler, repository):
    """4. Repeated settlement of the same snapshot produces no duplicate and preserves outcome."""
    snap = _create_test_snapshot(repository, prop_id="cpp_idempotent_test")

    # Initial settlement: 2 shots -> SETTLED_WIN (y = 1)
    res1 = settler.settle_snapshot(
        snapshot=snap,
        actual_shots=2,
        minutes_played=80,
        settlement_source="STATSHUB",
    )
    assert res1.is_settled is True
    assert res1.outcome_status == "SETTLED_WIN"
    assert res1.actual_outcome == 1

    # Second settlement call with same or empty input (e.g. routine scan retry)
    res2 = settler.settle_snapshot(
        snapshot="cpp_idempotent_test",
        actual_shots=2,
        minutes_played=80,
    )
    assert res2.is_settled is True
    assert res2.outcome_status == "SETTLED_WIN"
    assert res2.actual_outcome == 1

    # Third settlement call with missing data: must NOT overwrite settled WIN
    res3 = settler.settle_snapshot(
        snapshot="cpp_idempotent_test",
        actual_shots=None,
        outcome_status="UNKNOWN",
    )
    assert res3.outcome_status == "SETTLED_WIN"
    assert res3.actual_outcome == 1

    # Verify table has exactly 1 row (no duplicates)
    all_records = repository.list_all()
    assert len(all_records) == 1
    assert all_records[0].canonical_prop_id == "cpp_idempotent_test"
    assert all_records[0].actual_outcome == 1


def test_settler_rejects_mismatched_stat_type_props(settler, repository):
    """Regression test: settler must not match StatsHub props with differing stat_type (e.g. totaltackle vs SHOTS)."""
    from providers.statshub.models import StatsHubFixture, StatsHubHistoricalMatch, StatsHubPlayerStat, StatsHubPropResult
    snap = _create_test_snapshot(repository, prop_id="cpp_stat_type_mismatch")

    fix = StatsHubFixture(fixture_id="16391195", home_team="Hannover 96", away_team="Karlsruher SC")
    hm = StatsHubHistoricalMatch(
        opponent="Karlsruher SC",
        date="2026-09-02",
        timestamp=int(snap.kickoff_at.timestamp()),
        minutes_played=84,
        stat_value=4,
        event_id=16391195,
    )
    # Prop with stat_type="totaltackle" instead of "shots"
    ps = StatsHubPlayerStat(
        player_name="Lars Timo Gindorf",
        team="Hannover 96",
        opponent="Karlsruher SC",
        fixture=fix,
        stat_type="totaltackle",
        historical_matches=[hm],
    )
    mismatched_prop_results = [StatsHubPropResult(player_stat=ps)]

    results = settler.settle_from_statshub_props(
        prop_results=mismatched_prop_results,
        pending_snapshots=[snap],
    )

    # Must NOT settle because stat_type is totaltackle, not SHOTS
    assert len(results) == 0
    db_rec = repository.get_by_canonical_prop_id("cpp_stat_type_mismatch")
    assert db_rec.outcome_status == "PENDING"

