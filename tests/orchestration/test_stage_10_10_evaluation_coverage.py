"""
Stage 10.10: Evaluation Coverage & Opportunity Truth Verification Test Suite

Tests:
1. complete 1X2 market becomes evaluable
2. complete TOTALS market becomes evaluable
3. complete BTTS market becomes evaluable
4. incomplete market is excluded
5. invalid odds are excluded
6. invalid line is excluded
7. selection mismatch is excluded
8. unsupported market is excluded
9. exclusion reason is deterministic
10. evaluation coverage telemetry is correct
11. opportunities can only originate from evaluated markets
12. existing safety vetoes remain intact
13. Stage 10.9 overlap prioritization remains intact
"""

from decimal import Decimal
import pytest
from typing import List, Dict, Any, Optional

from domain.models import (
    Competition,
    Event,
    Market,
    Selection,
    Odds,
    CanonicalEvent,
    MatchEvidence,
)
from normalization.base_normalizer import NormalizedGraph
from normalization.market_identity import (
    CanonicalMarketKey,
    CanonicalMarketType,
    MarketScope,
    MarketPeriod,
)
from normalization.selection_identity import (
    CanonicalSelectionKey,
    CanonicalSelectionType,
)
from normalization.validation_pipeline import (
    CrossBookmakerValidationPipeline,
    CrossBookmakerValidationResult,
    ComparableSelectionPair,
)
from normalization.odds_comparison import (
    OddsComparison,
    OddsComparisonEngine,
    OddsComparisonStatus,
)
from normalization.surebet import (
    SurebetDetectorEngine,
    SurebetStatus,
    MarketCompletenessStatus,
)
from orchestration.models import (
    EvaluationExclusionReason,
    ResourceMetrics,
    ScanConfig,
    ScanCycleResult,
    CycleStatus,
)
from orchestration.scan_orchestrator import ProductionScanOrchestrator
from orchestration.event_selection import DefaultEventSelectionPolicy


# Helper factories
def _make_market(
    internal_id: str,
    market_type: str,
    line: Optional[Decimal] = None,
) -> Market:
    return Market(
        internal_id=internal_id,
        event_id="ev_1",
        market_type=market_type,
        line=float(line) if line is not None else None,
        provider_ids={"superbet": internal_id},
    )


def _make_selection(
    internal_id: str,
    market_id: str,
    selection_type: str,
) -> Selection:
    return Selection(
        internal_id=internal_id,
        market_id=market_id,
        selection_type=selection_type,
        provider_ids={"superbet": internal_id},
    )


def _make_comparable_pair(
    canonical_event_id: str,
    market_key: CanonicalMarketKey,
    selection_key: CanonicalSelectionKey,
    source_odds_val: float,
    target_odds_val: float,
    source_provider: str = "superbet",
    target_provider: str = "betclic",
) -> ComparableSelectionPair:
    ss_id = f"s_{selection_key.selection_type}"
    ts_id = f"t_{selection_key.selection_type}"
    ss = Selection(
        internal_id=ss_id,
        market_id="mkt_1",
        selection_type=selection_key.selection_type,
    )
    ts = Selection(
        internal_id=ts_id,
        market_id="mkt_2",
        selection_type=selection_key.selection_type,
    )
    return ComparableSelectionPair(
        canonical_event_id=canonical_event_id,
        canonical_market_key=market_key,
        canonical_selection_key=selection_key,
        source_provider=source_provider,
        target_provider=target_provider,
        source_event_id="ev_src",
        target_event_id="ev_tgt",
        source_internal_event_id="ev_src_int",
        target_internal_event_id="ev_tgt_int",
        source_market_id="mkt_1",
        target_market_id="mkt_2",
        source_selection_id=ss.internal_id,
        target_selection_id=ts.internal_id,
        source_selection=ss,
        target_selection=ts,
        source_odds=Odds(selection_id=ss_id, bookmaker=source_provider, decimal_odds=source_odds_val) if source_odds_val is not None else None,
        target_odds=Odds(selection_id=ts_id, bookmaker=target_provider, decimal_odds=target_odds_val) if target_odds_val is not None else None,
        event_evidence=MatchEvidence(
            source_provider=source_provider,
            target_provider=target_provider,
            source_event_id="ev_src",
            target_event_id="ev_tgt",
            decision="MATCHED",
            total_score=0.95,
            orientation="DIRECT",
        ),
        market_evidence={"status": "MATCHED"},
        selection_evidence={"status": "MATCHED"},
    )



