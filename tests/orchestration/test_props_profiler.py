import concurrent.futures
import threading
import time
import unittest
from typing import Any, Dict
from unittest.mock import MagicMock, patch

from fastapi import Response

from api.fastapi_app import get_latest_trace as fastapi_get_latest_trace, get_trace_by_id as fastapi_get_trace_by_id
from api.routes import APIRouter
from api.services import PlatformAPIService
from database.config import DatabaseConfig
from database.connection import DatabaseManager
from orchestration.profiler import (
    ScanExecutionProfiler,
    active_scan_profiler,
    get_current_scan_profiler,
    set_current_scan_profiler,
)
from scanner.global_props_scanner import (
    GlobalPropsScanner,
    GlobalScanBudget,
    GlobalScanScope,
)


class TestPropsProfilerCore(unittest.TestCase):
    """Test core ScanExecutionProfiler enhancements for multi-mode profiling."""

    def test_profiler_scan_type_initialization_and_serialization(self):
        # Default scan_type should be "main" for backwards compatibility
        p_main = ScanExecutionProfiler(scan_mode="NORMAL")
        self.assertEqual(p_main.scan_type, "main")
        report_main = p_main.finish_scan()
        self.assertEqual(report_main["scan_type"], "main")

        # Explicit team_props
        p_team = ScanExecutionProfiler(scan_mode="NORMAL", scan_type="team_props")
        self.assertEqual(p_team.scan_type, "team_props")
        report_team = p_team.finish_scan()
        self.assertEqual(report_team["scan_type"], "team_props")

        # Explicit player_props
        p_player = ScanExecutionProfiler(scan_mode="NORMAL", scan_type="player_props")
        self.assertEqual(p_player.scan_type, "player_props")
        report_player = p_player.finish_scan()
        self.assertEqual(report_player["scan_type"], "player_props")

    def test_dynamic_provider_discovery_including_statshub(self):
        profiler = ScanExecutionProfiler(scan_type="team_props")
        profiler.start_phase("trends_discovery")

        # Register statshub client worker and requests
        profiler.register_worker("statshub-client", provider="statshub", role="trends_client")
        profiler.record_request(
            provider="statshub",
            endpoint_category="team_trends",
            worker_id="statshub-client",
            start_rel_s=0.01,
            end_rel_s=0.05,
            http_status=200,
            success=True,
            bytes_received=15420,
        )
        profiler.finish_phase("trends_discovery")
        report = profiler.finish_scan()

        # Check acquisition_forensics includes statshub
        forensics = report.get("acquisition_forensics", {})
        self.assertIn("statshub", forensics, "statshub must be dynamically included in acquisition forensics")
        sh_stats = forensics["statshub"]
        self.assertEqual(sh_stats["requests_count"], 1)
        self.assertEqual(sh_stats["worker_count"], 1)
        self.assertGreater(sh_stats["total_network_seconds"], 0.0)

    def test_manual_start_and_finish_phase(self):
        profiler = ScanExecutionProfiler(scan_type="player_props")
        meas = profiler.start_phase("fixture_discovery", counters={"initial": 5})
        self.assertEqual(meas.name, "fixture_discovery")
        self.assertEqual(meas.counters["initial"], 5)
        time.sleep(0.01)
        fin = profiler.finish_phase("fixture_discovery", counters={"candidates": 10})
        self.assertIsNotNone(fin)
        self.assertEqual(fin.counters["candidates"], 10)
        self.assertGreater(fin.wall_clock_seconds, 0.0)

        report = profiler.finish_scan()
        phase_names = [p["name"] for p in report["phases"]]
        self.assertIn("fixture_discovery", phase_names)


