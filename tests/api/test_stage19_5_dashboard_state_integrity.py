"""
Stage 19.5 Regression Tests: Production Dashboard Scan State & API Communication Integrity
"""

import unittest
from unittest.mock import MagicMock, patch
from decimal import Decimal
import os

from api.routes import APIRouter
from api.services import PlatformAPIService, _serialize_scan_cycle_result
from api.fastapi_app import list_opportunities
from database.connection import DatabaseManager
from database.config import DatabaseConfig
from orchestration.models import (
    CycleStatus,
    ResourceMetrics,
    ScanCycleResult,
    StageTiming,
)
from orchestration.scan_orchestrator import ProductionScanOrchestrator
from providers.base.provider_result import ProviderResult
from providers.base.provider_state import ProviderState
from reference_odds.provider import TheOddsApiReferenceProvider


class TestStage195DashboardStateIntegrity(unittest.TestCase):
    """Test suite ensuring consistent dashboard scan state, API integrity, and error reporting."""

    def setUp(self):
        self.db_manager = DatabaseManager(DatabaseConfig.default_sqlite_in_memory())
        self.db_manager.create_tables()

        self.service = PlatformAPIService(db_manager=self.db_manager)
        self.router = APIRouter(service=self.service)

    def test_01_dashboard_scan_result_has_all_required_ui_fields(self):
        """Ensure serialized scan cycle result contains all keys required by renderDashboardView."""
        cycle_res = ScanCycleResult(
            execution_id="scan_stage19_5_test_001",
            cycle_status=CycleStatus.SUCCESS,
            started_at="2026-08-24T20:00:00Z",
            completed_at="2026-08-24T20:01:01Z",
            duration_seconds=61.8,
            stage_timings=StageTiming(
                acquisition_seconds=30.0,
                normalization_seconds=5.0,
                matching_seconds=10.0,
                detection_seconds=10.0,
                lifecycle_seconds=5.0,
                dispatch_seconds=1.8,
                total_duration_seconds=61.8,
            ),
            resource_metrics=ResourceMetrics(
                total_http_requests=10,
                detail_http_requests=5,
                events_discovered=161,
                events_selected=70,
                events_parsed=161,
                normalized_graphs=161,
                matched_events=12,
                markets_evaluated=45,
                peak_memory_mb=12.4,
            ),
            provider_results={
                "superbet": ProviderResult(
                    provider_name="superbet",
                    status=ProviderState.COMPLETED,
                    execution_duration=15.2,
                    discovered_objects=[{"id": i} for i in range(161)],
                    parsed_objects=[{"id": i} for i in range(161)],
                ),
                "betclic": ProviderResult(
                    provider_name="betclic",
                    status=ProviderState.COMPLETED,
                    execution_duration=14.8,
                    discovered_objects=[{"id": i} for i in range(135)],
                    parsed_objects=[{"id": i} for i in range(135)],
                ),
            },
            discovered_events_count=161,
            parsed_events_count=161,
            normalized_graphs_count=161,
            matched_events_count=12,
            detected_opportunities_count=0,
            diagnostics={
                "matching_diagnostic": {
                    "candidates_generated": 25,
                    "matched_events": 12,
                    "rejected_candidates": 13,
                    "rejection_reasons_breakdown": {"LOW_MATCH_SCORE": 10, "TEAM_NAME_MISMATCH": 3},
                    "explanation": "12 event(s) successfully matched across execution bookmakers.",
                }
            },
        )

        serialized = _serialize_scan_cycle_result(cycle_res)

        # Invariant checks for UI rendering
        self.assertEqual(serialized["execution_id"], "scan_stage19_5_test_001")
        self.assertEqual(serialized["cycle_status"], "SUCCESS")
        self.assertIn("pipeline_state", serialized)
        self.assertIn("matching_diagnostic", serialized)
        self.assertEqual(serialized["matching_diagnostic"]["candidates_generated"], 25)
        self.assertEqual(serialized["matching_diagnostic"]["matched_events"], 12)
        self.assertIn("bookmaker_coverage", serialized)
        self.assertIn("superbet", serialized["bookmaker_coverage"])
        self.assertIn("betclic", serialized["bookmaker_coverage"])
        self.assertIn("bet365", serialized["bookmaker_coverage"])
        self.assertIn("unibet", serialized["bookmaker_coverage"])
        self.assertIn("odds_api_telemetry", serialized)

    def test_02_scan_state_invariant_after_run(self):
        """Invariant: After successful scan, status, latest_scan, and history must share identity."""
        mock_orchestrator = MagicMock(spec=ProductionScanOrchestrator)
        cycle_res = ScanCycleResult(
            execution_id="scan_invariant_test_999",
            cycle_status=CycleStatus.SUCCESS,
            started_at="2026-08-24T20:00:00Z",
            completed_at="2026-08-24T20:01:00Z",
            duration_seconds=60.0,
            stage_timings=StageTiming(total_duration_seconds=60.0),
            resource_metrics=ResourceMetrics(
                events_discovered=100,
                events_selected=50,
                matched_events=5,
            ),
            provider_results={},
            discovered_events_count=100,
            parsed_events_count=100,
            normalized_graphs_count=100,
            matched_events_count=5,
            detected_opportunities_count=1,
        )
        mock_orchestrator.run_scan_cycle.return_value = cycle_res
        self.service.scan_orchestrator = mock_orchestrator

        # Run scan
        run_res = self.router.handle_post_run_scan()
        self.assertEqual(run_res.status_code, 200)

        # 1. Latest scan
        latest_res = self.router.handle_get_latest_scan()
        self.assertEqual(latest_res.status_code, 200)
        self.assertIsNotNone(latest_res.data)
        self.assertEqual(latest_res.data["execution_id"], "scan_invariant_test_999")
        self.assertEqual(latest_res.data["cycle_status"], "SUCCESS")

        # 2. Status
        status_res = self.router.handle_get_scan_status()
        self.assertEqual(status_res.status_code, 200)
        self.assertEqual(status_res.data["status"], "READY")
        self.assertFalse(status_res.data["is_scanning"])
        self.assertTrue(status_res.data["has_run"])
        self.assertEqual(status_res.data["last_scan_id"], "scan_invariant_test_999")
        self.assertEqual(status_res.data["last_cycle_status"], "SUCCESS")

        # 3. History
        history_res = self.router.handle_get_scan_history(limit=10)
        self.assertEqual(history_res.status_code, 200)
        self.assertGreaterEqual(len(history_res.data), 1)
        self.assertEqual(history_res.data[0]["execution_id"], "scan_invariant_test_999")
        self.assertEqual(history_res.data[0]["status"], "SUCCESS")

    def test_03_opportunities_query_parameter_compatibility(self):
        """Verify that FastAPI list_opportunities binds both 'type' and 'opportunity_type'."""
        # 1. Call via 'type'
        res_type = list_opportunities(type="SUREBET")
        self.assertEqual(res_type["status_code"], 200)

        # 2. Call via 'opportunity_type'
        res_opp_type = list_opportunities(opportunity_type="SUREBET")
        self.assertEqual(res_opp_type["status_code"], 200)

    def test_04_the_odds_api_reference_provider_unconfigured_behavior(self):
        """Verify unconfigured TheOddsApiReferenceProvider returns empty list without error or failure."""
        with patch.dict(os.environ, {}, clear=True):
            prov = TheOddsApiReferenceProvider(api_key=None)
            meta = prov.get_metadata()

            self.assertFalse(meta["has_api_key"])
            self.assertFalse(meta["is_configured"])
            self.assertEqual(meta["status"], "NOT_CONFIGURED")

            events = prov.fetch_reference_events(sport="soccer")
            self.assertEqual(events, [])
            quota = prov.get_quota_metrics()
            self.assertEqual(quota.errors_count, 0)

    def test_05_genuine_backend_failure_returns_error_envelope_and_resets_lock(self):
        """Verify genuine backend error returns 500 status and unlocks the scanner for future scans."""
        mock_orchestrator = MagicMock(spec=ProductionScanOrchestrator)
        mock_orchestrator.run_scan_cycle.side_effect = RuntimeError("Fatal scraper memory exhaustion")
        self.service.scan_orchestrator = mock_orchestrator

        run_res = self.router.handle_post_run_scan()
        self.assertEqual(run_res.status_code, 500)
        self.assertIn("Fatal scraper memory exhaustion", run_res.errors[0])

        # Ensure lock was released and status can be recovered
        self.assertFalse(self.service._is_scanning)


if __name__ == "__main__":
    unittest.main()
