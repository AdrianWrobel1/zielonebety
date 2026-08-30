"""
Unit & Integration Tests for Stage 8.1 Scanner Control & Dashboard Integration
"""

import unittest
from unittest.mock import MagicMock, patch
from decimal import Decimal
from datetime import datetime, timezone
import threading
import time

from api.routes import APIRouter
from api.services import PlatformAPIService, _sanitize_text
from api.exceptions import APIError
from database.connection import DatabaseManager
from database.config import DatabaseConfig
from orchestration.models import (
    CycleStatus,
    ResourceMetrics,
    ScanConfig,
    ScanCycleResult,
    StageTiming,
)
from orchestration.scan_orchestrator import ProductionScanOrchestrator
from providers.base.base_provider import BaseProvider
from providers.base.provider_result import ProviderResult
from providers.base.provider_state import ProviderState
from normalization.surebet import SurebetDetectionResult, SurebetOpportunity, SurebetLeg, SurebetStatus
from normalization.market_identity import CanonicalMarketKey, CanonicalMarketType
from normalization.selection_identity import CanonicalSelectionKey, CanonicalSelectionType


class MockControlledProvider(BaseProvider):
    """Controlled mock provider for scanner tests."""
    def __init__(self, name: str, state: ProviderState = ProviderState.COMPLETED, fail: bool = False):
        super().__init__(name=name)
        self._target_state = state
        self._fail = fail

    def fetch_events(self):
        if self._fail:
            raise RuntimeError(f"Simulated network timeout for provider '{self.name}'")
        return [{"id": f"{self.name}_ev1", "name": "Team A vs Team B"}]

    def parse_events(self, raw_data):
        if self._fail:
            return []
        return [{"id": f"{self.name}_ev1", "home": "Team A", "away": "Team B"}]

    def validate_events(self, parsed_data):
        return parsed_data

    def check_health(self):
        return {"status": "HEALTHY", "circuit_breaker": "CLOSED", "error_rate_5m": "0.0%"}


