"""P1-NEW-008 regression: DB transaction safety.

- concurrent duplicate fingerprint inserts converge to one row (no 500);
- IntegrityError recovery leaves the session usable;
- failed commit paths roll back (lifecycle log-and-continue stays usable);
- snapshot duplicate race returns the winning row.
"""
import os
import tempfile
import threading
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.models import (
    BaseORM,
    DeliveryRecordORM,
    OpportunityRecordORM,
    PlayerPropSnapshotORM,
)
from database.repositories.delivery_repository import DeliveryRepository
from database.repositories.opportunity_repository import OpportunityRepository
from database.repositories.player_prop_snapshot_repository import PlayerPropSnapshotRepository


def _engine():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    eng = create_engine(f"sqlite:///{tmp.name}", connect_args={"check_same_thread": False, "timeout": 30})
    BaseORM.metadata.create_all(eng)
    eng._tmp_path = tmp.name
    return eng


def _opp(i):
    now = datetime.now(timezone.utc)
    return OpportunityRecordORM(
        id=f"rec_{i}", fingerprint="fp_race", opportunity_type="VALUEBET",
        canonical_event_id="cev1", market_key="1X2", status="NEW",
        first_seen_at=now, last_seen_at=now, last_changed_at=now,
        arbitrage_margin=0.05, implied_probability_sum=0.95, snapshot_json="{}",
        consecutive_misses=0, alert_count=0,
    )


def test_concurrent_duplicate_fingerprint_converges():
    eng = _engine()
    try:
        S = sessionmaker(bind=eng)
        errors = []

        def worker(i):
            try:
                s = S()
                try:
                    OpportunityRepository(s).save_or_update(_opp(i))
                    s.commit()
                finally:
                    s.close()
            except Exception as exc:  # noqa: BLE001 - collected, asserted empty
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []
        s = S()
        try:
            rows = s.query(OpportunityRecordORM).filter_by(fingerprint="fp_race").all()
            assert len(rows) == 1
        finally:
            s.close()
    finally:
        eng.dispose()
        os.unlink(eng._tmp_path)


def test_integrity_error_recovery_keeps_session_usable():
    eng = _engine()
    try:
        S = sessionmaker(bind=eng)
        s = S()
        try:
            repo = OpportunityRepository(s)
            repo.save_or_update(_opp("a"))
            s.commit()
            # Losing race: row now exists under another id; save_or_update
            # must recover into an update, not raise.
            out = repo.save_or_update(_opp("b"))
            assert out.id == "rec_a"
            # Session still usable for subsequent reads/writes.
            assert repo.get_by_fingerprint("fp_race") is not None
            repo.save_or_update(_opp("c"))
            s.commit()
            assert s.query(OpportunityRecordORM).filter_by(fingerprint="fp_race").count() == 1
        finally:
            s.close()
    finally:
        eng.dispose()
        os.unlink(eng._tmp_path)


def test_delivery_idempotency_race_converges():
    eng = _engine()
    try:
        S = sessionmaker(bind=eng)
        now = datetime.now(timezone.utc)

        def mk(i):
            return DeliveryRecordORM(
                id=f"del_{i}", idempotency_key="idem_race", opportunity_fingerprint="fp1",
                opportunity_id="rec_1", consumer_name="telegram", lifecycle_version=1,
                state="PENDING", attempt_count=0, max_attempts=3,
                created_at=now, payload_snapshot_json="{}",
            )

        s = S()
        try:
            repo = DeliveryRepository(s)
            repo.save_or_update(mk("a"))
            s.commit()
            out = repo.save_or_update(mk("b"))
            assert out.id == "del_a"
            s.commit()
            assert s.query(DeliveryRecordORM).filter_by(idempotency_key="idem_race").count() == 1
        finally:
            s.close()
    finally:
        eng.dispose()
        os.unlink(eng._tmp_path)


def test_snapshot_duplicate_race_returns_winner():
    eng = _engine()
    try:
        S = sessionmaker(bind=eng)
        now = datetime.now(timezone.utc)

        def mk(i):
            return PlayerPropSnapshotORM(
                id=f"snap_{i}", canonical_prop_id="prop_race", canonical_event_id="cev1",
                canonical_player_id="cplr1", player_name="Test Player",
                home_team="A", away_team="B", competition="Test Cup",
                stat_type="SHOTS", line=0.5, direction="OVER",
                outcome_status="PENDING", observed_at=now, kickoff_at=now,
            )

        s = S()
        try:
            repo = PlayerPropSnapshotRepository(s)
            rec, created = repo.save_snapshot(mk("a"))
            assert created is True
            s.commit()
            rec2, created2 = repo.save_snapshot(mk("b"))
            assert created2 is False
            assert rec2.id == "snap_a"
        finally:
            s.close()
    finally:
        eng.dispose()
        os.unlink(eng._tmp_path)
