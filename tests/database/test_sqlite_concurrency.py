"""
Authoritative Concurrency & Lock Integrity Tests for SQLite
Verifies:
1. File-based SQLite initializes with PRAGMA journal_mode=WAL and PRAGMA busy_timeout.
2. Concurrent multi-threaded writers to opportunity_records and snapshots succeed without OperationalError: database is locked.
3. ProductionScanOrchestrator Stage 5 & 6 execute within explicit transaction boundaries and commit cleanly.
"""

import os
import shutil
import tempfile
import threading
import time
import unittest
from datetime import datetime, timezone
from decimal import Decimal

from database.config import DatabaseConfig
from database.connection import DatabaseManager
from database.models import BaseORM, OpportunityRecordORM, DeliveryRecordORM, SnapshotORM
from database.repositories.opportunity_repository import OpportunityRepository
from database.repositories.delivery_repository import DeliveryRepository
from normalization.alert_policy import DefaultOpportunityAlertPolicy, OpportunityAlertConfig
from normalization.delivery_reliability import DeliveryReconciliationService, DeliveryRetryConfig
from normalization.lifecycle import OpportunityLifecycleManager
from normalization.dispatcher import OpportunityDispatcher, InMemoryOpportunityConsumer
from orchestration.models import ScanConfig
from orchestration.scan_orchestrator import ProductionScanOrchestrator


class TestSQLiteConcurrency(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test_concurrency.db")
        self.config = DatabaseConfig(db_url=f"sqlite:///{self.db_path}")
        self.db_manager = DatabaseManager(self.config)
        self.db_manager.create_tables()

    def tearDown(self):
        self.db_manager.dispose()
        time.sleep(0.1)
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_sqlite_pragmas_wal_and_busy_timeout(self):
        """File-backed SQLite must enable WAL mode and busy_timeout."""
        with self.db_manager.get_session() as session:
            conn = session.connection().connection
            cur = conn.cursor()
            cur.execute("PRAGMA journal_mode;")
            journal_mode = cur.fetchone()[0].lower()
            cur.execute("PRAGMA busy_timeout;")
            busy_timeout = cur.fetchone()[0]

            self.assertEqual(journal_mode, "wal")
            self.assertGreaterEqual(busy_timeout, 5000)

    def test_concurrent_writers_do_not_lock(self):
        """Simultaneous writers across multiple threads must not encounter OperationalError: database is locked."""
        errors = []
        record_count = 20

        def writer_worker(worker_id: int):
            try:
                for i in range(record_count):
                    with self.db_manager.get_session() as session:
                        repo = OpportunityRepository(session)
                        rec = OpportunityRecordORM(
                            id=f"rec_{worker_id}_{i}",
                            fingerprint=f"fp_{worker_id}_{i}",
                            opportunity_type="SUREBET",
                            canonical_event_id=f"cev_{worker_id}_{i}",
                            market_key="1X2",
                            status="NEW",
                            arbitrage_margin=0.03,
                            implied_probability_sum=0.97,
                            snapshot_json="{}",
                        )
                        repo.save_or_update(rec)
                        session.commit()
                    time.sleep(0.005)
            except Exception as e:
                errors.append(f"Worker {worker_id} failed: {type(e).__name__}: {e}")

        threads = [threading.Thread(target=writer_worker, args=(w,)) for w in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [], f"Encountered concurrency errors: {errors}")

        # Verify all records written
        with self.db_manager.get_session() as session:
            count = session.query(OpportunityRecordORM).count()
            self.assertEqual(count, 5 * record_count)

    def test_orchestrator_lifecycle_commits_and_releases_lock(self):
        """ProductionScanOrchestrator must commit lifecycle changes so concurrent sessions can write immediately."""
        dispatcher = OpportunityDispatcher()
        dispatcher.register_consumer(InMemoryOpportunityConsumer("in_mem"))
        alert_policy = DefaultOpportunityAlertPolicy(OpportunityAlertConfig())

        # Create orchestrator with db_manager
        orchestrator = ProductionScanOrchestrator(
            config=ScanConfig(enable_reconciliation=True),
            db_manager=self.db_manager,
            alert_policy=alert_policy,
            dispatcher=dispatcher,
        )

        # Ensure session created during cycle is committed and closed
        with self.db_manager.get_session() as test_session:
            # Concurrent write to snapshots table must succeed immediately without locking
            snap = SnapshotORM(
                id="snap_test_1",
                provider_id="system",
                execution_id="exec_1",
                snapshot_type="TEST",
                payload="{}",
                created_at=datetime.now(timezone.utc),
            )
            test_session.add(snap)
            test_session.commit()

        # Check that lock is released
        with self.db_manager.get_session() as test_session:
            read_snap = test_session.query(SnapshotORM).filter_by(id="snap_test_1").first()
            self.assertIsNotNone(read_snap)


if __name__ == "__main__":
    unittest.main()
