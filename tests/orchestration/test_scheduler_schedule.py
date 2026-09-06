"""
Unit and integration tests for ScanScheduler time-based scheduling,
Europe/Warsaw timezone handling, composite cycle semantics, and failure isolation.
"""

import threading
import time
import unittest
from datetime import datetime, date, time as dt_time, timedelta, timezone
from unittest.mock import MagicMock, patch

try:
    from zoneinfo import ZoneInfo
    WARSAW_TZ = ZoneInfo("Europe/Warsaw")
except Exception:
    from normalization.identity import get_warsaw_tz
    WARSAW_TZ = get_warsaw_tz()

from orchestration.scheduler import (
    ScheduleWindow,
    validate_schedule_windows,
    parse_time_to_minutes,
    get_active_window,
    compute_next_trigger,
    DEFAULT_SCHEDULE_WINDOWS,
    ScanScheduler,
)
from api.exceptions import APIError


class TestSchedulerScheduleModel(unittest.TestCase):
    """Tests for ScheduleWindow parsing, validation, and Warsaw window lookup."""

    def test_parse_time_to_minutes(self):
        self.assertEqual(parse_time_to_minutes("00:00"), 0)
        self.assertEqual(parse_time_to_minutes("08:30"), 8 * 60 + 30)
        self.assertEqual(parse_time_to_minutes("23:59"), 23 * 60 + 59)
        # End of day representations
        self.assertEqual(parse_time_to_minutes("24:00", is_end=True), 1440)
        self.assertEqual(parse_time_to_minutes("00:00", is_end=True), 1440)

    def test_default_schedule_windows_valid(self):
        validated = validate_schedule_windows(DEFAULT_SCHEDULE_WINDOWS)
        self.assertEqual(len(validated), 5)
        self.assertEqual(validated[0].start_time, "00:00")
        self.assertEqual(validated[0].end_time, "08:00")
        self.assertEqual(validated[0].interval_minutes, 120)
        self.assertEqual(validated[3].start_time, "18:00")
        self.assertEqual(validated[3].end_time, "23:00")
        self.assertEqual(validated[3].interval_minutes, 15)

    def test_overlapping_windows_rejected(self):
        invalid = [
            ScheduleWindow("08:00", "15:00", 60),
            ScheduleWindow("14:00", "20:00", 30),  # overlaps with 14:00-15:00
        ]
        with self.assertRaises(ValueError) as ctx:
            validate_schedule_windows(invalid)
        self.assertIn("overlap", str(ctx.exception).lower())

    def test_inverted_window_rejected(self):
        invalid = [
            ScheduleWindow("18:00", "12:00", 30),
        ]
        with self.assertRaises(ValueError) as ctx:
            validate_schedule_windows(invalid)
        self.assertIn("must be before end", str(ctx.exception).lower())

    def test_invalid_interval_rejected(self):
        invalid = [
            ScheduleWindow("08:00", "12:00", 0),
        ]
        with self.assertRaises(ValueError) as ctx:
            validate_schedule_windows(invalid)
        self.assertIn("interval", str(ctx.exception).lower())

    def test_active_window_selection_across_day(self):
        # Base date in Europe/Warsaw
        base_d = date(2026, 9, 6)

        # 03:30 -> Overnight window (120m)
        t_0330 = datetime.combine(base_d, dt_time(3, 30), tzinfo=WARSAW_TZ)
        w1 = get_active_window(t_0330, DEFAULT_SCHEDULE_WINDOWS)
        self.assertEqual(w1.start_time, "00:00")
        self.assertEqual(w1.end_time, "08:00")
        self.assertEqual(w1.interval_minutes, 120)

        # 10:00 -> Morning window (60m)
        t_1000 = datetime.combine(base_d, dt_time(10, 0), tzinfo=WARSAW_TZ)
        w2 = get_active_window(t_1000, DEFAULT_SCHEDULE_WINDOWS)
        self.assertEqual(w2.start_time, "08:00")
        self.assertEqual(w2.end_time, "14:00")
        self.assertEqual(w2.interval_minutes, 60)

        # 16:30 -> Afternoon window (30m)
        t_1630 = datetime.combine(base_d, dt_time(16, 30), tzinfo=WARSAW_TZ)
        w3 = get_active_window(t_1630, DEFAULT_SCHEDULE_WINDOWS)
        self.assertEqual(w3.start_time, "14:00")
        self.assertEqual(w3.end_time, "18:00")
        self.assertEqual(w3.interval_minutes, 30)

        # 19:45 -> Evening peak window (15m)
        t_1945 = datetime.combine(base_d, dt_time(19, 45), tzinfo=WARSAW_TZ)
        w4 = get_active_window(t_1945, DEFAULT_SCHEDULE_WINDOWS)
        self.assertEqual(w4.start_time, "18:00")
        self.assertEqual(w4.end_time, "23:00")
        self.assertEqual(w4.interval_minutes, 15)

        # 23:15 -> Late night window (60m)
        t_2315 = datetime.combine(base_d, dt_time(23, 15), tzinfo=WARSAW_TZ)
        w5 = get_active_window(t_2315, DEFAULT_SCHEDULE_WINDOWS)
        self.assertEqual(w5.start_time, "23:00")
        self.assertEqual(w5.end_time, "24:00")
        self.assertEqual(w5.interval_minutes, 60)

    def test_compute_next_trigger_within_window(self):
        base_d = date(2026, 9, 6)
        # Scan finished at 18:30 in Europe/Warsaw (15m window)
        last_scan = datetime.combine(base_d, dt_time(18, 30), tzinfo=WARSAW_TZ)
        now = datetime.combine(base_d, dt_time(18, 32), tzinfo=WARSAW_TZ)

        next_trig = compute_next_trigger(now, last_scan, DEFAULT_SCHEDULE_WINDOWS)
        expected = datetime.combine(base_d, dt_time(18, 45), tzinfo=WARSAW_TZ)
        self.assertEqual(next_trig, expected)

    def test_compute_next_trigger_transitions_at_window_boundary(self):
        base_d = date(2026, 9, 6)
        # Scan completed at 17:45 in 14:00-18:00 (30m interval).
        # Normal 30m addition would give 18:15, but at 18:00 the 15m peak window begins.
        # It should transition at 18:00.
        last_scan = datetime.combine(base_d, dt_time(17, 45), tzinfo=WARSAW_TZ)
        now = datetime.combine(base_d, dt_time(17, 50), tzinfo=WARSAW_TZ)

        next_trig = compute_next_trigger(now, last_scan, DEFAULT_SCHEDULE_WINDOWS)
        expected = datetime.combine(base_d, dt_time(18, 0), tzinfo=WARSAW_TZ)
        self.assertEqual(next_trig, expected)


