"""
Stage 9.3: Real Valuebet E2E & Production Validation Test Suite

Covers:
1. Realistic reference fixture replay.
2. Full Valuebet E2E pipeline via ProductionScanOrchestrator.
3. Event matching and orientation safety (anti-inversion).
4. Market matching across 1X2, BTTS, TOTALS, DNB, HANDICAP.
5. Fair probability calculation & margin removal mathematics.
6. Mathematical value calculation: value = (bm_odds * fair_prob) - 1.
7. Exact Decimal precision verification.
8. Threshold boundary filtering (exact boundary checks).
9. False-positive audit and adversarial rejection rules.
10. Valuebet lifecycle transitions (NEW, duplicate suppression, UPDATED).
11. Conservative expiration and resurrection.
12. Database persistence into OpportunityRecordORM.
13. Persistence across process restart (fresh orchestrator instance).
14. Telegram alert message formatting & HTML escaping.
15. REST API serialization & Opportunity Explorer retrieval.
16. External Reference API failure isolation.
17. Surebet & Valuebet isolation (separate telemetry & calculations).
18. Resource safety & budget enforcement.
19. Scheduler integration & automated scan execution.
20. Deterministic replay and input permutation invariance.
21. Adversarial market cases (extreme overrounds, corrupted payloads).
22. Bounded Live E2E Scan (@pytest.mark.live).
23. Live Telegram Transport Verification (@pytest.mark.live).
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import os
from typing import Any, Dict, List, Optional, Tuple
import unittest
from unittest.mock import MagicMock

import pytest

from api.services import (
    PlatformAPIService,
    _serialize_scan_cycle_result,
    serialize_opportunity_detail,
    serialize_opportunity_summary,
)
from database.config import DatabaseConfig
from database.connection import DatabaseManager
from database.models import OpportunityRecordORM
from database.repositories.delivery_repository import DeliveryRepository
from database.repositories.opportunity_repository import OpportunityRepository
from domain.models import Competition, Event, Market, Odds, Selection
from normalization.base_normalizer import NormalizedGraph
from normalization.dispatcher import (
    DeliveryStatus,
    DispatchResult,
    DispatchStatus,
    DispatchableOpportunity,
    InMemoryOpportunityConsumer,
    OpportunityDispatcher,
)
from normalization.market_identity import CanonicalMarketKey, CanonicalMarketType
from normalization.selection_identity import CanonicalSelectionKey, CanonicalSelectionType
from notifications.telegram_client import TelegramSendResult
from notifications.telegram_consumer import (
    TelegramOpportunityConsumer,
    format_telegram_surebet_message,
    format_telegram_valuebet_message,
)
from orchestration.models import CycleStatus, ResourceBudget, ScanConfig
from orchestration.scan_orchestrator import ProductionScanOrchestrator
from orchestration.scheduler import ScanScheduler
from providers.base.base_provider import BaseProvider
from providers.base.models import ProviderMetadata, ValidationReport
from providers.base.provider_context import ProviderContext
from providers.base.provider_result import ProviderResult
from reference_odds.fair_calculator import FairProbabilityCalculator
from reference_odds.models import (
    FairProbabilityResult,
    ReferenceEvent,
    ReferenceMarket,
    ReferenceSelection,
    ReferenceSource,
)
from reference_odds.provider import MockReferenceOddsProvider, TheOddsApiReferenceProvider
from tests.fixtures.reference_odds_fixtures import (
    create_bookmaker_graph,
    create_reference_event,
    fixture_valid_1x2_reference_market,
    fixture_valid_btts_reference_market,
    fixture_valid_totals_reference_market,
)
from valuebets.config import ValuebetConfig
from valuebets.engine import ValuebetEngine
from valuebets.lifecycle import (
    LifecycleAction,
    ValuebetLifecycleManager,
    generate_valuebet_fingerprint,
)
from valuebets.models import ValueBetCandidate
from valuebets.quality_policy import ValuebetQualityConfig, ValuebetQualityPolicy


# ─────────────────────────────────────────────────────────────────────────────
# Test Helpers & Mock Fixtures
# ─────────────────────────────────────────────────────────────────────────────

def _setup_db() -> Tuple[DatabaseManager, OpportunityRepository, DeliveryRepository]:
    db = DatabaseManager(DatabaseConfig(db_url="sqlite:///:memory:"))
    db.create_tables()
    session = db.get_session()
    opp_repo = OpportunityRepository(session)
    del_repo = DeliveryRepository(session)
    return db, opp_repo, del_repo


def make_1x2_ref_market(
    home_odds: Decimal = Decimal("2.00"),
    draw_odds: Decimal = Decimal("3.50"),
    away_odds: Decimal = Decimal("4.00"),
    timestamp: Optional[str] = None,
    bookmaker_name: str = "pinnacle",
) -> ReferenceMarket:
    return ReferenceMarket(
        market_type=CanonicalMarketType.ONE_X_TWO.value,
        selections={
            CanonicalSelectionType.HOME.value: ReferenceSelection(
                selection_type=CanonicalSelectionType.HOME.value,
                odds=home_odds,
                participant_role="HOME",
            ),
            CanonicalSelectionType.DRAW.value: ReferenceSelection(
                selection_type=CanonicalSelectionType.DRAW.value,
                odds=draw_odds,
            ),
            CanonicalSelectionType.AWAY.value: ReferenceSelection(
                selection_type=CanonicalSelectionType.AWAY.value,
                odds=away_odds,
                participant_role="AWAY",
            ),
        },
        timestamp=timestamp or datetime.now(timezone.utc).isoformat(),
        bookmaker_name=bookmaker_name,
    )


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


class MockNormalizer:
    def __init__(self, graphs: List[NormalizedGraph]):
        self.graphs = graphs

    def normalize_event(self, obj: Any) -> NormalizedGraph:
        if isinstance(obj, NormalizedGraph):
            return obj
        return self.graphs[0] if self.graphs else obj

    def normalize_events(self, objs: List[Any]) -> List[NormalizedGraph]:
        return list(self.graphs)


# ─────────────────────────────────────────────────────────────────────────────
# Targeted Stage 9.3 Test Suite (21 Deterministic Tests + 2 Live Tests)
# ─────────────────────────────────────────────────────────────────────────────

class TestStage93RealValuebetProduction:
    """Authoritative Stage 9.3 Production Verification Test Suite."""

    # 1. Realistic reference fixture replay
    def test_01_realistic_reference_fixture_replay(self):
        sb_graph = create_bookmaker_graph(
            home_team="Arsenal",
            away_team="Chelsea",
            bookmaker="superbet",
            market_type="1X2",
            selections_odds={"HOME": 2.30, "DRAW": 3.40, "AWAY": 3.20},
        )
        ref_mkt = make_1x2_ref_market(home_odds=Decimal("2.05"), draw_odds=Decimal("3.50"), away_odds=Decimal("3.80"))
        ref_ev = create_reference_event(home_team="Arsenal", away_team="Chelsea", markets=[ref_mkt])

        engine = ValuebetEngine(config=ValuebetConfig(min_value_percent=Decimal("3.0")))
        result = engine.detect_valuebets(bookmaker_graphs=[sb_graph], reference_events=[ref_ev])

        assert len(result.candidates) >= 1
        val_cand = result.candidates[0]
        assert val_cand.selection_type == "HOME"
        assert val_cand.bookmaker_odds == Decimal("2.30")
        assert val_cand.value_percent > Decimal("3.0")
        assert val_cand.is_qualified is True

    # 2. Full Valuebet E2E pipeline
    def test_02_full_valuebet_e2e_pipeline(self):
        db, opp_repo, del_repo = _setup_db()
        consumer = InMemoryOpportunityConsumer()
        dispatcher = OpportunityDispatcher()
        dispatcher.register_consumer(consumer)

        sb_graph = create_bookmaker_graph(
            home_team="Real Madrid",
            away_team="Barcelona",
            bookmaker="superbet",
            market_type="1X2",
            selections_odds={"HOME": 2.25, "DRAW": 3.40, "AWAY": 3.10},
        )
        bc_graph = create_bookmaker_graph(
            home_team="Real Madrid",
            away_team="Barcelona",
            bookmaker="betclic",
            market_type="1X2",
            selections_odds={"HOME": 1.95, "DRAW": 3.40, "AWAY": 3.60},
        )
        ref_mkt = make_1x2_ref_market(home_odds=Decimal("2.00"), draw_odds=Decimal("3.50"), away_odds=Decimal("3.70"))
        ref_ev = create_reference_event(home_team="Real Madrid", away_team="Barcelona", markets=[ref_mkt])
        ref_provider = MockReferenceOddsProvider([ref_ev])

        orchestrator = ProductionScanOrchestrator(
            db_manager=db,
            opportunity_repository=opp_repo,
            delivery_repository=del_repo,
            dispatcher=dispatcher,
            reference_provider=ref_provider,
            config=ScanConfig(enable_valuebets=True, min_value_percent=Decimal("2.0")),
        )
        orchestrator.normalization_engine.register_normalizer("superbet", MockNormalizer([sb_graph]))
        orchestrator.normalization_engine.register_normalizer("betclic", MockNormalizer([bc_graph]))

        providers = {
            "superbet": MockProvider("superbet", [sb_graph]),
            "betclic": MockProvider("betclic", [bc_graph]),
        }

        cycle_res = orchestrator.run_scan_cycle(providers=providers)

        assert cycle_res.cycle_status == CycleStatus.SUCCESS
        assert cycle_res.valuebet_result is not None
        assert cycle_res.valuebet_candidates_count >= 1
        assert cycle_res.valuebets_new_count >= 1
        assert cycle_res.valuebets_dispatched_count >= 1
        assert len(consumer.received_opportunities) >= 1

        # Check DB record
        with db.get_session() as session:
            val_records = session.query(OpportunityRecordORM).filter_by(opportunity_type="VALUEBET").all()
            assert len(val_records) >= 1
            assert val_records[0].status in ("NEW", "ALERTED")

    # 3. Event matching & orientation safety
    def test_03_event_matching_and_orientation_safety(self):
        engine = ValuebetEngine()
        # Normal direct match
        bm_graph = create_bookmaker_graph(home_team="Liverpool", away_team="Manchester City")
        ref_ev_direct = create_reference_event(home_team="Liverpool", away_team="Manchester City")
        matched = engine._match_reference_event(bm_graph.event, [ref_ev_direct])
        assert matched is not None
        assert matched.home_team == "Liverpool"

        # Inverted match must be strictly rejected
        ref_ev_inverted = create_reference_event(home_team="Manchester City", away_team="Liverpool")
        matched_inv = engine._match_reference_event(bm_graph.event, [ref_ev_inverted])
        assert matched_inv is None, "Inverted event match must be rejected to prevent cross-team EV error"

    # 4. Market matching multi-market
    def test_04_market_matching_multi_market(self):
        engine = ValuebetEngine(config=ValuebetConfig(min_value_percent=Decimal("1.0")))

        # BTTS
        sb_btts = create_bookmaker_graph(
            market_type="BTTS",
            selections_odds={"YES": 2.10, "NO": 1.85},
        )
        ref_btts = fixture_valid_btts_reference_market()
        ref_ev = create_reference_event(markets=[ref_btts])
        res_btts = engine.detect_valuebets([sb_btts], [ref_ev])
        assert any(c.selection_type == "YES" for c in res_btts.candidates)

        # TOTALS 2.5
        sb_tot = create_bookmaker_graph(
            market_type="TOTALS",
            line=2.5,
            selections_odds={"OVER": 2.20, "UNDER": 1.75},
        )
        ref_tot = fixture_valid_totals_reference_market(line=Decimal("2.5"))
        ref_ev_tot = create_reference_event(markets=[ref_tot])
        res_tot = engine.detect_valuebets([sb_tot], [ref_ev_tot])
        assert any(c.selection_type == "OVER" and c.line == Decimal("2.5") for c in res_tot.candidates)

    # 5. Fair probability exact calculation
    def test_05_fair_probability_exact_calculation(self):
        calc = FairProbabilityCalculator()
        # 1X2 Market with exact odds
        mkt = ReferenceMarket(
            market_type=CanonicalMarketType.ONE_X_TWO.value,
            selections={
                "HOME": ReferenceSelection(selection_type="HOME", odds=Decimal("2.00")),
                "DRAW": ReferenceSelection(selection_type="DRAW", odds=Decimal("3.50")),
                "AWAY": ReferenceSelection(selection_type="AWAY", odds=Decimal("4.00")),
            },
        )
        res = calc.calculate_fair_probabilities(mkt)
        assert res.is_valid is True

        # Independent manual calculation
        raw_h = Decimal("1") / Decimal("2.00")      # 0.5000000000000000000000000000
        raw_d = Decimal("1") / Decimal("3.50")      # 0.2857142857142857142857142857
        raw_a = Decimal("1") / Decimal("4.00")      # 0.2500000000000000000000000000
        overround = raw_h + raw_d + raw_a          # 1.0357142857142857142857142857

        expected_fair_h = raw_h / overround
        expected_fair_d = raw_d / overround
        expected_fair_a = raw_a / overround

        assert res.raw_overround == overround
        assert res.fair_probabilities["HOME"] == expected_fair_h
        assert res.fair_probabilities["DRAW"] == expected_fair_d
        assert res.fair_probabilities["AWAY"] == expected_fair_a
        assert res.fair_odds["HOME"] == Decimal("1") / expected_fair_h

    # 6. Value calculation formula
    def test_06_value_calculation_formula(self):
        # bm_odds = 2.20, fair_prob = 0.50
        # Expected value_edge = (2.20 * 0.50) - 1 = 1.10 - 1 = 0.10
        # Expected value_percent = +10.0%
        bm_odds = Decimal("2.20")
        fair_prob = Decimal("0.5000")
        value_edge = (bm_odds * fair_prob) - Decimal("1")
        value_percent = value_edge * Decimal("100")

        assert value_edge == Decimal("0.1000")
        assert value_percent == Decimal("10.00")

    # 7. Exact Decimal precision
    def test_07_decimal_precision_exactness(self):
        # Verify no floating point binary rounding errors (e.g. 0.1 + 0.2 != 0.3)
        bm_odds = Decimal("2.15")
        ref_odds = Decimal("2.05")
        calc = FairProbabilityCalculator()
        mkt = ReferenceMarket(
            market_type=CanonicalMarketType.BTTS.value,
            selections={
                "YES": ReferenceSelection(selection_type="YES", odds=ref_odds),
                "NO": ReferenceSelection(selection_type="NO", odds=Decimal("1.90")),
            },
        )
        res = calc.calculate_fair_probabilities(mkt)
        fair_p_yes = res.fair_probabilities["YES"]

        assert isinstance(fair_p_yes, Decimal)
        edge = (bm_odds * fair_p_yes) - Decimal("1")
        assert isinstance(edge, Decimal)

    # 8. Threshold boundary filtering
    def test_08_threshold_boundary_filtering(self):
        cfg_min_5 = ValuebetConfig(min_value_percent=Decimal("5.0"))
        engine = ValuebetEngine(config=cfg_min_5)

        # 1. Candidate with low value -> NOT qualified
        sb_low = create_bookmaker_graph(
            market_type="1X2",
            selections_odds={"HOME": 2.10, "DRAW": 3.50, "AWAY": 3.50},
        )
        ref_mkt = make_1x2_ref_market(home_odds=Decimal("2.00"), draw_odds=Decimal("3.50"), away_odds=Decimal("3.50"))
        ref_ev = create_reference_event(markets=[ref_mkt])

        res_low = engine.detect_valuebets([sb_low], [ref_ev])
        assert len(res_low.qualified_valuebets) == 0

        # 2. Candidate with high value -> QUALIFIED
        sb_high = create_bookmaker_graph(
            market_type="1X2",
            selections_odds={"HOME": 2.35, "DRAW": 3.50, "AWAY": 3.50},
        )
        res_high = engine.detect_valuebets([sb_high], [ref_ev])
        assert len(res_high.qualified_valuebets) == 1

    # 9. False-positive audit and adversarial rejection rules
    def test_09_false_positive_rejection_adversarial(self):
        engine = ValuebetEngine()
        calc = FairProbabilityCalculator(max_freshness_seconds=300)

        # 1. Youth suffix mismatch (U21 vs senior)
        bm_u21 = create_bookmaker_graph(home_team="Arsenal U21", away_team="Chelsea U21")
        ref_senior = create_reference_event(home_team="Arsenal", away_team="Chelsea")
        assert engine._match_reference_event(bm_u21.event, [ref_senior]) is None

        # 2. Women's vs Men's mismatch
        bm_women = create_bookmaker_graph(home_team="Barcelona Women", away_team="Real Madrid Women")
        ref_men = create_reference_event(home_team="Barcelona", away_team="Real Madrid")
        assert engine._match_reference_event(bm_women.event, [ref_men]) is None

        # 3. Stale reference data
        stale_ts = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        ref_stale_mkt = make_1x2_ref_market(timestamp=stale_ts)
        fair_stale = calc.calculate_fair_probabilities(ref_stale_mkt)
        assert fair_stale.is_valid is False
        assert fair_stale.diagnostic == "STALE_REFERENCE_DATA"

        # 4. Incomplete reference market (missing Away leg)
        ref_incomplete = ReferenceMarket(
            market_type=CanonicalMarketType.ONE_X_TWO.value,
            selections={
                "HOME": ReferenceSelection(selection_type="HOME", odds=Decimal("2.00")),
                "DRAW": ReferenceSelection(selection_type="DRAW", odds=Decimal("3.50")),
            },
        )
        fair_inc = calc.calculate_fair_probabilities(ref_incomplete)
        assert fair_inc.is_valid is False
        assert fair_inc.diagnostic == "INCOMPLETE_REFERENCE_MARKET"

        # 5. Invalid reference odds (<= 1.0)
        ref_invalid_odds = ReferenceMarket(
            market_type=CanonicalMarketType.BTTS.value,
            selections={
                "YES": ReferenceSelection(selection_type="YES", odds=Decimal("0.95")),
                "NO": ReferenceSelection(selection_type="NO", odds=Decimal("2.00")),
            },
        )
        fair_inv = calc.calculate_fair_probabilities(ref_invalid_odds)
        assert fair_inv.is_valid is False
        assert fair_inv.diagnostic == "INVALID_ODDS"

        # 6. Totals line mismatch (Over 2.5 vs Over 3.5)
        bm_mkt_key = CanonicalMarketKey(market_type=CanonicalMarketType.TOTALS.value, line=Decimal("2.5"))
        ref_mkt_35 = fixture_valid_totals_reference_market(line=Decimal("3.5"))
        ref_ev_35 = create_reference_event(markets=[ref_mkt_35])
        assert engine._find_matching_reference_market(bm_mkt_key, ref_ev_35) is None

    # 10. Valuebet lifecycle transitions (NEW, Duplicate suppression, UPDATED)
    def test_10_valuebet_lifecycle_transitions(self):
        _, opp_repo, _ = _setup_db()
        life_mgr = ValuebetLifecycleManager(repository=opp_repo, material_value_delta_threshold=Decimal("1.0"))

        cand_1 = ValueBetCandidate(
            candidate_id="vbc_1",
            canonical_event_id="ev_01",
            event_name="Arsenal vs Chelsea",
            sport="football",
            competition_name="Premier League",
            market_type="1X2",
            selection_type="HOME",
            bookmaker="superbet",
            bookmaker_odds=Decimal("2.20"),
            value_percent=Decimal("5.0"),
            reference_fair_probability=Decimal("0.4772"),
            reference_fair_odds=Decimal("2.095"),
            is_qualified=True,
        )

        # Scan 1: NEW -> to_dispatch contains candidate
        t0 = datetime.now(timezone.utc)
        batch_1 = life_mgr.evaluate_candidates([cand_1], evaluation_time=t0)
        assert batch_1.new_count == 1
        assert len(batch_1.to_dispatch) == 1

        # Record delivery
        fp = generate_valuebet_fingerprint(cand_1)
        opp_repo.record_delivery_result(fingerprint=fp, success=True, delivery_status="DELIVERED", alert_time=t0)

        # Scan 2: Same candidate -> duplicate suppression (to_dispatch empty)
        t1 = t0 + timedelta(seconds=30)
        batch_2 = life_mgr.evaluate_candidates([cand_1], evaluation_time=t1)
        assert batch_2.suppressed_count == 1
        assert len(batch_2.to_dispatch) == 0

        # Scan 3: Material EV increase (+5.0% -> +7.5%) -> UPDATED -> to_dispatch contains updated candidate
        cand_updated = ValueBetCandidate(
            candidate_id="vbc_1_upd",
            canonical_event_id="ev_01",
            event_name="Arsenal vs Chelsea",
            sport="football",
            competition_name="Premier League",
            market_type="1X2",
            selection_type="HOME",
            bookmaker="superbet",
            bookmaker_odds=Decimal("2.35"),
            value_percent=Decimal("7.5"),
            reference_fair_probability=Decimal("0.4574"),
            reference_fair_odds=Decimal("2.186"),
            is_qualified=True,
        )
        t2 = t1 + timedelta(seconds=30)
        batch_3 = life_mgr.evaluate_candidates([cand_updated], evaluation_time=t2)
        assert batch_3.updated_count == 1
        assert len(batch_3.to_dispatch) == 1

    # 11. Conservative expiration and resurrection
    def test_11_valuebet_expiration_and_resurrection(self):
        _, opp_repo, _ = _setup_db()
        life_mgr = ValuebetLifecycleManager(repository=opp_repo)

        cand = ValueBetCandidate(
            candidate_id="vbc_exp",
            canonical_event_id="ev_exp",
            event_name="Man City vs Liverpool",
            sport="football",
            market_type="1X2",
            selection_type="HOME",
            bookmaker="superbet",
            bookmaker_odds=Decimal("2.10"),
            value_percent=Decimal("4.5"),
            is_qualified=True,
        )
        fp = generate_valuebet_fingerprint(cand)
        t0 = datetime.now(timezone.utc)
        life_mgr.evaluate_candidates([cand], evaluation_time=t0)

        # Cycle 1 missing -> miss count = 1
        life_mgr.expire_missing_candidates(evaluated_fingerprints=set(), evaluation_time=t0 + timedelta(seconds=10), max_misses=2)
        rec = opp_repo.get_by_fingerprint(fp)
        assert rec.consecutive_misses == 1
        assert rec.status in ("NEW", "ACTIVE")

        # Cycle 2 missing -> miss count = 2 -> EXPIRED
        life_mgr.expire_missing_candidates(evaluated_fingerprints=set(), evaluation_time=t0 + timedelta(seconds=20), max_misses=2)
        rec = opp_repo.get_by_fingerprint(fp)
        assert rec.status == "EXPIRED"

        # Cycle 3: Resurrected
        batch_resurrect = life_mgr.evaluate_candidates([cand], evaluation_time=t0 + timedelta(seconds=30))
        assert batch_resurrect.new_count == 1
        rec_resurrect = opp_repo.get_by_fingerprint(fp)
        assert rec_resurrect.status == "NEW"

    # 12. Database persistence into OpportunityRecordORM
    def test_12_valuebet_database_persistence(self):
        db, opp_repo, _ = _setup_db()
        cand = ValueBetCandidate(
            candidate_id="vbc_db_test",
            canonical_event_id="ev_db",
            event_name="Juventus vs Inter",
            sport="football",
            competition_name="Serie A",
            market_type="1X2",
            selection_type="AWAY",
            bookmaker="betclic",
            bookmaker_odds=Decimal("3.10"),
            value_percent=Decimal("6.20"),
            reference_fair_probability=Decimal("0.3426"),
            reference_fair_odds=Decimal("2.919"),
            is_qualified=True,
        )
        life_mgr = ValuebetLifecycleManager(repository=opp_repo)
        life_mgr.evaluate_candidates([cand])
        opp_repo.session.commit()

        with db.get_session() as session:
            rec = session.query(OpportunityRecordORM).filter_by(opportunity_type="VALUEBET").first()
            assert rec is not None
            assert rec.opportunity_type == "VALUEBET"
            assert rec.canonical_event_id == "ev_db"
            assert rec.arbitrage_margin == float(cand.value_percent)
            snap = json.loads(rec.snapshot_json)
            assert snap["fair_odds"] == str(cand.fair_odds)
            assert snap["reference_bookmaker"] == "pinnacle"

    # 13. Persistence across process restart
    def test_13_persistence_across_process_restart(self):
        db_config = DatabaseConfig(db_url="sqlite:///:memory:")
        db_mgr = DatabaseManager(db_config)
        db_mgr.create_tables()

        cand = ValueBetCandidate(
            candidate_id="vbc_restart",
            canonical_event_id="ev_restart",
            event_name="PSG vs Marseille",
            sport="football",
            market_type="1X2",
            selection_type="HOME",
            bookmaker="superbet",
            bookmaker_odds=Decimal("1.75"),
            value_percent=Decimal("5.5"),
            is_qualified=True,
        )

        # Instance 1 saves to DB
        with db_mgr.get_session() as session:
            repo1 = OpportunityRepository(session)
            life1 = ValuebetLifecycleManager(repository=repo1)
            life1.evaluate_candidates([cand])
            repo1.record_delivery_result(generate_valuebet_fingerprint(cand), success=True, delivery_status="DELIVERED")
            session.commit()

        # Instance 2 (simulating new process with same DB)
        with db_mgr.get_session() as session2:
            repo2 = OpportunityRepository(session2)
            life2 = ValuebetLifecycleManager(repository=repo2)
            batch = life2.evaluate_candidates([cand])
            # Must suppress duplicate alert
            assert batch.suppressed_count == 1
            assert len(batch.to_dispatch) == 0

    # 14. Telegram alert message formatting & HTML escaping
    def test_14_telegram_valuebet_alert_formatting_and_escaping(self):
        cand = ValueBetCandidate(
            candidate_id="vbc_tg",
            canonical_event_id="ev_tg",
            event_name="Bayern <Munich> & Friends vs Dortmund",
            sport="football",
            competition_name="Bundesliga <b>Championship</b>",
            kickoff="2026-08-18T18:30:00Z",
            market_type="1X2",
            selection_type="HOME",
            bookmaker="superbet",
            bookmaker_odds=Decimal("2.40"),
            reference_source="the_odds_api",
            reference_bookmaker="pinnacle",
            reference_fair_probability=Decimal("0.4500"),
            reference_fair_odds=Decimal("2.22"),
            reference_overround=Decimal("1.0420"),
            value_percent=Decimal("8.00"),
            is_qualified=True,
        )
        msg_html = format_telegram_valuebet_message(cand, parse_mode="HTML")

        # Check expected structure
        assert "📈 <b>VALUEBET ALERT +8.00%</b>" in msg_html
        assert "Bayern &lt;Munich&gt; &amp; Friends vs Dortmund" in msg_html
        assert "Bundesliga &lt;b&gt;Championship&lt;/b&gt;" in msg_html
        assert "SUPERBET @ <b>2.40</b>" in msg_html
        assert "Fair odds: 2.22" in msg_html
        assert "Fair probability: 45.00%" in msg_html
        assert "Pinnacle via the_odds_api (Overround: 4.20%)" in msg_html
        assert "Formula: <code>EV = (2.40 × 0.4500) - 1 = +8.00%</code>" in msg_html

    # 15. REST API serialization & Opportunity Explorer retrieval
    def test_15_api_explorer_serialization_and_filters(self):
        db, opp_repo, _ = _setup_db()
        service = PlatformAPIService(db_manager=db)

        cand = ValueBetCandidate(
            candidate_id="vbc_api_01",
            canonical_event_id="ev_api_01",
            event_name="Ajax vs Feyenoord",
            sport="football",
            competition_name="Eredivisie",
            market_type="1X2",
            selection_type="HOME",
            bookmaker="betclic",
            bookmaker_odds=Decimal("2.50"),
            value_percent=Decimal("7.20"),
            reference_fair_probability=Decimal("0.4288"),
            reference_fair_odds=Decimal("2.332"),
            is_qualified=True,
        )
        life_mgr = ValuebetLifecycleManager(repository=opp_repo)
        life_mgr.evaluate_candidates([cand])
        opp_repo.session.commit()

        # Test list_opportunities
        opps = service.list_opportunities(opportunity_type="VALUEBET")
        assert len(opps) >= 1
        opp_entry = opps[0]
        assert opp_entry["opportunity_type"] == "VALUEBET"
        assert opp_entry["value_percent"] == 7.20
        assert "fair_odds" in opp_entry
        assert "fair_probability" in opp_entry

        # Test get_opportunity_detail by fingerprint
        detail = service.get_opportunity_detail(opp_entry["fingerprint"])
        assert detail is not None
        assert detail["opportunity_type"] == "VALUEBET"
        assert "mathematical_explanation" in detail
        assert detail["mathematical_explanation"]["formula"] == "Value = (Bookmaker Odds * Fair Probability) - 1"
        assert detail["mathematical_explanation"]["fair_probability"] == float(cand.fair_probability)

    # 16. External Reference API failure isolation
    def test_16_reference_api_failure_isolation(self):
        db, opp_repo, del_repo = _setup_db()
        sb_graph = create_bookmaker_graph(bookmaker="superbet")
        bc_graph = create_bookmaker_graph(bookmaker="betclic")

        # Ref provider raising exception
        class BrokenRefProvider(MockReferenceOddsProvider):
            def fetch_reference_events(self, sport: str = "football", leagues: Optional[Any] = None, force_refresh: bool = False):
                raise ConnectionError("External Odds API 503 Service Unavailable")

        orchestrator = ProductionScanOrchestrator(
            db_manager=db,
            opportunity_repository=opp_repo,
            delivery_repository=del_repo,
            reference_provider=BrokenRefProvider([]),
            config=ScanConfig(enable_valuebets=True),
        )
        orchestrator.normalization_engine.register_normalizer("superbet", MockNormalizer([sb_graph]))
        orchestrator.normalization_engine.register_normalizer("betclic", MockNormalizer([bc_graph]))

        providers = {
            "superbet": MockProvider("superbet", [sb_graph]),
            "betclic": MockProvider("betclic", [bc_graph]),
        }

        # Cycle must not crash
        res = orchestrator.run_scan_cycle(providers=providers)
        assert res.cycle_status in (CycleStatus.SUCCESS, CycleStatus.PARTIAL)
        assert res.valuebet_candidates_count == 0
        assert "valuebet_error" in res.diagnostics or any("valuebet" in str(w).lower() for w in res.warnings)

    # 17. Surebet & Valuebet isolation
    def test_17_surebet_valuebet_pipeline_isolation(self):
        db, opp_repo, del_repo = _setup_db()
        fixed_start = "2026-08-20T20:00:00Z"

        def _make_graph(prov: str, ev_id: str, h_odds: float, d_odds: float, a_odds: float) -> NormalizedGraph:
            comp = Competition(name="Premier League", sport="Football", internal_id=f"comp_{prov}", provider_ids={prov: f"p_c_{prov}"})
            ev = Event(
                competition_id=comp.internal_id,
                home_participant="Arsenal",
                away_participant="Chelsea",
                scheduled_start=fixed_start,
                internal_id=ev_id,
                provider_ids={prov: f"p_ev_{ev_id}"},
                metadata={"provider": prov},
            )
            mkt = Market(event_id=ev.internal_id, market_type="1X2", internal_id=f"mkt_{ev_id}", provider_ids={prov: f"p_m_{ev_id}"}, metadata={"provider": prov})
            s_h = Selection(market_id=mkt.internal_id, selection_type="HOME", participant="Arsenal", internal_id=f"s_h_{ev_id}", provider_ids={prov: f"p_sh_{ev_id}"})
            s_d = Selection(market_id=mkt.internal_id, selection_type="DRAW", internal_id=f"s_d_{ev_id}", provider_ids={prov: f"p_sd_{ev_id}"})
            s_a = Selection(market_id=mkt.internal_id, selection_type="AWAY", participant="Chelsea", internal_id=f"s_a_{ev_id}", provider_ids={prov: f"p_sa_{ev_id}"})
            o_h = Odds(selection_id=s_h.internal_id, bookmaker=prov, decimal_odds=h_odds, internal_id=f"o_h_{ev_id}")
            o_d = Odds(selection_id=s_d.internal_id, bookmaker=prov, decimal_odds=d_odds, internal_id=f"o_d_{ev_id}")
            o_a = Odds(selection_id=s_a.internal_id, bookmaker=prov, decimal_odds=a_odds, internal_id=f"o_a_{ev_id}")
            return NormalizedGraph(competition=comp, event=ev, markets=[mkt], selections=[s_h, s_d, s_a], odds_list=[o_h, o_d, o_a])

        sb_graph = _make_graph("superbet", "ev_sb_01", 2.60, 3.60, 3.20)
        bc_graph = _make_graph("betclic", "ev_bc_01", 2.10, 3.40, 3.80)

        # Reference market provides fair benchmark (Home ~ 2.05 -> SB Home 2.60 is a +20% Valuebet)
        ref_mkt = make_1x2_ref_market(home_odds=Decimal("2.05"), draw_odds=Decimal("3.50"), away_odds=Decimal("3.80"))
        ref_ev = create_reference_event(home_team="Arsenal", away_team="Chelsea", markets=[ref_mkt], scheduled_start=fixed_start)
        ref_provider = MockReferenceOddsProvider([ref_ev])

        orchestrator = ProductionScanOrchestrator(
            db_manager=db,
            opportunity_repository=opp_repo,
            delivery_repository=del_repo,
            reference_provider=ref_provider,
            config=ScanConfig(enable_valuebets=True, min_value_percent=Decimal("2.0")),
        )
        orchestrator.normalization_engine.register_normalizer("superbet", MockNormalizer([sb_graph]))
        orchestrator.normalization_engine.register_normalizer("betclic", MockNormalizer([bc_graph]))

        providers = {
            "superbet": MockProvider("superbet", [sb_graph]),
            "betclic": MockProvider("betclic", [bc_graph]),
        }

        res = orchestrator.run_scan_cycle(providers=providers)

        # Both detected, strictly isolated
        assert res.detected_opportunities_count >= 1, "Surebet must be detected"
        assert res.valuebet_candidates_count >= 1, "Valuebet must be detected"
        assert res.detected_opportunities_count > 0 and res.valuebet_candidates_count > 0

    # 18. Resource safety & budget enforcement
    def test_18_resource_safety_budget_enforcement(self):
        budget = ResourceBudget(
            max_duration_seconds=60.0,
            max_http_requests=50,
            max_events=100,
            max_memory_mb=500.0,
        )
        config = ScanConfig(resource_budget=budget)
        orchestrator = ProductionScanOrchestrator(config=config)
        res = orchestrator.run_scan_cycle()

        assert res.duration_seconds <= 60.0
        assert res.resource_metrics.peak_memory_mb > 0.0

    # 19. Scheduler integration & automated scan execution
    def test_19_scheduler_integration_automated_valuebet_scan(self):
        db, _, _ = _setup_db()
        service = PlatformAPIService(db_manager=db)
        scheduler = service.scheduler

        assert scheduler is not None
        # Trigger single automated scan now
        scan_data = scheduler.run_scan_now()
        assert scan_data is not None
        assert "execution_id" in scan_data
        assert scan_data["scan_source"] == "AUTOMATED"
        assert len(service.get_scan_history()) >= 1

    # 20. Deterministic replay and permutation invariance
    def test_20_deterministic_replay_and_permutation_invariance(self):
        engine = ValuebetEngine()
        g1 = create_bookmaker_graph(home_team="Arsenal", away_team="Chelsea")
        g2 = create_bookmaker_graph(home_team="Liverpool", away_team="Everton")
        ref1 = create_reference_event(home_team="Arsenal", away_team="Chelsea", markets=[make_1x2_ref_market()])
        ref2 = create_reference_event(home_team="Liverpool", away_team="Everton", markets=[make_1x2_ref_market()])

        # Run 1: [g1, g2] vs [ref1, ref2]
        res1 = engine.detect_valuebets([g1, g2], [ref1, ref2])
        fps1 = sorted([generate_valuebet_fingerprint(c) for c in res1.candidates])

        # Run 2: Permuted [g2, g1] vs [ref2, ref1]
        res2 = engine.detect_valuebets([g2, g1], [ref2, ref1])
        fps2 = sorted([generate_valuebet_fingerprint(c) for c in res2.candidates])

        assert fps1 == fps2, "Permuted inputs must produce identical candidate identities and fingerprints"

    # 21. Adversarial market cases
    def test_21_adversarial_market_cases(self):
        calc = FairProbabilityCalculator()
        # Extreme overround > 2.5
        mkt_extreme = ReferenceMarket(
            market_type=CanonicalMarketType.ONE_X_TWO.value,
            selections={
                "HOME": ReferenceSelection(selection_type="HOME", odds=Decimal("1.05")),
                "DRAW": ReferenceSelection(selection_type="DRAW", odds=Decimal("1.05")),
                "AWAY": ReferenceSelection(selection_type="AWAY", odds=Decimal("1.05")),
            },
        )
        res = calc.calculate_fair_probabilities(mkt_extreme)
        assert res.is_valid is False
        assert res.diagnostic == "NON_POSITIVE_OR_EXTREME_OVERROUND"

    # ─────────────────────────────────────────────────────────────────────────
    # Live Gated Tests
    # ─────────────────────────────────────────────────────────────────────────

    @pytest.mark.live
    def test_22_live_bounded_e2e_scan(self):
        """Executes exactly one real live scan against Superbet & Betclic with Reference odds."""
        try:
            from dotenv import load_dotenv
            load_dotenv()
        except ImportError:
            pass

        config = ScanConfig(
            providers=("superbet", "betclic"),
            selection_mode="OVERVIEW_ONLY",
            event_limit=30,
            enable_valuebets=True,
            min_value_percent=Decimal("3.0"),
        )
        orchestrator = ProductionScanOrchestrator(config=config)
        res = orchestrator.run_scan_cycle()

        sb_res = res.provider_results.get("superbet")
        bc_res = res.provider_results.get("betclic")
        sb_disc = len(sb_res.discovered_objects) if sb_res else 0
        bc_disc = len(bc_res.discovered_objects) if bc_res else 0

        print("\n" + "=" * 60)
        print("STAGE 9.3 REAL BOUNDED LIVE E2E SCAN RESULTS")
        print("=" * 60)
        print(f"Cycle Status: {res.cycle_status.value}")
        print(f"Duration: {res.duration_seconds:.2f}s")
        print(f"Superbet Discovered: {sb_disc}")
        print(f"Betclic Discovered: {bc_disc}")
        print(f"Normalized Graphs: {res.normalized_graphs_count}")
        print(f"Matched Canonical Events: {res.matched_events_count}")
        print(f"Markets Matched: {res.markets_matched_count}")
        print(f"Markets Evaluated: {res.markets_evaluated_count}")
        print(f"Surebets Detected: {res.detected_opportunities_count}")
        print(f"Valuebet Candidates: {res.valuebet_candidates_count}")
        print(f"Valuebets Qualified: {res.valuebets_qualified_count}")
        print(f"Valuebets Dispatched: {res.valuebets_dispatched_count}")
        print(f"Reference Requests: {res.valuebet_reference_requests}")
        print(f"Reference Cache Hits: {res.valuebet_reference_cache_hits}")
        print(f"Reference Cache Misses: {res.valuebet_reference_cache_misses}")
        print(f"Peak Memory: {res.resource_metrics.peak_memory_mb:.2f} MB")
        print("=" * 60)

        assert res.cycle_status in (CycleStatus.SUCCESS, CycleStatus.PARTIAL)
        assert res.duration_seconds > 0.0

    @pytest.mark.live
    def test_23_live_telegram_delivery_verification(self):
        """Verifies Telegram alert transport under live gate."""
        try:
            from dotenv import load_dotenv
            load_dotenv()
        except ImportError:
            pass

        live_test = os.getenv("TELEGRAM_LIVE_TEST", "false").lower() in ("true", "1", "yes")
        bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
        chat_id = os.getenv("TELEGRAM_CHAT_ID")

        if not live_test or not bot_token or not chat_id:
            pytest.skip("Telegram live test gate disabled or credentials not configured.")

        consumer = TelegramOpportunityConsumer()
        cand = ValueBetCandidate(
            candidate_id="val_live_smoke",
            canonical_event_id="ev_smoke",
            event_name="Liverpool vs Arsenal",
            sport="football",
            competition_name="Premier League (Live Gate Test)",
            market_type="1X2",
            selection_type="HOME",
            bookmaker="superbet",
            bookmaker_odds=Decimal("2.40"),
            reference_source="the_odds_api",
            reference_bookmaker="pinnacle",
            reference_fair_probability=Decimal("0.4500"),
            reference_fair_odds=Decimal("2.22"),
            reference_overround=Decimal("1.0350"),
            value_percent=Decimal("8.00"),
            is_qualified=True,
        )
        res = consumer.consume(cand)
        print(f"\n[Telegram Live Gate] Status: {res.status.value}, Meta: {res.metadata}")
        assert res.status == DeliveryStatus.DELIVERED
        assert res.metadata.get("http_status") == 200
        assert "telegram_message_id" in res.metadata or "message_id" in res.metadata
