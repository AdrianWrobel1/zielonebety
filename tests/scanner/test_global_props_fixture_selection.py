"""
Unit & Integration Tests for Market Coverage-based Fixture Prioritization in GlobalPropsScanner.
"""

import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

from domain.models import Competition, Event
from normalization.base_normalizer import NormalizedGraph
from providers.superbet.models import SuperbetDiscoveredItem
from providers.betclic.models import BetclicDiscoveredItem
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
from scanner.execution_providers import NormalizedExecutionQuote
from scanner.global_props_scanner import (
    GlobalPropsScanner,
    GlobalScanScope,
    GlobalScanBudget,
)


class TestGlobalPropsFixtureSelection(unittest.TestCase):

    def setUp(self):
        self.now = datetime.now(timezone.utc)
        self.now_str = self.now.isoformat()
        self.tomorrow_str = (self.now + timedelta(days=1)).isoformat()

        # Fixture A: High market coverage (450 total: SB 250, BC 200)
        self.sb_disc_a = SuperbetDiscoveredItem(
            event_id="sb_a",
            match_name="Arsenal·Chelsea",
            competition_name="Premier League",
            start_time=self.now_str,
            metadata={"raw": {"totalMarkets": 250, "marketsCount": 250, "markets": []}},
        )
        self.bc_disc_a = BetclicDiscoveredItem(
            provider_event_id="bc_a",
            name="Arsenal - Chelsea",
            competition_name="Anglia - Premier League",
            url="https://betclic.pl/match-1",
            start_time=self.now_str,
            metadata={"raw": {"total_markets_count": 200, "openMarketCount": 200, "markets": []}},
        )

        # Fixture B: Medium market coverage (120 total: SB 70, BC 50)
        self.sb_disc_b = SuperbetDiscoveredItem(
            event_id="sb_b",
            match_name="Valencia·Villarreal",
            competition_name="LaLiga",
            start_time=self.now_str,
            metadata={"raw": {"totalMarkets": 70, "marketsCount": 70, "markets": []}},
        )
        self.bc_disc_b = BetclicDiscoveredItem(
            provider_event_id="bc_b",
            name="Valencia - Villarreal",
            competition_name="Hiszpania - LaLiga",
            url="https://betclic.pl/match-2",
            start_time=self.now_str,
            metadata={"raw": {"total_markets_count": 50, "openMarketCount": 50, "markets": []}},
        )

        # Fixture C: Low market coverage (25 total: SB 15, BC 10)
        self.sb_disc_c = SuperbetDiscoveredItem(
            event_id="sb_c",
            match_name="Tiny Team 1·Tiny Team 2",
            competition_name="Minor League",
            start_time=self.now_str,
            metadata={"raw": {"totalMarkets": 15, "marketsCount": 15, "markets": []}},
        )
        self.bc_disc_c = BetclicDiscoveredItem(
            provider_event_id="bc_c",
            name="Tiny Team 1 - Tiny Team 2",
            competition_name="Minor League",
            url="https://betclic.pl/match-3",
            start_time=self.now_str,
            metadata={"raw": {"total_markets_count": 10, "openMarketCount": 10, "markets": []}},
        )

        # StatsHub player trends for all three fixtures
        self.player_trends = [
            # Prop for Fixture A
            StatsHubPropResult(
                player_stat=StatsHubPlayerStat(
                    player_id=1,
                    player_name="Bukayo Saka",
                    team="Arsenal",
                    opponent="Chelsea",
                    stat_type="shots",
                    line=1.5,
                    odds_type="over",
                    hit_rate_pct=80.0,
                    sample_size=10,
                    average=2.5,
                    fixture=StatsHubFixture(fixture_id="fix_a", home_team="Arsenal", away_team="Chelsea", competition="Premier League", kickoff=self.now_str),
                    bookmaker_odds=[StatsHubBookmakerOdds(bookmaker="Bet365", line=1.5, side="over", decimal_odds=1.85)],
                )
            ),
            # Prop for Fixture B
            StatsHubPropResult(
                player_stat=StatsHubPlayerStat(
                    player_id=2,
                    player_name="Hugo Duro",
                    team="Valencia",
                    opponent="Villarreal",
                    stat_type="shots",
                    line=1.5,
                    odds_type="over",
                    hit_rate_pct=70.0,
                    sample_size=10,
                    average=2.1,
                    fixture=StatsHubFixture(fixture_id="fix_b", home_team="Valencia", away_team="Villarreal", competition="LaLiga", kickoff=self.now_str),
                    bookmaker_odds=[StatsHubBookmakerOdds(bookmaker="Bet365", line=1.5, side="over", decimal_odds=1.90)],
                )
            ),
            # Prop for Fixture C (Low coverage fixture)
            StatsHubPropResult(
                player_stat=StatsHubPlayerStat(
                    player_id=3,
                    player_name="Tiny Player",
                    team="Tiny Team 1",
                    opponent="Tiny Team 2",
                    stat_type="shots",
                    line=0.5,
                    odds_type="over",
                    hit_rate_pct=60.0,
                    sample_size=10,
                    average=1.0,
                    fixture=StatsHubFixture(fixture_id="fix_c", home_team="Tiny Team 1", away_team="Tiny Team 2", competition="Minor League", kickoff=self.now_str),
                    bookmaker_odds=[StatsHubBookmakerOdds(bookmaker="Bet365", line=0.5, side="over", decimal_odds=1.70)],
                )
            ),
        ]

    @patch("scanner.global_props_scanner.SuperbetProvider")
    @patch("scanner.global_props_scanner.BetclicProvider")
    @patch("scanner.global_props_scanner.StatsHubProvider")
    @patch("scanner.global_props_scanner.StatsHubTeamPropsProvider")
    @patch("scanner.global_props_scanner.ExecutionMarketEngine.extract_quotes_from_graphs")
    @patch("scanner.global_props_scanner.ExecutionMarketEngine.extract_team_quotes_from_graphs")
    def test_market_coverage_priority_over_poor_fixtures(
        self,
        mock_extract_team,
        mock_extract_player,
        mock_team_provider,
        mock_player_provider,
        mock_bc_cls,
        mock_sb_cls,
    ):
        """Fixture with higher real market coverage is preferred before fixture with lower coverage."""
        # Bookmaker discovery returns C first, then B, then A (order shouldn't matter)
        sb_inst = MagicMock()
        sb_inst.discover.return_value = [self.sb_disc_c, self.sb_disc_b, self.sb_disc_a]
        sb_inst.run.return_value.parsed_objects = []
        mock_sb_cls.return_value = sb_inst

        bc_inst = MagicMock()
        bc_inst.discover.return_value = [self.bc_disc_c, self.bc_disc_b, self.bc_disc_a]
        bc_inst.run.return_value.parsed_objects = []
        mock_bc_cls.return_value = bc_inst

        p_inst = MagicMock()
        p_inst.run.return_value.parsed_objects = self.player_trends
        mock_player_provider.return_value = p_inst

        t_inst = MagicMock()
        t_inst.run.return_value.parsed_objects = []
        mock_team_provider.return_value = t_inst

        mock_extract_player.return_value = []
        mock_extract_team.return_value = []

        scanner = GlobalPropsScanner()
        scope = GlobalScanScope(props_scope="PLAYER", stat_types=["shots"])
        budget = GlobalScanBudget(max_fixtures=2)

        result = scanner.execute_scan(scope=scope, budget=budget)

        self.assertIn(result.status, ("SUCCESS", "PARTIAL"))
        funnel = result.funnel_metrics

        # Must discover all 3 fixtures from bookmakers, but select top 2 by market coverage (A=450, B=120)
        self.assertEqual(funnel.fixtures_discovered, 3)
        self.assertEqual(funnel.fixtures_selected, 2)

        # Provider must be asked to fetch details ONLY for A and B
        sb_inst.set_discovered_items.assert_called_once()
        sb_selected = sb_inst.set_discovered_items.call_args[0][0]
        sb_selected_ids = [item.event_id for item in sb_selected]
        self.assertEqual(set(sb_selected_ids), {"sb_a", "sb_b"}, f"Expected top fixtures sb_a and sb_b, got {sb_selected_ids}")
        self.assertNotIn("sb_c", sb_selected_ids)

        bc_inst.set_discovered_items.assert_called_once()
        bc_selected = bc_inst.set_discovered_items.call_args[0][0]
        bc_selected_ids = [item.provider_event_id for item in bc_selected]
        self.assertEqual(set(bc_selected_ids), {"bc_a", "bc_b"}, f"Expected top fixtures bc_a and bc_b, got {bc_selected_ids}")
        self.assertNotIn("bc_c", bc_selected_ids)

    @patch("scanner.global_props_scanner.SuperbetProvider")
    @patch("scanner.global_props_scanner.BetclicProvider")
    @patch("scanner.global_props_scanner.StatsHubProvider")
    @patch("scanner.global_props_scanner.StatsHubTeamPropsProvider")
    @patch("scanner.global_props_scanner.ExecutionMarketEngine.extract_quotes_from_graphs")
    @patch("scanner.global_props_scanner.ExecutionMarketEngine.extract_team_quotes_from_graphs")
    def test_selection_determinism(
        self,
        mock_extract_team,
        mock_extract_player,
        mock_team_provider,
        mock_player_provider,
        mock_bc_cls,
        mock_sb_cls,
    ):
        """Selection is deterministic regardless of item insertion or discovery order."""
        sb_inst = MagicMock()
        bc_inst = MagicMock()
        mock_sb_cls.return_value = sb_inst
        mock_bc_cls.return_value = bc_inst

        p_inst = MagicMock()
        p_inst.run.return_value.parsed_objects = self.player_trends
        mock_player_provider.return_value = p_inst

        t_inst = MagicMock()
        t_inst.run.return_value.parsed_objects = []
        mock_team_provider.return_value = t_inst
        mock_extract_player.return_value = []
        mock_extract_team.return_value = []

        scanner = GlobalPropsScanner()
        scope = GlobalScanScope(props_scope="PLAYER", stat_types=["shots"])
        budget = GlobalScanBudget(max_fixtures=2)

        # Run 1: Order [A, B, C]
        sb_inst.discover.return_value = [self.sb_disc_a, self.sb_disc_b, self.sb_disc_c]
        bc_inst.discover.return_value = [self.bc_disc_a, self.bc_disc_b, self.bc_disc_c]
        res1 = scanner.execute_scan(scope=scope, budget=budget)
        sb_call1 = [x.event_id for x in sb_inst.set_discovered_items.call_args[0][0]]

        # Run 2: Order [C, B, A]
        sb_inst.set_discovered_items.reset_mock()
        bc_inst.set_discovered_items.reset_mock()
        sb_inst.discover.return_value = [self.sb_disc_c, self.sb_disc_b, self.sb_disc_a]
        bc_inst.discover.return_value = [self.bc_disc_c, self.bc_disc_b, self.bc_disc_a]
        res2 = scanner.execute_scan(scope=scope, budget=budget)
        sb_call2 = [x.event_id for x in sb_inst.set_discovered_items.call_args[0][0]]

        self.assertEqual(sb_call1, sb_call2)
        self.assertEqual(sb_call1, ["sb_a", "sb_b"])

    @patch("scanner.global_props_scanner.SuperbetProvider")
    @patch("scanner.global_props_scanner.BetclicProvider")
    @patch("scanner.global_props_scanner.StatsHubProvider")
    @patch("scanner.global_props_scanner.StatsHubTeamPropsProvider")
    @patch("scanner.global_props_scanner.ExecutionMarketEngine.extract_quotes_from_graphs")
    @patch("scanner.global_props_scanner.ExecutionMarketEngine.extract_team_quotes_from_graphs")
    def test_tie_breaker_market_coverage_identical(
        self,
        mock_extract_team,
        mock_extract_player,
        mock_team_provider,
        mock_player_provider,
        mock_bc_cls,
        mock_sb_cls,
    ):
        """When market coverage is identical, selection uses deterministic tie-breakers (kickoff, name)."""
        # Match X: 100 markets, earlier kickoff (today)
        sb_x = SuperbetDiscoveredItem(
            event_id="sb_x",
            match_name="Betis·Sevilla",
            competition_name="LaLiga",
            start_time=(self.now + timedelta(hours=2)).isoformat(),
            metadata={"raw": {"totalMarkets": 100}},
        )
        # Match Y: 100 markets, later kickoff (tomorrow)
        sb_y = SuperbetDiscoveredItem(
            event_id="sb_y",
            match_name="Atletico Madrid·Getafe",
            competition_name="LaLiga",
            start_time=self.tomorrow_str,
            metadata={"raw": {"totalMarkets": 100}},
        )

        sb_inst = MagicMock()
        sb_inst.discover.return_value = [sb_y, sb_x]  # Y first in discovery
        sb_inst.run.return_value.parsed_objects = []
        mock_sb_cls.return_value = sb_inst

        bc_inst = MagicMock()
        bc_inst.discover.return_value = []
        bc_inst.run.return_value.parsed_objects = []
        mock_bc_cls.return_value = bc_inst

        p_inst = MagicMock()
        p_inst.run.return_value.parsed_objects = []
        mock_player_provider.return_value = p_inst

        t_inst = MagicMock()
        t_inst.run.return_value.parsed_objects = []
        mock_team_provider.return_value = t_inst

        mock_extract_player.return_value = []
        mock_extract_team.return_value = []

        scanner = GlobalPropsScanner()
        scope = GlobalScanScope(props_scope="PLAYER")
        budget = GlobalScanBudget(max_fixtures=1)

        result = scanner.execute_scan(scope=scope, budget=budget)

        self.assertIn(result.status, ("SUCCESS", "PARTIAL"))
        self.assertEqual(result.funnel_metrics.fixtures_selected, 1)

        sb_selected = sb_inst.set_discovered_items.call_args[0][0]
        self.assertEqual(len(sb_selected), 1)
        self.assertEqual(sb_selected[0].event_id, "sb_x", "Expected earlier match 'Betis vs Sevilla' to win tie-breaker over tomorrow's match")

    @patch("scanner.global_props_scanner.SuperbetProvider")
    @patch("scanner.global_props_scanner.BetclicProvider")
    @patch("scanner.global_props_scanner.StatsHubProvider")
    @patch("scanner.global_props_scanner.StatsHubTeamPropsProvider")
    @patch("scanner.global_props_scanner.ExecutionMarketEngine.extract_quotes_from_graphs")
    @patch("scanner.global_props_scanner.ExecutionMarketEngine.extract_team_quotes_from_graphs")
    def test_max_fixtures_hard_limit(
        self,
        mock_extract_team,
        mock_extract_player,
        mock_team_provider,
        mock_player_provider,
        mock_bc_cls,
        mock_sb_cls,
    ):
        """max_fixtures is a strict ceiling even when 20 candidate fixtures are discovered."""
        # 10 fixtures with market counts from 10 to 100
        team_pairs = [
            ("Arsenal", "Chelsea"),
            ("Liverpool", "Man City"),
            ("Real Madrid", "Barcelona"),
            ("Bayern", "Dortmund"),
            ("PSG", "Marseille"),
            ("Inter", "Milan"),
            ("Juventus", "Roma"),
            ("Atletico", "Sevilla"),
            ("Porto", "Benfica"),
            ("Ajax", "Feyenoord"),
        ]
        sb_items = [
            SuperbetDiscoveredItem(
                event_id=f"sb_{i+1}",
                match_name=f"{h}·{a}",
                competition_name="League",
                start_time=self.now_str,
                metadata={"raw": {"totalMarkets": 10 * (i + 1)}},
            )
            for i, (h, a) in enumerate(team_pairs)
        ]
        sb_inst = MagicMock()
        sb_inst.discover.return_value = sb_items
        sb_inst.run.return_value.parsed_objects = []
        mock_sb_cls.return_value = sb_inst

        bc_inst = MagicMock()
        bc_inst.discover.return_value = []
        bc_inst.run.return_value.parsed_objects = []
        mock_bc_cls.return_value = bc_inst

        p_inst = MagicMock()
        p_inst.run.return_value.parsed_objects = []
        mock_player_provider.return_value = p_inst

        t_inst = MagicMock()
        t_inst.run.return_value.parsed_objects = []
        mock_team_provider.return_value = t_inst

        mock_extract_player.return_value = []
        mock_extract_team.return_value = []

        scanner = GlobalPropsScanner()
        scope = GlobalScanScope(props_scope="PLAYER")
        budget = GlobalScanBudget(max_fixtures=3)

        result = scanner.execute_scan(scope=scope, budget=budget)

        self.assertEqual(result.funnel_metrics.fixtures_discovered, 10)
        self.assertEqual(result.funnel_metrics.fixtures_selected, 3)

        sb_selected = sb_inst.set_discovered_items.call_args[0][0]
        self.assertEqual(len(sb_selected), 3)
        # Must be the top 3 with highest market counts (sb_10=100, sb_9=90, sb_8=80)
        selected_ids = [x.event_id for x in sb_selected]
        self.assertEqual(selected_ids, ["sb_10", "sb_9", "sb_8"])

    @patch("scanner.global_props_scanner.SuperbetProvider")
    @patch("scanner.global_props_scanner.BetclicProvider")
    @patch("scanner.global_props_scanner.StatsHubProvider")
    @patch("scanner.global_props_scanner.StatsHubTeamPropsProvider")
    @patch("scanner.global_props_scanner.ExecutionMarketEngine.extract_quotes_from_graphs")
    @patch("scanner.global_props_scanner.ExecutionMarketEngine.extract_team_quotes_from_graphs")
    def test_safe_fallback_on_malformed_market_coverage(
        self,
        mock_extract_team,
        mock_extract_player,
        mock_team_provider,
        mock_player_provider,
        mock_bc_cls,
        mock_sb_cls,
    ):
        """Fixture with missing / None / string market counts defaults to 0 and does not crash."""
        sb_corrupt_1 = SuperbetDiscoveredItem(
            event_id="sb_corrupt_1",
            match_name="Team A·Team B",
            start_time=self.now_str,
            metadata={"raw": {"totalMarkets": "invalid_number"}},
        )
        sb_corrupt_2 = SuperbetDiscoveredItem(
            event_id="sb_corrupt_2",
            match_name="Team C·Team D",
            start_time=self.now_str,
            metadata={},
        )
        sb_valid = SuperbetDiscoveredItem(
            event_id="sb_valid",
            match_name="Team E·Team F",
            start_time=self.now_str,
            metadata={"raw": {"totalMarkets": 50}},
        )

        sb_inst = MagicMock()
        sb_inst.discover.return_value = [sb_corrupt_1, sb_corrupt_2, sb_valid]
        sb_inst.run.return_value.parsed_objects = []
        mock_sb_cls.return_value = sb_inst

        bc_inst = MagicMock()
        bc_inst.discover.return_value = []
        bc_inst.run.return_value.parsed_objects = []
        mock_bc_cls.return_value = bc_inst

        p_inst = MagicMock()
        p_inst.run.return_value.parsed_objects = []
        mock_player_provider.return_value = p_inst

        t_inst = MagicMock()
        t_inst.run.return_value.parsed_objects = []
        mock_team_provider.return_value = t_inst

        mock_extract_player.return_value = []
        mock_extract_team.return_value = []

        scanner = GlobalPropsScanner()
        scope = GlobalScanScope(props_scope="PLAYER")
        budget = GlobalScanBudget(max_fixtures=1)

        result = scanner.execute_scan(scope=scope, budget=budget)

        self.assertEqual(result.funnel_metrics.fixtures_discovered, 3)
        self.assertEqual(result.funnel_metrics.fixtures_selected, 1)

        sb_selected = sb_inst.set_discovered_items.call_args[0][0]
        self.assertEqual(sb_selected[0].event_id, "sb_valid")

    @patch("scanner.global_props_scanner.SuperbetProvider")
    @patch("scanner.global_props_scanner.BetclicProvider")
    @patch("scanner.global_props_scanner.StatsHubProvider")
    @patch("scanner.global_props_scanner.StatsHubTeamPropsProvider")
    @patch("scanner.global_props_scanner.ExecutionMarketEngine.extract_quotes_from_graphs")
    @patch("scanner.global_props_scanner.ExecutionMarketEngine.extract_team_quotes_from_graphs")
    def test_unselected_fixtures_strictly_excluded_from_downstream_props(
        self,
        mock_extract_team,
        mock_extract_player,
        mock_team_provider,
        mock_player_provider,
        mock_bc_cls,
        mock_sb_cls,
    ):
        """Unselected fixtures are strictly excluded from downstream StatsHub props deduplication & evaluation."""
        sb_inst = MagicMock()
        sb_inst.discover.return_value = [self.sb_disc_a, self.sb_disc_b, self.sb_disc_c]
        sb_inst.run.return_value.parsed_objects = []
        mock_sb_cls.return_value = sb_inst

        bc_inst = MagicMock()
        bc_inst.discover.return_value = [self.bc_disc_a, self.bc_disc_b, self.bc_disc_c]
        bc_inst.run.return_value.parsed_objects = []
        mock_bc_cls.return_value = bc_inst

        # StatsHub returns props for A, B, and C
        p_inst = MagicMock()
        p_inst.run.return_value.parsed_objects = self.player_trends
        mock_player_provider.return_value = p_inst

        t_inst = MagicMock()
        t_inst.run.return_value.parsed_objects = []
        mock_team_provider.return_value = t_inst

        mock_extract_player.return_value = []
        mock_extract_team.return_value = []

        scanner = GlobalPropsScanner()
        scope = GlobalScanScope(props_scope="PLAYER", stat_types=["shots"])
        budget = GlobalScanBudget(max_fixtures=2)

        result = scanner.execute_scan(scope=scope, budget=budget)

        funnel = result.funnel_metrics
        # Only props for A and B must be in trends_deduplicated and evaluated
        self.assertEqual(funnel.fixtures_selected, 2)
        self.assertEqual(funnel.trends_deduplicated, 2)
        self.assertEqual(funnel.evaluated_count, 2)

        # Prop C (Tiny Player) must NOT be present in diagnostic or qualified results
        all_players = [o.player_name for o in result.qualified_opportunities] + [d.player_name for d in result.diagnostic_candidates]
        self.assertIn("Bukayo Saka", all_players)
        self.assertIn("Hugo Duro", all_players)
        self.assertNotIn("Tiny Player", all_players)


if __name__ == "__main__":
    unittest.main()