class TestProfilerTraceIsolation(unittest.TestCase):
    """Test trace isolation between concurrent execution contexts."""

    def test_concurrent_traces_do_not_leak_telemetry(self):
        p_team = ScanExecutionProfiler(scan_type="team_props")
        p_player = ScanExecutionProfiler(scan_type="player_props")

        def run_team_scan():
            with active_scan_profiler(p_team):
                p_team.start_phase("team_phase")
                for i in range(10):
                    p_team.register_worker(f"team-worker-{i}", provider="statshub", role="team_fetcher")
                    p_team.record_request(
                        provider="statshub",
                        endpoint_category="team_trends",
                        worker_id=f"team-worker-{i}",
                        start_rel_s=0.001 * i,
                        end_rel_s=0.002 * (i + 1),
                        http_status=200,
                        success=True,
                    )
                p_team.finish_phase("team_phase")

        def run_player_scan():
            with active_scan_profiler(p_player):
                p_player.start_phase("player_phase")
                for i in range(5):
                    p_player.register_worker(f"player-worker-{i}", provider="superbet", role="player_fetcher")
                    p_player.record_request(
                        provider="superbet",
                        endpoint_category="player_props",
                        worker_id=f"player-worker-{i}",
                        start_rel_s=0.001 * i,
                        end_rel_s=0.003 * (i + 1),
                        http_status=200,
                        success=True,
                    )
                p_player.finish_phase("player_phase")

        # Run concurrently across multiple threads
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            f1 = pool.submit(run_team_scan)
            f2 = pool.submit(run_player_scan)
            f1.result()
            f2.result()

        rep_team = p_team.finish_scan()
        rep_player = p_player.finish_scan()

        # Team trace should ONLY have statshub and 10 workers/requests
        self.assertEqual(len(rep_team["requests"]), 10)
        self.assertTrue(all(r["provider"] == "statshub" for r in rep_team["requests"]))
        self.assertEqual([p["name"] for p in rep_team["phases"]], ["team_phase"])

        # Player trace should ONLY have superbet and 5 workers/requests
        self.assertEqual(len(rep_player["requests"]), 5)
        self.assertTrue(all(r["provider"] == "superbet" for r in rep_player["requests"]))
        self.assertEqual([p["name"] for p in rep_player["phases"]], ["player_phase"])

    def test_tri_mode_concurrency_isolation(self):
        p_main = ScanExecutionProfiler(scan_mode="NORMAL", scan_type="main")
        p_team = ScanExecutionProfiler(scan_mode="NORMAL", scan_type="team_props")
        p_player = ScanExecutionProfiler(scan_mode="NORMAL", scan_type="player_props")

        def worker_task(prof, prov, cat, worker_id):
            set_current_scan_profiler(prof, set_global=False)
            current = get_current_scan_profiler()
            assert current is prof
            current.register_worker(worker_id, provider=prov)
            current.record_request(
                provider=prov,
                endpoint_category=cat,
                worker_id=worker_id,
                start_rel_s=0.01,
                end_rel_s=0.02,
                http_status=200,
                success=True,
            )

        def run_main():
            with active_scan_profiler(p_main):
                with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
                    futures = [pool.submit(worker_task, p_main, "odds_api", "main_odds", f"main-w-{i}") for i in range(12)]
                    for f in futures:
                        f.result()

        def run_team():
            with active_scan_profiler(p_team):
                with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
                    futures = [pool.submit(worker_task, p_team, "statshub", "team_trends", f"team-w-{i}") for i in range(8)]
                    for f in futures:
                        f.result()

        def run_player():
            with active_scan_profiler(p_player):
                with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
                    futures = [pool.submit(worker_task, p_player, "superbet", "player_detail", f"player-w-{i}") for i in range(6)]
                    for f in futures:
                        f.result()

        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as master_pool:
            f_main = master_pool.submit(run_main)
            f_team = master_pool.submit(run_team)
            f_player = master_pool.submit(run_player)
            f_main.result()
            f_team.result()
            f_player.result()

        m_rep = p_main.finish_scan()
        t_rep = p_team.finish_scan()
        p_rep = p_player.finish_scan()

        self.assertEqual(len(m_rep["requests"]), 12)
        self.assertTrue(all(r["provider"] == "odds_api" for r in m_rep["requests"]))
        self.assertEqual(len(t_rep["requests"]), 8)
        self.assertTrue(all(r["provider"] == "statshub" for r in t_rep["requests"]))
        self.assertEqual(len(p_rep["requests"]), 6)
        self.assertTrue(all(r["provider"] == "superbet" for r in p_rep["requests"]))


class TestGlobalPropsScannerTraceIntegration(unittest.TestCase):
    """Test GlobalPropsScanner execution with authentic phase profiling."""

    def test_global_props_scanner_attaches_authentic_trace(self):
        scanner = GlobalPropsScanner()
        scope = GlobalScanScope(props_scope="TEAM", max_results=2, min_ev_percent=1.0)
        budget = GlobalScanBudget(max_fixtures=2, max_execution_events=2)

        # Mock discovery and trends to run fast and authentically
        with patch.object(scanner, "cached_execution_events", []):
            with patch("scanner.global_props_scanner.StatsHubClient") as mock_sh_client:
                instance = mock_sh_client.return_value
                instance.discover_upcoming_fixtures.return_value = []
                instance.fetch_props.return_value = {}
                instance.fetch_team_trends.return_value = []

                result = scanner.execute_scan(scope=scope, budget=budget)

        self.assertIsNotNone(result.scan_trace, "scan_trace must be attached to GlobalScanResult")
        trace = result.scan_trace
        self.assertEqual(trace["scan_type"], "team_props")
        phase_names = [p["name"] for p in trace["phases"]]

        # Verify authentic phases were recorded
        expected_phases = [
            "fixture_discovery",
            "trends_discovery",
            "fixture_prioritization",
            "execution_acquisition",
            "quote_extraction",
            "matching_and_evaluation",
            "ranking",
        ]
        for ep in expected_phases:
            self.assertIn(ep, phase_names, f"Phase {ep} must be recorded in props trace")


