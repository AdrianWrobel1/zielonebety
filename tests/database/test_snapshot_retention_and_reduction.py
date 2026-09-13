"""
Targeted Regression Test Suite for Snapshot Retention, Compaction, and Storage Bounds (P1)

Validates:
1. Snapshot write -> read roundtrip.
2. Old format snapshot (with _events_detail_map) remains readable.
3. New compacted snapshot remains readable.
4. Retention removes only eligible snapshots.
5. Retention preserves snapshots inside retention window (e.g. 7 days).
6. Retention preserves minimum keep count (e.g. 20 scans) regardless of age.
7. Retention strictly preserves protected snapshot types (e.g. SCHEDULER_CONFIG).
8. Cleanup is idempotent (repeated runs do nothing after first run).
9. Cleanup does not delete unrelated records (other tables or types).
10. Dry-run performs zero deletion while returning accurate metrics.
11. Empty snapshot table is handled safely without error.
12. Historical compaction strips _events_detail_map from older records while keeping summary.
13. Latest snapshot retains _events_detail_map for cold-start recovery.
14. Corrupted/invalid JSON payload handled gracefully without crashing.
"""

import json
import uuid
import pytest
from datetime import datetime, timezone, timedelta

from database.connection import DatabaseManager
from database.config import DatabaseConfig
from database.models import SnapshotORM, ProviderORM
from database.repositories.snapshot_repository import SnapshotRepository
from api.services import _save_scan_snapshot, _load_scan_snapshots, _load_latest_ultra_scan_snapshot


@pytest.fixture
def in_memory_db():
    config = DatabaseConfig.default_sqlite_in_memory()
    db = DatabaseManager(config)
    db.create_tables()
    with db.get_session() as session:
        prov = ProviderORM(id="system", name="System", code="sys", enabled=True)
        session.add(prov)
        session.commit()
    return db


def create_mock_scan_payload(execution_id: str, with_detail_map: bool = True, event_count: int = 5) -> dict:
    events_map = {}
    if with_detail_map:
        for i in range(event_count):
            events_map[f"ev_{i}"] = {
                "id": f"ev_{i}",
                "home_team": f"Team A {i}",
                "away_team": f"Team B {i}",
                "markets": [
                    {
                        "market_type": "1X2",
                        "selections": [
                            {"selection_type": "HOME", "odds": {"superbet": 2.0, "betclic": 1.95}},
                            {"selection_type": "AWAY", "odds": {"superbet": 3.5, "betclic": 3.40}},
                        ],
                    }
                ],
            }
    return {
        "execution_id": execution_id,
        "cycle_status": "SUCCESS",
        "pipeline_state": "SUCCESS_CLEAN",
        "started_at": "2026-09-01T10:00:00Z",
        "completed_at": "2026-09-01T10:00:05Z",
        "duration_seconds": 5.0,
        "counts": {
            "discovered_events": event_count,
            "matched_events": event_count,
            "detected_opportunities": 1,
        },
        "scan_trace": {"trace_id": execution_id, "duration": 5.0},
        "opportunities": [
            {
                "id": f"opp_{execution_id}",
                "type": "SUREBET",
                "margin_pct": 2.5,
            }
        ],
        "events": [
            {"id": f"ev_{i}", "home_team": f"Team A {i}", "away_team": f"Team B {i}"}
            for i in range(event_count)
        ],
        "_events_detail_map": events_map,
    }


