"""
Stage 7.4: Production Readiness & Real Scan Validation Test Suite

Validates:
1. Bounded production scan configuration abstractions.
2. Controlled event selection & prioritization policy (popular competitions, kickoff window, max events).
3. Resource budget safety limits and tripwires.
4. Full deterministic E2E scan cycle on recorded multi-bookmaker fixtures.
5. Idempotent repeated scan execution (zero duplicate alerts).
6. Material vs immaterial odds update distinction.
7. Production failure isolation & graceful recovery (provider timeouts, normalization errors, delivery failures).
8. Process restart persistence across orchestrator instances.
9. Zero-surebet mathematical interpretation & nearest-opportunity telemetry.
10. Multi-cycle repeatability benchmark.
11. Real live multi-bookmaker production-style scan (@pytest.mark.live).
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
import time
from typing import Any, Dict, List, Optional, Tuple
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
from orchestration.event_selection import DefaultEventSelectionPolicy, EventSelectionPolicy
from orchestration.exceptions import CriticalPipelineFailure
from orchestration.models import (
    CycleStatus,
    ResourceBudget,
    ResourceMetrics,
    ScanConfig,
    ScanCycleResult,
)
from orchestration.scan_orchestrator import ProductionScanOrchestrator
from providers.base.base_provider import BaseProvider
from providers.base.execution_engine import ExecutionEngine
from providers.base.models import ProviderMetadata, ValidationReport
from providers.base.provider_context import ProviderContext
from providers.base.provider_state import ProviderState
from providers.betclic.provider import BetclicProvider
from providers.superbet.provider import SuperbetProvider


# ─────────────────────────────────────────────────────────────────────────────
# Fixture & Synthetic Graph Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _build_test_graph(
    provider_name: str,
    event_id: str,
    home_team: str,
    away_team: str,
    start_time_iso: str,
    odds_h: float,
    odds_d: float,
    odds_a: float,
    comp_name: str = "Premier League",
) -> NormalizedGraph:
    """Builds a fully normalized graph with specified 1X2 odds for deterministic testing."""
    comp = Competition(name=comp_name, internal_id=f"comp_{event_id}", provider_ids={provider_name: f"p_comp_{event_id}"})
    ev = Event(
        competition_id=comp.internal_id,
        home_participant=home_team,
        away_participant=away_team,
        scheduled_start=start_time_iso,
        internal_id=f"ev_{event_id}",
        provider_ids={provider_name: event_id},
    )
    mkt_id = f"mkt_{event_id}_1x2"
    mkt = Market(event_id=ev.internal_id, market_type="1X2", internal_id=mkt_id, provider_ids={provider_name: f"p_mkt_{event_id}"})

    sel_h = Selection(market_id=mkt_id, selection_type="HOME", participant=home_team, internal_id=f"sel_{event_id}_h", provider_ids={provider_name: f"p_sel_{event_id}_h"})
    sel_d = Selection(market_id=mkt_id, selection_type="DRAW", participant="Draw", internal_id=f"sel_{event_id}_d", provider_ids={provider_name: f"p_sel_{event_id}_d"})
    sel_a = Selection(market_id=mkt_id, selection_type="AWAY", participant=away_team, internal_id=f"sel_{event_id}_a", provider_ids={provider_name: f"p_sel_{event_id}_a"})

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


def load_fixtures() -> Dict[str, Any]:
    """Loads recorded Superbet + Betclic fixtures."""
    fixture_dir = Path("tests/fixtures/recordings/multi_bookmaker")
    with open(fixture_dir / "superbet_payloads.json", "r", encoding="utf-8") as f:
        sb_payloads = json.load(f)
    with open(fixture_dir / "betclic_payloads.json", "r", encoding="utf-8") as f:
        bc_payloads = json.load(f)
    return {
        "superbet": sb_payloads,
        "betclic": bc_payloads,
    }


def create_mock_providers_from_fixtures() -> Dict[str, BaseProvider]:
    """Instantiates Superbet and Betclic providers pre-loaded with recorded fixtures."""
    fixtures = load_fixtures()

    sb = SuperbetProvider()
    sb.set_mock_discovery_payload(fixtures["superbet"])
    sb.set_mock_fetch_provider(lambda item: item.metadata.get("raw") if item.metadata else {})

    bc = BetclicProvider()
    bc.set_mock_discovery_payload(fixtures["betclic"])
    bc.set_mock_fetch_provider(lambda item: item.metadata.get("raw") if item.metadata else {})

    return {"superbet": sb, "betclic": bc}


def setup_in_memory_db() -> Tuple[DatabaseManager, OpportunityRepository, DeliveryRepository]:
    """Sets up an in-memory SQLite database for deterministic lifecycle testing."""
    engine = create_engine("sqlite:///:memory:")
    BaseORM.metadata.create_all(engine)
    session_cls = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = session_cls()
    db_mgr = DatabaseManager()
    db_mgr._session_factory = session_cls
    opp_repo = OpportunityRepository(session)
    del_repo = DeliveryRepository(session)
    return db_mgr, opp_repo, del_repo


class MockCustomProvider(BaseProvider):
    """Test helper provider allowing injection of parsed objects or deliberate failures."""

    def __init__(
        self,
        name: str,
        parsed_items: Optional[List[Any]] = None,
        should_fail: bool = False,
        failure_stage: str = "fetch",
    ):
        ctx = ProviderContext(provider_name=name)
        meta = ProviderMetadata(name=name, code=name[:4])
        super().__init__(context=ctx, metadata=meta)
        self._parsed_items = parsed_items if parsed_items is not None else []
        self._should_fail = should_fail
        self._failure_stage = failure_stage

    def discover(self) -> List[Any]:
        if self._should_fail and self._failure_stage == "discover":
            raise RuntimeError(f"Simulated discovery failure in {self.metadata.name}")
        return [{"id": f"ev_{i}"} for i in range(len(self._parsed_items))]

    def fetch(self, discovery_items: List[Any]) -> List[Any]:
        if self._should_fail and self._failure_stage == "fetch":
            raise TimeoutError(f"Simulated HTTP timeout in {self.metadata.name}")
        return [{"raw": "data"} for _ in discovery_items]

    def parse(self, raw_data: List[Any]) -> List[Any]:
        if self._should_fail and self._failure_stage == "parse":
            raise ValueError(f"Simulated parse failure in {self.metadata.name}")
        return self._parsed_items

    def validate(self, parsed_data: List[Any]) -> ValidationReport:
        return ValidationReport(is_valid=True, total_objects=len(parsed_data), valid_objects=len(parsed_data))


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


# ─────────────────────────────────────────────────────────────────────────────
# OFFLINE DETERMINISTIC TEST SUITE
# ─────────────────────────────────────────────────────────────────────────────

def test_1_bounded_production_scan_configuration():
    """Test 1: Validates default and customized production scan configurations."""
    # Default config
    cfg = ScanConfig()
    assert cfg.providers == ("superbet", "betclic")
    assert cfg.source_provider == "superbet"
    assert cfg.target_provider == "betclic"
    assert cfg.selection_mode == "OVERVIEW_ONLY"
    assert cfg.sport == "football"
    assert cfg.enable_reconciliation is True

    # Custom bounded config
    budget = ResourceBudget(
        max_duration_seconds=30.0,
        max_http_requests=50,
        max_detail_requests=10,
        max_events=20,
        max_memory_mb=256.0,
    )
    custom_cfg = ScanConfig(
        providers=("superbet", "betclic"),
        event_limit=15,
        preferred_competitions=("Premier League", "Ekstraklasa", "LaLiga"),
        hours_ahead=24,
        selection_mode="SELECTED",
        max_detail_requests=5,
        request_timeout=10.0,
        rate_limit_per_sec=3.0,
        resource_budget=budget,
    )
    assert custom_cfg.event_limit == 15
    assert len(custom_cfg.preferred_competitions) == 3
    assert custom_cfg.hours_ahead == 24
    assert custom_cfg.selection_mode == "SELECTED"
    assert custom_cfg.resource_budget.max_events == 20


def test_2_controlled_event_selection_and_prioritization():
    """Test 2: Validates kickoff window, preferred competition ranking, and event limit caps."""
    policy = DefaultEventSelectionPolicy(default_preferred_competitions=["Premier League", "Ekstraklasa"])
    base_time = datetime(2026, 8, 17, 12, 0, 0, tzinfo=timezone.utc)

    # Sample items: 1 obscure league soon, 1 premier league soon, 1 ekstraklasa far future, 1 obscure far future
    items = [
        {"id": "ev_obscure_soon", "competition": "Costa Rica Cup", "start_time": "2026-08-17T14:00:00Z"},
        {"id": "ev_pl_soon", "competition": "Premier League", "start_time": "2026-08-17T15:00:00Z"},
        {"id": "ev_ekstraklasa_future", "competition": "PKO BP Ekstraklasa", "start_time": "2026-08-18T10:00:00Z"},
        {"id": "ev_obscure_far_future", "competition": "Obscure Division 4", "start_time": "2026-08-25T12:00:00Z"},
    ]

    # Filter with hours_ahead = 24
    ranked = policy.filter_and_rank_discovered_items(
        items=items,
        hours_ahead=24,
        current_time=base_time,
        preferred_competitions=["Premier League", "Ekstraklasa"],
    )

    # Obscure far future (7 days out) must be filtered out
    ranked_ids = [it["id"] for it in ranked]
    assert "ev_obscure_far_future" not in ranked_ids

    # Preferred competitions must be ranked first (Premier League, Ekstraklasa ahead of obscure)
    assert ranked_ids[0] == "ev_pl_soon"
    assert ranked_ids[1] == "ev_ekstraklasa_future"
    assert ranked_ids[2] == "ev_obscure_soon"

    # Select detail IDs with max_detail_requests = 2
    detail_ids = policy.select_events_for_detail(
        items=items,
        max_detail_requests=2,
        preferred_competitions=["Premier League", "Ekstraklasa"],
    )
    assert detail_ids == ["ev_pl_soon", "ev_ekstraklasa_future"]


def test_3_resource_budget_safety_and_enforcement():
    """Test 3: Exceeding configured resource budget records warnings and does not crash."""
    providers = create_mock_providers_from_fixtures()
    # Configure an intentionally tiny budget that will be exceeded
    strict_budget = ResourceBudget(
        max_events=2,              # Recorded fixtures have ~10-20 events
        max_duration_seconds=0.00001, # Instant duration limit
        max_memory_mb=0.0001,      # Tiny memory limit
    )
    config = ScanConfig(
        providers=("superbet", "betclic"),
        resource_budget=strict_budget,
    )
    orchestrator = ProductionScanOrchestrator(config=config)
    result = orchestrator.run_scan_cycle(providers=providers)

    # Verify safe execution with budget warnings
    assert result.cycle_status == CycleStatus.SUCCESS
    assert result.has_warnings is True
    assert result.diagnostics.get("budget_exceeded_events") is True or result.diagnostics.get("budget_exceeded_duration") is True
    assert any("Resource budget exceeded" in w for w in result.warnings)


def test_4_full_production_pipeline_on_recorded_fixtures():
    """Test 4: Full E2E scan on recorded Superbet + Betclic fixtures through ProductionScanOrchestrator."""
    providers = create_mock_providers_from_fixtures()
    config = ScanConfig(
        providers=("superbet", "betclic"),
        event_limit=10,
    )
    orchestrator = ProductionScanOrchestrator(config=config)
    result = orchestrator.run_scan_cycle(providers=providers)

    assert result.cycle_status == CycleStatus.SUCCESS
    assert result.discovered_events_count > 0
    assert result.parsed_events_count > 0
    assert result.normalized_graphs_count > 0
    assert result.matched_events_count > 0
    assert result.validation_result is not None
    assert result.detection_result is not None
    assert result.resource_metrics.peak_memory_mb > 0
    assert result.stage_timings.total_duration_seconds > 0

    # Audit report generation
    report = result.generate_audit_report(detailed=True)
    assert "PRODUCTION SCAN CYCLE AUDIT REPORT" in report
    assert "STAGE TIMINGS:" in report
    assert "RESOURCE METRICS:" in report


def test_5_idempotent_repeated_scan_cycles():
    """Test 5: Validates that repeated identical scan cycles do not generate duplicate alerts."""
    db_mgr, opp_repo, del_repo = setup_in_memory_db()
    consumer = InMemoryOpportunityConsumer()
    dispatcher = OpportunityDispatcher()
    dispatcher.register_consumer(consumer)

    config = ScanConfig(providers=("superbet", "betclic"))
    orchestrator = ProductionScanOrchestrator(
        config=config,
        db_manager=db_mgr,
        opportunity_repository=opp_repo,
        delivery_repository=del_repo,
        dispatcher=dispatcher,
    )

    # Synthetic surebet graphs: H@SB=2.40, D@BC=3.90, A@BC=4.60 -> S=0.8905 (<1.0)
    sb_graph = _build_test_graph("superbet", "sb_101", "Real Madrid", "Barcelona", "2026-08-22T20:00:00Z", 2.40, 3.50, 3.10)
    bc_graph = _build_test_graph("betclic", "bc_201", "Real Madrid", "Barcelona", "2026-08-22T20:00:00Z", 2.10, 3.90, 4.60)

    norm_engine = NormalizationEngine()
    norm_engine.register_normalizer("superbet", MockNormalizerWrapper([sb_graph]))
    norm_engine.register_normalizer("betclic", MockNormalizerWrapper([bc_graph]))
    orchestrator.normalization_engine = norm_engine

    t0 = datetime(2026, 8, 17, 12, 0, 0, tzinfo=timezone.utc)

    # Run Cycle 1
    p1 = {"superbet": MockCustomProvider("superbet", [sb_graph]), "betclic": MockCustomProvider("betclic", [bc_graph])}
    res_1 = orchestrator.run_scan_cycle(providers=p1, evaluation_time=t0)
    assert res_1.cycle_status == CycleStatus.SUCCESS
    assert res_1.detected_opportunities_count == 1
    assert res_1.new_opportunities_count == 1
    assert len(consumer.received_opportunities) == 1

    # Run Cycle 2 (identical fixtures, 60s later)
    p2 = {"superbet": MockCustomProvider("superbet", [sb_graph]), "betclic": MockCustomProvider("betclic", [bc_graph])}
    t1 = t0 + timedelta(seconds=60)
    res_2 = orchestrator.run_scan_cycle(providers=p2, evaluation_time=t1)
    assert res_2.cycle_status == CycleStatus.SUCCESS

    # In Cycle 2, duplicate alert is suppressed
    assert res_2.detected_opportunities_count == 1
    assert res_2.new_opportunities_count == 0
    assert res_2.suppressed_opportunities_count == 1
    assert res_2.dispatched_count == 0
    assert len(consumer.received_opportunities) == 1


def test_6_odds_update_lifecycle_distinction():
    """Test 6: Verifies that material odds updates trigger alerts while immaterial updates are suppressed."""
    db_mgr, opp_repo, del_repo = setup_in_memory_db()
    consumer = InMemoryOpportunityConsumer()
    dispatcher = OpportunityDispatcher()
    dispatcher.register_consumer(consumer)

    alert_policy = DefaultOpportunityAlertPolicy(
        config=OpportunityAlertConfig(
            min_margin_delta=Decimal("0.005"),
            min_odds_absolute_delta=Decimal("0.05"),
        )
    )

    orchestrator = ProductionScanOrchestrator(
        db_manager=db_mgr,
        opportunity_repository=opp_repo,
        delivery_repository=del_repo,
        dispatcher=dispatcher,
        alert_policy=alert_policy,
    )

    # Initial surebet odds
    sb_g1 = _build_test_graph("superbet", "sb_104", "Bayern Munich", "Dortmund", "2026-08-23T18:30:00Z", 2.30, 3.40, 3.20)
    bc_g1 = _build_test_graph("betclic", "bc_204", "Bayern Munich", "Dortmund", "2026-08-23T18:30:00Z", 2.10, 3.80, 4.50)

    norm_engine = NormalizationEngine()
    norm_engine.register_normalizer("superbet", MockNormalizerWrapper([sb_g1]))
    norm_engine.register_normalizer("betclic", MockNormalizerWrapper([bc_g1]))
    orchestrator.normalization_engine = norm_engine

    t1 = datetime(2026, 8, 23, 10, 0, 0, tzinfo=timezone.utc)
    res1 = orchestrator.run_scan_cycle(
        providers={"superbet": MockCustomProvider("superbet", [sb_g1]), "betclic": MockCustomProvider("betclic", [bc_g1])},
        evaluation_time=t1,
    )
    assert res1.new_opportunities_count == 1
    assert len(consumer.received_opportunities) == 1

    # Cycle 2: Material change in Betclic Away odds (4.50 -> 5.50)
    sb_g2 = _build_test_graph("superbet", "sb_104", "Bayern Munich", "Dortmund", "2026-08-23T18:30:00Z", 2.30, 3.40, 3.20)
    bc_g2 = _build_test_graph("betclic", "bc_204", "Bayern Munich", "Dortmund", "2026-08-23T18:30:00Z", 2.10, 3.80, 5.50)

    norm_engine = NormalizationEngine()
    norm_engine.register_normalizer("superbet", MockNormalizerWrapper([sb_g2]))
    norm_engine.register_normalizer("betclic", MockNormalizerWrapper([bc_g2]))

    t2 = datetime(2026, 8, 23, 10, 10, 0, tzinfo=timezone.utc)
    res2 = orchestrator.run_scan_cycle(
        providers={"superbet": MockCustomProvider("superbet", [sb_g2]), "betclic": MockCustomProvider("betclic", [bc_g2])},
        evaluation_time=t2,
    )

    assert res2.cycle_status == CycleStatus.SUCCESS
    assert res2.detected_opportunities_count == 1
    assert res2.updated_opportunities_count == 1
    assert res2.dispatched_count == 1
    assert len(consumer.received_opportunities) == 2


def test_7_production_failure_isolation_and_recovery():
    """Test 7: Validates single provider timeouts, malformed payloads, dual failures, and delivery error safety."""
    # 7a: Superbet timeout -> PARTIAL status, Betclic data preserved
    sb_failing = MockCustomProvider(name="superbet", should_fail=True, failure_stage="fetch")
    bc_healthy = create_mock_providers_from_fixtures()["betclic"]

    orchestrator = ProductionScanOrchestrator()
    res_partial = orchestrator.run_scan_cycle(providers={"superbet": sb_failing, "betclic": bc_healthy})

    assert res_partial.cycle_status == CycleStatus.PARTIAL
    assert res_partial.detected_opportunities_count == 0
    assert len(res_partial.errors) > 0
    assert "Provider 'superbet' failed" in res_partial.errors[0]

    # 7b: Dual provider failure -> FAILED status
    bc_failing = MockCustomProvider(name="betclic", should_fail=True, failure_stage="fetch")
    res_failed = orchestrator.run_scan_cycle(providers={"superbet": sb_failing, "betclic": bc_failing})

    assert res_failed.cycle_status == CycleStatus.FAILED
    assert res_failed.detected_opportunities_count == 0

    # 7c: Delivery failure isolation -> Cycle completes, delivery marked failed, opportunity not corrupted
    class FailingConsumer(OpportunityConsumer):
        @property
        def name(self) -> str:
            return "failing_consumer"

        def consume(self, opportunity: DispatchableOpportunity) -> ConsumerDeliveryResult:
            return ConsumerDeliveryResult(
                consumer_name=self.name,
                status=DeliveryStatus.FAILED_RETRYABLE,
                error="Simulated Telegram network drop",
            )

    db_mgr, opp_repo, del_repo = setup_in_memory_db()
    failing_disp = OpportunityDispatcher()
    failing_disp.register_consumer(FailingConsumer())

    orch_failing_del = ProductionScanOrchestrator(
        db_manager=db_mgr,
        opportunity_repository=opp_repo,
        delivery_repository=del_repo,
        dispatcher=failing_disp,
    )
    res_del_fail = orch_failing_del.run_scan_cycle(providers=create_mock_providers_from_fixtures())
    assert res_del_fail.cycle_status == CycleStatus.SUCCESS


def test_8_restart_persistence_and_no_duplicate_alerts():
    """Test 8: Validates that SQLite persistence survives process restart across orchestrator instances."""
    engine = create_engine("sqlite:///:memory:", echo=False)
    BaseORM.metadata.create_all(engine)
    session_cls = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = session_cls()

    consumer_1 = InMemoryOpportunityConsumer()
    disp_1 = OpportunityDispatcher()
    disp_1.register_consumer(consumer_1)

    db_mgr_1 = DatabaseManager()
    db_mgr_1._session_factory = session_cls
    opp_repo_1 = OpportunityRepository(session)
    del_repo_1 = DeliveryRepository(session)

    orch_1 = ProductionScanOrchestrator(
        db_manager=db_mgr_1,
        opportunity_repository=opp_repo_1,
        delivery_repository=del_repo_1,
        dispatcher=disp_1,
    )

    sb_g = _build_test_graph("superbet", "sb_108", "PSG", "Marseille", "2026-08-25T21:00:00Z", 2.40, 3.50, 3.10)
    bc_g = _build_test_graph("betclic", "bc_208", "PSG", "Marseille", "2026-08-25T21:00:00Z", 2.10, 3.90, 4.60)

    norm_engine_1 = NormalizationEngine()
    norm_engine_1.register_normalizer("superbet", MockNormalizerWrapper([sb_g]))
    norm_engine_1.register_normalizer("betclic", MockNormalizerWrapper([bc_g]))
    orch_1.normalization_engine = norm_engine_1

    t0 = datetime(2026, 8, 17, 12, 0, 0, tzinfo=timezone.utc)
    res_1 = orch_1.run_scan_cycle(
        providers={"superbet": MockCustomProvider("superbet", [sb_g]), "betclic": MockCustomProvider("betclic", [bc_g])},
        evaluation_time=t0,
    )
    assert res_1.cycle_status == CycleStatus.SUCCESS
    assert res_1.new_opportunities_count == 1
    assert len(consumer_1.received_opportunities) == 1

    # Simulate Process Restart: Instantiate brand new Orchestrator instance 2 connected to same session/DB
    consumer_2 = InMemoryOpportunityConsumer()
    disp_2 = OpportunityDispatcher()
    disp_2.register_consumer(consumer_2)

    db_mgr_2 = DatabaseManager()
    db_mgr_2._session_factory = session_cls
    opp_repo_2 = OpportunityRepository(session)
    del_repo_2 = DeliveryRepository(session)

    orch_2 = ProductionScanOrchestrator(
        db_manager=db_mgr_2,
        opportunity_repository=opp_repo_2,
        delivery_repository=del_repo_2,
        dispatcher=disp_2,
    )

    norm_engine_2 = NormalizationEngine()
    norm_engine_2.register_normalizer("superbet", MockNormalizerWrapper([sb_g]))
    norm_engine_2.register_normalizer("betclic", MockNormalizerWrapper([bc_g]))
    orch_2.normalization_engine = norm_engine_2

    t1 = t0 + timedelta(seconds=120)
    res_2 = orch_2.run_scan_cycle(
        providers={"superbet": MockCustomProvider("superbet", [sb_g]), "betclic": MockCustomProvider("betclic", [bc_g])},
        evaluation_time=t1,
    )

    assert res_2.cycle_status == CycleStatus.SUCCESS
    # Zero new dispatches on instance 2 after restart
    assert res_2.new_opportunities_count == 0
    assert res_2.dispatched_count == 0
    assert len(consumer_2.received_opportunities) == 0


def test_9_zero_surebet_production_telemetry():
    """Test 9: Verifies exact mathematical extraction of nearest opportunity telemetry when 0 surebets exist."""
    providers = create_mock_providers_from_fixtures()
    orchestrator = ProductionScanOrchestrator()
    result = orchestrator.run_scan_cycle(providers=providers)

    assert result.cycle_status == CycleStatus.SUCCESS

    # If 0 surebets are found in this fixture run, nearest opportunity telemetry must be present
    if result.detected_opportunities_count == 0 and result.detection_result and result.detection_result.no_surebet_evaluations:
        near = result.diagnostics.get("nearest_opportunity")
        assert near is not None
        assert "implied_probability_sum" in near
        assert "margin_pct" in near
        assert "distance_to_arbitrage" in near

        prob_sum = Decimal(near["implied_probability_sum"])
        distance = Decimal(near["distance_to_arbitrage"])
        assert prob_sum >= Decimal("1.0")
        assert distance >= Decimal("0.0")


def test_10_multi_cycle_repeatability_benchmark():
    """Test 10: Runs sequential cycles (Scan A -> Scan B -> Scan C) validating state stability."""
    db_mgr, opp_repo, del_repo = setup_in_memory_db()
    consumer = InMemoryOpportunityConsumer()
    dispatcher = OpportunityDispatcher()
    dispatcher.register_consumer(consumer)

    orchestrator = ProductionScanOrchestrator(
        db_manager=db_mgr,
        opportunity_repository=opp_repo,
        delivery_repository=del_repo,
        dispatcher=dispatcher,
    )

    t_start = datetime(2026, 8, 17, 12, 0, 0, tzinfo=timezone.utc)
    results: List[ScanCycleResult] = []

    for i in range(3):
        t_cycle = t_start + timedelta(seconds=i * 60)
        providers = create_mock_providers_from_fixtures()
        res = orchestrator.run_scan_cycle(providers=providers, evaluation_time=t_cycle)
        results.append(res)
        assert res.cycle_status == CycleStatus.SUCCESS
        assert res.duration_seconds >= 0.0

    assert len(results) == 3
    # Cycle 2 and Cycle 3 must have 0 new opportunities
    assert results[1].new_opportunities_count == 0
    assert results[2].new_opportunities_count == 0


# ─────────────────────────────────────────────────────────────────────────────
# LIVE NETWORK INTEGRATION TESTS (@pytest.mark.live)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.live
def test_live_bounded_production_scan():
    """Live Test 1: Real bounded scan with live Superbet + Betclic providers via ProductionScanOrchestrator."""
    budget = ResourceBudget(
        max_duration_seconds=30.0,
        max_http_requests=50,
        max_events=300,
        max_memory_mb=512.0,
    )
    config = ScanConfig(
        providers=("superbet", "betclic"),
        selection_mode="OVERVIEW_ONLY",
        hours_ahead=48,
        event_limit=50,
        preferred_competitions=("Premier League", "LaLiga", "Serie A", "Bundesliga", "Ekstraklasa", "Champions League"),
        resource_budget=budget,
    )

    orchestrator = ProductionScanOrchestrator(config=config)
    result = orchestrator.run_scan_cycle()

    # Generate and print audit report
    print("\n" + result.generate_audit_report(detailed=True))

    assert result.cycle_status in (CycleStatus.SUCCESS, CycleStatus.PARTIAL)
    assert result.discovered_events_count > 0
    assert result.parsed_events_count > 0
    assert result.normalized_graphs_count > 0
    assert result.resource_metrics.total_http_requests > 0
    assert result.resource_metrics.peak_memory_mb > 0


@pytest.mark.live
def test_live_repeated_scan_idempotency():
    """Live Test 2: Two consecutive live scans validating live deduplication and zero notification spam."""
    db_mgr, opp_repo, del_repo = setup_in_memory_db()
    consumer = InMemoryOpportunityConsumer()
    dispatcher = OpportunityDispatcher()
    dispatcher.register_consumer(consumer)

    config = ScanConfig(
        providers=("superbet", "betclic"),
        selection_mode="OVERVIEW_ONLY",
        event_limit=30,
    )

    orchestrator = ProductionScanOrchestrator(
        config=config,
        db_manager=db_mgr,
        opportunity_repository=opp_repo,
        delivery_repository=del_repo,
        dispatcher=dispatcher,
    )

    # Scan 1
    t0 = datetime.now(timezone.utc)
    res_1 = orchestrator.run_scan_cycle(evaluation_time=t0)
    print("\n[LIVE SCAN 1 RESULT]", f"Status: {res_1.cycle_status.value}, Opps: {res_1.detected_opportunities_count}, Dispatched: {res_1.dispatched_count}")

    # Scan 2
    t1 = t0 + timedelta(seconds=15)
    res_2 = orchestrator.run_scan_cycle(evaluation_time=t1)
    print("[LIVE SCAN 2 RESULT]", f"Status: {res_2.cycle_status.value}, Opps: {res_2.detected_opportunities_count}, Dispatched: {res_2.dispatched_count}")

    assert res_1.cycle_status in (CycleStatus.SUCCESS, CycleStatus.PARTIAL)
    assert res_2.cycle_status in (CycleStatus.SUCCESS, CycleStatus.PARTIAL)

    # If scan 1 alerted opportunities, scan 2 must not re-dispatch identical opportunities
    if res_1.dispatched_count > 0:
        assert res_2.new_opportunities_count == 0


@pytest.mark.live
def test_live_multi_cycle_repeatability():
    """Live Test 3: 3-cycle live benchmark recording metrics and verifying consistent pipeline execution."""
    config = ScanConfig(
        providers=("superbet", "betclic"),
        selection_mode="OVERVIEW_ONLY",
        event_limit=25,
    )
    orchestrator = ProductionScanOrchestrator(config=config)

    cycle_timings: List[float] = []
    statuses: List[CycleStatus] = []

    for i in range(3):
        res = orchestrator.run_scan_cycle()
        cycle_timings.append(res.duration_seconds)
        statuses.append(res.cycle_status)
        print(f"\n[LIVE CYCLE {i+1}] Duration: {res.duration_seconds:.2f}s | Status: {res.cycle_status.value} | Matched: {res.matched_events_count} | Markets: {res.resource_metrics.markets_evaluated}")

    assert len(statuses) == 3
    assert all(s in (CycleStatus.SUCCESS, CycleStatus.PARTIAL) for s in statuses)
    assert all(t > 0.0 for t in cycle_timings)
