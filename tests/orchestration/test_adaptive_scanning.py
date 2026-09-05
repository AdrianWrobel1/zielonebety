"""
Tests for Stage B.2: Adaptive Scanning Engine.

Protects:
1. Kickoff proximity interval calculation (post-kickoff -> STOP, T-60m -> 15m, T-2h -> 30m, T-6h -> 45m, T-24h -> 90m).
2. Fixture priority enhancement with kickoff weight.
3. Next-day evening discovery window (19:00 - 23:00) behavior.
4. Provider budget limits prevent uncontrolled scans.
5. Stop pre-match scanning once match is in progress or finished.
"""

from datetime import datetime, timezone, timedelta
import unittest

from scanner.adaptive_scanning import (
    AdaptiveScanningEngine,
    AdaptiveScanDecision,
    calculate_adaptive_fixture_priority,
)


class TestAdaptiveScanning(unittest.TestCase):

    def setUp(self):
        self.engine = AdaptiveScanningEngine()

    def test_post_kickoff_event_stops_pre_match_scanning(self):
        """1. Fixtures whose kickoff is in the past are marked STOP / excluded from pre-match scan."""
        now = datetime(2026, 9, 5, 18, 0, 0, tzinfo=timezone.utc)
        past_kickoffs = [
            (now - timedelta(minutes=15)).isoformat(),
            (now - timedelta(hours=2)).isoformat(),
        ]

        decision = self.engine.evaluate_scan_cycle(
            current_time=now,
            upcoming_kickoffs=past_kickoffs,
            last_scan_at=(now - timedelta(minutes=30)).isoformat(),
        )

        # No active pre-match fixtures -> stops / idle sleep
        self.assertTrue(decision.is_idle)
        self.assertGreaterEqual(decision.next_interval_minutes, 60)
        self.assertEqual(decision.active_fixtures_count, 0)

    def test_kickoff_proximity_drives_dynamic_intervals(self):
        """2. T-60m gets 15m, T-2h gets 25-30m, T-6h gets 45-60m, T-24h gets 90-120m."""
        now = datetime(2026, 9, 5, 12, 0, 0, tzinfo=timezone.utc)

        # T-45m (very high priority)
        d_t60 = self.engine.evaluate_scan_cycle(
            current_time=now,
            upcoming_kickoffs=[(now + timedelta(minutes=45)).isoformat()],
        )
        self.assertEqual(d_t60.phase, "T_MINUS_60M")
        self.assertLessEqual(d_t60.next_interval_minutes, 15)

        # T-90m (high priority T-2h)
        d_t2h = self.engine.evaluate_scan_cycle(
            current_time=now,
            upcoming_kickoffs=[(now + timedelta(minutes=90)).isoformat()],
        )
        self.assertEqual(d_t2h.phase, "T_MINUS_2H")
        self.assertLessEqual(d_t2h.next_interval_minutes, 30)

        # T-4h (medium priority T-6h)
        d_t6h = self.engine.evaluate_scan_cycle(
            current_time=now,
            upcoming_kickoffs=[(now + timedelta(hours=4)).isoformat()],
        )
        self.assertEqual(d_t6h.phase, "T_MINUS_6H")
        self.assertLessEqual(d_t6h.next_interval_minutes, 60)

        # T-18h (low priority / discovery)
        d_t24h = self.engine.evaluate_scan_cycle(
            current_time=now,
            upcoming_kickoffs=[(now + timedelta(hours=18)).isoformat()],
        )
        self.assertEqual(d_t24h.phase, "T_MINUS_24H")
        self.assertGreaterEqual(d_t24h.next_interval_minutes, 60)

    def test_next_day_evening_discovery_window(self):
        """3. Evening window (e.g. 20:30 Warsaw time) elevates priority for next-day matches."""
        # 18:30 UTC == 20:30 Warsaw (CEST) - inside 16:00-22:00 high-activity window
        evening_time = datetime(2026, 9, 5, 18, 30, 0, tzinfo=timezone.utc)
        # Next day matches at 16:30, 18:30 Warsaw time (20h - 22h away)
        next_day_kickoffs = [
            (evening_time + timedelta(hours=20)).isoformat(),
            (evening_time + timedelta(hours=22)).isoformat(),
        ]

        decision = self.engine.evaluate_scan_cycle(
            current_time=evening_time,
            upcoming_kickoffs=next_day_kickoffs,
        )

        self.assertTrue(decision.is_evening_discovery_window)
        self.assertTrue(decision.should_trigger_evening_digest)
        # In evening discovery window, interval is tightened to discover newly posted prop markets
        self.assertLessEqual(decision.next_interval_minutes, 35)

    def test_budget_ceiling_throttles_excessive_scans(self):
        """4. Budget ceiling prevents uncontrolled scans if requests limit is reached."""
        now = datetime(2026, 9, 5, 15, 0, 0, tzinfo=timezone.utc)
        kickoff_soon = [(now + timedelta(minutes=30)).isoformat()]

        # Under normal conditions -> 15 min
        normal_d = self.engine.evaluate_scan_cycle(
            current_time=now,
            upcoming_kickoffs=kickoff_soon,
            hourly_requests_used=10,
            hourly_budget_ceiling=100,
        )
        self.assertEqual(normal_d.next_interval_minutes, 15)

        # Budget exhausted (> 90% used) -> throttled interval
        throttled_d = self.engine.evaluate_scan_cycle(
            current_time=now,
            upcoming_kickoffs=kickoff_soon,
            hourly_requests_used=95,
            hourly_budget_ceiling=100,
        )
        self.assertTrue(throttled_d.is_budget_throttled)
        self.assertGreaterEqual(throttled_d.next_interval_minutes, 45)

    def test_fixture_priority_boosts_kickoff_proximity_without_whitelisting(self):
        """5. Priority score incorporates kickoff proximity without excluding lower tiers."""
        now = datetime(2026, 9, 5, 12, 0, 0, tzinfo=timezone.utc)
        t_soon = (now + timedelta(minutes=45)).isoformat()
        t_far = (now + timedelta(hours=48)).isoformat()

        # Same tier & markets: sooner match has higher adaptive score
        score_soon = calculate_adaptive_fixture_priority(tier=2, market_coverage=50, kickoff=t_soon, now=now)
        score_far = calculate_adaptive_fixture_priority(tier=2, market_coverage=50, kickoff=t_far, now=now)
        self.assertGreater(score_soon, score_far)

        # Lower tier (Tier 3) with high market coverage and imminent kickoff can compete with far Tier 1
        score_tier3_soon = calculate_adaptive_fixture_priority(tier=3, market_coverage=80, kickoff=t_soon, now=now)
        score_tier1_far = calculate_adaptive_fixture_priority(tier=1, market_coverage=30, kickoff=t_far, now=now)
        self.assertGreater(score_tier3_soon, score_tier1_far)

    def test_warsaw_timezone_dst_and_high_activity_window(self):
        """6. Confirms Europe/Warsaw timezone is used with automatic DST awareness and exact 16:00-22:00 boundaries."""
        # --- Summer date: Jul 15 (CEST = UTC+2) ---
        # 13:59 UTC == 15:59 Warsaw -> outside high-activity
        t_1559_summer = datetime(2026, 7, 15, 13, 59, 0, tzinfo=timezone.utc)
        self.assertFalse(self.engine.evaluate_scan_cycle(current_time=t_1559_summer).is_high_activity_window)

        # 14:00 UTC == 16:00 Warsaw -> high-activity starts
        t_1600_summer = datetime(2026, 7, 15, 14, 0, 0, tzinfo=timezone.utc)
        self.assertTrue(self.engine.evaluate_scan_cycle(current_time=t_1600_summer).is_high_activity_window)

        # 19:59 UTC == 21:59 Warsaw -> high-activity active
        t_2159_summer = datetime(2026, 7, 15, 19, 59, 0, tzinfo=timezone.utc)
        self.assertTrue(self.engine.evaluate_scan_cycle(current_time=t_2159_summer).is_high_activity_window)

        # 20:00 UTC == 22:00 Warsaw -> high-activity ends
        t_2200_summer = datetime(2026, 7, 15, 20, 0, 0, tzinfo=timezone.utc)
        self.assertFalse(self.engine.evaluate_scan_cycle(current_time=t_2200_summer).is_high_activity_window)

        # --- Winter date: Jan 15 (CET = UTC+1) ---
        # 14:59 UTC == 15:59 Warsaw -> outside high-activity
        t_1559_winter = datetime(2026, 1, 15, 14, 59, 0, tzinfo=timezone.utc)
        self.assertFalse(self.engine.evaluate_scan_cycle(current_time=t_1559_winter).is_high_activity_window)

        # 15:00 UTC == 16:00 Warsaw -> high-activity starts
        t_1600_winter = datetime(2026, 1, 15, 15, 0, 0, tzinfo=timezone.utc)
        self.assertTrue(self.engine.evaluate_scan_cycle(current_time=t_1600_winter).is_high_activity_window)

        # 20:59 UTC == 21:59 Warsaw -> high-activity active
        t_2159_winter = datetime(2026, 1, 15, 20, 59, 0, tzinfo=timezone.utc)
        self.assertTrue(self.engine.evaluate_scan_cycle(current_time=t_2159_winter).is_high_activity_window)

        # 21:00 UTC == 22:00 Warsaw -> high-activity ends
        t_2200_winter = datetime(2026, 1, 15, 21, 0, 0, tzinfo=timezone.utc)
        self.assertFalse(self.engine.evaluate_scan_cycle(current_time=t_2200_winter).is_high_activity_window)


if __name__ == "__main__":
    unittest.main()
