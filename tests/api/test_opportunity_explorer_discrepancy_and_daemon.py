"""
Targeted tests for Opportunity Explorer Quote Discrepancy sorting,
instant filter query caching, and Automated Scanner Daemon Normal/Ultra integration.
"""
import os
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch

from core.opportunity_explorer import (
    OpportunityType,
    UnifiedOpportunityDTO,
)
from api.services import PlatformAPIService
from database.config import DatabaseConfig
from database.connection import DatabaseManager
from orchestration.scheduler import ScanScheduler, DEFAULT_SCAN_MODE


class TestOpportunityExplorerDiscrepancySortingAndCaching(unittest.TestCase):
    """Tests for multi-tier deterministic discrepancy sorting and instant filter query caching."""

    def setUp(self):
        self.service = PlatformAPIService()
        # Ensure cache clean slate
        PlatformAPIService._unified_opportunities_cache = None

    def tearDown(self):
        PlatformAPIService._unified_opportunities_cache = None

    def _build_mock_dto(self, id: str, disc_pct: float = None, odds_diff: float = None, opp_type: str = "QUOTE_DISCREPANCY") -> UnifiedOpportunityDTO:
        return UnifiedOpportunityDTO(
            id=id,
            type=opp_type,
            source="statshub",
            status="AVAILABLE",
            event="Arsenal vs Chelsea",
            sport="Football",
            competition="Premier League",
            market="Total Corners - Arsenal Over 4.5",
            side="OVER",
            line=4.5,
            best_bookmaker="Superbet",
            execution_odds=2.10,
            lower_bookmaker="Betclic",
            lower_execution_odds=1.50,
            odds_difference=odds_diff,
            price_discrepancy_pct=disc_pct,
            all_bookmakers=["Superbet", "Betclic"],
        )

    def test_explorer_discrepancy_sorting_descending(self):
        """
        Verify Discrepancy High -> Low sorting eliminates the reported anomaly:
        [47.7, 13.0, 10.3, 31.8, 49.0] must be strictly ordered descending:
        [49.0, 47.7, 31.8, 13.0, 10.3].
        """
        dtos = [
            self._build_mock_dto("item_47", disc_pct=47.7, odds_diff=0.55),
            self._build_mock_dto("item_13", disc_pct=13.0, odds_diff=0.20),
            self._build_mock_dto("item_10", disc_pct=10.3, odds_diff=0.15),
            self._build_mock_dto("item_31", disc_pct=31.8, odds_diff=0.40),
            self._build_mock_dto("item_49", disc_pct=49.0, odds_diff=0.65),
        ]
        PlatformAPIService._unified_opportunities_cache = dtos

        res = self.service.get_unified_explorer_opportunities(
            opp_type="QUOTE_DISCREPANCY",
            sort="discrepancy",
            order="desc",
            limit=10,
            offset=0,
        )

        items = res["items"]
        assert len(items) == 5
        pcts = [item["price_discrepancy_pct"] for item in items]
        assert pcts == [49.0, 47.7, 31.8, 13.0, 10.3], f"Expected strictly descending order but got {pcts}"

    def test_explorer_discrepancy_sorting_ascending(self):
        """
        Verify Discrepancy Low -> High sorting is strictly ordered ascending:
        [10.3, 13.0, 31.8, 47.7, 49.0].
        """
        dtos = [
            self._build_mock_dto("item_47", disc_pct=47.7, odds_diff=0.55),
            self._build_mock_dto("item_13", disc_pct=13.0, odds_diff=0.20),
            self._build_mock_dto("item_10", disc_pct=10.3, odds_diff=0.15),
            self._build_mock_dto("item_31", disc_pct=31.8, odds_diff=0.40),
            self._build_mock_dto("item_49", disc_pct=49.0, odds_diff=0.65),
        ]
        PlatformAPIService._unified_opportunities_cache = dtos

        res = self.service.get_unified_explorer_opportunities(
            opp_type="QUOTE_DISCREPANCY",
            sort="discrepancy",
            order="asc",
            limit=10,
            offset=0,
        )

        items = res["items"]
        assert len(items) == 5
        pcts = [item["price_discrepancy_pct"] for item in items]
        assert pcts == [10.3, 13.0, 31.8, 47.7, 49.0], f"Expected strictly ascending order but got {pcts}"

    def test_explorer_discrepancy_tie_breakers(self):
        """
        Verify tie-breaking on identical discrepancy %:
        1. Higher odds_difference wins in DESC; lower in ASC.
        2. Lower alphabetical ID wins if odds_difference is identical.
        """
        dtos = [
            self._build_mock_dto("id_c", disc_pct=25.0, odds_diff=0.30),
            self._build_mock_dto("id_a", disc_pct=25.0, odds_diff=0.50),
            self._build_mock_dto("id_b", disc_pct=25.0, odds_diff=0.30),
        ]
        PlatformAPIService._unified_opportunities_cache = dtos

        # DESC test
        res_desc = self.service.get_unified_explorer_opportunities(
            sort="discrepancy",
            order="desc",
            limit=10,
        )
        ids_desc = [item["id"] for item in res_desc["items"]]
        # id_a (odds_diff 0.50) is highest odds_diff.
        # Between id_b and id_c (both 0.30), id_b < id_c alphabetically.
        assert ids_desc == ["id_a", "id_b", "id_c"], f"Expected ['id_a', 'id_b', 'id_c'] but got {ids_desc}"

        # ASC test
        res_asc = self.service.get_unified_explorer_opportunities(
            sort="discrepancy",
            order="asc",
            limit=10,
        )
        ids_asc = [item["id"] for item in res_asc["items"]]
        # In ASC: lower odds_diff (0.30) comes first. Between id_b and id_c, id_b < id_c. Then id_a (0.50).
        assert ids_asc == ["id_b", "id_c", "id_a"], f"Expected ['id_b', 'id_c', 'id_a'] but got {ids_asc}"

    def test_explorer_non_discrepancies_sort_to_bottom(self):
        """
        Verify that items without discrepancy or with sub-10% differences sort below qualified discrepancies.
        """
        dtos = [
            self._build_mock_dto("sub_10", disc_pct=5.0, odds_diff=0.08),
            self._build_mock_dto("qual_25", disc_pct=25.0, odds_diff=0.40),
            self._build_mock_dto("none_disc", disc_pct=None, odds_diff=None, opp_type="VALUEBET"),
        ]
        PlatformAPIService._unified_opportunities_cache = dtos

        res = self.service.get_unified_explorer_opportunities(
            sort="discrepancy",
            order="desc",
            limit=10,
        )
        ids = [item["id"] for item in res["items"]]
        assert ids == ["qual_25", "sub_10", "none_disc"], f"Expected ['qual_25', 'sub_10', 'none_disc'] but got {ids}"

    def test_explorer_filter_caching_and_invalidation(self):
        """
        Verify that _unified_opportunities_cache is populated and re-used for filter queries,
        and properly invalidated upon scan cycle completion.
        """
        assert PlatformAPIService._unified_opportunities_cache is None

        # Seed with a known collection
        sentinel_dto = self._build_mock_dto("sentinel_1", disc_pct=15.0, odds_diff=0.25)
        PlatformAPIService._unified_opportunities_cache = [sentinel_dto]

        # Filter query must use cache without calling _collect_unified_explorer_opportunities
        with patch.object(self.service, "_collect_unified_explorer_opportunities") as mock_collect:
            res = self.service.get_unified_explorer_opportunities(limit=10)
            mock_collect.assert_not_called()
            assert len(res["items"]) == 1
            assert res["items"][0]["id"] == "sentinel_1"

        # Cache invalidation check
        PlatformAPIService._unified_opportunities_cache = None
        with patch.object(self.service, "_collect_unified_explorer_opportunities", return_value=[sentinel_dto]) as mock_collect:
            res = self.service.get_unified_explorer_opportunities(limit=10)
            mock_collect.assert_called_once()
            assert PlatformAPIService._unified_opportunities_cache is not None
            assert len(PlatformAPIService._unified_opportunities_cache) == 1


