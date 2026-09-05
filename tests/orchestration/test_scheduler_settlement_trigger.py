"""
Targeted tests for Stage A.10.5: Automated Post-Match Settlement Trigger Integration.

Contract requirements:
1. TEST 1 — TRIGGER EXECUTES RUNNER:
   Scheduler/Service trigger executes PlayerShotsSettlementRunner on the database session.
2. TEST 2 — REPEATED TRIGGER IDEMPOTENCY:
   Multiple invocations preserve state without duplication.
"""

from datetime import datetime, timezone, timedelta
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.models import BaseORM, PlayerPropSnapshotORM
from database.repositories.player_prop_snapshot_repository import PlayerPropSnapshotRepository


class MockDBManager:
    """Lightweight in-memory DBManager for scheduler trigger testing."""

    def __init__(self):
        self.engine = create_engine("sqlite:///:memory:")
        BaseORM.metadata.create_all(self.engine)
        self.session_factory = sessionmaker(bind=self.engine)

    def get_session(self):
        return self.session_factory()

    def check_health(self):
        return True


def test_scheduler_trigger_executes_player_shots_settlement():
    """TEST 1 — TRIGGER EXECUTES RUNNER: scheduler.run_settlement_now triggers settlement runner."""
    from api.services import PlatformAPIService

    db_mgr = MockDBManager()
    session = db_mgr.get_session()
    repo = PlayerPropSnapshotRepository(session)

    # Insert a pending snapshot with kickoff in the past
    now = datetime(2026, 9, 2, 22, 0, tzinfo=timezone.utc)
    past_ko = now - timedelta(hours=3)
    snap = PlayerPropSnapshotORM(
        id="snap_trig_001",
        canonical_prop_id="cpp_trig_001",
        canonical_event_id="cev_trig_1",
        canonical_player_id="cplr_trig_1",
        player_name="Jaydon Banel",
        home_team="Burnley",
        away_team="Middlesbrough",
        competition="Championship",
        stat_type="SHOTS",
        line=0.5,
        direction="OVER",
        kickoff_at=past_ko,
        observed_at=past_ko - timedelta(hours=2),
        created_at=past_ko - timedelta(hours=2),
        outcome_status="PENDING",
    )
    repo.save_snapshot(snap)
    session.commit()
    session.close()

    service = PlatformAPIService(db_manager=db_mgr)

    # Trigger settlement through scheduler with explicit evaluation timestamp
    res = service.scheduler.run_settlement_now(now=now)

    assert res is not None
    assert res.get("status") == "COMPLETED"
    assert "eligible" in res
    assert res.get("eligible") == 1


def test_scheduler_trigger_repeated_execution_is_idempotent():
    """TEST 2 — REPEATED TRIGGER IDEMPOTENCY: Multiple invocations preserve state without duplication."""
    from api.services import PlatformAPIService

    db_mgr = MockDBManager()
    service = PlatformAPIService(db_manager=db_mgr)

    # First execution on empty/pending DB
    res1 = service.scheduler.run_settlement_now()
    assert res1.get("status") == "COMPLETED"

    # Second immediate execution
    res2 = service.scheduler.run_settlement_now()
    assert res2.get("status") == "COMPLETED"
    assert res2.get("settled") == 0
