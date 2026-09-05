"""
RED Test Suite for Global Props Scanner:
1. Problem A: API / UI dataflow consistency, min_ev_percent 0.0 handling, diagnostic candidates in GET.
2. Problem B: Championship / lower leagues team matching, fixture selection, and end-to-end scanner participation.
"""

import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

from database.config import DatabaseConfig
from database.connection import DatabaseManager
from api.services import PlatformAPIService
from api.routes import APIRouter
from normalization.identity import normalize_team_name
from normalization.competitions import resolve_canonical_competition
from scanner.prop_execution_matcher import PropExecutionMatcher
from scanner.team_prop_execution_matcher import TeamPropExecutionMatcher
from scanner.global_props_scanner import (
    GlobalPropsScanner,
    GlobalScanScope,
    GlobalScanBudget,
    GlobalScanOpportunity,
    GlobalScanResult,
    GlobalScanFunnelMetrics,
)
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


class TestGlobalPropsChampionshipAndUISync(unittest.TestCase):

    def setUp(self):
        PlatformAPIService._cached_global_props_results = None
        self.db_manager = DatabaseManager(DatabaseConfig.default_sqlite_in_memory())
        self.service = PlatformAPIService(db_manager=self.db_manager)
        if hasattr(self.service, "scheduler") and self.service.scheduler is not None:
            self.service.scheduler.stop()
        self.router = APIRouter(service=self.service)

    def tearDown(self):
        PlatformAPIService._cached_global_props_results = None
        if hasattr(self.service, "scheduler") and self.service.scheduler is not None:
            self.service.scheduler.stop()
        if hasattr(self.db_manager, "dispose"):
            self.db_manager.dispose()

    # =========================================================================
    # PROBLEM A: API / UI DATAFLOW & CONTRACT CONSISTENCY
    # =========================================================================

    def test_problem_a1_scan_global_props_accepts_zero_min_ev_percent(self):
        """PlatformAPIService.scan_global_props must respect min_ev_percent=0.0 and NOT default to 3.0."""
        with patch("scanner.global_props_scanner.GlobalPropsScanner.execute_scan") as mock_exec:
            mock_exec.return_value = GlobalScanResult(
                scope={"props_scope": "ALL", "min_ev_percent": 0.0},
                budget={},
                status="SUCCESS",
                funnel_metrics=GlobalScanFunnelMetrics(),
                qualified_opportunities=[],
                diagnostic_candidates=[],
                duration_ms=10.0,
            )

            # Trigger scan with explicit 0.0% min_ev_percent
            self.service.scan_global_props({"props_scope": "ALL", "min_ev_percent": 0.0})

            mock_exec.assert_called_once()
            called_scope = mock_exec.call_args[1].get("scope") or mock_exec.call_args[0][0]
            self.assertEqual(called_scope.min_ev_percent, 0.0, "min_ev_percent=0.0 was coerced to default 3.0 due to falsy evaluation bug")

    def test_problem_a2_get_global_props_preserves_diagnostic_candidates(self):
        """GET /api/v1/props/global-results must expose diagnostic_candidates from cached scan."""
        mock_diag = {
            "canonical_prop_key": "prop:diag:1",
            "player_name": "Rejected Player",
            "team": "Team A",
            "status": "REJECTED",
            "reason_code": "POLISH_ODDS_UNAVAILABLE",
        }
        cached_payload = {
            "status": "SUCCESS",
            "qualified_count": 0,
            "total_qualified_matching_filter": 0,
            "qualified_opportunities": [],
            "diagnostic_candidates": [mock_diag],
            "funnel_metrics": {
                "fixtures_discovered": 10,
                "fixtures_selected": 5,
                "trends_deduplicated": 50,
                "evaluated_count": 50,
                "qualified_count": 0,
                "rejected_count": 50,
            },
            "scanned_at": "2026-08-31T21:00:00Z",
        }
        PlatformAPIService._cached_global_props_results = cached_payload

        res = self.service.get_global_props_results()
        self.assertIn("diagnostic_candidates", res)
        self.assertEqual(len(res["diagnostic_candidates"]), 1)
        self.assertEqual(res["diagnostic_candidates"][0]["player_name"], "Rejected Player")

    def test_problem_a3_post_and_get_symmetric_filtered_count(self):
        """Both POST and GET responses must provide total_qualified_matching_filter."""
        mock_opp = {
            "canonical_prop_key": "prop:saka:shots:1.5:OVER",
            "player_name": "Bukayo Saka",
            "team": "Arsenal",
            "opponent": "Chelsea",
            "stat_type": "SHOTS",
            "line": 1.5,
            "side": "OVER",
            "net_ev_pct": 5.0,
            "status": "QUALIFIED",
        }
        mock_scan_res = {
            "status": "SUCCESS",
            "qualified_count": 1,
            "total_qualified_matching_filter": 1,
            "qualified_opportunities": [mock_opp],
            "diagnostic_candidates": [],
            "funnel_metrics": {"qualified_count": 1},
            "scanned_at": "2026-08-31T21:00:00Z",
        }

        with patch.object(PlatformAPIService, "scan_global_props", return_value=mock_scan_res):
            post_res = self.router.handle_post_global_props_scan({"props_scope": "PLAYER"})
            self.assertEqual(post_res.data.get("total_qualified_matching_filter"), 1)

    # =========================================================================
    # PROBLEM B: CHAMPIONSHIP / LOWER LEAGUES MATCHING & PIPELINE
    # =========================================================================

    def test_problem_b1_championship_team_and_fixture_matching(self):
        """PropExecutionMatcher and TeamPropExecutionMatcher must match standard Championship team variants."""
        pairs_to_test = [
            ("Millwall", "Bolton", "Millwall", "Bolton Wanderers"),
            ("Leeds", "Sheffield United", "Leeds United", "Sheffield Utd"),
            ("West Brom", "Norwich", "West Bromwich Albion", "Norwich City"),
            ("Blackburn", "QPR", "Blackburn Rovers", "Queens Park Rangers"),
            ("Preston", "Coventry", "Preston North End", "Coventry City"),
            ("Sheffield Wednesday", "Hull", "Sheffield Wed", "Hull City"),
            ("Oxford United", "Portsmouth", "Oxford Utd", "Portsmouth"),
        ]

        for target_h, target_a, cand_h, cand_a in pairs_to_test:
            p_match, p_conf = PropExecutionMatcher.is_fixture_match(target_h, target_a, cand_h, cand_a)
            self.assertTrue(
                p_match,
                f"PropExecutionMatcher failed to match fixture: '{target_h} vs {target_a}' <=> '{cand_h} vs {cand_a}'",
            )
            self.assertGreaterEqual(p_conf, 0.85)

            t_match, t_conf = TeamPropExecutionMatcher.is_fixture_match(target_h, target_a, cand_h, cand_a)
            self.assertTrue(
                t_match,
                f"TeamPropExecutionMatcher failed to match fixture: '{target_h} vs {target_a}' <=> '{cand_h} vs {cand_a}'",
            )
            self.assertGreaterEqual(t_conf, 0.85)

    @patch("scanner.global_props_scanner.SuperbetProvider")
    @patch("scanner.global_props_scanner.BetclicProvider")
    @patch("scanner.global_props_scanner.StatsHubProvider")
    @patch("scanner.global_props_scanner.StatsHubTeamPropsProvider")
    @patch("scanner.global_props_scanner.ExecutionMarketEngine.extract_quotes_from_graphs")
    @patch("scanner.global_props_scanner.ExecutionMarketEngine.extract_team_quotes_from_graphs")
    def test_problem_b2_championship_fixture_full_participation_in_scan(
        self,
        mock_extract_team,
        mock_extract_player,
        mock_team_p,
        mock_player_p,
        mock_bc_p,
        mock_sb_p,
    ):
        """Championship match (Millwall vs Bolton) is discovered, scored by markets, selected, and evaluated."""
        now_str = datetime.now(timezone.utc).isoformat()

        # 1. Superbet discovers Championship fixture
        sb_item = SuperbetDiscoveredItem(
            event_id="sb_champ_1",
            match_name="Millwall·Bolton",
            competition_name="Anglia - Championship",
            start_time=now_str,
            metadata={"raw": {"totalMarkets": 220}},
        )
        sb_inst = MagicMock()
        sb_inst.discover.return_value = [sb_item]
        sb_inst.run.return_value.parsed_objects = []
        mock_sb_p.return_value = sb_inst

        # 2. Betclic discovers Championship fixture
        bc_item = BetclicDiscoveredItem(
            provider_event_id="bc_champ_1",
            name="Millwall - Bolton Wanderers",
            competition_name="Anglia - Championship",
            url="https://betclic.pl/championship-match",
            start_time=now_str,
            metadata={"raw": {"total_markets_count": 180}},
        )
        bc_inst = MagicMock()
        bc_inst.discover.return_value = [bc_item]
        bc_inst.run.return_value.parsed_objects = []
        mock_bc_p.return_value = bc_inst

        # 3. StatsHub returns player trends for Millwall vs Bolton
        champ_prop = StatsHubPropResult(
            player_stat=StatsHubPlayerStat(
                player_id=501,
                player_name="Josh Coburn",
                team="Millwall",
                opponent="Bolton",
                stat_type="shots",
                line=1.5,
                odds_type="over",
                hit_rate_pct=80.0,
                sample_size=10,
                average=2.3,
                fixture=StatsHubFixture(
                    fixture_id="sh_champ_1",
                    home_team="Millwall",
                    away_team="Bolton",
                    competition="Championship",
                    kickoff=now_str,
                ),
                bookmaker_odds=[
                    StatsHubBookmakerOdds(bookmaker="Bet365", line=1.5, side="over", decimal_odds=1.80)
                ],
            )
        )
        p_inst = MagicMock()
        p_inst.run.return_value.parsed_objects = [champ_prop]
        mock_player_p.return_value = p_inst

        t_inst = MagicMock()
        t_inst.run.return_value.parsed_objects = []
        mock_team_p.return_value = t_inst

        # 4. Polish bookmaker quote for Josh Coburn (Net EV +18.7% -> QUALIFIED)
        mock_extract_player.return_value = [
            NormalizedExecutionQuote(
                bookmaker="Superbet",
                player="Josh Coburn",
                fixture="Millwall vs Bolton",
                stat_type="SHOTS",
                line=1.5,
                side="OVER",
                odds=2.40,
                active=True,
                event_id="sb_champ_1",
            )
        ]
        mock_extract_team.return_value = []

        scanner = GlobalPropsScanner()
        scope = GlobalScanScope(props_scope="PLAYER", stat_types=["shots"], min_ev_percent=3.0)
        budget = GlobalScanBudget(max_fixtures=10, max_execution_events=10)

        result = scanner.execute_scan(scope=scope, budget=budget)

        # Assertions:
        # 1. Fixture discovered and selected
        self.assertEqual(result.funnel_metrics.fixtures_discovered, 1)
        self.assertEqual(result.funnel_metrics.fixtures_selected, 1)

        # 2. Selected candidates must include Millwall vs Bolton with Tier 2 and total market coverage 400 (220+180)
        self.assertEqual(len(result.selected_fixtures), 1)
        sel_fix = result.selected_fixtures[0]
        sel_tier = sel_fix.get("tier") if isinstance(sel_fix, dict) else getattr(sel_fix, "tier", None)
        sel_cov = sel_fix.get("total_market_coverage") if isinstance(sel_fix, dict) else getattr(sel_fix, "total_market_coverage", None)
        sel_score = sel_fix.get("priority_score") if isinstance(sel_fix, dict) else getattr(sel_fix, "priority_score", None)
        self.assertEqual(sel_tier, 1)
        self.assertEqual(sel_cov, 400)
        self.assertEqual(sel_score, 500.0)

        # 3. Prop must be scoped, matched, and QUALIFIED
        self.assertEqual(result.funnel_metrics.trends_deduplicated, 1)
        self.assertEqual(result.funnel_metrics.evaluated_count, 1)
        self.assertEqual(result.funnel_metrics.qualified_count, 1)
        self.assertEqual(len(result.qualified_opportunities), 1)
        opp = result.qualified_opportunities[0]
        self.assertEqual(opp.player_name, "Josh Coburn")
        self.assertEqual(opp.competition, "Championship")
        self.assertEqual(opp.status, "QUALIFIED")
        self.assertGreater(opp.net_ev_pct, 3.0)

    @patch("scanner.global_props_scanner.SuperbetProvider")
    @patch("scanner.global_props_scanner.BetclicProvider")
    @patch("scanner.global_props_scanner.StatsHubProvider")
    @patch("scanner.global_props_scanner.StatsHubTeamPropsProvider")
    @patch("scanner.global_props_scanner.ExecutionMarketEngine.extract_quotes_from_graphs")
    @patch("scanner.global_props_scanner.ExecutionMarketEngine.extract_team_quotes_from_graphs")
    def test_problem_b3_championship_name_variants_end_to_end(
        self,
        mock_extract_team,
        mock_extract_player,
        mock_team_p,
        mock_player_p,
        mock_bc_p,
        mock_sb_p,
    ):
        """Championship match with abbreviated/variant names (Leeds United vs Sheffield Utd) is matched and evaluated end-to-end."""
        now_str = datetime.now(timezone.utc).isoformat()

        # 1. Superbet discovers 'Leeds - Sheffield United'
        sb_item = SuperbetDiscoveredItem(
            event_id="sb_leeds_1",
            match_name="Leeds·Sheffield United",
            competition_name="Championship",
            start_time=now_str,
            metadata={"raw": {"totalMarkets": 250}},
        )
        sb_inst = MagicMock()
        sb_inst.discover.return_value = [sb_item]
        sb_inst.run.return_value.parsed_objects = []
        mock_sb_p.return_value = sb_inst

        # 2. Betclic discovers 'Leeds United - Sheffield Utd'
        bc_item = BetclicDiscoveredItem(
            provider_event_id="bc_leeds_1",
            name="Leeds United - Sheffield Utd",
            competition_name="Anglia - Championship",
            url="https://betclic.pl/leeds-sheff",
            start_time=now_str,
            metadata={"raw": {"total_markets_count": 190}},
        )
        bc_inst = MagicMock()
        bc_inst.discover.return_value = [bc_item]
        bc_inst.run.return_value.parsed_objects = []
        mock_bc_p.return_value = bc_inst

        # 3. StatsHub returns player trends for 'Leeds vs Sheffield United'
        champ_prop = StatsHubPropResult(
            player_stat=StatsHubPlayerStat(
                player_id=601,
                player_name="Patrick Bamford",
                team="Leeds",
                opponent="Sheffield United",
                stat_type="shots",
                line=1.5,
                odds_type="over",
                hit_rate_pct=75.0,
                sample_size=10,
                average=2.2,
                fixture=StatsHubFixture(
                    fixture_id="sh_leeds_1",
                    home_team="Leeds",
                    away_team="Sheffield United",
                    competition="Championship",
                    kickoff=now_str,
                ),
                bookmaker_odds=[
                    StatsHubBookmakerOdds(bookmaker="Bet365", line=1.5, side="over", decimal_odds=1.80)
                ],
            )
        )
        p_inst = MagicMock()
        p_inst.run.return_value.parsed_objects = [champ_prop]
        mock_player_p.return_value = p_inst

        t_inst = MagicMock()
        t_inst.run.return_value.parsed_objects = []
        mock_team_p.return_value = t_inst

        # 4. Polish bookmaker quote for Bamford
        mock_extract_player.return_value = [
            NormalizedExecutionQuote(
                bookmaker="Superbet",
                player="Patrick Bamford",
                fixture="Leeds vs Sheffield United",
                stat_type="SHOTS",
                line=1.5,
                side="OVER",
                odds=2.40,
                active=True,
                event_id="sb_leeds_1",
            )
        ]
        mock_extract_team.return_value = []

        scanner = GlobalPropsScanner()
        scope = GlobalScanScope(props_scope="PLAYER", stat_types=["shots"], min_ev_percent=3.0)
        budget = GlobalScanBudget(max_fixtures=10, max_execution_events=10)

        result = scanner.execute_scan(scope=scope, budget=budget)

        # Assertions:
        # Fixture discovered and merged from both SB and BC (250+190 = 440 market coverage)
        self.assertEqual(result.funnel_metrics.fixtures_discovered, 1)
        self.assertEqual(result.funnel_metrics.fixtures_selected, 1)
        sel_fix = result.selected_fixtures[0]
        sel_cov = sel_fix.get("total_market_coverage") if isinstance(sel_fix, dict) else getattr(sel_fix, "total_market_coverage", None)
        self.assertEqual(sel_cov, 440)

        # Prop evaluated and QUALIFIED
        self.assertEqual(result.funnel_metrics.qualified_count, 1)
        self.assertEqual(len(result.qualified_opportunities), 1)
        self.assertEqual(result.qualified_opportunities[0].player_name, "Patrick Bamford")


if __name__ == "__main__":
    unittest.main()
