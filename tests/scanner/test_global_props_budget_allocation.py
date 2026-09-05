"""
TDD Test Suite: Shared Execution Budget Allocation (Superbet & Betclic)

Verifies the contract of shared execution budget (max_execution_events) allocation:
- TEST 1: BOTH BOOKMAKERS — when both have candidates, neither starves the other.
- TEST 2: ONLY SUPERBET — when only Superbet has candidates, it can use full budget.
- TEST 3: ONLY BETCLIC — when only Betclic has candidates, it can use full budget.
- TEST 4: CANDIDATES BELOW BUDGET — when combined candidates < budget, no artificial slots.
- TEST 5: HARD GLOBAL CEILING — selected_superbet + selected_betclic <= max_execution_events.
- TEST 6: PROVIDER BOUNDARY — set_discovered_items called with allocated subset before run.
- TEST 7: DETERMINISM — repeated runs with identical inputs produce identical allocations.
"""

import unittest
from unittest.mock import MagicMock, patch

from providers.betclic.models import BetclicDiscoveredItem, BetclicEvent
from providers.statshub.models import (
    StatsHubFixture,
    StatsHubPlayerStat,
    StatsHubBookmakerOdds,
    StatsHubPropResult,
)
from providers.superbet.models import SuperbetDiscoveredItem, SuperbetEvent
from scanner.global_props_scanner import (
    GlobalPropsScanner,
    GlobalScanScope,
    GlobalScanBudget,
)


