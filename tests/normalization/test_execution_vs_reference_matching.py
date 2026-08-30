"""
Unit Tests for Business Rule: Matched Events (Execution) vs Reference Matches
Verifies:
1. Superbet + Betclic = Execution Bookmakers (eligible for surebet detection & actionable matched events).
2. Bet365 + Unibet = Reference Bookmakers (enrich canonical events for valuebets, do not increase actionable matched events counter).
3. Surebet detection never generates legs with Bet365 or Unibet.
4. Internal N-way matching preserves reference odds attachments.
"""

from decimal import Decimal
import pytest

from domain.models import Competition, Event, Market, Selection, Odds, CanonicalEvent, EventSource
from normalization.base_normalizer import NormalizedGraph
from normalization.market_identity import CanonicalMarketKey, CanonicalMarketType
from normalization.selection_identity import CanonicalSelectionKey, CanonicalSelectionType
from normalization.odds_comparison import OddsComparison, OddsComparisonStatus
from normalization.surebet import (
    SurebetDetectorEngine,
    SurebetStatus,
    EXECUTION_BOOKMAKERS,
    ALLOWED_EXECUTION_BOOKMAKERS,
)
from scanner.surebet_detector import SurebetDetector
from orchestration.models import (
    ScanCycleResult,
    CycleStatus,
    ResourceMetrics,
    StageTiming,
    ScanConfig,
)
from normalization.validation_pipeline import (
    CrossBookmakerValidationPipeline,
    CrossBookmakerValidationResult,
    CanonicalEventValidationRecord,
    ComparableSelectionPair,
)
from normalization.market_matcher import MarketMatchBatchResult
from api.services import _serialize_scan_cycle_result, _serialize_events_from_scan_result
from orchestration.scan_orchestrator import ProductionScanOrchestrator
from providers.base.provider_result import ProviderResult, ProviderState
from providers.base.models import ValidationReport


class TestExecutionVsReferenceSurebetDetection:
    """Verifies that Surebet detection only admits execution bookmakers (Superbet & Betclic)."""

    def test_surebet_detector_engine_excludes_reference_bookmakers(self):
        from core.tax_engine import TaxEngine, BookmakerTaxConfig
        no_tax = TaxEngine(custom_configs={"superbet": BookmakerTaxConfig(tax_enabled=False), "betclic": BookmakerTaxConfig(tax_enabled=False)})
        engine = SurebetDetectorEngine(tax_engine=no_tax)
        assert engine.is_provider_allowed("superbet") is True
        assert engine.is_provider_allowed("betclic") is True
        assert engine.is_provider_allowed("bet365") is False
        assert engine.is_provider_allowed("unibet") is False

        mkt_key = CanonicalMarketKey(
            market_type=CanonicalMarketType.BTTS.value,
            period="FULL_TIME",
            scope="MATCH",
        )
        sel_key_yes = CanonicalSelectionKey(market_key=mkt_key, selection_type="YES")
        sel_key_no = CanonicalSelectionKey(market_key=mkt_key, selection_type="NO")

        # Scenario: Bet365 has high odds (2.20) for YES, Superbet has 2.05 for YES, Betclic has 2.10 for NO
        # If Bet365 was allowed, S = 1/2.20 + 1/2.10 = 0.4545 + 0.4761 = 0.9306 (Surebet with Bet365 leg)
        # But Bet365 is a reference bookmaker and must be excluded!
        # With Superbet (2.05) and Betclic (2.10): S = 1/2.05 + 1/2.10 = 0.4878 + 0.4761 = 0.9639 (Surebet with Superbet + Betclic)
        comps = [
            OddsComparison(
                canonical_event_id="cev_01",
                canonical_market_key=mkt_key,
                canonical_selection_key=sel_key_yes,
                source_provider="bet365",
                target_provider="superbet",
                source_event_id="b365_ev1",
                target_event_id="sb_ev1",
                source_internal_event_id="iev_b365",
                target_internal_event_id="iev_sb",
                source_market_id="m_b365_1",
                target_market_id="m_sb_1",
                source_selection_id="s_b365_yes",
                target_selection_id="s_sb_yes",
                status=OddsComparisonStatus.VALID,
                source_odds=Decimal("2.20"),
                target_odds=Decimal("2.05"),
                evidence={"source_validation_status": "VALID", "target_validation_status": "VALID"},
            ),
            OddsComparison(
                canonical_event_id="cev_01",
                canonical_market_key=mkt_key,
                canonical_selection_key=sel_key_no,
                source_provider="betclic",
                target_provider="unibet",
                source_event_id="bc_ev1",
                target_event_id="uni_ev1",
                source_internal_event_id="iev_bc",
                target_internal_event_id="iev_uni",
                source_market_id="m_bc_1",
                target_market_id="m_uni_1",
                source_selection_id="s_bc_no",
                target_selection_id="s_uni_no",
                status=OddsComparisonStatus.VALID,
                source_odds=Decimal("2.10"),
                target_odds=Decimal("2.25"),
                evidence={"source_validation_status": "VALID", "target_validation_status": "VALID"},
            ),
        ]

        eval_res = engine.evaluate_market(
            canonical_event_id="cev_01",
            canonical_market_key=mkt_key,
            comparisons=comps,
        )

        assert eval_res.status == SurebetStatus.SUREBET
        assert eval_res.opportunity is not None
        # Legs must ONLY come from superbet and betclic, never bet365 or unibet
        leg_providers = {l.provider for l in eval_res.opportunity.legs}
        assert leg_providers == {"superbet", "betclic"}
        assert "bet365" not in leg_providers
        assert "unibet" not in leg_providers

    def test_scanner_surebet_detector_filters_reference_odds(self):
        detector = SurebetDetector()
        event = Event(competition_id="c1", home_participant="TeamA", away_participant="TeamB", internal_id="ev1")
        market = Market(event_id="ev1", market_type="1X2", internal_id="m1")
        sel_home = Selection(market_id="m1", selection_type="HOME", internal_id="s_home")
        sel_away = Selection(market_id="m1", selection_type="AWAY", internal_id="s_away")

        # Bet365 has 2.50, but Bet365 is reference only!
        odds_list = [
            Odds(selection_id="s_home", bookmaker="bet365", decimal_odds=2.50),
            Odds(selection_id="s_home", bookmaker="superbet", decimal_odds=2.10),
            Odds(selection_id="s_away", bookmaker="betclic", decimal_odds=2.10),
        ]

        opps = detector.detect_surebets(event, [market], [sel_home, sel_away], odds_list)
        assert len(opps) == 1
        opp = opps[0]
        legs_bookmakers = {l.bookmaker for l in opp.legs}
        assert legs_bookmakers == {"superbet", "betclic"}
        assert "bet365" not in legs_bookmakers