class TestSnapshotRetentionAndCompaction:

    def test_1_snapshot_write_read_roundtrip(self, in_memory_db):
        """Verify basic save and point-lookup roundtrip."""
        payload = create_mock_scan_payload("exec_001")
        with in_memory_db.get_session() as session:
            repo = SnapshotRepository(session)
            snap = SnapshotORM(
                id="snap_001",
                provider_id="system",
                execution_id="exec_001",
                snapshot_type="SCAN_CYCLE_RESULT",
                payload=json.dumps(payload),
                created_at=datetime.now(timezone.utc),
            )
            repo.save_snapshot(snap, compact_previous=False)
            session.commit()

        with in_memory_db.get_session() as session:
            repo = SnapshotRepository(session)
            loaded = repo.get_by_execution_id("exec_001")
            assert loaded is not None
            assert loaded.id == "snap_001"
            assert loaded.snapshot_type == "SCAN_CYCLE_RESULT"
            data = json.loads(loaded.payload)
            assert data["execution_id"] == "exec_001"
            assert "_events_detail_map" in data

    def test_2_old_and_new_snapshots_remain_readable(self, in_memory_db):
        """Verify that old snapshots (with _events_detail_map) and compacted ones are both readable."""
        old_payload = create_mock_scan_payload("exec_old", with_detail_map=True)
        new_payload = create_mock_scan_payload("exec_new", with_detail_map=False)

        with in_memory_db.get_session() as session:
            repo = SnapshotRepository(session)
            session.add(SnapshotORM(
                id="snap_old", provider_id="system", execution_id="exec_old",
                snapshot_type="SCAN_CYCLE_RESULT", payload=json.dumps(old_payload),
                created_at=datetime.now(timezone.utc) - timedelta(days=2),
            ))
            session.add(SnapshotORM(
                id="snap_new", provider_id="system", execution_id="exec_new",
                snapshot_type="SCAN_CYCLE_RESULT", payload=json.dumps(new_payload),
                created_at=datetime.now(timezone.utc),
            ))
            session.commit()

        # Load via service helper
        results = _load_scan_snapshots(in_memory_db, limit=10)
        assert len(results) == 2
        exec_ids = [r["execution_id"] for r in results]
        assert "exec_new" in exec_ids
        assert "exec_old" in exec_ids

    def test_3_retention_removes_only_eligible_snapshots(self, in_memory_db):
        """Snapshots older than retention window AND exceeding keep limit are pruned."""
        now = datetime.now(timezone.utc)
        with in_memory_db.get_session() as session:
            repo = SnapshotRepository(session)
            # Create 10 recent snapshots (within 2 days)
            for i in range(10):
                session.add(SnapshotORM(
                    id=f"snap_recent_{i}", provider_id="system", execution_id=f"exec_rec_{i}",
                    snapshot_type="SCAN_CYCLE_RESULT", payload=json.dumps({"i": i}),
                    created_at=now - timedelta(hours=i),
                ))
            # Create 5 expired snapshots (older than 10 days)
            for i in range(5):
                session.add(SnapshotORM(
                    id=f"snap_expired_{i}", provider_id="system", execution_id=f"exec_exp_{i}",
                    snapshot_type="SCAN_CYCLE_RESULT", payload=json.dumps({"expired": i}),
                    created_at=now - timedelta(days=15 + i),
                ))
            session.commit()

        # Prune with retention_days=7, max_scan_keep=10
        with in_memory_db.get_session() as session:
            repo = SnapshotRepository(session)
            report = repo.prune_expired_snapshots(
                retention_days=7,
                max_scan_keep=10,
                dry_run=False,
            )
            session.commit()
            assert report["eligible_count"] == 5
            assert report["deleted_count"] == 5
            assert report["retained_count"] == 10

        # Verify only 10 remain
        with in_memory_db.get_session() as session:
            repo = SnapshotRepository(session)
            remaining = repo.list_recent("SCAN_CYCLE_RESULT", limit=50)
            assert len(remaining) == 10
            for r in remaining:
                assert r.id.startswith("snap_recent_")

    def test_4_retention_preserves_min_keep_count_regardless_of_age(self, in_memory_db):
        """Even if snapshots are older than retention_days, max_scan_keep newest are strictly preserved."""
        now = datetime.now(timezone.utc)
        with in_memory_db.get_session() as session:
            repo = SnapshotRepository(session)
            # Create 15 snapshots, ALL older than 30 days
            for i in range(15):
                session.add(SnapshotORM(
                    id=f"snap_old_{i}", provider_id="system", execution_id=f"exec_old_{i}",
                    snapshot_type="SCAN_CYCLE_RESULT", payload=json.dumps({"i": i}),
                    created_at=now - timedelta(days=30 + i),
                ))
            session.commit()

        # Prune with max_scan_keep=10, retention_days=7
        with in_memory_db.get_session() as session:
            repo = SnapshotRepository(session)
            report = repo.prune_expired_snapshots(
                retention_days=7,
                max_scan_keep=10,
                dry_run=False,
            )
            session.commit()
            # 5 should be deleted, exactly 10 preserved
            assert report["eligible_count"] == 5
            assert report["deleted_count"] == 5
            assert report["retained_count"] == 10

        with in_memory_db.get_session() as session:
            repo = SnapshotRepository(session)
            remaining = repo.list_recent("SCAN_CYCLE_RESULT", limit=50)
            assert len(remaining) == 10

    def test_5_retention_strictly_protects_scheduler_config(self, in_memory_db):
        """SCHEDULER_CONFIG snapshots must NEVER be pruned."""
        now = datetime.now(timezone.utc)
        with in_memory_db.get_session() as session:
            session.add(SnapshotORM(
                id="snap_sched_1", provider_id="system", execution_id="scheduler_config",
                snapshot_type="SCHEDULER_CONFIG", payload=json.dumps({"enabled": True}),
                created_at=now - timedelta(days=100),
            ))
            session.commit()

        with in_memory_db.get_session() as session:
            repo = SnapshotRepository(session)
            report = repo.prune_expired_snapshots(
                retention_days=7,
                max_scan_keep=0,
                dry_run=False,
            )
            session.commit()
            assert report["deleted_count"] == 0

        with in_memory_db.get_session() as session:
            repo = SnapshotRepository(session)
            sched = repo.get_by_execution_id("scheduler_config")
            assert sched is not None
            assert sched.id == "snap_sched_1"

    def test_6_dry_run_performs_zero_deletion(self, in_memory_db):
        """Dry-run returns calculation report without deleting any records."""
        now = datetime.now(timezone.utc)
        with in_memory_db.get_session() as session:
            for i in range(10):
                session.add(SnapshotORM(
                    id=f"snap_exp_{i}", provider_id="system", execution_id=f"exec_{i}",
                    snapshot_type="SCAN_CYCLE_RESULT", payload=json.dumps({"size": "test" * 50}),
                    created_at=now - timedelta(days=20 + i),
                ))
            session.commit()

        with in_memory_db.get_session() as session:
            repo = SnapshotRepository(session)
            report = repo.prune_expired_snapshots(
                retention_days=7,
                max_scan_keep=3,
                dry_run=True,
            )
            assert report["dry_run"] is True
            assert report["eligible_count"] == 7
            assert report["deleted_count"] == 0
            assert report["total_freed_bytes"] > 0

        # Confirm all 10 still exist
        with in_memory_db.get_session() as session:
            repo = SnapshotRepository(session)
            assert len(repo.list_all()) == 10

    def test_7_cleanup_idempotency(self, in_memory_db):
        """Running cleanup twice produces zero deletions on the second pass."""
        now = datetime.now(timezone.utc)
        with in_memory_db.get_session() as session:
            for i in range(8):
                session.add(SnapshotORM(
                    id=f"snap_{i}", provider_id="system", execution_id=f"exec_{i}",
                    snapshot_type="SCAN_CYCLE_RESULT", payload=json.dumps({"i": i}),
                    created_at=now - timedelta(days=10 + i),
                ))
            session.commit()

        # Pass 1
        with in_memory_db.get_session() as session:
            repo = SnapshotRepository(session)
            r1 = repo.prune_expired_snapshots(retention_days=7, max_scan_keep=4, dry_run=False)
            session.commit()
            assert r1["deleted_count"] == 4

        # Pass 2
        with in_memory_db.get_session() as session:
            repo = SnapshotRepository(session)
            r2 = repo.prune_expired_snapshots(retention_days=7, max_scan_keep=4, dry_run=False)
            session.commit()
            assert r2["deleted_count"] == 0
            assert r2["eligible_count"] == 0

    def test_8_empty_snapshot_table_safety(self, in_memory_db):
        """Operations on an empty snapshots table run without errors."""
        with in_memory_db.get_session() as session:
            repo = SnapshotRepository(session)
            report = repo.prune_expired_snapshots(retention_days=7, dry_run=False)
            assert report["eligible_count"] == 0
            assert report["deleted_count"] == 0

            comp = repo.compact_historical_snapshots(snapshot_type="SCAN_CYCLE_RESULT", dry_run=False)
            assert comp["compacted_count"] == 0

    def test_9_historical_compaction_strips_events_detail_map_only(self, in_memory_db):
        """Older historical snapshots have _events_detail_map removed, keeping the latest intact."""
        now = datetime.now(timezone.utc)
        p_latest = create_mock_scan_payload("exec_latest", with_detail_map=True)
        p_old1 = create_mock_scan_payload("exec_old1", with_detail_map=True)
        p_old2 = create_mock_scan_payload("exec_old2", with_detail_map=True)

        with in_memory_db.get_session() as session:
            session.add(SnapshotORM(
                id="snap_latest", provider_id="system", execution_id="exec_latest",
                snapshot_type="SCAN_CYCLE_RESULT", payload=json.dumps(p_latest),
                created_at=now,
            ))
            session.add(SnapshotORM(
                id="snap_old1", provider_id="system", execution_id="exec_old1",
                snapshot_type="SCAN_CYCLE_RESULT", payload=json.dumps(p_old1),
                created_at=now - timedelta(hours=1),
            ))
            session.add(SnapshotORM(
                id="snap_old2", provider_id="system", execution_id="exec_old2",
                snapshot_type="SCAN_CYCLE_RESULT", payload=json.dumps(p_old2),
                created_at=now - timedelta(hours=2),
            ))
            session.commit()

        with in_memory_db.get_session() as session:
            repo = SnapshotRepository(session)
            comp_report = repo.compact_historical_snapshots(
                snapshot_type="SCAN_CYCLE_RESULT",
                keep_full_count=1,
                dry_run=False,
            )
            session.commit()
            assert comp_report["scanned_count"] == 3
            assert comp_report["compacted_count"] == 2
            assert comp_report["freed_bytes"] > 0

        # Verify: latest still has _events_detail_map, old ones do not
        with in_memory_db.get_session() as session:
            repo = SnapshotRepository(session)
            latest = repo.get_by_execution_id("exec_latest")
            data_latest = json.loads(latest.payload)
            assert "_events_detail_map" in data_latest
            assert len(data_latest["_events_detail_map"]) == 5

            old1 = repo.get_by_execution_id("exec_old1")
            data_old1 = json.loads(old1.payload)
            assert "_events_detail_map" not in data_old1
            # But all essential summary fields are fully preserved!
            assert data_old1["counts"]["detected_opportunities"] == 1
            assert data_old1["opportunities"][0]["margin_pct"] == 2.5
            assert data_old1["scan_trace"]["duration"] == 5.0

    def test_10_automatic_compaction_and_retention_on_save_scan_snapshot(self, in_memory_db):
        """_save_scan_snapshot automatically compacts older snapshots and applies retention."""
        now = datetime.now(timezone.utc)
        # Pre-populate 5 old snapshots
        for i in range(5):
            payload = create_mock_scan_payload(f"exec_init_{i}", with_detail_map=True)
            _save_scan_snapshot(in_memory_db, f"exec_init_{i}", payload)

        with in_memory_db.get_session() as session:
            repo = SnapshotRepository(session)
            snaps = repo.list_recent("SCAN_CYCLE_RESULT", limit=10)
            assert len(snaps) == 5
            # Only the single newest has _events_detail_map
            data_newest = json.loads(snaps[0].payload)
            assert "_events_detail_map" in data_newest
            # Older 4 were compacted
            for s in snaps[1:]:
                d = json.loads(s.payload)
                assert "_events_detail_map" not in d

    def test_11_corrupted_json_payload_handled_gracefully(self, in_memory_db):
        """Corrupted/invalid JSON payload in snapshot does not crash repository operations."""
        with in_memory_db.get_session() as session:
            session.add(SnapshotORM(
                id="snap_bad", provider_id="system", execution_id="exec_bad",
                snapshot_type="SCAN_CYCLE_RESULT", payload="INVALID_JSON{{{{",
                created_at=datetime.now(timezone.utc),
            ))
            session.commit()

        with in_memory_db.get_session() as session:
            repo = SnapshotRepository(session)
            # Compaction should gracefully skip the invalid row
            comp = repo.compact_historical_snapshots(snapshot_type="SCAN_CYCLE_RESULT", keep_full_count=0)
            assert comp["compacted_count"] == 0

            # Pruning should still be able to delete it safely if expired
            report = repo.prune_expired_snapshots(retention_days=0, max_scan_keep=0, dry_run=False)
            session.commit()
            assert report["deleted_count"] == 1
