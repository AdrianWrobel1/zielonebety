"""
Targeted Verification Tests for ULTRA SCAN Dashboard Integration.

Verifies the 12 required integration points:
1. Mode selector contains: ULTRA Scan (Full Day)
2. Selecting ULTRA causes the existing ULTRA API endpoint/path to be called.
3. The dashboard does not send an artificial fixture/detail limit.
4. Normal Scan behavior remains unchanged.
5. Deep Scan behavior remains unchanged.
6. ULTRA RUNNING state disables/prevents duplicate execution.
7. Already-running response (409 Conflict) is handled correctly.
8. SUCCESS is displayed correctly.
9. PARTIAL is displayed as PARTIAL (not converted to SUCCESS).
10. FAILED is displayed as FAILED (not converted to SUCCESS).
11. ULTRA result data appears in the existing dashboard result/history flow.
12. The scheduler and dashboard use the same ULTRA backend execution path.
"""

import inspect
import json
import os
import re
import threading
import unittest
from unittest.mock import MagicMock, patch

os.environ["DATABASE_URL"] = "sqlite:///:memory:"

from api.routes import APIRouter
from api.services import PlatformAPIService
from api.exceptions import APIError
from orchestration.models import ScanConfig
from orchestration.scheduler import ScanScheduler


class TestUltraDashboardIntegration(unittest.TestCase):
    """Targeted acceptance suite for Dashboard ULTRA Scan integration."""

    def setUp(self):
        self.mock_service = MagicMock(spec=PlatformAPIService)
        self.router = APIRouter(service=self.mock_service)
        self.root_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        self.index_html_path = os.path.join(self.root_dir, "web", "index.html")
        self.app_js_path = os.path.join(self.root_dir, "web", "app.js")
        self.styles_css_path = os.path.join(self.root_dir, "web", "styles.css")

    # ──────────────────────────────────────────────────────────────────────────
    # Test 1: Mode selector contains ULTRA Scan (Full Day)
    # ──────────────────────────────────────────────────────────────────────────
    def test_01_mode_selector_contains_ultra_option(self):
        with open(self.index_html_path, "r", encoding="utf-8") as f:
            html = f.read()

        # Selector element must exist
        self.assertIn('id="dash-scan-mode-select"', html)
        # Must contain NORMAL, DEEP, and ULTRA options
        self.assertIn('<option value="NORMAL"', html)
        self.assertIn('<option value="DEEP"', html)
        self.assertIn('<option value="ULTRA">ULTRA Scan (Full Day)</option>', html)

    # ──────────────────────────────────────────────────────────────────────────
    # Test 2: Selecting ULTRA causes existing ULTRA API endpoint to be called
    # ──────────────────────────────────────────────────────────────────────────
    def test_02_selecting_ultra_calls_ultra_api_endpoint(self):
        with open(self.app_js_path, "r", encoding="utf-8") as f:
            js = f.read()

        # Frontend api.runScan must route 'ULTRA' to /api/v1/scan/ultra
        self.assertIn("scanMode === 'ULTRA'", js)
        self.assertIn("/api/v1/scan/ultra", js)

        # Router handle_post_ultra_scan calls service.run_ultra_scan
        self.mock_service.run_ultra_scan.return_value = {
            "execution_id": "ultra_test_02",
            "status": "SUCCESS",
            "counts": {"surebets": 0},
        }
        res = self.router.handle_post_ultra_scan({})
        self.assertEqual(res.status_code, 200)
        self.mock_service.run_ultra_scan.assert_called_once_with(
            scope_params={},
            manual=True,
            dispatch_telegram=True,
        )

    # ──────────────────────────────────────────────────────────────────────────
    # Test 3: Dashboard does not send artificial detail/event limit
    # ──────────────────────────────────────────────────────────────────────────
    def test_03_dashboard_does_not_send_artificial_limits(self):
        with open(self.app_js_path, "r", encoding="utf-8") as f:
            js = f.read()

        # Search the runScan ULTRA block
        ultra_block_match = re.search(r"scanMode === 'ULTRA'.*?return res\.json\(\);", js, re.DOTALL)
        self.assertIsNotNone(ultra_block_match, "Could not find ULTRA scanMode block in app.js")
        ultra_block = ultra_block_match.group(0)

        # Must not pass any bounded verification parameters
        for forbidden in ["max_superbet_details", "max_betclic_details", "--limit", "max_detail_requests"]:
            self.assertNotIn(forbidden, ultra_block, f"Found artificial limit parameter '{forbidden}' in dashboard ULTRA trigger")

        # Must send empty body
        self.assertIn("body: JSON.stringify({})", ultra_block)

    # ──────────────────────────────────────────────────────────────────────────
    # Test 4: Normal Scan behavior remains unchanged
    # ──────────────────────────────────────────────────────────────────────────
    def test_04_normal_scan_behavior_unchanged(self):
        self.mock_service.run_scan.return_value = {
            "execution_id": "scan_norm_04",
            "cycle_status": "SUCCESS",
            "counts": {"discovered_events": 15},
        }
        res = self.router.handle_post_run_scan({"scan_mode": "NORMAL"})
        self.assertEqual(res.status_code, 200)
        self.mock_service.run_scan.assert_called_once()
        config_arg = self.mock_service.run_scan.call_args[1].get("config")
        self.assertIsNotNone(config_arg)
        self.assertEqual(config_arg.scan_mode, "NORMAL")

    # ──────────────────────────────────────────────────────────────────────────
    # Test 5: Deep Scan behavior remains unchanged
    # ──────────────────────────────────────────────────────────────────────────
    def test_05_deep_scan_behavior_unchanged(self):
        self.mock_service.run_scan.return_value = {
            "execution_id": "scan_deep_05",
            "cycle_status": "SUCCESS",
            "counts": {"discovered_events": 50},
        }
        res = self.router.handle_post_run_scan({"scan_mode": "DEEP"})
        self.assertEqual(res.status_code, 200)
        self.mock_service.run_scan.assert_called_once()
        config_arg = self.mock_service.run_scan.call_args[1].get("config")
        self.assertIsNotNone(config_arg)
        self.assertEqual(config_arg.scan_mode, "DEEP")

    # ──────────────────────────────────────────────────────────────────────────
    # Test 6: ULTRA RUNNING state disables/prevents duplicate execution
    # ──────────────────────────────────────────────────────────────────────────
    def test_06_ultra_running_state_prevents_duplicate_execution(self):
        with open(self.app_js_path, "r", encoding="utf-8") as f:
            js = f.read()

        # UI sets button disabled and changes label
        self.assertIn("btnRun.disabled = true", js)
        self.assertIn("ULTRA RUNNING...", js)
        self.assertIn("SCANNING_ULTRA", js)

        # Service lock enforces non-blocking acquisition
        with patch.object(PlatformAPIService, "__init__", lambda s, **kw: None):
            svc = PlatformAPIService.__new__(PlatformAPIService)
            svc._scan_lock = threading.Lock()
            svc._is_scanning = False
            svc._scanner_status = "READY"

            # Acquire the lock to simulate in-flight scan
            acquired = svc._scan_lock.acquire(blocking=False)
            self.assertTrue(acquired)

            # Concurrent call to run_ultra_scan must raise 409 APIError
            with self.assertRaises(APIError) as ctx:
                svc.run_ultra_scan()
            self.assertEqual(ctx.exception.status_code, 409)
            self.assertIn("already in progress", str(ctx.exception))

            # Release lock
            svc._scan_lock.release()

    # ──────────────────────────────────────────────────────────────────────────
    # Test 7: Already-running response (409 Conflict) is handled correctly
    # ──────────────────────────────────────────────────────────────────────────
    def test_07_already_running_response_handled_correctly(self):
        self.mock_service.run_ultra_scan.side_effect = APIError(
            "Scan is already in progress. Please wait for the current cycle to complete.",
            status_code=409,
        )
        res = self.router.handle_post_ultra_scan({})
        self.assertEqual(res.status_code, 409)
        self.assertTrue(any("already in progress" in err for err in res.errors))

        # Frontend handles 409 gracefully without crashing
        with open(self.app_js_path, "r", encoding="utf-8") as f:
            js = f.read()
        self.assertIn("res.status_code === 409", js)
        self.assertIn("Scan already in progress", js)

    # ──────────────────────────────────────────────────────────────────────────
    # Test 8: SUCCESS is displayed correctly
    # ──────────────────────────────────────────────────────────────────────────
    def test_08_success_status_displayed_correctly(self):
        with open(self.app_js_path, "r", encoding="utf-8") as f:
            js = f.read()

        # Status badge mapping for SUCCESS
        self.assertIn("badge-cycle-success", js)
        self.assertIn("cycleStatus === 'SUCCESS'", js)

        # CSS styling must exist
        with open(self.styles_css_path, "r", encoding="utf-8") as f:
            css = f.read()
        self.assertIn(".badge-cycle-success", css)

    # ──────────────────────────────────────────────────────────────────────────
    # Test 9: PARTIAL is displayed as PARTIAL (not converted to SUCCESS)
    # ──────────────────────────────────────────────────────────────────────────
    def test_09_partial_status_displayed_as_partial(self):
        with open(self.app_js_path, "r", encoding="utf-8") as f:
            js = f.read()

        # Must explicitly check and render PARTIAL
        self.assertIn("cycleStatus === 'PARTIAL'", js)
        self.assertIn("badge-cycle-partial", js)
        self.assertIn("Scan completed with PARTIAL status", js)

        # CSS styling must provide distinctive warning/amber color
        with open(self.styles_css_path, "r", encoding="utf-8") as f:
            css = f.read()
        self.assertIn(".badge-cycle-partial", css)

    # ──────────────────────────────────────────────────────────────────────────
    # Test 10: FAILED is displayed as FAILED (not converted to SUCCESS)
    # ──────────────────────────────────────────────────────────────────────────
    def test_10_failed_status_displayed_as_failed(self):
        with open(self.app_js_path, "r", encoding="utf-8") as f:
            js = f.read()

        # Must explicitly check and render FAILED
        self.assertIn("badge-cycle-failed", js)
        self.assertIn("Scan FAILED", js)

        # CSS styling must provide distinctive danger/red color
        with open(self.styles_css_path, "r", encoding="utf-8") as f:
            css = f.read()
        self.assertIn(".badge-cycle-failed", css)

    # ──────────────────────────────────────────────────────────────────────────
    # Test 11: ULTRA result data appears in existing dashboard result/history flow
    # ──────────────────────────────────────────────────────────────────────────
    def test_11_ultra_result_appears_in_dashboard_history_and_status(self):
        with patch.object(PlatformAPIService, "__init__", lambda s, **kw: None):
            svc = PlatformAPIService.__new__(PlatformAPIService)
            svc._scan_lock = threading.Lock()
            svc._is_scanning = False
            svc._scanner_status = "READY"
            svc._last_scan_result = None
            svc._last_ultra_scan_result = None
            svc._scan_history = []
            svc.db_manager = None
            svc.scan_orchestrator = MagicMock()

            # Mock UltraScanOrchestrator
            mock_ultra_result = MagicMock()
            mock_ultra_result.to_dict.return_value = {
                "execution_id": "ultra_20260904_120000",
                "status": "SUCCESS",
                "target_date": "2026-09-04",
                "started_at": "2026-09-04T12:00:00Z",
                "completed_at": "2026-09-04T12:03:00Z",
                "duration_seconds": 180.5,
                "funnel": {
                    "discovered_events_total": 359,
                    "discovered_today_events": 85,
                    "matched_events_today": 32,
                    "evaluated_markets_total": 450,
                },
                "counts": {
                    "surebets": 2,
                    "valuebets": 5,
                    "player_props": 3,
                    "team_props": 1,
                    "top_opportunities": 10,
                },
                "top_opportunities": [],
                "surebets": [],
                "valuebets": [],
                "player_props": [],
                "team_props": [],
            }

            with patch("orchestration.ultra_scan.UltraScanOrchestrator") as MockOrch:
                MockOrch.return_value.execute.return_value = mock_ultra_result

                # Execute manual ULTRA scan
                svc.run_ultra_scan(manual=True, dispatch_telegram=False)

            # 1. Verify scan history contains the ULTRA execution entry
            history = svc.get_scan_history()
            self.assertEqual(len(history), 1)
            entry = history[0]
            self.assertEqual(entry["execution_id"], "ultra_20260904_120000")
            self.assertEqual(entry["status"], "SUCCESS")
            self.assertEqual(entry["scan_source"], "ULTRA / MANUAL")
            self.assertEqual(entry["events_discovered"], 359)
            self.assertEqual(entry["events_selected"], 85)
            self.assertEqual(entry["events_matched"], 32)
            self.assertEqual(entry["surebets_count"], 2)
            self.assertEqual(entry["duration_seconds"], 180.5)

            # 2. Verify get_scan_status reflects ULTRA execution
            status = svc.get_scan_status()
            self.assertTrue(status["has_run"])
            self.assertEqual(status["last_scan_id"], "ultra_20260904_120000")
            self.assertEqual(status["last_cycle_status"], "SUCCESS")

            # 3. Verify get_latest_scan returns the ULTRA scan result
            latest = svc.get_latest_scan()
            self.assertIsNotNone(latest)
            self.assertEqual(latest["execution_id"], "ultra_20260904_120000")

    # ──────────────────────────────────────────────────────────────────────────
    # Test 12: Scheduler and Dashboard converge on same backend execution path
    # ──────────────────────────────────────────────────────────────────────────
    def test_12_scheduler_and_dashboard_converge_on_same_backend_path(self):
        sched_src = inspect.getsource(ScanScheduler.run_ultra_scan_now)
        router_src = inspect.getsource(APIRouter.handle_post_ultra_scan)

        # Both MUST call run_ultra_scan on the service
        self.assertIn("self._service.run_ultra_scan", sched_src)
        self.assertIn("self.service.run_ultra_scan", router_src)

    # ──────────────────────────────────────────────────────────────────────────
    # Test 13: Provider Health Grid maps ULTRA scan correctly (no UNKNOWN)
    # ──────────────────────────────────────────────────────────────────────────
    def test_13_ultra_provider_health_mapping(self):
        with open(self.app_js_path, "r", encoding="utf-8") as f:
            js = f.read()

        # Check that renderProviderHealthGrid handles isUltra
        self.assertIn("function renderProviderHealthGrid(scan)", js)
        self.assertIn("ultraFunnel.detail_fetch_success_superbet", js)
        self.assertIn("ultraFunnel.detail_fetch_success_betclic", js)
        self.assertIn("ultraFunnel.provider_status", js)
        self.assertIn("NOT USED", js)
        self.assertIn("matched_events_today", js)
        self.assertIn("telegram_dispatch", js)

    # ──────────────────────────────────────────────────────────────────────────
    # Test 14: Dynamic telemetry labels for target-day events and matched markets
    # ──────────────────────────────────────────────────────────────────────────
    def test_14_ultra_telemetry_labels_and_funnel(self):
        with open(self.app_js_path, "r", encoding="utf-8") as f:
            js = f.read()

        # Check dynamic label assignments
        self.assertIn("dash-events-selected-label", js)
        self.assertIn("ULTRA Event Horizon (Warsaw)", js)
        self.assertIn("dash-events-matched-foot", js)
        self.assertIn("dash-markets-evaluated-foot", js)
        self.assertIn("ULTRA Event Horizon", js)

        # Ensure index.html contains the corresponding span hooks
        with open(self.index_html_path, "r", encoding="utf-8") as f:
            html = f.read()
        self.assertIn('id="dash-events-selected-label"', html)
        self.assertIn('id="dash-events-matched-foot"', html)
        self.assertIn('id="dash-markets-evaluated-foot"', html)

    # ──────────────────────────────────────────────────────────────────────────
    # Test 15: Lazy loading from database snapshot in get_latest_scan & get_scan_status
    # ──────────────────────────────────────────────────────────────────────────
    def test_15_lazy_load_snapshot_consistency(self):
        with patch.object(PlatformAPIService, "__init__", lambda s, **kw: None):
            svc = PlatformAPIService.__new__(PlatformAPIService)
            svc._scan_lock = threading.Lock()
            svc._is_scanning = False
            svc._scanner_status = "READY"
            svc._last_scan_result = None
            svc._last_ultra_scan_result = None
            svc.db_manager = MagicMock()

            persisted_ultra = {
                "execution_id": "ultra_persisted_test",
                "status": "SUCCESS",
                "target_date": "2026-09-04",
                "started_at": "2026-09-04T12:00:00Z",
                "completed_at": "2026-09-04T12:01:30Z",
                "duration_seconds": 90.4,
                "funnel": {
                    "discovered_events_total": 2157,
                    "discovered_today_events": 337,
                    "matched_events_today": 39,
                    "matched_markets_total": 780,
                    "evaluated_markets_total": 3353,
                    "provider_status": {"superbet": "AVAILABLE", "betclic": "AVAILABLE"},
                },
                "counts": {
                    "top_opportunities": 2,
                    "surebets": 2,
                    "valuebets": 0,
                    "player_props": 0,
                    "team_props": 0,
                },
                "top_opportunities": [],
                "surebets": [],
                "valuebets": [],
            }

            with patch("api.services._load_latest_ultra_scan_snapshot", return_value=persisted_ultra) as mock_loader:
                # 1. Calling get_latest_scan with unprimed in-memory state lazily loads
                latest = svc.get_latest_scan()
                self.assertIsNotNone(latest)
                self.assertEqual(latest["execution_id"], "ultra_persisted_test")
                mock_loader.assert_called_once()

                # Reset to None to test get_scan_status
                svc._last_ultra_scan_result = None
                mock_loader.reset_mock()

                # 2. Calling get_scan_status lazily loads
                status = svc.get_scan_status()
                self.assertTrue(status["has_run"])
                self.assertEqual(status["last_scan_id"], "ultra_persisted_test")
                self.assertEqual(status["last_cycle_status"], "SUCCESS")
                mock_loader.assert_called_once()

    # ──────────────────────────────────────────────────────────────────────────
    # Test 16: History restoration includes persisted ULTRA snapshot
    # ──────────────────────────────────────────────────────────────────────────
    def test_16_history_restoration_includes_ultra_snapshot(self):
        persisted_ultra = {
            "execution_id": "ultra_persisted_history",
            "status": "SUCCESS",
            "target_date": "2026-09-04",
            "started_at": "2026-09-04T14:00:00Z",
            "completed_at": "2026-09-04T14:01:30Z",
            "duration_seconds": 90.4,
            "funnel": {
                "discovered_events_total": 2157,
                "discovered_today_events": 337,
                "matched_events_today": 39,
            },
            "counts": {"surebets": 2},
        }

        mock_db = MagicMock()
        with patch("api.services._load_latest_ultra_scan_snapshot", return_value=persisted_ultra):
            with patch("api.services._load_scan_snapshots", return_value=[]):
                with patch.object(ScanScheduler, "start"):
                    svc = PlatformAPIService(db_manager=mock_db)

                    history = svc.get_scan_history()
                    self.assertEqual(len(history), 1)
                    self.assertEqual(history[0]["execution_id"], "ultra_persisted_history")
                    self.assertEqual(history[0]["scan_source"], "ULTRA / SNAPSHOT")
                    self.assertEqual(history[0]["events_discovered"], 2157)
                    self.assertEqual(history[0]["events_selected"], 337)

    # ──────────────────────────────────────────────────────────────────────────
    # Test 17: Events endpoint limit contract allows 250
    # ──────────────────────────────────────────────────────────────────────────
    def test_17_events_endpoint_limit_contract_allows_250(self):
        """Validates that GET /api/v1/events limit constraint accommodates full-slate browsing up to 500."""
        from api.fastapi_app import list_events
        sig = inspect.signature(list_events)
        param = sig.parameters["limit"]
        query_field = param.default
        le_constraint = next((m.le for m in getattr(query_field, "metadata", []) if hasattr(m, "le")), None)
        self.assertIsNotNone(le_constraint, "Could not find 'le' constraint in Query metadata")
        self.assertGreaterEqual(le_constraint, 500, f"Expected le >= 500, got {le_constraint}")

        # Ensure app.js limit (250) is <= endpoint limit
        with open(self.app_js_path, "r", encoding="utf-8") as f:
            js = f.read()
        self.assertIn("limit: 250", js)

    # ──────────────────────────────────────────────────────────────────────────
    # Test 18: ULTRA scan populates events cache for Event Browser
    # ──────────────────────────────────────────────────────────────────────────
    def test_18_ultra_scan_populates_events_cache_for_event_browser(self):
        """Validates that ULTRA scan execution results serialize canonical events and populate cache."""
        from orchestration.ultra_scan import UltraScanResult, UltraScanFunnelMetrics
        from api.services import _serialize_events_from_ultra_result

        # Build mock canonical event and validation result
        mock_ce = MagicMock()
        mock_ce.canonical_event_id = "cev_ultra_test_001"
        mock_ce.home_team = "Real Madrid"
        mock_ce.away_team = "Barcelona"
        mock_ce.competition = MagicMock(name="LaLiga")
        mock_ce.sport = "football"
        mock_ce.scheduled_start = "2026-09-04T20:00:00Z"
        mock_ce.status = "SCHEDULED"
        mock_ce.sources = {"superbet": MagicMock(provider_event_id="sb_1"), "betclic": MagicMock(provider_event_id="bc_1")}
        mock_ce.match_evidence = [MagicMock(total_score=0.98)]

        mock_record = MagicMock()
        mock_record.canonical_event = mock_ce
        mock_record.matched_markets = []

        mock_val_res = MagicMock()
        mock_val_res.event_validation_records = [mock_record]
        mock_val_res.canonical_events = [mock_ce]

        mock_ultra_res = MagicMock(spec=UltraScanResult)
        mock_ultra_res.validation_result = mock_val_res
        mock_ultra_res.all_graphs = []
        mock_ultra_res.top_opportunities = []
        mock_ultra_res.surebets = []
        mock_ultra_res.valuebets = []
        mock_ultra_res.player_props = []
        mock_ultra_res.team_props = []

        summaries, details_map = _serialize_events_from_ultra_result(mock_ultra_res)
        self.assertEqual(len(summaries), 1)
        self.assertEqual(summaries[0]["id"], "cev_ultra_test_001")
        self.assertEqual(summaries[0]["home_team"], "Real Madrid")
        self.assertEqual(summaries[0]["away_team"], "Barcelona")
        self.assertIn("cev_ultra_test_001", details_map)

        # Verify PlatformAPIService caches and serves them via list_events and get_event_detail
        mock_db = MagicMock()
        with patch.object(ScanScheduler, "start"):
            svc = PlatformAPIService(db_manager=mock_db)
            svc._events_summary_cache = list(summaries)
            svc._events_cache = dict(details_map)

            evs = svc.list_events(limit=250)
            self.assertEqual(len(evs), 1)
            self.assertEqual(evs[0]["canonical_event_id"], "cev_ultra_test_001")

            detail = svc.get_event_detail("cev_ultra_test_001")
            self.assertIsNotNone(detail)
            self.assertEqual(detail["home_team"], "Real Madrid")

    # ──────────────────────────────────────────────────────────────────────────
    # Test 19: Opportunity Explorer hydrates ULTRA opportunities
    # ──────────────────────────────────────────────────────────────────────────
    def test_19_opportunity_explorer_hydrates_ultra_opportunities(self):
        """Validates that Opportunity Explorer aggregates qualified ULTRA opportunities."""
        from orchestration.ultra_scan import UltraOpportunity

        mock_opp = UltraOpportunity(
            opportunity_id="prop_ultra_yannick_01",
            category="PLAYER_PROP",
            match_name="Al Shabab vs Al Hilal",
            competition="Saudi Pro League",
            kickoff="2026-09-04 18:00 UTC",
            market_display="Shots (Powyżej 1.5)",
            selection_display="Yannick Carrasco Over 1.5",
            bookmaker="Superbet",
            raw_odds=1.48,
            effective_odds=1.30,
            fair_odds=1.19,
            edge_pct=9.66,
            confidence="MEDIUM",
            ultra_rank_score=8.5,
            reference_sources=["Betclic"],
            details={"player_name": "Yannick Carrasco", "line": 1.5},
        )

        mock_db = MagicMock()
        with patch.object(ScanScheduler, "start"):
            svc = PlatformAPIService(db_manager=mock_db)
            svc._last_ultra_scan_result = {
                "execution_id": "ultra_test_opps",
                "completed_at": "2026-09-04T15:00:00Z",
                "top_opportunities": [mock_opp.to_dict()],
                "player_props": [mock_opp.to_dict()],
            }

            explorer_res = svc.get_unified_explorer_opportunities(limit=100)
            items = explorer_res["items"]
            self.assertGreaterEqual(len(items), 1)
            found = next((i for i in items if i["id"] == "prop_ultra_yannick_01"), None)
            self.assertIsNotNone(found)
            self.assertEqual(found["type"], "PLAYER_PROP")
            self.assertEqual(found["player"], "Yannick Carrasco")
            self.assertEqual(found["best_bookmaker"], "Superbet")
            self.assertEqual(found["execution_odds"], 1.30)
            self.assertEqual(found["fair_odds"], 1.19)
            self.assertEqual(found["source"], "ultra_scan")


if __name__ == "__main__":
    unittest.main()

