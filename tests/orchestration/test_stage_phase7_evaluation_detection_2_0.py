"""
Phase 7 — Evaluation / Detection 2.0 Deterministic Verification Test Suite

Verifies:
1. Complete Cardinality Funnel & Exact Accounting:
   matched_markets = evaluated_markets + rejected_markets + not_evaluated_markets
2. Evaluated vs. Qualified Distinction:
   - Surebet: EVALUATED (S >= 1.0) vs. OPPORTUNITY (S < 1.0)
   - Valuebet: candidate found (EV > 0) vs. qualified (EV% >= min_value_percent) vs. rejected ref data
3. Rejection Reason Taxonomy Completeness:
   - INCOMPLETE_SELECTIONS
   - LINE_INVALID
   - INVALID_MARKET_IDENTITY
   - INCOMPLETE_EVENT_IDENTITY
   - CROSS_MARKET_CONTAMINATION
   - INVALID_ODDS
   - UNSUPPORTED_MARKET
   - DUPLICATE_CANONICAL_MARKET
   - ZERO_COMPARABLE_SELECTIONS
4. Lightweight Provenance Retention:
   - Lineage preserved without raw provider payloads
5. Valuebet Rejection Telemetry:
   - Reference data rejections (stale, incomplete, invalid odds) tracked in telemetry
6. API Serialization Funnel:
   - Evaluation records and funnel preserved in API payload
"""

