"""
Tests for Asynchronous Scan Triggering, Concurrency Safety, and Netlify 504 Elimination.
"""

import unittest
from unittest.mock import MagicMock, patch
import time
import threading
from datetime import datetime, timezone

from api.routes import APIRouter
from api.services import PlatformAPIService
from api.exceptions import APIError
from database.connection import DatabaseManager
from database.config import DatabaseConfig
from orchestration.models import (
    CycleStatus,
    ResourceMetrics,
    ScanConfig,
    ScanCycleResult,
)
from orchestration.scan_orchestrator import ProductionScanOrchestrator


class TestAsyncScanTriggerSuite(unittest.TestCase):
    """Test suite covering the asynchronous scan trigger contract (HTTP 202),
    lock reservation, background completion, failure reporting, and timeout prevention.
    """

    def setUp(self):
        self.db_manager = DatabaseManager(DatabaseConfig.default_sqlite_in_memory())
        self.db_manager.create_tables()
        self.service = PlatformAPIService(db_manager=self.db_manager)
        self.router = APIRouter(service=self.service)

    def tearDown(self):
        # Clean up any lingering background thread
        self.service.wait_for_current_scan(timeout=2.0)
        if self.service._scan_lock.locked():
            try:
                self.service._scan_lock.release()
            except Exception:
                pass

    def test_01_post_scan_run_returns_202_fast_regression(self):
        """Timeout regression test: POST /api/v1/scan/run returns HTTP 202 in < 1.0s
        even when the underlying scan cycle takes substantial time.
        """
        mock_orchestrator = MagicMock(spec=ProductionScanOrchestrator)

        def slow_scan(*args, **kwargs):
            time.sleep(0.5)
            return ScanCycleResult(
                execution_id=kwargs.get("execution_id", "scan_slow"),
                cycle_status=CycleStatus.SUCCESS,
                started_at="2026-09-14T00:00:00Z",
                completed_at="2026-09-14T00:00:01Z",
                duration_seconds=0.5,
                resource_metrics=ResourceMetrics(),
            )

        mock_orchestrator.run_scan_cycle.side_effect = slow_scan
        self.service.scan_orchestrator = mock_orchestrator

        start_time = time.monotonic()
        run_res = self.router.handle_post_run_scan()
        elapsed = time.monotonic() - start_time

        # Fast HTTP 202 response must return immediately without awaiting the 0.5s scan
        self.assertEqual(run_res.status_code, 202)
        self.assertLess(elapsed, 0.4, f"Expected trigger in <400ms, took {elapsed:.3f}s")
        self.assertEqual(run_res.data["status"], "SCANNING")
        self.assertTrue(run_res.data["execution_id"].startswith("scan_"))

        # Separately verify background execution completes
        self.assertTrue(self.service.wait_for_current_scan(timeout=2.0))
        status = self.service.get_scan_status()
        self.assertEqual(status["last_cycle_status"], "SUCCESS")
        self.assertFalse(status["is_scanning"])

    def test_02_response_contains_execution_id_and_scanning_status(self):
        """Verify response payload and metadata contain execution_id and SCANNING status."""
        mock_orchestrator = MagicMock(spec=ProductionScanOrchestrator)
        mock_orchestrator.run_scan_cycle.return_value = ScanCycleResult(
            execution_id="scan_mock_02",
            cycle_status=CycleStatus.SUCCESS,
            started_at="2026-09-14T00:00:00Z",
            completed_at="2026-09-14T00:00:01Z",
            duration_seconds=0.1,
            resource_metrics=ResourceMetrics(),
        )
        self.service.scan_orchestrator = mock_orchestrator

        run_res = self.router.handle_post_run_scan()
        self.assertEqual(run_res.status_code, 202)
        self.assertIn("execution_id", run_res.data)
        self.assertIn("execution_id", run_res.metadata)
        self.assertEqual(run_res.data["execution_id"], run_res.metadata["execution_id"])
        self.assertEqual(run_res.data["status"], "SCANNING")

        self.service.wait_for_current_scan(timeout=2.0)

    def test_03_get_scan_status_reports_scanning_while_active(self):
        """Verify GET /api/v1/scan/status reports status=SCANNING and is_scanning=True while active."""
        gate_event = threading.Event()
        mock_orchestrator = MagicMock(spec=ProductionScanOrchestrator)

        def gated_scan(*args, **kwargs):
            gate_event.wait(timeout=3.0)
            return ScanCycleResult(
                execution_id=kwargs.get("execution_id", "scan_gated"),
                cycle_status=CycleStatus.SUCCESS,
                started_at="2026-09-14T00:00:00Z",
                completed_at="2026-09-14T00:00:01Z",
                duration_seconds=0.1,
                resource_metrics=ResourceMetrics(),
            )

        mock_orchestrator.run_scan_cycle.side_effect = gated_scan
        self.service.scan_orchestrator = mock_orchestrator

        run_res = self.router.handle_post_run_scan()
        exec_id = run_res.data["execution_id"]

        # While scan is blocked on gate_event:
        status_res = self.router.handle_get_scan_status()
        self.assertEqual(status_res.status_code, 200)
        self.assertTrue(status_res.data["is_scanning"])
        self.assertEqual(status_res.data["status"], "SCANNING")
        self.assertEqual(status_res.data["current_execution_id"], exec_id)
        self.assertEqual(status_res.data["last_cycle_status"], "SCANNING")

        # Unblock scan and wait for completion
        gate_event.set()
        self.assertTrue(self.service.wait_for_current_scan(timeout=2.0))

        # Now status must report READY and SUCCESS
        final_status = self.router.handle_get_scan_status()
        self.assertFalse(final_status.data["is_scanning"])
        self.assertEqual(final_status.data["status"], "READY")
        self.assertEqual(final_status.data["last_cycle_status"], "SUCCESS")
        self.assertEqual(final_status.data["last_scan_id"], exec_id)

    def test_04_concurrency_lock_second_request_rejected_409(self):
        """Verify two simultaneous requests do not start two scans; second receives 409 Conflict."""
        gate_event = threading.Event()
        mock_orchestrator = MagicMock(spec=ProductionScanOrchestrator)

        def gated_scan(*args, **kwargs):
            gate_event.wait(timeout=3.0)
            return ScanCycleResult(
                execution_id=kwargs.get("execution_id", "scan_concur"),
                cycle_status=CycleStatus.SUCCESS,
                started_at="2026-09-14T00:00:00Z",
                completed_at="2026-09-14T00:00:01Z",
                duration_seconds=0.1,
                resource_metrics=ResourceMetrics(),
            )

        mock_orchestrator.run_scan_cycle.side_effect = gated_scan
        self.service.scan_orchestrator = mock_orchestrator

        # Request 1 starts
        res1 = self.router.handle_post_run_scan()
        self.assertEqual(res1.status_code, 202)

        # Request 2 must receive 409 Conflict immediately
        res2 = self.router.handle_post_run_scan()
        self.assertEqual(res2.status_code, 409)
        self.assertIn("already in progress", res2.errors[0])

        gate_event.set()
        self.assertTrue(self.service.wait_for_current_scan(timeout=2.0))

    def test_05_failed_background_scan_becomes_failed_in_status(self):
        """Verify that an exception in background execution marks status as ERROR and last_cycle_status=FAILED."""
        mock_orchestrator = MagicMock(spec=ProductionScanOrchestrator)
        mock_orchestrator.run_scan_cycle.side_effect = RuntimeError("Network connection reset by peer")
        self.service.scan_orchestrator = mock_orchestrator

        run_res = self.router.handle_post_run_scan()
        self.assertEqual(run_res.status_code, 202)
        exec_id = run_res.data["execution_id"]

        # Await worker termination
        self.assertTrue(self.service.wait_for_current_scan(timeout=2.0))

        # Lock must be released
        self.assertFalse(self.service._is_scanning)
        self.assertFalse(self.service._scan_lock.locked())

        # Status must expose ERROR / FAILED
        status = self.service.get_scan_status()
        self.assertEqual(status["status"], "ERROR")
        self.assertEqual(status["last_cycle_status"], "FAILED")
        self.assertEqual(status["last_scan_id"], exec_id)

    def test_06_execution_id_passed_into_run_scan_cycle_for_correlation(self):
        """Verify execution_id generated at trigger time is passed into run_scan_cycle."""
        mock_orchestrator = MagicMock(spec=ProductionScanOrchestrator)
        mock_orchestrator.run_scan_cycle.return_value = ScanCycleResult(
            execution_id="scan_placeholder",
            cycle_status=CycleStatus.SUCCESS,
            started_at="2026-09-14T00:00:00Z",
            completed_at="2026-09-14T00:00:01Z",
            duration_seconds=0.1,
            resource_metrics=ResourceMetrics(),
        )
        self.service.scan_orchestrator = mock_orchestrator

        run_res = self.router.handle_post_run_scan()
        trigger_id = run_res.data["execution_id"]

        self.assertTrue(self.service.wait_for_current_scan(timeout=2.0))

        mock_orchestrator.run_scan_cycle.assert_called_once()
        call_kwargs = mock_orchestrator.run_scan_cycle.call_args[1]
        self.assertEqual(call_kwargs.get("execution_id"), trigger_id)

    def test_07_synchronous_run_scan_still_supported(self):
        """Verify direct synchronous call to service.run_scan() works for internal callers."""
        mock_orchestrator = MagicMock(spec=ProductionScanOrchestrator)
        mock_orchestrator.run_scan_cycle.return_value = ScanCycleResult(
            execution_id="scan_sync_direct",
            cycle_status=CycleStatus.SUCCESS,
            started_at="2026-09-14T00:00:00Z",
            completed_at="2026-09-14T00:00:01Z",
            duration_seconds=0.05,
            resource_metrics=ResourceMetrics(markets_evaluated=12),
        )
        self.service.scan_orchestrator = mock_orchestrator

        result = self.service.run_scan()
        self.assertEqual(result["execution_id"], "scan_sync_direct")
        self.assertEqual(result["cycle_status"], "SUCCESS")
        self.assertEqual(self.service.get_scan_status()["status"], "READY")


if __name__ == "__main__":
    unittest.main()
