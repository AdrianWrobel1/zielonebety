"""
End-to-end integration tests for Valuebet Pipeline in ProductionScanOrchestrator (Stage 9.2).
"""

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional
import pytest
from unittest.mock import MagicMock

from database.connection import DatabaseManager
from normalization.base_normalizer import NormalizedGraph
from orchestration.models import CycleStatus, ScanConfig
from orchestration.scan_orchestrator import ProductionScanOrchestrator
from providers.base.base_provider import BaseProvider
from providers.base.provider_result import ProviderResult
from providers.base.provider_state import ProviderState
from reference_odds.models import ReferenceEvent
from reference_odds.provider import ReferenceOddsProvider
from tests.fixtures.reference_odds_fixtures import (
    create_bookmaker_graph,
    create_reference_event,
    fixture_valid_1x2_reference_market,
)
from api.services import (
    _serialize_scan_cycle_result,
    serialize_opportunity_summary,
    serialize_opportunity_detail,
)


from providers.base.models import ProviderMetadata, ValidationReport
from providers.base.provider_context import ProviderContext


class MockProvider(BaseProvider):
    def __init__(self, name: str, graphs: List[NormalizedGraph]):
        ctx = ProviderContext(provider_name=name)
        meta = ProviderMetadata(name=name, code=name)
        super().__init__(context=ctx, metadata=meta)
        self._name = name
        self._graphs = graphs

    @property
    def name(self) -> str:
        return self._name

    def discover(self) -> List[Any]:
        return [{"id": f"disc_{i}"} for i in range(len(self._graphs))]

    def fetch(self, discovery_items: List[Any]) -> List[Any]:
        return [{"raw": "data"} for _ in discovery_items]

    def parse(self, raw_data: List[Any]) -> List[Any]:
        return list(self._graphs)

    def validate(self, parsed_data: List[Any]) -> ValidationReport:
        return ValidationReport(valid_objects=len(parsed_data), invalid_objects=0, is_valid=True)


from reference_odds.provider import MockReferenceOddsProvider


class MockRefProvider(MockReferenceOddsProvider):
    def __init__(self, events: List[ReferenceEvent], fail: bool = False):
        super().__init__(events=events)
        self._fail = fail

    def fetch_reference_events(self, sport: str = "football", leagues: Optional[Any] = None, force_refresh: bool = False) -> List[ReferenceEvent]:
        if self._fail:
            raise ConnectionError("External reference API timeout")
        return self.events


from database.config import DatabaseConfig


class MockNormalizerWrapper:
    def __init__(self, graphs: List[NormalizedGraph]):
        self.graphs = graphs

    def normalize_event(self, obj: Any) -> NormalizedGraph:
        if isinstance(obj, NormalizedGraph):
            return obj
        if isinstance(obj, dict) and "graph" in obj:
            return obj["graph"]
        return self.graphs[0] if self.graphs else obj

    def normalize_events(self, objs: List[Any]) -> List[NormalizedGraph]:
        return list(self.graphs)


class TestValuebetOrchestrator:
    def test_orchestrator_detects_and_persists_valuebets(self):
        db = DatabaseManager(DatabaseConfig(db_url="sqlite:///:memory:"))
        db.create_tables()

        # Superbet offers 2.30 on Arsenal Home (Reference fair odds is ~2.07 -> +11% EV)
        sb_graph = create_bookmaker_graph(
            home_team="Arsenal",
            away_team="Chelsea",
            bookmaker="superbet",
            market_type="1X2",
            selections_odds={"HOME": 2.30, "DRAW": 3.20, "AWAY": 3.10},
        )
        bc_graph = create_bookmaker_graph(
            home_team="Arsenal",
            away_team="Chelsea",
            bookmaker="betclic",
            market_type="1X2",
            selections_odds={"HOME": 1.95, "DRAW": 3.30, "AWAY": 3.60},
        )

        providers = {
            "superbet": MockProvider("superbet", [sb_graph]),
            "betclic": MockProvider("betclic", [bc_graph]),
        }

        ref_mkt = fixture_valid_1x2_reference_market()
        ref_ev = create_reference_event(home_team="Arsenal", away_team="Chelsea", markets=[ref_mkt])
        ref_prov = MockRefProvider([ref_ev])

        orchestrator = ProductionScanOrchestrator(
            db_manager=db,
            reference_provider=ref_prov,
            config=ScanConfig(enable_valuebets=True, min_value_percent=Decimal("2.0")),
        )
        orchestrator.normalization_engine.register_normalizer("superbet", MockNormalizerWrapper([sb_graph]))
        orchestrator.normalization_engine.register_normalizer("betclic", MockNormalizerWrapper([bc_graph]))

        result = orchestrator.run_scan_cycle(providers=providers)

        assert result.cycle_status in (CycleStatus.SUCCESS, CycleStatus.PARTIAL)
        assert result.valuebet_result is not None
        assert len(result.valuebet_result.candidates) >= 1
        assert result.valuebet_candidates_count >= 1
        assert result.valuebets_new_count >= 1
        assert result.stage_timings.valuebet_seconds > 0.0

        # Verify DB persistence
        with db.get_session() as session:
            from database.models import OpportunityRecordORM
            val_recs = session.query(OpportunityRecordORM).filter_by(opportunity_type="VALUEBET").all()
            assert len(val_recs) >= 1
            assert val_recs[0].status == "NEW"

        # Verify API serialization
        serialized = _serialize_scan_cycle_result(result)
        assert serialized["counts"]["valuebet_candidates"] >= 1
        assert serialized["stage_timings"]["valuebet_seconds"] >= 0.0

        val_opps = [o for o in serialized["opportunities"] if o.get("opportunity_type") == "VALUEBET"]
        assert len(val_opps) >= 1
        assert val_opps[0]["value_percent"] >= 2.0
        assert val_opps[0]["fair_odds"] > 0

    def test_failure_isolation_on_reference_provider_error(self):
        db = DatabaseManager(DatabaseConfig(db_url="sqlite:///:memory:"))
        db.create_tables()

        sb_graph = create_bookmaker_graph(
            home_team="Arsenal",
            away_team="Chelsea",
            bookmaker="superbet",
            market_type="1X2",
            selections_odds={"HOME": 2.10, "DRAW": 3.20, "AWAY": 3.10},
        )
        providers = {"superbet": MockProvider("superbet", [sb_graph])}

        # Reference provider that throws an error
        failing_ref_prov = MockRefProvider([], fail=True)

        orchestrator = ProductionScanOrchestrator(
            db_manager=db,
            reference_provider=failing_ref_prov,
            config=ScanConfig(enable_valuebets=True),
        )
        orchestrator.normalization_engine.register_normalizer("superbet", MockNormalizerWrapper([sb_graph]))

        result = orchestrator.run_scan_cycle(providers=providers)

        # Scan should complete without unhandled crash
        assert result.cycle_status != CycleStatus.FAILED
        assert any("valuebet subsystem" in w.lower() for w in result.warnings)
        assert "valuebet_error" in result.diagnostics
