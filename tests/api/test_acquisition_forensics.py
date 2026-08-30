"""
Targeted tests for Acquisition Forensics and Scan Profiler Telemetry (Stage 47)
"""

import unittest
from unittest.mock import MagicMock

from orchestration.profiler import ScanExecutionProfiler, set_current_scan_profiler
from api.services import PlatformAPIService
from api.routes import APIRouter
from database.connection import DatabaseManager
from database.config import DatabaseConfig
from orchestration.models import ScanCycleResult, CycleStatus


class TestAcquisitionForensics(unittest.TestCase):

    def setUp(self):
        self.db_manager = DatabaseManager(DatabaseConfig.default_sqlite_in_memory())
        self.db_manager.create_tables()
        self.service = PlatformAPIService(db_manager=self.db_manager)
        self.router = APIRouter(service=self.service)

    def test_acquisition_forensics_breakdown(self):
        profiler = ScanExecutionProfiler(execution_id="forensics_test_01", scan_mode="NORMAL")
        set_current_scan_profiler(profiler)

        with profiler.trace_phase("acquisition", counters={"providers": ["superbet", "betclic", "odds_api"]}):
            # Superbet worker
            profiler.register_worker("superbet-worker-1", provider="superbet", role="detail_fetcher")
            profiler.record_worker_interval("superbet-worker-1", state="RATE_LIMIT_WAIT", start_rel_s=0.01, end_rel_s=0.03, task_id="sb_01")
            profiler.record_worker_interval("superbet-worker-1", state="WORKING", start_rel_s=0.03, end_rel_s=0.08, task_id="sb_01")
            profiler.record_worker_task_complete("superbet-worker-1", task_id="sb_01", duration_ms=50.0, success=True)
            profiler.record_request(
                provider="superbet",
                endpoint_category="detail_events",
                worker_id="superbet-worker-1",
                start_rel_s=0.03,
                end_rel_s=0.08,
                http_status=200,
                rate_limit_wait_ms=20.0,
                success=True,
                bytes_received=1024,
            )

            # Betclic worker
            profiler.register_worker("betclic-worker-1", provider="betclic", role="detail_fetcher")
            profiler.record_worker_interval("betclic-worker-1", state="WORKING", start_rel_s=0.02, end_rel_s=0.09, task_id="bc_01")
            profiler.record_worker_task_complete("betclic-worker-1", task_id="bc_01", duration_ms=70.0, success=True)
            profiler.record_request(
                provider="betclic",
                endpoint_category="detail_grpc",
                worker_id="betclic-worker-1",
                start_rel_s=0.02,
                end_rel_s=0.09,
                http_status=200,
                success=True,
                bytes_received=2048,
            )

            # Odds API request
            profiler.record_request(
                provider="odds_api",
                endpoint_category="odds_multi",
                worker_id="odds-api-worker",
                start_rel_s=0.01,
                end_rel_s=0.05,
                http_status=200,
                rate_limit_wait_ms=5.0,
                success=True,
                bytes_received=4096,
            )

        report = profiler.finish_scan()
        self.assertIn("acquisition_forensics", report)
        forensics = report["acquisition_forensics"]
        self.assertIn("superbet", forensics)
        self.assertIn("betclic", forensics)
        self.assertIn("odds_api", forensics)

        sb = forensics["superbet"]
        self.assertEqual(sb["worker_count"], 1)
        self.assertEqual(sb["requests_count"], 1)
        self.assertEqual(sb["tasks_completed"], 1)
        self.assertGreater(sb["total_work_seconds"], 0.0)
        self.assertGreater(sb["total_rate_limit_wait_seconds"], 0.0)

        bc = forensics["betclic"]
        self.assertEqual(bc["worker_count"], 1)
        self.assertEqual(bc["requests_count"], 1)
        self.assertEqual(bc["tasks_completed"], 1)

        oapi = forensics["odds_api"]
        self.assertEqual(oapi["requests_count"], 1)

    def test_profiler_reload_and_export_trace_id_linkage(self):
        # 1. No scan run -> 404
        res_none = self.router.handle_get_latest_trace()
        self.assertEqual(res_none.status_code, 404)

        # 2. Run scan cycle and verify exact trace_id linkage
        mock_result = ScanCycleResult(
            execution_id="scan_exec_999",
            cycle_status=CycleStatus.SUCCESS,
            started_at="2026-08-28T12:00:00Z",
            completed_at="2026-08-28T12:00:02Z",
            duration_seconds=2.0,
            diagnostics={
                "scan_trace": {
                    "trace_id": "trace_20260828_999",
                    "execution_id": "scan_exec_999",
                    "total_duration_wall_s": 2.0,
                    "phases": [
                        {
                            "name": "acquisition",
                            "phase_name": "acquisition",
                            "start_rel_s": 0.0,
                            "end_rel_s": 1.5,
                            "wall_clock_seconds": 1.5,
                            "duration_s": 1.5,
                            "memory_delta_mb": 0.5,
                        }
                    ],
                    "acquisition_forensics": {
                        "superbet": {"requests_count": 41, "worker_count": 8},
                        "betclic": {"requests_count": 40, "worker_count": 8},
                    },
                    "workers": [
                        {
                            "worker_id": "superbet-worker-1",
                            "provider": "superbet",
                            "completed_tasks": 5,
                            "failed_tasks": 0,
                            "total_active_time_s": 1.2,
                            "total_rate_limit_wait_s": 0.1,
                            "total_idle_time_s": 0.2,
                            "utilization_pct": 80.0,
                        }
                    ],
                    "requests": [],
                }
            }
        )

        self.service.scan_orchestrator.run_scan_cycle = MagicMock(return_value=mock_result)
        self.service.run_scan(scan_source="MANUAL")

        # 3. Fetch latest trace
        latest_res = self.router.handle_get_latest_trace()
        self.assertEqual(latest_res.status_code, 200)
        self.assertEqual(latest_res.data["trace_id"], "trace_20260828_999")
        self.assertEqual(latest_res.data["execution_id"], "scan_exec_999")

        # 4. Fetch trace by ID
        by_id_res = self.router.handle_get_trace_by_id("trace_20260828_999")
        self.assertEqual(by_id_res.status_code, 200)
        self.assertEqual(by_id_res.data["trace_id"], "trace_20260828_999")


if __name__ == "__main__":
    unittest.main()
