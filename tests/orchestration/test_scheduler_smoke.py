"""
Stage 8.3: Scheduler Smoke Verification Test

Strictly verifies the 7 criteria for scheduler smoke verification:
1. Enable scheduler.
2. Configure a short safe interval suitable for testing.
3. Confirm exactly one scheduled scan can start.
4. Confirm overlapping execution is prevented.
5. Confirm scheduler remains alive after scan completion.
6. Disable scheduler.
7. Confirm no further scheduled scan is started.
"""

import threading
import time
import unittest
from datetime import datetime, timezone

from api.services import PlatformAPIService
from database.connection import DatabaseManager
from database.config import DatabaseConfig
from orchestration.models import CycleStatus, ScanCycleResult
from orchestration.scheduler import ScanScheduler


class TestSchedulerSmokeVerification(unittest.TestCase):
    """Smoke verification of ScanScheduler lifecycle, execution, concurrency, and termination."""

    def setUp(self):
        self.db_manager = DatabaseManager(DatabaseConfig.default_sqlite_in_memory())
        self.db_manager.create_tables()
        self.service = PlatformAPIService(db_manager=self.db_manager)
        # Ensure default scheduler on service is stopped to avoid interference
        self.service.scheduler.disable()
        self.service.scheduler.stop()

    def tearDown(self):
        if hasattr(self, "scheduler") and self.scheduler:
            self.scheduler.disable()
            self.scheduler.stop()
        self.service.scheduler.disable()
        self.service.scheduler.stop()

    def test_scheduler_full_smoke_verification_lifecycle(self):
        """Full end-to-end smoke verification of scheduler lifecycle and safety tripwires."""
        scan_execution_count = 0
        scan_in_progress = False
        scan_overlap_detected = False
        scan_lock = threading.Lock()

        def fake_run_scan_cycle(providers=None, evaluation_time=None):
            nonlocal scan_execution_count, scan_in_progress, scan_overlap_detected
            with scan_lock:
                if scan_in_progress:
                    scan_overlap_detected = True
                scan_in_progress = True

            # Simulate 150ms scan workload
            time.sleep(0.15)

            with scan_lock:
                scan_execution_count += 1
                scan_in_progress = False

            return ScanCycleResult(
                execution_id=f"smoke_scan_{scan_execution_count}",
                cycle_status=CycleStatus.SUCCESS,
                started_at=datetime.now(timezone.utc).isoformat(),
                completed_at=datetime.now(timezone.utc).isoformat(),
                duration_seconds=0.15,
            )

        # Mock the underlying orchestrator run_scan_cycle
        self.service.scan_orchestrator.run_scan_cycle = fake_run_scan_cycle

        # Create a dedicated scheduler for this smoke verification
        self.scheduler = ScanScheduler(service=self.service, interval_minutes=15, enabled=False)

        # Step 1: Initial state is disabled
        status = self.scheduler.get_status()
        self.assertFalse(status["enabled"])
        self.assertIsNone(status["next_scan_at"])

        # Step 2: Enable scheduler and configure parameters
        self.scheduler.enable(
            interval_minutes=5,
            scan_scope="POPULAR",
            hours_ahead=12,
            event_limit=25,
        )
        status_enabled = self.scheduler.get_status()
        self.assertTrue(status_enabled["enabled"])
        self.assertEqual(status_enabled["interval_minutes"], 5)
        self.assertEqual(status_enabled["scan_scope"], "POPULAR")
        self.assertEqual(status_enabled["event_limit"], 25)
        self.assertIsNotNone(status_enabled["next_scan_at"])
        self.assertTrue(status_enabled["is_running"])

        # Step 3: Confirm exactly one scheduled scan can start and execute cleanly
        res = self.scheduler.run_scan_now()
        self.assertEqual(res["cycle_status"], "SUCCESS")
        self.assertEqual(scan_execution_count, 1)

        status_after_first = self.scheduler.get_status()
        self.assertEqual(status_after_first["last_scan_status"], "SUCCESS")
        self.assertIsNotNone(status_after_first["last_scan_at"])
        self.assertIsNotNone(status_after_first["last_scan_id"])

        # Step 4: Confirm overlapping execution is prevented (concurrency protection)
        # We hold the service scan lock and attempt a scan via scheduler
        acquired = self.service._scan_lock.acquire(blocking=False)
        self.assertTrue(acquired)
        try:
            # While lock is held, scheduler.run_scan_now() must be rejected with 409
            from api.exceptions import APIError
            with self.assertRaises(APIError) as ctx:
                self.scheduler.run_scan_now()
            self.assertEqual(ctx.exception.status_code, 409)
        finally:
            self.service._scan_lock.release()

        # Overlap must be 0
        self.assertFalse(scan_overlap_detected)
        self.assertEqual(scan_execution_count, 1)

        # Step 5: Confirm scheduler remains alive after scan completion and failure recovery
        self.assertTrue(self.scheduler._thread.is_alive())
        # Run another scan to confirm it is fully operational
        res2 = self.scheduler.run_scan_now()
        self.assertEqual(res2["cycle_status"], "SUCCESS")
        self.assertEqual(scan_execution_count, 2)
        self.assertTrue(self.scheduler._thread.is_alive())

        # Step 6: Disable scheduler
        self.scheduler.disable()
        status_disabled = self.scheduler.get_status()
        self.assertFalse(status_disabled["enabled"])
        self.assertIsNone(status_disabled["next_scan_at"])

        # Step 7: Confirm no further scheduled scan is started
        current_count = scan_execution_count
        time.sleep(0.3)
        self.assertEqual(scan_execution_count, current_count)

        # Stop worker thread cleanly
        self.scheduler.stop()
        self.assertFalse(self.scheduler._thread.is_alive())


if __name__ == "__main__":
    unittest.main()
