"""
Tests for Asynchronous Global Props Scanner Triggering, Concurrency Safety, and Netlify 504 Elimination.
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


class TestAsyncGlobalPropsScanTriggerSuite(unittest.TestCase):
    """Test suite covering the asynchronous global props scan trigger contract (HTTP 202),
    concurrency lock, background worker lifecycle, failure reporting, and Netlify 504 elimination.
    """

    def setUp(self):
        self.db_manager = DatabaseManager(DatabaseConfig.default_sqlite_in_memory())
        self.db_manager.create_tables()
        self.service = PlatformAPIService(db_manager=self.db_manager)
        self.router = APIRouter(service=self.service)

    def tearDown(self):
        # Clean up any lingering background thread
        self.service.wait_for_current_props_scan(timeout=2.0)
        if self.service._props_scan_lock.locked():
            try:
                self.service._props_scan_lock.release()
            except Exception:
                pass
        self.service._is_props_scanning = False
        self.service._props_scanner_status = "READY"

    def test_01_post_global_scan_returns_202_fast_regression(self):
        """Netlify timeout regression test: POST /api/v1/props/global-scan returns HTTP 202 in < 0.4s
        even when the underlying props scan cycle takes substantial time.
        """
        def slow_props_scan(*args, **kwargs):
            time.sleep(0.5)
            return {
                "status": "SUCCESS",
                "execution_id": kwargs.get("execution_id", "props_slow"),
                "qualified_count": 2,
                "total_qualified_matching_filter": 2,
                "qualified_opportunities": [],
            }

        with patch.object(self.service, "scan_global_props", side_effect=slow_props_scan):
            start_time = time.monotonic()
            run_res = self.router.handle_post_global_props_scan({"props_scope": "ALL", "scan_mode": "NORMAL"})
            elapsed = time.monotonic() - start_time

            # Fast HTTP 202 response must return immediately without awaiting the 0.5s scan
            self.assertEqual(run_res.status_code, 202)
            self.assertLess(elapsed, 0.4, f"Expected trigger in <400ms, took {elapsed:.3f}s")
            self.assertEqual(run_res.data["status"], "SCANNING")
            self.assertTrue(run_res.data["execution_id"].startswith("props_scan_"))

            # Background execution completes safely
            self.assertTrue(self.service.wait_for_current_props_scan(timeout=2.0))
            status = self.service.get_props_scan_status()
            self.assertEqual(status["last_scan_status"], "SUCCESS")
            self.assertFalse(status["is_scanning"])

    def test_02_response_contains_execution_id_and_scanning_status(self):
        """Verify response payload and metadata contain execution_id, scan_mode, and SCANNING status."""
        def quick_scan(*args, **kwargs):
            return {
                "status": "SUCCESS",
                "execution_id": kwargs.get("execution_id", "props_quick"),
                "qualified_count": 0,
                "qualified_opportunities": [],
            }

        with patch.object(self.service, "scan_global_props", side_effect=quick_scan):
            run_res = self.router.handle_post_global_props_scan({"props_scope": "PLAYER", "scan_mode": "ULTRA"})
            self.assertEqual(run_res.status_code, 202)
            self.assertIn("execution_id", run_res.data)
            self.assertIn("execution_id", run_res.metadata)
            self.assertEqual(run_res.data["execution_id"], run_res.metadata["execution_id"])
            self.assertEqual(run_res.data["status"], "SCANNING")
            self.assertEqual(run_res.data["scan_mode"], "ULTRA")
            self.assertIn("started_at", run_res.data)

            self.service.wait_for_current_props_scan(timeout=2.0)

    def test_03_get_props_scan_status_lifecycle(self):
        """Verify GET /api/v1/props/scan/status reports status=SCANNING and is_scanning=True while active,
        and transitions to READY / SUCCESS after completion.
        """
        gate_event = threading.Event()

        def gated_scan(*args, **kwargs):
            gate_event.wait(timeout=3.0)
            return {
                "status": "SUCCESS",
                "execution_id": kwargs.get("execution_id", "props_gated"),
                "qualified_count": 1,
                "qualified_opportunities": [],
            }

        # Initial status before any scan
        initial_status = self.router.handle_get_props_scan_status()
        self.assertEqual(initial_status.status_code, 200)
        self.assertEqual(initial_status.data["status"], "READY")
        self.assertFalse(initial_status.data["is_scanning"])

        with patch.object(self.service, "scan_global_props", side_effect=gated_scan):
            run_res = self.router.handle_post_global_props_scan({"props_scope": "ALL"})
            exec_id = run_res.data["execution_id"]

            # While props scan is blocked on gate_event:
            status_res = self.router.handle_get_props_scan_status()
            self.assertEqual(status_res.status_code, 200)
            self.assertTrue(status_res.data["is_scanning"])
            self.assertEqual(status_res.data["status"], "SCANNING")
            self.assertEqual(status_res.data["current_execution_id"], exec_id)

            # Unblock scan and wait for completion
            gate_event.set()
            self.assertTrue(self.service.wait_for_current_props_scan(timeout=2.0))

            # Final status must report READY and SUCCESS
            final_status = self.router.handle_get_props_scan_status()
            self.assertFalse(final_status.data["is_scanning"])
            self.assertEqual(final_status.data["status"], "READY")
            self.assertEqual(final_status.data["last_scan_status"], "SUCCESS")
            self.assertEqual(final_status.data["last_cycle_status"], "SUCCESS")
            self.assertEqual(final_status.data["last_scan_id"], exec_id)

    def test_04_concurrency_lock_second_request_rejected_409(self):
        """Verify two simultaneous requests do not start two scans; second receives 409 Conflict."""
        gate_event = threading.Event()

        def gated_scan(*args, **kwargs):
            gate_event.wait(timeout=3.0)
            return {
                "status": "SUCCESS",
                "execution_id": kwargs.get("execution_id", "props_concur"),
                "qualified_count": 0,
                "qualified_opportunities": [],
            }

        with patch.object(self.service, "scan_global_props", side_effect=gated_scan):
            # Request 1 starts
            res1 = self.router.handle_post_global_props_scan()
            self.assertEqual(res1.status_code, 202)

            # Request 2 must receive 409 Conflict immediately
            res2 = self.router.handle_post_global_props_scan()
            self.assertEqual(res2.status_code, 409)
            self.assertIn("already in progress", res2.errors[0])

            # Release lock and wait for completion
            gate_event.set()
            self.assertTrue(self.service.wait_for_current_props_scan(timeout=2.0))

            # Request 3 should now be accepted since previous scan finished
            res3 = self.router.handle_post_global_props_scan()
            self.assertEqual(res3.status_code, 202)
            self.assertTrue(self.service.wait_for_current_props_scan(timeout=2.0))

    def test_05_failed_background_scan_becomes_failed_in_status(self):
        """Verify that an exception in background execution marks status as ERROR and last_scan_status=FAILED."""
        def failing_scan(*args, **kwargs):
            raise RuntimeError("StatsHub connection timed out after 3 retries")

        with patch.object(self.service, "scan_global_props", side_effect=failing_scan):
            run_res = self.router.handle_post_global_props_scan()
            self.assertEqual(run_res.status_code, 202)
            exec_id = run_res.data["execution_id"]

            # Await worker termination
            self.assertTrue(self.service.wait_for_current_props_scan(timeout=2.0))

            # Lock must be released
            self.assertFalse(self.service._is_props_scanning)
            self.assertFalse(self.service._props_scan_lock.locked())

            # Status must expose ERROR / FAILED with error message
            status = self.service.get_props_scan_status()
            self.assertEqual(status["status"], "ERROR")
            self.assertEqual(status["last_scan_status"], "FAILED")
            self.assertEqual(status["last_cycle_status"], "FAILED")
            self.assertEqual(status["last_scan_id"], exec_id)
            self.assertIn("StatsHub connection timed out", status["error_message"])

    def test_06_normal_parameters_preserved(self):
        """Verify NORMAL mode parameters (scope, min_ev) are correctly forwarded to scan_global_props."""
        mock_scan = MagicMock(return_value={"status": "SUCCESS", "qualified_count": 0, "qualified_opportunities": []})

        with patch.object(self.service, "scan_global_props", mock_scan):
            run_res = self.router.handle_post_global_props_scan({
                "props_scope": "PLAYER",
                "min_ev_percent": 3.5,
                "scan_mode": "NORMAL",
            })
            self.assertEqual(run_res.status_code, 202)
            self.assertEqual(run_res.data["scan_mode"], "NORMAL")

            self.assertTrue(self.service.wait_for_current_props_scan(timeout=2.0))
            mock_scan.assert_called_once()
            call_kwargs = mock_scan.call_args[1]
            self.assertEqual(call_kwargs["scope_params"]["props_scope"], "PLAYER")
            self.assertEqual(call_kwargs["scope_params"]["min_ev_percent"], 3.5)
            self.assertEqual(call_kwargs["scope_params"]["scan_mode"], "NORMAL")
            self.assertEqual(call_kwargs["execution_id"], run_res.data["execution_id"])

    def test_07_ultra_parameters_preserved(self):
        """Verify ULTRA mode parameters are correctly forwarded to scan_global_props."""
        mock_scan = MagicMock(return_value={"status": "SUCCESS", "qualified_count": 0, "qualified_opportunities": []})

        with patch.object(self.service, "scan_global_props", mock_scan):
            run_res = self.router.handle_post_global_props_scan({
                "props_scope": "ALL",
                "min_ev_percent": 0.0,
                "scan_mode": "ULTRA",
            })
            self.assertEqual(run_res.status_code, 202)
            self.assertEqual(run_res.data["scan_mode"], "ULTRA")

            self.assertTrue(self.service.wait_for_current_props_scan(timeout=2.0))
            mock_scan.assert_called_once()
            call_kwargs = mock_scan.call_args[1]
            self.assertEqual(call_kwargs["scope_params"]["scan_mode"], "ULTRA")
            self.assertEqual(call_kwargs["scope_params"]["min_ev_percent"], 0.0)

    def test_08_synchronous_scan_global_props_still_supported(self):
        """Verify direct synchronous call to service.scan_global_props() works for internal callers."""
        from scanner.global_props_scanner import GlobalScanResult, GlobalScanFunnelMetrics
        mock_result = GlobalScanResult(
            scope={"props_scope": "PLAYER"},
            budget={},
            status="SUCCESS",
            funnel_metrics=GlobalScanFunnelMetrics(),
            qualified_opportunities=[],
            diagnostic_candidates=[],
            duration_ms=50.0,
        )
        with patch("scanner.global_props_scanner.GlobalPropsScanner.execute_scan", return_value=mock_result):
            result = self.service.scan_global_props(scope_params={"props_scope": "PLAYER"})
            self.assertEqual(result["status"], "SUCCESS")
            self.assertEqual(result["qualified_count"], 0)
            self.assertFalse(self.service._is_props_scanning)
            self.assertFalse(self.service._props_scan_lock.locked())
            status = self.service.get_props_scan_status()
            self.assertEqual(status["status"], "READY")
            self.assertEqual(status["last_scan_status"], "SUCCESS")

    def test_09_existing_global_props_results_contract(self):
        """Verify GET /api/v1/props/global-results retrieves cached results without triggering re-scan."""
        cached_data = {
            "status": "SUCCESS",
            "scanned_at": "2026-09-14T03:00:00Z",
            "scan_mode": "ULTRA",
            "qualified_count": 1,
            "total_qualified_matching_filter": 1,
            "qualified_opportunities": [
                {
                    "canonical_prop_key": "prop:saka:shots:1.5:OVER",
                    "prop_type": "PLAYER",
                    "player_name": "Bukayo Saka",
                    "team": "Arsenal",
                    "stat_type": "SHOTS",
                    "line": 1.5,
                    "side": "OVER",
                    "net_ev_pct": 6.2,
                    "status": "QUALIFIED",
                }
            ],
            "diagnostic_candidates": [],
            "funnel_metrics": {"qualified_count": 1},
        }
        PlatformAPIService._cached_global_props_results = cached_data

        with patch.object(self.service, "scan_global_props") as mock_scan:
            res = self.router.handle_get_global_props_results(props_scope="PLAYER", min_net_ev=5.0)
            mock_scan.assert_not_called()
            self.assertEqual(res.status_code, 200)
            self.assertEqual(res.data["total_qualified_matching_filter"], 1)
            self.assertEqual(len(res.data["qualified_opportunities"]), 1)
    def test_10_independent_locks_between_main_and_props_scanners(self):
        """Verify Main Scanner and Global Props Scanner have completely independent locks and lifecycles."""
        props_gate = threading.Event()
        main_gate = threading.Event()

        def gated_props_scan(*args, **kwargs):
            props_gate.wait(timeout=3.0)
            return {"status": "SUCCESS", "execution_id": "props_independent", "qualified_count": 0}

        def gated_main_scan(*args, **kwargs):
            main_gate.wait(timeout=3.0)
            from orchestration.models import ScanCycleResult, CycleStatus, ResourceMetrics
            return ScanCycleResult(
                execution_id="main_independent",
                cycle_status=CycleStatus.SUCCESS,
                started_at="2026-09-14T00:00:00Z",
                completed_at="2026-09-14T00:00:01Z",
                duration_seconds=0.1,
                resource_metrics=ResourceMetrics(),
            )

        mock_orchestrator = MagicMock()
        mock_orchestrator.run_scan_cycle.side_effect = gated_main_scan
        self.service.scan_orchestrator = mock_orchestrator

        with patch.object(self.service, "scan_global_props", side_effect=gated_props_scan):
            # 1. Start props scan
            res_props = self.router.handle_post_global_props_scan()
            self.assertEqual(res_props.status_code, 202)
            self.assertTrue(self.service._is_props_scanning)
            self.assertTrue(self.service._props_scan_lock.locked())

            # 2. Main scanner must NOT be locked; can be triggered concurrently
            res_main = self.router.handle_post_run_scan()
            self.assertEqual(res_main.status_code, 202)
            self.assertTrue(self.service._is_scanning)
            self.assertTrue(self.service._scan_lock.locked())

            # 3. Both are scanning simultaneously
            self.assertTrue(self.service._is_props_scanning)
            self.assertTrue(self.service._is_scanning)

            # 4. Release gates
            props_gate.set()
            main_gate.set()

            self.assertTrue(self.service.wait_for_current_props_scan(timeout=2.0))
            self.assertTrue(self.service.wait_for_current_scan(timeout=2.0))

            # 5. Both completed and locks released
            self.assertFalse(self.service._is_props_scanning)
            self.assertFalse(self.service._is_scanning)
            self.assertFalse(self.service._props_scan_lock.locked())
            self.assertFalse(self.service._scan_lock.locked())


if __name__ == "__main__":
    unittest.main()
