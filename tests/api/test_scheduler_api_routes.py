"""
Targeted tests for Scheduler REST API endpoints.
Validates configure schema, invalid schedule rejection (400),
run-now execution (200), and status reporting.
"""

import unittest
from unittest.mock import MagicMock, patch

from api.routes import APIRouter
from api.services import PlatformAPIService
from database.config import DatabaseConfig
from database.connection import DatabaseManager
from orchestration.scheduler import ScanScheduler


class TestSchedulerAPIRoutes(unittest.TestCase):

    def setUp(self):
        self.db_manager = DatabaseManager(DatabaseConfig.default_sqlite_in_memory())
        self.db_manager.create_tables()
        self.service = PlatformAPIService(db_manager=self.db_manager)
        self.service.scheduler.disable()
        self.service.scheduler.stop()
        self.router = APIRouter(service=self.service)

    def tearDown(self):
        self.service.scheduler.disable()
        self.service.scheduler.stop()

    def test_get_scheduler_status(self):
        res = self.router.handle_get_scheduler_status()
        self.assertEqual(res.status_code, 200)
        data = res.data
        self.assertIn("enabled", data)
        self.assertIn("scanners", data)
        self.assertIn("schedule", data)
        self.assertIn("active_window", data)

    def test_configure_scheduler_valid_scanners_and_schedule(self):
        payload = {
            "enabled": True,
            "scanners": {
                "ultra": True,
                "global_props": False,
            },
            "schedule": [
                {"start_time": "00:00", "end_time": "12:00", "interval_minutes": 60},
                {"start_time": "12:00", "end_time": "24:00", "interval_minutes": 15},
            ],
        }
        res = self.router.handle_post_scheduler_configure(payload)
        self.assertEqual(res.status_code, 200)
        data = res.data
        self.assertTrue(data["enabled"])
        self.assertTrue(data["scanners"]["ultra"])
        self.assertFalse(data["scanners"]["global_props"])
        self.assertEqual(len(data["schedule"]), 2)
        self.assertEqual(data["schedule"][0]["interval_minutes"], 60)
        self.assertEqual(data["schedule"][1]["interval_minutes"], 15)

    def test_configure_scheduler_rejects_overlapping_schedule(self):
        payload = {
            "schedule": [
                {"start_time": "00:00", "end_time": "14:00", "interval_minutes": 60},
                {"start_time": "12:00", "end_time": "20:00", "interval_minutes": 15},  # overlaps!
            ],
        }
        res = self.router.handle_post_scheduler_configure(payload)
        self.assertEqual(res.status_code, 400)
        self.assertTrue(any("overlap" in str(e).lower() for e in res.errors))

    def test_scheduler_run_now_endpoint_returns_cycle_result(self):
        mock_cycle = {
            "execution_id": "test_api_cycle",
            "cycle_status": "SUCCESS",
            "status": "SUCCESS",
            "duration_seconds": 1.2,
            "scanners": {
                "ultra": {"enabled": True, "status": "SUCCESS"},
                "global_props": {"enabled": True, "status": "SUCCESS"},
            },
        }
        with patch.object(self.service.scheduler, "run_scan_now", return_value=mock_cycle):
            res = self.router.handle_post_scheduler_run_now()
            self.assertEqual(res.status_code, 200)
            self.assertEqual(res.data["execution_id"], "test_api_cycle")
            self.assertEqual(res.data["cycle_status"], "SUCCESS")


if __name__ == "__main__":
    unittest.main()
