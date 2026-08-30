"""
Stage 8.3 Targeted Tests — Event Selection Policy & Scan Scheduler

Tests covered (~20 focused unit tests):
  1–10:  DefaultEventSelectionPolicy — prioritization, filtering, limits
  11–17: ScanScheduler — state, enable/disable, failure recovery
  18–20: Scheduler API endpoints via APIRouter
"""

import threading
import time
import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch, PropertyMock

from orchestration.event_selection import DefaultEventSelectionPolicy
from orchestration.scheduler import ScanScheduler
from api.routes import APIRouter
from api.services import PlatformAPIService
from api.exceptions import APIError
from database.connection import DatabaseManager
from database.config import DatabaseConfig


# ─────────────────────────────────────────────────────────────────────────────
# Helpers — minimal discovered-item stubs
# ─────────────────────────────────────────────────────────────────────────────

class _Item:
    """Simple stub for a discovered event item."""
    def __init__(self, event_id, competition_name, start_time=None):
        self.event_id = event_id
        self.competition_name = competition_name
        self.start_time = start_time  # datetime or None


def _make_item(eid, comp, hours_from_now=2):
    dt = datetime.now(timezone.utc) + timedelta(hours=hours_from_now)
    return _Item(eid, comp, dt)


# ─────────────────────────────────────────────────────────────────────────────
# Section 1 — DefaultEventSelectionPolicy
# ─────────────────────────────────────────────────────────────────────────────

class TestDefaultEventSelectionPolicy(unittest.TestCase):

    def setUp(self):
        self.policy = DefaultEventSelectionPolicy()

    def test_01_popular_competition_prioritization(self):
        """Premier League events must appear before unknown competitions after ranking."""
        pl_item = _make_item("pl1", "Premier League", hours_from_now=5)
        unknown_item = _make_item("u1", "Lithuanian 2nd Division", hours_from_now=1)  # closer kickoff

        result = self.policy.filter_and_rank_discovered_items([unknown_item, pl_item])

        # Premier League should rank first despite later kickoff
        self.assertEqual(result[0].event_id, "pl1")

    def test_02_kickoff_window_filtering(self):
        """Events outside hours_ahead window must be excluded."""
        inside = _make_item("in1", "La Liga", hours_from_now=10)
        outside = _make_item("out1", "La Liga", hours_from_now=30)

        result = self.policy.filter_and_rank_discovered_items(
            [inside, outside],
            hours_ahead=24,
        )
        ids = [i.event_id for i in result]
        self.assertIn("in1", ids)
        self.assertNotIn("out1", ids)

    def test_03_kickoff_window_includes_2h_before(self):
        """Events up to 2h before current time are retained (in-flight matches)."""
        recent_past = _make_item("past1", "Bundesliga", hours_from_now=-1)  # 1h ago — within 2h grace

        result = self.policy.filter_and_rank_discovered_items(
            [recent_past],
            hours_ahead=24,
        )
        self.assertEqual(len(result), 1)

    def test_04_event_limit_enforcement(self):
        """Result must be capped at limit."""
        items = [_make_item(f"ev{i}", "Premier League", hours_from_now=i + 1) for i in range(20)]

        result = self.policy.filter_and_rank_discovered_items(items, limit=7)
        self.assertEqual(len(result), 7)

    def test_05_detail_request_limit(self):
        """select_events_for_detail must not return more IDs than max_detail_requests."""
        items = [_make_item(f"ev{i}", "La Liga", hours_from_now=i + 1) for i in range(30)]

        result = self.policy.select_events_for_detail(items, max_detail_requests=5)
        self.assertLessEqual(len(result), 5)

    def test_06_deterministic_ordering(self):
        """Same input must produce identical ordering on repeated calls."""
        items = [_make_item(f"ev{i}", "Ekstraklasa", hours_from_now=i + 1) for i in range(10)]

        r1 = self.policy.filter_and_rank_discovered_items(items)
        r2 = self.policy.filter_and_rank_discovered_items(items)

        self.assertEqual([i.event_id for i in r1], [i.event_id for i in r2])

    def test_07_mixed_pref_and_non_pref_ordering(self):
        """All preferred competitions appear before all non-preferred."""
        pref_items = [_make_item(f"pref{i}", "Champions League", hours_from_now=10 - i) for i in range(3)]
        non_pref_items = [_make_item(f"np{i}", "Albanian Superliga", hours_from_now=1 + i) for i in range(3)]

        all_items = non_pref_items + pref_items  # non-pref first as input
        result = self.policy.filter_and_rank_discovered_items(all_items)

        result_ids = [i.event_id for i in result]
        last_pref_idx = max(result_ids.index(f"pref{i}") for i in range(3))
        first_np_idx = min(result_ids.index(f"np{i}") for i in range(3))
        self.assertLess(last_pref_idx, first_np_idx)

    def test_08_zero_items_returns_empty(self):
        """Empty input → empty output."""
        result = self.policy.filter_and_rank_discovered_items([])
        self.assertEqual(result, [])

    def test_09_no_kickoff_items_included_without_window(self):
        """Items without kickoff are kept when hours_ahead is None."""
        no_kickoff = _Item("nk1", "Serie A", start_time=None)
        result = self.policy.filter_and_rank_discovered_items([no_kickoff], hours_ahead=None)
        self.assertEqual(len(result), 1)

    def test_10_select_events_for_detail_returns_ids(self):
        """select_events_for_detail must return string IDs, not item objects."""
        items = [_make_item(f"ev{i}", "Bundesliga", hours_from_now=i + 1) for i in range(5)]
        result = self.policy.select_events_for_detail(items, max_detail_requests=5)
        for eid in result:
            self.assertIsInstance(eid, str)