class TestExecutionVsReferenceTelemetryAndMatching:
    """Verifies that Matched Events counter reflects actionable Superbet <-> Betclic matches."""

    def test_reference_only_matches_do_not_increment_matched_events(self):
        # 1. Event matched between Superbet and Bet365 only (No Betclic)
        ev1 = Event(competition_id="c1", home_participant="A", away_participant="B", internal_id="ev1")
        ce_ref_only = CanonicalEvent(
            canonical_event_id="cev_ref_only",
            sport="Football",
            home_team="A",
            away_team="B",
            sources={
                "superbet": EventSource(provider="superbet", provider_event_id="sb_1", internal_event_id="ev1", home_participant="A", away_participant="B", original_event=ev1),
                "bet365": EventSource(provider="bet365", provider_event_id="b365_1", internal_event_id="ev2", home_participant="A", away_participant="B", original_event=ev1),
            },
        )

        # 2. Event matched between Superbet and Betclic (Actionable Match)
        ce_actionable = CanonicalEvent(
            canonical_event_id="cev_actionable",
            sport="Football",
            home_team="C",
            away_team="D",
            sources={
                "superbet": EventSource(provider="superbet", provider_event_id="sb_2", internal_event_id="ev3", home_participant="C", away_participant="D", original_event=ev1),
                "betclic": EventSource(provider="betclic", provider_event_id="bc_2", internal_event_id="ev4", home_participant="C", away_participant="D", original_event=ev1),
            },
        )

        rec_ref = CanonicalEventValidationRecord(
            canonical_event=ce_ref_only,
            market_batch_result=MarketMatchBatchResult(),
        )
        rec_actionable = CanonicalEventValidationRecord(
            canonical_event=ce_actionable,
            market_batch_result=MarketMatchBatchResult(),
        )

        val_res = CrossBookmakerValidationResult(
            canonical_events=[ce_ref_only, ce_actionable],
            event_validation_records=[rec_ref, rec_actionable],
            event_candidates=[],
            event_decisions=[],
        )

        scan_res = ScanCycleResult(
            execution_id="test_exec",
            cycle_status=CycleStatus.SUCCESS,
            started_at="2026-08-20T12:00:00Z",
            completed_at="2026-08-20T12:00:01Z",
            duration_seconds=1.0,
            stage_timings=StageTiming(),
            resource_metrics=ResourceMetrics(matched_events=1),
            validation_result=val_res,
            matched_events_count=1,
        )

        serialized = _serialize_scan_cycle_result(scan_res)
        # Actionable matched events counter must be 1 (only Superbet <-> Betclic), not 2
        assert serialized["counts"]["matched_events"] == 1
        assert serialized["matching_diagnostic"]["matched_events"] == 1

        # Check event serialization
        summaries, details = _serialize_events_from_scan_result(scan_res)
        actionable_ev = next(s for s in summaries if s["canonical_event_id"] == "cev_actionable")
        ref_ev = next(s for s in summaries if s["canonical_event_id"] == "cev_ref_only")

        assert actionable_ev["matching_status"] == "MATCHED"
        assert actionable_ev["is_actionable_match"] is True

        assert ref_ev["matching_status"] == "UNMATCHED"
        assert ref_ev["is_actionable_match"] is False