class TestScannerControlSuite(unittest.TestCase):
    """Test suite covering Stage 8.1 Scanner Control API, concurrency protection, and telemetry serialization."""

    def setUp(self):
        self.db_manager = DatabaseManager(DatabaseConfig.default_sqlite_in_memory())
        self.db_manager.create_tables()

        self.service = PlatformAPIService(db_manager=self.db_manager)
        self.router = APIRouter(service=self.service)

    def test_01_initial_state_not_run(self):
        """Verify initial scanner status before any scan has been executed."""
        status_res = self.router.handle_get_scan_status()
        self.assertEqual(status_res.status_code, 200)
        self.assertEqual(status_res.data["status"], "READY")
        self.assertFalse(status_res.data["is_scanning"])
        self.assertFalse(status_res.data["has_run"])
        self.assertIsNone(status_res.data["last_scan_id"])
        self.assertEqual(status_res.data["last_cycle_status"], "NOT_RUN")

        latest_res = self.router.handle_get_latest_scan()
        self.assertEqual(latest_res.status_code, 200)
        self.assertIsNone(latest_res.data)
        self.assertEqual(latest_res.metadata.get("status"), "NOT_RUN")

    def test_02_successful_scan_cycle_execution_and_serialization(self):
        """Verify executing a successful scan cycle serializes all metrics, timings, and counts."""
        mock_orchestrator = MagicMock(spec=ProductionScanOrchestrator)
        cycle_res = ScanCycleResult(
            execution_id="scan_20260817_test_001",
            cycle_status=CycleStatus.SUCCESS,
            started_at="2026-08-17T12:00:00Z",
            completed_at="2026-08-17T12:00:02Z",
            duration_seconds=2.15,
            stage_timings=StageTiming(
                acquisition_seconds=1.20,
                normalization_seconds=0.15,
                matching_seconds=0.40,
                detection_seconds=0.25,
                lifecycle_seconds=0.10,
                dispatch_seconds=0.05,
                reconciliation_seconds=0.00,
                total_duration_seconds=2.15,
            ),
            resource_metrics=ResourceMetrics(
                total_http_requests=4,
                detail_http_requests=2,
                events_discovered=216,
                events_selected=70,
                events_parsed=70,
                normalized_graphs=70,
                matched_events=4,
                markets_evaluated=38,
                peak_memory_mb=5.65,
            ),
            provider_results={
                "superbet": ProviderResult(
                    provider_name="superbet",
                    status=ProviderState.COMPLETED,
                    execution_duration=0.85,
                    discovered_objects=[{"id": 1}],
                    parsed_objects=[{"id": 1}],
                ),
                "betclic": ProviderResult(
                    provider_name="betclic",
                    status=ProviderState.COMPLETED,
                    execution_duration=0.65,
                    discovered_objects=[{"id": 2}],
                    parsed_objects=[{"id": 2}],
                ),
            },
            discovered_events_count=216,
            parsed_events_count=70,
            normalized_graphs_count=70,
            matched_events_count=4,
            detected_opportunities_count=0,
            diagnostics={
                "nearest_opportunity": {
                    "event": "ev_pisa_empoli",
                    "market": "MATCH_WINNER",
                    "implied_probability_sum": "1.0185",
                    "margin_pct": "-1.85",
                    "distance_to_arbitrage": "0.0185",
                }
            },
        )
        mock_orchestrator.run_scan_cycle.return_value = cycle_res
        self.service.scan_orchestrator = mock_orchestrator

        run_res = self.router.handle_post_run_scan()
        self.assertEqual(run_res.status_code, 200)
        data = run_res.data

        self.assertEqual(data["execution_id"], "scan_20260817_test_001")
        self.assertEqual(data["cycle_status"], "SUCCESS")
        self.assertEqual(data["duration_seconds"], 2.15)
        self.assertEqual(data["selection_policy"], "Popular competitions")
        self.assertEqual(data["counts"]["discovered_events"], 216)
        self.assertEqual(data["counts"]["selected_events"], 70)
        self.assertEqual(data["counts"]["matched_events"], 4)
        self.assertEqual(data["resource_metrics"]["markets_evaluated"], 38)
        self.assertEqual(data["resource_metrics"]["peak_memory_mb"], 5.65)
        self.assertEqual(data["stage_timings"]["acquisition_seconds"], 1.20)
        self.assertEqual(data["stage_timings"]["matching_seconds"], 0.40)

        # Provider breakdown
        self.assertIn("superbet", data["provider_results"])
        self.assertIn("betclic", data["provider_results"])
        self.assertEqual(data["provider_results"]["superbet"]["status"], "COMPLETED")
        self.assertEqual(data["provider_results"]["betclic"]["status"], "COMPLETED")

    def test_03_zero_surebet_state_telemetry(self):
        """Verify zero-surebet state exposes evaluated markets and nearest opportunity telemetry."""
        mock_orchestrator = MagicMock(spec=ProductionScanOrchestrator)
        mock_orchestrator.run_scan_cycle.return_value = ScanCycleResult(
            execution_id="scan_zero_sb",
            cycle_status=CycleStatus.SUCCESS,
            started_at="2026-08-17T12:00:00Z",
            completed_at="2026-08-17T12:00:01Z",
            duration_seconds=1.1,
            resource_metrics=ResourceMetrics(markets_evaluated=45),
            detected_opportunities_count=0,
            diagnostics={
                "nearest_opportunity": {
                    "event": "canonical_madrid_barca",
                    "market": "1X2",
                    "implied_probability_sum": "1.0095",
                    "margin_pct": "-0.95",
                    "distance_to_arbitrage": "0.0095",
                }
            },
        )
        self.service.scan_orchestrator = mock_orchestrator

        run_res = self.router.handle_post_run_scan()
        self.assertEqual(run_res.status_code, 200)
        data = run_res.data

        self.assertEqual(data["counts"]["detected_opportunities"], 0)
        self.assertEqual(len(data["opportunities"]), 0)
        self.assertIsNotNone(data["nearest_opportunity"])
        self.assertEqual(data["nearest_opportunity"]["event"], "canonical_madrid_barca")
        self.assertEqual(data["nearest_opportunity"]["implied_probability_sum"], "1.0095")
        self.assertEqual(data["nearest_opportunity"]["margin_pct"], "-0.95")

    def test_04_partial_scan_state_handling(self):
        """Verify partial scan state when one provider fails is serialized properly without crash."""
        mock_orchestrator = MagicMock(spec=ProductionScanOrchestrator)
        mock_orchestrator.run_scan_cycle.return_value = ScanCycleResult(
            execution_id="scan_partial_001",
            cycle_status=CycleStatus.PARTIAL,
            started_at="2026-08-17T12:00:00Z",
            completed_at="2026-08-17T12:00:01Z",
            duration_seconds=1.05,
            provider_results={
                "superbet": ProviderResult(provider_name="superbet", status=ProviderState.COMPLETED, execution_duration=0.85),
                "betclic": ProviderResult(provider_name="betclic", status=ProviderState.FAILED, execution_duration=0.5, errors=["Acquisition timed out"]),
            },
            warnings=["Provider 'betclic' failed; preserved successful data from 'superbet'."],
        )
        self.service.scan_orchestrator = mock_orchestrator

        run_res = self.router.handle_post_run_scan()
        self.assertEqual(run_res.status_code, 200)
        data = run_res.data
        self.assertEqual(data["cycle_status"], "PARTIAL")
        self.assertEqual(data["provider_results"]["betclic"]["status"], "FAILED")
        self.assertIn("Provider 'betclic' failed", data["warnings"][0])

    def test_05_failed_scan_state_handling(self):
        """Verify failed scan state transitions status to ERROR and records errors cleanly."""
        mock_orchestrator = MagicMock(spec=ProductionScanOrchestrator)
        mock_orchestrator.run_scan_cycle.return_value = ScanCycleResult(
            execution_id="scan_failed_001",
            cycle_status=CycleStatus.FAILED,
            started_at="2026-08-17T12:00:00Z",
            completed_at="2026-08-17T12:00:01Z",
            duration_seconds=0.5,
            errors=["Both configured providers failed; aborting downstream stages."],
        )
        self.service.scan_orchestrator = mock_orchestrator

        run_res = self.router.handle_post_run_scan()
        self.assertEqual(run_res.status_code, 200)
        self.assertEqual(run_res.data["cycle_status"], "FAILED")

        # Check status endpoint reflects ERROR
        status_res = self.router.handle_get_scan_status()
        self.assertEqual(status_res.data["status"], "ERROR")
        self.assertEqual(status_res.data["last_cycle_status"], "FAILED")

    def test_06_duplicate_scan_prevention(self):
        """Verify application-level lock prevents concurrent/duplicate scans with 409 Conflict."""
        # Hold lock manually to simulate active scan
        self.service._scan_lock.acquire(blocking=False)
        self.service._is_scanning = True

        try:
            # Second scan trigger must be rejected
            res = self.router.handle_post_run_scan()
            self.assertEqual(res.status_code, 409)
            self.assertIn("already in progress", res.errors[0])
        finally:
            self.service._is_scanning = False
            self.service._scan_lock.release()

    def test_07_scan_history_accumulation(self):
        """Verify sequential scans accumulate in scan history in reverse chronological order."""
        mock_orchestrator = MagicMock(spec=ProductionScanOrchestrator)

        for i in range(1, 4):
            mock_orchestrator.run_scan_cycle.return_value = ScanCycleResult(
                execution_id=f"scan_hist_{i}",
                cycle_status=CycleStatus.SUCCESS,
                started_at=f"2026-08-17T12:0{i}:00Z",
                completed_at=f"2026-08-17T12:0{i}:02Z",
                duration_seconds=2.0 + i * 0.1,
                discovered_events_count=200 + i,
            )
            self.service.scan_orchestrator = mock_orchestrator
            self.router.handle_post_run_scan()

        hist_res = self.router.handle_get_scan_history(limit=5)
        self.assertEqual(hist_res.status_code, 200)
        history = hist_res.data
        self.assertEqual(len(history), 3)
        # Most recent should be first
        self.assertEqual(history[0]["execution_id"], "scan_hist_3")
        self.assertEqual(history[1]["execution_id"], "scan_hist_2")
        self.assertEqual(history[2]["execution_id"], "scan_hist_1")

    def test_08_sensitive_data_redaction(self):
        """Verify sensitive bot tokens, auth headers, and full local paths are redacted."""
        raw_warning = "Telegram error with token 123456789:ABCdefGhIjkLmNoPqRsTuVwXyZ on C:\\Users\\Adrian\\secrets.py"
        sanitized = _sanitize_text(raw_warning)
        self.assertNotIn("123456789:ABCdefGhIjkLmNoPqRsTuVwXyZ", sanitized)
        self.assertIn("[REDACTED_BOT_TOKEN]", sanitized)
        self.assertNotIn("C:\\Users\\Adrian\\secrets.py", sanitized)

        bearer_text = "Failed with Bearer eyJhbGciOiJIUzI1NiJ9.secret"
        sanitized_bearer = _sanitize_text(bearer_text)
        self.assertNotIn("eyJhbGciOiJIUzI1NiJ9.secret", sanitized_bearer)
        self.assertIn("[REDACTED_TOKEN]", sanitized_bearer)

    def test_09_opportunity_summary_serialization(self):
        """Verify detected surebets are cleanly serialized with legs, odds, and margins."""
        mock_orchestrator = MagicMock(spec=ProductionScanOrchestrator)

        market_key = CanonicalMarketKey(market_type=CanonicalMarketType.ONE_X_TWO)
        sel_key_1 = CanonicalSelectionKey(market_key=market_key, selection_type=CanonicalSelectionType.HOME)
        sel_key_x = CanonicalSelectionKey(market_key=market_key, selection_type=CanonicalSelectionType.DRAW)
        sel_key_2 = CanonicalSelectionKey(market_key=market_key, selection_type=CanonicalSelectionType.AWAY)

        opp = SurebetOpportunity(
            opportunity_id="opp_test_123",
            canonical_event_id="ev_real_barca",
            canonical_market_key=market_key,
            arbitrage_margin=Decimal("0.0345"),
            implied_probability_sum=Decimal("0.9655"),
            status=SurebetStatus.SUREBET,
            legs=(
                SurebetLeg(
                    canonical_selection_key=sel_key_1,
                    selection_type="HOME",
                    provider="superbet",
                    odds=Decimal("2.45"),
                    source_selection_id="s1",
                ),
                SurebetLeg(
                    canonical_selection_key=sel_key_x,
                    selection_type="DRAW",
                    provider="betclic",
                    odds=Decimal("3.60"),
                    source_selection_id="s2",
                ),
                SurebetLeg(
                    canonical_selection_key=sel_key_2,
                    selection_type="AWAY",
                    provider="superbet",
                    odds=Decimal("3.19"),
                    source_selection_id="s3",
                ),
            ),
        )

        mock_orchestrator.run_scan_cycle.return_value = ScanCycleResult(
            execution_id="scan_opp_001",
            cycle_status=CycleStatus.SUCCESS,
            started_at="2026-08-17T12:04:15Z",
            completed_at="2026-08-17T12:04:17Z",
            duration_seconds=2.15,
            detection_result=SurebetDetectionResult(opportunities=[opp]),
            detected_opportunities_count=1,
        )
        self.service.scan_orchestrator = mock_orchestrator

        run_res = self.router.handle_post_run_scan()
        self.assertEqual(run_res.status_code, 200)
        opps = run_res.data["opportunities"]
        self.assertEqual(len(opps), 1)
        self.assertEqual(opps[0]["opportunity_id"], "opp_test_123")
        self.assertEqual(opps[0]["arbitrage_margin_pct"], 3.45)
        self.assertEqual(len(opps[0]["legs"]), 3)
        self.assertEqual(opps[0]["legs"][0]["provider"], "superbet")
        self.assertEqual(opps[0]["legs"][0]["odds"], 2.45)

    def test_10_fastapi_http_endpoints_integration(self):
        """Verify all scan routes work end-to-end via FastAPI handler functions."""
        from fastapi import Response
        from api.fastapi_app import get_scan_status, get_latest_scan, get_scan_history, trigger_scan

        # GET /api/v1/scan/status
        status_json = get_scan_status()
        self.assertEqual(status_json["status_code"], 200)
        self.assertIn("status", status_json["data"])
        self.assertIn("is_scanning", status_json["data"])

        # GET /api/v1/scan/latest (initial NOT_RUN)
        latest_json = get_latest_scan()
        self.assertEqual(latest_json["status_code"], 200)

        # GET /api/v1/scan/history
        hist_json = get_scan_history(limit=5)
        self.assertEqual(hist_json["status_code"], 200)
        self.assertIsInstance(hist_json["data"], list)

        # POST /api/v1/scan/run
        resp = Response()
        from api.fastapi_app import service_instance
        with patch.object(service_instance.scan_orchestrator, "run_scan_cycle") as mock_scan:
            mock_scan.return_value = ScanCycleResult(
                execution_id="fastapi_test_scan",
                cycle_status=CycleStatus.SUCCESS,
                started_at="2026-08-17T12:00:00Z",
                completed_at="2026-08-17T12:00:01Z",
                duration_seconds=1.0,
            )
            scan_run_json = trigger_scan(response=resp)
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(scan_run_json["data"]["execution_id"], "fastapi_test_scan")


