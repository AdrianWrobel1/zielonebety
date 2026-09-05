"""
Comprehensive TDD Test Suite for Global Props Scanner Dataflow & Budget Enforcement.

Tests:
TEST A — GET GLOBAL RESULTS (APIRouter + service routing contract)
TEST B — SCANNER -> POST (POST /api/v1/props/global-scan with qualified opportunity)
TEST C — POST -> CACHE -> GET (Opportunity preserved exactly between POST and GET)
TEST D — GET -> FRONTEND (Contract test for UI payload and rendering logic)
TEST E — MIN NET EV (+3.24% qualified vs +2.83% rejected at min_net_ev=3.0)
TEST F — EMPTY STATE (GET without cache -> NOT_RUN, 0 scanned, no provider calls)
TEST G — HARD EXECUTION BUDGET (max_execution_events=2 limits fetch/parse/validate to <= 2)
TEST H — GLOBALNY BUDŻET PROVIDERÓW (Superbet + Betclic combined fetch count <= max_execution_events)
TEST I — MAX FIXTURES (max_fixtures=3 limits selected fixtures, downstream ignores non-selected)
TEST J — STATSHUB SCOPING (Props outside selected fixtures rejected before matching/evaluation; telemetry separated)
TEST K — MAX TRENDS REQUESTS (max_trends_requests=2 limits StatsHub requests)
TEST L — SERVICES SCAN PROPS BOUNDED LIFECYCLE (Superbet & Betclic do not pass all 926 discovered events to fetch in services.scan_props)
"""

import json
import unittest
from decimal import Decimal
from unittest.mock import MagicMock, patch, call

from api.routes import APIRouter
from api.services import PlatformAPIService
from database.config import DatabaseConfig
from database.connection import DatabaseManager
from domain.models import Competition, Event
from normalization.base_normalizer import NormalizedGraph
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
    GlobalScanOpportunity,
    GlobalScanResult,
    GlobalScanFunnelMetrics,
)


