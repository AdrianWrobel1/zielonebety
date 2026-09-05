"""
Tests for Global Props Match-Level Discovery and Team Props Integration.

Verifies:
1. Team Props discovery initializes StatsHubTeamPropsProvider with mode="team_trends" and games={fixture_ids}.
2. Player Props discovery passes fixture_ids={selected_fids} to StatsHubConfig.
3. Hunter queries filter out unsupported stat types (e.g. corners, cards, shots_on_target) to prevent HTTP 400 errors.
"""

import unittest
from unittest.mock import MagicMock, patch

from domain.models import Competition, Event
from normalization.base_normalizer import NormalizedGraph
from scanner.global_props_scanner import (
    GlobalPropsScanner,
    GlobalScanScope,
    GlobalScanBudget,
    DiscoveredFixtureCandidate,
)
from providers.statshub.models import (
    StatsHubFixture,
    StatsHubPlayerStat,
    StatsHubBookmakerOdds,
    StatsHubPropResult,
)
from providers.statshub.team_models import (
    StatsHubTeamFixture,
    StatsHubTeamStat,
    StatsHubTeamBookmakerOdds,
    StatsHubTeamPropResult,
)


class TestGlobalPropsMatchDiscovery(unittest.TestCase):

    def setUp(self):
        # Create a mock execution candidate fixture with a resolved StatsHub fixture_id
        self.mock_graph = NormalizedGraph(
            event=Event(
                competition_id="comp-1",
                home_participant="Real Madrid",
                away_participant="Barcelona",
                scheduled_start="2026-09-05T20:00:00Z",
            ),
            competition=Competition(name="La Liga", sport="football"),
            markets=[],
        )

    @patch("scanner.global_props_scanner.StatsHubTeamPropsProvider")
    @patch("scanner.global_props_scanner.StatsHubProvider")
    def test_team_props_discovery_with_games(self, mock_p_provider, mock_t_provider):
        """Verify that Team Props discovery queries StatsHub in team_trends mode with games parameter."""
        scanner = GlobalPropsScanner(cached_execution_events=[self.mock_graph])
        # Inject fixture_id on candidate
        scope = GlobalScanScope(props_scope="TEAM", fixture_ids="16416308")
        budget = GlobalScanBudget(max_fixtures=1, max_trends_requests=5)

        mock_t_inst = MagicMock()
        mock_t_inst.run.return_value.parsed_objects = [
            StatsHubTeamPropResult(
                team_stat=StatsHubTeamStat(
                    team_name="Real Madrid",
                    opponent_name="Barcelona",
                    stat_type="corners",
                    line=5.5,
                    odds_type="over",
                    bookmaker_odds=[
                        StatsHubTeamBookmakerOdds(bookmaker="Bet365", line=5.5, side="over", decimal_odds=1.85)
                    ],
                    fixture=StatsHubTeamFixture(
                        fixture_id="16416308",
                        home_team="Real Madrid",
                        away_team="Barcelona",
                    ),
                )
            )
        ]
        mock_t_provider.return_value = mock_t_inst

        result = scanner.execute_scan(scope=scope, budget=budget)

        # Must have called StatsHubTeamPropsProvider
        self.assertTrue(mock_t_provider.called, "StatsHubTeamPropsProvider should have been called")
        call_cfg = mock_t_provider.call_args[1].get("config") or mock_t_provider.call_args[0][0]

        # REQUIREMENT: mode must be team_trends and games must contain the fixture id
        self.assertEqual(call_cfg.mode, "team_trends", "Team props provider must be called with mode='team_trends'")
        self.assertIn("16416308", str(call_cfg.games), "Team props provider must receive games parameter with fixture ID")

    @patch("scanner.global_props_scanner.StatsHubProvider")
    def test_player_props_scoped_to_fixtures(self, mock_p_provider):
        """Verify that Player Props discovery propagates selected fixture IDs to Hunter fixture_ids."""
        scanner = GlobalPropsScanner(cached_execution_events=[self.mock_graph])
        scope = GlobalScanScope(props_scope="PLAYER", fixture_ids="16416308")
        budget = GlobalScanBudget(max_fixtures=1, max_trends_requests=5)

        mock_p_inst = MagicMock()
        mock_p_inst.run.return_value.parsed_objects = []
        mock_p_provider.return_value = mock_p_inst

        result = scanner.execute_scan(scope=scope, budget=budget)

        self.assertTrue(mock_p_provider.called, "StatsHubProvider should have been called")
        call_cfg = mock_p_provider.call_args[1].get("config") or mock_p_provider.call_args[0][0]

        # REQUIREMENT: fixture_ids must be passed to StatsHubConfig
        self.assertIn("16416308", str(call_cfg.fixture_ids), "Hunter config must receive fixture_ids for match-level scoping")

    @patch("scanner.global_props_scanner.StatsHubProvider")
    def test_no_unsupported_hunter_stats_sent(self, mock_p_provider):
        """Verify that Hunter queries filter out unsupported stats (corners, cards, shots_on_target) to avoid HTTP 400."""
        scanner = GlobalPropsScanner(cached_execution_events=[self.mock_graph])
        scope = GlobalScanScope(
            props_scope="PLAYER",
            stat_types=["shots", "shots_on_target", "corners", "cards", "fouls"],
            fixture_ids="16416308",
        )
        budget = GlobalScanBudget(max_fixtures=1, max_trends_requests=10)

        mock_p_inst = MagicMock()
        mock_p_inst.run.return_value.parsed_objects = []
        mock_p_provider.return_value = mock_p_inst

        result = scanner.execute_scan(scope=scope, budget=budget)

        requested_stats = []
        for c in mock_p_provider.call_args_list:
            cfg = c[1].get("config") or c[0][0]
            requested_stats.append(cfg.stat)

        # Hunter must only be queried with supported stats
        unsupported = {"corners", "cards", "shots_on_target"}
        for st in requested_stats:
            self.assertNotIn(st, unsupported, f"Unsupported stat '{st}' must not be sent to Hunter endpoint")

    @patch("scanner.global_props_scanner.StatsHubClient.discover_upcoming_fixtures")
    @patch("scanner.global_props_scanner.StatsHubProvider")
    @patch("scanner.global_props_scanner.StatsHubTeamPropsProvider")
    def test_pre_discovery_fixture_id_resolution(self, mock_t_provider, mock_p_provider, mock_sh_fixtures):
        """Verify candidate fixtures resolve StatsHub fixture IDs prior to trend acquisition."""
        mock_sh_fixtures.return_value = [
            {
                "fixture_id": "199999",
                "home_team": "Real Madrid",
                "away_team": "Barcelona",
                "competition": "La Liga",
                "kickoff": 1787616000,
            }
        ]
        mock_p_inst = MagicMock()
        mock_p_inst.run.return_value.parsed_objects = []
        mock_p_provider.return_value = mock_p_inst

        mock_t_inst = MagicMock()
        mock_t_inst.run.return_value.parsed_objects = []
        mock_t_provider.return_value = mock_t_inst

        # Candidate starts without fixture_id (as from live bookmaker discovery)
        scanner = GlobalPropsScanner(cached_execution_events=[self.mock_graph])
        scope = GlobalScanScope(props_scope="ALL")  # No fixture_ids in scope!
        budget = GlobalScanBudget(max_fixtures=1)

        result = scanner.execute_scan(scope=scope, budget=budget)

        # 1. Selected candidate must have had fixture_id resolved
        selected = result.selected_fixtures
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["fixture_id"], "199999")

        # 2. Player props provider must have received resolved fixture_id
        p_cfg = mock_p_provider.call_args[1].get("config") or mock_p_provider.call_args[0][0]
        self.assertIn("199999", str(p_cfg.fixture_ids))

        # 3. Team props provider must have received resolved fixture_id in games
        t_cfg = mock_t_provider.call_args[1].get("config") or mock_t_provider.call_args[0][0]
        self.assertIn("199999", str(t_cfg.games))


if __name__ == "__main__":
    unittest.main()