class TestSchedulerControlIntegration(unittest.TestCase):
    """Stage 8.3 — Scheduler API integration tests via PlatformAPIService."""

    def setUp(self):
        self.db_manager = DatabaseManager(DatabaseConfig.default_sqlite_in_memory())
        self.db_manager.create_tables()
        self.service = PlatformAPIService(db_manager=self.db_manager)
        self.router = APIRouter(service=self.service)

    def tearDown(self):
        # Stop scheduler background thread cleanly
        self.service.scheduler.disable()
        self.service.scheduler.stop()

    def test_sched_01_scheduler_status_initial_disabled(self):
        """Scheduler must be DISABLED on fresh PlatformAPIService init."""
        status_res = self.router.handle_get_scheduler_status()
        self.assertEqual(status_res.status_code, 200)
        self.assertFalse(status_res.data["enabled"])
        self.assertIsNone(status_res.data["next_scan_at"])
        self.assertIsNone(status_res.data["last_scan_at"])

    def test_sched_02_manual_scan_source_recorded(self):
        """run_scan(scan_source='MANUAL') must record 'MANUAL' in scan history."""
        mock_result = ScanCycleResult(
            execution_id="sched_manual_test",
            cycle_status=CycleStatus.SUCCESS,
            started_at="2026-08-17T14:00:00Z",
            completed_at="2026-08-17T14:00:01Z",
            duration_seconds=1.0,
        )
        with patch.object(self.service.scan_orchestrator, "run_scan_cycle", return_value=mock_result):
            self.service.run_scan(scan_source="MANUAL")

        history = self.service.get_scan_history(limit=1)
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["scan_source"], "MANUAL")

    def test_sched_03_conflict_409_while_scanning(self):
        """Attempting a second concurrent scan must raise APIError(409)."""
        # Manually hold the lock to simulate an in-progress scan
        acquired = self.service._scan_lock.acquire(blocking=False)
        self.assertTrue(acquired, "Should acquire lock in test setup")
        try:
            with self.assertRaises(APIError) as ctx:
                self.service.run_scan()
            self.assertEqual(ctx.exception.status_code, 409)
        finally:
            self.service._scan_lock.release()