class TestGlobalPropsBudgetAllocation(unittest.TestCase):

    def _create_mock_trends(self, count: int = 10):
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

    # ----------------------------------------------------------------------
    # TEST 1 — BOTH BOOKMAKERS (Budget = 4, SB = 10, BC = 10)
    # ----------------------------------------------------------------------
    @patch("scanner.global_props_scanner.SuperbetProvider")
    @patch("scanner.global_props_scanner.BetclicProvider")
    @patch("scanner.global_props_scanner.StatsHubTeamPropsProvider")
    @patch("scanner.global_props_scanner.StatsHubProvider")
    @patch("scanner.global_props_scanner.ExecutionMarketEngine.extract_quotes_from_graphs")
    @patch("scanner.global_props_scanner.ExecutionMarketEngine.extract_team_quotes_from_graphs")
    def test_1_both_bookmakers_share_execution_budget(
        self,
        mock_extract_team,
        mock_extract_player,
        mock_statshub_p,
        mock_statshub_t,
        mock_bc_p,
        mock_sb_p,
    ):
        """When budget=4 and both Superbet and Betclic have 10 candidates, both get allocated execution slots."""
        mock_statshub_p.return_value.run.return_value.parsed_objects = self._create_mock_trends(10)
        mock_statshub_t.return_value.run.return_value.parsed_objects = []
        mock_extract_player.return_value = []
        mock_extract_team.return_value = []

        sb_inst = MagicMock()
        sb_inst.discover.return_value = [
            SuperbetDiscoveredItem(
                event_id=f"sb-{i}",
                match_name=f"HomeTeam_{i} - AwayTeam_{i}",
                competition_id="1",
                competition_name="Test League",
                start_time="2026-09-01T20:00:00Z",
            )
            for i in range(1, 11)
        ]
        sb_inst.run.return_value.parsed_objects = []
        mock_sb_p.return_value = sb_inst

        bc_inst = MagicMock()
        bc_inst.discover.return_value = [
            BetclicDiscoveredItem(
                provider_event_id=f"bc-{i}",
                name=f"HomeTeam_{i} - AwayTeam_{i}",
                competition_name="Test League",
                url="https://betclic.pl/e",
                start_time="2026-09-01T20:00:00Z",
            )
            for i in range(1, 11)
        ]
        bc_inst.run.return_value.parsed_objects = []
        mock_bc_p.return_value = bc_inst

        scanner = GlobalPropsScanner(cached_execution_events=[])
        scope = GlobalScanScope(props_scope="PLAYER", min_ev_percent=3.0)
        budget = GlobalScanBudget(max_fixtures=10, max_execution_events=4)

        result = scanner.execute_scan(scope=scope, budget=budget)

        # Superbet set_discovered_items verification
        sb_inst.set_discovered_items.assert_called_once()
        sb_items = sb_inst.set_discovered_items.call_args[0][0]
        sb_count = len(sb_items)

        # Betclic set_discovered_items verification
        bc_inst.set_discovered_items.assert_called_once()
        bc_items = bc_inst.set_discovered_items.call_args[0][0]
        bc_count = len(bc_items)

        # Both must receive slots (neither starves the other)
        self.assertGreater(sb_count, 0, "Superbet should receive execution events")
        self.assertGreater(bc_count, 0, "Betclic should receive execution events when both have candidates")
        self.assertEqual(sb_count + bc_count, 4, f"Combined execution events ({sb_count} + {bc_count}) must equal budget (4)")

    # ----------------------------------------------------------------------
    # TEST 2 — ONLY SUPERBET (Budget = 4, SB = 10, BC = 0)
    # ----------------------------------------------------------------------
    @patch("scanner.global_props_scanner.SuperbetProvider")
    @patch("scanner.global_props_scanner.BetclicProvider")
    @patch("scanner.global_props_scanner.StatsHubTeamPropsProvider")
    @patch("scanner.global_props_scanner.StatsHubProvider")
    @patch("scanner.global_props_scanner.ExecutionMarketEngine.extract_quotes_from_graphs")
    @patch("scanner.global_props_scanner.ExecutionMarketEngine.extract_team_quotes_from_graphs")
    def test_2_only_superbet_consumes_full_budget(
        self,
        mock_extract_team,
        mock_extract_player,
        mock_statshub_p,
        mock_statshub_t,
        mock_bc_p,
        mock_sb_p,
    ):
        """When only Superbet has candidates, it can consume the full budget up to max_execution_events."""
        mock_statshub_p.return_value.run.return_value.parsed_objects = self._create_mock_trends(10)
        mock_statshub_t.return_value.run.return_value.parsed_objects = []
        mock_extract_player.return_value = []
        mock_extract_team.return_value = []

        sb_inst = MagicMock()
        sb_inst.discover.return_value = [
            SuperbetDiscoveredItem(
                event_id=f"sb-{i}",
                match_name=f"HomeTeam_{i} - AwayTeam_{i}",
                competition_id="1",
                competition_name="Test League",
                start_time="2026-09-01T20:00:00Z",
            )
            for i in range(1, 11)
        ]
        sb_inst.run.return_value.parsed_objects = []
        mock_sb_p.return_value = sb_inst

        bc_inst = MagicMock()
        bc_inst.discover.return_value = []  # 0 Betclic events
        bc_inst.run.return_value.parsed_objects = []
        mock_bc_p.return_value = bc_inst

        scanner = GlobalPropsScanner(cached_execution_events=[])
        scope = GlobalScanScope(props_scope="PLAYER", min_ev_percent=3.0)
        budget = GlobalScanBudget(max_fixtures=10, max_execution_events=4)

        result = scanner.execute_scan(scope=scope, budget=budget)

        sb_inst.set_discovered_items.assert_called_once()
        sb_items = sb_inst.set_discovered_items.call_args[0][0]
        self.assertEqual(len(sb_items), 4, "Superbet should consume full budget of 4 when Betclic has 0")

    # ----------------------------------------------------------------------
    # TEST 3 — ONLY BETCLIC (Budget = 4, SB = 0, BC = 10)
    # ----------------------------------------------------------------------
    @patch("scanner.global_props_scanner.SuperbetProvider")
    @patch("scanner.global_props_scanner.BetclicProvider")
    @patch("scanner.global_props_scanner.StatsHubTeamPropsProvider")
    @patch("scanner.global_props_scanner.StatsHubProvider")
    @patch("scanner.global_props_scanner.ExecutionMarketEngine.extract_quotes_from_graphs")
    @patch("scanner.global_props_scanner.ExecutionMarketEngine.extract_team_quotes_from_graphs")
    def test_3_only_betclic_consumes_full_budget(
        self,
        mock_extract_team,
        mock_extract_player,
        mock_statshub_p,
        mock_statshub_t,
        mock_bc_p,
        mock_sb_p,
    ):
        """When only Betclic has candidates, it can consume the full budget up to max_execution_events."""
        mock_statshub_p.return_value.run.return_value.parsed_objects = self._create_mock_trends(10)
        mock_statshub_t.return_value.run.return_value.parsed_objects = []
        mock_extract_player.return_value = []
        mock_extract_team.return_value = []

        sb_inst = MagicMock()
        sb_inst.discover.return_value = []  # 0 Superbet events
        sb_inst.run.return_value.parsed_objects = []
        mock_sb_p.return_value = sb_inst

        bc_inst = MagicMock()
        bc_inst.discover.return_value = [
            BetclicDiscoveredItem(
                provider_event_id=f"bc-{i}",
                name=f"HomeTeam_{i} - AwayTeam_{i}",
                competition_name="Test League",
                url="https://betclic.pl/e",
                start_time="2026-09-01T20:00:00Z",
            )
            for i in range(1, 11)
        ]
        bc_inst.run.return_value.parsed_objects = []
        mock_bc_p.return_value = bc_inst

        scanner = GlobalPropsScanner(cached_execution_events=[])
        scope = GlobalScanScope(props_scope="PLAYER", min_ev_percent=3.0)
        budget = GlobalScanBudget(max_fixtures=10, max_execution_events=4)

        result = scanner.execute_scan(scope=scope, budget=budget)

        bc_inst.set_discovered_items.assert_called_once()
        bc_items = bc_inst.set_discovered_items.call_args[0][0]
        self.assertEqual(len(bc_items), 4, "Betclic should consume full budget of 4 when Superbet has 0")

    # ----------------------------------------------------------------------
    # TEST 4 — CANDIDATES BELOW BUDGET (Budget = 10, SB = 2, BC = 3)
    # ----------------------------------------------------------------------
    @patch("scanner.global_props_scanner.SuperbetProvider")
    @patch("scanner.global_props_scanner.BetclicProvider")
    @patch("scanner.global_props_scanner.StatsHubTeamPropsProvider")
    @patch("scanner.global_props_scanner.StatsHubProvider")
    @patch("scanner.global_props_scanner.ExecutionMarketEngine.extract_quotes_from_graphs")
    @patch("scanner.global_props_scanner.ExecutionMarketEngine.extract_team_quotes_from_graphs")
    def test_4_candidates_below_budget_no_artificial_slots(
        self,
        mock_extract_team,
        mock_extract_player,
        mock_statshub_p,
        mock_statshub_t,
        mock_bc_p,
        mock_sb_p,
    ):
        """When SB=2 and BC=3 with budget=10, exactly 2 and 3 items are allocated (total 5)."""
        mock_statshub_p.return_value.run.return_value.parsed_objects = self._create_mock_trends(5)
        mock_statshub_t.return_value.run.return_value.parsed_objects = []
        mock_extract_player.return_value = []
        mock_extract_team.return_value = []

        sb_inst = MagicMock()
        sb_inst.discover.return_value = [
            SuperbetDiscoveredItem(
                event_id=f"sb-{i}",
                match_name=f"HomeTeam_{i} - AwayTeam_{i}",
                competition_id="1",
                competition_name="Test League",
                start_time="2026-09-01T20:00:00Z",
            )
            for i in range(1, 3)  # 2 items
        ]
        sb_inst.run.return_value.parsed_objects = []
        mock_sb_p.return_value = sb_inst

        bc_inst = MagicMock()
        bc_inst.discover.return_value = [
            BetclicDiscoveredItem(
                provider_event_id=f"bc-{i}",
                name=f"HomeTeam_{i} - AwayTeam_{i}",
                competition_name="Test League",
                url="https://betclic.pl/e",
                start_time="2026-09-01T20:00:00Z",
            )
            for i in range(1, 4)  # 3 items
        ]
        bc_inst.run.return_value.parsed_objects = []
        mock_bc_p.return_value = bc_inst

        scanner = GlobalPropsScanner(cached_execution_events=[])
        scope = GlobalScanScope(props_scope="PLAYER", min_ev_percent=3.0)
        budget = GlobalScanBudget(max_fixtures=10, max_execution_events=10)

        result = scanner.execute_scan(scope=scope, budget=budget)

        sb_items = sb_inst.set_discovered_items.call_args[0][0]
        bc_items = bc_inst.set_discovered_items.call_args[0][0]
        self.assertEqual(len(sb_items), 2)
        self.assertEqual(len(bc_items), 3)
        self.assertEqual(len(sb_items) + len(bc_items), 5)

    # ----------------------------------------------------------------------
    # TEST 5 — HARD GLOBAL CEILING (selected_sb + selected_bc <= budget)
    # ----------------------------------------------------------------------
    @patch("scanner.global_props_scanner.SuperbetProvider")
    @patch("scanner.global_props_scanner.BetclicProvider")
    @patch("scanner.global_props_scanner.StatsHubTeamPropsProvider")
    @patch("scanner.global_props_scanner.StatsHubProvider")
    @patch("scanner.global_props_scanner.ExecutionMarketEngine.extract_quotes_from_graphs")
    @patch("scanner.global_props_scanner.ExecutionMarketEngine.extract_team_quotes_from_graphs")
    def test_5_hard_global_ceiling_across_combinations(
        self,
        mock_extract_team,
        mock_extract_player,
        mock_statshub_p,
        mock_statshub_t,
        mock_bc_p,
        mock_sb_p,
    ):
        """Combined allocated events never exceed max_execution_events across varying candidate ratios."""
        mock_statshub_p.return_value.run.return_value.parsed_objects = self._create_mock_trends(10)
        mock_statshub_t.return_value.run.return_value.parsed_objects = []
        mock_extract_player.return_value = []
        mock_extract_team.return_value = []

        test_cases = [
            (5, 10, 10),  # budget=5, sb=10, bc=10
            (3, 10, 10),  # budget=3, sb=10, bc=10
            (7, 2, 8),    # budget=7, sb=2, bc=8
            (6, 8, 1),    # budget=6, sb=8, bc=1
        ]

        for budget_limit, sb_count, bc_count in test_cases:
            sb_inst = MagicMock()
            sb_inst.discover.return_value = [
                SuperbetDiscoveredItem(
                    event_id=f"sb-{i}",
                    match_name=f"HomeTeam_{i} - AwayTeam_{i}",
                    competition_id="1",
                    competition_name="Test League",
                    start_time="2026-09-01T20:00:00Z",
                )
                for i in range(1, sb_count + 1)
            ]
            sb_inst.run.return_value.parsed_objects = []
            mock_sb_p.return_value = sb_inst

            bc_inst = MagicMock()
            bc_inst.discover.return_value = [
                BetclicDiscoveredItem(
                    provider_event_id=f"bc-{i}",
                    name=f"HomeTeam_{i} - AwayTeam_{i}",
                    competition_name="Test League",
                    url="https://betclic.pl/e",
                    start_time="2026-09-01T20:00:00Z",
                )
                for i in range(1, bc_count + 1)
            ]
            bc_inst.run.return_value.parsed_objects = []
            mock_bc_p.return_value = bc_inst

            scanner = GlobalPropsScanner(cached_execution_events=[])
            scope = GlobalScanScope(props_scope="PLAYER", min_ev_percent=3.0)
            budget = GlobalScanBudget(max_fixtures=10, max_execution_events=budget_limit)

            result = scanner.execute_scan(scope=scope, budget=budget)

            sb_allocated = len(sb_inst.set_discovered_items.call_args[0][0]) if sb_inst.set_discovered_items.called else 0
            bc_allocated = len(bc_inst.set_discovered_items.call_args[0][0]) if bc_inst.set_discovered_items.called else 0

            self.assertLessEqual(
                sb_allocated + bc_allocated,
                budget_limit,
                f"Combined allocated ({sb_allocated} + {bc_allocated}) exceeded budget {budget_limit}",
            )

    # ----------------------------------------------------------------------
    # TEST 6 — PROVIDER BOUNDARY (Early bounding before run)
    # ----------------------------------------------------------------------
    @patch("scanner.global_props_scanner.SuperbetProvider")
    @patch("scanner.global_props_scanner.BetclicProvider")
    @patch("scanner.global_props_scanner.StatsHubTeamPropsProvider")
    @patch("scanner.global_props_scanner.StatsHubProvider")
    @patch("scanner.global_props_scanner.ExecutionMarketEngine.extract_quotes_from_graphs")
    @patch("scanner.global_props_scanner.ExecutionMarketEngine.extract_team_quotes_from_graphs")
    def test_6_provider_boundary_set_discovered_items_before_run(
        self,
        mock_extract_team,
        mock_extract_player,
        mock_statshub_p,
        mock_statshub_t,
        mock_bc_p,
        mock_sb_p,
    ):
        """set_discovered_items is called with the allocated items before provider.run() executes."""
        mock_statshub_p.return_value.run.return_value.parsed_objects = self._create_mock_trends(5)
        mock_statshub_t.return_value.run.return_value.parsed_objects = []
        mock_extract_player.return_value = []
        mock_extract_team.return_value = []

        call_order = []

        sb_inst = MagicMock()
        sb_inst.discover.return_value = [
            SuperbetDiscoveredItem(
                event_id="sb-1",
                match_name="HomeTeam_1 - AwayTeam_1",
                competition_id="1",
                competition_name="Test League",
                start_time="2026-09-01T20:00:00Z",
            )
        ]
        sb_inst.set_discovered_items.side_effect = lambda items: call_order.append("sb_set_discovered")
        sb_inst.run.side_effect = lambda: (call_order.append("sb_run"), MagicMock(parsed_objects=[]))[1]
        mock_sb_p.return_value = sb_inst

        bc_inst = MagicMock()
        bc_inst.discover.return_value = [
            BetclicDiscoveredItem(
                provider_event_id="bc-1",
                name="HomeTeam_1 - AwayTeam_1",
                competition_name="Test League",
                url="https://betclic.pl/e",
                start_time="2026-09-01T20:00:00Z",
            )
        ]
        bc_inst.set_discovered_items.side_effect = lambda items: call_order.append("bc_set_discovered")
        bc_inst.run.side_effect = lambda: (call_order.append("bc_run"), MagicMock(parsed_objects=[]))[1]
        mock_bc_p.return_value = bc_inst

        scanner = GlobalPropsScanner(cached_execution_events=[])
        scope = GlobalScanScope(props_scope="PLAYER", min_ev_percent=3.0)
        budget = GlobalScanBudget(max_fixtures=5, max_execution_events=2)

        scanner.execute_scan(scope=scope, budget=budget)

        self.assertIn("sb_set_discovered", call_order)
        self.assertIn("sb_run", call_order)
        self.assertLess(call_order.index("sb_set_discovered"), call_order.index("sb_run"))

        if "bc_run" in call_order:
            self.assertIn("bc_set_discovered", call_order)
            self.assertLess(call_order.index("bc_set_discovered"), call_order.index("bc_run"))

    # ----------------------------------------------------------------------
    # TEST 7 — DETERMINISM (Repeated runs produce identical allocations)
    # ----------------------------------------------------------------------
    @patch("scanner.global_props_scanner.SuperbetProvider")
    @patch("scanner.global_props_scanner.BetclicProvider")
    @patch("scanner.global_props_scanner.StatsHubTeamPropsProvider")
    @patch("scanner.global_props_scanner.StatsHubProvider")
    @patch("scanner.global_props_scanner.ExecutionMarketEngine.extract_quotes_from_graphs")
    @patch("scanner.global_props_scanner.ExecutionMarketEngine.extract_team_quotes_from_graphs")
    def test_7_determinism_repeated_runs(
        self,
        mock_extract_team,
        mock_extract_player,
        mock_statshub_p,
        mock_statshub_t,
        mock_bc_p,
        mock_sb_p,
    ):
        """Identical inputs produce exact identical allocation counts and items across multiple runs."""
        mock_statshub_p.return_value.run.return_value.parsed_objects = self._create_mock_trends(10)
        mock_statshub_t.return_value.run.return_value.parsed_objects = []
        mock_extract_player.return_value = []
        mock_extract_team.return_value = []

        sb_items = [
            SuperbetDiscoveredItem(
                event_id=f"sb-{i}",
                match_name=f"HomeTeam_{i} - AwayTeam_{i}",
                competition_id="1",
                competition_name="Test League",
                start_time="2026-09-01T20:00:00Z",
            )
            for i in range(1, 11)
        ]
        bc_items = [
            BetclicDiscoveredItem(
                provider_event_id=f"bc-{i}",
                name=f"HomeTeam_{i} - AwayTeam_{i}",
                competition_name="Test League",
                url="https://betclic.pl/e",
                start_time="2026-09-01T20:00:00Z",
            )
            for i in range(1, 11)
        ]

        allocations = []
        for _ in range(3):
            sb_inst = MagicMock()
            sb_inst.discover.return_value = sb_items
            sb_inst.run.return_value.parsed_objects = []
            mock_sb_p.return_value = sb_inst

            bc_inst = MagicMock()
            bc_inst.discover.return_value = bc_items
            bc_inst.run.return_value.parsed_objects = []
            mock_bc_p.return_value = bc_inst

            scanner = GlobalPropsScanner(cached_execution_events=[])
            scope = GlobalScanScope(props_scope="PLAYER", min_ev_percent=3.0)
            budget = GlobalScanBudget(max_fixtures=10, max_execution_events=4)

            scanner.execute_scan(scope=scope, budget=budget)

            sb_allocated_ids = [it.event_id for it in sb_inst.set_discovered_items.call_args[0][0]] if sb_inst.set_discovered_items.called else []
            bc_allocated_ids = [it.provider_event_id for it in bc_inst.set_discovered_items.call_args[0][0]] if bc_inst.set_discovered_items.called else []
            allocations.append((sb_allocated_ids, bc_allocated_ids))

        self.assertEqual(allocations[0], allocations[1])
        self.assertEqual(allocations[1], allocations[2])


if __name__ == "__main__":
    unittest.main()