class TestSchedulerCompositeCycleSemantics(unittest.TestCase):
    """Tests for composite cycle execution, failure isolation, and concurrency."""

    def setUp(self):
        self.mock_service = MagicMock()
        self.scheduler = ScanScheduler(service=self.mock_service, enabled=False, execute_ultra=True, execute_global_props=True)

    def tearDown(self):
        self.scheduler.stop()

    def test_cycle_both_success_reports_success(self):
        mock_ultra = {"execution_id": "ultra_1", "status": "SUCCESS", "opportunities": [1, 2]}
        mock_props = {"status": "SUCCESS", "qualified_opportunities": [1]}

        self.scheduler.run_ultra_scan_now = MagicMock(return_value=mock_ultra)
        self.scheduler.run_global_props_scan_now = MagicMock(return_value=mock_props)

        res = self.scheduler.execute_automated_cycle(manual=True)
        self.assertEqual(res["cycle_status"], "SUCCESS")
        self.assertEqual(res["scanners"]["ultra"]["status"], "SUCCESS")
        self.assertEqual(res["scanners"]["global_props"]["status"], "SUCCESS")

        status = self.scheduler.get_status()
        self.assertEqual(status["last_scan_status"], "SUCCESS")
        self.assertIsNotNone(status["last_cycle"])

    def test_cycle_partial_when_ultra_fails(self):
        self.scheduler.run_ultra_scan_now = MagicMock(side_effect=RuntimeError("Ultra bookmaker timeout"))
        mock_props = {"status": "SUCCESS", "qualified_opportunities": [1, 2, 3]}
        self.scheduler.run_global_props_scan_now = MagicMock(return_value=mock_props)

        res = self.scheduler.execute_automated_cycle(manual=True)
        self.assertEqual(res["cycle_status"], "PARTIAL")
        self.assertEqual(res["scanners"]["ultra"]["status"], "FAILED")
        self.assertEqual(res["scanners"]["global_props"]["status"], "SUCCESS")

        status = self.scheduler.get_status()
        self.assertEqual(status["last_scan_status"], "PARTIAL")

    def test_cycle_partial_when_props_fails(self):
        mock_ultra = {"execution_id": "ultra_2", "status": "SUCCESS", "opportunities": [1]}
        self.scheduler.run_ultra_scan_now = MagicMock(return_value=mock_ultra)
        self.scheduler.run_global_props_scan_now = MagicMock(side_effect=RuntimeError("StatsHub API error"))

        res = self.scheduler.execute_automated_cycle(manual=True)
        self.assertEqual(res["cycle_status"], "PARTIAL")
        self.assertEqual(res["scanners"]["ultra"]["status"], "SUCCESS")
        self.assertEqual(res["scanners"]["global_props"]["status"], "FAILED")

        status = self.scheduler.get_status()
        self.assertEqual(status["last_scan_status"], "PARTIAL")

    def test_cycle_failed_when_both_fail(self):
        self.scheduler.run_ultra_scan_now = MagicMock(side_effect=RuntimeError("Ultra failure"))
        self.scheduler.run_global_props_scan_now = MagicMock(side_effect=RuntimeError("Props failure"))

        res = self.scheduler.execute_automated_cycle(manual=True)
        self.assertEqual(res["cycle_status"], "FAILED")
        self.assertEqual(res["scanners"]["ultra"]["status"], "FAILED")
        self.assertEqual(res["scanners"]["global_props"]["status"], "FAILED")

        status = self.scheduler.get_status()
        self.assertEqual(status["last_scan_status"], "FAILED")

    def test_concurrency_protection_raises_409_on_overlapping_cycle(self):
        # Acquire cycle lock manually to simulate running cycle
        acquired = self.scheduler._cycle_lock.acquire(blocking=False)
        self.assertTrue(acquired)
        try:
            with self.assertRaises(APIError) as ctx:
                self.scheduler.run_scan_now()
            self.assertEqual(ctx.exception.status_code, 409)
        finally:
            self.scheduler._cycle_lock.release()


if __name__ == "__main__":
    unittest.main()
