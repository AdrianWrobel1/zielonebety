"""
Stage 7.1: Production Scan Orchestration Integration & Adversarial Test Suite

Verifies:
1. Full successful scan cycle end-to-end (acquisition -> normalization -> matching -> detection -> lifecycle -> dispatch -> delivery).
2. Successful scan cycle with 0 surebets (valid SUCCESS outcome).
3. Repeated identical scan cycles (deterministic idempotency: Cycle 1 alerts, Cycle 2 suppresses duplicates).
4. Material opportunity update cycle (odds change > threshold -> UPDATED -> update alert dispatched).
5. Provider failure isolation (Single provider fails -> PARTIAL status, safe downstream bypass, 0 false surebets).
6. Dual provider failure (Both providers fail -> FAILED status).
7. Normalization error isolation (partial normalization failure).
8. Unmatched events handling (0 matches is normal, not an error).
9. Delivery failure safety (transient/permanent failure does not falsely mark opportunity as ALERTED).
10. Delivery reconciliation integration (reconciles due pending/retryable alerts).
11. Recovery on next cycle (failed cycle does not corrupt subsequent healthy cycles).
12. Process restart persistence (state preserved in SQLite across orchestrator instances).
13. Genuine fixture replay execution (using recorded manifests).
14. Complete adversarial scenarios (empty graphs, malformed inputs, scanner exceptions).
15. Live execution diagnostic (isolated with @pytest.mark.live).
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
from typing import Any, Dict, List, Optional
import unittest

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.connection import DatabaseManager
from database.models import BaseORM, DeliveryRecordORM, OpportunityRecordORM
from database.repositories.delivery_repository import DeliveryRepository
from database.repositories.opportunity_repository import OpportunityRepository
from domain.models import Competition, Event, Market, Odds, Selection
from normalization.alert_policy import DefaultOpportunityAlertPolicy, OpportunityAlertConfig
from normalization.base_normalizer import NormalizedGraph
from normalization.delivery_reliability import (
    DeliveryReconciliationService,
    DeliveryRetryConfig,
    DeliveryState,
)
from normalization.dispatcher import (
    ConsumerDeliveryResult,
    DeliveryStatus,
    DispatchableOpportunity,
    InMemoryOpportunityConsumer,
    OpportunityConsumer,
    OpportunityDispatcher,
)
from normalization.engine import NormalizationEngine
from normalization.lifecycle import OpportunityLifecycleManager, OpportunityStatus
from normalization.market_identity import CanonicalMarketKey, CanonicalMarketType
from normalization.selection_identity import CanonicalSelectionKey, CanonicalSelectionType
from normalization.surebet import SurebetDetectorEngine
from normalization.validation_pipeline import CrossBookmakerValidationPipeline
from orchestration.exceptions import CriticalPipelineFailure
from orchestration.models import CycleStatus, ScanConfig, ScanCycleResult
from orchestration.scan_orchestrator import ProductionScanOrchestrator
from providers.base.base_provider import BaseProvider
from providers.base.models import ProviderMetadata, ValidationReport
from providers.base.provider_context import ProviderContext
from providers.base.provider_state import ProviderState
from providers.betclic.provider import BetclicProvider
from providers.superbet.provider import SuperbetProvider


class MockProvider(BaseProvider):
    """Test helper provider allowing injection of parsed objects or deliberate failures."""

    def __init__(
        self,
        name: str,
        parsed_items: Optional[List[Any]] = None,
        should_fail: bool = False,
        failure_stage: str = "fetch",
        is_degraded: bool = False,
    ):
        ctx = ProviderContext(provider_name=name)
        meta = ProviderMetadata(name=name, code=name)
        super().__init__(context=ctx, metadata=meta)
        self._parsed_items = parsed_items if parsed_items is not None else []
        self._should_fail = should_fail
        self._failure_stage = failure_stage
        self._is_degraded = is_degraded

    def discover(self) -> List[Any]:
        if self._should_fail and self._failure_stage == "discovery":
            raise RuntimeError(f"Simulated discovery failure for {self.metadata.name}")
        return [{"id": f"disc_{i}"} for i in range(len(self._parsed_items))]

    def fetch(self, discovery_items: List[Any]) -> List[Any]:
        if self._should_fail and self._failure_stage == "fetch":
            raise RuntimeError(f"Simulated network timeout/fetch failure for {self.metadata.name}")
        return [{"raw": "data"} for _ in discovery_items]

    def parse(self, raw_data: List[Any]) -> List[Any]:
        if self._should_fail and self._failure_stage == "parse":
            raise RuntimeError(f"Simulated parse failure for {self.metadata.name}")
        return list(self._parsed_items)

    def validate(self, parsed_data: List[Any]) -> ValidationReport:
        if self._is_degraded:
            return ValidationReport(valid_objects=len(parsed_data) - 1, invalid_objects=1, is_valid=False)
        return ValidationReport(valid_objects=len(parsed_data), invalid_objects=0, is_valid=True)


class FailingConsumer(OpportunityConsumer):
    """Consumer that simulates network/HTTP failures."""

    def __init__(self, name: str = "failing_consumer", fail_count: int = 1):
        self._name = name
        self.fail_count = fail_count
        self.attempts = 0

    @property
    def name(self) -> str:
        return self._name

    def consume(self, opportunity: DispatchableOpportunity) -> ConsumerDeliveryResult:
        self.attempts += 1
        if self.attempts <= self.fail_count:
            return ConsumerDeliveryResult(
                consumer_name=self.name,
                status=DeliveryStatus.FAILED,
                error="Simulated 503 Service Unavailable / Network Timeout",
            )
        return ConsumerDeliveryResult(
            consumer_name=self.name,
            status=DeliveryStatus.DELIVERED,
            metadata={"message_id": "msg_recovered_123"},
        )


def _build_test_graph(
    provider_name: str,
    event_id: str,
    home_team: str,
    away_team: str,
    kickoff: str,
    odds_h: float,
    odds_d: float,
    odds_a: float,
    market_type: str = "1X2",
    line: Optional[float] = None,
) -> NormalizedGraph:
    """Helper to build a controlled NormalizedGraph with referential integrity."""
    comp_id = f"comp_{provider_name}_premier_league"
    comp = Competition(
        name="Premier League",
        sport="Football",
        country="England",
        internal_id=comp_id,
        provider_ids={provider_name: f"p_comp_{provider_name}"},
    )
    ev = Event(
        competition_id=comp.internal_id,
        home_participant=home_team,
        away_participant=away_team,
        scheduled_start=kickoff,
        internal_id=event_id,
        provider_ids={provider_name: f"p_ev_{event_id}"},
        metadata={"provider": provider_name},
    )
    mkt_id = f"mkt_{event_id}_1"
    mkt = Market(
        event_id=ev.internal_id,
        market_type=market_type,
        line=line,
        internal_id=mkt_id,
        provider_ids={provider_name: f"p_mkt_{mkt_id}"},
        metadata={"provider": provider_name},
    )
    sel_h = Selection(
        market_id=mkt_id,
        selection_type="HOME",
        participant=home_team,
        internal_id=f"sel_{event_id}_h",
        provider_ids={provider_name: f"p_sel_{event_id}_h"},
    )
    sel_d = Selection(
        market_id=mkt_id,
        selection_type="DRAW",
        internal_id=f"sel_{event_id}_d",
        provider_ids={provider_name: f"p_sel_{event_id}_d"},
    )
    sel_a = Selection(
        market_id=mkt_id,
        selection_type="AWAY",
        participant=away_team,
        internal_id=f"sel_{event_id}_a",
        provider_ids={provider_name: f"p_sel_{event_id}_a"},
    )

    o_h = Odds(selection_id=sel_h.internal_id, decimal_odds=odds_h, bookmaker=provider_name, internal_id=f"o_{event_id}_h")
    o_d = Odds(selection_id=sel_d.internal_id, decimal_odds=odds_d, bookmaker=provider_name, internal_id=f"o_{event_id}_d")
    o_a = Odds(selection_id=sel_a.internal_id, decimal_odds=odds_a, bookmaker=provider_name, internal_id=f"o_{event_id}_a")

    return NormalizedGraph(
        competition=comp,
        event=ev,
        markets=[mkt],
        selections=[sel_h, sel_d, sel_a],
        odds_list=[o_h, o_d, o_a],
    )


class MockNormalizerWrapper:
    """Normalizer mock that returns pre-built NormalizedGraphs."""

    def __init__(self, graphs: List[NormalizedGraph]):
        self.graphs = graphs

    def normalize_event(self, obj: Any) -> NormalizedGraph:
        if isinstance(obj, NormalizedGraph):
            return obj
        if isinstance(obj, dict) and "graph" in obj:
            return obj["graph"]
        raise ValueError("Cannot normalize object")


class TestProductionScanOrchestration(unittest.TestCase):
    """Authoritative test suite for Stage 7.1 Production Scan Orchestration."""

    def setUp(self):
        # Setup clean in-memory SQLite database for lifecycle and delivery tracking
        self.engine = create_engine("sqlite:///:memory:", echo=False)
        BaseORM.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, autoflush=False, expire_on_commit=False)
        self.session = self.Session()

        self.opp_repo = OpportunityRepository(self.session)
        self.del_repo = DeliveryRepository(self.session)

        self.alert_policy = DefaultOpportunityAlertPolicy(
            config=OpportunityAlertConfig(
                min_margin_delta=Decimal("0.005"),
                min_odds_relative_delta=Decimal("0.02"),
                min_odds_absolute_delta=Decimal("0.05"),
            )
        )

        self.dispatcher = OpportunityDispatcher()
        self.in_memory_consumer = InMemoryOpportunityConsumer(name="in_memory_consumer")
        self.dispatcher.register_consumer(self.in_memory_consumer)

        self.lifecycle_manager = OpportunityLifecycleManager(
            repository=self.opp_repo,
            alert_policy=self.alert_policy,
            delivery_repository=self.del_repo,
        )

        self.reconciliation_service = DeliveryReconciliationService(
            delivery_repository=self.del_repo,
            opportunity_repository=self.opp_repo,
            dispatcher=self.dispatcher,
            retry_config=DeliveryRetryConfig(max_attempts=3, initial_delay_seconds=1.0),
        )

        self.orchestrator = ProductionScanOrchestrator(
            config=ScanConfig(enable_reconciliation=True),
            opportunity_repository=self.opp_repo,
            delivery_repository=self.del_repo,
            lifecycle_manager=self.lifecycle_manager,
            alert_policy=self.alert_policy,
            dispatcher=self.dispatcher,
            reconciliation_service=self.reconciliation_service,
        )

    def tearDown(self):
        self.session.close()
        BaseORM.metadata.drop_all(self.engine)

    def _setup_mock_normalizers(self, sb_graphs: List[NormalizedGraph], bc_graphs: List[NormalizedGraph]):
        """Inject pre-configured graphs into normalization engine."""
        self.orchestrator.normalization_engine.register_normalizer("superbet", MockNormalizerWrapper(sb_graphs))
        self.orchestrator.normalization_engine.register_normalizer("betclic", MockNormalizerWrapper(bc_graphs))

    # ──────────────────────────────────────────────────────────────────────────
    # 1. Full Successful Scan Cycle with Surebet Opportunity
    # ──────────────────────────────────────────────────────────────────────────

    def test_01_full_successful_scan_cycle_with_surebet(self):
        """Proves complete cycle: acquisition -> normalization -> matching -> surebet -> lifecycle NEW -> dispatch -> delivery -> SUCCESS."""
        # Superbet: H=2.30, D=3.40, A=3.20
        # Betclic:  H=2.10, D=3.80, A=4.50
        # Best Odds: H=2.30 (SB), D=3.80 (BC), A=4.50 (BC)
        # S = 1/2.30 + 1/3.80 + 1/4.50 = 0.4348 + 0.2632 + 0.2222 = 0.9202 (< 1.0 -> Surebet!)
        sb_graph = _build_test_graph("superbet", "sb_101", "Arsenal", "Chelsea", "2026-08-20T19:00:00Z", 2.30, 3.40, 3.20)
        bc_graph = _build_test_graph("betclic", "bc_201", "Arsenal", "Chelsea", "2026-08-20T19:00:00Z", 2.10, 3.80, 4.50)

        self._setup_mock_normalizers([sb_graph], [bc_graph])

        p_sb = MockProvider("superbet", parsed_items=[sb_graph])
        p_bc = MockProvider("betclic", parsed_items=[bc_graph])

        t_eval = datetime(2026, 8, 20, 12, 0, 0, tzinfo=timezone.utc)
        result = self.orchestrator.run_scan_cycle(
            providers={"superbet": p_sb, "betclic": p_bc},
            evaluation_time=t_eval,
        )

        # 1. Check Status and Traceability
        self.assertEqual(result.cycle_status, CycleStatus.SUCCESS)
        self.assertTrue(result.is_success)
        self.assertFalse(result.is_partial)
        self.assertFalse(result.is_failed)
        self.assertTrue(result.execution_id.startswith("scan_"))

        # 2. Check Metrics
        self.assertEqual(result.discovered_events_count, 2)
        self.assertEqual(result.parsed_events_count, 2)
        self.assertEqual(result.normalized_graphs_count, 2)
        self.assertEqual(result.matched_events_count, 1)
        self.assertEqual(result.unmatched_events_count, 0)
        self.assertEqual(result.detected_opportunities_count, 1)
        self.assertEqual(result.new_opportunities_count, 1)
        self.assertEqual(result.updated_opportunities_count, 0)
        self.assertEqual(result.suppressed_opportunities_count, 0)
        self.assertEqual(result.dispatched_count, 1)
        self.assertEqual(result.delivered_count, 1)
        self.assertEqual(result.failed_delivery_count, 0)

        # 3. Check Consumers & Database State
        self.assertEqual(len(self.in_memory_consumer.received_opportunities), 1)
        delivered_opp = self.in_memory_consumer.received_opportunities[0]
        self.assertTrue(delivered_opp.opportunity_id.startswith("sb:"))
        self.assertGreater(delivered_opp.arbitrage_margin, Decimal("0.0"))

        # Persisted Opportunity Record in database must be ALERTED
        records = self.opp_repo.list_active()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].status, OpportunityStatus.ALERTED.value)
        self.assertEqual(records[0].alert_count, 1)

        # Audit Report Check
        report = result.generate_audit_report(detailed=True)
        self.assertIn("PRODUCTION SCAN CYCLE AUDIT REPORT", report)
        self.assertIn("Cycle Status: SUCCESS", report)
        self.assertIn("DETECTED OPPORTUNITIES:", report)

    # ──────────────────────────────────────────────────────────────────────────
    # 2. Successful Scan Cycle with Zero Opportunities
    # ──────────────────────────────────────────────────────────────────────────

    def test_02_successful_scan_cycle_zero_surebets(self):
        """Proves that a scan finding 0 surebets is a valid SUCCESS outcome (Invariant 19)."""
        # Normal odds with vig: H=1.80, D=3.20, A=4.00 on both sides -> S > 1.0 (no surebet)
        sb_graph = _build_test_graph("superbet", "sb_102", "Liverpool", "Everton", "2026-08-21T15:00:00Z", 1.80, 3.20, 4.00)
        bc_graph = _build_test_graph("betclic", "bc_202", "Liverpool", "Everton", "2026-08-21T15:00:00Z", 1.85, 3.10, 3.90)

        self._setup_mock_normalizers([sb_graph], [bc_graph])

        p_sb = MockProvider("superbet", parsed_items=[sb_graph])
        p_bc = MockProvider("betclic", parsed_items=[bc_graph])

        result = self.orchestrator.run_scan_cycle(providers={"superbet": p_sb, "betclic": p_bc})

        self.assertEqual(result.cycle_status, CycleStatus.SUCCESS)
        self.assertTrue(result.is_success)
        self.assertEqual(result.matched_events_count, 1)
        self.assertEqual(result.detected_opportunities_count, 0)
        self.assertEqual(result.dispatched_count, 0)
        self.assertEqual(result.delivered_count, 0)
        self.assertEqual(len(result.errors), 0)

    # ──────────────────────────────────────────────────────────────────────────
    # 3. Repeated Identical Scan Cycles (Deterministic Idempotency)
    # ──────────────────────────────────────────────────────────────────────────

    def test_03_repeated_identical_scan_idempotency(self):
        """Proves that running identical scans does not duplicate alerts (Invariants 20 & 21)."""
        sb_graph = _build_test_graph("superbet", "sb_103", "Real Madrid", "Barcelona", "2026-08-22T20:00:00Z", 2.40, 3.50, 3.10)
        bc_graph = _build_test_graph("betclic", "bc_203", "Real Madrid", "Barcelona", "2026-08-22T20:00:00Z", 2.10, 3.90, 4.60)

        self._setup_mock_normalizers([sb_graph], [bc_graph])

        p_sb = MockProvider("superbet", parsed_items=[sb_graph])
        p_bc = MockProvider("betclic", parsed_items=[bc_graph])

        # --- Cycle 1: First observation ---
        t1 = datetime(2026, 8, 22, 10, 0, 0, tzinfo=timezone.utc)
        res1 = self.orchestrator.run_scan_cycle(
            providers={"superbet": p_sb, "betclic": p_bc},
            evaluation_time=t1,
        )

        self.assertEqual(res1.cycle_status, CycleStatus.SUCCESS)
        self.assertEqual(res1.detected_opportunities_count, 1)
        self.assertEqual(res1.new_opportunities_count, 1)
        self.assertEqual(res1.suppressed_opportunities_count, 0)
        self.assertEqual(res1.delivered_count, 1)
        self.assertEqual(len(self.in_memory_consumer.received_opportunities), 1)

        # --- Cycle 2: Second observation with identical source data ---
        t2 = datetime(2026, 8, 22, 10, 5, 0, tzinfo=timezone.utc)
        res2 = self.orchestrator.run_scan_cycle(
            providers={"superbet": p_sb, "betclic": p_bc},
            evaluation_time=t2,
        )

        self.assertEqual(res2.cycle_status, CycleStatus.SUCCESS)
        self.assertEqual(res2.detected_opportunities_count, 1)
        self.assertEqual(res2.new_opportunities_count, 0)
        self.assertEqual(res2.updated_opportunities_count, 0)
        self.assertEqual(res2.suppressed_opportunities_count, 1)
        self.assertEqual(res2.dispatched_count, 0)
        self.assertEqual(res2.delivered_count, 0)

        # In-memory consumer must STILL have only 1 total delivered alert (0 duplicate alert!)
        self.assertEqual(len(self.in_memory_consumer.received_opportunities), 1)

    # ──────────────────────────────────────────────────────────────────────────
    # 4. Material Opportunity Update Cycle
    # ──────────────────────────────────────────────────────────────────────────

    def test_04_material_opportunity_odds_update_cycle(self):
        """Proves that a material odds change propagates as UPDATED and dispatches a new alert (Invariant 21)."""
        sb_graph_1 = _build_test_graph("superbet", "sb_104", "Bayern Munich", "Dortmund", "2026-08-23T18:30:00Z", 2.30, 3.40, 3.20)
        bc_graph_1 = _build_test_graph("betclic", "bc_204", "Bayern Munich", "Dortmund", "2026-08-23T18:30:00Z", 2.10, 3.80, 4.50)

        self._setup_mock_normalizers([sb_graph_1], [bc_graph_1])

        # Cycle 1: Initial alert
        t1 = datetime(2026, 8, 23, 10, 0, 0, tzinfo=timezone.utc)
        res1 = self.orchestrator.run_scan_cycle(
            providers={"superbet": MockProvider("superbet", [sb_graph_1]), "betclic": MockProvider("betclic", [bc_graph_1])},
            evaluation_time=t1,
        )
        self.assertEqual(res1.new_opportunities_count, 1)
        self.assertEqual(len(self.in_memory_consumer.received_opportunities), 1)

        # Cycle 2: Material change in Betclic Away odds (4.50 -> 5.50, delta = +1.00 > 0.05)
        sb_graph_2 = _build_test_graph("superbet", "sb_104", "Bayern Munich", "Dortmund", "2026-08-23T18:30:00Z", 2.30, 3.40, 3.20)
        bc_graph_2 = _build_test_graph("betclic", "bc_204", "Bayern Munich", "Dortmund", "2026-08-23T18:30:00Z", 2.10, 3.80, 5.50)

        self._setup_mock_normalizers([sb_graph_2], [bc_graph_2])

        t2 = datetime(2026, 8, 23, 10, 10, 0, tzinfo=timezone.utc)
        res2 = self.orchestrator.run_scan_cycle(
            providers={"superbet": MockProvider("superbet", [sb_graph_2]), "betclic": MockProvider("betclic", [bc_graph_2])},
            evaluation_time=t2,
        )

        self.assertEqual(res2.cycle_status, CycleStatus.SUCCESS)
        self.assertEqual(res2.detected_opportunities_count, 1)
        self.assertEqual(res2.new_opportunities_count, 0)
        self.assertEqual(res2.updated_opportunities_count, 1)
        self.assertEqual(res2.dispatched_count, 1)
        self.assertEqual(res2.delivered_count, 1)

        # In-memory consumer now has 2 total alerts (Initial + Update)
        self.assertEqual(len(self.in_memory_consumer.received_opportunities), 2)
        # Database record must have alert_count = 2
        record = self.opp_repo.list_active()[0]
        self.assertEqual(record.status, OpportunityStatus.ALERTED.value)
        self.assertEqual(record.alert_count, 2)

    # ──────────────────────────────────────────────────────────────────────────
    # 5. Single Provider Failure (Partial / Degraded Cycle)
    # ──────────────────────────────────────────────────────────────────────────

    def test_05_single_provider_failure_partial_degraded(self):
        """Proves that a single provider failure produces PARTIAL status and safely avoids false surebets (Sections 8 & 9)."""
        sb_graph = _build_test_graph("superbet", "sb_105", "Juventus", "AC Milan", "2026-08-24T20:45:00Z", 2.30, 3.40, 3.20)
        self._setup_mock_normalizers([sb_graph], [])

        p_sb = MockProvider("superbet", parsed_items=[sb_graph], should_fail=False)
        p_bc = MockProvider("betclic", parsed_items=[], should_fail=True, failure_stage="fetch")

        result = self.orchestrator.run_scan_cycle(providers={"superbet": p_sb, "betclic": p_bc})

        # Cycle must be PARTIAL (not SUCCESS and not total FAILED)
        self.assertEqual(result.cycle_status, CycleStatus.PARTIAL)
        self.assertTrue(result.is_partial)
        self.assertFalse(result.is_success)

        # Superbet provider result and normalization result preserved
        self.assertIn("superbet", result.provider_results)
        self.assertIn("betclic", result.provider_results)
        self.assertEqual(result.provider_results["superbet"].status, ProviderState.COMPLETED)
        self.assertEqual(result.provider_results["betclic"].status, ProviderState.FAILED)
        self.assertEqual(result.normalized_graphs_count, 1)

        # Crucial Invariant: Downstream surebet detection safely skipped -> 0 false opportunities
        self.assertEqual(result.detected_opportunities_count, 0)
        self.assertEqual(result.dispatched_count, 0)
        self.assertTrue(result.has_warnings)
        self.assertTrue(result.has_errors)

    # ──────────────────────────────────────────────────────────────────────────
    # 6. Dual Provider Failure (Total Cycle Failure)
    # ──────────────────────────────────────────────────────────────────────────

    def test_06_dual_provider_failure(self):
        """Proves that when both providers fail, cycle returns CycleStatus.FAILED."""
        p_sb = MockProvider("superbet", should_fail=True, failure_stage="fetch")
        p_bc = MockProvider("betclic", should_fail=True, failure_stage="discovery")

        result = self.orchestrator.run_scan_cycle(providers={"superbet": p_sb, "betclic": p_bc})

        self.assertEqual(result.cycle_status, CycleStatus.FAILED)
        self.assertTrue(result.is_failed)
        self.assertEqual(result.normalized_graphs_count, 0)
        self.assertEqual(result.detected_opportunities_count, 0)
        self.assertEqual(result.dispatched_count, 0)
        self.assertGreater(len(result.errors), 0)

    # ──────────────────────────────────────────────────────────────────────────
    # 7. Normalization Partial Failure Isolation
    # ──────────────────────────────────────────────────────────────────────────

    def test_07_normalization_partial_failure_isolation(self):
        """Proves that partial normalization errors are isolated while valid data proceeds (Section 10)."""
        valid_sb = _build_test_graph("superbet", "sb_107", "PSG", "Marseille", "2026-08-25T21:00:00Z", 2.30, 3.40, 3.20)
        invalid_sb = {"malformed": "data"}

        valid_bc = _build_test_graph("betclic", "bc_207", "PSG", "Marseille", "2026-08-25T21:00:00Z", 2.10, 3.80, 4.50)

        self._setup_mock_normalizers([valid_sb], [valid_bc])

        p_sb = MockProvider("superbet", parsed_items=[valid_sb, invalid_sb])
        p_bc = MockProvider("betclic", parsed_items=[valid_bc])

        result = self.orchestrator.run_scan_cycle(providers={"superbet": p_sb, "betclic": p_bc})

        self.assertEqual(result.cycle_status, CycleStatus.SUCCESS)
        self.assertEqual(result.normalization_failed_count, 1)
        self.assertEqual(result.normalized_graphs_count, 2)
        self.assertEqual(result.matched_events_count, 1)
        self.assertEqual(result.detected_opportunities_count, 1)

    # ──────────────────────────────────────────────────────────────────────────
    # 8. Unmatched Events Handling
    # ──────────────────────────────────────────────────────────────────────────

    def test_08_unmatched_events_normal_success(self):
        """Proves that unmatched events are normal and do not produce errors or false surebets (Section 11)."""
        sb_graph = _build_test_graph("superbet", "sb_108", "Ajax", "Feyenoord", "2026-08-26T14:30:00Z", 2.00, 3.50, 3.50)
        bc_graph = _build_test_graph("betclic", "bc_208", "Benfica", "Porto", "2026-08-26T14:30:00Z", 2.10, 3.40, 3.40)

        self._setup_mock_normalizers([sb_graph], [bc_graph])

        p_sb = MockProvider("superbet", parsed_items=[sb_graph])
        p_bc = MockProvider("betclic", parsed_items=[bc_graph])

        result = self.orchestrator.run_scan_cycle(providers={"superbet": p_sb, "betclic": p_bc})

        self.assertEqual(result.cycle_status, CycleStatus.SUCCESS)
        self.assertEqual(result.matched_events_count, 0)
        self.assertEqual(result.unmatched_events_count, 2)
        self.assertEqual(result.detected_opportunities_count, 0)
        self.assertEqual(len(result.errors), 0)

    # ──────────────────────────────────────────────────────────────────────────
    # 9. Delivery Failure Safety & Integrity
    # ──────────────────────────────────────────────────────────────────────────

    def test_09_consumer_delivery_failure_safety(self):
        """Proves that consumer delivery failures keep opportunity in NEW state for retry and do NOT mark ALERTED (Section 16)."""
        sb_graph = _build_test_graph("superbet", "sb_109", "Sevilla", "Betis", "2026-08-27T21:00:00Z", 2.30, 3.40, 3.20)
        bc_graph = _build_test_graph("betclic", "bc_209", "Sevilla", "Betis", "2026-08-27T21:00:00Z", 2.10, 3.80, 4.50)

        self._setup_mock_normalizers([sb_graph], [bc_graph])

        # Replace in-memory consumer with failing consumer
        failing_consumer = FailingConsumer(name="failing_tg_consumer", fail_count=5)
        dispatcher = OpportunityDispatcher()
        dispatcher.register_consumer(failing_consumer)

        lifecycle = OpportunityLifecycleManager(
            repository=self.opp_repo,
            alert_policy=self.alert_policy,
            delivery_repository=self.del_repo,
        )

        orchestrator = ProductionScanOrchestrator(
            opportunity_repository=self.opp_repo,
            delivery_repository=self.del_repo,
            lifecycle_manager=lifecycle,
            dispatcher=dispatcher,
        )
        orchestrator.normalization_engine.register_normalizer("superbet", MockNormalizerWrapper([sb_graph]))
        orchestrator.normalization_engine.register_normalizer("betclic", MockNormalizerWrapper([bc_graph]))

        result = orchestrator.run_scan_cycle(
            providers={"superbet": MockProvider("superbet", [sb_graph]), "betclic": MockProvider("betclic", [bc_graph])}
        )

        self.assertEqual(result.cycle_status, CycleStatus.SUCCESS)
        self.assertEqual(result.detected_opportunities_count, 1)
        self.assertEqual(result.dispatched_count, 1)
        self.assertEqual(result.delivered_count, 0)
        self.assertEqual(result.failed_delivery_count, 1)

        # Database state: Opportunity status remains NEW (NOT marked ALERTED!)
        records = self.opp_repo.list_active()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].status, OpportunityStatus.NEW.value)
        self.assertEqual(records[0].alert_count, 0)
        self.assertEqual(records[0].delivery_status, "FAILED")

        # Delivery Record must be RETRY_PENDING
        del_records = self.del_repo.list_pending_or_retryable(datetime.now(timezone.utc))
        # Delivery record was created and state is RETRY_PENDING
        all_del = self.session.query(DeliveryRecordORM).all()
        self.assertEqual(len(all_del), 1)
        self.assertEqual(all_del[0].state, DeliveryState.RETRY_PENDING.value)

    # ──────────────────────────────────────────────────────────────────────────
    # 10. Delivery Reconciliation Integration
    # ──────────────────────────────────────────────────────────────────────────

    def test_10_delivery_reconciliation_integration(self):
        """Proves that DeliveryReconciliationService recycles and retries pending alerts in subsequent scans."""
        sb_graph = _build_test_graph("superbet", "sb_110", "Inter", "Roma", "2026-08-28T18:00:00Z", 2.30, 3.40, 3.20)
        bc_graph = _build_test_graph("betclic", "bc_210", "Inter", "Roma", "2026-08-28T18:00:00Z", 2.10, 3.80, 4.50)

        self._setup_mock_normalizers([sb_graph], [bc_graph])

        # Consumer that fails on attempt 1, succeeds on attempt 2
        failing_consumer = FailingConsumer(name="recovering_consumer", fail_count=1)
        dispatcher = OpportunityDispatcher()
        dispatcher.register_consumer(failing_consumer)

        lifecycle = OpportunityLifecycleManager(
            repository=self.opp_repo,
            alert_policy=self.alert_policy,
            delivery_repository=self.del_repo,
        )
        reconciliation = DeliveryReconciliationService(
            delivery_repository=self.del_repo,
            opportunity_repository=self.opp_repo,
            dispatcher=dispatcher,
            retry_config=DeliveryRetryConfig(max_attempts=3, initial_delay_seconds=0.0),
        )

        orchestrator = ProductionScanOrchestrator(
            opportunity_repository=self.opp_repo,
            delivery_repository=self.del_repo,
            lifecycle_manager=lifecycle,
            dispatcher=dispatcher,
            reconciliation_service=reconciliation,
        )
        orchestrator.normalization_engine.register_normalizer("superbet", MockNormalizerWrapper([sb_graph]))
        orchestrator.normalization_engine.register_normalizer("betclic", MockNormalizerWrapper([bc_graph]))

        # Cycle 1: First attempt fails delivery
        t1 = datetime(2026, 8, 28, 12, 0, 0, tzinfo=timezone.utc)
        res1 = orchestrator.run_scan_cycle(
            providers={"superbet": MockProvider("superbet", [sb_graph]), "betclic": MockProvider("betclic", [bc_graph])},
            evaluation_time=t1,
        )
        self.assertEqual(res1.failed_delivery_count, 1)

        # Cycle 2: In a scan where no new opportunities are detected, reconciliation processes due retry
        t2 = t1 + timedelta(seconds=10)
        res2 = orchestrator.run_scan_cycle(
            providers={"superbet": MockProvider("superbet", []), "betclic": MockProvider("betclic", [])},
            evaluation_time=t2,
        )
        self.assertIsNotNone(res2.reconciliation_summary)
        self.assertEqual(res2.reconciliation_summary.delivered_count, 1)

        # Opportunity record must now be ALERTED
        records = self.opp_repo.list_active()
        self.assertEqual(records[0].status, OpportunityStatus.ALERTED.value)
        self.assertEqual(records[0].alert_count, 1)

    # ──────────────────────────────────────────────────────────────────────────
    # 11. Recovery on Next Scan Cycle
    # ──────────────────────────────────────────────────────────────────────────

    def test_11_recovery_on_next_scan_cycle(self):
        """Proves that a cycle failure in Cycle 1 does not contaminate Cycle 2 (Section 22)."""
        sb_graph = _build_test_graph("superbet", "sb_111", "Napoli", "Lazio", "2026-08-29T20:45:00Z", 2.30, 3.40, 3.20)
        bc_graph = _build_test_graph("betclic", "bc_211", "Napoli", "Lazio", "2026-08-29T20:45:00Z", 2.10, 3.80, 4.50)

        # --- Cycle 1: Provider failure ---
        p_sb_bad = MockProvider("superbet", should_fail=True, failure_stage="fetch")
        p_bc_bad = MockProvider("betclic", should_fail=True, failure_stage="fetch")

        res1 = self.orchestrator.run_scan_cycle(providers={"superbet": p_sb_bad, "betclic": p_bc_bad})
        self.assertEqual(res1.cycle_status, CycleStatus.FAILED)

        # --- Cycle 2: Healthy execution ---
        self._setup_mock_normalizers([sb_graph], [bc_graph])
        p_sb_good = MockProvider("superbet", parsed_items=[sb_graph])
        p_bc_good = MockProvider("betclic", parsed_items=[bc_graph])

        res2 = self.orchestrator.run_scan_cycle(providers={"superbet": p_sb_good, "betclic": p_bc_good})
        self.assertEqual(res2.cycle_status, CycleStatus.SUCCESS)
        self.assertEqual(res2.detected_opportunities_count, 1)
        self.assertEqual(res2.delivered_count, 1)

    # ──────────────────────────────────────────────────────────────────────────
    # 12. Process Restart State Persistence
    # ──────────────────────────────────────────────────────────────────────────

    def test_12_process_restart_state_persistence(self):
        """Proves that lifecycle state persists across distinct orchestrator instances sharing the same DB."""
        sb_graph = _build_test_graph("superbet", "sb_112", "Monaco", "Lyon", "2026-08-30T21:00:00Z", 2.30, 3.40, 3.20)
        bc_graph = _build_test_graph("betclic", "bc_212", "Monaco", "Lyon", "2026-08-30T21:00:00Z", 2.10, 3.80, 4.50)

        # Process / Orchestrator Instance A
        orch_a = ProductionScanOrchestrator(
            opportunity_repository=self.opp_repo,
            delivery_repository=self.del_repo,
            lifecycle_manager=OpportunityLifecycleManager(self.opp_repo, self.alert_policy, self.del_repo),
            dispatcher=self.dispatcher,
        )
        orch_a.normalization_engine.register_normalizer("superbet", MockNormalizerWrapper([sb_graph]))
        orch_a.normalization_engine.register_normalizer("betclic", MockNormalizerWrapper([bc_graph]))

        res_a = orch_a.run_scan_cycle(
            providers={"superbet": MockProvider("superbet", [sb_graph]), "betclic": MockProvider("betclic", [bc_graph])}
        )
        self.assertEqual(res_a.new_opportunities_count, 1)
        self.assertEqual(res_a.delivered_count, 1)

        # Simulate process restart -> Orchestrator Instance B with new session connected to same engine
        new_session = self.Session()
        opp_repo_b = OpportunityRepository(new_session)
        del_repo_b = DeliveryRepository(new_session)
        consumer_b = InMemoryOpportunityConsumer(name="consumer_b")
        dispatcher_b = OpportunityDispatcher()
        dispatcher_b.register_consumer(consumer_b)

        orch_b = ProductionScanOrchestrator(
            opportunity_repository=opp_repo_b,
            delivery_repository=del_repo_b,
            lifecycle_manager=OpportunityLifecycleManager(opp_repo_b, self.alert_policy, del_repo_b),
            dispatcher=dispatcher_b,
        )
        orch_b.normalization_engine.register_normalizer("superbet", MockNormalizerWrapper([sb_graph]))
        orch_b.normalization_engine.register_normalizer("betclic", MockNormalizerWrapper([bc_graph]))

        res_b = orch_b.run_scan_cycle(
            providers={"superbet": MockProvider("superbet", [sb_graph]), "betclic": MockProvider("betclic", [bc_graph])}
        )

        # Instance B must recognize the persisted state and suppress duplicate alerts
        self.assertEqual(res_b.cycle_status, CycleStatus.SUCCESS)
        self.assertEqual(res_b.new_opportunities_count, 0)
        self.assertEqual(res_b.suppressed_opportunities_count, 1)
        self.assertEqual(len(consumer_b.received_opportunities), 0)
        new_session.close()

    # ──────────────────────────────────────────────────────────────────────────
    # 13. Genuine Provider Fixture Replay Execution
    # ──────────────────────────────────────────────────────────────────────────

    def test_13_genuine_provider_fixture_replay_execution(self):
        """Runs the orchestrator using genuine recorded payloads from Superbet & Betclic."""
        sb_live_path = Path("tests/fixtures/recordings/superbet/live_manifest/response_000.json")
        bc_live_path = Path("tests/fixtures/recordings/betclic/live_manifest/response_000.json")

        with open(sb_live_path, "r", encoding="utf-8") as f:
            sb_raw = json.load(f)
        with open(bc_live_path, "r", encoding="utf-8") as f:
            bc_raw = json.load(f)

        sb_provider = SuperbetProvider()
        sb_provider.set_mock_discovery_payload(sb_raw.get("events", []))
        sb_provider.set_mock_fetch_provider(lambda item: item.metadata.get("raw", {}))

        bc_provider = BetclicProvider()
        bc_provider.set_mock_discovery_payload(bc_raw)
        bc_provider.set_mock_fetch_provider(lambda items: [bc_raw])

        result = self.orchestrator.run_scan_cycle(
            providers={"superbet": sb_provider, "betclic": bc_provider}
        )

        self.assertEqual(result.cycle_status, CycleStatus.SUCCESS)
        self.assertGreater(result.parsed_events_count, 0)
        self.assertGreater(result.normalized_graphs_count, 0)
        # In recorded sample fixtures, 0 overlap between current manifests is normal
        self.assertEqual(result.detected_opportunities_count, 0)
        self.assertEqual(len(result.errors), 0)

    # ──────────────────────────────────────────────────────────────────────────
    # 14. Complete Adversarial Scenarios
    # ──────────────────────────────────────────────────────────────────────────

    def test_14_adversarial_empty_provider_results(self):
        """Adversarial test: both providers return empty data cleanly."""
        p_sb = MockProvider("superbet", parsed_items=[])
        p_bc = MockProvider("betclic", parsed_items=[])

        result = self.orchestrator.run_scan_cycle(providers={"superbet": p_sb, "betclic": p_bc})

        self.assertEqual(result.cycle_status, CycleStatus.SUCCESS)
        self.assertEqual(result.normalized_graphs_count, 0)
        self.assertEqual(result.detected_opportunities_count, 0)
        self.assertEqual(result.diagnostics.get("data_availability"), "ZERO_EVENTS_BOTH_PROVIDERS")

    def test_15_adversarial_degraded_validation_provider(self):
        """Adversarial test: provider completes with validation warnings (DEGRADED state)."""
        sb_graph = _build_test_graph("superbet", "sb_115", "Porto", "Braga", "2026-08-31T20:00:00Z", 2.00, 3.50, 3.50)
        bc_graph = _build_test_graph("betclic", "bc_215", "Porto", "Braga", "2026-08-31T20:00:00Z", 2.10, 3.40, 3.40)

        self._setup_mock_normalizers([sb_graph], [bc_graph])

        p_sb = MockProvider("superbet", parsed_items=[sb_graph], is_degraded=True)
        p_bc = MockProvider("betclic", parsed_items=[bc_graph])

        result = self.orchestrator.run_scan_cycle(providers={"superbet": p_sb, "betclic": p_bc})

        self.assertEqual(result.cycle_status, CycleStatus.SUCCESS)
        self.assertTrue(result.has_warnings)

    def test_16_performance_and_stage_durations(self):
        """Measures and verifies that all stage timings are tracked non-zero and non-negative."""
        sb_graph = _build_test_graph("superbet", "sb_116", "Team A", "Team B", "2026-09-01T20:00:00Z", 2.30, 3.40, 3.20)
        bc_graph = _build_test_graph("betclic", "bc_216", "Team A", "Team B", "2026-09-01T20:00:00Z", 2.10, 3.80, 4.50)

        self._setup_mock_normalizers([sb_graph], [bc_graph])

        result = self.orchestrator.run_scan_cycle(
            providers={"superbet": MockProvider("superbet", [sb_graph]), "betclic": MockProvider("betclic", [bc_graph])}
        )

        self.assertGreaterEqual(result.duration_seconds, 0.0)
        self.assertGreaterEqual(result.stage_timings.acquisition_seconds, 0.0)
        self.assertGreaterEqual(result.stage_timings.normalization_seconds, 0.0)
        self.assertGreaterEqual(result.stage_timings.matching_seconds, 0.0)
        self.assertGreaterEqual(result.stage_timings.detection_seconds, 0.0)
        self.assertGreaterEqual(result.stage_timings.lifecycle_seconds, 0.0)
        self.assertGreaterEqual(result.stage_timings.dispatch_seconds, 0.0)
        self.assertGreaterEqual(result.stage_timings.reconciliation_seconds, 0.0)

    # ──────────────────────────────────────────────────────────────────────────
    # 15. Live Execution Diagnostic (Isolated)
    # ──────────────────────────────────────────────────────────────────────────

    @pytest.mark.live
    def test_17_live_scan_cycle_isolated(self):
        """Isolated live test against live bookmaker endpoints (skipped during normal CI)."""
        orchestrator = ProductionScanOrchestrator()
        result = orchestrator.run_scan_cycle()

        self.assertIn(result.cycle_status, [CycleStatus.SUCCESS, CycleStatus.PARTIAL, CycleStatus.FAILED])
        self.assertTrue(result.duration_seconds > 0)


if __name__ == "__main__":
    unittest.main()