# ─────────────────────────────────────────────────────────────────────────────
# Section 2 — ScanScheduler unit tests
# ─────────────────────────────────────────────────────────────────────────────

class _MockService:
    """Minimal service mock for scheduler tests."""
    def __init__(self, scan_delay=0.0, fail=False, fail_with_409=False):
        self._scan_delay = scan_delay
        self._fail = fail
        self._fail_with_409 = fail_with_409
        self.scan_calls = 0

    def run_scan(self, config=None, scan_source="AUTOMATED"):
        self.scan_calls += 1
        if self._scan_delay > 0:
            time.sleep(self._scan_delay)
        if self._fail_with_409:
            raise APIError("Scan already in progress.", status_code=409)
        if self._fail:
            raise RuntimeError("Simulated scan failure")
        return {
            "execution_id": f"sched_test_{self.scan_calls}",
            "cycle_status": "SUCCESS",
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "duration_seconds": 0.1,
        }


class TestScanScheduler(unittest.TestCase):

    def test_11_scheduler_initial_state_disabled(self):
        """Scheduler must start disabled by default."""
        svc = _MockService()
        sched = ScanScheduler(service=svc, interval_minutes=15, enabled=False)
        status = sched.get_status()
        self.assertFalse(status["enabled"])
        self.assertEqual(status["interval_minutes"], 15)
        self.assertIsNone(status["next_scan_at"])

    def test_12_scheduler_enable_updates_config(self):
        """enable() must flip enabled=True and set next_scan_at."""
        svc = _MockService()
        sched = ScanScheduler(service=svc, interval_minutes=15, enabled=False)
        sched.enable(interval_minutes=10, scan_scope="POPULAR", hours_ahead=12, event_limit=20)
        status = sched.get_status()
        self.assertTrue(status["enabled"])
        self.assertEqual(status["interval_minutes"], 10)
        self.assertEqual(status["scan_scope"], "POPULAR")
        self.assertEqual(status["hours_ahead"], 12)
        self.assertEqual(status["event_limit"], 20)
        self.assertIsNotNone(status["next_scan_at"])
        sched.stop()

    def test_13_scheduler_disable_updates_config(self):
        """disable() must set enabled=False and clear next_scan_at."""
        svc = _MockService()
        sched = ScanScheduler(service=svc, interval_minutes=15, enabled=True)
        sched.start()
        sched.enable(interval_minutes=5)  # ensure next_scan_at is set
        sched.disable()
        status = sched.get_status()
        self.assertFalse(status["enabled"])
        self.assertIsNone(status["next_scan_at"])
        sched.stop()

    def test_14_scheduler_does_not_overlap_scans(self):
        """
        If service.run_scan raises 409, the scheduler must skip that cycle
        and not call run_scan a second time simultaneously.
        """
        svc = _MockService(fail_with_409=True)
        sched = ScanScheduler(service=svc, interval_minutes=15, enabled=False)
        # run_scan_now must propagate 409 to caller
        with self.assertRaises(APIError) as ctx:
            sched.run_scan_now()
        self.assertEqual(ctx.exception.status_code, 409)
        # only one attempt was made
        self.assertEqual(svc.scan_calls, 1)

    def test_15_scheduler_survives_scan_failure(self):
        """
        A failed scan must update last_error but NOT permanently break the scheduler.
        Status must remain operational.
        """
        svc = _MockService()
        sched = ScanScheduler(service=svc, interval_minutes=15, enabled=False)

        # Simulate failure by temporarily breaking the service
        svc._fail = True
        try:
            sched.run_scan_now()
        except Exception:
            pass

        # Scheduler itself should still be functional
        svc._fail = False
        result = sched.run_scan_now()  # should succeed now
        self.assertEqual(result["cycle_status"], "SUCCESS")

    def test_16_scheduler_next_scan_at_when_enabled(self):
        """next_scan_at must be in the future when enabled."""
        svc = _MockService()
        sched = ScanScheduler(service=svc, interval_minutes=5, enabled=False)
        sched.enable(interval_minutes=5)
        status = sched.get_status()
        self.assertIsNotNone(status["next_scan_at"])
        next_dt = datetime.fromisoformat(status["next_scan_at"])
        self.assertGreater(next_dt, datetime.now(timezone.utc))
        sched.stop()

    def test_17_scheduler_next_scan_at_when_disabled_is_none(self):
        """next_scan_at must be None when scheduler is disabled."""
        svc = _MockService()
        sched = ScanScheduler(service=svc, interval_minutes=15, enabled=False)
        status = sched.get_status()
        self.assertIsNone(status["next_scan_at"])


