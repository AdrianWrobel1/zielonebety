"""
Stage 13: Evaluation Coverage & Opportunity Pipeline Verification Test Suite

Tests:
1. Every matched market receives a terminal evaluation state (EVALUATED, OPPORTUNITY, REJECTED, NOT_EVALUATED).
2. Machine-readable rejection/not-evaluation reasons are preserved deterministically.
3. Reference-only bookmakers (bet365, unibet) cannot produce surebets.
4. Telemetry counts reconcile mathematically:
   matched_markets = evaluated_markets + rejected_markets + not_evaluated_markets
5. No duplicate opportunity or rejection records exist.
6. Semantic false-positive protections (period, scope, participant, line) remain authoritative.
7. Controlled scan execution verifies the complete pipeline funnel and exact telemetry reconciliation.
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
    MatchedMarketLineage,
    CanonicalEventValidationRecord,
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
    CycleStatus,
    EvaluationExclusionReason,
    MarketEvaluationState,
    MatchedMarketEvaluationRecord,
    ResourceMetrics,
    ScanConfig,
    ScanCycleResult,
)
from orchestration.scan_orchestrator import ProductionScanOrchestrator
from providers.base.base_provider import BaseProvider
from providers.base.models import ProviderMetadata, ValidationReport
from providers.base.provider_context import ProviderContext


def _make_market(
    internal_id: str,
    market_type: str,
    line: Optional[float] = None,
    provider: str = "superbet",
) -> Market:
    return Market(
        internal_id=internal_id,
        event_id="ev_1",
        market_type=market_type,
        line=line,
        provider_ids={provider: internal_id},
    )


def _make_selection(
    internal_id: str,
    market_id: str,
    selection_type: str,
    provider: str = "superbet",
) -> Selection:
    return Selection(
        internal_id=internal_id,
        market_id=market_id,
        selection_type=selection_type,
        provider_ids={provider: internal_id},
    )


def _make_comparable_pair(
    canonical_event_id: str,
    market_key: CanonicalMarketKey,
    selection_type: str,
    source_provider: str = "superbet",
    target_provider: str = "betclic",
    source_odds_val: Optional[Decimal] = Decimal("2.10"),
    target_odds_val: Optional[Decimal] = Decimal("2.05"),
) -> ComparableSelectionPair:
    sel_key = CanonicalSelectionKey(
        market_key=market_key,
        selection_type=selection_type,
    )
    s_odds = (
        Odds(
            selection_id="s_sel",
            bookmaker=source_provider,
            decimal_odds=float(source_odds_val),
        )
        if source_odds_val
        else None
    )
    t_odds = (
        Odds(
            selection_id="t_sel",
            bookmaker=target_provider,
            decimal_odds=float(target_odds_val),
        )
        if target_odds_val
        else None
    )

    ev_ev = MatchEvidence(
        source_provider=source_provider,
        target_provider=target_provider,
        source_event_id="src_ev_1",
        target_event_id="tgt_ev_1",
        decision="MATCHED",
        total_score=0.98,
        orientation="REGULAR",
        evidence={"source_name": "Arsenal vs Chelsea", "home_team": "Arsenal", "away_team": "Chelsea"},
        home_team="Arsenal",
        away_team="Chelsea",
    )

    return ComparableSelectionPair(
        canonical_event_id=canonical_event_id,
        canonical_market_key=market_key,
        canonical_selection_key=sel_key,
        source_provider=source_provider,
        target_provider=target_provider,
        source_event_id="src_ev_1",
        target_event_id="tgt_ev_1",
        source_internal_event_id="s_ev_int",
        target_internal_event_id="t_ev_int",
        source_market_id="s_mkt_1",
        target_market_id="t_mkt_1",
        source_selection_id=f"s_sel_{selection_type}",
        target_selection_id=f"t_sel_{selection_type}",
        source_selection=_make_selection("s_sel_1", "s_mkt_1", selection_type, provider=source_provider),
        target_selection=_make_selection("t_sel_1", "t_mkt_1", selection_type, provider=target_provider),
        source_odds=s_odds,
        target_odds=t_odds,
        event_evidence=ev_ev,
    )


class MockProvider(BaseProvider):
    def __init__(self, name: str, parsed_items: Optional[List[Any]] = None):
        ctx = ProviderContext(provider_name=name)
        meta = ProviderMetadata(name=name, code=name)
        super().__init__(context=ctx, metadata=meta)
        self._parsed_items = parsed_items if parsed_items is not None else []

    def discover(self) -> List[Any]:
        return [{"id": f"disc_{i}"} for i in range(len(self._parsed_items))]

    def fetch(self, discovery_items: List[Any]) -> List[Any]:
        return [{"raw": "data"} for _ in discovery_items]

    def parse(self, raw_data: List[Any]) -> List[Any]:
        return list(self._parsed_items)

    def validate(self, parsed_data: List[Any]) -> ValidationReport:
        return ValidationReport(valid_objects=len(parsed_data), invalid_objects=0, is_valid=True)


class MockNormalizerWrapper:
    def __init__(self, graphs: List[NormalizedGraph]):
        self.graphs = graphs

    def normalize_event(self, obj: Any) -> NormalizedGraph:
        if isinstance(obj, NormalizedGraph):
            return obj
        if isinstance(obj, dict) and "graph" in obj:
            return obj["graph"]
        return obj


class TestStage13EvaluationPipeline:
    """Test suite verifying Stage 13 Evaluation Coverage & Opportunity Pipeline."""

    def test_complete_market_receives_evaluated_state_when_no_surebet(self):
        """A complete 1X2 market evaluated without arbitrage receives EVALUATED state."""
        mkt_key = CanonicalMarketKey(
            market_type=CanonicalMarketType.ONE_X_TWO.value,
            period=MarketPeriod.FULL_TIME.value,
            scope=MarketScope.MATCH.value,
        )
        pairs = [
            _make_comparable_pair("ev_1", mkt_key, CanonicalSelectionType.HOME.value, source_odds_val=Decimal("2.00"), target_odds_val=Decimal("1.95")),
            _make_comparable_pair("ev_1", mkt_key, CanonicalSelectionType.DRAW.value, source_odds_val=Decimal("3.30"), target_odds_val=Decimal("3.40")),
            _make_comparable_pair("ev_1", mkt_key, CanonicalSelectionType.AWAY.value, source_odds_val=Decimal("3.60"), target_odds_val=Decimal("3.50")),
        ]

        engine = SurebetDetectorEngine()
        result = engine.detect(pairs)

        assert len(result.evaluations) == 1
        evaluation = result.evaluations[0]
        assert evaluation.status == SurebetStatus.NO_SUREBET
        assert evaluation.completeness_status == MarketCompletenessStatus.COMPLETE
        assert evaluation.opportunity is None

    def test_complete_market_receives_opportunity_state_when_surebet(self):
        """A complete market evaluated with S < 1.0 produces an opportunity."""
        mkt_key = CanonicalMarketKey(
            market_type=CanonicalMarketType.BTTS.value,
            period=MarketPeriod.FULL_TIME.value,
            scope=MarketScope.MATCH.value,
        )
        pairs = [
            _make_comparable_pair("ev_1", mkt_key, CanonicalSelectionType.YES.value, source_odds_val=Decimal("2.60"), target_odds_val=Decimal("1.80")),
            _make_comparable_pair("ev_1", mkt_key, CanonicalSelectionType.NO.value, source_odds_val=Decimal("1.80"), target_odds_val=Decimal("2.10")),
        ]

        engine = SurebetDetectorEngine()
        result = engine.detect(pairs)

        assert len(result.evaluations) == 1
        evaluation = result.evaluations[0]
        assert evaluation.status == SurebetStatus.SUREBET
        assert evaluation.opportunity is not None
        assert evaluation.opportunity.arbitrage_margin > Decimal("0")

    def test_incomplete_market_receives_rejected_state_with_reason(self):
        """A market missing a required outcome (e.g. 1X2 with only HOME and DRAW) is REJECTED."""
        mkt_key = CanonicalMarketKey(
            market_type=CanonicalMarketType.ONE_X_TWO.value,
            period=MarketPeriod.FULL_TIME.value,
            scope=MarketScope.MATCH.value,
        )
        pairs = [
            _make_comparable_pair("ev_1", mkt_key, CanonicalSelectionType.HOME.value, source_odds_val=Decimal("2.10"), target_odds_val=Decimal("2.00")),
            _make_comparable_pair("ev_1", mkt_key, CanonicalSelectionType.DRAW.value, source_odds_val=Decimal("3.30"), target_odds_val=Decimal("3.40")),
        ]

        engine = SurebetDetectorEngine()
        result = engine.detect(pairs)

        assert len(result.evaluations) == 1
        evaluation = result.evaluations[0]
        assert evaluation.status == SurebetStatus.INCOMPLETE_MARKET
        assert evaluation.exclusion_reason_code == "INCOMPLETE_SELECTIONS"
        assert "AWAY" in evaluation.missing_selection_types

    def test_reference_only_bookmakers_cannot_produce_surebets(self):
        """Bet365 and Unibet are reference-only and cannot form surebet legs."""
        mkt_key = CanonicalMarketKey(
            market_type=CanonicalMarketType.BTTS.value,
            period=MarketPeriod.FULL_TIME.value,
            scope=MarketScope.MATCH.value,
        )
        pairs = [
            _make_comparable_pair("ev_1", mkt_key, CanonicalSelectionType.YES.value, source_provider="bet365", target_provider="unibet", source_odds_val=Decimal("2.50"), target_odds_val=Decimal("1.80")),
            _make_comparable_pair("ev_1", mkt_key, CanonicalSelectionType.NO.value, source_provider="bet365", target_provider="unibet", source_odds_val=Decimal("1.80"), target_odds_val=Decimal("2.50")),
        ]

        engine = SurebetDetectorEngine()
        result = engine.detect(pairs)

        assert len(result.evaluations) == 1
        evaluation = result.evaluations[0]
        assert evaluation.status == SurebetStatus.INCOMPLETE_MARKET
        assert len(result.opportunities) == 0

    def test_mathematical_telemetry_reconciliation(self):
        """Reconciliation invariant: matched_markets = evaluated + rejected + not_evaluated."""
        comp = Competition(internal_id="comp_1", name="Premier League")

        ev_sb = Event(competition_id="comp_1", home_participant="Arsenal", away_participant="Chelsea", internal_id="ev_sb_1", provider_ids={"superbet": "sb_1"})
        ev_bc = Event(competition_id="comp_1", home_participant="Arsenal", away_participant="Chelsea", internal_id="ev_bc_1", provider_ids={"betclic": "bc_1"})

        m_1x2_sb = Market(event_id="ev_sb_1", market_type="1X2", internal_id="m_1x2_sb", provider_ids={"superbet": "m_1x2_sb"})
        m_1x2_bc = Market(event_id="ev_bc_1", market_type="1X2", internal_id="m_1x2_bc", provider_ids={"betclic": "m_1x2_bc"})

        s_1x2_h_sb = Selection(market_id="m_1x2_sb", selection_type="HOME", internal_id="s_1x2_h_sb", provider_ids={"superbet": "s_1x2_h_sb"})
        s_1x2_d_sb = Selection(market_id="m_1x2_sb", selection_type="DRAW", internal_id="s_1x2_d_sb", provider_ids={"superbet": "s_1x2_d_sb"})
        s_1x2_a_sb = Selection(market_id="m_1x2_sb", selection_type="AWAY", internal_id="s_1x2_a_sb", provider_ids={"superbet": "s_1x2_a_sb"})

        s_1x2_h_bc = Selection(market_id="m_1x2_bc", selection_type="HOME", internal_id="s_1x2_h_bc", provider_ids={"betclic": "s_1x2_h_bc"})
        s_1x2_d_bc = Selection(market_id="m_1x2_bc", selection_type="DRAW", internal_id="s_1x2_d_bc", provider_ids={"betclic": "s_1x2_d_bc"})
        s_1x2_a_bc = Selection(market_id="m_1x2_bc", selection_type="AWAY", internal_id="s_1x2_a_bc", provider_ids={"betclic": "s_1x2_a_bc"})

        odds_sb = [
            Odds(selection_id="s_1x2_h_sb", bookmaker="superbet", decimal_odds=2.00),
            Odds(selection_id="s_1x2_d_sb", bookmaker="superbet", decimal_odds=3.30),
            Odds(selection_id="s_1x2_a_sb", bookmaker="superbet", decimal_odds=3.60),
        ]
        odds_bc = [
            Odds(selection_id="s_1x2_h_bc", bookmaker="betclic", decimal_odds=1.95),
            Odds(selection_id="s_1x2_d_bc", bookmaker="betclic", decimal_odds=3.40),
            Odds(selection_id="s_1x2_a_bc", bookmaker="betclic", decimal_odds=3.50),
        ]

        m_btts_sb = Market(event_id="ev_sb_1", market_type="BTTS", internal_id="m_btts_sb", provider_ids={"superbet": "m_btts_sb"})
        m_btts_bc = Market(event_id="ev_bc_1", market_type="BTTS", internal_id="m_btts_bc", provider_ids={"betclic": "m_btts_bc"})
        s_btts_y_sb = Selection(market_id="m_btts_sb", selection_type="YES", internal_id="s_btts_y_sb", provider_ids={"superbet": "s_btts_y_sb"})
        s_btts_n_sb = Selection(market_id="m_btts_sb", selection_type="NO", internal_id="s_btts_n_sb", provider_ids={"superbet": "s_btts_n_sb"})
        s_btts_y_bc = Selection(market_id="m_btts_bc", selection_type="YES", internal_id="s_btts_y_bc", provider_ids={"betclic": "s_btts_y_bc"})
        s_btts_n_bc = Selection(market_id="m_btts_bc", selection_type="NO", internal_id="s_btts_n_bc", provider_ids={"betclic": "s_btts_n_bc"})

        odds_sb.extend([
            Odds(selection_id="s_btts_y_sb", bookmaker="superbet", decimal_odds=2.60),
            Odds(selection_id="s_btts_n_sb", bookmaker="superbet", decimal_odds=1.80),
        ])
        odds_bc.extend([
            Odds(selection_id="s_btts_y_bc", bookmaker="betclic", decimal_odds=1.80),
            Odds(selection_id="s_btts_n_bc", bookmaker="betclic", decimal_odds=2.10),
        ])

        g_sb = NormalizedGraph(
            competition=comp,
            event=ev_sb,
            markets=[m_1x2_sb, m_btts_sb],
            selections=[s_1x2_h_sb, s_1x2_d_sb, s_1x2_a_sb, s_btts_y_sb, s_btts_n_sb],
            odds_list=odds_sb,
        )
        g_bc = NormalizedGraph(
            competition=comp,
            event=ev_bc,
            markets=[m_1x2_bc, m_btts_bc],
            selections=[s_1x2_h_bc, s_1x2_d_bc, s_1x2_a_bc, s_btts_y_bc, s_btts_n_bc],
            odds_list=odds_bc,
        )

        prov_sb = MockProvider("superbet", parsed_items=[g_sb])
        prov_bc = MockProvider("betclic", parsed_items=[g_bc])

        orchestrator = ProductionScanOrchestrator(
            config=ScanConfig(providers=("superbet", "betclic"), enable_valuebets=False)
        )
        orchestrator.normalization_engine.register_normalizer("superbet", MockNormalizerWrapper([g_sb]))
        orchestrator.normalization_engine.register_normalizer("betclic", MockNormalizerWrapper([g_bc]))

        result = orchestrator.run_scan_cycle(providers={"superbet": prov_sb, "betclic": prov_bc})

        matched = result.resource_metrics.matched_markets_total
        evaluated = result.resource_metrics.evaluated_markets_total
        rejected = result.resource_metrics.rejected_markets_total
        not_evaluated = result.resource_metrics.not_evaluated_markets_total

        assert matched == evaluated + rejected + not_evaluated
        assert len(result.resource_metrics.market_evaluation_records) == matched

        for rec in result.resource_metrics.market_evaluation_records:
            assert isinstance(rec.state, MarketEvaluationState)
            if rec.state in (MarketEvaluationState.REJECTED, MarketEvaluationState.NOT_EVALUATED):
                assert isinstance(rec.reason, EvaluationExclusionReason)
            else:
                assert rec.reason is None

        assert result.detected_opportunities_count == 1
        assert result.valid_surebets_count == 1

    def test_no_duplicate_opportunity_or_rejection_records(self):
        """Verifies duplicate market keys collapse with DUPLICATE_CANONICAL_MARKET reason."""
        comp = Competition(internal_id="comp_1", name="Premier League")
        ev_sb = Event(competition_id="comp_1", home_participant="Arsenal", away_participant="Chelsea", internal_id="ev_sb_2", provider_ids={"superbet": "sb_2"})
        ev_bc = Event(competition_id="comp_1", home_participant="Arsenal", away_participant="Chelsea", internal_id="ev_bc_2", provider_ids={"betclic": "bc_2"})
        ev_oa = Event(competition_id="comp_1", home_participant="Arsenal", away_participant="Chelsea", internal_id="ev_oa_2", provider_ids={"odds_api": "oa_2"})

        m_1x2_sb = Market(event_id="ev_sb_2", market_type="1X2", internal_id="m_1x2_sb", provider_ids={"superbet": "m_1x2_sb"})
        m_1x2_bc = Market(event_id="ev_bc_2", market_type="1X2", internal_id="m_1x2_bc", provider_ids={"betclic": "m_1x2_bc"})
        m_1x2_oa = Market(event_id="ev_oa_2", market_type="1X2", internal_id="m_1x2_oa", provider_ids={"odds_api": "m_1x2_oa"})

        s_h_sb = Selection(market_id="m_1x2_sb", selection_type="HOME", internal_id="s_h_sb", provider_ids={"superbet": "s_h_sb"})
        s_d_sb = Selection(market_id="m_1x2_sb", selection_type="DRAW", internal_id="s_d_sb", provider_ids={"superbet": "s_d_sb"})
        s_a_sb = Selection(market_id="m_1x2_sb", selection_type="AWAY", internal_id="s_a_sb", provider_ids={"superbet": "s_a_sb"})

        s_h_bc = Selection(market_id="m_1x2_bc", selection_type="HOME", internal_id="s_h_bc", provider_ids={"betclic": "s_h_bc"})
        s_d_bc = Selection(market_id="m_1x2_bc", selection_type="DRAW", internal_id="s_d_bc", provider_ids={"betclic": "s_d_bc"})
        s_a_bc = Selection(market_id="m_1x2_bc", selection_type="AWAY", internal_id="s_a_bc", provider_ids={"betclic": "s_a_bc"})

        s_h_oa = Selection(market_id="m_1x2_oa", selection_type="HOME", internal_id="s_h_oa", provider_ids={"odds_api": "s_h_oa"})
        s_d_oa = Selection(market_id="m_1x2_oa", selection_type="DRAW", internal_id="s_d_oa", provider_ids={"odds_api": "s_d_oa"})
        s_a_oa = Selection(market_id="m_1x2_oa", selection_type="AWAY", internal_id="s_a_oa", provider_ids={"odds_api": "s_a_oa"})

        odds_sb = [
            Odds(selection_id="s_h_sb", bookmaker="superbet", decimal_odds=2.00),
            Odds(selection_id="s_d_sb", bookmaker="superbet", decimal_odds=3.30),
            Odds(selection_id="s_a_sb", bookmaker="superbet", decimal_odds=3.60),
        ]
        odds_bc = [
            Odds(selection_id="s_h_bc", bookmaker="betclic", decimal_odds=1.95),
            Odds(selection_id="s_d_bc", bookmaker="betclic", decimal_odds=3.40),
            Odds(selection_id="s_a_bc", bookmaker="betclic", decimal_odds=3.50),
        ]
        odds_oa = [
            Odds(selection_id="s_h_oa", bookmaker="odds_api", decimal_odds=2.05),
            Odds(selection_id="s_d_oa", bookmaker="odds_api", decimal_odds=3.35),
            Odds(selection_id="s_a_oa", bookmaker="odds_api", decimal_odds=3.55),
        ]

        g_sb = NormalizedGraph(
            competition=comp,
            event=ev_sb,
            markets=[m_1x2_sb],
            selections=[s_h_sb, s_d_sb, s_a_sb],
            odds_list=odds_sb,
        )
        g_bc = NormalizedGraph(
            competition=comp,
            event=ev_bc,
            markets=[m_1x2_bc],
            selections=[s_h_bc, s_d_bc, s_a_bc],
            odds_list=odds_bc,
        )
        g_oa = NormalizedGraph(
            competition=comp,
            event=ev_oa,
            markets=[m_1x2_oa],
            selections=[s_h_oa, s_d_oa, s_a_oa],
            odds_list=odds_oa,
        )

        prov_sb = MockProvider("superbet", parsed_items=[g_sb])
        prov_bc = MockProvider("betclic", parsed_items=[g_bc])
        prov_oa = MockProvider("odds_api", parsed_items=[g_oa])

        orchestrator = ProductionScanOrchestrator(
            config=ScanConfig(providers=("superbet", "betclic", "odds_api"), enable_valuebets=False)
        )
        orchestrator.normalization_engine.register_normalizer("superbet", MockNormalizerWrapper([g_sb]))
        orchestrator.normalization_engine.register_normalizer("betclic", MockNormalizerWrapper([g_bc]))
        orchestrator.normalization_engine.register_normalizer("odds_api", MockNormalizerWrapper([g_oa]))

        result = orchestrator.run_scan_cycle(providers={"superbet": prov_sb, "betclic": prov_bc, "odds_api": prov_oa})

        matched = result.resource_metrics.matched_markets_total
        evaluated = result.resource_metrics.evaluated_markets_total
        not_evaluated = result.resource_metrics.not_evaluated_markets_total
        rejected = result.resource_metrics.rejected_markets_total

        assert matched >= 2
        assert evaluated == 1
        assert not_evaluated >= 1
        assert matched == evaluated + rejected + not_evaluated
        assert result.resource_metrics.rejection_reasons_breakdown.get(EvaluationExclusionReason.DUPLICATE_CANONICAL_MARKET.value) >= 1

    def test_semantic_safeguards_period_scope_line_isolation(self):
        """Verifies that period, scope, and line mismatches cannot produce cross-market evaluation."""
        mkt_key_ft = CanonicalMarketKey(
            market_type=CanonicalMarketType.TOTALS.value,
            period=MarketPeriod.FULL_TIME.value,
            scope=MarketScope.MATCH.value,
            line=2.5,
        )
        mkt_key_ht = CanonicalMarketKey(
            market_type=CanonicalMarketType.TOTALS.value,
            period=MarketPeriod.FIRST_HALF.value,
            scope=MarketScope.MATCH.value,
            line=2.5,
        )
        mkt_key_diff_line = CanonicalMarketKey(
            market_type=CanonicalMarketType.TOTALS.value,
            period=MarketPeriod.FULL_TIME.value,
            scope=MarketScope.MATCH.value,
            line=3.5,
        )

        # Pair 1: Over 2.5 FT
        # Pair 2: Under 2.5 HT (period mismatch!)
        pair_ft = _make_comparable_pair("ev_1", mkt_key_ft, CanonicalSelectionType.OVER.value, source_odds_val=Decimal("2.20"), target_odds_val=Decimal("2.10"))
        pair_ht = _make_comparable_pair("ev_1", mkt_key_ht, CanonicalSelectionType.UNDER.value, source_odds_val=Decimal("2.20"), target_odds_val=Decimal("2.10"))
        pair_line = _make_comparable_pair("ev_1", mkt_key_diff_line, CanonicalSelectionType.UNDER.value, source_odds_val=Decimal("2.20"), target_odds_val=Decimal("2.10"))

        engine = SurebetDetectorEngine()
        result = engine.detect([pair_ft, pair_ht, pair_line])

        # Detector must evaluate each distinct canonical market key independently and not combine them!
        assert len(result.evaluations) == 3
        for ev in result.evaluations:
            # Each market is missing the opposite outcome (e.g. OVER is missing UNDER for its specific period/line)
            assert ev.status == SurebetStatus.INCOMPLETE_MARKET
            assert ev.opportunity is None

        assert len(result.opportunities) == 0

    def test_api_service_formats_evaluation_funnel_and_records(self):
        """Verifies that PlatformAPIService formats the Stage 13 evaluation funnel and terminal records."""
        from api.services import _serialize_scan_cycle_result

        comp = Competition(internal_id="comp_1", name="Premier League")
        ev_sb = Event(competition_id="comp_1", home_participant="Arsenal", away_participant="Chelsea", internal_id="ev_sb_1", provider_ids={"superbet": "sb_1"})
        ev_bc = Event(competition_id="comp_1", home_participant="Arsenal", away_participant="Chelsea", internal_id="ev_bc_1", provider_ids={"betclic": "bc_1"})

        m_1x2_sb = Market(event_id="ev_sb_1", market_type="1X2", internal_id="m_1x2_sb", provider_ids={"superbet": "m_1x2_sb"})
        m_1x2_bc = Market(event_id="ev_bc_1", market_type="1X2", internal_id="m_1x2_bc", provider_ids={"betclic": "m_1x2_bc"})

        s_h_sb = Selection(market_id="m_1x2_sb", selection_type="HOME", internal_id="s_h_sb", provider_ids={"superbet": "s_h_sb"})
        s_d_sb = Selection(market_id="m_1x2_sb", selection_type="DRAW", internal_id="s_d_sb", provider_ids={"superbet": "s_d_sb"})
        s_a_sb = Selection(market_id="m_1x2_sb", selection_type="AWAY", internal_id="s_a_sb", provider_ids={"superbet": "s_a_sb"})

        s_h_bc = Selection(market_id="m_1x2_bc", selection_type="HOME", internal_id="s_h_bc", provider_ids={"betclic": "s_h_bc"})
        s_d_bc = Selection(market_id="m_1x2_bc", selection_type="DRAW", internal_id="s_d_bc", provider_ids={"betclic": "s_d_bc"})
        s_a_bc = Selection(market_id="m_1x2_bc", selection_type="AWAY", internal_id="s_a_bc", provider_ids={"betclic": "s_a_bc"})

        odds_sb = [
            Odds(selection_id="s_h_sb", bookmaker="superbet", decimal_odds=2.00),
            Odds(selection_id="s_d_sb", bookmaker="superbet", decimal_odds=3.30),
            Odds(selection_id="s_a_sb", bookmaker="superbet", decimal_odds=3.60),
        ]
        odds_bc = [
            Odds(selection_id="s_h_bc", bookmaker="betclic", decimal_odds=1.95),
            Odds(selection_id="s_d_bc", bookmaker="betclic", decimal_odds=3.40),
            Odds(selection_id="s_a_bc", bookmaker="betclic", decimal_odds=3.50),
        ]

        g_sb = NormalizedGraph(
            competition=comp,
            event=ev_sb,
            markets=[m_1x2_sb],
            selections=[s_h_sb, s_d_sb, s_a_sb],
            odds_list=odds_sb,
        )
        g_bc = NormalizedGraph(
            competition=comp,
            event=ev_bc,
            markets=[m_1x2_bc],
            selections=[s_h_bc, s_d_bc, s_a_bc],
            odds_list=odds_bc,
        )

        prov_sb = MockProvider("superbet", parsed_items=[g_sb])
        prov_bc = MockProvider("betclic", parsed_items=[g_bc])

        orchestrator = ProductionScanOrchestrator(
            config=ScanConfig(providers=("superbet", "betclic"), enable_valuebets=False)
        )
        orchestrator.normalization_engine.register_normalizer("superbet", MockNormalizerWrapper([g_sb]))
        orchestrator.normalization_engine.register_normalizer("betclic", MockNormalizerWrapper([g_bc]))

        scan_result = orchestrator.run_scan_cycle(providers={"superbet": prov_sb, "betclic": prov_bc})

        api_payload = _serialize_scan_cycle_result(scan_result)

        assert "evaluation_funnel" in api_payload
        funnel = api_payload["evaluation_funnel"]
        assert funnel["matched_markets"] == 1
        assert funnel["evaluated_markets"] == 1
        assert funnel["rejected_markets"] == 0
        assert funnel["not_evaluated_markets"] == 0
        assert "market_evaluation_records" in api_payload
        assert len(api_payload["market_evaluation_records"]) == 1
        assert api_payload["market_evaluation_records"][0]["state"] == "EVALUATED"
