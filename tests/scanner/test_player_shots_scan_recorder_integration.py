"""
Targeted tests for Stage A.10: Normal Scan Flow -> Player Shots O0.5 Candidate -> PlayerShotsSnapshotRecorder Integration.

Verifies:
1. Normal scan flow with a qualifying Player Shots Over 0.5 candidate records and persists a pre-match snapshot.
2. Preserves all snapshot invariants (stat=SHOTS, line=0.5, direction=OVER, status=PENDING, observed_at < kickoff_at).
3. Idempotency & scope guard: repeated scan cycles preserve existing records without duplicates, and out-of-scope props (e.g. line 1.5 or fouls) are not snapshotted.
"""

import unittest
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone, timedelta
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.models import BaseORM, PlayerPropSnapshotORM
from database.repositories.player_prop_snapshot_repository import PlayerPropSnapshotRepository
from scanner.player_shots_snapshot_recorder import PlayerShotsSnapshotRecorder
from scanner.global_props_scanner import (
    GlobalPropsScanner,
    GlobalScanScope,
    GlobalScanBudget,
)
from providers.statshub.models import (
    StatsHubFixture,
    StatsHubPlayerStat,
    StatsHubBookmakerOdds,
    StatsHubPropResult,
)


class TestPlayerShotsScanRecorderIntegration(unittest.TestCase):

    def setUp(self):
        # In-memory SQLite session for isolated, ultra-fast test execution
        self.engine = create_engine("sqlite:///:memory:")
        BaseORM.metadata.create_all(self.engine)
        self.session_factory = sessionmaker(bind=self.engine)
        self.session = self.session_factory()
        self.repository = PlayerPropSnapshotRepository(self.session)
        self.recorder = PlayerShotsSnapshotRecorder(repository=self.repository)

    def tearDown(self):
        self.session.close()
        self.engine.dispose()

    @patch("scanner.global_props_scanner.SuperbetProvider")
    @patch("scanner.global_props_scanner.BetclicProvider")
    @patch("scanner.global_props_scanner.StatsHubProvider")
    @patch("scanner.global_props_scanner.StatsHubTeamPropsProvider")
    @patch("scanner.global_props_scanner.ExecutionMarketEngine.extract_team_quotes_from_graphs")
    @patch("scanner.global_props_scanner.ExecutionMarketEngine.extract_quotes_from_graphs")
    def test_global_props_scanner_persists_player_shots_snapshot(
        self,
        mock_extract_player,
        mock_extract_team,
        mock_team_provider,
        mock_player_provider,
        mock_bc_p,
        mock_sb_p,
    ):
        """TEST 1 (RED -> GREEN): Normal scan with Player Shots O0.5 candidate persists snapshot into repository."""
        now = datetime.now(timezone.utc)
        kickoff = now + timedelta(hours=4)
        kickoff_str = kickoff.strftime("%Y-%m-%d %H:%M UTC")

        mock_shots_prop = StatsHubPropResult(
            player_stat=StatsHubPlayerStat(
                player_id=201,
                player_name="Cian Ashford",
                team="Cardiff City",
                opponent="Queens Park Rangers",
                stat_type="shots",
                line=0.5,
                odds_type="over",
                hit_rate_pct=80.0,
                hit_rate_count=8,
                sample_size=10,
                average=1.8,
                position="F",
                fixture=StatsHubFixture(
                    fixture_id="fix-cardiff-qpr",
                    home_team="Cardiff City",
                    away_team="Queens Park Rangers",
                    competition="Championship",
                    kickoff=kickoff_str,
                ),
                bookmaker_odds=[
                    StatsHubBookmakerOdds(bookmaker="Bet365", line=0.5, side="over", decimal_odds=1.18),
                    StatsHubBookmakerOdds(bookmaker="SkyBet", line=0.5, side="over", decimal_odds=1.19),
                ],
            )
        )

        mock_p_inst = MagicMock()
        mock_p_inst.run.return_value.parsed_objects = [mock_shots_prop]
        mock_player_provider.return_value = mock_p_inst

        mock_t_inst = MagicMock()
        mock_t_inst.run.return_value.parsed_objects = []
        mock_team_provider.return_value = mock_t_inst

        mock_extract_player.return_value = []
        mock_extract_team.return_value = []

        scanner = GlobalPropsScanner(
            cached_execution_events=[],
            snapshot_recorder=self.recorder,
        )
        scope = GlobalScanScope(
            props_scope="PLAYER",
            stat_types=["shots"],
            min_ev_percent=0.0,
            max_results=10,
        )
        budget = GlobalScanBudget(max_fixtures=5)

        scan_result = scanner.execute_scan(scope=scope, budget=budget)

        # Assert repository has persisted snapshot
        snapshots = self.repository.list_all()
        self.assertEqual(len(snapshots), 1, "Expected 1 Player Shots Over 0.5 snapshot persisted by normal scan")

        snap = snapshots[0]
        self.assertEqual(snap.player_name, "Cian Ashford")
        self.assertEqual(snap.home_team, "Cardiff City")
        self.assertEqual(snap.away_team, "Queens Park Rangers")
        self.assertEqual(snap.stat_type, "SHOTS")
        self.assertEqual(snap.line, 0.5)
        self.assertEqual(snap.direction, "OVER")
        self.assertEqual(snap.outcome_status, "PENDING")
        self.assertIsNone(snap.actual_shots)
        self.assertIsNone(snap.actual_outcome)
        self.assertIsNotNone(snap.kickoff_at)
        self.assertIsNotNone(snap.observed_at)
        self.assertLess(snap.observed_at, snap.kickoff_at)
        self.assertIsNotNone(snap.reference_probability)
        self.assertGreater(snap.reference_probability, 0.0)
        self.assertLess(snap.reference_probability, 1.0)

    @patch("scanner.global_props_scanner.SuperbetProvider")
    @patch("scanner.global_props_scanner.BetclicProvider")
    @patch("scanner.global_props_scanner.StatsHubProvider")
    @patch("scanner.global_props_scanner.StatsHubTeamPropsProvider")
    @patch("scanner.global_props_scanner.ExecutionMarketEngine.extract_team_quotes_from_graphs")
    @patch("scanner.global_props_scanner.ExecutionMarketEngine.extract_quotes_from_graphs")
    def test_global_props_scanner_snapshot_idempotency_and_scope(
        self,
        mock_extract_player,
        mock_extract_team,
        mock_team_provider,
        mock_player_provider,
        mock_bc_p,
        mock_sb_p,
    ):
        """TEST 2: Idempotency preserves existing snapshot on repeated scan, and out-of-scope props are not snapshotted."""
        now = datetime.now(timezone.utc)
        kickoff = now + timedelta(hours=4)
        kickoff_str = kickoff.strftime("%Y-%m-%d %H:%M UTC")

        # 1. Valid Shots O0.5
        prop_shots_05 = StatsHubPropResult(
            player_stat=StatsHubPlayerStat(
                player_id=201,
                player_name="Cian Ashford",
                team="Cardiff City",
                opponent="Queens Park Rangers",
                stat_type="shots",
                line=0.5,
                odds_type="over",
                hit_rate_pct=80.0,
                sample_size=10,
                average=1.8,
                fixture=StatsHubFixture(fixture_id="fix-1", home_team="Cardiff City", away_team="Queens Park Rangers", competition="Championship", kickoff=kickoff_str),
                bookmaker_odds=[StatsHubBookmakerOdds(bookmaker="Bet365", line=0.5, side="over", decimal_odds=1.18)],
            )
        )
        # 2. Out of scope line: Shots Over 1.5
        prop_shots_15 = StatsHubPropResult(
            player_stat=StatsHubPlayerStat(
                player_id=202,
                player_name="Rubin Colwill",
                team="Cardiff City",
                opponent="Queens Park Rangers",
                stat_type="shots",
                line=1.5,
                odds_type="over",
                hit_rate_pct=40.0,
                sample_size=10,
                average=1.8,
                fixture=StatsHubFixture(fixture_id="fix-1", home_team="Cardiff City", away_team="Queens Park Rangers", competition="Championship", kickoff=kickoff_str),
                bookmaker_odds=[StatsHubBookmakerOdds(bookmaker="Bet365", line=1.5, side="over", decimal_odds=2.10)],
            )
        )
        # 3. Out of scope stat: Fouls Over 0.5
        prop_fouls_05 = StatsHubPropResult(
            player_stat=StatsHubPlayerStat(
                player_id=203,
                player_name="Manolis Siopis",
                team="Cardiff City",
                opponent="Queens Park Rangers",
                stat_type="fouls",
                line=0.5,
                odds_type="over",
                hit_rate_pct=90.0,
                sample_size=10,
                average=2.1,
                fixture=StatsHubFixture(fixture_id="fix-1", home_team="Cardiff City", away_team="Queens Park Rangers", competition="Championship", kickoff=kickoff_str),
                bookmaker_odds=[StatsHubBookmakerOdds(bookmaker="Bet365", line=0.5, side="over", decimal_odds=1.15)],
            )
        )

        mock_p_inst = MagicMock()
        mock_p_inst.run.return_value.parsed_objects = [prop_shots_05, prop_shots_15, prop_fouls_05]
        mock_player_provider.return_value = mock_p_inst

        mock_t_inst = MagicMock()
        mock_t_inst.run.return_value.parsed_objects = []
        mock_team_provider.return_value = mock_t_inst

        mock_extract_player.return_value = []
        mock_extract_team.return_value = []

        scanner = GlobalPropsScanner(
            cached_execution_events=[],
            snapshot_recorder=self.recorder,
        )
        scope = GlobalScanScope(
            props_scope="PLAYER",
            stat_types=["shots", "fouls"],
            min_ev_percent=0.0,
            max_results=10,
        )
        budget = GlobalScanBudget(max_fixtures=5)

        # First scan cycle
        scanner.execute_scan(scope=scope, budget=budget)
        snapshots = self.repository.list_all()
        # Exactly 1 snapshot: only Shots O0.5, ignoring Shots O1.5 and Fouls O0.5
        self.assertEqual(len(snapshots), 1)
        self.assertEqual(snapshots[0].player_name, "Cian Ashford")

        # Second scan cycle (simulate recurring scheduler run with same data)
        scanner.execute_scan(scope=scope, budget=budget)
        snapshots_second = self.repository.list_all()
        self.assertEqual(len(snapshots_second), 1, "Idempotency violated: duplicate snapshot created")