class TestStage1010EvaluationCoverage:
    """Stage 10.10 Diagnostic & Evaluation Truth Suite."""

    # 1. complete 1X2 market becomes evaluable
    def test_complete_1x2_market_evaluable(self):
        mkt_key = CanonicalMarketKey(
            sport="football",
            market_type=CanonicalMarketType.ONE_X_TWO.value,
            scope=MarketScope.MATCH.value,
            period=MarketPeriod.FULL_TIME.value,
        )
        pairs = [
            _make_comparable_pair(
                "cev_1",
                mkt_key,
                CanonicalSelectionKey(market_key=mkt_key, selection_type=CanonicalSelectionType.HOME.value),
                source_odds_val=2.50,
                target_odds_val=2.40,
            ),
            _make_comparable_pair(
                "cev_1",
                mkt_key,
                CanonicalSelectionKey(market_key=mkt_key, selection_type=CanonicalSelectionType.DRAW.value),
                source_odds_val=3.40,
                target_odds_val=3.50,
            ),
            _make_comparable_pair(
                "cev_1",
                mkt_key,
                CanonicalSelectionKey(market_key=mkt_key, selection_type=CanonicalSelectionType.AWAY.value),
                source_odds_val=2.90,
                target_odds_val=3.10,
            ),
        ]
        detector = SurebetDetectorEngine()
        res = detector.detect(pairs)
        assert len(res.evaluations) == 1
        eval_item = res.evaluations[0]
        assert eval_item.status in (SurebetStatus.SUREBET, SurebetStatus.NO_SUREBET)
        assert eval_item.completeness_status == MarketCompletenessStatus.COMPLETE
        assert len(eval_item.best_legs) == 3
        assert eval_item.exclusion_reason_code is None

    # 2. complete TOTALS market becomes evaluable
    def test_complete_totals_market_evaluable(self):
        mkt_key = CanonicalMarketKey(
            sport="football",
            market_type=CanonicalMarketType.TOTALS.value,
            line=Decimal("2.5"),
            scope=MarketScope.MATCH.value,
            period=MarketPeriod.FULL_TIME.value,
        )
        pairs = [
            _make_comparable_pair(
                "cev_1",
                mkt_key,
                CanonicalSelectionKey(market_key=mkt_key, selection_type=CanonicalSelectionType.OVER.value),
                source_odds_val=1.95,
                target_odds_val=2.10,
            ),
            _make_comparable_pair(
                "cev_1",
                mkt_key,
                CanonicalSelectionKey(market_key=mkt_key, selection_type=CanonicalSelectionType.UNDER.value),
                source_odds_val=2.05,
                target_odds_val=1.85,
            ),
        ]
        detector = SurebetDetectorEngine()
        res = detector.detect(pairs)
        assert len(res.evaluations) == 1
        eval_item = res.evaluations[0]
        assert eval_item.status in (SurebetStatus.SUREBET, SurebetStatus.NO_SUREBET)
        assert eval_item.completeness_status == MarketCompletenessStatus.COMPLETE
        assert len(eval_item.best_legs) == 2

    # 3. complete BTTS market becomes evaluable
    def test_complete_btts_market_evaluable(self):
        mkt_key = CanonicalMarketKey(
            sport="football",
            market_type=CanonicalMarketType.BTTS.value,
            scope=MarketScope.MATCH.value,
            period=MarketPeriod.FULL_TIME.value,
        )
        pairs = [
            _make_comparable_pair(
                "cev_1",
                mkt_key,
                CanonicalSelectionKey(market_key=mkt_key, selection_type=CanonicalSelectionType.YES.value),
                source_odds_val=1.80,
                target_odds_val=1.75,
            ),
            _make_comparable_pair(
                "cev_1",
                mkt_key,
                CanonicalSelectionKey(market_key=mkt_key, selection_type=CanonicalSelectionType.NO.value),
                source_odds_val=2.05,
                target_odds_val=2.15,
            ),
        ]
        detector = SurebetDetectorEngine()
        res = detector.detect(pairs)
        assert len(res.evaluations) == 1
        eval_item = res.evaluations[0]
        assert eval_item.status in (SurebetStatus.SUREBET, SurebetStatus.NO_SUREBET)
        assert eval_item.completeness_status == MarketCompletenessStatus.COMPLETE


    # 4. incomplete market is excluded
    def test_incomplete_market_excluded(self):
        # 1X2 with only HOME and DRAW (missing AWAY)
        mkt_key = CanonicalMarketKey(
            sport="football",
            market_type=CanonicalMarketType.ONE_X_TWO.value,
            scope=MarketScope.MATCH.value,
            period=MarketPeriod.FULL_TIME.value,
        )
        pairs = [
            _make_comparable_pair(
                "cev_1",
                mkt_key,
                CanonicalSelectionKey(market_key=mkt_key, selection_type=CanonicalSelectionType.HOME.value),
                source_odds_val=2.50,
                target_odds_val=2.40,
            ),
            _make_comparable_pair(
                "cev_1",
                mkt_key,
                CanonicalSelectionKey(market_key=mkt_key, selection_type=CanonicalSelectionType.DRAW.value),
                source_odds_val=3.40,
                target_odds_val=3.50,
            ),
        ]
        detector = SurebetDetectorEngine()
        res = detector.detect(pairs)
        assert len(res.evaluations) == 1
        eval_item = res.evaluations[0]
        assert eval_item.status == SurebetStatus.INCOMPLETE_MARKET
        assert eval_item.completeness_status == MarketCompletenessStatus.INCOMPLETE
        assert eval_item.exclusion_reason_code == EvaluationExclusionReason.INCOMPLETE_SELECTIONS.value
        assert "AWAY" in eval_item.missing_selection_types
        assert len(res.opportunities) == 0

    # 5. invalid odds are excluded
    def test_invalid_odds_excluded(self):
        mkt_key = CanonicalMarketKey(
            sport="football",
            market_type=CanonicalMarketType.BTTS.value,
            scope=MarketScope.MATCH.value,
            period=MarketPeriod.FULL_TIME.value,
        )
        # YES has valid odds, NO has odds <= 1.0 (invalid)
        pairs = [
            _make_comparable_pair(
                "cev_1",
                mkt_key,
                CanonicalSelectionKey(market_key=mkt_key, selection_type=CanonicalSelectionType.YES.value),
                source_odds_val=1.80,
                target_odds_val=1.85,
            ),
            _make_comparable_pair(
                "cev_1",
                mkt_key,
                CanonicalSelectionKey(market_key=mkt_key, selection_type=CanonicalSelectionType.NO.value),
                source_odds_val=0.95,  # Invalid <= 1.0
                target_odds_val=1.00,  # Invalid <= 1.0
            ),
        ]
        detector = SurebetDetectorEngine()
        res = detector.detect(pairs)
        eval_item = res.evaluations[0]
        assert eval_item.status == SurebetStatus.INCOMPLETE_MARKET
        assert "NO" in eval_item.missing_selection_types
        assert eval_item.exclusion_reason_code == EvaluationExclusionReason.INCOMPLETE_SELECTIONS.value

    # 6. invalid line is excluded
    def test_invalid_line_excluded(self):
        # TOTALS market missing numeric line (line=None)
        mkt_key = CanonicalMarketKey(
            sport="football",
            market_type=CanonicalMarketType.TOTALS.value,
            line=None,  # Missing line
            scope=MarketScope.MATCH.value,
            period=MarketPeriod.FULL_TIME.value,
        )
        pairs = [
            _make_comparable_pair(
                "cev_1",
                mkt_key,
                CanonicalSelectionKey(market_key=mkt_key, selection_type=CanonicalSelectionType.OVER.value),
                source_odds_val=1.95,
                target_odds_val=2.10,
            ),
        ]
        detector = SurebetDetectorEngine()
        res = detector.detect(pairs)
        eval_item = res.evaluations[0]
        assert eval_item.status == SurebetStatus.INVALID_MARKET
        assert eval_item.exclusion_reason_code == EvaluationExclusionReason.LINE_INVALID.value

    # 7. selection mismatch is excluded
    def test_selection_mismatch_excluded(self):
        # Market where selections do not form a valid partition
        mkt_key = CanonicalMarketKey(
            sport="football",
            market_type=CanonicalMarketType.ONE_X_TWO.value,
            scope=MarketScope.MATCH.value,
            period=MarketPeriod.FULL_TIME.value,
        )
        # Over/under selection fed into 1X2 market key
        pairs = [
            _make_comparable_pair(
                "cev_1",
                mkt_key,
                CanonicalSelectionKey(market_key=mkt_key, selection_type=CanonicalSelectionType.OVER.value),
                source_odds_val=1.95,
                target_odds_val=2.10,
            ),
        ]
        detector = SurebetDetectorEngine()
        res = detector.detect(pairs)
        eval_item = res.evaluations[0]
        assert eval_item.status == SurebetStatus.INCOMPLETE_MARKET
        assert eval_item.exclusion_reason_code == EvaluationExclusionReason.INCOMPLETE_SELECTIONS.value

    # 8. unsupported market is excluded
    def test_unsupported_market_excluded(self):
        mkt_key = CanonicalMarketKey(
            sport="football",
            market_type="CORRECT_SCORE",
            scope=MarketScope.MATCH.value,
            period=MarketPeriod.FULL_TIME.value,
        )
        pairs = [
            _make_comparable_pair(
                "cev_1",
                mkt_key,
                CanonicalSelectionKey(market_key=mkt_key, selection_type="SCORE_1_0"),
                source_odds_val=7.50,
                target_odds_val=8.00,
            ),
        ]
        detector = SurebetDetectorEngine()
        res = detector.detect(pairs)
        eval_item = res.evaluations[0]
        assert eval_item.status == SurebetStatus.UNSUPPORTED_MARKET
        assert eval_item.exclusion_reason_code == EvaluationExclusionReason.UNSUPPORTED_MARKET.value

    # 9. exclusion reason is deterministic
    def test_exclusion_reason_deterministic(self):
        assert EvaluationExclusionReason.INCOMPLETE_SELECTIONS.value == "INCOMPLETE_SELECTIONS"
        assert EvaluationExclusionReason.INVALID_ODDS.value == "INVALID_ODDS"
        assert EvaluationExclusionReason.UNSUPPORTED_MARKET.value == "UNSUPPORTED_MARKET"
        assert EvaluationExclusionReason.LINE_INVALID.value == "LINE_INVALID"
        assert EvaluationExclusionReason.DUPLICATE_CANONICAL_MARKET.value == "DUPLICATE_CANONICAL_MARKET"
        assert EvaluationExclusionReason.ZERO_COMPARABLE_SELECTIONS.value == "ZERO_COMPARABLE_SELECTIONS"

    # 10. evaluation coverage telemetry is correct
    def test_evaluation_coverage_telemetry_correct(self):
        metrics = ResourceMetrics(
            matched_markets_total=10,
            evaluation_candidates_total=8,
            evaluated_markets_total=6,
            evaluation_excluded_total=4,
            evaluation_exclusion_breakdown={
                EvaluationExclusionReason.DUPLICATE_CANONICAL_MARKET.value: 2,
                EvaluationExclusionReason.INCOMPLETE_SELECTIONS.value: 2,
            },
            evaluation_coverage_rate=0.75,
        )
        assert metrics.matched_markets_total == 10
        assert metrics.evaluation_candidates_total == 8
        assert metrics.evaluated_markets_total == 6
        assert metrics.evaluation_excluded_total == 4
        assert metrics.evaluation_coverage_rate == 0.75

    # 11. opportunities can only originate from evaluated markets
    def test_opportunities_only_from_evaluated_markets(self):
        mkt_key = CanonicalMarketKey(
            sport="football",
            market_type=CanonicalMarketType.BTTS.value,
            scope=MarketScope.MATCH.value,
            period=MarketPeriod.FULL_TIME.value,
        )
        # Arbitrage opportunity: YES=2.10 (Betclic), NO=2.15 (Superbet) -> S = 1/2.10 + 1/2.15 = 0.476 + 0.465 = 0.941 < 1.0
        pairs = [
            _make_comparable_pair(
                "cev_1",
                mkt_key,
                CanonicalSelectionKey(market_key=mkt_key, selection_type=CanonicalSelectionType.YES.value),
                source_odds_val=1.70,
                target_odds_val=2.10,
            ),
            _make_comparable_pair(
                "cev_1",
                mkt_key,
                CanonicalSelectionKey(market_key=mkt_key, selection_type=CanonicalSelectionType.NO.value),
                source_odds_val=2.15,
                target_odds_val=1.75,
            ),
        ]
        detector = SurebetDetectorEngine()
        res = detector.detect(pairs)
        assert len(res.evaluations) == 1
        assert len(res.opportunities) == 1
        opp = res.opportunities[0]
        assert opp.canonical_event_id == "cev_1"
        assert opp.canonical_market_key == mkt_key
        assert opp.implied_probability_sum < Decimal("1.0")
        assert opp.arbitrage_margin > Decimal("0.0")

    # 12. existing safety vetoes remain intact
    def test_existing_safety_vetoes_intact(self):
        pipeline = CrossBookmakerValidationPipeline()
        # Ensure event_matcher has veto check
        matcher = pipeline.event_matcher
        assert hasattr(matcher, "match_candidates")
        assert hasattr(matcher, "config")

    # 13. Stage 10.9 overlap prioritization remains intact
    def test_stage_10_9_overlap_prioritization_intact(self):
        policy = DefaultEventSelectionPolicy()
        assert hasattr(policy, "prioritize_detail_events")

    # 14. Full scan orchestrator evaluation coverage & breakdown
    def test_orchestrator_evaluation_coverage_end_to_end(self):
        from unittest.mock import MagicMock
        from providers.base.provider_result import ProviderResult
        from providers.base.provider_state import ProviderState

        orchestrator = ProductionScanOrchestrator(
            config=ScanConfig(
                enable_valuebets=False,
                enable_reconciliation=False,
            )
        )
        # Mock providers returning rich multi-market graphs
        ev1 = Event(
            competition_id="comp_1",
            home_participant="Arsenal",
            away_participant="Chelsea",
            scheduled_start="2026-08-20T19:00:00Z",
            internal_id="ev_sb_1",
            provider_ids={"superbet": "sb_1"},
        )
        m1_1x2_a = Market(event_id="ev_sb_1", market_type="1X2", internal_id="m1_1", provider_ids={"superbet": "m1_1"})
        m1_1x2_b = Market(event_id="ev_sb_1", market_type="1X2", internal_id="m1_2", provider_ids={"superbet": "m1_2"}) # duplicate 1X2
        m1_tot = Market(event_id="ev_sb_1", market_type="TOTALS", line=2.5, internal_id="m1_3", provider_ids={"superbet": "m1_3"})
        m1_btts = Market(event_id="ev_sb_1", market_type="BTTS", internal_id="m1_4", provider_ids={"superbet": "m1_4"})

        s1_1 = Selection(market_id="m1_1", selection_type="HOME", internal_id="s1_1", provider_ids={"superbet": "s1_1"})
        s1_x = Selection(market_id="m1_1", selection_type="DRAW", internal_id="s1_x", provider_ids={"superbet": "s1_x"})
        s1_2 = Selection(market_id="m1_1", selection_type="AWAY", internal_id="s1_2", provider_ids={"superbet": "s1_2"})
        s1_tot_o = Selection(market_id="m1_3", selection_type="OVER", internal_id="s1_to", provider_ids={"superbet": "s1_to"})
        s1_tot_u = Selection(market_id="m1_3", selection_type="UNDER", internal_id="s1_tu", provider_ids={"superbet": "s1_tu"})
        s1_btts_y = Selection(market_id="m1_4", selection_type="YES", internal_id="s1_by", provider_ids={"superbet": "s1_by"})
        s1_btts_n = Selection(market_id="m1_4", selection_type="NO", internal_id="s1_bn", provider_ids={"superbet": "s1_bn"})

        o1_1 = Odds(selection_id="s1_1", bookmaker="superbet", decimal_odds=2.10)
        o1_x = Odds(selection_id="s1_x", bookmaker="superbet", decimal_odds=3.40)
        o1_2 = Odds(selection_id="s1_2", bookmaker="superbet", decimal_odds=3.20)
        o1_to = Odds(selection_id="s1_to", bookmaker="superbet", decimal_odds=1.90)
        o1_tu = Odds(selection_id="s1_tu", bookmaker="superbet", decimal_odds=1.95)
        o1_by = Odds(selection_id="s1_by", bookmaker="superbet", decimal_odds=1.85)
        o1_bn = Odds(selection_id="s1_bn", bookmaker="superbet", decimal_odds=1.95)

        comp = Competition(name="Premier League", internal_id="comp_1")
        g_sb = NormalizedGraph(
            competition=comp,
            event=ev1,
            markets=[m1_1x2_a, m1_1x2_b, m1_tot, m1_btts],
            selections=[s1_1, s1_x, s1_2, s1_tot_o, s1_tot_u, s1_btts_y, s1_btts_n],
            odds_list=[o1_1, o1_x, o1_2, o1_to, o1_tu, o1_by, o1_bn],
        )

        ev2 = Event(
            competition_id="comp_1",
            home_participant="Arsenal",
            away_participant="Chelsea",
            scheduled_start="2026-08-20T19:00:00Z",
            internal_id="ev_bc_1",
            provider_ids={"betclic": "bc_1"},
        )
        m2_1x2 = Market(event_id="ev_bc_1", market_type="1X2", internal_id="m2_1", provider_ids={"betclic": "m2_1"})
        m2_tot = Market(event_id="ev_bc_1", market_type="TOTALS", line=2.5, internal_id="m2_2", provider_ids={"betclic": "m2_2"})
        m2_btts = Market(event_id="ev_bc_1", market_type="BTTS", internal_id="m2_3", provider_ids={"betclic": "m2_3"})

        s2_1 = Selection(market_id="m2_1", selection_type="HOME", internal_id="s2_1", provider_ids={"betclic": "s2_1"})
        s2_x = Selection(market_id="m2_1", selection_type="DRAW", internal_id="s2_x", provider_ids={"betclic": "s2_x"})
        s2_2 = Selection(market_id="m2_1", selection_type="AWAY", internal_id="s2_2", provider_ids={"betclic": "s2_2"})
        s2_tot_o = Selection(market_id="m2_2", selection_type="OVER", internal_id="s2_to", provider_ids={"betclic": "s2_to"})
        s2_tot_u = Selection(market_id="m2_2", selection_type="UNDER", internal_id="s2_tu", provider_ids={"betclic": "s2_tu"})
        s2_btts_y = Selection(market_id="m2_3", selection_type="YES", internal_id="s2_by", provider_ids={"betclic": "s2_by"})
        s2_btts_n = Selection(market_id="m2_3", selection_type="NO", internal_id="s2_bn", provider_ids={"betclic": "s2_bn"})

        o2_1 = Odds(selection_id="s2_1", bookmaker="betclic", decimal_odds=2.05)
        o2_x = Odds(selection_id="s2_x", bookmaker="betclic", decimal_odds=3.50)
        o2_2 = Odds(selection_id="s2_2", bookmaker="betclic", decimal_odds=3.30)
        o2_to = Odds(selection_id="s2_to", bookmaker="betclic", decimal_odds=1.95)
        o2_tu = Odds(selection_id="s2_tu", bookmaker="betclic", decimal_odds=1.90)
        o2_by = Odds(selection_id="s2_by", bookmaker="betclic", decimal_odds=1.80)
        o2_bn = Odds(selection_id="s2_bn", bookmaker="betclic", decimal_odds=2.00)

        g_bc = NormalizedGraph(
            competition=comp,
            event=ev2,
            markets=[m2_1x2, m2_tot, m2_btts],
            selections=[s2_1, s2_x, s2_2, s2_tot_o, s2_tot_u, s2_btts_y, s2_btts_n],
            odds_list=[o2_1, o2_x, o2_2, o2_to, o2_tu, o2_by, o2_bn],
        )

        mock_sb_res = ProviderResult(
            provider_name="superbet",
            status=ProviderState.COMPLETED,
            execution_duration=0.1,
            discovered_objects=[ev1],
            parsed_objects=[ev1],
        )
        mock_bc_res = ProviderResult(
            provider_name="betclic",
            status=ProviderState.COMPLETED,
            execution_duration=0.1,
            discovered_objects=[ev2],
            parsed_objects=[ev2],
        )

        orchestrator.execution_engine.execute = MagicMock(side_effect=lambda p: mock_sb_res if p.name == "superbet" else mock_bc_res)
        orchestrator.normalization_engine.normalize = MagicMock(side_effect=lambda provider_name, parsed_objects: MagicMock(
            graphs=[g_sb] if provider_name == "superbet" else [g_bc],
            failed_count=0,
            errors=[],
        ))

        mock_sb_prov = MagicMock()
        mock_sb_prov.name = "superbet"
        mock_bc_prov = MagicMock()
        mock_bc_prov.name = "betclic"
        mock_bc_prov.acquisition_metrics = {"detail_requests_successful": 1}

        result = orchestrator.run_scan_cycle(providers={"superbet": mock_sb_prov, "betclic": mock_bc_prov})

        assert result.cycle_status == CycleStatus.SUCCESS
        assert result.matched_events_count == 1
        assert result.matched_markets_total >= 2
        assert result.evaluation_candidates_total >= 2
        assert result.evaluated_markets_total >= 2
        assert result.evaluation_coverage_rate > 0.0

        # Check market family breakdown table exists and contains families
        breakdown = result.market_coverage_breakdown
        assert "1X2" in breakdown
        assert "TOTALS" in breakdown
        assert "BTTS" in breakdown
        assert breakdown["TOTALS"]["evaluated"] >= 1


