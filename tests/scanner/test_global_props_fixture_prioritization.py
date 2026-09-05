"""
Unit & Integration Tests for Fixture Prioritization in GlobalPropsScanner.

Validates the principle: TIER = PRIORITY, NOT WHITELIST.

Requirements:
1. Higher tier has higher priority given comparable markets.
2. Lower tier can still be selected.
3. Absence of high-tier fixtures does not cause empty results when lower-tier fixtures exist.
4. Market coverage impacts ranking.
5. Lower tier with richer market coverage outranks higher tier with poor market coverage.
6. max_fixtures remains a hard limit.
7. Ranking is strictly deterministic regardless of input discovery ordering.
8. Deterministic tie-breaker is preserved.
9. Selected fixtures are the only ones passed to StatsHub props discovery & bounding.
10. PLAYER, TEAM, and ALL scopes function identically for fixture selection.
11. max_trends_requests remains a global limit.
12. max_execution_events remains a global limit.
13. MATCH_UNCERTAIN is not artificially generated.
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
    GlobalScanFunnelMetrics,
)


class TestGlobalPropsFixturePrioritization(unittest.TestCase):

    def setUp(self):
        self.now = datetime.now(timezone.utc)
        self.now_str = self.now.isoformat()
        self.tomorrow_str = (self.now + timedelta(days=1)).isoformat()

        # Fixture Tier 0 (Champions League) - 100 markets
        self.item_t0_100m = SuperbetDiscoveredItem(
            event_id="sb_t0_100",
            match_name="Real Madrid·Bayern Munich",
            competition_name="UEFA Champions League",
            start_time=self.now_str,
            metadata={"raw": {"totalMarkets": 100}},
        )

        # Fixture Tier 1 (Premier League) - 300 markets
        self.item_t1_300m = SuperbetDiscoveredItem(
            event_id="sb_t1_300",
            match_name="Arsenal·Chelsea",
            competition_name="Premier League",
            start_time=self.now_str,
            metadata={"raw": {"totalMarkets": 300}},
        )

        # Fixture Tier 0 (Champions League) - 300 markets
        self.item_t0_300m = SuperbetDiscoveredItem(
            event_id="sb_t0_300",
            match_name="Barcelona·PSG",
            competition_name="UEFA Champions League",
            start_time=self.now_str,
            metadata={"raw": {"totalMarkets": 300}},
        )

        # Fixture Tier 2 (Championship / Cup) - 450 markets
        self.item_t2_450m = SuperbetDiscoveredItem(
            event_id="sb_t2_450",
            match_name="Leeds·Sheffield United",
            competition_name="Championship",
            start_time=self.now_str,
            metadata={"raw": {"totalMarkets": 450}},
        )

        # Fixture Tier 2 (Standard League / Cup) - 300 markets
        self.item_t2_300m = SuperbetDiscoveredItem(
            event_id="sb_t2_300",
            match_name="Legia Warszawa·Wisla Krakow",
            competition_name="Puchar Polski",
            start_time=self.now_str,
            metadata={"raw": {"totalMarkets": 300}},
        )

        # Fixture Tier 2 (Standard League) - 120 markets
        self.item_t2_120m = SuperbetDiscoveredItem(
            event_id="sb_t2_120",
            match_name="Norwich·Watford",
            competition_name="Championship",
            start_time=self.now_str,
            metadata={"raw": {"totalMarkets": 120}},
        )

        # Fixture Tier 4 (Minor League) - 20 markets
        self.item_t4_20m = SuperbetDiscoveredItem(
            event_id="sb_t4_20",
            match_name="Minor Club A·Minor Club B",
            competition_name="Regional Amateur League",
            start_time=self.now_str,
            metadata={"raw": {"totalMarkets": 20}},
        )

        # Fixture Tier 4 (Minor League) - 50 markets
        self.item_t4_50m = SuperbetDiscoveredItem(
            event_id="sb_t4_50",
            match_name="Minor Club C·Minor Club D",
            competition_name="Regional Amateur League",
            start_time=self.now_str,
            metadata={"raw": {"totalMarkets": 50}},
        )

    # -------------------------------------------------------------------------
    # 1. Higher tier has higher priority given comparable / equal markets
    # -------------------------------------------------------------------------
    @patch("scanner.global_props_scanner.SuperbetProvider")
    @patch("scanner.global_props_scanner.BetclicProvider")
    @patch("scanner.global_props_scanner.StatsHubProvider")
    @patch("scanner.global_props_scanner.StatsHubTeamPropsProvider")
    @patch("scanner.global_props_scanner.ExecutionMarketEngine.extract_quotes_from_graphs")
    @patch("scanner.global_props_scanner.ExecutionMarketEngine.extract_team_quotes_from_graphs")
    def test_higher_tier_higher_priority_on_equal_markets(
        self, mock_extract_team, mock_extract_player, mock_team_p, mock_player_p, mock_bc_p, mock_sb_p
    ):
        """Tier 0 (300 mkts) > Tier 1 (300 mkts) > Tier 2 (300 mkts)."""
        sb_inst = MagicMock()
        # Discovered in reverse order (Tier 2, then Tier 1, then Tier 0)
        sb_inst.discover.return_value = [self.item_t2_300m, self.item_t1_300m, self.item_t0_300m]
        sb_inst.run.return_value.parsed_objects = []
        mock_sb_p.return_value = sb_inst

        bc_inst = MagicMock()
        bc_inst.discover.return_value = []
        bc_inst.run.return_value.parsed_objects = []
        mock_bc_p.return_value = bc_inst

        p_inst = MagicMock()
        p_inst.run.return_value.parsed_objects = []
        mock_player_p.return_value = p_inst

        t_inst = MagicMock()
        t_inst.run.return_value.parsed_objects = []
        mock_team_p.return_value = t_inst

        mock_extract_player.return_value = []
        mock_extract_team.return_value = []

        scanner = GlobalPropsScanner()
        scope = GlobalScanScope(props_scope="ALL")
        budget = GlobalScanBudget(max_fixtures=3)

        result = scanner.execute_scan(scope=scope, budget=budget)

        sb_inst.set_discovered_items.assert_called_once()
        selected_items = sb_inst.set_discovered_items.call_args[0][0]
        selected_ids = [item.event_id for item in selected_items]

        self.assertEqual(selected_ids, ["sb_t0_300", "sb_t1_300", "sb_t2_300"])

    @patch("scanner.global_props_scanner.SuperbetProvider")
    @patch("scanner.global_props_scanner.BetclicProvider")
    @patch("scanner.global_props_scanner.StatsHubProvider")
    @patch("scanner.global_props_scanner.StatsHubTeamPropsProvider")
    @patch("scanner.global_props_scanner.ExecutionMarketEngine.extract_quotes_from_graphs")
    @patch("scanner.global_props_scanner.ExecutionMarketEngine.extract_team_quotes_from_graphs")
    def test_tier_weighted_advantage_over_comparable_markets(
        self, mock_extract_team, mock_extract_player, mock_team_p, mock_player_p, mock_bc_p, mock_sb_p
    ):
        """Tier 0 with 250 markets (score: 375.0) outranks Tier 1 with 270 markets (score: 337.5)."""
        item_t0_250m = SuperbetDiscoveredItem(
            event_id="sb_t0_250",
            match_name="Real Madrid·Bayern Munich",
            competition_name="UEFA Champions League",
            start_time=self.now_str,
            metadata={"raw": {"totalMarkets": 250}},
        )
        item_t1_270m = SuperbetDiscoveredItem(
            event_id="sb_t1_270",
            match_name="Arsenal·Chelsea",
            competition_name="Premier League",
            start_time=self.now_str,
            metadata={"raw": {"totalMarkets": 270}},
        )

        sb_inst = MagicMock()
        sb_inst.discover.return_value = [item_t1_270m, item_t0_250m]
        sb_inst.run.return_value.parsed_objects = []
        mock_sb_p.return_value = sb_inst

        bc_inst = MagicMock()
        bc_inst.discover.return_value = []
        bc_inst.run.return_value.parsed_objects = []
        mock_bc_p.return_value = bc_inst

        p_inst = MagicMock()
        p_inst.run.return_value.parsed_objects = []
        mock_player_p.return_value = p_inst

        t_inst = MagicMock()
        t_inst.run.return_value.parsed_objects = []
        mock_team_p.return_value = t_inst

        mock_extract_player.return_value = []
        mock_extract_team.return_value = []

        scanner = GlobalPropsScanner()
        scope = GlobalScanScope(props_scope="ALL")
        budget = GlobalScanBudget(max_fixtures=2)

        result = scanner.execute_scan(scope=scope, budget=budget)

        sb_selected = [x.event_id for x in sb_inst.set_discovered_items.call_args[0][0]]
        self.assertEqual(sb_selected, ["sb_t0_250", "sb_t1_270"])

    # -------------------------------------------------------------------------
    # 2. Lower tier can still be selected
    # -------------------------------------------------------------------------
    @patch("scanner.global_props_scanner.SuperbetProvider")
    @patch("scanner.global_props_scanner.BetclicProvider")
    @patch("scanner.global_props_scanner.StatsHubProvider")
    @patch("scanner.global_props_scanner.StatsHubTeamPropsProvider")
    @patch("scanner.global_props_scanner.ExecutionMarketEngine.extract_quotes_from_graphs")
    @patch("scanner.global_props_scanner.ExecutionMarketEngine.extract_team_quotes_from_graphs")
    def test_lower_tier_can_be_selected(
        self, mock_extract_team, mock_extract_player, mock_team_p, mock_player_p, mock_bc_p, mock_sb_p
    ):
        """Tier 2 and Tier 4 fixtures can be selected when max_fixtures allows."""
        sb_inst = MagicMock()
        sb_inst.discover.return_value = [self.item_t0_300m, self.item_t2_450m, self.item_t4_50m]
        sb_inst.run.return_value.parsed_objects = []
        mock_sb_p.return_value = sb_inst

        bc_inst = MagicMock()
        bc_inst.discover.return_value = []
        bc_inst.run.return_value.parsed_objects = []
        mock_bc_p.return_value = bc_inst

        p_inst = MagicMock()
        p_inst.run.return_value.parsed_objects = []
        mock_player_p.return_value = p_inst

        t_inst = MagicMock()
        t_inst.run.return_value.parsed_objects = []
        mock_team_p.return_value = t_inst

        mock_extract_player.return_value = []
        mock_extract_team.return_value = []

        scanner = GlobalPropsScanner()
        scope = GlobalScanScope(props_scope="ALL")
        budget = GlobalScanBudget(max_fixtures=3)

        result = scanner.execute_scan(scope=scope, budget=budget)

        self.assertEqual(result.funnel_metrics.fixtures_selected, 3)
        sb_selected = [x.event_id for x in sb_inst.set_discovered_items.call_args[0][0]]
        self.assertIn("sb_t2_450", sb_selected)
        self.assertIn("sb_t4_50", sb_selected)

    # -------------------------------------------------------------------------
    # 3. Absence of high-tier fixtures does not cause empty results
    # -------------------------------------------------------------------------
    @patch("scanner.global_props_scanner.SuperbetProvider")
    @patch("scanner.global_props_scanner.BetclicProvider")
    @patch("scanner.global_props_scanner.StatsHubProvider")
    @patch("scanner.global_props_scanner.StatsHubTeamPropsProvider")
    @patch("scanner.global_props_scanner.ExecutionMarketEngine.extract_quotes_from_graphs")
    @patch("scanner.global_props_scanner.ExecutionMarketEngine.extract_team_quotes_from_graphs")
    def test_no_high_tier_fixtures_descends_cleanly(
        self, mock_extract_team, mock_extract_player, mock_team_p, mock_player_p, mock_bc_p, mock_sb_p
    ):
        """When only Tier 2 and Tier 4 fixtures are available, scanner descends cleanly and selects them."""
        sb_inst = MagicMock()
        sb_inst.discover.return_value = [self.item_t2_450m, self.item_t2_120m, self.item_t4_50m]
        sb_inst.run.return_value.parsed_objects = []
        mock_sb_p.return_value = sb_inst

        bc_inst = MagicMock()
        bc_inst.discover.return_value = []
        bc_inst.run.return_value.parsed_objects = []
        mock_bc_p.return_value = bc_inst

        p_inst = MagicMock()
        p_inst.run.return_value.parsed_objects = []
        mock_player_p.return_value = p_inst

        t_inst = MagicMock()
        t_inst.run.return_value.parsed_objects = []
        mock_team_p.return_value = t_inst

        mock_extract_player.return_value = []
        mock_extract_team.return_value = []

        scanner = GlobalPropsScanner()
        scope = GlobalScanScope(props_scope="ALL")
        budget = GlobalScanBudget(max_fixtures=2)

        result = scanner.execute_scan(scope=scope, budget=budget)

        self.assertEqual(result.funnel_metrics.fixtures_selected, 2)
        sb_selected = [x.event_id for x in sb_inst.set_discovered_items.call_args[0][0]]
        self.assertEqual(sb_selected, ["sb_t2_450", "sb_t2_120"])

    # -------------------------------------------------------------------------
    # 4. Market coverage impacts ranking
    # -------------------------------------------------------------------------
    @patch("scanner.global_props_scanner.SuperbetProvider")
    @patch("scanner.global_props_scanner.BetclicProvider")
    @patch("scanner.global_props_scanner.StatsHubProvider")
    @patch("scanner.global_props_scanner.StatsHubTeamPropsProvider")
    @patch("scanner.global_props_scanner.ExecutionMarketEngine.extract_quotes_from_graphs")
    @patch("scanner.global_props_scanner.ExecutionMarketEngine.extract_team_quotes_from_graphs")
    def test_market_coverage_impacts_ranking_within_same_tier(
        self, mock_extract_team, mock_extract_player, mock_team_p, mock_player_p, mock_bc_p, mock_sb_p
    ):
        """Within Tier 2, fixture with 450 markets beats fixture with 120 markets."""
        sb_inst = MagicMock()
        sb_inst.discover.return_value = [self.item_t2_120m, self.item_t2_450m]
        sb_inst.run.return_value.parsed_objects = []
        mock_sb_p.return_value = sb_inst

        bc_inst = MagicMock()
        bc_inst.discover.return_value = []
        bc_inst.run.return_value.parsed_objects = []
        mock_bc_p.return_value = bc_inst

        p_inst = MagicMock()
        p_inst.run.return_value.parsed_objects = []
        mock_player_p.return_value = p_inst

        t_inst = MagicMock()
        t_inst.run.return_value.parsed_objects = []
        mock_team_p.return_value = t_inst

        mock_extract_player.return_value = []
        mock_extract_team.return_value = []

        scanner = GlobalPropsScanner()
        scope = GlobalScanScope(props_scope="ALL")
        budget = GlobalScanBudget(max_fixtures=2)

        result = scanner.execute_scan(scope=scope, budget=budget)

        sb_selected = [x.event_id for x in sb_inst.set_discovered_items.call_args[0][0]]
        self.assertEqual(sb_selected, ["sb_t2_450", "sb_t2_120"])

    # -------------------------------------------------------------------------
    # 5. Lower tier with richer market coverage outranks higher tier with poor market coverage
    # -------------------------------------------------------------------------
    @patch("scanner.global_props_scanner.SuperbetProvider")
    @patch("scanner.global_props_scanner.BetclicProvider")
    @patch("scanner.global_props_scanner.StatsHubProvider")
    @patch("scanner.global_props_scanner.StatsHubTeamPropsProvider")
    @patch("scanner.global_props_scanner.ExecutionMarketEngine.extract_quotes_from_graphs")
    @patch("scanner.global_props_scanner.ExecutionMarketEngine.extract_team_quotes_from_graphs")
    def test_lower_tier_with_rich_markets_outranks_higher_tier_with_poor_markets(
        self, mock_extract_team, mock_extract_player, mock_team_p, mock_player_p, mock_bc_p, mock_sb_p
    ):
        """Tier 2 (450 markets) outranks Tier 0 (100 markets), and Tier 4 (20 markets) is ranked 3rd."""
        sb_inst = MagicMock()
        # Order: Tier 4, Tier 0, Tier 2
        sb_inst.discover.return_value = [self.item_t4_20m, self.item_t0_100m, self.item_t2_450m]
        sb_inst.run.return_value.parsed_objects = []
        mock_sb_p.return_value = sb_inst

        bc_inst = MagicMock()
        bc_inst.discover.return_value = []
        bc_inst.run.return_value.parsed_objects = []
        mock_bc_p.return_value = bc_inst

        p_inst = MagicMock()
        p_inst.run.return_value.parsed_objects = []
        mock_player_p.return_value = p_inst

        t_inst = MagicMock()
        t_inst.run.return_value.parsed_objects = []
        mock_team_p.return_value = t_inst

        mock_extract_player.return_value = []
        mock_extract_team.return_value = []

        scanner = GlobalPropsScanner()
        scope = GlobalScanScope(props_scope="ALL")
        budget = GlobalScanBudget(max_fixtures=3)

        result = scanner.execute_scan(scope=scope, budget=budget)

        sb_selected = [x.event_id for x in sb_inst.set_discovered_items.call_args[0][0]]
        # Tier 2 with 450 markets (score: 450) must beat Tier 0 with 100 markets (score: 150)
        # and Tier 4 with 20 markets (score: 10)
        self.assertEqual(sb_selected, ["sb_t2_450", "sb_t0_100", "sb_t4_20"])

    # -------------------------------------------------------------------------
    # 6. max_fixtures remains a hard limit
    # -------------------------------------------------------------------------
    @patch("scanner.global_props_scanner.SuperbetProvider")
    @patch("scanner.global_props_scanner.BetclicProvider")
    @patch("scanner.global_props_scanner.StatsHubProvider")
    @patch("scanner.global_props_scanner.StatsHubTeamPropsProvider")
    @patch("scanner.global_props_scanner.ExecutionMarketEngine.extract_quotes_from_graphs")
    @patch("scanner.global_props_scanner.ExecutionMarketEngine.extract_team_quotes_from_graphs")
    def test_max_fixtures_hard_ceiling(
        self, mock_extract_team, mock_extract_player, mock_team_p, mock_player_p, mock_bc_p, mock_sb_p
    ):
        """When 6 candidates exist, max_fixtures=2 strictly limits selected candidates to 2."""
        sb_inst = MagicMock()
        sb_inst.discover.return_value = [
            self.item_t0_300m,
            self.item_t1_300m,
            self.item_t2_450m,
            self.item_t2_300m,
            self.item_t2_120m,
            self.item_t4_50m,
        ]
        sb_inst.run.return_value.parsed_objects = []
        mock_sb_p.return_value = sb_inst

        bc_inst = MagicMock()
        bc_inst.discover.return_value = []
        bc_inst.run.return_value.parsed_objects = []
        mock_bc_p.return_value = bc_inst

        p_inst = MagicMock()
        p_inst.run.return_value.parsed_objects = []
        mock_player_p.return_value = p_inst

        t_inst = MagicMock()
        t_inst.run.return_value.parsed_objects = []
        mock_team_p.return_value = t_inst

        mock_extract_player.return_value = []
        mock_extract_team.return_value = []

        scanner = GlobalPropsScanner()
        scope = GlobalScanScope(props_scope="ALL")
        budget = GlobalScanBudget(max_fixtures=2)

        result = scanner.execute_scan(scope=scope, budget=budget)

        self.assertEqual(result.funnel_metrics.fixtures_discovered, 6)
        self.assertEqual(result.funnel_metrics.fixtures_selected, 2)
        sb_selected = [x.event_id for x in sb_inst.set_discovered_items.call_args[0][0]]
        self.assertEqual(len(sb_selected), 2)

    # -------------------------------------------------------------------------
    # 7. Ranking determinism regardless of input discovery order
    # -------------------------------------------------------------------------
    @patch("scanner.global_props_scanner.SuperbetProvider")
    @patch("scanner.global_props_scanner.BetclicProvider")
    @patch("scanner.global_props_scanner.StatsHubProvider")
    @patch("scanner.global_props_scanner.StatsHubTeamPropsProvider")
    @patch("scanner.global_props_scanner.ExecutionMarketEngine.extract_quotes_from_graphs")
    @patch("scanner.global_props_scanner.ExecutionMarketEngine.extract_team_quotes_from_graphs")
    def test_ranking_determinism_input_order_invariant(
        self, mock_extract_team, mock_extract_player, mock_team_p, mock_player_p, mock_bc_p, mock_sb_p
    ):
        """Any permutation of input discovery order produces identical selected fixtures order."""
        sb_inst = MagicMock()
        bc_inst = MagicMock()
        mock_sb_p.return_value = sb_inst
        mock_bc_p.return_value = bc_inst

        p_inst = MagicMock()
        p_inst.run.return_value.parsed_objects = []
        mock_player_p.return_value = p_inst

        t_inst = MagicMock()
        t_inst.run.return_value.parsed_objects = []
        mock_team_p.return_value = t_inst

        mock_extract_player.return_value = []
        mock_extract_team.return_value = []

        items_order_1 = [self.item_t0_100m, self.item_t2_450m, self.item_t1_300m, self.item_t4_50m]
        items_order_2 = [self.item_t4_50m, self.item_t1_300m, self.item_t0_100m, self.item_t2_450m]

        scanner = GlobalPropsScanner()
        scope = GlobalScanScope(props_scope="ALL")
        budget = GlobalScanBudget(max_fixtures=3)

        sb_inst.discover.return_value = items_order_1
        bc_inst.discover.return_value = []
        scanner.execute_scan(scope=scope, budget=budget)
        order_1_selected = [x.event_id for x in sb_inst.set_discovered_items.call_args[0][0]]

        sb_inst.set_discovered_items.reset_mock()
        sb_inst.discover.return_value = items_order_2
        scanner.execute_scan(scope=scope, budget=budget)
        order_2_selected = [x.event_id for x in sb_inst.set_discovered_items.call_args[0][0]]

        self.assertEqual(order_1_selected, order_2_selected)
        # Expected order: t2_450 (score 450), t1_300 (score 375), t0_100 (score 150)
        self.assertEqual(order_1_selected, ["sb_t2_450", "sb_t1_300", "sb_t0_100"])

    # -------------------------------------------------------------------------
    # 8. Deterministic tie-breaker is preserved
    # -------------------------------------------------------------------------
    @patch("scanner.global_props_scanner.SuperbetProvider")
    @patch("scanner.global_props_scanner.BetclicProvider")
    @patch("scanner.global_props_scanner.StatsHubProvider")
    @patch("scanner.global_props_scanner.StatsHubTeamPropsProvider")
    @patch("scanner.global_props_scanner.ExecutionMarketEngine.extract_quotes_from_graphs")
    @patch("scanner.global_props_scanner.ExecutionMarketEngine.extract_team_quotes_from_graphs")
    def test_deterministic_tie_breakers(
        self, mock_extract_team, mock_extract_player, mock_team_p, mock_player_p, mock_bc_p, mock_sb_p
    ):
        """When priority score and tier are equal, kickoff timestamp and match name resolve tie deterministically."""
        item_early = SuperbetDiscoveredItem(
            event_id="sb_early",
            match_name="Chelsea·Arsenal",
            competition_name="Premier League",
            start_time=self.now_str,
            metadata={"raw": {"totalMarkets": 200}},
        )
        item_late = SuperbetDiscoveredItem(
            event_id="sb_late",
            match_name="Liverpool·Man City",
            competition_name="Premier League",
            start_time=self.tomorrow_str,
            metadata={"raw": {"totalMarkets": 200}},
        )

        sb_inst = MagicMock()
        sb_inst.discover.return_value = [item_late, item_early]
        sb_inst.run.return_value.parsed_objects = []
        mock_sb_p.return_value = sb_inst

        bc_inst = MagicMock()
        bc_inst.discover.return_value = []
        bc_inst.run.return_value.parsed_objects = []
        mock_bc_p.return_value = bc_inst

        p_inst = MagicMock()
        p_inst.run.return_value.parsed_objects = []
        mock_player_p.return_value = p_inst

        t_inst = MagicMock()
        t_inst.run.return_value.parsed_objects = []
        mock_team_p.return_value = t_inst

        mock_extract_player.return_value = []
        mock_extract_team.return_value = []

        scanner = GlobalPropsScanner()
        scope = GlobalScanScope(props_scope="ALL")
        budget = GlobalScanBudget(max_fixtures=1)

        result = scanner.execute_scan(scope=scope, budget=budget)

        sb_selected = [x.event_id for x in sb_inst.set_discovered_items.call_args[0][0]]
        self.assertEqual(sb_selected, ["sb_early"])

    # -------------------------------------------------------------------------
    # 9. Selected fixtures are the only ones passed to downstream StatsHub discovery / bounding
    # -------------------------------------------------------------------------
    @patch("scanner.global_props_scanner.SuperbetProvider")
    @patch("scanner.global_props_scanner.BetclicProvider")
    @patch("scanner.global_props_scanner.StatsHubProvider")
    @patch("scanner.global_props_scanner.StatsHubTeamPropsProvider")
    @patch("scanner.global_props_scanner.ExecutionMarketEngine.extract_quotes_from_graphs")
    @patch("scanner.global_props_scanner.ExecutionMarketEngine.extract_team_quotes_from_graphs")
    def test_only_selected_fixtures_passed_to_statshub_bounding(
        self, mock_extract_team, mock_extract_player, mock_team_p, mock_player_p, mock_bc_p, mock_sb_p
    ):
        """Props belonging to non-selected fixtures are strictly filtered out of trends_deduplicated and evaluation."""
        sb_inst = MagicMock()
        sb_inst.discover.return_value = [self.item_t2_450m, self.item_t4_20m]
        sb_inst.run.return_value.parsed_objects = []
        mock_sb_p.return_value = sb_inst

        bc_inst = MagicMock()
        bc_inst.discover.return_value = []
        bc_inst.run.return_value.parsed_objects = []
        mock_bc_p.return_value = bc_inst

        prop_selected = StatsHubPropResult(
            player_stat=StatsHubPlayerStat(
                player_id=1,
                player_name="Patrick Bamford",
                team="Leeds",
                opponent="Sheffield United",
                stat_type="shots",
                line=1.5,
                odds_type="over",
                hit_rate_pct=75.0,
                sample_size=10,
                average=2.0,
                fixture=StatsHubFixture(fixture_id="fix_leeds", home_team="Leeds", away_team="Sheffield United", competition="Championship", kickoff=self.now_str),
                bookmaker_odds=[StatsHubBookmakerOdds(bookmaker="Bet365", line=1.5, side="over", decimal_odds=1.80)],
            )
        )
        prop_excluded = StatsHubPropResult(
            player_stat=StatsHubPlayerStat(
                player_id=2,
                player_name="Minor Guy",
                team="Minor Club A",
                opponent="Minor Club B",
                stat_type="shots",
                line=0.5,
                odds_type="over",
                hit_rate_pct=60.0,
                sample_size=10,
                average=1.0,
                fixture=StatsHubFixture(fixture_id="fix_minor", home_team="Minor Club A", away_team="Minor Club B", competition="Regional Amateur League", kickoff=self.now_str),
                bookmaker_odds=[StatsHubBookmakerOdds(bookmaker="Bet365", line=0.5, side="over", decimal_odds=1.60)],
            )
        )

        p_inst = MagicMock()
        p_inst.run.return_value.parsed_objects = [prop_selected, prop_excluded]
        mock_player_p.return_value = p_inst

        t_inst = MagicMock()
        t_inst.run.return_value.parsed_objects = []
        mock_team_p.return_value = t_inst

        mock_extract_player.return_value = []
        mock_extract_team.return_value = []

        scanner = GlobalPropsScanner()
        scope = GlobalScanScope(props_scope="PLAYER", stat_types=["shots"])
        budget = GlobalScanBudget(max_fixtures=1)

        result = scanner.execute_scan(scope=scope, budget=budget)

        funnel = result.funnel_metrics
        self.assertEqual(funnel.fixtures_selected, 1)
        self.assertEqual(funnel.trends_discovered, 2)
        self.assertEqual(funnel.trends_deduplicated, 1)
        self.assertEqual(funnel.evaluated_count, 1)

        all_names = [o.player_name for o in result.qualified_opportunities] + [d.player_name for d in result.diagnostic_candidates]
        self.assertIn("Patrick Bamford", all_names)
        self.assertNotIn("Minor Guy", all_names)

    # -------------------------------------------------------------------------
    # 10. PLAYER, TEAM, and ALL scopes function identically for fixture selection
    # -------------------------------------------------------------------------
    @patch("scanner.global_props_scanner.SuperbetProvider")
    @patch("scanner.global_props_scanner.BetclicProvider")
    @patch("scanner.global_props_scanner.StatsHubProvider")
    @patch("scanner.global_props_scanner.StatsHubTeamPropsProvider")
    @patch("scanner.global_props_scanner.ExecutionMarketEngine.extract_quotes_from_graphs")
    @patch("scanner.global_props_scanner.ExecutionMarketEngine.extract_team_quotes_from_graphs")
    def test_scope_preservation_player_team_all(
        self, mock_extract_team, mock_extract_player, mock_team_p, mock_player_p, mock_bc_p, mock_sb_p
    ):
        """Fixture prioritization orders candidates identically regardless of props_scope."""
        sb_inst = MagicMock()
        sb_inst.discover.return_value = [self.item_t0_100m, self.item_t2_450m]
        sb_inst.run.return_value.parsed_objects = []
        mock_sb_p.return_value = sb_inst

        bc_inst = MagicMock()
        bc_inst.discover.return_value = []
        bc_inst.run.return_value.parsed_objects = []
        mock_bc_p.return_value = bc_inst

        p_inst = MagicMock()
        p_inst.run.return_value.parsed_objects = []
        mock_player_p.return_value = p_inst

        t_inst = MagicMock()
        t_inst.run.return_value.parsed_objects = []
        mock_team_p.return_value = t_inst

        mock_extract_player.return_value = []
        mock_extract_team.return_value = []

        scanner = GlobalPropsScanner()
        budget = GlobalScanBudget(max_fixtures=2)

        for scope_type in ("PLAYER", "TEAM", "ALL"):
            sb_inst.set_discovered_items.reset_mock()
            scope = GlobalScanScope(props_scope=scope_type, stat_types=["shots"])
            scanner.execute_scan(scope=scope, budget=budget)
            sb_selected = [x.event_id for x in sb_inst.set_discovered_items.call_args[0][0]]
            self.assertEqual(
                sb_selected,
                ["sb_t2_450", "sb_t0_100"],
                f"Scope {scope_type} failed to prioritize Tier 2 rich markets over Tier 0 poor markets",
            )

    # -------------------------------------------------------------------------
    # 11. max_trends_requests remains a global limit
    # -------------------------------------------------------------------------
    @patch("scanner.global_props_scanner.SuperbetProvider")
    @patch("scanner.global_props_scanner.BetclicProvider")
    @patch("scanner.global_props_scanner.StatsHubProvider")
    @patch("scanner.global_props_scanner.StatsHubTeamPropsProvider")
    def test_budget_max_trends_requests_global_limit(
        self, mock_team_p, mock_player_p, mock_bc_p, mock_sb_p
    ):
        """max_trends_requests=2 limits total requests across PLAYER and TEAM to exactly 2."""
        mock_sb_p.return_value.discover.return_value = []
        mock_bc_p.return_value.discover.return_value = []
        mock_player_p.return_value.run.return_value.parsed_objects = []
        mock_team_p.return_value.run.return_value.parsed_objects = []

        scanner = GlobalPropsScanner()
        scope = GlobalScanScope(props_scope="ALL", stat_types=["shots", "fouls", "corners"])
        budget = GlobalScanBudget(max_trends_requests=2)
        scanner.execute_scan(scope=scope, budget=budget)

        total_reqs = mock_player_p.call_count + mock_team_p.call_count
        self.assertEqual(total_reqs, 2)

    # -------------------------------------------------------------------------
    # 12. max_execution_events remains a global limit
    # -------------------------------------------------------------------------
    @patch("scanner.global_props_scanner.SuperbetProvider")
    @patch("scanner.global_props_scanner.BetclicProvider")
    @patch("scanner.global_props_scanner.StatsHubProvider")
    @patch("scanner.global_props_scanner.StatsHubTeamPropsProvider")
    @patch("scanner.global_props_scanner.ExecutionMarketEngine.extract_quotes_from_graphs")
    @patch("scanner.global_props_scanner.ExecutionMarketEngine.extract_team_quotes_from_graphs")
    def test_budget_max_execution_events_global_limit(
        self, mock_extract_team, mock_extract_player, mock_team_p, mock_player_p, mock_bc_p, mock_sb_p
    ):
        """max_execution_events=1 limits Polish execution details query to at most 1 event."""
        sb_inst = MagicMock()
        sb_inst.discover.return_value = [self.item_t0_300m, self.item_t2_450m]
        sb_inst.run.return_value.parsed_objects = []
        mock_sb_p.return_value = sb_inst

        bc_inst = MagicMock()
        bc_inst.discover.return_value = []
        bc_inst.run.return_value.parsed_objects = []
        mock_bc_p.return_value = bc_inst

        mock_player_p.return_value.run.return_value.parsed_objects = []
        mock_team_p.return_value.run.return_value.parsed_objects = []
        mock_extract_player.return_value = []
        mock_extract_team.return_value = []

        scanner = GlobalPropsScanner()
        scope = GlobalScanScope(props_scope="PLAYER")
        budget = GlobalScanBudget(max_fixtures=5, max_execution_events=1)

        scanner.execute_scan(scope=scope, budget=budget)

        sb_inst.set_discovered_items.assert_called_once()
        sb_selected = sb_inst.set_discovered_items.call_args[0][0]
        self.assertEqual(len(sb_selected), 1)

    # -------------------------------------------------------------------------
    # 13. MATCH_UNCERTAIN is not artificially generated
    # -------------------------------------------------------------------------
    def test_match_uncertain_not_artificially_generated(self):
        """Funnel match_uncertain defaults to 0 and is not artificially incremented on normal rejection."""
        funnel = GlobalScanFunnelMetrics(fixtures_discovered=10, fixtures_selected=3)
        funnel.increment_rejection("INSUFFICIENT_REFERENCE_SOURCES")
        funnel.increment_rejection("BELOW_VALUE_THRESHOLD")

        self.assertEqual(funnel.match_uncertain, 0)
        self.assertEqual(funnel.to_dict()["match_uncertain"], 0)


if __name__ == "__main__":
    unittest.main()
