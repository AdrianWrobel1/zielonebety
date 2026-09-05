"""
Stage 8.4 — Production Hardening, Persistence & Deployment Readiness Test Suite

Validates:
1. Health, liveness, and readiness endpoints structure and responses
2. Database configuration from environment and persistent SQLite fallback
3. Opportunity lifecycle persistence and deduplication across simulated process restarts
4. Delivery retry state persistence and reconciliation across simulated process restarts
5. Scan history and latest scan result persistence across simulated process restarts
6. Scheduler configuration persistence across simulated process restarts
7. Scheduler worker idempotency, thread safety, and clean shutdown
8. Error boundary: provider failure isolation (Superbet down, Betclic up -> safe PARTIAL cycle)
9. Error boundary: scheduler survival on scan errors
10. Secret and token redaction in logs and error responses
11. Safe API error handling and envelope consistency
12. Multi-cycle stability (resource and thread stability across consecutive cycles)
"""

import gc
import json
import os
import tempfile
import threading
import time
import tracemalloc
import unittest
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from typing import Dict, List, Optional, Any
from unittest.mock import MagicMock, patch

from core.config import CoreConfig
from core.logging import redact_secrets, StructuredFormatter, get_logger
from database.config import DatabaseConfig
from database.connection import DatabaseManager
from database.models import BaseORM, OpportunityRecordORM, DeliveryRecordORM, ProviderORM, SnapshotORM
from database.repositories.opportunity_repository import OpportunityRepository
from database.repositories.delivery_repository import DeliveryRepository
from domain.models import Competition, Event, Market, Selection, Odds
from normalization.alert_policy import DefaultOpportunityAlertPolicy
from normalization.base_normalizer import NormalizedGraph
from normalization.delivery_reliability import DeliveryReconciliationService
from normalization.dispatcher import (
    OpportunityDispatcher,
    OpportunityConsumer,
    ConsumerDeliveryResult,
    DeliveryStatus,
)
from normalization.engine import NormalizationEngine
from normalization.lifecycle import OpportunityLifecycleManager
from normalization.surebet import (
    CanonicalMarketKey,
    CanonicalSelectionKey,
    SurebetDetectorEngine,
    SurebetLeg,
    SurebetOpportunity,
)
from normalization.validation_pipeline import CrossBookmakerValidationPipeline
from orchestration.models import ScanConfig, ScanCycleResult, CycleStatus
from orchestration.scan_orchestrator import ProductionScanOrchestrator
from orchestration.scheduler import ScanScheduler
from api.routes import APIRouter
from api.services import PlatformAPIService, _sanitize_text, _save_scan_snapshot
from providers.base.base_provider import BaseProvider
from providers.base.models import ProviderMetadata, ValidationReport
from providers.base.provider_context import ProviderContext
from providers.base.provider_result import ProviderResult
from providers.base.provider_state import ProviderState


class MockProvider(BaseProvider):
    """Test helper provider allowing injection of parsed objects or deliberate failures."""

    def __init__(
        self,
        name: str,
        parsed_items: Optional[List[Any]] = None,
        should_fail: bool = False,
        failure_stage: str = "fetch",
    ):
        ctx = ProviderContext(provider_name=name)
        meta = ProviderMetadata(name=name, code=name)
        super().__init__(context=ctx, metadata=meta)
        self._parsed_items = parsed_items if parsed_items is not None else []
        self._should_fail = should_fail
        self._failure_stage = failure_stage

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
        return ValidationReport(valid_objects=len(parsed_data), invalid_objects=0, is_valid=True)


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


def _create_mock_graph(event_id: str, bookmaker: str, odds1: float, oddsX: float, odds2: float) -> NormalizedGraph:
    comp = Competition(
        name="Premier League",
        sport="Football",
        country="England",
        internal_id=f"comp_{bookmaker}",
    )
    ev = Event(
        competition_id=f"comp_{bookmaker}",
        home_participant="Arsenal",
        away_participant="Chelsea",
        scheduled_start="2026-08-17T20:00:00Z",
        internal_id=event_id,
        metadata={"provider": bookmaker},
    )
    m = Market(event_id=event_id, market_type="1X2", internal_id=f"{event_id}_m1", metadata={"provider": bookmaker})
    s1 = Selection(market_id=f"{event_id}_m1", selection_type="HOME", participant="Arsenal", internal_id=f"{event_id}_s1")
    sX = Selection(market_id=f"{event_id}_m1", selection_type="DRAW", participant=None, internal_id=f"{event_id}_sX")
    s2 = Selection(market_id=f"{event_id}_m1", selection_type="AWAY", participant="Chelsea", internal_id=f"{event_id}_s2")
    o1 = Odds(selection_id=f"{event_id}_s1", bookmaker=bookmaker, decimal_odds=odds1, internal_id=f"{event_id}_o1")
    oX = Odds(selection_id=f"{event_id}_sX", bookmaker=bookmaker, decimal_odds=oddsX, internal_id=f"{event_id}_oX")
    o2 = Odds(selection_id=f"{event_id}_s2", bookmaker=bookmaker, decimal_odds=odds2, internal_id=f"{event_id}_o2")
    return NormalizedGraph(
        competition=comp,
        event=ev,
        markets=[m],
        selections=[s1, sX, s2],
        odds_list=[o1, oX, o2],
    )