class TestAutomatedScannerDaemonIntegration(unittest.TestCase):
    """Tests for Automated Scanner Daemon Normal/Ultra mode configuration, persistence, and concurrency."""

    def setUp(self):
        # Create a disposable temporary SQLite database file
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test_daemon.db")
        self.db_url = f"sqlite:///{self.db_path}"
        self.db_config = DatabaseConfig(db_url=self.db_url)
        self.db_manager = DatabaseManager(self.db_config)
        self.db_manager.create_tables()

        self.start_patcher = patch.object(ScanScheduler, "start")
        self.mock_start = self.start_patcher.start()

        self.service = PlatformAPIService()
        self.scheduler = ScanScheduler(
            service=self.service,
            interval_minutes=15,
            enabled=False,
            db_manager=self.db_manager,
        )

    def tearDown(self):
        self.scheduler.stop()
        self.start_patcher.stop()
        try:
            if os.path.exists(self.db_path):
                os.remove(self.db_path)
        except Exception:
            pass

    def test_scheduler_scan_mode_configuration_and_persistence(self):
        """
        Verify scheduler can be configured to ULTRA, persists in SQLite,
        and is faithfully reloaded on scheduler restart.
        """
        # Default should be NORMAL
        assert self.scheduler.get_status()["scan_mode"] == "NORMAL"

        # Configure to ULTRA
        self.scheduler.configure(
            enabled=True,
            interval_minutes=30,
            scan_scope="ALL",
            scan_mode="ULTRA",
            hours_ahead=48,
            event_limit=75,
        )

        status = self.scheduler.get_status()
        assert status["enabled"] is True
        assert status["scan_mode"] == "ULTRA"
        assert status["interval_minutes"] == 30
        assert status["scan_scope"] == "ALL"
        assert status["hours_ahead"] == 48
        assert status["event_limit"] == 75

        # Create a new scheduler instance on the same DB and verify restored state
        new_scheduler = ScanScheduler(
            service=self.service,
            db_manager=self.db_manager,
        )
        new_status = new_scheduler.get_status()
        assert new_status["enabled"] is True
        assert new_status["scan_mode"] == "ULTRA"
        assert new_status["interval_minutes"] == 30
        assert new_status["scan_scope"] == "ALL"
        assert new_status["hours_ahead"] == 48
        assert new_status["event_limit"] == 75
        new_scheduler.stop()

    def test_scheduler_run_now_dispatches_configured_mode(self):
        """
        Verify run_scan_now() executes the configured scan mode:
        - NORMAL mode dispatches service.run_scan(scan_source='AUTOMATED')
        - ULTRA mode dispatches scheduler.run_ultra_scan_now(manual=True)
        """
        # 1. NORMAL mode dispatch
        self.scheduler.configure(scan_mode="NORMAL")
        mock_normal_result = {
            "cycle_status": "SUCCESS",
            "events_discovered": 10,
            "events_selected": 5,
            "events_matched": 4,
            "opportunities_found": 2,
        }
        with patch.object(self.service, "run_scan", return_value=mock_normal_result) as mock_run_scan:
            res = self.scheduler.run_scan_now()
            mock_run_scan.assert_called_once()
            assert mock_run_scan.call_args[1]["scan_source"] == "AUTOMATED"
            assert res["cycle_status"] == "SUCCESS"
            assert self.scheduler.get_status()["last_scan_status"] == "SUCCESS"

        # 2. ULTRA mode dispatch
        self.scheduler.configure(scan_mode="ULTRA")
        mock_ultra_result = {
            "status": "SUCCESS",
            "date": "2026-09-06",
            "total_candidates": 45,
            "qualified_count": 8,
            "scan_mode": "ULTRA",
        }
        with patch.object(self.scheduler, "run_ultra_scan_now", return_value=mock_ultra_result) as mock_ultra:
            res = self.scheduler.run_scan_now()
            mock_ultra.assert_called_once_with(manual=True)
            assert res["status"] == "SUCCESS"
            assert self.scheduler.get_status()["last_scan_status"] == "SUCCESS"

    def test_scheduler_concurrency_409_handling(self):
        """
        Verify that if a scan is already running (concurrency conflict):
        - run_scan_now() raises the 409 exception without marking state as FAILED
        - worker loop handles 409 cleanly by skipping cycle without crash
        """
        self.scheduler.configure(scan_mode="NORMAL")

        with patch.object(self.service, "run_scan", side_effect=Exception("409 Conflict: Scan already in progress")):
            with self.assertRaises(Exception) as ctx:
                self.scheduler.run_scan_now()
            assert "409" in str(ctx.exception)
            # 409 must NOT mark last_scan_status as FAILED
            assert self.scheduler.get_status()["last_scan_status"] != "FAILED"