class TestGlobalPropsDataflowAndBudgets(unittest.TestCase):

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

    # ----------------------------------------------------------------------
    # TEST A — GET GLOBAL RESULTS
    # ----------------------------------------------------------------------
    def test_a_get_global_results_routing_exact(self):
        """GET /api/v1/props/global-results resolves to handle_get_global_props_results, NOT handle_get_prop_detail."""
        # 1. Verify that handle_get_prop_detail for "global-results" produces 404 (the broken symptom)
        detail_res = self.router.handle_get_prop_detail("global-results")
        self.assertEqual(detail_res.status_code, 404)
        self.assertIn("not found", detail_res.errors[0].lower())

        # 2. Verify that handle_get_global_props_results returns 200 with proper contract
        global_res = self.router.handle_get_global_props_results(limit=50, offset=0, min_net_ev=3.0)
        self.assertEqual(global_res.status_code, 200)
        self.assertIsNotNone(global_res.data)
        self.assertEqual(global_res.data.get("status"), "NOT_RUN")
        self.assertEqual(global_res.data.get("qualified_count"), 0)
        self.assertEqual(global_res.data.get("qualified_opportunities"), [])

    # ----------------------------------------------------------------------
    # TEST B — SCANNER -> POST
    # ----------------------------------------------------------------------
    def test_b_scanner_to_post_returns_qualified_opportunity(self):
        """POST /api/v1/props/global-scan returns deterministic qualified opportunity."""
        mock_opp = {
            "canonical_prop_key": "prop:vinicius:shots_on_target:1.5:OVER",
            "prop_type": "PLAYER",
            "player_name": "Vinicius Junior",
            "team": "Real Madrid",
            "opponent": "Barcelona",
            "match_name": "Real Madrid vs Barcelona",
            "fixture_id": "fix-100",
            "competition": "La Liga",
            "kickoff": "2026-09-01T20:00:00Z",
            "stat_type": "SHOTS_ON_TARGET",
            "line": 1.5,
            "side": "OVER",
            "period": "FULL_TIME",
            "scope": "PLAYER",
            "participant_role": None,
            "reference_consensus_odds": 1.85,
            "reference_fair_probability": 0.5405,
            "reference_fair_odds": 1.85,
            "reference_sources_count": 1,
            "reference_odds": [{"bookmaker": "Bet365", "line": 1.5, "side": "over", "odds": 1.85}],
            "best_bookmaker": "Superbet",
            "best_raw_odds": 2.40,
            "best_effective_odds": 2.112,
            "net_ev_pct": 14.16,
            "gross_ev_pct": 29.73,
            "value_edge_pp": 12.5,
            "is_valuebet": True,
            "status": "QUALIFIED",
            "reason_code": "VALUEBET_FOUND",
            "reason": "Net EV +14.16% exceeds threshold 3.0%",
            "trend_hits": 8,
            "trend_window": 10,
            "hit_rate_pct": 80.0,
            "stat_average": 2.2,
            "last_5_avg": 2.4,
            "last_10_avg": 2.2,
            "execution_odds": {"Superbet": {"raw_odds": 2.40, "effective_odds": 2.112}},
            "provenance": {"calculation_method": "multi_source_consensus"},
        }

        mock_scan_res = {
            "scope": {"props_scope": "ALL", "min_ev_percent": 3.0},
            "budget": {"max_fixtures": 30, "max_execution_events": 20},
            "status": "SUCCESS",
            "funnel_metrics": {
                "fixtures_discovered": 1,
                "fixtures_selected": 1,
                "trends_discovered": 1,
                "trends_deduplicated": 1,
                "matched_props": 1,
                "evaluated_count": 1,
                "qualified_count": 1,
                "rejected_count": 0,
                "rejection_breakdown": {},
                "execution_time_ms": 50.0,
            },
            "qualified_count": 1,
            "diagnostic_count": 0,
            "selected_fixtures": [{"home_team": "Real Madrid", "away_team": "Barcelona"}],
            "qualified_opportunities": [mock_opp],
            "diagnostic_candidates": [],
            "duration_ms": 50.0,
            "scanned_at": "2026-08-31T20:00:00Z",
        }

        with patch.object(PlatformAPIService, "scan_global_props", return_value=mock_scan_res):
            res = self.router.handle_post_global_props_scan({"props_scope": "ALL", "min_ev_percent": 3.0})
            self.assertEqual(res.status_code, 200)
            data = res.data
            self.assertEqual(data.get("qualified_count"), 1)
            opps = data.get("qualified_opportunities", [])
            self.assertEqual(len(opps), 1)
            opp = opps[0]
            self.assertEqual(opp["player_name"], "Vinicius Junior")
            self.assertEqual(opp["team"], "Real Madrid")
            self.assertEqual(opp["best_bookmaker"], "Superbet")
            self.assertEqual(opp["best_raw_odds"], 2.40)
            self.assertGreaterEqual(opp["net_ev_pct"], 3.0)

    # ----------------------------------------------------------------------
    # TEST C — POST -> CACHE -> GET
    # ----------------------------------------------------------------------
    def test_c_post_cache_get_contract_preservation(self):
        """Qualified opportunity from POST is stored in cache and returned verbatim on GET."""
        mock_opp = {
            "canonical_prop_key": "prop:vinicius:shots_on_target:1.5:OVER",
            "prop_type": "PLAYER",
            "player_name": "Vinicius Junior",
            "team": "Real Madrid",
            "opponent": "Barcelona",
            "match_name": "Real Madrid vs Barcelona",
            "fixture_id": "fix-100",
            "competition": "La Liga",
            "kickoff": "2026-09-01T20:00:00Z",
            "stat_type": "SHOTS_ON_TARGET",
            "line": 1.5,
            "side": "OVER",
            "net_ev_pct": 8.5,
            "best_bookmaker": "Superbet",
            "best_raw_odds": 2.40,
            "status": "QUALIFIED",
        }

        cached_payload = {
            "scope": {"props_scope": "ALL"},
            "budget": {},
            "status": "SUCCESS",
            "funnel_metrics": {"qualified_count": 1, "evaluated_count": 1},
            "qualified_count": 1,
            "qualified_opportunities": [mock_opp],
            "diagnostic_candidates": [],
            "duration_ms": 40.0,
            "scanned_at": "2026-08-31T20:00:00Z",
        }
        PlatformAPIService._cached_global_props_results = cached_payload

        res = self.router.handle_get_global_props_results(limit=50, offset=0, min_net_ev=3.0)
        self.assertEqual(res.status_code, 200)
        data = res.data
        self.assertEqual(data.get("status"), "SUCCESS")
        self.assertEqual(len(data.get("qualified_opportunities", [])), 1)
        returned_opp = data["qualified_opportunities"][0]
        self.assertEqual(returned_opp["canonical_prop_key"], mock_opp["canonical_prop_key"])
        self.assertEqual(returned_opp["player_name"], mock_opp["player_name"])
        self.assertEqual(returned_opp["best_bookmaker"], mock_opp["best_bookmaker"])
        self.assertEqual(returned_opp["net_ev_pct"], mock_opp["net_ev_pct"])

    # ----------------------------------------------------------------------
    # TEST C2 — DETERMINISTIC QUALIFIED EVALUATOR DIRECT
    # ----------------------------------------------------------------------
    def test_c2_deterministic_evaluator_returns_qualified(self):
        """PropsValueEvaluator produces QUALIFIED opportunity with net_ev_pct >= min_ev_percent without math tampering."""
        from scanner.prop_execution_matcher import PropOddsComparison, ExecutionBookmakerOdds
        from scanner.props_value_evaluator import PropsValueEvaluator, PropsEvaluationConfig

        fix = StatsHubFixture(fixture_id="f-100", home_team="Real Madrid", away_team="Barcelona", competition="La Liga", kickoff="2026-09-01T20:00:00Z")
        stat = StatsHubPlayerStat(
            player_id=10,
            player_name="Vinicius Junior",
            team="Real Madrid",
            opponent="Barcelona",
            stat_type="shots_on_target",
            line=1.5,
            odds_type="over",
            hit_rate_pct=80.0,
            sample_size=10,
            fixture=fix,
            bookmaker_odds=[StatsHubBookmakerOdds("Bet365", 1.5, "over", 1.85)],
        )
        prop_res = StatsHubPropResult(player_stat=stat)

        comparison = PropOddsComparison(
            reference_best_odds=1.85,
            reference_best_bookmaker="Bet365",
            reference_odds_list=[{"bookmaker": "Bet365", "line": 1.5, "side": "over", "odds": 1.85}],
            execution_odds={
                "Superbet": ExecutionBookmakerOdds(
                    bookmaker="Superbet",
                    status="AVAILABLE",
                    decimal_odds=2.40,
                    line=1.5,
                    side="OVER",
                )
            },
            best_executable_odds=2.40,
            best_executable_bookmaker="Superbet",
            execution_status="BETTABLE",
            canonical_prop_key="prop:vinicius:shots_on_target:1.5:OVER",
        )

        evaluator = PropsValueEvaluator(config=PropsEvaluationConfig(min_value_percent=Decimal("3.0")))
        eval_res = evaluator.evaluate_player_prop(prop_res, comparison)

        self.assertEqual(eval_res.overall_status, "QUALIFIED")
        self.assertTrue(eval_res.is_valuebet)
        self.assertEqual(eval_res.best_bookmaker, "Superbet")
        self.assertEqual(eval_res.best_raw_odds, 2.40)
        # Net EV is approx +18.73%, well above +3.0% threshold
        self.assertGreater(eval_res.best_net_ev_pct, 3.0)

    # ----------------------------------------------------------------------
    # TEST D — GET -> FRONTEND
    # ----------------------------------------------------------------------
    def test_d_frontend_payload_contract(self):
        """Frontend receiving qualified_opportunities must receive all keys required for rendering."""
        sample_payload = {
            "status": "SUCCESS",
            "qualified_count": 1,
            "qualified_opportunities": [
                {
                    "canonical_prop_key": "prop:mbappe:shots:2.5:OVER",
                    "prop_type": "PLAYER",
                    "player_name": "Kylian Mbappe",
                    "team": "Real Madrid",
                    "opponent": "Barcelona",
                    "match_name": "Real Madrid vs Barcelona",
                    "fixture_id": "fix-100",
                    "competition": "La Liga",
                    "kickoff": "2026-09-01T20:00:00Z",
                    "stat_type": "SHOTS",
                    "line": 2.5,
                    "side": "OVER",
                    "net_ev_pct": 5.2,
                    "best_bookmaker": "Superbet",
                    "best_raw_odds": 2.10,
                    "is_valuebet": True,
                    "status": "QUALIFIED",
                }
            ],
            "funnel_metrics": {
                "fixtures_discovered": 1,
                "fixtures_selected": 1,
                "trends_discovered": 1,
                "trends_deduplicated": 1,
                "matched_props": 1,
                "evaluated_count": 1,
                "qualified_count": 1,
                "rejected_count": 0,
            },
        }

        PlatformAPIService._cached_global_props_results = sample_payload
        res = self.service.get_global_props_results(min_net_ev=3.0)
        items = res["qualified_opportunities"]
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertIn("canonical_prop_key", item)
        self.assertIn("prop_type", item)
        self.assertIn("player_name", item)
        self.assertIn("team", item)
        self.assertIn("stat_type", item)
        self.assertIn("line", item)
        self.assertIn("side", item)
        self.assertIn("net_ev_pct", item)
        self.assertIn("best_bookmaker", item)
        self.assertIn("best_raw_odds", item)

    # ----------------------------------------------------------------------
    # TEST E — MIN NET EV
    # ----------------------------------------------------------------------
    def test_e_min_net_ev_threshold_boundary(self):
        """net_ev_pct >= min_net_ev: +3.24% qualified, +2.83% rejected at min_net_ev=3.0."""
        cached_payload = {
            "status": "SUCCESS",
            "qualified_count": 3,
            "qualified_opportunities": [
                {"canonical_prop_key": "p1", "net_ev_pct": 3.24, "player_name": "P1", "stat_type": "SHOTS"},
                {"canonical_prop_key": "p2", "net_ev_pct": 2.83, "player_name": "P2", "stat_type": "SHOTS"},
                {"canonical_prop_key": "p3", "net_ev_pct": -1.26, "player_name": "P3", "stat_type": "SHOTS"},
            ],
            "funnel_metrics": {},
        }
        PlatformAPIService._cached_global_props_results = cached_payload

        res = self.service.get_global_props_results(min_net_ev=3.0)

        opps = res["qualified_opportunities"]
        self.assertEqual(len(opps), 1)
        self.assertEqual(opps[0]["canonical_prop_key"], "p1")
        self.assertEqual(opps[0]["net_ev_pct"], 3.24)

    # ----------------------------------------------------------------------
    # TEST F — EMPTY STATE
    # ----------------------------------------------------------------------
    def test_f_empty_cache_state(self):
        """GET /api/v1/props/global-results without cache returns status=NOT_RUN without running scan."""
        PlatformAPIService._cached_global_props_results = None

        with patch.object(self.service, "scan_global_props") as mock_scan:
            res = self.service.get_global_props_results()
            mock_scan.assert_not_called()
            self.assertEqual(res["status"], "NOT_RUN")
            self.assertEqual(res["qualified_count"], 0)
            self.assertEqual(res["qualified_opportunities"], [])

    # ----------------------------------------------------------------------
    # TEST G — HARD EXECUTION BUDGET
    # ----------------------------------------------------------------------
    @patch("scanner.global_props_scanner.BetclicProvider")
    @patch("scanner.global_props_scanner.SuperbetProvider")
    @patch("scanner.global_props_scanner.StatsHubTeamPropsProvider")
    @patch("scanner.global_props_scanner.StatsHubProvider")
    def test_g_hard_execution_budget_lifecycle_cap(
        self,
        mock_statshub_p_cls,
        mock_statshub_t_cls,
        mock_sb_cls,
        mock_bc_cls,
    ):
        """When max_execution_events=2, total events passed to fetch/parse/validate across providers <= 2."""
        # 1. StatsHub mock
        p_inst = MagicMock()
        fix1 = StatsHubFixture(fixture_id="f1", home_team="TeamA", away_team="TeamB", competition="L1", kickoff="2026-09-01T20:00:00Z")
        stat1 = StatsHubPlayerStat(player_id=1, player_name="Player1", team="TeamA", opponent="TeamB", stat_type="shots", line=1.5, odds_type="over", hit_rate_pct=80.0, sample_size=10, fixture=fix1, bookmaker_odds=[StatsHubBookmakerOdds("Bet365", 1.5, "over", 1.9)])
        p_inst.run.return_value.parsed_objects = [StatsHubPropResult(player_stat=stat1)]
        mock_statshub_p_cls.return_value = p_inst

        t_inst = MagicMock()
        t_inst.run.return_value.parsed_objects = []
        mock_statshub_t_cls.return_value = t_inst

        # 2. Superbet mock discovers 20 events
        sb_inst = MagicMock()
        sb_items = [
            SuperbetDiscoveredItem(event_id=f"sb-{i}", match_name=f"TeamA - TeamB" if i == 1 else f"OtherHome{i} - OtherAway{i}", competition_id="1", competition_name="L1", start_time="2026-09-01T20:00:00Z")
            for i in range(1, 21)
        ]
        sb_inst.discover.return_value = sb_items
        sb_inst.run.return_value.parsed_objects = []
        mock_sb_cls.return_value = sb_inst

        # 3. Betclic mock discovers 20 events
        bc_inst = MagicMock()
        bc_items = [
            BetclicDiscoveredItem(provider_event_id=f"bc-{i}", name=f"TeamA - TeamB" if i == 1 else f"OtherHome{i} - OtherAway{i}", competition_name="L1", url="https://betclic.pl/e", start_time="2026-09-01T20:00:00Z")
            for i in range(1, 21)
        ]
        bc_inst.discover.return_value = bc_items
        bc_inst.run.return_value.parsed_objects = []
        mock_bc_cls.return_value = bc_inst

        scanner = GlobalPropsScanner()
        budget = GlobalScanBudget(max_fixtures=5, max_execution_events=2)
        scanner.execute_scan(budget=budget)

        total_executed_items = 0
        if sb_inst.set_discovered_items.called:
            total_executed_items += len(sb_inst.set_discovered_items.call_args[0][0])
        if bc_inst.set_discovered_items.called:
            total_executed_items += len(bc_inst.set_discovered_items.call_args[0][0])

        self.assertLessEqual(total_executed_items, 2, f"Expected total executed items <= 2, got {total_executed_items}")

    # ----------------------------------------------------------------------
    # TEST H — GLOBALNY BUDŻET PROVIDERÓW
    # ----------------------------------------------------------------------
    @patch("scanner.global_props_scanner.BetclicProvider")
    @patch("scanner.global_props_scanner.SuperbetProvider")
    @patch("scanner.global_props_scanner.StatsHubTeamPropsProvider")
    @patch("scanner.global_props_scanner.StatsHubProvider")
    def test_h_global_budget_shared_between_superbet_and_betclic(
        self,
        mock_statshub_p_cls,
        mock_statshub_t_cls,
        mock_sb_cls,
        mock_bc_cls,
    ):
        """When max_execution_events=3, Superbet + Betclic combined execution items <= 3."""
        p_inst = MagicMock()
        trends = []
        for i in range(1, 6):
            fix = StatsHubFixture(fixture_id=f"f{i}", home_team=f"Home{i}", away_team=f"Away{i}", competition="L1", kickoff="2026-09-01T20:00:00Z")
            stat = StatsHubPlayerStat(player_id=i, player_name=f"P{i}", team=f"Home{i}", opponent=f"Away{i}", stat_type="shots", line=1.5, odds_type="over", hit_rate_pct=80.0, sample_size=10, fixture=fix, bookmaker_odds=[StatsHubBookmakerOdds("Bet365", 1.5, "over", 1.9)])
            trends.append(StatsHubPropResult(player_stat=stat))
        p_inst.run.return_value.parsed_objects = trends
        mock_statshub_p_cls.return_value = p_inst

        t_inst = MagicMock()
        t_inst.run.return_value.parsed_objects = []
        mock_statshub_t_cls.return_value = t_inst

        # Superbet has 5 matching events
        sb_inst = MagicMock()
        sb_items = [
            SuperbetDiscoveredItem(event_id=f"sb-{i}", match_name=f"Home{i} - Away{i}", competition_id="1", competition_name="L1", start_time="2026-09-01T20:00:00Z")
            for i in range(1, 6)
        ]
        sb_inst.discover.return_value = sb_items
        sb_inst.run.return_value.parsed_objects = []
        mock_sb_cls.return_value = sb_inst

        # Betclic has 5 matching events
        bc_inst = MagicMock()
        bc_items = [
            BetclicDiscoveredItem(provider_event_id=f"bc-{i}", name=f"Home{i} - Away{i}", competition_name="L1", url="https://betclic.pl/e", start_time="2026-09-01T20:00:00Z")
            for i in range(1, 6)
        ]
        bc_inst.discover.return_value = bc_items
        bc_inst.run.return_value.parsed_objects = []
        mock_bc_cls.return_value = bc_inst

        scanner = GlobalPropsScanner()
        budget = GlobalScanBudget(max_fixtures=5, max_execution_events=3)
        scanner.execute_scan(budget=budget)

        sb_count = len(sb_inst.set_discovered_items.call_args[0][0]) if sb_inst.set_discovered_items.called else 0
        bc_count = len(bc_inst.set_discovered_items.call_args[0][0]) if bc_inst.set_discovered_items.called else 0

        self.assertLessEqual(sb_count + bc_count, 3, f"Superbet ({sb_count}) + Betclic ({bc_count}) > 3")

    # ----------------------------------------------------------------------
    # TEST I — MAX FIXTURES
    # ----------------------------------------------------------------------
    @patch("scanner.global_props_scanner.BetclicProvider")
    @patch("scanner.global_props_scanner.SuperbetProvider")
    @patch("scanner.global_props_scanner.StatsHubTeamPropsProvider")
    @patch("scanner.global_props_scanner.StatsHubProvider")
    def test_i_max_fixtures_selection_scoping(
        self,
        mock_statshub_p_cls,
        mock_statshub_t_cls,
        mock_sb_cls,
        mock_bc_cls,
    ):
        """When max_fixtures=3 from 10 discovered, exactly 3 are selected."""
        sb_inst = MagicMock()
        sb_inst.discover.return_value = []
        mock_sb_cls.return_value = sb_inst

        bc_inst = MagicMock()
        bc_inst.discover.return_value = []
        mock_bc_cls.return_value = bc_inst

        p_inst = MagicMock()
        trends = []
        for i in range(1, 11):
            fix = StatsHubFixture(fixture_id=f"f{i}", home_team=f"Home{i}", away_team=f"Away{i}", competition="L1", kickoff=f"2026-09-0{min(i,9)}T20:00:00Z")
            stat = StatsHubPlayerStat(player_id=i, player_name=f"P{i}", team=f"Home{i}", opponent=f"Away{i}", stat_type="shots", line=1.5, odds_type="over", hit_rate_pct=80.0, sample_size=10, fixture=fix, bookmaker_odds=[StatsHubBookmakerOdds("Bet365", 1.5, "over", 1.9)])
            trends.append(StatsHubPropResult(player_stat=stat))
        p_inst.run.return_value.parsed_objects = trends
        mock_statshub_p_cls.return_value = p_inst

        t_inst = MagicMock()
        t_inst.run.return_value.parsed_objects = []
        mock_statshub_t_cls.return_value = t_inst

        scanner = GlobalPropsScanner(cached_execution_events=[])
        budget = GlobalScanBudget(max_fixtures=3, max_execution_events=2)
        res = scanner.execute_scan(budget=budget)

        self.assertEqual(res.funnel_metrics.fixtures_discovered, 10)
        self.assertEqual(res.funnel_metrics.fixtures_selected, 3)

    # ----------------------------------------------------------------------
    # TEST J — STATSHUB SCOPING
    # ----------------------------------------------------------------------
    @patch("scanner.global_props_scanner.BetclicProvider")
    @patch("scanner.global_props_scanner.SuperbetProvider")
    @patch("scanner.global_props_scanner.StatsHubTeamPropsProvider")
    @patch("scanner.global_props_scanner.StatsHubProvider")
    def test_j_statshub_props_outside_selected_fixtures_excluded(
        self,
        mock_statshub_p_cls,
        mock_statshub_t_cls,
        mock_sb_cls,
        mock_bc_cls,
    ):
        """Props belonging to non-selected fixtures never reach deduplication, matching, or evaluation."""
        sb_inst = MagicMock()
        sb_inst.discover.return_value = []
        mock_sb_cls.return_value = sb_inst

        bc_inst = MagicMock()
        bc_inst.discover.return_value = []
        mock_bc_cls.return_value = bc_inst

        p_inst = MagicMock()
        trends = []
        # 10 fixtures with 2 props each = 20 props
        for i in range(1, 11):
            fix = StatsHubFixture(fixture_id=f"f{i}", home_team=f"Home{i}", away_team=f"Away{i}", competition="L1", kickoff="2026-09-01T20:00:00Z")
            stat1 = StatsHubPlayerStat(player_id=i * 10 + 1, player_name=f"P{i}_1", team=f"Home{i}", opponent=f"Away{i}", stat_type="shots", line=1.5, odds_type="over", hit_rate_pct=80.0, sample_size=10, fixture=fix, bookmaker_odds=[StatsHubBookmakerOdds("Bet365", 1.5, "over", 1.9)])
            stat2 = StatsHubPlayerStat(player_id=i * 10 + 2, player_name=f"P{i}_2", team=f"Home{i}", opponent=f"Away{i}", stat_type="shots", line=2.5, odds_type="over", hit_rate_pct=70.0, sample_size=10, fixture=fix, bookmaker_odds=[StatsHubBookmakerOdds("Bet365", 2.5, "over", 2.2)])
            trends.extend([StatsHubPropResult(player_stat=stat1), StatsHubPropResult(player_stat=stat2)])

        p_inst.run.return_value.parsed_objects = trends
        mock_statshub_p_cls.return_value = p_inst

        t_inst = MagicMock()
        t_inst.run.return_value.parsed_objects = []
        mock_statshub_t_cls.return_value = t_inst

        scanner = GlobalPropsScanner(cached_execution_events=[])
        scope = GlobalScanScope(props_scope="PLAYER", stat_types=["shots"])
        # max_fixtures = 2 -> only 2 fixtures selected (4 props scoped to selected)
        budget = GlobalScanBudget(max_fixtures=2, max_execution_events=2)
        res = scanner.execute_scan(scope=scope, budget=budget)

        self.assertEqual(res.funnel_metrics.trends_discovered, 20)
        self.assertEqual(res.funnel_metrics.fixtures_selected, 2)
        # Telemetry check for scoped to selected
        self.assertEqual(res.funnel_metrics.trends_scoped_to_selected, 4)
        self.assertLessEqual(res.funnel_metrics.trends_deduplicated, 4)
        self.assertLessEqual(res.funnel_metrics.evaluated_count, 4)

    # ----------------------------------------------------------------------
    # TEST K — MAX TRENDS REQUESTS
    # ----------------------------------------------------------------------
    @patch("scanner.global_props_scanner.BetclicProvider")
    @patch("scanner.global_props_scanner.SuperbetProvider")
    @patch("scanner.global_props_scanner.StatsHubTeamPropsProvider")
    @patch("scanner.global_props_scanner.StatsHubProvider")
    def test_k_max_trends_requests_respected(
        self,
        mock_statshub_p_cls,
        mock_statshub_t_cls,
        mock_sb_cls,
        mock_bc_cls,
    ):
        """When max_trends_requests=2, StatsHub provider run() is called at most 2 times."""
        sb_inst = MagicMock()
        sb_inst.discover.return_value = []
        mock_sb_cls.return_value = sb_inst

        bc_inst = MagicMock()
        bc_inst.discover.return_value = []
        mock_bc_cls.return_value = bc_inst

        p_inst = MagicMock()
        p_inst.run.return_value.parsed_objects = []
        mock_statshub_p_cls.return_value = p_inst

        t_inst = MagicMock()
        t_inst.run.return_value.parsed_objects = []
        mock_statshub_t_cls.return_value = t_inst

        scanner = GlobalPropsScanner(cached_execution_events=[])
        scope = GlobalScanScope(
            props_scope="ALL",
            stat_types=["shots", "shots_on_target", "fouls", "corners", "cards"],
        )
        budget = GlobalScanBudget(max_trends_requests=2)
        scanner.execute_scan(scope=scope, budget=budget)

        total_requests = mock_statshub_p_cls.call_count + mock_statshub_t_cls.call_count
        self.assertLessEqual(total_requests, 2, f"StatsHub requests made: {total_requests}, expected <= 2")

    # ----------------------------------------------------------------------
    # TEST L — SERVICES SCAN PROPS BOUNDED LIFECYCLE
    # ----------------------------------------------------------------------
    @patch("providers.superbet.provider.SuperbetProvider")
    @patch("providers.betclic.provider.BetclicProvider")
    @patch("providers.statshub.provider.StatsHubProvider")
    def test_l_services_scan_props_bounds_execution_lifecycle(
        self,
        mock_statshub_p_cls,
        mock_bc_cls,
        mock_sb_cls,
    ):
        """In PlatformAPIService.scan_props, providers MUST have set_discovered_items called with matched items only."""
        # StatsHub returns 1 prop for TeamA vs TeamB
        p_inst = MagicMock()
        fix = StatsHubFixture(fixture_id="f1", home_team="TeamA", away_team="TeamB", competition="L1", kickoff="2026-09-01T20:00:00Z")
        stat = StatsHubPlayerStat(player_id=1, player_name="Player1", team="TeamA", opponent="TeamB", stat_type="shots", line=1.5, odds_type="over", hit_rate_pct=80.0, sample_size=10, fixture=fix, bookmaker_odds=[StatsHubBookmakerOdds("Bet365", 1.5, "over", 1.9)])
        p_inst.run.return_value.parsed_objects = [StatsHubPropResult(player_stat=stat)]
        mock_statshub_p_cls.return_value = p_inst

        # Superbet discovers 926 items
        sb_inst = MagicMock()
        sb_items = [
            SuperbetDiscoveredItem(event_id=f"sb-{i}", match_name=f"TeamA vs TeamB" if i == 1 else f"OtherH{i} vs OtherA{i}", competition_id="1", competition_name="L1", start_time="2026-09-01T20:00:00Z")
            for i in range(1, 927)
        ]
        sb_inst.discover.return_value = sb_items
        sb_inst.run.return_value.parsed_objects = []
        mock_sb_cls.return_value = sb_inst

        # Betclic discovers 370 items
        bc_inst = MagicMock()
        bc_items = [
            BetclicDiscoveredItem(provider_event_id=f"bc-{i}", name=f"TeamA vs TeamB" if i == 1 else f"OtherH{i} vs OtherA{i}", competition_name="L1", url="https://betclic.pl/e", start_time="2026-09-01T20:00:00Z")
            for i in range(1, 371)
        ]
        bc_inst.discover.return_value = bc_items
        bc_inst.run.return_value.parsed_objects = []
        mock_bc_cls.return_value = bc_inst

        self.service.scan_player_props(config_params={"stat": "shots"})

        # Verify that set_discovered_items was called on sb_inst with matched item (1 item) and NOT 926 items
        self.assertTrue(sb_inst.set_discovered_items.called, "SuperbetProvider.set_discovered_items was not called before sb_p.run() in scan_props")
        sb_set_items = sb_inst.set_discovered_items.call_args[0][0]
        self.assertEqual(len(sb_set_items), 1, f"Expected 1 matched item set for Superbet, got {len(sb_set_items)}")

        # Verify that set_discovered_items was called on bc_inst with matched item (1 item) and NOT 370 items
        self.assertTrue(bc_inst.set_discovered_items.called, "BetclicProvider.set_discovered_items was not called before bc_p.run() in scan_props")
        bc_set_items = bc_inst.set_discovered_items.call_args[0][0]
        self.assertEqual(len(bc_set_items), 1, f"Expected 1 matched item set for Betclic, got {len(bc_set_items)}")


if __name__ == "__main__":
    unittest.main()