class TestProductionHardening(unittest.TestCase):
    """Targeted validation tests for Stage 8.4 Production Hardening."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_zielonebety.db")
        self.db_url = f"sqlite:///{self.db_path}"
        self.db_config = DatabaseConfig(db_url=self.db_url)
        self.db_manager = DatabaseManager(self.db_config)
        self.db_manager.create_tables()

    def tearDown(self):
        self.db_manager.dispose()
        gc.collect()
        try:
            self.temp_dir.cleanup()
        except Exception:
            pass

    # ─────────────────────────────────────────────────────────────────────────
    # 1. Health, Liveness & Readiness Endpoints
    # ─────────────────────────────────────────────────────────────────────────

    def test_health_and_probes(self):
        """Verify GET /health, /health/liveness, and /health/readiness."""
        service = PlatformAPIService(db_manager=self.db_manager)
        router = APIRouter(service=service)

        # Health
        h_res = router.handle_get_health()
        self.assertEqual(h_res.status_code, 200)
        data = h_res.data
        self.assertIn(data["status"], ["healthy", "degraded"])
        self.assertEqual(data["database"], "ok")
        self.assertTrue(data["database_connected"])
        self.assertIn(data["scheduler"], ["enabled", "disabled"])
        self.assertIn(data["scanner"], ["ready", "scanning", "error"])

        # Liveness
        l_res = router.handle_get_liveness()
        self.assertEqual(l_res.status_code, 200)
        self.assertEqual(l_res.data["status"], "alive")

        # Readiness
        r_res = router.handle_get_readiness()
        self.assertEqual(r_res.status_code, 200)
        self.assertEqual(r_res.data["status"], "ready")
        self.assertEqual(r_res.data["database"], "ok")

        service.scheduler.stop()

    # ─────────────────────────────────────────────────────────────────────────
    # 2. Database Config From Environment
    # ─────────────────────────────────────────────────────────────────────────

    def test_database_config_from_env(self):
        """Verify DatabaseConfig.from_env() resolves env vars and persistent sqlite fallback."""
        with patch.dict(os.environ, {"DATABASE_URL": "postgresql://u:p@localhost:5432/testdb"}):
            cfg = DatabaseConfig.from_env()
            self.assertEqual(cfg.db_url, "postgresql://u:p@localhost:5432/testdb")

        with patch.dict(os.environ, {
            "DATABASE_URL": "",
            "POSTGRES_HOST": "db.host",
            "POSTGRES_PORT": "5433",
            "POSTGRES_USER": "admin",
            "POSTGRES_PASSWORD": "secretpassword",
            "POSTGRES_DB": "bety",
        }, clear=True):
            cfg2 = DatabaseConfig.from_env()
            self.assertEqual(cfg2.db_url, "postgresql://admin:secretpassword@db.host:5433/bety")

        with patch.dict(os.environ, {}, clear=True):
            cfg3 = DatabaseConfig.from_env()
            self.assertTrue(cfg3.db_url.startswith("sqlite:///"))

    # ─────────────────────────────────────────────────────────────────────────
    # 3. Opportunity Lifecycle Persistence Across Restart
    # ─────────────────────────────────────────────────────────────────────────

    def test_opportunity_lifecycle_persistence_across_restarts(self):
        """Verify opportunities survive restart and are properly deduplicated in Scan B."""
        from normalization.lifecycle import generate_opportunity_fingerprint

        m_key = CanonicalMarketKey(market_type="1X2", period="FULL_TIME", scope="MATCH", line=None)
        sel_key_home = CanonicalSelectionKey(market_key=m_key, selection_type="HOME")
        sel_key_draw = CanonicalSelectionKey(market_key=m_key, selection_type="DRAW")
        sel_key_away = CanonicalSelectionKey(market_key=m_key, selection_type="AWAY")

        m_legs = (
            SurebetLeg(canonical_selection_key=sel_key_home, selection_type="HOME", provider="superbet", odds=Decimal("3.20"), source_selection_id="s1"),
            SurebetLeg(canonical_selection_key=sel_key_draw, selection_type="DRAW", provider="betclic", odds=Decimal("3.60"), source_selection_id="s2"),
            SurebetLeg(canonical_selection_key=sel_key_away, selection_type="AWAY", provider="betclic", odds=Decimal("3.30"), source_selection_id="s3"),
        )
        canonical_fp = generate_opportunity_fingerprint("SUREBET", "ev_arsenal_chelsea", m_key, m_legs)

        # 1. First run on Process A: Seed opportunity in database
        with self.db_manager.get_session() as session:
            repo = OpportunityRepository(session)
            now = datetime.now(timezone.utc)
            opp_rec = OpportunityRecordORM(
                id="opp_restart_test_01",
                fingerprint=canonical_fp,
                opportunity_type="SUREBET",
                canonical_event_id="ev_arsenal_chelsea",
                market_key="1X2:FULL_TIME:MATCH:no_line",
                status="NEW",
                first_seen_at=now,
                last_seen_at=now,
                last_changed_at=now,
                arbitrage_margin=0.035,
                implied_probability_sum=0.965,
                consecutive_misses=0,
                snapshot_json=json.dumps({
                    "opportunity_id": "opp_restart_test_01",
                    "canonical_event_id": "ev_arsenal_chelsea",
                    "bookmakers": ["superbet", "betclic"],
                    "legs": [
                        {"provider": "superbet", "selection_type": "HOME", "odds": "3.20", "source_selection_id": "s1"},
                        {"provider": "betclic", "selection_type": "DRAW", "odds": "3.60", "source_selection_id": "s2"},
                        {"provider": "betclic", "selection_type": "AWAY", "odds": "3.30", "source_selection_id": "s3"},
                    ],
                }),
            )
            repo.save_or_update(opp_rec)
            session.commit()

        # 2. Simulate Process Restart: Create fresh session and OpportunityLifecycleManager
        with self.db_manager.get_session() as session_restart:
            restart_repo = OpportunityRepository(session_restart)
            saved = restart_repo.get_by_fingerprint(canonical_fp)
            self.assertIsNotNone(saved)
            self.assertEqual(saved.id, "opp_restart_test_01")
            self.assertEqual(saved.status, "NEW")

            # Create mock incoming surebet matching identical fingerprint
            detected_opp = SurebetOpportunity(
                opportunity_id="opp_restart_test_01",
                canonical_event_id="ev_arsenal_chelsea",
                canonical_market_key=m_key,
                legs=m_legs,
                implied_probability_sum=Decimal("0.965"),
                arbitrage_margin=Decimal("0.035"),
                is_mixed_bookmakers=True,
                bookmakers=("superbet", "betclic"),
            )

            detection_result = MagicMock()
            detection_result.opportunities = [detected_opp]
            detection_result.scanned_market_keys = {("ev_arsenal_chelsea", m_key.to_key_string())}

            lifecycle_mgr = OpportunityLifecycleManager(
                repository=restart_repo,
                alert_policy=DefaultOpportunityAlertPolicy(),
            )
            dispatcher = OpportunityDispatcher()
            summary = lifecycle_mgr.process_and_dispatch(
                detection_result=detection_result,
                dispatcher=dispatcher,
            )

            # Check that it did NOT create a duplicate, but updated/suppressed existing record
            self.assertEqual(summary.evaluation_batch.new_count, 0)

            all_recs = restart_repo.list_all()
            self.assertEqual(len(all_recs), 1)
            self.assertEqual(all_recs[0].fingerprint, canonical_fp)

    # ─────────────────────────────────────────────────────────────────────────
    # 4. Delivery Retry Persistence Across Restart
    # ─────────────────────────────────────────────────────────────────────────

    def test_delivery_retry_persistence_across_restarts(self):
        """Verify pending delivery retries survive restart and are reconciled."""
        from normalization.dispatcher import ConsumerDeliveryResult, DeliveryStatus

        now = datetime.now(timezone.utc)
        payload_data = {
            "opportunity_id": "opp_del_01",
            "canonical_event_id": "ev_01",
            "canonical_market_key": {
                "market_type": "1X2",
                "period": "FULL_TIME",
                "scope": "MATCH",
                "line": None,
            },
            "legs": [
                {"selection_type": "HOME", "provider": "superbet", "odds": "2.40", "source_selection_id": "s1"},
                {"selection_type": "DRAW", "provider": "betclic", "odds": "3.50", "source_selection_id": "s2"},
                {"selection_type": "AWAY", "provider": "betclic", "odds": "3.20", "source_selection_id": "s3"},
            ],
            "arbitrage_margin": "0.04",
            "implied_probability_sum": "0.96",
        }

        with self.db_manager.get_session() as session:
            # Seed opportunity
            opp_repo = OpportunityRepository(session)
            opp_rec = OpportunityRecordORM(
                id="opp_del_01",
                fingerprint="fp_delivery_test",
                canonical_event_id="ev_01",
                market_key="1X2:FULL_TIME:MATCH:no_line",
                status="NEW",
                first_seen_at=now,
                last_seen_at=now,
                last_changed_at=now,
                arbitrage_margin=0.04,
                implied_probability_sum=0.96,
                snapshot_json=json.dumps(payload_data),
            )
            opp_repo.save_or_update(opp_rec)

            # Seed failing delivery in RETRY_PENDING state
            del_repo = DeliveryRepository(session)
            del_rec = DeliveryRecordORM(
                id="del_01",
                idempotency_key="ik_test_retry_01",
                opportunity_fingerprint="fp_delivery_test",
                opportunity_id="opp_del_01",
                consumer_name="TelegramConsumer",
                lifecycle_version=1,
                state="RETRY_PENDING",
                attempt_count=1,
                max_attempts=3,
                created_at=now - timedelta(minutes=5),
                next_retry_at=now - timedelta(minutes=1),  # Due for retry
                payload_snapshot_json=json.dumps(payload_data),
            )
            del_repo.save_or_update(del_rec)
            session.commit()

        # Simulate Restart: Reconcile in a new session with working consumer
        with self.db_manager.get_session() as session_restart:
            restart_del_repo = DeliveryRepository(session_restart)
            restart_opp_repo = OpportunityRepository(session_restart)

            class SuccessConsumer(OpportunityConsumer):
                def __init__(self):
                    self.delivered = []

                @property
                def name(self) -> str:
                    return "TelegramConsumer"

                def consume(self, opp: Any) -> ConsumerDeliveryResult:
                    self.delivered.append(opp)
                    return ConsumerDeliveryResult(
                        consumer_name="TelegramConsumer",
                        status=DeliveryStatus.DELIVERED,
                        metadata={"msg_id": "123"},
                    )

            test_consumer = SuccessConsumer()
            dispatcher = OpportunityDispatcher()
            dispatcher.register_consumer(test_consumer)

            reconciliation = DeliveryReconciliationService(
                delivery_repository=restart_del_repo,
                opportunity_repository=restart_opp_repo,
                dispatcher=dispatcher,
            )
            rec_summary = reconciliation.reconcile(current_time=now)

            self.assertEqual(rec_summary.scanned_count, 1)
            self.assertEqual(rec_summary.delivered_count, 1)

            # Check that record in DB transitioned to DELIVERED
            updated_del = restart_del_repo.get_by_idempotency_key("ik_test_retry_01")
            self.assertEqual(updated_del.state, "DELIVERED")
            self.assertIsNotNone(updated_del.delivered_at)

    # ─────────────────────────────────────────────────────────────────────────
    # 5. Scan History & Latest Scan Result Persistence Across Restart
    # ─────────────────────────────────────────────────────────────────────────

    def test_scan_history_and_latest_scan_persistence(self):
        """Verify scan history and last scan result persist to SQLite and restore on service startup."""
        service_a = PlatformAPIService(db_manager=self.db_manager)
        service_a.scheduler.stop()

        # Run mock scan via service_a
        mock_result = {
            "execution_id": "scan_20260817_120000_abcd1234",
            "cycle_status": "SUCCESS",
            "started_at": "2026-08-17T12:00:00Z",
            "completed_at": "2026-08-17T12:00:02Z",
            "duration_seconds": 2.0,
            "counts": {
                "discovered_events": 10,
                "selected_events": 8,
                "matched_events": 2,
                "detected_opportunities": 1,
            },
            "opportunities": [],
            "errors": [],
            "warnings": [],
        }

        _save_scan_snapshot(self.db_manager, "scan_20260817_120000_abcd1234", mock_result)

        # Simulate restart: Create service_b connected to same persistent DB
        service_b = PlatformAPIService(db_manager=self.db_manager)
        service_b.scheduler.stop()

        latest = service_b.get_latest_scan()
        self.assertIsNotNone(latest)
        self.assertEqual(latest["execution_id"], "scan_20260817_120000_abcd1234")
        self.assertEqual(latest["cycle_status"], "SUCCESS")

        history = service_b.get_scan_history(limit=5)
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["execution_id"], "scan_20260817_120000_abcd1234")
        self.assertEqual(history[0]["events_discovered"], 10)

    # ─────────────────────────────────────────────────────────────────────────
    # 6. Scheduler Configuration Persistence Across Restart
    # ─────────────────────────────────────────────────────────────────────────

    def test_scheduler_configuration_persistence(self):
        """Verify scheduler configuration survives restart."""
        service_a = PlatformAPIService(db_manager=self.db_manager)
        service_a.scheduler.stop()

        # Configure scheduler on service_a
        service_a.configure_scheduler(
            enabled=True,
            interval_minutes=25,
            scan_scope="ALL",
            hours_ahead=48,
            event_limit=100,
        )

        status_a = service_a.get_scheduler_status()
        self.assertTrue(status_a["enabled"])
        self.assertEqual(status_a["interval_minutes"], 25)
        self.assertEqual(status_a["scan_scope"], "ALL")
        self.assertEqual(status_a["hours_ahead"], 48)
        self.assertEqual(status_a["event_limit"], 100)

        # Simulate restart: create service_b
        service_b = PlatformAPIService(db_manager=self.db_manager)
        service_b.scheduler.stop()

        status_b = service_b.get_scheduler_status()
        self.assertTrue(status_b["enabled"])
        self.assertEqual(status_b["interval_minutes"], 25)
        self.assertEqual(status_b["scan_scope"], "ALL")
        self.assertEqual(status_b["hours_ahead"], 48)
        self.assertEqual(status_b["event_limit"], 100)

    # ─────────────────────────────────────────────────────────────────────────
    # 7. Scheduler Idempotency & Clean Stop
    # ─────────────────────────────────────────────────────────────────────────

    def test_scheduler_idempotency_and_clean_stop(self):
        """Verify scheduler start/stop idempotency and clean exit without orphan threads."""
        service = PlatformAPIService(db_manager=self.db_manager)
        scheduler = service.scheduler

        # Idempotent start
        scheduler.start()
        scheduler.start()
        self.assertTrue(scheduler._thread.is_alive())

        # Clean stop
        scheduler.stop()
        self.assertFalse(scheduler._thread.is_alive())

        # Stop again (safe)
        scheduler.stop()

    # ─────────────────────────────────────────────────────────────────────────
    # 8. Error Boundary: Provider Failure Isolation
    # ─────────────────────────────────────────────────────────────────────────

    def test_provider_failure_isolation(self):
        """Verify when Superbet fails, Betclic succeeds and cycle enters safe PARTIAL state."""
        g_bc = _create_mock_graph("ev_bc_01", "betclic", 2.5, 3.2, 2.9)

        failing_superbet = MockProvider("superbet", should_fail=True, failure_stage="fetch")
        working_betclic = MockProvider("betclic", parsed_items=[g_bc])

        orchestrator = ProductionScanOrchestrator(
            config=ScanConfig(fail_on_critical_error=False),
            db_manager=self.db_manager,
        )
        orchestrator.normalization_engine.register_normalizer("betclic", MockNormalizerWrapper([g_bc]))

        result = orchestrator.run_scan_cycle(
            providers={"superbet": failing_superbet, "betclic": working_betclic}
        )

        self.assertEqual(result.cycle_status, CycleStatus.PARTIAL)
        self.assertEqual(result.provider_results["superbet"].status, ProviderState.FAILED)
        self.assertEqual(result.provider_results["betclic"].status, ProviderState.COMPLETED)
        # Safe isolation: 0 surebets evaluated when one provider fails
        self.assertEqual(result.detected_opportunities_count, 0)
        self.assertGreater(len(result.warnings), 0)

    # ─────────────────────────────────────────────────────────────────────────
    # 9. Error Boundary: Scheduler Survives Scan Errors
    # ─────────────────────────────────────────────────────────────────────────

    def test_scheduler_survives_scan_error(self):
        """Verify scheduler handles scan exceptions gracefully without crashing background worker."""
        service = PlatformAPIService(db_manager=self.db_manager)
        service.scheduler.stop()

        # Mock run_scan to raise an unexpected runtime error
        with patch.object(service, "run_scan", side_effect=RuntimeError("Provider API timeout")):
            with self.assertRaises(Exception):
                service.scheduler.run_scan_now()

            # Scheduler status should record error and remain valid
            status = service.scheduler.get_status()
            self.assertIsNotNone(status)
            self.assertEqual(status["last_scan_status"], "FAILED")
            self.assertIn("RuntimeError", status["last_error"])

    # ─────────────────────────────────────────────────────────────────────────
    # 10. Secret & Credential Redaction
    # ─────────────────────────────────────────────────────────────────────────

    def test_secret_redaction(self):
        """Verify bot tokens, authorization headers, passwords and bearer tokens are redacted."""
        raw_token = "8872614952:AAFvj0DRjKH4CRByDrQN8PlZwGABXqqvdro"
        raw_msg = f"Connecting to Telegram with token {raw_token} and Bearer eyJhbGciOiJIUzI1Ni.secret"

        redacted = redact_secrets(raw_msg)
        self.assertNotIn(raw_token, redacted)
        self.assertIn("[REDACTED_BOT_TOKEN]", redacted)
        self.assertNotIn("eyJhbGciOiJIUzI1Ni", redacted)
        self.assertIn("[REDACTED_TOKEN]", redacted)

        # DB password redaction
        db_url_raw = "postgresql://user:super_secret_db_pass@localhost:5432/zielonebety"
        redacted_db = redact_secrets(db_url_raw)
        self.assertNotIn("super_secret_db_pass", redacted_db)
        self.assertIn("[REDACTED_PASSWORD]", redacted_db)

        # Service text sanitizer
        sanitized = _sanitize_text(f"Telegram token: {raw_token} at C:\\Users\\Admin\\app.py")
        self.assertNotIn(raw_token, sanitized)
        self.assertIn("[REDACTED_BOT_TOKEN]", sanitized)

    # ─────────────────────────────────────────────────────────────────────────
    # 11. API Safe Error Envelopes
    # ─────────────────────────────────────────────────────────────────────────

    def test_api_safe_error_envelopes(self):
        """Verify invalid IDs and exceptions return controlled envelopes without leaking internal tracebacks."""
        service = PlatformAPIService(db_manager=self.db_manager)
        service.scheduler.stop()
        router = APIRouter(service=service)

        # Nonexistent opportunity -> controlled 404
        res404 = router.handle_get_opportunity_detail("nonexistent_opp_id_9999")
        self.assertEqual(res404.status_code, 404)
        self.assertIn("not found", res404.errors[0].lower())

        # Invalid scheduler configure -> controlled 400
        res400 = router.handle_post_scheduler_configure({"interval_minutes": -5})
        # Interval is capped to min 1 safely
        self.assertEqual(res400.status_code, 200)
        self.assertEqual(res400.data["interval_minutes"], 1)

    # ─────────────────────────────────────────────────────────────────────────
    # 12. Multi-Cycle Stability & Resource Safety
    # ─────────────────────────────────────────────────────────────────────────

    def test_multi_cycle_stability(self):
        """Verify memory and thread counts remain stable across 6 consecutive scan cycles."""
        initial_threads = threading.active_count()

        g_sb = _create_mock_graph("ev_stab_01", "superbet", 2.45, 3.40, 3.10)
        g_bc = _create_mock_graph("ev_stab_01", "betclic", 2.40, 3.45, 3.15)

        orchestrator = ProductionScanOrchestrator(
            config=ScanConfig(fail_on_critical_error=False),
            db_manager=self.db_manager,
        )
        orchestrator.normalization_engine.register_normalizer("superbet", MockNormalizerWrapper([g_sb]))
        orchestrator.normalization_engine.register_normalizer("betclic", MockNormalizerWrapper([g_bc]))
        if tracemalloc.is_tracing():
            tracemalloc.reset_peak()

        for i in range(6):
            prov_sb = MockProvider("superbet", parsed_items=[g_sb])
            prov_bc = MockProvider("betclic", parsed_items=[g_bc])
            res = orchestrator.run_scan_cycle(providers={"superbet": prov_sb, "betclic": prov_bc})
            self.assertEqual(res.cycle_status, CycleStatus.SUCCESS)
            self.assertLess(res.resource_metrics.peak_memory_mb, 200.0)

        # Verify threads did not multiply
        final_threads = threading.active_count()
        self.assertLessEqual(final_threads, initial_threads + 2)


if __name__ == "__main__":
    unittest.main()
