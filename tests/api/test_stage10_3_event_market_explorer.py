"""
Stage 10.3: Real Event & Market Explorer Test Suite

Verifies:
1. events endpoint returns real scanned events
2. empty state before first scan (returns empty list)
3. event filtering by sport, competition, provider, search, and matched
4. event detail serialization (canonical event ID, teams, competition, kickoff, confidence)
5. real bookmaker coverage (Superbet + Betclic provider summaries)
6. real market serialization across canonical types (1X2, BTTS, TOTALS, DRAW_NO_BET)
7. exact TOTALS line preservation (e.g. 2.5 vs 3.5 distinct)
8. selection serialization with provider odds mapping
9. best odds identification for display and implied probability
10. zero-opportunity event returns honest zero-opportunity state
11. real surebet attached to event is exposed with full mathematical explanation
12. real valuebet attached to event is exposed with EV and fair probability
13. invalid/missing event returns 404 ResourceNotFoundError
14. no fake events / mock data in responses
15. no undefined / NaN in UI serialization
16. events pagination (limit, offset)
17. nearest opportunity telemetry attached to zero-surebet event
18. opportunity deep link and identifier consistency
"""

import json
from decimal import Decimal
from datetime import datetime, timezone
import unittest
from unittest.mock import MagicMock, patch

from api.routes import APIRouter
from api.services import (
    PlatformAPIService,
    _serialize_events_from_scan_result,
    _serialize_scan_cycle_result,
)
from database.config import DatabaseConfig
from database.connection import DatabaseManager
from domain.models import (
    CanonicalCompetition,
    CanonicalEvent,
    EventSource,
    MatchEvidence,
    Market,
    Selection,
    Odds,
    Event,
    Competition,
)
from normalization.base_normalizer import NormalizedGraph
from normalization.market_identity import CanonicalMarketKey, CanonicalMarketType
from normalization.selection_identity import CanonicalSelectionKey, CanonicalSelectionType
from normalization.surebet import (
    SurebetOpportunity,
    SurebetLeg,
    SurebetStatus,
    SurebetDetectionResult,
    MarketSurebetEvaluation,
    MarketCompletenessStatus,
)
from normalization.validation_pipeline import (
    CanonicalEventValidationRecord,
    ComparableSelectionPair,
    CrossBookmakerValidationResult,
    MatchedMarketLineage,
    PipelineMetrics,
)
from normalization.market_matcher import MarketMatchBatchResult, MarketMatchDecision, MarketMatchDecisionType
from normalization.selection_matcher import SelectionMatchBatchResult
from orchestration.models import CycleStatus, ScanCycleResult, StageTiming, ResourceMetrics
from valuebets.models import ValueBetCandidate, ValueBetDetectionResult


