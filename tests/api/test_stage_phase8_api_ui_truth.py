"""
Phase 8: API + Observability + UI Flow & Truth Preservation Tests
"""

import unittest
from unittest.mock import MagicMock
from decimal import Decimal
from datetime import datetime, timezone

from api.services import PlatformAPIService, _serialize_scan_cycle_result, _serialize_events_from_scan_result
from core.opportunity_explorer import OpportunityExplorerAdapter
from database.connection import DatabaseManager
from database.config import DatabaseConfig
from orchestration.models import (
    CycleStatus,
    ResourceMetrics,
    ScanCycleResult,
    StageTiming,
    MatchedMarketEvaluationRecord,
    MarketEvaluationState,
    EvaluationExclusionReason,
)
from domain.models import CanonicalEvent
from normalization.validation_pipeline import (
    CanonicalEventValidationRecord,
    CrossBookmakerValidationResult,
    MatchedMarketLineage,
    ComparableSelectionPair,
    MarketCompletenessStatus,
)
from normalization.market_identity import CanonicalMarketKey
from normalization.selection_identity import CanonicalSelectionKey


class TestPhase8ApiUiTruth(unittest.TestCase):
    """Test suite ensuring that backend evaluation and matching truth reaches API/UI without information loss."""

    def setUp(self):
        self.db_manager = DatabaseManager(DatabaseConfig.default_sqlite_in_memory())
        self.db_manager.create_tables()
        self.service = PlatformAPIService(db_manager=self.db_manager)

    def test_01_evaluation_funnel_and_valuebet_rejections_survive_serialization(self):
        """Verify that all funnel metrics, rejection reasons, and valuebet telemetry survive serialization."""
        eval_record = MatchedMarketEvaluationRecord(
            canonical_event_id="ev-stage8-01",
            canonical_market_key="1X2:FULL_TIME:MATCH:no_line",
            state=MarketEvaluationState.REJECTED,
            reason=EvaluationExclusionReason.INVALID_MARKET_IDENTITY,
            market_type="1X2",
            source_provider="superbet",
            target_provider="betclic",
            details={"error": "missing player_name"},
        )

        cycle_res = ScanCycleResult(
            execution_id="scan_phase8_001",
            cycle_status=CycleStatus.SUCCESS,
            started_at=datetime.now(timezone.utc).isoformat(),
            completed_at=datetime.now(timezone.utc).isoformat(),
            duration_seconds=42.5,
            stage_timings=StageTiming(
                acquisition_seconds=10.0,
                normalization_seconds=5.0,
                matching_seconds=10.0,
                detection_seconds=12.0,
                lifecycle_seconds=4.0,
                dispatch_seconds=1.5,
                total_duration_seconds=42.5,
            ),
            resource_metrics=ResourceMetrics(
                total_http_requests=50,
                detail_http_requests=15,
                events_discovered=200,
                events_selected=100,
                events_parsed=200,
                normalized_graphs=200,
                matched_events=40,
                markets_evaluated=120,
                surebet_candidates=150,
                valid_surebets=2,
                rejected_markets_total=20,
                not_evaluated_markets_total=10,
                rejection_reasons_breakdown={
                    "INVALID_MARKET_IDENTITY": 5,
                    "INCOMPLETE_SELECTIONS": 10,
                    "LINE_INVALID": 5,
                },
                valuebet_rejection_reasons_breakdown={
                    "STALE_REFERENCE_DATA": 3,
                    "INCOMPLETE_REFERENCE_MARKET": 2,
                },
            ),
            discovered_events_count=200,
            parsed_events_count=200,
            normalized_graphs_count=200,
            matched_events_count=40,
            markets_matched_count=150,
            markets_evaluated_count=120,
            surebet_candidates_count=150,
            valid_surebets_count=2,
            rejected_markets_count=20,
            not_evaluated_markets_count=10,
            rejection_reasons_breakdown={
                "INVALID_MARKET_IDENTITY": 5,
                "INCOMPLETE_SELECTIONS": 10,
                "LINE_INVALID": 5,
            },
            valuebet_rejection_reasons_breakdown={
                "STALE_REFERENCE_DATA": 3,
                "INCOMPLETE_REFERENCE_MARKET": 2,
            },
            market_evaluation_records=[eval_record],
        )

        serialized = _serialize_scan_cycle_result(cycle_res)

        # Invariant checks for evaluation funnel
        funnel = serialized.get("evaluation_funnel", {})
        self.assertEqual(funnel.get("discovered_events"), 200)
        self.assertEqual(funnel.get("matched_events"), 40)
        self.assertEqual(funnel.get("matched_markets"), 150)
        self.assertEqual(funnel.get("evaluated_markets"), 120)
        self.assertEqual(funnel.get("valid_surebets"), 2)
        self.assertEqual(funnel.get("rejected_markets"), 20)
        self.assertEqual(funnel.get("not_evaluated_markets"), 10)

        # Invariant checks for rejection breakdown
        sb_rejs = funnel.get("rejection_reasons_breakdown", {})
        self.assertEqual(sb_rejs.get("INVALID_MARKET_IDENTITY"), 5)
        self.assertEqual(sb_rejs.get("INCOMPLETE_SELECTIONS"), 10)

        vb_rejs = funnel.get("valuebet_rejection_reasons_breakdown", {})
        self.assertEqual(vb_rejs.get("STALE_REFERENCE_DATA"), 3)
        self.assertEqual(vb_rejs.get("INCOMPLETE_REFERENCE_MARKET"), 2)

        # Invariant check for market evaluation records
        records = serialized.get("market_evaluation_records", [])
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["canonical_event_id"], "ev-stage8-01")
        self.assertEqual(records[0]["state"], "REJECTED")
        self.assertEqual(records[0]["reason"], "INVALID_MARKET_IDENTITY")

    def test_02_event_details_and_market_completeness_survive_serialization(self):
        """Verify that completeness_status, is_evaluation_eligible, and exclusion_reasons survive in event detail."""
        ce = CanonicalEvent(
            canonical_event_id="ce-test-001",
            home_team="Arsenal",
            away_team="Chelsea",
            sport="football",
            scheduled_start=datetime.now(timezone.utc).isoformat(),
            sources={
                "superbet": None,
                "betclic": None,
            },
        )

        m_key = CanonicalMarketKey(market_type="1X2", period="FULL_TIME", scope="MATCH", line=None)
        sel_key_1 = CanonicalSelectionKey(market_key=m_key, selection_type="HOME")
        sel_key_2 = CanonicalSelectionKey(market_key=m_key, selection_type="AWAY")

        class MockOdds:
            decimal_odds = Decimal("2.10")

        class MockSel:
            participant = None

        pair1 = ComparableSelectionPair(
            canonical_event_id="ce-test-001",
            canonical_market_key=m_key,
            canonical_selection_key=sel_key_1,
            source_provider="superbet",
            target_provider="betclic",
            source_event_id="e1",
            target_event_id="e2",
            source_internal_event_id="ie1",
            target_internal_event_id="ie2",
            source_market_id="m-sb-1",
            target_market_id="m-bc-1",
            source_selection_id="s1",
            target_selection_id="t1",
            source_selection=MockSel(),
            target_selection=MockSel(),
            source_odds=MockOdds(),
            target_odds=MockOdds(),
        )
        pair2 = ComparableSelectionPair(
            canonical_event_id="ce-test-001",
            canonical_market_key=m_key,
            canonical_selection_key=sel_key_2,
            source_provider="superbet",
            target_provider="betclic",
            source_event_id="e1",
            target_event_id="e2",
            source_internal_event_id="ie1",
            target_internal_event_id="ie2",
            source_market_id="m-sb-1",
            target_market_id="m-bc-1",
            source_selection_id="s2",
            target_selection_id="t2",
            source_selection=MockSel(),
            target_selection=MockSel(),
            source_odds=MockOdds(),
            target_odds=MockOdds(),
        )

        lineage = MatchedMarketLineage(
            canonical_event_id="ce-test-001",
            canonical_market_key=m_key,
            source_market_id="m-sb-1",
            target_market_id="m-bc-1",
            source_market=None,
            target_market=None,
            market_decision=None,
            selection_batch_result=None,
            comparable_selections=[pair1, pair2],
            completeness_status=MarketCompletenessStatus.PARTIAL,
            is_evaluation_eligible=False,
            exclusion_reasons=("INCOMPLETE_SELECTIONS: missing DRAW outcome",),
        )

        record = CanonicalEventValidationRecord(
            canonical_event=ce,
            market_batch_result=None,
            matched_markets=[lineage],
        )

        vr = CrossBookmakerValidationResult(
            canonical_events=[ce],
            event_validation_records=[record],
        )

        scan_result = ScanCycleResult(
            execution_id="scan_phase8_ev_001",
            cycle_status=CycleStatus.SUCCESS,
            started_at=datetime.now(timezone.utc).isoformat(),
            completed_at=datetime.now(timezone.utc).isoformat(),
            duration_seconds=10.0,
            stage_timings=StageTiming(),
            resource_metrics=ResourceMetrics(),
            validation_result=vr,
        )

        summaries, details_map = _serialize_events_from_scan_result(scan_result)

        self.assertEqual(len(summaries), 1)
        ev_detail = details_map.get("ce-test-001")
        self.assertIsNotNone(ev_detail)

        markets = ev_detail.get("markets", [])
        self.assertEqual(len(markets), 1)
        m = markets[0]
        self.assertEqual(m["canonical_market_key"], m_key.to_key_string())
        self.assertEqual(m["market_type"], "1X2")
        self.assertEqual(m["completeness_status"], "PARTIAL")
        self.assertEqual(m["is_evaluation_eligible"], False)
        self.assertIn("INCOMPLETE_SELECTIONS: missing DRAW outcome", m["exclusion_reasons"])
        self.assertEqual(len(m["selections"]), 2)

    def test_03_zero_selection_market_is_omitted_from_comparison_matrix(self):
        """Verify that a market with zero comparable selections is omitted from active comparison view."""
        ce = CanonicalEvent(
            canonical_event_id="ce-empty-002",
            home_team="Liverpool",
            away_team="Man City",
            sport="football",
            scheduled_start=datetime.now(timezone.utc).isoformat(),
            sources={"superbet": None, "betclic": None},
        )
        m_key = CanonicalMarketKey(market_type="PLAYER_SHOTS", period="FULL_TIME", scope="PLAYER", line=Decimal("1.5"), player_name="Salah")

        lineage_empty = MatchedMarketLineage(
            canonical_event_id="ce-empty-002",
            canonical_market_key=m_key,
            source_market_id="m-sb-empty",
            target_market_id="m-bc-empty",
            source_market=None,
            target_market=None,
            market_decision=None,
            selection_batch_result=None,
            comparable_selections=[],
            completeness_status=MarketCompletenessStatus.INCOMPLETE,
            is_evaluation_eligible=False,
            exclusion_reasons=("ZERO_COMPARABLE_SELECTIONS",),
        )

        record = CanonicalEventValidationRecord(
            canonical_event=ce,
            market_batch_result=None,
            matched_markets=[lineage_empty],
        )

        vr = CrossBookmakerValidationResult(
            canonical_events=[ce],
            event_validation_records=[record],
        )

        scan_result = ScanCycleResult(
            execution_id="scan_phase8_empty_002",
            cycle_status=CycleStatus.SUCCESS,
            started_at=datetime.now(timezone.utc).isoformat(),
            completed_at=datetime.now(timezone.utc).isoformat(),
            duration_seconds=5.0,
            stage_timings=StageTiming(),
            resource_metrics=ResourceMetrics(),
            validation_result=vr,
        )

        summaries, details_map = _serialize_events_from_scan_result(scan_result)
        ev_detail = details_map.get("ce-empty-002")
        self.assertIsNotNone(ev_detail)
        # Verify that 0-selection market was omitted from markets list to avoid phantom empty table
        self.assertEqual(len(ev_detail.get("markets", [])), 0)

    def test_04_unified_opportunity_explorer_adapter_preserves_evaluation_truth(self):
        """Verify that OpportunityExplorerAdapter preserves all EV and ROI math without recalculation."""
        val_cand_dict = {
            "candidate_id": "vb-truth-001",
            "canonical_event_id": "ev-val-001",
            "event_name": "Bayern Munich vs Dortmund",
            "sport": "football",
            "competition_name": "Bundesliga",
            "market_type": "1X2",
            "selection_type": "HOME",
            "bookmaker": "superbet",
            "bookmaker_odds": 2.40,
            "reference_bookmaker": "pinnacle",
            "reference_source": "The-Odds-API",
            "reference_raw_odds": 2.00,
            "fair_probability": 0.5000,
            "fair_odds": 2.00,
            "value_percent": 20.0,
            "expected_value": 0.2000,
            "event": {"home_participant": "Bayern Munich", "away_participant": "Dortmund"},
        }

        dto = OpportunityExplorerAdapter.from_valuebet(val_cand_dict)
        self.assertEqual(dto.type, "VALUEBET")
        self.assertEqual(dto.gross_ev_pct, 20.0)
        self.assertEqual(dto.execution_odds, 2.40)
        self.assertEqual(dto.fair_odds, 2.00)
        self.assertEqual(dto.model_probability_pct, 50.0)
        self.assertEqual(dto.best_bookmaker, "superbet")

    def test_05_unmatched_event_player_props_preserve_scope_and_skip_empty(self):
        """Verify that unmatched events preserve PLAYER scope, player_name, and skip empty selection markets."""
        from normalization.base_normalizer import NormalizedGraph
        from domain.models import Event, Market, Selection, Odds, Competition

        ev = Event(
            internal_id="ev-unm-001",
            home_participant="Barcelona",
            away_participant="Real Madrid",
            scheduled_start=datetime.now(timezone.utc).isoformat(),
            competition_id="comp-1",
        )
        comp = Competition(
            internal_id="comp-1",
            name="La Liga",
            sport="football",
        )

        mkt_with_sel = Market(
            internal_id="mkt-lewy-1",
            event_id="ev-unm-001",
            market_type="PLAYER_SHOTS",
            line=Decimal("1.5"),
            metadata={"scope": "PLAYER", "player_name": "Robert Lewandowski", "metric": "SHOTS"},
        )
        mkt_empty = Market(
            internal_id="mkt-empty-2",
            event_id="ev-unm-001",
            market_type="PLAYER_FOULS",
            line=Decimal("0.5"),
            metadata={"scope": "PLAYER", "player_name": "Pedri", "metric": "FOULS"},
        )

        sel = Selection(
            internal_id="sel-lewy-1",
            market_id="mkt-lewy-1",
            selection_type="OVER",
            line=Decimal("1.5"),
            participant="Robert Lewandowski",
        )
        odd = Odds(
            selection_id="sel-lewy-1",
            bookmaker="superbet",
            decimal_odds=Decimal("1.85"),
        )

        graph = NormalizedGraph(
            event=ev,
            competition=comp,
            markets=[mkt_with_sel, mkt_empty],
            selections=[sel],
            odds_list=[odd],
        )

        scan_result = ScanCycleResult(
            execution_id="scan_phase8_unm_005",
            cycle_status=CycleStatus.SUCCESS,
            started_at=datetime.now(timezone.utc).isoformat(),
            completed_at=datetime.now(timezone.utc).isoformat(),
            duration_seconds=3.0,
            stage_timings=StageTiming(),
            resource_metrics=ResourceMetrics(),
            normalization_results={"superbet": MagicMock(graphs=[graph])},
            validation_result=CrossBookmakerValidationResult(),
        )

        summaries, details_map = _serialize_events_from_scan_result(scan_result)
        self.assertEqual(len(summaries), 1)
        detail = details_map.get("ev-unm-001")
        self.assertIsNotNone(detail)

        # Empty market mkt_empty is skipped
        mkts = detail.get("markets", [])
        self.assertEqual(len(mkts), 1)
        m = mkts[0]
        self.assertEqual(m["market_type"], "PLAYER_SHOTS")
        self.assertEqual(m["scope"], "PLAYER")
        self.assertEqual(m["completeness_status"], "PARTIAL")
        self.assertIn("robert_lewandowski", m["canonical_market_key"])
        self.assertEqual(len(m["selections"]), 1)
        self.assertEqual(m["selections"][0]["participant"], "Robert Lewandowski")
        self.assertEqual(m["selections"][0]["odds"]["superbet"], 1.85)


if __name__ == "__main__":
    unittest.main()
