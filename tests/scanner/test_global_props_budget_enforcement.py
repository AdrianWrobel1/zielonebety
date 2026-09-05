"""
Unit & Integration Tests for Bounded Global Scan Budget Enforcement

Validates:
1. Strict distinction between events_discovered (provider-level discovery) and
   events_processed (normalized execution graphs passed to quote extraction & matching).
2. Enforcement of max_fixtures (e.g., 3 selected from 10 discovered).
3. Enforcement of max_execution_events ceiling across cached graphs and live providers.
4. Cached execution events are filtered against selected fixtures_map rather than
   passing entire platform caches into normalization and matcher.
5. Provider fetch/parse/validate execution is restricted to matched items only.
"""

import unittest
from unittest.mock import MagicMock, patch

from domain.models import Competition, Event
from normalization.base_normalizer import NormalizedGraph
from providers.statshub.models import (
    StatsHubFixture,
    StatsHubPlayerStat,
    StatsHubBookmakerOdds,
    StatsHubPropResult,
)
from providers.superbet.models import SuperbetDiscoveredItem, SuperbetEvent
from providers.betclic.models import BetclicDiscoveredItem, BetclicEvent
from scanner.global_props_scanner import (
    GlobalPropsScanner,
    GlobalScanScope,
    GlobalScanBudget,
)