def build_sample_scan_cycle_result() -> ScanCycleResult:
    """Builds a realistic, mathematically sound ScanCycleResult with real events and markets."""
    now_str = datetime.now(timezone.utc).isoformat()

    # 1. Canonical Event: Arsenal vs Chelsea
    comp = CanonicalCompetition(name="Premier League", sport="football", country="England")
    sources = {
        "superbet": EventSource(
            provider="superbet",
            provider_event_id="sb_ars_che_01",
            internal_event_id="ev_sb_01",
            home_participant="Arsenal",
            away_participant="Chelsea",
            scheduled_start="2026-08-17T18:30:00Z",
            competition_name="Premier League",
        ),
        "betclic": EventSource(
            provider="betclic",
            provider_event_id="bc_ars_che_02",
            internal_event_id="ev_bc_02",
            home_participant="Arsenal",
            away_participant="Chelsea",
            scheduled_start="2026-08-17T18:30:00Z",
            competition_name="Premier League",
        ),
    }
    evidence = MatchEvidence(
        source_provider="superbet",
        target_provider="betclic",
        source_event_id="sb_ars_che_01",
        target_event_id="bc_ars_che_02",
        decision="MATCH",
        total_score=0.98,
        orientation="HOME_AWAY",
    )
    ce_arsenal = CanonicalEvent(
        canonical_event_id="cev_arsenal_chelsea_001",
        sport="football",
        home_team="Arsenal",
        away_team="Chelsea",
        scheduled_start="2026-08-17T18:30:00Z",
        competition=comp,
        sources=sources,
        match_evidence=[evidence],
    )

    # 2. Markets for Arsenal vs Chelsea:
    # 2a. 1X2 Market
    mkt_1x2_key = CanonicalMarketKey(market_type=CanonicalMarketType.ONE_X_TWO.value, period="FULL_TIME", scope="MATCH")
    src_mkt_1x2 = Market(event_id="ev_sb_01", market_type="1X2", internal_id="mkt_sb_1x2")
    tgt_mkt_1x2 = Market(event_id="ev_bc_02", market_type="1X2", internal_id="mkt_bc_1x2")

    pair_home = ComparableSelectionPair(
        canonical_event_id=ce_arsenal.canonical_event_id,
        canonical_market_key=mkt_1x2_key,
        canonical_selection_key=CanonicalSelectionKey(market_key=mkt_1x2_key, selection_type=CanonicalSelectionType.HOME.value),
        source_provider="superbet",
        target_provider="betclic",
        source_event_id="sb_ars_che_01",
        target_event_id="bc_ars_che_02",
        source_internal_event_id="ev_sb_01",
        target_internal_event_id="ev_bc_02",
        source_market_id="mkt_sb_1x2",
        target_market_id="mkt_bc_1x2",
        source_selection_id="sel_sb_1",
        target_selection_id="sel_bc_1",
        source_selection=Selection(market_id="mkt_sb_1x2", selection_type="HOME", participant="Arsenal", internal_id="sel_sb_1"),
        target_selection=Selection(market_id="mkt_bc_1x2", selection_type="HOME", participant="Arsenal", internal_id="sel_bc_1"),
        source_odds=Odds(selection_id="sel_sb_1", bookmaker="superbet", decimal_odds=2.15),
        target_odds=Odds(selection_id="sel_bc_1", bookmaker="betclic", decimal_odds=2.10),
    )
    pair_draw = ComparableSelectionPair(
        canonical_event_id=ce_arsenal.canonical_event_id,
        canonical_market_key=mkt_1x2_key,
        canonical_selection_key=CanonicalSelectionKey(market_key=mkt_1x2_key, selection_type=CanonicalSelectionType.DRAW.value),
        source_provider="superbet",
        target_provider="betclic",
        source_event_id="sb_ars_che_01",
        target_event_id="bc_ars_che_02",
        source_internal_event_id="ev_sb_01",
        target_internal_event_id="ev_bc_02",
        source_market_id="mkt_sb_1x2",
        target_market_id="mkt_bc_1x2",
        source_selection_id="sel_sb_x",
        target_selection_id="sel_bc_x",
        source_selection=Selection(market_id="mkt_sb_1x2", selection_type="DRAW", internal_id="sel_sb_x"),
        target_selection=Selection(market_id="mkt_bc_1x2", selection_type="DRAW", internal_id="sel_bc_x"),
        source_odds=Odds(selection_id="sel_sb_x", bookmaker="superbet", decimal_odds=3.60),
        target_odds=Odds(selection_id="sel_bc_x", bookmaker="betclic", decimal_odds=3.55),
    )
    pair_away = ComparableSelectionPair(
        canonical_event_id=ce_arsenal.canonical_event_id,
        canonical_market_key=mkt_1x2_key,
        canonical_selection_key=CanonicalSelectionKey(market_key=mkt_1x2_key, selection_type=CanonicalSelectionType.AWAY.value),
        source_provider="superbet",
        target_provider="betclic",
        source_event_id="sb_ars_che_01",
        target_event_id="bc_ars_che_02",
        source_internal_event_id="ev_sb_01",
        target_internal_event_id="ev_bc_02",
        source_market_id="mkt_sb_1x2",
        target_market_id="mkt_bc_1x2",
        source_selection_id="sel_sb_2",
        target_selection_id="sel_bc_2",
        source_selection=Selection(market_id="mkt_sb_1x2", selection_type="AWAY", participant="Chelsea", internal_id="sel_sb_2"),
        target_selection=Selection(market_id="mkt_bc_1x2", selection_type="AWAY", participant="Chelsea", internal_id="sel_bc_2"),
        source_odds=Odds(selection_id="sel_sb_2", bookmaker="superbet", decimal_odds=3.40),
        target_odds=Odds(selection_id="sel_bc_2", bookmaker="betclic", decimal_odds=3.50),
    )

    lineage_1x2 = MatchedMarketLineage(
        canonical_event_id=ce_arsenal.canonical_event_id,
        canonical_market_key=mkt_1x2_key,
        source_market_id="mkt_sb_1x2",
        target_market_id="mkt_bc_1x2",
        source_market=src_mkt_1x2,
        target_market=tgt_mkt_1x2,
        market_decision=MarketMatchDecision(
            decision=MarketMatchDecisionType.MATCHED,
            source_market_id="mkt_sb_1x2",
            target_market_id="mkt_bc_1x2",
        ),
        selection_batch_result=SelectionMatchBatchResult(),
        comparable_selections=[pair_home, pair_draw, pair_away],
    )

    # 2b. TOTALS 2.5 Market
    mkt_totals_25_key = CanonicalMarketKey(market_type=CanonicalMarketType.TOTALS.value, period="FULL_TIME", scope="MATCH", line=Decimal("2.5"))
    src_mkt_tot25 = Market(event_id="ev_sb_01", market_type="TOTALS", line=2.5, internal_id="mkt_sb_tot25")
    tgt_mkt_tot25 = Market(event_id="ev_bc_02", market_type="TOTALS", line=2.5, internal_id="mkt_bc_tot25")

    pair_over25 = ComparableSelectionPair(
        canonical_event_id=ce_arsenal.canonical_event_id,
        canonical_market_key=mkt_totals_25_key,
        canonical_selection_key=CanonicalSelectionKey(market_key=mkt_totals_25_key, selection_type=CanonicalSelectionType.OVER.value, selection_line=Decimal("2.5")),
        source_provider="superbet",
        target_provider="betclic",
        source_event_id="sb_ars_che_01",
        target_event_id="bc_ars_che_02",
        source_internal_event_id="ev_sb_01",
        target_internal_event_id="ev_bc_02",
        source_market_id="mkt_sb_tot25",
        target_market_id="mkt_bc_tot25",
        source_selection_id="sel_sb_ov25",
        target_selection_id="sel_bc_ov25",
        source_selection=Selection(market_id="mkt_sb_tot25", selection_type="OVER", line=2.5, internal_id="sel_sb_ov25"),
        target_selection=Selection(market_id="mkt_bc_tot25", selection_type="OVER", line=2.5, internal_id="sel_bc_ov25"),
        source_odds=Odds(selection_id="sel_sb_ov25", bookmaker="superbet", decimal_odds=1.85),
        target_odds=Odds(selection_id="sel_bc_ov25", bookmaker="betclic", decimal_odds=1.80),
    )
    pair_under25 = ComparableSelectionPair(
        canonical_event_id=ce_arsenal.canonical_event_id,
        canonical_market_key=mkt_totals_25_key,
        canonical_selection_key=CanonicalSelectionKey(market_key=mkt_totals_25_key, selection_type=CanonicalSelectionType.UNDER.value, selection_line=Decimal("2.5")),
        source_provider="superbet",
        target_provider="betclic",
        source_event_id="sb_ars_che_01",
        target_event_id="bc_ars_che_02",
        source_internal_event_id="ev_sb_01",
        target_internal_event_id="ev_bc_02",
        source_market_id="mkt_sb_tot25",
        target_market_id="mkt_bc_tot25",
        source_selection_id="sel_sb_un25",
        target_selection_id="sel_bc_un25",
        source_selection=Selection(market_id="mkt_sb_tot25", selection_type="UNDER", line=2.5, internal_id="sel_sb_un25"),
        target_selection=Selection(market_id="mkt_bc_tot25", selection_type="UNDER", line=2.5, internal_id="sel_bc_un25"),
        source_odds=Odds(selection_id="sel_sb_un25", bookmaker="superbet", decimal_odds=1.95),
        target_odds=Odds(selection_id="sel_bc_un25", bookmaker="betclic", decimal_odds=2.00),
    )

    lineage_tot25 = MatchedMarketLineage(
        canonical_event_id=ce_arsenal.canonical_event_id,
        canonical_market_key=mkt_totals_25_key,
        source_market_id="mkt_sb_tot25",
        target_market_id="mkt_bc_tot25",
        source_market=src_mkt_tot25,
        target_market=tgt_mkt_tot25,
        market_decision=MarketMatchDecision(
            decision=MarketMatchDecisionType.MATCHED,
            source_market_id="mkt_sb_1x2",
            target_market_id="mkt_bc_1x2",
        ),
        selection_batch_result=SelectionMatchBatchResult(),
        comparable_selections=[pair_over25, pair_under25],
    )

    # 2c. TOTALS 3.5 Market (Testing line distinctness)
    mkt_totals_35_key = CanonicalMarketKey(market_type=CanonicalMarketType.TOTALS.value, period="FULL_TIME", scope="MATCH", line=Decimal("3.5"))
    src_mkt_tot35 = Market(event_id="ev_sb_01", market_type="TOTALS", line=3.5, internal_id="mkt_sb_tot35")
    tgt_mkt_tot35 = Market(event_id="ev_bc_02", market_type="TOTALS", line=3.5, internal_id="mkt_bc_tot35")

    pair_over35 = ComparableSelectionPair(
        canonical_event_id=ce_arsenal.canonical_event_id,
        canonical_market_key=mkt_totals_35_key,
        canonical_selection_key=CanonicalSelectionKey(market_key=mkt_totals_35_key, selection_type=CanonicalSelectionType.OVER.value, selection_line=Decimal("3.5")),
        source_provider="superbet",
        target_provider="betclic",
        source_event_id="sb_ars_che_01",
        target_event_id="bc_ars_che_02",
        source_internal_event_id="ev_sb_01",
        target_internal_event_id="ev_bc_02",
        source_market_id="mkt_sb_tot35",
        target_market_id="mkt_bc_tot35",
        source_selection_id="sel_sb_ov35",
        target_selection_id="sel_bc_ov35",
        source_selection=Selection(market_id="mkt_sb_tot35", selection_type="OVER", line=3.5, internal_id="sel_sb_ov35"),
        target_selection=Selection(market_id="mkt_bc_tot35", selection_type="OVER", line=3.5, internal_id="sel_bc_ov35"),
        source_odds=Odds(selection_id="sel_sb_ov35", bookmaker="superbet", decimal_odds=3.10),
        target_odds=Odds(selection_id="sel_bc_ov35", bookmaker="betclic", decimal_odds=3.00),
    )
    pair_under35 = ComparableSelectionPair(
        canonical_event_id=ce_arsenal.canonical_event_id,
        canonical_market_key=mkt_totals_35_key,
        canonical_selection_key=CanonicalSelectionKey(market_key=mkt_totals_35_key, selection_type=CanonicalSelectionType.UNDER.value, selection_line=Decimal("3.5")),
        source_provider="superbet",
        target_provider="betclic",
        source_event_id="sb_ars_che_01",
        target_event_id="bc_ars_che_02",
        source_internal_event_id="ev_sb_01",
        target_internal_event_id="ev_bc_02",
        source_market_id="mkt_sb_tot35",
        target_market_id="mkt_bc_tot35",
        source_selection_id="sel_sb_un35",
        target_selection_id="sel_bc_un35",
        source_selection=Selection(market_id="mkt_sb_tot35", selection_type="UNDER", line=3.5, internal_id="sel_sb_un35"),
        target_selection=Selection(market_id="mkt_bc_tot35", selection_type="UNDER", line=3.5, internal_id="sel_bc_un35"),
        source_odds=Odds(selection_id="sel_sb_un35", bookmaker="superbet", decimal_odds=1.35),
        target_odds=Odds(selection_id="sel_bc_un35", bookmaker="betclic", decimal_odds=1.38),
    )

    lineage_tot35 = MatchedMarketLineage(
        canonical_event_id=ce_arsenal.canonical_event_id,
        canonical_market_key=mkt_totals_35_key,
        source_market_id="mkt_sb_tot35",
        target_market_id="mkt_bc_tot35",
        source_market=src_mkt_tot35,
        target_market=tgt_mkt_tot35,
        market_decision=MarketMatchDecision(
            decision=MarketMatchDecisionType.MATCHED,
            source_market_id="mkt_sb_tot35",
            target_market_id="mkt_bc_tot35",
        ),
        selection_batch_result=SelectionMatchBatchResult(),
        comparable_selections=[pair_over35, pair_under35],
    )

    val_record_arsenal = CanonicalEventValidationRecord(
        canonical_event=ce_arsenal,
        market_batch_result=MarketMatchBatchResult(),
        matched_markets=[lineage_1x2, lineage_tot25, lineage_tot35],
    )

    # 3. Canonical Event 2: Real Madrid vs Barcelona (With Surebet)
    comp_laliga = CanonicalCompetition(name="La Liga", sport="football", country="Spain")
    ce_elclasico = CanonicalEvent(
        canonical_event_id="cev_real_barca_002",
        sport="football",
        home_team="Real Madrid",
        away_team="Barcelona",
        scheduled_start="2026-08-17T21:00:00Z",
        competition=comp_laliga,
        sources={
            "superbet": EventSource(provider="superbet", provider_event_id="sb_rm_fcb", internal_event_id="ev_sb_rm", home_participant="Real Madrid", away_participant="Barcelona"),
            "betclic": EventSource(provider="betclic", provider_event_id="bc_rm_fcb", internal_event_id="ev_bc_rm", home_participant="Real Madrid", away_participant="Barcelona"),
        },
        match_evidence=[evidence],
    )

    leg_h = SurebetLeg(
        canonical_selection_key=CanonicalSelectionKey(market_key=mkt_1x2_key, selection_type="HOME"),
        selection_type="HOME",
        provider="superbet",
        odds=Decimal("2.25"),
        source_selection_id="sb_sel_rm",
        implied_probability=Decimal("1.0") / Decimal("2.25"),
    )
    leg_d = SurebetLeg(
        canonical_selection_key=CanonicalSelectionKey(market_key=mkt_1x2_key, selection_type="DRAW"),
        selection_type="DRAW",
        provider="superbet",
        odds=Decimal("3.90"),
        source_selection_id="sb_sel_dr",
        implied_probability=Decimal("1.0") / Decimal("3.90"),
    )
    leg_a = SurebetLeg(
        canonical_selection_key=CanonicalSelectionKey(market_key=mkt_1x2_key, selection_type="AWAY"),
        selection_type="AWAY",
        provider="betclic",
        odds=Decimal("4.20"),
        source_selection_id="bc_sel_fcb",
        implied_probability=Decimal("1.0") / Decimal("4.20"),
    )
    sum_s = leg_h.implied_probability + leg_d.implied_probability + leg_a.implied_probability
    margin = (Decimal("1.0") / sum_s) - Decimal("1.0")

    opp_clasico = SurebetOpportunity(
        opportunity_id="sb:cev_real_barca_002:1X2:HOME:superbet:2.25|DRAW:superbet:3.90|AWAY:betclic:4.20",
        canonical_event_id="cev_real_barca_002",
        canonical_market_key=mkt_1x2_key,
        legs=(leg_h, leg_d, leg_a),
        implied_probability_sum=sum_s,
        arbitrage_margin=margin,
        status=SurebetStatus.SUREBET,
        is_mixed_bookmakers=True,
        bookmakers=("superbet", "betclic"),
    )

    val_record_clasico = CanonicalEventValidationRecord(
        canonical_event=ce_elclasico,
        market_batch_result=MarketMatchBatchResult(),
        matched_markets=[],
    )

    # 4. Canonical Event 3: Bayern Munich vs Dortmund (With Valuebet)
    comp_bundesliga = CanonicalCompetition(name="Bundesliga", sport="football", country="Germany")
    ce_bayern = CanonicalEvent(
        canonical_event_id="cev_bayern_bvb_003",
        sport="football",
        home_team="Bayern Munich",
        away_team="Borussia Dortmund",
        scheduled_start="2026-08-17T17:30:00Z",
        competition=comp_bundesliga,
        sources={
            "superbet": EventSource(provider="superbet", provider_event_id="sb_bay_bvb", internal_event_id="ev_sb_bay", home_participant="Bayern Munich", away_participant="Borussia Dortmund"),
        },
    )
    val_record_bayern = CanonicalEventValidationRecord(
        canonical_event=ce_bayern,
        market_batch_result=MarketMatchBatchResult(),
        matched_markets=[],
    )

    val_cand_bvb = ValueBetCandidate(
        candidate_id="val_cand_bay_001",
        canonical_event_id="cev_bayern_bvb_003",
        event_name="Bayern Munich vs Borussia Dortmund",
        sport="football",
        competition_name="Bundesliga",
        kickoff="2026-08-17T17:30:00Z",
        market_key=CanonicalMarketKey(market_type="1X2", period="FULL_TIME", scope="MATCH"),
        market_type="1X2",
        line=None,
        selection_key=CanonicalSelectionKey(market_key=CanonicalMarketKey(market_type="1X2", period="FULL_TIME", scope="MATCH"), selection_type="HOME"),
        selection_type="HOME",
        bookmaker="superbet",
        bookmaker_odds=Decimal("1.95"),
        bookmaker_implied_prob=Decimal("1.0") / Decimal("1.95"),
        reference_source="the_odds_api",
        reference_bookmaker="pinnacle",
        reference_raw_odds=Decimal("1.75"),
        reference_overround=Decimal("1.025"),
        reference_fair_probability=Decimal("0.5556"),
        reference_fair_odds=Decimal("1.80"),
        value_edge=Decimal("0.0833"),
        value_percent=Decimal("8.33"),
        is_qualified=True,
    )

    val_result = CrossBookmakerValidationResult(
        canonical_events=[ce_arsenal, ce_elclasico, ce_bayern],
        event_validation_records=[val_record_arsenal, val_record_clasico, val_record_bayern],
        metrics=PipelineMetrics(
            source_event_count=3,
            target_event_count=2,
            matched_event_count=2,
            canonical_event_count=3,
            matched_market_count=3,
        ),
    )

    det_result = SurebetDetectionResult(
        opportunities=[opp_clasico],
        evaluations=[
            MarketSurebetEvaluation(
                canonical_event_id="cev_arsenal_chelsea_001",
                canonical_market_key=mkt_1x2_key,
                status=SurebetStatus.NO_SUREBET,
                completeness_status=MarketCompletenessStatus.COMPLETE,
                required_selection_types=("HOME", "DRAW", "AWAY"),
                available_selection_types=("HOME", "DRAW", "AWAY"),
                missing_selection_types=(),
                best_legs=(leg_h, leg_d, leg_a),
                implied_probability_sum=Decimal("1.0345"),
                arbitrage_margin=Decimal("-0.0334"),
            )
        ],
    )

    val_res = ValueBetDetectionResult(
        candidates=[val_cand_bvb],
        qualified_valuebets=[val_cand_bvb],
    )

    return ScanCycleResult(
        execution_id="scan_test_10_3_001",
        cycle_status=CycleStatus.SUCCESS,
        started_at=now_str,
        completed_at=now_str,
        duration_seconds=1.25,
        stage_timings=StageTiming(acquisition_seconds=0.4, normalization_seconds=0.3, matching_seconds=0.2, detection_seconds=0.1, total_duration_seconds=1.25),
        resource_metrics=ResourceMetrics(events_discovered=10, events_selected=5, events_parsed=5, markets_discovered=25, markets_normalized=20, markets_matched=3, matched_events=2),
        validation_result=val_result,
        detection_result=det_result,
        valuebet_result=val_res,
        discovered_events_count=10,
        parsed_events_count=5,
        normalized_graphs_count=5,
        matched_events_count=2,
        detected_opportunities_count=1,
    )