class TestAPIProfilerModeEndpoints(unittest.TestCase):
    """Test API retrieval of multi-mode traces via router and FastAPI endpoints."""

    def setUp(self):
        self.db_manager = DatabaseManager(DatabaseConfig.default_sqlite_in_memory())
        self.db_manager.create_tables()
        self.service = PlatformAPIService(db_manager=self.db_manager)
        self.router = APIRouter(service=self.service)

    def test_mode_parameter_trace_retrieval(self):
        mock_main_trace = {
            "trace_id": "trace_main_001",
            "execution_id": "exec_main_001",
            "scan_mode": "NORMAL",
            "scan_type": "main",
            "total_duration_wall_s": 2.5,
            "phases": [{"name": "acquisition", "wall_clock_seconds": 1.5, "wall_clock_ms": 1500.0, "pct_of_total": 60.0}],
            "workers": [],
            "requests": [],
            "acquisition_forensics": {},
            "latency_percentiles": {},
            "top_stragglers": [],
            "bottleneck_summary": [],
        }
        mock_team_trace = {
            "trace_id": "trace_team_002",
            "execution_id": "exec_team_002",
            "scan_mode": "NORMAL",
            "scan_type": "team_props",
            "total_duration_wall_s": 1.8,
            "phases": [{"name": "trends_discovery", "wall_clock_seconds": 0.8, "wall_clock_ms": 800.0, "pct_of_total": 44.4}],
            "workers": [],
            "requests": [],
            "acquisition_forensics": {},
            "latency_percentiles": {},
            "top_stragglers": [],
            "bottleneck_summary": [],
        }
        mock_player_trace = {
            "trace_id": "trace_player_003",
            "execution_id": "exec_player_003",
            "scan_mode": "NORMAL",
            "scan_type": "player_props",
            "total_duration_wall_s": 3.1,
            "phases": [{"name": "trends_discovery", "wall_clock_seconds": 1.2, "wall_clock_ms": 1200.0, "pct_of_total": 38.7}],
            "workers": [],
            "requests": [],
            "acquisition_forensics": {},
            "latency_percentiles": {},
            "top_stragglers": [],
            "bottleneck_summary": [],
        }

        self.service._latest_traces["main"] = mock_main_trace
        self.service._latest_traces["team_props"] = mock_team_trace
        self.service._latest_traces["player_props"] = mock_player_trace

        # 1. Default request (no mode param) -> Main Scan
        res_default = self.router.handle_get_latest_trace()
        self.assertEqual(res_default.status_code, 200)
        self.assertEqual(res_default.data["trace_id"], "trace_main_001")
        self.assertEqual(res_default.data["scan_type"], "main")

        # 2. Explicit mode=main
        res_main = self.router.handle_get_latest_trace(mode="main")
        self.assertEqual(res_main.status_code, 200)
        self.assertEqual(res_main.data["trace_id"], "trace_main_001")

        # 3. mode=team_props
        res_team = self.router.handle_get_latest_trace(mode="team_props")
        self.assertEqual(res_team.status_code, 200)
        self.assertEqual(res_team.data["trace_id"], "trace_team_002")
        self.assertEqual(res_team.data["scan_type"], "team_props")

        # 4. mode=player_props
        res_player = self.router.handle_get_latest_trace(mode="player_props")
        self.assertEqual(res_player.status_code, 200)
        self.assertEqual(res_player.data["trace_id"], "trace_player_003")
        self.assertEqual(res_player.data["scan_type"], "player_props")

        # 5. Empty trace -> 404 with honest message
        self.service._latest_traces["team_props"] = None
        res_empty = self.router.handle_get_latest_trace(mode="team_props")
        self.assertEqual(res_empty.status_code, 404)
        self.assertTrue(any("Team Props" in err for err in res_empty.errors))

        # 6. Trace by ID lookup works for all modes
        res_by_id = self.router.handle_get_trace_by_id("trace_player_003")
        self.assertEqual(res_by_id.status_code, 200)
        self.assertEqual(res_by_id.data["trace_id"], "trace_player_003")

    def test_fastapi_endpoint_handlers(self):
        from api.fastapi_app import service_instance

        mock_team_trace = {
            "trace_id": "trace_fastapi_team_01",
            "execution_id": "exec_team_01",
            "scan_mode": "NORMAL",
            "scan_type": "team_props",
            "total_duration_wall_s": 1.2,
            "phases": [],
            "workers": [],
            "requests": [],
            "acquisition_forensics": {},
            "latency_percentiles": {},
            "top_stragglers": [],
            "bottleneck_summary": [],
        }
        service_instance._latest_traces["team_props"] = mock_team_trace

        resp = Response()
        res_dict = fastapi_get_latest_trace(response=resp, mode="team_props")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(res_dict["data"]["trace_id"], "trace_fastapi_team_01")
        self.assertEqual(res_dict["data"]["scan_type"], "team_props")