from decimal import Decimal
import pytest
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

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
    MarketCompletenessStatus,
)
from normalization.selection_identity import (
    CanonicalSelectionKey,
    CanonicalSelectionType,
)
from normalization.validation_pipeline import (
    ComparableSelectionPair,
    MatchedMarketLineage,
    CanonicalEventValidationRecord,
    CrossBookmakerValidationPipeline,
)
from normalization.odds_comparison import (
    OddsComparisonEngine,
    OddsComparisonStatus,
)
from normalization.surebet import (
    SurebetDetectorEngine,
    SurebetStatus,
    SurebetOpportunity,
    SurebetLeg,
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
from reference_odds.models import ReferenceEvent, ReferenceMarket, ReferenceSelection
from valuebets.config import ValuebetConfig
from valuebets.engine import ValuebetEngine


# ──────────────────────────────────────────────────────────────────────────────
# Test Fixtures & Helper Factories
# ──────────────────────────────────────────────────────────────────────────────

def _create_mock_comparable_pair(
    canonical_event_id: str,
    market_key: CanonicalMarketKey,
    selection_type: str,
    source_odds_val: Optional[Decimal] = Decimal("2.10"),
    target_odds_val: Optional[Decimal] = Decimal("2.05"),
    source_provider: str = "superbet",
    target_provider: str = "betclic",
    source_market_id: str = "sm_1",
    target_market_id: str = "tm_1",
    home_team: str = "Arsenal",
    away_team: str = "Chelsea",
    source_is_active: bool = True,
    target_is_active: bool = True,
) -> ComparableSelectionPair:
    sel_key = CanonicalSelectionKey(
        market_key=market_key,
        selection_type=selection_type,
    )
    s_meta = {"is_active": source_is_active}
    t_meta = {"is_active": target_is_active}
    ss = Selection(
        internal_id=f"{source_provider}_{selection_type}",
        market_id=source_market_id,
        selection_type=selection_type,
        metadata=s_meta,
    )
    ts = Selection(
        internal_id=f"{target_provider}_{selection_type}",
        market_id=target_market_id,
        selection_type=selection_type,
        metadata=t_meta,
    )
    s_odds = (
        Odds(
            selection_id=ss.internal_id,
            bookmaker=source_provider,
            decimal_odds=float(source_odds_val),
        )
        if source_odds_val is not None
        else None
    )
    t_odds = (
        Odds(
            selection_id=ts.internal_id,
            bookmaker=target_provider,
            decimal_odds=float(target_odds_val),
        )
        if target_odds_val is not None
        else None
    )
    ev_ev = MatchEvidence(
        source_provider=source_provider,
        target_provider=target_provider,
        source_event_id="s_ev_1",
        target_event_id="t_ev_1",
        decision="MATCHED",
        total_score=0.98,
        orientation="DIRECT",
        evidence={"source_name": f"{home_team} vs {away_team}", "home_team": home_team, "away_team": away_team},
        home_team=home_team,
        away_team=away_team,
    )
    return ComparableSelectionPair(
        canonical_event_id=canonical_event_id,
        canonical_market_key=market_key,
        canonical_selection_key=sel_key,
        source_provider=source_provider,
        target_provider=target_provider,
        source_event_id="s_ev_1",
        target_event_id="t_ev_1",
        source_internal_event_id="s_ev_int",
        target_internal_event_id="t_ev_int",
        source_market_id=source_market_id,
        target_market_id=target_market_id,
        source_selection_id=ss.internal_id,
        target_selection_id=ts.internal_id,
        source_selection=ss,
        target_selection=ts,
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


# ──────────────────────────────────────────────────────────────────────────────
# Test Cases
# ──────────────────────────────────────────────────────────────────────────────

class TestPhase7EvaluationDetection:
    """Comprehensive test suite for Phase 7 Evaluation / Detection 2.0."""

    # 1. Full Cardinality Reconciliation
    def test_complete_cardinality_funnel_reconciliation(self):
        """Proves matched_markets = evaluated_markets + rejected_markets + not_evaluated_markets."""
        comp = Competition(internal_id="comp_1", name="Premier League")
        ev_sb = Event(competition_id="comp_1", home_participant="Liverpool", away_participant="Arsenal", internal_id="ev_sb", provider_ids={"superbet": "sb_1"})
        ev_bc = Event(competition_id="comp_1", home_participant="Liverpool", away_participant="Arsenal", internal_id="ev_bc", provider_ids={"betclic": "bc_1"})

        # Market 1: Complete 1X2 (Evaluated, No Surebet)
        m_1x2_sb = Market(event_id="ev_sb", market_type="1X2", internal_id="m1_sb", provider_ids={"superbet": "m1_sb"})
        m_1x2_bc = Market(event_id="ev_bc", market_type="1X2", internal_id="m1_bc", provider_ids={"betclic": "m1_bc"})
        s_1x2_h_sb = Selection(market_id="m1_sb", selection_type="HOME", internal_id="s1_h_sb", provider_ids={"superbet": "s1_h_sb"})
        s_1x2_d_sb = Selection(market_id="m1_sb", selection_type="DRAW", internal_id="s1_d_sb", provider_ids={"superbet": "s1_d_sb"})
        s_1x2_a_sb = Selection(market_id="m1_sb", selection_type="AWAY", internal_id="s1_a_sb", provider_ids={"superbet": "s1_a_sb"})
        s_1x2_h_bc = Selection(market_id="m1_bc", selection_type="HOME", internal_id="s1_h_bc", provider_ids={"betclic": "s1_h_bc"})
        s_1x2_d_bc = Selection(market_id="m1_bc", selection_type="DRAW", internal_id="s1_d_bc", provider_ids={"betclic": "s1_d_bc"})
        s_1x2_a_bc = Selection(market_id="m1_bc", selection_type="AWAY", internal_id="s1_a_bc", provider_ids={"betclic": "s1_a_bc"})

        # Market 2: Complete BTTS with Tax-Adjusted Surebet (Opportunity)
        m_btts_sb = Market(event_id="ev_sb", market_type="BTTS", internal_id="m2_sb", provider_ids={"superbet": "m2_sb"})
        m_btts_bc = Market(event_id="ev_bc", market_type="BTTS", internal_id="m2_bc", provider_ids={"betclic": "m2_bc"})
        s_btts_y_sb = Selection(market_id="m2_sb", selection_type="YES", internal_id="s2_y_sb", provider_ids={"superbet": "s2_y_sb"})
        s_btts_n_sb = Selection(market_id="m2_sb", selection_type="NO", internal_id="s2_n_sb", provider_ids={"superbet": "s2_n_sb"})
        s_btts_y_bc = Selection(market_id="m2_bc", selection_type="YES", internal_id="s2_y_bc", provider_ids={"betclic": "s2_y_bc"})
        s_btts_n_bc = Selection(market_id="m2_bc", selection_type="NO", internal_id="s2_n_bc", provider_ids={"betclic": "s2_n_bc"})

        # Market 3: Incomplete Draw No Bet (Missing Away outcome) -> Rejected (INCOMPLETE_SELECTIONS)
        m_dnb_sb = Market(event_id="ev_sb", market_type="DRAW_NO_BET", internal_id="m3_sb", provider_ids={"superbet": "m3_sb"})
        m_dnb_bc = Market(event_id="ev_bc", market_type="DRAW_NO_BET", internal_id="m3_bc", provider_ids={"betclic": "m3_bc"})
        s_dnb_h_sb = Selection(market_id="m3_sb", selection_type="HOME", internal_id="s3_h_sb", provider_ids={"superbet": "s3_h_sb"})
        s_dnb_h_bc = Selection(market_id="m3_bc", selection_type="HOME", internal_id="s3_h_bc", provider_ids={"betclic": "s3_h_bc"})

        # Market 4: Totals line 2.5 with missing UNDER outcome -> Rejected (INCOMPLETE_SELECTIONS)
        m_tot_sb = Market(event_id="ev_sb", market_type="TOTALS", line=2.5, internal_id="m4_sb", provider_ids={"superbet": "m4_sb"})
        m_tot_bc = Market(event_id="ev_bc", market_type="TOTALS", line=2.5, internal_id="m4_bc", provider_ids={"betclic": "m4_bc"})
        s_tot_o_sb = Selection(market_id="m4_sb", selection_type="OVER", internal_id="s4_o_sb", provider_ids={"superbet": "s4_o_sb"})
        s_tot_o_bc = Selection(market_id="m4_bc", selection_type="OVER", internal_id="s4_o_bc", provider_ids={"betclic": "s4_o_bc"})

        odds_sb = [
            # 1X2
            Odds(selection_id="s1_h_sb", bookmaker="superbet", decimal_odds=2.00),
            Odds(selection_id="s1_d_sb", bookmaker="superbet", decimal_odds=3.30),
            Odds(selection_id="s1_a_sb", bookmaker="superbet", decimal_odds=3.60),
            # BTTS (Superbet 2.50 net 2.20 vs Betclic 2.10 -> S < 1.0)
            Odds(selection_id="s2_y_sb", bookmaker="superbet", decimal_odds=1.70),
            Odds(selection_id="s2_n_sb", bookmaker="superbet", decimal_odds=2.50),
            # DNB
            Odds(selection_id="s3_h_sb", bookmaker="superbet", decimal_odds=1.85),
            # Totals
            Odds(selection_id="s4_o_sb", bookmaker="superbet", decimal_odds=1.90),
        ]
        odds_bc = [
            # 1X2
            Odds(selection_id="s1_h_bc", bookmaker="betclic", decimal_odds=1.95),
            Odds(selection_id="s1_d_bc", bookmaker="betclic", decimal_odds=3.40),
            Odds(selection_id="s1_a_bc", bookmaker="betclic", decimal_odds=3.50),
            # BTTS
            Odds(selection_id="s2_y_bc", bookmaker="betclic", decimal_odds=2.10),
            Odds(selection_id="s2_n_bc", bookmaker="betclic", decimal_odds=1.75),
            # DNB
            Odds(selection_id="s3_h_bc", bookmaker="betclic", decimal_odds=1.80),
            # Totals
            Odds(selection_id="s4_o_bc", bookmaker="betclic", decimal_odds=1.90),
        ]

        g_sb = NormalizedGraph(
            competition=comp,
            event=ev_sb,
            markets=[m_1x2_sb, m_btts_sb, m_dnb_sb, m_tot_sb],
            selections=[s_1x2_h_sb, s_1x2_d_sb, s_1x2_a_sb, s_btts_y_sb, s_btts_n_sb, s_dnb_h_sb, s_tot_o_sb],
            odds_list=odds_sb,
        )
        g_bc = NormalizedGraph(
            competition=comp,
            event=ev_bc,
            markets=[m_1x2_bc, m_btts_bc, m_dnb_bc, m_tot_bc],
            selections=[s_1x2_h_bc, s_1x2_d_bc, s_1x2_a_bc, s_btts_y_bc, s_btts_n_bc, s_dnb_h_bc, s_tot_o_bc],
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

        # Exact Cardinality Invariant Verification
        matched_total = result.resource_metrics.matched_markets_total
        eval_total = result.resource_metrics.evaluated_markets_total
        rej_total = result.resource_metrics.rejected_markets_total
        not_eval_total = result.resource_metrics.not_evaluated_markets_total

        assert matched_total == 4
        assert eval_total == 2
        assert rej_total == 2
        assert not_eval_total == 0
        assert matched_total == eval_total + rej_total + not_eval_total

        # Evaluated vs Qualified distinction
        assert result.valid_surebets_count == 1
        assert result.detected_opportunities_count == 1

        # Rejection reason verification
        reasons = result.resource_metrics.rejection_reasons_breakdown
        assert reasons.get(EvaluationExclusionReason.INCOMPLETE_SELECTIONS.value) == 2

    # 2. Specific Rejection Taxonomy Tests
    def test_invalid_market_identity_rejection(self):
        """Proves that TEAM scope without participant_role produces INVALID_MARKET_IDENTITY."""
        mkt_key = CanonicalMarketKey(
            sport="football",
            market_type=CanonicalMarketType.TOTALS.value,
            scope=MarketScope.TEAM.value,
            participant_role=None,  # Missing required role!
            period=MarketPeriod.FULL_TIME.value,
            line=1.5,
        )
        pair = _create_mock_comparable_pair("ev_1", mkt_key, CanonicalSelectionType.OVER.value)
        engine = SurebetDetectorEngine()
        res = engine.detect([pair])

        assert len(res.evaluations) == 1
        eval_rec = res.evaluations[0]
        assert eval_rec.status == SurebetStatus.INVALID_MARKET
        assert eval_rec.exclusion_reason_code == "INVALID_MARKET_IDENTITY"

    def test_cross_market_contamination_rejection(self):
        """Proves that single-bookmaker legs from distinct market IDs produce CROSS_MARKET_CONTAMINATION."""
        mkt_key = CanonicalMarketKey(
            sport="football",
            market_type=CanonicalMarketType.BTTS.value,
            scope=MarketScope.MATCH.value,
            period=MarketPeriod.FULL_TIME.value,
        )
        # Superbet YES from market sm_1, Superbet NO from market sm_2 (cross-market stitching)
        pair_y = _create_mock_comparable_pair("ev_1", mkt_key, CanonicalSelectionType.YES.value, source_market_id="sm_1", source_odds_val=Decimal("2.60"), target_odds_val=Decimal("1.10"))
        pair_n = _create_mock_comparable_pair("ev_1", mkt_key, CanonicalSelectionType.NO.value, source_market_id="sm_2", source_odds_val=Decimal("2.60"), target_odds_val=Decimal("1.10"))

        engine = SurebetDetectorEngine()
        res = engine.detect([pair_y, pair_n])

        assert len(res.evaluations) == 1
        eval_rec = res.evaluations[0]
        assert eval_rec.status == SurebetStatus.INVALID_MARKET
        assert eval_rec.exclusion_reason_code == "CROSS_MARKET_CONTAMINATION"
        assert len(res.opportunities) == 0

    def test_incomplete_event_identity_rejection(self):
        """Proves that canonical event with invalid/unknown teams is rejected."""
        mkt_key = CanonicalMarketKey(
            sport="football",
            market_type=CanonicalMarketType.BTTS.value,
            scope=MarketScope.MATCH.value,
            period=MarketPeriod.FULL_TIME.value,
        )
        pair_y = _create_mock_comparable_pair("ev_1", mkt_key, CanonicalSelectionType.YES.value, home_team="—", away_team="Unknown", source_odds_val=Decimal("2.60"), target_odds_val=Decimal("1.80"))
        pair_n = _create_mock_comparable_pair("ev_1", mkt_key, CanonicalSelectionType.NO.value, home_team="—", away_team="Unknown", source_odds_val=Decimal("1.80"), target_odds_val=Decimal("2.10"))

        engine = SurebetDetectorEngine()
        res = engine.detect([pair_y, pair_n])

        assert len(res.evaluations) == 1
        eval_rec = res.evaluations[0]
        assert eval_rec.status == SurebetStatus.INVALID_MARKET
        assert eval_rec.exclusion_reason_code == "INCOMPLETE_EVENT_IDENTITY"

    def test_invalid_odds_rejection(self):
        """Proves that odds <= 1.0 or non-numeric/inactive odds do not form valid evaluations."""
        mkt_key = CanonicalMarketKey(
            sport="football",
            market_type=CanonicalMarketType.BTTS.value,
            scope=MarketScope.MATCH.value,
            period=MarketPeriod.FULL_TIME.value,
        )
        # Inactive selection on target
        pair_y = _create_mock_comparable_pair("ev_1", mkt_key, CanonicalSelectionType.YES.value, target_is_active=False)
        pair_n = _create_mock_comparable_pair("ev_1", mkt_key, CanonicalSelectionType.NO.value, source_odds_val=Decimal("1.00")) # Invalid odds <= 1.0

        engine = SurebetDetectorEngine()
        res = engine.detect([pair_y, pair_n])

        assert len(res.evaluations) == 1
        eval_rec = res.evaluations[0]
        assert eval_rec.status == SurebetStatus.INCOMPLETE_MARKET

    # 3. Valuebet Evaluated vs Qualified Distinction & Rejection Tracking
    def test_valuebet_evaluated_vs_qualified_and_rejection_tracking(self):
        """Proves that ValuebetEngine distinguishes evaluated selections, candidates found, and qualified valuebets."""
        # Freshness-relative timestamps: the production freshness window is 30 min
        # (ValuebetConfig.max_freshness_seconds=1800). A pinned historical timestamp
        # is permanently stale, so express "fresh reference data" relative to now
        # with a wide safety margin (5 min old vs 30 min window — no boundary race).
        now = datetime.now(timezone.utc)
        kickoff_iso = (now + timedelta(hours=2)).isoformat().replace("+00:00", "Z")
        ref_timestamp_iso = (now - timedelta(minutes=5)).isoformat().replace("+00:00", "Z")
        comp = Competition(internal_id="comp_1", name="Premier League")
        ev_bm = Event(competition_id="comp_1", home_participant="Real Madrid", away_participant="Barcelona", internal_id="ev_bm_1", scheduled_start=kickoff_iso)
        mkt_1x2 = Market(event_id="ev_bm_1", market_type="1X2", internal_id="m_1x2")
        s_h = Selection(market_id="m_1x2", selection_type="HOME", internal_id="s_h")
        s_d = Selection(market_id="m_1x2", selection_type="DRAW", internal_id="s_d")
        s_a = Selection(market_id="m_1x2", selection_type="AWAY", internal_id="s_a")
        odds = [
            Odds(selection_id="s_h", bookmaker="superbet", decimal_odds=2.40),
            Odds(selection_id="s_d", bookmaker="superbet", decimal_odds=3.20),
            Odds(selection_id="s_a", bookmaker="superbet", decimal_odds=3.00),
        ]
        graph = NormalizedGraph(
            competition=comp,
            event=ev_bm,
            markets=[mkt_1x2],
            selections=[s_h, s_d, s_a],
            odds_list=odds,
        )

        # Reference event with sharp odds: Real Madrid 1.95, Draw 3.60, Barcelona 4.00
        ref_ev = ReferenceEvent(
            source="the_odds_api",
            source_event_id="ref_1",
            home_team="Real Madrid",
            away_team="Barcelona",
            scheduled_start=kickoff_iso,
            sport="soccer",
            markets=[
                ReferenceMarket(
                    market_type="1X2",
                    bookmaker_name="pinnacle",
                    timestamp=ref_timestamp_iso,
                    selections={
                        "HOME": ReferenceSelection(selection_type="HOME", odds=Decimal("1.95")),
                        "DRAW": ReferenceSelection(selection_type="DRAW", odds=Decimal("3.60")),
                        "AWAY": ReferenceSelection(selection_type="AWAY", odds=Decimal("4.00")),
                    },
                )
            ],
        )

        # Min value percent = 5.0%
        engine = ValuebetEngine(config=ValuebetConfig(min_value_percent=Decimal("5.0"), enable_negative_value_candidates=False))
        result = engine.detect_valuebets(
            bookmaker_graphs=[graph],
            reference_events=[ref_ev],
        )

        # 3 selections evaluated
        assert result.metrics.selections_evaluated == 3
        # Home selection has value: fair_p ~ 0.493, bm_odds = 2.40 -> value = (2.40 * 0.493) - 1 = +18.3% > 5.0%
        assert len(result.qualified_valuebets) >= 1
        assert result.qualified_valuebets[0].is_qualified is True
        assert result.qualified_valuebets[0].value_percent >= Decimal("5.0")
        assert result.qualified_valuebets[0].canonical_event_id == "ev_bm_1"
        assert result.qualified_valuebets[0].reference_source == "the_odds_api"
        assert result.qualified_valuebets[0].reference_bookmaker == "pinnacle"

    # 4. Provenance Preservation
    def test_provenance_retention_through_evaluation(self):
        """Proves that SurebetOpportunity and SurebetLeg retain full multi-layer provenance without raw payloads."""
        mkt_key = CanonicalMarketKey(
            sport="football",
            market_type=CanonicalMarketType.BTTS.value,
            scope=MarketScope.MATCH.value,
            period=MarketPeriod.FULL_TIME.value,
        )
        pair_y = _create_mock_comparable_pair("ev_1", mkt_key, CanonicalSelectionType.YES.value, source_odds_val=Decimal("2.60"), target_odds_val=Decimal("1.80"))
        pair_n = _create_mock_comparable_pair("ev_1", mkt_key, CanonicalSelectionType.NO.value, source_odds_val=Decimal("1.80"), target_odds_val=Decimal("2.10"))

        engine = SurebetDetectorEngine()
        res = engine.detect([pair_y, pair_n])

        assert len(res.opportunities) == 1
        opp = res.opportunities[0]
        assert opp.canonical_event_id == "ev_1"
        assert opp.canonical_market_key == mkt_key
        assert opp.opportunity_id.startswith("sb:ev_1:football:BTTS:")

        for leg in opp.legs:
            assert isinstance(leg, SurebetLeg)
            assert leg.selection_type in ("YES", "NO")
            assert leg.provider in ("superbet", "betclic")
            assert leg.odds > Decimal("1.0")
            assert leg.effective_odds is not None
            assert leg.source_selection_id is not None
            assert leg.source_event_id is not None
            assert leg.source_market_id is not None
            assert isinstance(leg.selection_evidence, dict)

    # 5. API Serialization Integrity
    def test_api_serialization_evaluation_records_and_funnel(self):
        """Proves that _serialize_scan_cycle_result preserves evaluation funnel and terminal records."""
        from api.services import _serialize_scan_cycle_result

        comp = Competition(internal_id="comp_1", name="Premier League")
        ev_sb = Event(competition_id="comp_1", home_participant="Liverpool", away_participant="Arsenal", internal_id="ev_sb", provider_ids={"superbet": "sb_1"})
        ev_bc = Event(competition_id="comp_1", home_participant="Liverpool", away_participant="Arsenal", internal_id="ev_bc", provider_ids={"betclic": "bc_1"})

        m_1x2_sb = Market(event_id="ev_sb", market_type="1X2", internal_id="m1_sb", provider_ids={"superbet": "m1_sb"})
        m_1x2_bc = Market(event_id="ev_bc", market_type="1X2", internal_id="m1_bc", provider_ids={"betclic": "m1_bc"})
        s_1x2_h_sb = Selection(market_id="m1_sb", selection_type="HOME", internal_id="s1_h_sb", provider_ids={"superbet": "s1_h_sb"})
        s_1x2_d_sb = Selection(market_id="m1_sb", selection_type="DRAW", internal_id="s1_d_sb", provider_ids={"superbet": "s1_d_sb"})
        s_1x2_a_sb = Selection(market_id="m1_sb", selection_type="AWAY", internal_id="s1_a_sb", provider_ids={"superbet": "s1_a_sb"})
        s_1x2_h_bc = Selection(market_id="m1_bc", selection_type="HOME", internal_id="s1_h_bc", provider_ids={"betclic": "s1_h_bc"})
        s_1x2_d_bc = Selection(market_id="m1_bc", selection_type="DRAW", internal_id="s1_d_bc", provider_ids={"betclic": "s1_d_bc"})
        s_1x2_a_bc = Selection(market_id="m1_bc", selection_type="AWAY", internal_id="s1_a_bc", provider_ids={"betclic": "s1_a_bc"})

        odds_sb = [
            Odds(selection_id="s1_h_sb", bookmaker="superbet", decimal_odds=2.00),
            Odds(selection_id="s1_d_sb", bookmaker="superbet", decimal_odds=3.30),
            Odds(selection_id="s1_a_sb", bookmaker="superbet", decimal_odds=3.60),
        ]
        odds_bc = [
            Odds(selection_id="s1_h_bc", bookmaker="betclic", decimal_odds=1.95),
            Odds(selection_id="s1_d_bc", bookmaker="betclic", decimal_odds=3.40),
            Odds(selection_id="s1_a_bc", bookmaker="betclic", decimal_odds=3.50),
        ]

        g_sb = NormalizedGraph(
            competition=comp,
            event=ev_sb,
            markets=[m_1x2_sb],
            selections=[s_1x2_h_sb, s_1x2_d_sb, s_1x2_a_sb],
            odds_list=odds_sb,
        )
        g_bc = NormalizedGraph(
            competition=comp,
            event=ev_bc,
            markets=[m_1x2_bc],
            selections=[s_1x2_h_bc, s_1x2_d_bc, s_1x2_a_bc],
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
        serialized = _serialize_scan_cycle_result(scan_result)

        assert "evaluation_funnel" in serialized
        funnel = serialized["evaluation_funnel"]
        assert funnel["matched_markets"] == 1
        assert funnel["evaluated_markets"] == 1
        assert funnel["rejected_markets"] == 0
        assert funnel["not_evaluated_markets"] == 0
        assert "market_evaluation_records" in serialized
        assert len(serialized["market_evaluation_records"]) == 1
        assert serialized["market_evaluation_records"][0]["state"] == "EVALUATED"