class TestStage103EventMarketExplorer(unittest.TestCase):
    """Focused Unit Test Suite for Stage 10.3 Event & Market Explorer."""

    def setUp(self):
        self.db = DatabaseManager(DatabaseConfig.default_sqlite_in_memory())
        self.db.create_tables()
        self.service = PlatformAPIService(db_manager=self.db)
        self.router = APIRouter(service=self.service)

    def test_01_empty_state_before_first_scan(self):
        """Before any scan has run, GET /api/v1/events must return an honest empty list []."""
        res = self.router.handle_get_events()
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data, [])
        self.assertEqual(res.metadata.get("count"), 0)

    def test_02_events_endpoint_returns_real_scanned_events(self):
        """Events endpoint returns real canonical events after a scan cycle."""
        scan_res = build_sample_scan_cycle_result()
        with patch.object(self.service.scan_orchestrator, "run_scan_cycle", return_value=scan_res):
            self.service.run_scan(scan_source="MANUAL")

        res = self.router.handle_get_events()
        self.assertEqual(res.status_code, 200)
        events = res.data
        self.assertGreaterEqual(len(events), 3)

        # Check Arsenal vs Chelsea
        ars = next((e for e in events if "Arsenal" in e["home_team"]), None)
        self.assertIsNotNone(ars)
        self.assertEqual(ars["away_team"], "Chelsea")
        self.assertEqual(ars["competition"], "Premier League")
        self.assertEqual(ars["sport"], "football")
        self.assertEqual(ars["matching_status"], "MATCHED")
        self.assertIn("superbet", ars["participating_bookmakers"])
        self.assertIn("betclic", ars["participating_bookmakers"])
        self.assertEqual(ars["matched_markets_count"], 3)

    def test_03_event_filtering_by_sport_and_competition(self):
        """Events can be filtered by sport and competition name."""
        scan_res = build_sample_scan_cycle_result()
        with patch.object(self.service.scan_orchestrator, "run_scan_cycle", return_value=scan_res):
            self.service.run_scan()

        # Filter by Premier League
        res_pl = self.router.handle_get_events(competition="Premier League")
        self.assertEqual(len(res_pl.data), 1)
        self.assertEqual(res_pl.data[0]["home_team"], "Arsenal")

        # Filter by La Liga
        res_ll = self.router.handle_get_events(competition="La Liga")
        self.assertEqual(len(res_ll.data), 1)
        self.assertEqual(res_ll.data[0]["home_team"], "Real Madrid")

    def test_04_event_filtering_by_provider_and_search_and_matched(self):
        """Events can be filtered by participating provider, search string, and matching status."""
        scan_res = build_sample_scan_cycle_result()
        with patch.object(self.service.scan_orchestrator, "run_scan_cycle", return_value=scan_res):
            self.service.run_scan()

        # Search by team name "Barcelona"
        res_search = self.router.handle_get_events(search="Barcelona")
        self.assertEqual(len(res_search.data), 1)
        self.assertEqual(res_search.data[0]["away_team"], "Barcelona")

        # Filter by matched=true
        res_matched = self.router.handle_get_events(matched="true")
        for ev in res_matched.data:
            self.assertEqual(ev["matching_status"], "MATCHED")

    def test_05_event_detail_metadata_serialization(self):
        """Event detail view serializes full canonical metadata, kickoff, teams, and confidence."""
        scan_res = build_sample_scan_cycle_result()
        with patch.object(self.service.scan_orchestrator, "run_scan_cycle", return_value=scan_res):
            self.service.run_scan()

        res = self.router.handle_get_event_detail("cev_arsenal_chelsea_001")
        self.assertEqual(res.status_code, 200)
        detail = res.data
        self.assertEqual(detail["canonical_event_id"], "cev_arsenal_chelsea_001")
        self.assertEqual(detail["home_team"], "Arsenal")
        self.assertEqual(detail["away_team"], "Chelsea")
        self.assertEqual(detail["competition"], "Premier League")
        self.assertEqual(detail["kickoff"], "2026-08-17T18:30:00Z")
        self.assertEqual(detail["matching_confidence"], 0.98)

    def test_06_real_bookmaker_coverage_table(self):
        """Event detail contains real provider coverage breakdown with status and market counts."""
        scan_res = build_sample_scan_cycle_result()
        with patch.object(self.service.scan_orchestrator, "run_scan_cycle", return_value=scan_res):
            self.service.run_scan()

        res = self.router.handle_get_event_detail("cev_arsenal_chelsea_001")
        self.assertEqual(res.status_code, 200)
        providers = res.data.get("providers", [])
        self.assertEqual(len(providers), 2)
        prov_names = [p["provider"] for p in providers]
        self.assertIn("superbet", prov_names)
        self.assertIn("betclic", prov_names)
        for p in providers:
            self.assertEqual(p["status"], "Available")
            self.assertGreater(p["market_count"], 0)
            self.assertIn("raw_market_count", p)
            self.assertIn("normalized_market_count", p)
            self.assertIn("matched_market_count", p)
            self.assertGreater(p["raw_market_count"], 0)
            self.assertGreater(p["normalized_market_count"], 0)
            self.assertGreater(p["matched_market_count"], 0)

    def test_07_real_market_serialization_and_exact_line_preservation(self):
        """Markets are canonically grouped and exact lines (TOTALS 2.5 vs TOTALS 3.5) are preserved."""
        scan_res = build_sample_scan_cycle_result()
        with patch.object(self.service.scan_orchestrator, "run_scan_cycle", return_value=scan_res):
            self.service.run_scan()

        res = self.router.handle_get_event_detail("cev_arsenal_chelsea_001")
        self.assertEqual(res.status_code, 200)
        markets = res.data.get("markets", [])
        self.assertEqual(len(markets), 3)

        # 1X2 Market
        m_1x2 = next((m for m in markets if m["market_type"] == "1X2"), None)
        self.assertIsNotNone(m_1x2)
        self.assertIsNone(m_1x2["line"])
        self.assertIn("1X2", m_1x2["canonical_market_key"])

        # TOTALS 2.5
        m_tot25 = next((m for m in markets if m["market_type"] == "TOTALS" and m["line"] == 2.5), None)
        self.assertIsNotNone(m_tot25)
        self.assertEqual(m_tot25["line"], 2.5)
        self.assertIn("2.5", m_tot25["canonical_market_key"])

        # TOTALS 3.5
        m_tot35 = next((m for m in markets if m["market_type"] == "TOTALS" and m["line"] == 3.5), None)
        self.assertIsNotNone(m_tot35)
        self.assertEqual(m_tot35["line"], 3.5)
        self.assertIn("3.5", m_tot35["canonical_market_key"])

        # Distinct keys
        self.assertNotEqual(m_tot25["canonical_market_key"], m_tot35["canonical_market_key"])

    def test_08_selection_serialization_and_odds_mapping(self):
        """Selections preserve outcome types, participants, and bookmaker odds map."""
        scan_res = build_sample_scan_cycle_result()
        with patch.object(self.service.scan_orchestrator, "run_scan_cycle", return_value=scan_res):
            self.service.run_scan()

        res = self.router.handle_get_event_detail("cev_arsenal_chelsea_001")
        m_1x2 = next(m for m in res.data["markets"] if m["market_type"] == "1X2")
        sels = m_1x2["selections"]
        self.assertEqual(len(sels), 3)

        # HOME outcome (Arsenal)
        sel_h = next(s for s in sels if s["selection_type"] == "HOME")
        self.assertEqual(sel_h["odds"]["superbet"], 2.15)
        self.assertEqual(sel_h["odds"]["betclic"], 2.10)
        self.assertEqual(sel_h["participant"], "Arsenal")

        # DRAW outcome
        sel_d = next(s for s in sels if s["selection_type"] == "DRAW")
        self.assertEqual(sel_d["odds"]["superbet"], 3.60)
        self.assertEqual(sel_d["odds"]["betclic"], 3.55)

        # AWAY outcome (Chelsea)
        sel_a = next(s for s in sels if s["selection_type"] == "AWAY")
        self.assertEqual(sel_a["odds"]["superbet"], 3.40)
        self.assertEqual(sel_a["odds"]["betclic"], 3.50)

    def test_09_best_odds_identification_for_display(self):
        """Best odds and implied probability are computed authoritatively by the backend."""
        scan_res = build_sample_scan_cycle_result()
        with patch.object(self.service.scan_orchestrator, "run_scan_cycle", return_value=scan_res):
            self.service.run_scan()

        res = self.router.handle_get_event_detail("cev_arsenal_chelsea_001")
        m_1x2 = next(m for m in res.data["markets"] if m["market_type"] == "1X2")
        sels = m_1x2["selections"]

        sel_h = next(s for s in sels if s["selection_type"] == "HOME")
        # Superbet 2.15 > Betclic 2.10 -> Best: Superbet 2.15
        self.assertEqual(sel_h["best_odds"]["bookmaker"], "superbet")
        self.assertEqual(sel_h["best_odds"]["odds"], 2.15)
        self.assertEqual(sel_h["best_odds"]["implied_probability"], round(1.0 / 2.15, 4))

        sel_a = next(s for s in sels if s["selection_type"] == "AWAY")
        # Betclic 3.50 > Superbet 3.40 -> Best: Betclic 3.50
        self.assertEqual(sel_a["best_odds"]["bookmaker"], "betclic")
        self.assertEqual(sel_a["best_odds"]["odds"], 3.50)
        self.assertEqual(sel_a["best_odds"]["implied_probability"], round(1.0 / 3.50, 4))

    def test_10_zero_opportunity_event_state(self):
        """Events without qualified opportunities return an empty opportunity list without errors."""
        scan_res = build_sample_scan_cycle_result()
        with patch.object(self.service.scan_orchestrator, "run_scan_cycle", return_value=scan_res):
            self.service.run_scan()

        res = self.router.handle_get_event_detail("cev_arsenal_chelsea_001")
        self.assertEqual(res.status_code, 200)
        opps = res.data.get("opportunities", {})
        self.assertEqual(opps.get("surebets"), [])
        self.assertFalse(res.data.get("has_surebet"))

        # Nearest opportunity telemetry is provided for the zero-surebet evaluation
        nearest = opps.get("nearest_opportunity")
        self.assertIsNotNone(nearest)
        self.assertAlmostEqual(nearest["implied_probability_sum"], 1.0345, places=3)
        self.assertIn(">= 1.0", nearest["explanation"])

    def test_11_real_surebet_attached_to_event(self):
        """Events with a real detected SurebetOpportunity include full opportunity details and math."""
        scan_res = build_sample_scan_cycle_result()
        with patch.object(self.service.scan_orchestrator, "run_scan_cycle", return_value=scan_res):
            self.service.run_scan()

        res = self.router.handle_get_event_detail("cev_real_barca_002")
        self.assertEqual(res.status_code, 200)
        detail = res.data
        self.assertTrue(detail["has_surebet"])
        self.assertEqual(detail["surebets_count"], 1)

        surebet = detail["opportunities"]["surebets"][0]
        self.assertEqual(surebet["opportunity_type"], "SUREBET")
        self.assertGreater(surebet["margin_pct"], 0.0)
        math = surebet["mathematical_explanation"]
        self.assertEqual(math["formula"], "S = sum(1 / odds_i)")
        self.assertTrue(math["is_surebet"])
        self.assertLess(math["implied_probability_sum"], 1.0)
        self.assertGreater(math["arbitrage_margin_pct"], 0.0)

    def test_12_real_valuebet_attached_to_event(self):
        """Events with a real Valuebet include EV%, fair probability, and sharp reference baseline."""
        scan_res = build_sample_scan_cycle_result()
        with patch.object(self.service.scan_orchestrator, "run_scan_cycle", return_value=scan_res):
            self.service.run_scan()

        res = self.router.handle_get_event_detail("cev_bayern_bvb_003")
        self.assertEqual(res.status_code, 200)
        detail = res.data
        self.assertTrue(detail["has_valuebet"])
        self.assertEqual(detail["valuebets_count"], 1)

        val = detail["opportunities"]["valuebets"][0]
        self.assertEqual(val["opportunity_type"], "VALUEBET")
        self.assertEqual(val["value_percent"], 8.33)
        self.assertEqual(val["fair_odds"], 1.80)
        self.assertEqual(val["bookmaker_odds"], 1.95)
        self.assertEqual(val["reference_bookmaker"], "pinnacle")

    def test_13_missing_event_returns_404(self):
        """Querying an unknown or non-existent event ID returns 404 status code."""
        res = self.router.handle_get_event_detail("non_existent_event_999")
        self.assertEqual(res.status_code, 404)
        self.assertIn("not found", res.errors[0])

    def test_14_no_fake_events_in_response(self):
        """Only genuine scanned events are returned; no hardcoded fixtures."""
        scan_res = build_sample_scan_cycle_result()
        with patch.object(self.service.scan_orchestrator, "run_scan_cycle", return_value=scan_res):
            self.service.run_scan()

        res = self.router.handle_get_events()
        event_ids = [e["id"] for e in res.data]
        self.assertIn("cev_arsenal_chelsea_001", event_ids)
        self.assertIn("cev_real_barca_002", event_ids)
        self.assertIn("cev_bayern_bvb_003", event_ids)
        self.assertNotIn("fake_event_123", event_ids)

    def test_15_serialization_integrity_no_nan_or_undefined(self):
        """All serialized JSON responses must be valid and free of NaN, Infinity, or None keys."""
        scan_res = build_sample_scan_cycle_result()
        with patch.object(self.service.scan_orchestrator, "run_scan_cycle", return_value=scan_res):
            self.service.run_scan()

        events_res = self.router.handle_get_events().to_dict()
        events_json = json.dumps(events_res)
        self.assertNotIn("NaN", events_json)
        self.assertNotIn("Infinity", events_json)

        detail_res = self.router.handle_get_event_detail("cev_arsenal_chelsea_001").to_dict()
        detail_json = json.dumps(detail_res)
        self.assertNotIn("NaN", detail_json)
        self.assertNotIn("Infinity", detail_json)

    def test_16_events_pagination(self):
        """Events list respects limit and offset pagination parameters."""
        scan_res = build_sample_scan_cycle_result()
        with patch.object(self.service.scan_orchestrator, "run_scan_cycle", return_value=scan_res):
            self.service.run_scan()

        res_page1 = self.router.handle_get_events(limit=2, offset=0)
        self.assertEqual(len(res_page1.data), 2)

        res_page2 = self.router.handle_get_events(limit=2, offset=2)
        self.assertEqual(len(res_page2.data), 1)

    def test_17_read_only_event_detail_does_not_trigger_scan(self):
        """Calling get_event_detail must NOT invoke the scan orchestrator."""
        scan_res = build_sample_scan_cycle_result()
        with patch.object(self.service.scan_orchestrator, "run_scan_cycle", return_value=scan_res):
            self.service.run_scan()

        with patch.object(self.service.scan_orchestrator, "run_scan_cycle") as mock_scan:
            self.router.handle_get_event_detail("cev_arsenal_chelsea_001")
            mock_scan.assert_not_called()

    def test_18_opportunity_deep_link_consistency(self):
        """Opportunity ID attached to event matches the ID retrievable via get_opportunity_detail."""
        scan_res = build_sample_scan_cycle_result()
        with patch.object(self.service.scan_orchestrator, "run_scan_cycle", return_value=scan_res):
            self.service.run_scan()

        ev_detail = self.router.handle_get_event_detail("cev_real_barca_002").data
        surebet = ev_detail["opportunities"]["surebets"][0]
        opp_id = surebet["opportunity_id"]

        opp_detail_res = self.router.handle_get_opportunity_detail(opp_id)
        self.assertEqual(opp_detail_res.status_code, 200)
        self.assertEqual(opp_detail_res.data["opportunity_id"], opp_id)


if __name__ == "__main__":
    unittest.main()