# ─────────────────────────────────────────────────────────────────────────────
# Section 3 — Scheduler API endpoint integration tests
# ─────────────────────────────────────────────────────────────────────────────

class TestSchedulerAPIEndpoints(unittest.TestCase):

    def setUp(self):
        db = DatabaseManager(DatabaseConfig.default_sqlite_in_memory())
        db.create_tables()
        self.service = PlatformAPIService(db_manager=db)
        self.router = APIRouter(service=self.service)

    def test_18_scheduler_status_endpoint_returns_200(self):
        """GET scheduler status must return 200 with enabled=False by default."""
        res = self.router.handle_get_scheduler_status()
        self.assertEqual(res.status_code, 200)
        self.assertIn("enabled", res.data)
        self.assertFalse(res.data["enabled"])

    def test_19_scheduler_configure_enables(self):
        """POST configure with enabled=True must enable scheduler."""
        payload = {"enabled": True, "interval_minutes": 10}
        res = self.router.handle_post_scheduler_configure(payload)
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.data["enabled"])
        self.assertEqual(res.data["interval_minutes"], 10)
        # Clean up: disable
        self.service.scheduler.disable()
        self.service.scheduler.stop()

    def test_20_scheduler_configure_interval(self):
        """POST configure with interval_minutes=30 must persist that value."""
        payload = {"enabled": False, "interval_minutes": 30, "scan_scope": "ALL"}
        res = self.router.handle_post_scheduler_configure(payload)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["interval_minutes"], 30)
        self.assertEqual(res.data["scan_scope"], "ALL")


if __name__ == "__main__":
    unittest.main()
