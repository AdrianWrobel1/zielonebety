"""
Unit & Integration Tests for Stage 5: Global Player / Team Props Scanner.

Validates:
1. Multi-fixture scanning across configurable scope (time horizon, tournaments, stat types, props scope).
2. Enforcement of global scan budgets (max_fixtures, max_trends_requests, max_execution_events).
3. Canonical deduplication across multiple acquisition paths and foreign bookmaker quotes.
4. Funnel observability telemetry & accurate metric accounting.
5. Separation of QUALIFIED opportunities vs REJECTED diagnostic candidates.
6. Deterministic EV-based ranking (Net EV % DESC + secondary stable tie-breakers).
7. PlatformAPIService and API routes integration.
"""

import unittest
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

from domain.models import Competition, Event
from normalization.base_normalizer import NormalizedGraph
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
    GlobalScanOpportunity,
    GlobalScanResult,
)
from scanner.props_value_evaluator import PropsValueEvaluator, PropsEvaluationConfig
from database.connection import DatabaseManager
from database.config import DatabaseConfig
from api.services import PlatformAPIService
from api.routes import APIRouter


class TestGlobalPropsScanner(unittest.TestCase):

    def setUp(self):
        # In-memory database manager to ensure clean isolation
        self.db_mgr = DatabaseManager(DatabaseConfig.default_sqlite_in_memory())
        self.db_mgr.create_tables()
        self.service = PlatformAPIService(db_manager=self.db_mgr)
        self.router = APIRouter(service=self.service)

        # Clear cached global props before each test
        PlatformAPIService._cached_global_props_results = None

        # Construct synthetic multi-fixture mock trends dataset
        self.mock_player_trends = [
            # Fixture 1: Real Madrid vs Barcelona (2 players)
            StatsHubPropResult(
                player_stat=StatsHubPlayerStat(
                    player_id=101,
                    player_name="Vinicius Junior",
                    team="Real Madrid",
                    opponent="Barcelona",
                    stat_type="shots_on_target",
                    line=1.5,
                    odds_type="over",
                    hit_rate_pct=80.0,
                    sample_size=10,
                    average=2.2,
                    fixture=StatsHubFixture(fixture_id="fix-100", home_team="Real Madrid", away_team="Barcelona", competition="La Liga", kickoff="2026-09-01T20:00:00Z"),
                    bookmaker_odds=[
                        StatsHubBookmakerOdds(bookmaker="Bet365", line=1.5, side="over", decimal_odds=1.85),
                        StatsHubBookmakerOdds(bookmaker="Unibet", line=1.5, side="over", decimal_odds=1.90),
                    ],
                )
            ),
            StatsHubPropResult(
                player_stat=StatsHubPlayerStat(
                    player_id=102,
                    player_name="Robert Lewandowski",
                    team="Barcelona",
                    opponent="Real Madrid",
                    stat_type="shots",
                    line=2.5,
                    odds_type="over",
                    hit_rate_pct=70.0,
                    sample_size=10,
                    average=3.4,
                    fixture=StatsHubFixture(fixture_id="fix-100", home_team="Real Madrid", away_team="Barcelona", competition="La Liga", kickoff="2026-09-01T20:00:00Z"),
                    bookmaker_odds=[
                        StatsHubBookmakerOdds(bookmaker="Bet365", line=2.5, side="over", decimal_odds=1.60),
                    ],
                )
            ),
            # Fixture 2: Arsenal vs Chelsea (1 player)
            StatsHubPropResult(
                player_stat=StatsHubPlayerStat(
                    player_id=201,
                    player_name="Bukayo Saka",
                    team="Arsenal",
                    opponent="Chelsea",
                    stat_type="fouls",
                    line=1.5,
                    odds_type="over",
                    hit_rate_pct=60.0,
                    sample_size=10,
                    average=1.8,
                    fixture=StatsHubFixture(fixture_id="fix-200", home_team="Arsenal", away_team="Chelsea", competition="Premier League", kickoff="2026-09-02T19:00:00Z"),
                    bookmaker_odds=[
                        StatsHubBookmakerOdds(bookmaker="Bet365", line=1.5, side="over", decimal_odds=1.75),
                    ],
                )
            ),
            # Duplicate Player Trend Record (Should be deduplicated)
            StatsHubPropResult(
                player_stat=StatsHubPlayerStat(
                    player_id=101,
                    player_name="Vinicius Junior",
                    team="Real Madrid",
                    opponent="Barcelona",
                    stat_type="shots_on_target",
                    line=1.5,
                    odds_type="over",
                    hit_rate_pct=80.0,
                    sample_size=10,
                    average=2.2,
                    fixture=StatsHubFixture(fixture_id="fix-100", home_team="Real Madrid", away_team="Barcelona", competition="La Liga", kickoff="2026-09-01T20:00:00Z"),
                    bookmaker_odds=[
                        StatsHubBookmakerOdds(bookmaker="Bet365", line=1.5, side="over", decimal_odds=1.85),
                    ],
                )
            ),
        ]

        self.mock_team_trends = [
            # Fixture 1: Real Madrid vs Barcelona (Team Shots)
            StatsHubTeamPropResult(
                team_stat=StatsHubTeamStat(
                    team_id=1001,
                    team_name="Real Madrid",
                    opponent_name="Barcelona",
                    stat_type="shots",
                    line=14.5,
                    odds_type="over",
                    participant_role="HOME",
                    hit_rate_pct=75.0,
                    sample_size=12,
                    average=16.1,
                    fixture=StatsHubTeamFixture(fixture_id="fix-100", home_team="Real Madrid", away_team="Barcelona", competition="La Liga", kickoff="2026-09-01T20:00:00Z"),
                    bookmaker_odds=[
                        StatsHubTeamBookmakerOdds(bookmaker="Bet365", line=14.5, side="over", decimal_odds=1.70),
                        StatsHubTeamBookmakerOdds(bookmaker="Paddy Power", line=14.5, side="over", decimal_odds=1.75),
                    ],
                )
            ),
            # Fixture 3: Liverpool vs Man City (Team Corners)
            StatsHubTeamPropResult(
                team_stat=StatsHubTeamStat(
                    team_id=3001,
                    team_name="Liverpool",
                    opponent_name="Manchester City",
                    stat_type="corners",
                    line=5.5,
                    odds_type="over",
                    participant_role="HOME",
                    hit_rate_pct=80.0,
                    sample_size=10,
                    average=6.8,
                    fixture=StatsHubTeamFixture(fixture_id="fix-300", home_team="Liverpool", away_team="Manchester City", competition="Premier League", kickoff="2026-09-03T16:30:00Z"),
                    bookmaker_odds=[
                        StatsHubTeamBookmakerOdds(bookmaker="Bet365", line=5.5, side="over", decimal_odds=1.80),
                    ],
                )
            ),
        ]

        # Execution Quotes (Superbet & Betclic)
        self.mock_exec_quotes = [
            # Vinicius Junior @ Superbet: 2.40 (12% tax -> 2.112, P_fair ~ 0.52 -> Net EV ~ +9.8% -> QUALIFIED)
            NormalizedExecutionQuote(
                bookmaker="Superbet",
                player="Vinicius Junior",
                fixture="Real Madrid vs Barcelona",
                stat_type="SHOTS_ON_TARGET",
                line=1.5,
                side="OVER",
                odds=2.40,
                active=True,
                event_id="sb-100",
            ),
            # Lewandowski @ Superbet: 1.70 (12% tax -> 1.496, P_fair ~ 0.60 -> Net EV ~ -10.2% -> BELOW_VALUE_THRESHOLD)
            NormalizedExecutionQuote(
                bookmaker="Superbet",
                player="Robert Lewandowski",
                fixture="Real Madrid vs Barcelona",
                stat_type="SHOTS",
                line=2.5,
                side="OVER",
                odds=1.70,
                active=True,
                event_id="sb-100",
            ),
            # Liverpool Corners @ Betclic: 2.15 (0% tax -> 2.15, P_fair ~ 0.53 -> Net EV ~ +13.9% -> QUALIFIED)
            NormalizedExecutionQuote(
                bookmaker="Betclic",
                team="Liverpool",
                fixture="Liverpool vs Manchester City",
                stat_type="CORNERS",
                line=5.5,
                side="OVER",
                odds=2.15,
                active=True,
                scope="TEAM",
                participant_role="HOME",
                event_id="bc-300",
            ),

        ]

        # Normalized Graph Mock for cached events
        self.mock_graph = NormalizedGraph(
            competition=Competition(name="La Liga", sport="football"),
            event=Event(
                competition_id="comp-1",
                home_participant="Real Madrid",
                away_participant="Barcelona",
                scheduled_start="2026-09-01T20:00:00Z",
                provider_ids={"superbet": "sb-100"},
            ),
            markets=[],
            selections=[],
            odds_list=[],
        )


    @patch("scanner.global_props_scanner.SuperbetProvider")
    @patch("scanner.global_props_scanner.BetclicProvider")
    @patch("scanner.global_props_scanner.StatsHubProvider")
    @patch("scanner.global_props_scanner.StatsHubTeamPropsProvider")
    @patch("scanner.global_props_scanner.ExecutionMarketEngine.extract_team_quotes_from_graphs")
    @patch("scanner.global_props_scanner.ExecutionMarketEngine.extract_quotes_from_graphs")
    def test_global_scan_multi_fixture_execution(self, mock_extract_player, mock_extract_team, mock_team_provider, mock_player_provider, mock_bc_p, mock_sb_p):
        """End-to-end global scan across Player Props and Team Props with ranking and funnel telemetry."""
        # Setup mock providers
        mock_p_inst = MagicMock()
        mock_p_inst.run.return_value.parsed_objects = self.mock_player_trends
        mock_player_provider.return_value = mock_p_inst

        mock_t_inst = MagicMock()
        mock_t_inst.run.return_value.parsed_objects = self.mock_team_trends
        mock_team_provider.return_value = mock_t_inst

        mock_extract_player.return_value = [q for q in self.mock_exec_quotes if q.scope != "TEAM"]
        mock_extract_team.return_value = [q for q in self.mock_exec_quotes if q.scope == "TEAM"]

        scanner = GlobalPropsScanner(cached_execution_events=[self.mock_graph])
        scope = GlobalScanScope(
            props_scope="ALL",
            stat_types=["shots"],
            min_ev_percent=3.0,
            max_results=50,
        )
        budget = GlobalScanBudget(max_fixtures=10)

        result = scanner.execute_scan(scope=scope, budget=budget)

        self.assertEqual(result.status, "SUCCESS")
        funnel = result.funnel_metrics

        # Funnel metrics assertions
        self.assertEqual(funnel.fixtures_discovered, 3)  # fix-100, fix-200, fix-300
        self.assertEqual(funnel.fixtures_selected, 3)
        self.assertEqual(funnel.trends_discovered, 6)  # 4 player + 2 team
        self.assertEqual(funnel.trends_deduplicated, 5)  # 1 duplicate Vinicius trend removed

        # Qualified Opportunities
        self.assertGreaterEqual(len(result.qualified_opportunities), 1)

        # Vinicius Junior (Player Prop) and Liverpool Corners (Team Prop) must be QUALIFIED
        self.assertTrue(any(o.player_name == "Vinicius Junior" for o in result.qualified_opportunities))
        self.assertTrue(any(o.team == "Liverpool" for o in result.qualified_opportunities))

        # Deterministic Ranking Check: Net EV % DESC
        for i in range(len(result.qualified_opportunities) - 1):
            cur_ev = result.qualified_opportunities[i].net_ev_pct
            next_ev = result.qualified_opportunities[i + 1].net_ev_pct
            self.assertGreaterEqual(cur_ev, next_ev)

        # Diagnostic candidates check
        self.assertTrue(any(d.player_name == "Robert Lewandowski" for d in result.diagnostic_candidates))
        lew_diag = next(d for d in result.diagnostic_candidates if d.player_name == "Robert Lewandowski")
        self.assertEqual(lew_diag.reason_code, "BELOW_VALUE_THRESHOLD")


    @patch("scanner.global_props_scanner.SuperbetProvider")
    @patch("scanner.global_props_scanner.BetclicProvider")
    @patch("scanner.global_props_scanner.StatsHubProvider")
    @patch("scanner.global_props_scanner.StatsHubTeamPropsProvider")
    @patch("scanner.global_props_scanner.ExecutionMarketEngine.extract_quotes_from_graphs")
    def test_global_scan_scope_filtering_player_only(self, mock_extract, mock_team_provider, mock_player_provider, mock_bc_p, mock_sb_p):
        """Scope props_scope='PLAYER' processes exclusively player props."""
        mock_p_inst = MagicMock()
        mock_p_inst.run.return_value.parsed_objects = self.mock_player_trends
        mock_player_provider.return_value = mock_p_inst

        mock_extract.return_value = [q for q in self.mock_exec_quotes if q.scope != "TEAM"]

        scanner = GlobalPropsScanner(cached_execution_events=[self.mock_graph])
        scope = GlobalScanScope(props_scope="PLAYER")
        result = scanner.execute_scan(scope=scope)

        # Team provider must NOT be executed
        mock_team_provider.assert_not_called()
        self.assertTrue(all(o.prop_type == "PLAYER" for o in result.qualified_opportunities))

    @patch("scanner.global_props_scanner.SuperbetProvider")
    @patch("scanner.global_props_scanner.BetclicProvider")
    @patch("scanner.global_props_scanner.StatsHubProvider")
    @patch("scanner.global_props_scanner.StatsHubTeamPropsProvider")
    @patch("scanner.global_props_scanner.ExecutionMarketEngine.extract_team_quotes_from_graphs")
    def test_global_scan_scope_filtering_team_only(self, mock_extract_team, mock_team_provider, mock_player_provider, mock_bc_p, mock_sb_p):
        """Scope props_scope='TEAM' processes exclusively team props."""
        mock_t_inst = MagicMock()
        mock_t_inst.run.return_value.parsed_objects = self.mock_team_trends
        mock_team_provider.return_value = mock_t_inst

        mock_extract_team.return_value = [q for q in self.mock_exec_quotes if q.scope == "TEAM"]

        scanner = GlobalPropsScanner(cached_execution_events=[self.mock_graph])
        scope = GlobalScanScope(props_scope="TEAM")
        result = scanner.execute_scan(scope=scope)

        # Player provider must NOT be executed
        mock_player_provider.assert_not_called()
        self.assertTrue(all(o.prop_type == "TEAM" for o in result.qualified_opportunities))

    @patch("scanner.global_props_scanner.SuperbetProvider")
    @patch("scanner.global_props_scanner.BetclicProvider")
    @patch("scanner.global_props_scanner.StatsHubProvider")
    @patch("scanner.global_props_scanner.StatsHubTeamPropsProvider")
    @patch("scanner.global_props_scanner.ExecutionMarketEngine.extract_quotes_from_graphs")
    def test_global_scan_budget_limits_fixtures_ceiling(self, mock_extract, mock_team_provider, mock_player_provider, mock_bc_p, mock_sb_p):
        """Budget max_fixtures=1 constrains scan to exactly 1 fixture."""
        mock_p_inst = MagicMock()
        mock_p_inst.run.return_value.parsed_objects = self.mock_player_trends
        mock_player_provider.return_value = mock_p_inst

        mock_t_inst = MagicMock()
        mock_t_inst.run.return_value.parsed_objects = self.mock_team_trends
        mock_team_provider.return_value = mock_t_inst

        mock_extract.return_value = self.mock_exec_quotes

        scanner = GlobalPropsScanner(cached_execution_events=[self.mock_graph])
        budget = GlobalScanBudget(max_fixtures=1)
        result = scanner.execute_scan(budget=budget)

        self.assertEqual(result.funnel_metrics.fixtures_selected, 1)

    @patch("scanner.global_props_scanner.SuperbetProvider")
    @patch("scanner.global_props_scanner.BetclicProvider")
    @patch("scanner.global_props_scanner.StatsHubProvider")
    @patch("scanner.global_props_scanner.StatsHubTeamPropsProvider")
    def test_global_scan_budget_max_trends_requests_strict_ceiling(self, mock_team_provider, mock_player_provider, mock_bc_p, mock_sb_p):
        """Budget max_trends_requests=2 limits total StatsHub requests to exactly 2 across player and team."""
        mock_p_inst = MagicMock()
        mock_p_inst.run.return_value.parsed_objects = []
        mock_player_provider.return_value = mock_p_inst

        mock_t_inst = MagicMock()
        mock_t_inst.run.return_value.parsed_objects = []
        mock_team_provider.return_value = mock_t_inst

        scanner = GlobalPropsScanner()
        scope = GlobalScanScope(
            props_scope="ALL",
            stat_types=["shots", "fouls", "corners", "cards"],
        )
        budget = GlobalScanBudget(max_trends_requests=2)
        scanner.execute_scan(scope=scope, budget=budget)

        total_requests = mock_player_provider.call_count + mock_team_provider.call_count
        self.assertEqual(total_requests, 2)

    def test_platform_api_service_global_props_scan_and_filter(self):
        """Validates PlatformAPIService.scan_global_props and get_global_props_results."""
        service = self.service

        # Mock scanner within service
        with patch("scanner.global_props_scanner.GlobalPropsScanner.execute_scan") as mock_exec:
            mock_funnel = GlobalScanFunnelMetrics(fixtures_discovered=5, fixtures_selected=5, qualified_count=2)
            mock_opp1 = GlobalScanOpportunity(
                canonical_prop_key="prop:1",
                prop_type="PLAYER",
                player_name="Vinicius Junior",
                team="Real Madrid",
                opponent="Barcelona",
                match_name="Real Madrid vs Barcelona",
                fixture_id="fix-1",
                competition="La Liga",
                kickoff="2026-09-01T20:00:00Z",
                stat_type="SHOTS_ON_TARGET",
                line=1.5,
                side="OVER",
                period="FULL_TIME",
                scope="PLAYER",
                participant_role=None,
                reference_consensus_odds=1.85,
                reference_fair_probability=0.52,
                reference_fair_odds=1.92,
                reference_sources_count=2,
                reference_odds=[],
                best_bookmaker="Superbet",
                best_raw_odds=2.40,
                best_effective_odds=2.112,
                net_ev_pct=9.8,
                gross_ev_pct=24.8,
                value_edge_pp=4.6,
                is_valuebet=True,
                status="QUALIFIED",
                reason_code="QUALIFIED",
                reason=None,
                trend_hits=8,
                trend_window=10,
                hit_rate_pct=80.0,
                stat_average=2.2,
                last_5_avg=2.4,
                last_10_avg=2.2,
                execution_odds={},
                provenance={},
            )
            mock_opp2 = GlobalScanOpportunity(
                canonical_prop_key="team_prop:2",
                prop_type="TEAM",
                player_name=None,
                team="Liverpool",
                opponent="Manchester City",
                match_name="Liverpool vs Manchester City",
                fixture_id="fix-3",
                competition="Premier League",
                kickoff="2026-09-03T16:30:00Z",
                stat_type="CORNERS",
                line=5.5,
                side="OVER",
                period="FULL_TIME",
                scope="TEAM",
                participant_role="HOME",
                reference_consensus_odds=1.80,
                reference_fair_probability=0.53,
                reference_fair_odds=1.88,
                reference_sources_count=1,
                reference_odds=[],
                best_bookmaker="Betclic",
                best_raw_odds=2.15,
                best_effective_odds=2.15,
                net_ev_pct=13.9,
                gross_ev_pct=13.9,
                value_edge_pp=6.5,
                is_valuebet=True,
                status="QUALIFIED",
                reason_code="QUALIFIED",
                reason=None,
                trend_hits=8,
                trend_window=10,
                hit_rate_pct=80.0,
                stat_average=6.8,
                last_5_avg=7.0,
                last_10_avg=6.8,
                execution_odds={},
                provenance={},
            )
            mock_exec.return_value = GlobalScanResult(
                scope={"props_scope": "ALL"},
                budget={},
                status="SUCCESS",
                funnel_metrics=mock_funnel,
                qualified_opportunities=[mock_opp2, mock_opp1],
                diagnostic_candidates=[],
                duration_ms=120.0,
            )

            # 1. Scan Trigger
            scan_res = service.scan_global_props({"props_scope": "ALL", "min_ev_percent": 3.0})
            self.assertEqual(scan_res["status"], "SUCCESS")
            self.assertEqual(len(scan_res["qualified_opportunities"]), 2)

            # 2. Filter by search "vinicius"
            filtered = service.get_global_props_results(search="vinicius")
            self.assertEqual(len(filtered["qualified_opportunities"]), 1)
            self.assertEqual(filtered["qualified_opportunities"][0]["player_name"], "Vinicius Junior")

            # 3. Filter by props_scope "TEAM"
            team_filtered = service.get_global_props_results(props_scope="TEAM")
            self.assertEqual(len(team_filtered["qualified_opportunities"]), 1)
            self.assertEqual(team_filtered["qualified_opportunities"][0]["team"], "Liverpool")

    def test_api_router_global_props_endpoints(self):
        """Validates APIRouter HTTP endpoint wrappers for global props scanning."""
        service = self.service
        router = self.router

        with patch.object(service, "scan_global_props") as mock_scan, \
             patch.object(service, "get_global_props_results") as mock_get:

            mock_scan.return_value = {
                "status": "SUCCESS",
                "qualified_count": 3,
                "qualified_opportunities": [],
            }
            mock_get.return_value = {
                "status": "SUCCESS",
                "total_qualified_matching_filter": 3,
                "qualified_opportunities": [],
            }

            # POST /api/v1/props/global-scan
            post_res = router.handle_post_global_props_scan({"props_scope": "ALL", "min_ev_percent": 3.0})
            self.assertEqual(post_res.status_code, 200)
            self.assertEqual(post_res.data["status"], "SUCCESS")
            mock_scan.assert_called_once_with(scope_params={"props_scope": "ALL", "min_ev_percent": 3.0})

            # GET /api/v1/props/global-results
            get_res = router.handle_get_global_props_results(props_scope="PLAYER", min_net_ev=5.0)
            self.assertEqual(get_res.status_code, 200)
            self.assertEqual(get_res.data["total_qualified_matching_filter"], 3)
            mock_get.assert_called_once_with(
                props_scope="PLAYER",
                stat=None,
                search=None,
                min_net_ev=5.0,
                limit=50,
                offset=0,
            )

    def test_get_global_props_results_empty_cache_returns_not_run(self):
        """Validates that get_global_props_results returns status='NOT_RUN' when no scan has been executed yet."""
        PlatformAPIService._cached_global_props_results = None
        service = self.service
        with patch.object(service, "scan_global_props") as mock_scan:
            res = service.get_global_props_results()
            mock_scan.assert_not_called()
            self.assertEqual(res["status"], "NOT_RUN")
            self.assertEqual(res["qualified_count"], 0)
            self.assertEqual(res["qualified_opportunities"], [])
            self.assertEqual(res["funnel_metrics"]["match_uncertain"], 0)

    def test_global_scan_funnel_match_uncertain_telemetry(self):
        """Validates that GlobalScanFunnelMetrics correctly serializes match_uncertain without conflating with rejected_count."""
        # Case 1: Pure rejection without ambiguity
        funnel_clean = GlobalScanFunnelMetrics(
            fixtures_discovered=23,
            fixtures_selected=3,
            trends_discovered=523,
            trends_deduplicated=71,
            matched_props=0,
            match_uncertain=0,
            rejected_count=71,
            unavailable_polish_odds=19,
        )
        funnel_clean.increment_rejection("INSUFFICIENT_REFERENCE_SOURCES")
        funnel_clean.increment_rejection("POLISH_ODDS_UNAVAILABLE")
        d_clean = funnel_clean.to_dict()
        self.assertEqual(d_clean["match_uncertain"], 0)
        self.assertEqual(d_clean["rejected_count"], 71)
        self.assertIn("INSUFFICIENT_REFERENCE_SOURCES", d_clean["rejection_breakdown"])
        self.assertIn("POLISH_ODDS_UNAVAILABLE", d_clean["rejection_breakdown"])

        # Case 2: Genuine ambiguous matching
        funnel_ambiguous = GlobalScanFunnelMetrics(
            fixtures_discovered=10,
            fixtures_selected=3,
            trends_discovered=100,
            trends_deduplicated=50,
            matched_props=10,
            match_uncertain=5,
            rejected_count=40,
        )
        funnel_ambiguous.increment_rejection("EVENT_AMBIGUOUS")
        d_ambiguous = funnel_ambiguous.to_dict()
        self.assertEqual(d_ambiguous["match_uncertain"], 5)
        self.assertEqual(d_ambiguous["rejected_count"], 40)
        self.assertNotEqual(d_ambiguous["match_uncertain"], d_ambiguous["rejected_count"])

    def test_taxonomy_and_coverage_api_endpoints(self):
        """Validates that APIRouter exposes /api/v1/props/taxonomy and /api/v1/props/coverage."""
        router = APIRouter(service=self.service)

        tax_res = router.handle_get_props_taxonomy()
        self.assertEqual(tax_res.status_code, 200)
        self.assertIn("groups", tax_res.data)
        self.assertEqual(len(tax_res.data["groups"]), 2)

        cov_res = router.handle_get_props_coverage()
        self.assertEqual(cov_res.status_code, 200)
        self.assertEqual(cov_res.data["total_categories"], 15)
        self.assertEqual(cov_res.data["player_categories_count"], 8)
        self.assertEqual(cov_res.data["team_categories_count"], 7)

    def test_get_global_props_results_scope_prefixed_filtering(self):
        """Validates stat filtering with scope prefixes (e.g. player_shots vs team_shots)."""
        PlatformAPIService._cached_global_props_results = {
            "status": "SUCCESS",
            "scope": {},
            "budget": {},
            "funnel_metrics": {},
            "qualified_count": 2,
            "diagnostic_count": 0,
            "qualified_opportunities": [
                {
                    "canonical_prop_key": "player_shots_key",
                    "prop_type": "PLAYER",
                    "stat_type": "SHOTS",
                    "player_name": "Lewandowski",
                    "team": "Barcelona",
                    "is_valuebet": True,
                    "status": "QUALIFIED",
                    "reason_code": "QUALIFIED",
                },
                {
                    "canonical_prop_key": "team_shots_key",
                    "prop_type": "TEAM",
                    "stat_type": "SHOTS",
                    "player_name": None,
                    "team": "Barcelona",
                    "is_valuebet": True,
                    "status": "QUALIFIED",
                    "reason_code": "QUALIFIED",
                },
            ],
            "diagnostic_candidates": [],
            "items": [],
            "all_candidates": [],
            "total_qualified_matching_filter": 2,
            "total_items_matching_filter": 2,
            "limit": 50,
            "offset": 0,
        }

        # Filter by player_shots
        res_player = self.service.get_global_props_results(stat="player_shots")
        self.assertEqual(len(res_player["items"]), 1)
        self.assertEqual(res_player["items"][0]["prop_type"], "PLAYER")
        self.assertEqual(res_player["items"][0]["player_name"], "Lewandowski")

        # Filter by team_shots
        res_team = self.service.get_global_props_results(stat="team_shots")
        self.assertEqual(len(res_team["items"]), 1)
        self.assertEqual(res_team["items"][0]["prop_type"], "TEAM")
        self.assertEqual(res_team["items"][0]["team"], "Barcelona")

        # Filter by raw shots with props_scope='PLAYER'
        res_scope_player = self.service.get_global_props_results(props_scope="PLAYER", stat="shots")
        self.assertEqual(len(res_scope_player["items"]), 1)
        self.assertEqual(res_scope_player["items"][0]["prop_type"], "PLAYER")


if __name__ == "__main__":
    unittest.main()

