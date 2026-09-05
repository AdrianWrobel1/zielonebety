"""
Unit and Integration Tests for ULTRA SCAN API Router Handlers.

Covers:
- POST /api/v1/scan/ultra (manual trigger, parameter parsing, 409 conflict protection)
- GET /api/v1/scan/ultra/latest (latest scan result retrieval, NOT_RUN when not run)
- Router handler dispatching
- Error handling and status codes
"""

import unittest
from unittest.mock import MagicMock

from api.routes import APIRouter
from api.services import PlatformAPIService
from api.exceptions import APIError


class TestUltraScanAPIRouter(unittest.TestCase):

    def setUp(self):
        self.mock_service = MagicMock(spec=PlatformAPIService)
        self.router = APIRouter(service=self.mock_service)

    def test_router_handle_post_ultra_scan_success(self):
        self.mock_service.run_ultra_scan.return_value = {
            "execution_id": "ultra_20260904_100000",
            "status": "SUCCESS",
            "target_date": "2026-09-04",
            "counts": {"top_opportunities": 5, "surebets": 1, "valuebets": 3, "player_props": 2},
        }

        body = {
            "target_date": "2026-09-04",
            "min_ev_percent": 2.5,
            "enable_props": True,
            "dispatch_telegram": False,
        }
        res = self.router.handle_post_ultra_scan(body)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["execution_id"], "ultra_20260904_100000")
        self.assertEqual(res.data["status"], "SUCCESS")

        expected_scope = {
            "target_date": "2026-09-04",
            "min_ev_percent": 2.5,
            "enable_props": True,
        }
        self.mock_service.run_ultra_scan.assert_called_once_with(
            scope_params=expected_scope,
            manual=True,
            dispatch_telegram=False,
        )

    def test_router_handle_post_ultra_scan_concurrency_conflict(self):
        self.mock_service.run_ultra_scan.side_effect = APIError(
            "Scan is already in progress. Please wait for the current cycle to complete.",
            status_code=409,
        )

        res = self.router.handle_post_ultra_scan({})
        self.assertEqual(res.status_code, 409)
        self.assertTrue(any("already in progress" in err for err in res.errors))

    def test_router_handle_get_latest_ultra_scan_found(self):
        sample_result = {
            "execution_id": "ultra_20260904_100000",
            "status": "SUCCESS",
            "funnel": {"discovered_today_events": 85},
            "top_opportunities": [],
        }
        self.mock_service.get_latest_ultra_scan.return_value = sample_result

        res = self.router.handle_get_latest_ultra_scan()
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["execution_id"], "ultra_20260904_100000")

    def test_router_handle_get_latest_ultra_scan_not_found(self):
        self.mock_service.get_latest_ultra_scan.return_value = None

        res = self.router.handle_get_latest_ultra_scan()
        self.assertEqual(res.status_code, 200)
        self.assertIsNone(res.data)
        self.assertEqual(res.metadata.get("status"), "NOT_RUN")


if __name__ == "__main__":
    unittest.main()