class TestScanProfilerSuite(unittest.TestCase):
    """Stage 46 — Main Scan Execution Profiler & Trace API tests."""

    def setUp(self):
        self.db_manager = DatabaseManager(DatabaseConfig.default_sqlite_in_memory())
        self.db_manager.create_tables()
        self.service = PlatformAPIService(db_manager=self.db_manager)
        self.router = APIRouter(service=self.service)

    def test_prof_01_percentiles_calculation(self):
        from orchestration.profiler import calculate_percentiles
        res = calculate_percentiles([10.0, 20.0, 30.0, 40.0, 50.0])
        self.assertEqual(res["count"], 5)
        self.assertEqual(res["min"], 10.0)
        self.assertEqual(res["max"], 50.0)
        self.assertEqual(res["median"], 30.0)

    def test_prof_02_profiler_lifecycle_and_serialization(self):
        from orchestration.profiler import ScanExecutionProfiler, set_current_scan_profiler
        profiler = ScanExecutionProfiler(execution_id="test_stage46_scan", scan_mode="NORMAL")
        set_current_scan_profiler(profiler)

        with profiler.trace_phase("pre_discovery", counters={"items": 5}):
            time.sleep(0.005)

        profiler.register_worker("sb-w1", provider="superbet", role="detail_fetcher")
        profiler.worker_enter()
        profiler.record_worker_interval("sb-w1", state="RATE_LIMIT_WAIT", start_rel_s=0.001, end_rel_s=0.003, task_id="t1")
        profiler.record_worker_interval("sb-w1", state="WORKING", start_rel_s=0.003, end_rel_s=0.008, task_id="t1")
        profiler.record_worker_task_complete("sb-w1", task_id="t1", duration_ms=7.0, success=True)
        profiler.record_request(
            provider="superbet",
            endpoint_category="detail_event",
            worker_id="sb-w1",
            start_rel_s=0.001,
            end_rel_s=0.008,
            http_status=200,
            rate_limit_wait_ms=2.0,
            success=True,
            bytes_received=512,
        )
        profiler.worker_exit()

        trace_report = profiler.finish_scan()
        self.assertTrue(trace_report["trace_id"].startswith("trace_"))
        self.assertEqual(trace_report["execution_id"], "test_stage46_scan")
        self.assertEqual(len(trace_report["phases"]), 1)
        self.assertEqual(len(trace_report["workers"]), 1)
        self.assertEqual(trace_report["request_summary"]["total_requests"], 1)

    def test_prof_03_trace_endpoints(self):
        # Empty trace check
        res_empty = self.router.handle_get_latest_trace()
        self.assertEqual(res_empty.status_code, 404)

        # Populate a scan result with trace
        mock_result = ScanCycleResult(
            execution_id="stage46_scan_exec_01",
            cycle_status=CycleStatus.SUCCESS,
            started_at="2026-08-28T10:00:00Z",
            completed_at="2026-08-28T10:00:01Z",
            duration_seconds=1.0,
            diagnostics={
                "scan_trace": {
                    "trace_id": "trace_test_46",
                    "execution_id": "stage46_scan_exec_01",
                    "total_duration_wall_s": 1.0,
                    "phases": [],
                    "workers": [],
                }
            }
        )
        with patch.object(self.service.scan_orchestrator, "run_scan_cycle", return_value=mock_result):
            self.service.run_scan(scan_source="MANUAL")

        # Verify handle_get_latest_trace
        res_latest = self.router.handle_get_latest_trace()
        self.assertEqual(res_latest.status_code, 200)
        self.assertEqual(res_latest.data["trace_id"], "trace_test_46")

        # Verify handle_get_trace_by_id
        res_by_id = self.router.handle_get_trace_by_id("trace_test_46")
        self.assertEqual(res_by_id.status_code, 200)
        self.assertEqual(res_by_id.data["execution_id"], "stage46_scan_exec_01")


if __name__ == "__main__":
    unittest.main()