class TestGlobalScanBudgetEnforcement(unittest.TestCase):

    def _create_mock_fixture_trends(self, count: int = 10):
        """Creates synthetic player trends across N distinct fixtures."""
        trends = []
        for i in range(1, count + 1):
            fix = StatsHubFixture(
                fixture_id=f"fix-{i}",
                home_team=f"HomeTeam_{i}",
                away_team=f"AwayTeam_{i}",
                competition="Test League",
                kickoff=f"2026-09-0{min(i, 9)}T20:00:00Z",
            )
            stat = StatsHubPlayerStat(
                player_id=100 + i,
                player_name=f"Player_{i}",
                team=f"HomeTeam_{i}",
                opponent=f"AwayTeam_{i}",
                stat_type="shots",
                line=1.5,
                odds_type="over",
                hit_rate_pct=75.0,
                sample_size=10,
                average=2.0,
                fixture=fix,
                bookmaker_odds=[
                    StatsHubBookmakerOdds(bookmaker="Bet365", line=1.5, side="over", decimal_odds=1.85),
                ],
            )
            trends.append(StatsHubPropResult(player_stat=stat))
        return trends

    @patch("scanner.global_props_scanner.StatsHubTeamPropsProvider")
    @patch("scanner.global_props_scanner.StatsHubProvider")
    @patch("scanner.global_props_scanner.ExecutionMarketEngine.extract_quotes_from_graphs")
    @patch("scanner.global_props_scanner.ExecutionMarketEngine.extract_team_quotes_from_graphs")
    def test_cached_execution_events_filtered_to_selected_fixtures_and_budget(
        self,
        mock_extract_team,
        mock_extract_player,
        mock_player_provider,
        mock_team_provider,
    ):
        """When 20 cached graphs are provided, only graphs matching selected fixtures (max 3)

        and respecting max_execution_events (max 2) are passed to quote extraction and matcher.
        """
        # StatsHub discovers 10 fixtures
        p_inst = MagicMock()
        p_inst.run.return_value.parsed_objects = self._create_mock_fixture_trends(10)
        mock_player_provider.return_value = p_inst

        t_inst = MagicMock()
        t_inst.run.return_value.parsed_objects = []
        mock_team_provider.return_value = t_inst

        mock_extract_player.return_value = []
        mock_extract_team.return_value = []

        # Construct 20 cached graphs (first 5 match HomeTeam_1..5 vs AwayTeam_1..5, rest are unrelated)
        cached_graphs = []
        for i in range(1, 21):
            if i <= 5:
                home, away = f"HomeTeam_{i}", f"AwayTeam_{i}"
            else:
                home, away = f"UnrelatedHome_{i}", f"UnrelatedAway_{i}"
            g = NormalizedGraph(
                competition=Competition(name="Test League", sport="football"),
                event=Event(
                    competition_id="comp-1",
                    home_participant=home,
                    away_participant=away,
                    scheduled_start="2026-09-01T20:00:00Z",
                    provider_ids={"superbet": f"sb-{i}"},
                ),
                markets=[],
                selections=[],
                odds_list=[],
            )
            cached_graphs.append(g)

        scanner = GlobalPropsScanner(cached_execution_events=cached_graphs)
        scope = GlobalScanScope(props_scope="PLAYER", min_ev_percent=3.0)
        # Budget: max 3 fixtures selected, max 2 execution events processed
        budget = GlobalScanBudget(max_fixtures=3, max_execution_events=2)

        result = scanner.execute_scan(scope=scope, budget=budget)

        # 1. Check fixture accounting
        self.assertEqual(result.funnel_metrics.fixtures_discovered, 10)
        self.assertEqual(result.funnel_metrics.fixtures_selected, 3)

        # 2. Check graphs passed to extract_quotes_from_graphs
        mock_extract_player.assert_called_once()
        graphs_passed = mock_extract_player.call_args[0][0]
        # Must be filtered to matched selected fixtures AND bounded by max_execution_events (2)
        self.assertEqual(len(graphs_passed), 2, f"Expected exactly 2 bounded execution graphs, got {len(graphs_passed)}")
        for g in graphs_passed:
            self.assertIn(g.event.home_participant, ["HomeTeam_1", "HomeTeam_2", "HomeTeam_3"])

    @patch("scanner.global_props_scanner.SuperbetProvider")
    @patch("scanner.global_props_scanner.BetclicProvider")
    @patch("scanner.global_props_scanner.StatsHubTeamPropsProvider")
    @patch("scanner.global_props_scanner.StatsHubProvider")
    @patch("scanner.global_props_scanner.ExecutionMarketEngine.extract_quotes_from_graphs")
    @patch("scanner.global_props_scanner.ExecutionMarketEngine.extract_team_quotes_from_graphs")
    def test_live_provider_discovery_vs_processed_events_budget(
        self,
        mock_extract_team,
        mock_extract_player,
        mock_player_provider,
        mock_team_provider,
        mock_bc_provider,
        mock_sb_provider,
    ):
        """Live provider discovery discovers 50 events, but scanner strictly restricts

        provider.run execution and normalization to matched fixtures bounded by max_execution_events.
        """
        # StatsHub discovers 10 fixtures
        p_inst = MagicMock()
        p_inst.run.return_value.parsed_objects = self._create_mock_fixture_trends(10)
        mock_player_provider.return_value = p_inst

        t_inst = MagicMock()
        t_inst.run.return_value.parsed_objects = []
        mock_team_provider.return_value = t_inst

        mock_extract_player.return_value = []
        mock_extract_team.return_value = []

        # Superbet provider mock with 50 discovered events
        sb_inst = MagicMock()
        sb_disc_items = []
        for i in range(1, 51):
            if i <= 5:
                match_name = f"HomeTeam_{i} - AwayTeam_{i}"
            else:
                match_name = f"UnrelatedSB_{i} - UnrelatedAway_{i}"
            sb_disc_items.append(
                SuperbetDiscoveredItem(
                    event_id=f"sb-{i}",
                    match_name=match_name,
                    competition_id="10",
                    competition_name="Test League",
                    start_time="2026-09-01T20:00:00Z",
                )
            )
        sb_inst.discover.return_value = sb_disc_items

        # Mock sb_inst.run() returning SuperbetEvents
        def sb_run_side_effect():
            discovered = sb_inst.get_discovered_items() or sb_disc_items
            res = MagicMock()
            parsed = []
            for it in discovered:
                parts = it.match_name.split(" - ")
                h = parts[0].strip() if len(parts) > 0 else ""
                a = parts[1].strip() if len(parts) > 1 else ""
                ev = SuperbetEvent(
                    event_id=it.event_id,
                    name=it.match_name,
                    home_team=h,
                    away_team=a,
                    start_time=it.start_time,
                    competition_name="Test League",
                    markets=[],
                )
                parsed.append(ev)
            res.parsed_objects = parsed
            return res

        sb_inst.run.side_effect = sb_run_side_effect
        sb_inst.set_discovered_items.side_effect = lambda items: setattr(sb_inst, "_cached", items)
        sb_inst.get_discovered_items.side_effect = lambda: getattr(sb_inst, "_cached", None)
        mock_sb_provider.return_value = sb_inst

        # Betclic provider mock with 50 discovered events
        bc_inst = MagicMock()
        bc_disc_items = []
        for i in range(1, 51):
            if i <= 5:
                match_name = f"HomeTeam_{i} - AwayTeam_{i}"
            else:
                match_name = f"UnrelatedBC_{i} - UnrelatedAway_{i}"
            bc_disc_items.append(
                BetclicDiscoveredItem(
                    provider_event_id=f"bc-{i}",
                    name=match_name,
                    competition_name="Test League",
                    url="https://betclic.pl/event",
                    start_time="2026-09-01T20:00:00Z",
                )
            )
        bc_inst.discover.return_value = bc_disc_items

        def bc_run_side_effect():
            discovered = bc_inst.get_discovered_items() or bc_disc_items
            res = MagicMock()
            parsed = []
            for it in discovered:
                parts = it.name.split(" - ")
                h = parts[0].strip() if len(parts) > 0 else ""
                a = parts[1].strip() if len(parts) > 1 else ""
                ev = BetclicEvent(
                    provider_event_id=it.provider_event_id,
                    name=it.name,
                    competition_name="Test League",
                    home_team=h,
                    away_team=a,
                    start_time=it.start_time,
                    markets=[],
                )
                parsed.append(ev)
            res.parsed_objects = parsed
            return res

        bc_inst.run.side_effect = bc_run_side_effect
        bc_inst.set_discovered_items.side_effect = lambda items: setattr(bc_inst, "_cached", items)
        bc_inst.get_discovered_items.side_effect = lambda: getattr(bc_inst, "_cached", None)
        mock_bc_provider.return_value = bc_inst

        scanner = GlobalPropsScanner(cached_execution_events=[])
        scope = GlobalScanScope(props_scope="PLAYER", min_ev_percent=3.0)
        # Budget: max 3 fixtures, max 2 execution events
        budget = GlobalScanBudget(max_fixtures=3, max_execution_events=2)

        result = scanner.execute_scan(scope=scope, budget=budget)

        self.assertEqual(result.funnel_metrics.fixtures_discovered, 10)
        self.assertEqual(result.funnel_metrics.fixtures_selected, 3)

        # Check that provider.set_discovered_items was called with matched items only (<= 2)
        sb_inst.set_discovered_items.assert_called_once()
        sb_set_items = sb_inst.set_discovered_items.call_args[0][0]
        self.assertLessEqual(len(sb_set_items), 2, f"Superbet was set with {len(sb_set_items)} items, expected <= 2")

        # Check graphs passed to extract_quotes_from_graphs
        mock_extract_player.assert_called_once()
        graphs_passed = mock_extract_player.call_args[0][0]
        self.assertLessEqual(len(graphs_passed), 2, f"Expected total normalized graphs <= 2, got {len(graphs_passed)}")


if __name__ == "__main__":
    unittest.main()
