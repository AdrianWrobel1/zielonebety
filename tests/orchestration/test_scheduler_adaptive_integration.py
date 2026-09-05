"""
Stage B.2: Scheduler Adaptive & Props Notification Integration Tests.

Protects:
1. ScanScheduler status exposes adaptive mode and phase information.
2. run_global_props_scan_now executes global props scan and records timestamps.
3. Telegram failure during props scan is strictly isolated.
4. Adaptive mode toggle works via configure().
"""

from datetime import datetime, timezone
import unittest
from unittest.mock import MagicMock, patch

from database.connection import DatabaseManager
from database.config import DatabaseConfig
from api.services import PlatformAPIService
from orchestration.scheduler import ScanScheduler
from notifications.telegram_client import FakeTelegramClient


class TestSchedulerAdaptiveIntegration(unittest.TestCase):

    def setUp(self):
        self.db_manager = DatabaseManager(DatabaseConfig.default_sqlite_in_memory())
        self.db_manager.create_tables()
        self.service = PlatformAPIService(db_manager=self.db_manager)
        self.service.scheduler.disable()
        self.service.scheduler.stop()

    def tearDown(self):
        if hasattr(self, "scheduler") and self.scheduler:
            self.scheduler.disable()
            self.scheduler.stop()
        self.service.scheduler.disable()
        self.service.scheduler.stop()

    def test_scheduler_status_exposes_adaptive_fields(self):
        """1. ScanScheduler.get_status() includes adaptive_mode, current_phase, and props scan timestamp."""
        self.scheduler = ScanScheduler(service=self.service, interval_minutes=15, enabled=False)
        status = self.scheduler.get_status()

        self.assertIn("adaptive_mode", status)
        self.assertTrue(status["adaptive_mode"])
        self.assertIn("current_phase", status)
        self.assertIn("last_global_props_scan_at", status)
        self.assertIn("evening_discovery_active", status)

    def test_scheduler_configure_toggles_adaptive_mode(self):
        """2. configure(adaptive_mode=False) toggles adaptive mode cleanly."""
        self.scheduler = ScanScheduler(service=self.service, interval_minutes=15, enabled=False)
        self.scheduler.configure(adaptive_mode=False)
        status = self.scheduler.get_status()
        self.assertFalse(status["adaptive_mode"])

    def test_run_global_props_scan_now_records_result_and_isolates_telegram_failure(self):
        """3. run_global_props_scan_now calls service.scan_global_props and isolates Telegram failure."""
        self.scheduler = ScanScheduler(service=self.service, interval_minutes=15, enabled=False)

        mock_props_result = {
            "status": "SUCCESS",
            "qualified_opportunities": [
                {
                    "canonical_prop_key": "prop:PLAYER:fix1:messi:shots:2.5:OVER",
                    "prop_type": "PLAYER",
                    "player_name": "Messi",
                    "team": "Inter Miami",
                    "opponent": "Orlando City",
                    "match_name": "Inter Miami vs Orlando City",
                    "fixture_id": "fix1",
                    "competition": "MLS",
                    "kickoff": "2026-09-06T00:00:00Z",
                    "stat_type": "shots",
                    "line": 2.5,
                    "side": "over",
                    "period": "regular",
                    "scope": "ALL",
                    "participant_role": None,
                    "reference_consensus_odds": 1.90,
                    "reference_fair_probability": 0.55,
                    "reference_fair_odds": 1.82,
                    "reference_sources_count": 2,
                    "reference_odds": [],
                    "best_bookmaker": "Superbet",
                    "best_raw_odds": 2.20,
                    "best_effective_odds": 1.936,
                    "net_ev_pct": 6.48,
                    "gross_ev_pct": 8.0,
                    "value_edge_pp": 4.0,
                    "is_valuebet": True,
                    "status": "QUALIFIED",
                    "reason_code": "QUALIFIED_VALUEBET",
                    "reason": None,
                    "trend_hits": 8,
                    "trend_window": 10,
                    "hit_rate_pct": 80.0,
                    "stat_average": 3.1,
                    "last_5_avg": 3.0,
                    "last_10_avg": 3.1,
                    "execution_odds": {},
                    "provenance": {},
                    "confidence": "HIGH",
                    "superbet_odds": 2.20,
                    "betclic_odds": None,
                    "superbet_status": "AVAILABLE",
                    "betclic_status": "UNAVAILABLE",
                    "reference_probability_pct": 55.0,
                    "action": "VALUE BET",
                    "tier": 2,
                }
            ],
            "diagnostic_candidates": [],
        }

        self.service.scan_global_props = MagicMock(return_value=mock_props_result)

        # Inject a failing notification manager to verify failure isolation
        mock_notif_mgr = MagicMock()
        mock_notif_mgr.process_opportunities.side_effect = RuntimeError("Telegram Network Timeout")
        self.scheduler._props_notification_manager = mock_notif_mgr

        # Should execute successfully without throwing exception
        res = self.scheduler.run_global_props_scan_now()
        self.assertEqual(res["status"], "SUCCESS")
        self.assertEqual(len(res["qualified_opportunities"]), 1)

        # Confirm timestamp was recorded
        status = self.scheduler.get_status()
        self.assertIsNotNone(status["last_global_props_scan_at"])

    def test_adaptive_decision_drives_effective_interval_and_next_scan_at(self):
        """4. Adaptive decision with 90m (e.g. IDLE) drives effective_interval_minutes and next_scan_at, not static 30m."""
        from scanner.adaptive_scanning import AdaptiveScanDecision

        self.scheduler = ScanScheduler(service=self.service, interval_minutes=30, enabled=True)

        idle_decision = AdaptiveScanDecision(
            phase="IDLE",
            next_interval_minutes=90,
            active_fixtures_count=0,
            is_idle=True,
            is_evening_discovery_window=False,
            should_trigger_evening_digest=False,
            is_budget_throttled=False,
            closest_kickoff_minutes=None,
            recommended_scope="POPULAR",
        )

        with self.scheduler._config_lock:
            self.scheduler._last_adaptive_decision = idle_decision

        status = self.scheduler.get_status()
        self.assertEqual(status["interval_minutes"], 30, "Base configured interval must remain 30m")
        self.assertEqual(status.get("effective_interval_minutes"), 90, "Effective interval must reflect adaptive 90m")

        # Simulate scan result recording
        now = datetime.now(timezone.utc)
        self.scheduler._record_result({"execution_id": "test_scan", "cycle_status": "SUCCESS", "completed_at": now.isoformat()})

        next_scan_str = self.scheduler.get_status()["next_scan_at"]
        self.assertIsNotNone(next_scan_str)
        next_scan_dt = datetime.fromisoformat(next_scan_str)
        delta_minutes = (next_scan_dt - now).total_seconds() / 60.0

        # Must be around 90 minutes (e.g. 89-91), NOT 30 minutes
        self.assertAlmostEqual(delta_minutes, 90.0, delta=2.0, msg=f"Next scan was scheduled in {delta_minutes}m, expected ~90m")


if __name__ == "__main__":
    unittest.main()
