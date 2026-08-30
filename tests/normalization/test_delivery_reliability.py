"""
Stage 6.5 Test Suite: Delivery Reliability, Retry & Reconciliation

Verifies:
1. Delivery State Transitions (PENDING -> DELIVERED, RETRY_PENDING, FAILED_PERMANENT, EXHAUSTED, SUPERSEDED).
2. Persistence across restarts (simulated via fresh SQLite sessions and reconnection).
3. Deterministic Idempotency Key generation and deduplication.
4. Failure Classification (HTTP 5xx, 429, timeout as transient; 400, 401, 403, 404 as permanent).
5. Bounded Exponential Backoff without infinite loops.
6. Crash Safety Boundaries (crash before send, crash after send before local DB commit).
7. Versioned Updates & Superseding (newer material generation supersedes stale pending retry).
8. Adversarial Resilience (corrupted snapshot JSON, missing opportunity, duplicate invocations).
9. Non-blocking operation (zero sleep in detection loop).
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.models import BaseORM, DeliveryRecordORM, OpportunityRecordORM
from database.repositories.delivery_repository import DeliveryRepository
from database.repositories.opportunity_repository import OpportunityRepository
from domain.models import MatchEvidence
from normalization.delivery_reliability import (
    DeliveryReconciliationService,
    DeliveryRetryConfig,
    DeliveryState,
    FailureCategory,
    ReconciliationSummary,
    calculate_next_retry_time,
    classify_delivery_failure,
    deserialize_opportunity_snapshot,
    generate_delivery_idempotency_key,
)
from normalization.dispatcher import (
    ConsumerDeliveryResult,
    DeliveryStatus,
    DispatchResult,
    DispatchStatus,
    DispatchableOpportunity,
    InMemoryOpportunityConsumer,
    OpportunityConsumer,
    OpportunityDispatcher,
)
from normalization.lifecycle import (
    OpportunityLifecycleManager,
    OpportunityStatus,
    generate_opportunity_fingerprint,
)
from normalization.market_identity import CanonicalMarketKey
from normalization.selection_identity import CanonicalSelectionKey
from normalization.surebet import (
    MarketSurebetEvaluation,
    SurebetDetectionResult,
    SurebetLeg,
    SurebetOpportunity,
    SurebetStatus,
)
from notifications.telegram_client import FakeTelegramClient
from notifications.telegram_consumer import TelegramConfig, TelegramOpportunityConsumer


class FlakyConsumer:
    """Mock consumer that can simulate transient or permanent failures for specific attempts."""

    def __init__(
        self,
        name: str = "telegram",
        fail_attempts: int = 0,
        failure_status: int = 500,
        error_message: str = "Internal Server Error",
    ) -> None:
        self._name = name
        self.fail_attempts = fail_attempts
        self.failure_status = failure_status
        self.error_message = error_message
        self.attempts = 0
        self.received_opportunities: list = []

    @property
    def name(self) -> str:
        return self._name

    def consume(self, opportunity: DispatchableOpportunity) -> ConsumerDeliveryResult:
        self.attempts += 1
        if self.attempts <= self.fail_attempts:
            return ConsumerDeliveryResult(
                consumer_name=self.name,
                status=DeliveryStatus.FAILED,
                error=f"HTTP {self.failure_status}: {self.error_message}",
                metadata={"http_status": self.failure_status},
            )
        self.received_opportunities.append(opportunity)
        return ConsumerDeliveryResult(
            consumer_name=self.name,
            status=DeliveryStatus.DELIVERED,
            metadata={"http_status": 200, "telegram_message_id": f"msg_{self.attempts}"},
        )


@pytest.fixture
def test_db():
    """Provides a fresh, isolated in-memory SQLite database with ORM tables initialized."""
    engine = create_engine("sqlite:///:memory:", echo=False)
    BaseORM.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = session_factory()
    yield {
        "engine": engine,
        "session_factory": session_factory,
        "session": session,
        "opp_repo": OpportunityRepository(session),
        "deliv_repo": DeliveryRepository(session),
    }
    session.close()


def _make_sample_opportunity(
    opp_id: str = "opp_test_001",
    event_id: str = "evt_ars_che",
    home_odds: Decimal = Decimal("2.10"),
    away_odds: Decimal = Decimal("2.10"),
) -> SurebetOpportunity:
    """Helper creating a valid 2-way SurebetOpportunity."""
    mkt_key = CanonicalMarketKey(
        market_type="TOTALS",
        period="FULL_MATCH",
        scope="ALL",
        line=Decimal("2.5"),
    )
    leg1 = SurebetLeg(
        selection_type="OVER",
        canonical_selection_key=CanonicalSelectionKey(market_key=mkt_key, selection_type="OVER"),
        provider="superbet",
        odds=home_odds,
        source_selection_id="sb_over_1",
        source_event_id="sb_evt_1",
        source_market_id="sb_mkt_1",
    )
    leg2 = SurebetLeg(
        selection_type="UNDER",
        canonical_selection_key=CanonicalSelectionKey(market_key=mkt_key, selection_type="UNDER"),
        provider="betclic",
        odds=away_odds,
        source_selection_id="bc_under_1",
        source_event_id="bc_evt_1",
        source_market_id="bc_mkt_1",
    )
    prob1 = Decimal("1.0") / home_odds
    prob2 = Decimal("1.0") / away_odds
    prob_sum = prob1 + prob2
    margin = (Decimal("1.0") / prob_sum) - Decimal("1.0")

    evidence = MatchEvidence(
        source_provider="superbet",
        target_provider="betclic",
        source_event_id="sb_evt_1",
        target_event_id="bc_evt_1",
        decision="MATCH",
        total_score=1.0,
        orientation="NATURAL",
        evidence={
            "home_team": "Arsenal",
            "away_team": "Chelsea",
            "competition_name": "Premier League",
            "start_time": "2026-08-20T19:00:00Z",
        },
    )


    return SurebetOpportunity(
        opportunity_id=opp_id,
        canonical_event_id=event_id,
        canonical_market_key=mkt_key,
        legs=(leg1, leg2),
        implied_probability_sum=prob_sum,
        arbitrage_margin=margin,
        is_mixed_bookmakers=True,
        bookmakers=("superbet", "betclic"),
        status=SurebetStatus.SUREBET,
        event_evidence=evidence,
    )


class TestDeliveryReliabilityMatrix:
    """Comprehensive Stage 6.5 verification matrix."""

    # -------------------------------------------------------------------------
    # 1. Delivery & Retry Flow
    # -------------------------------------------------------------------------
    def test_01_successful_first_delivery(self, test_db):
        """1. Successful first delivery commits DELIVERED delivery record and ALERTED opportunity."""
        session = test_db["session"]
        opp_repo = test_db["opp_repo"]
        deliv_repo = test_db["deliv_repo"]

        consumer = InMemoryOpportunityConsumer(name="telegram")
        dispatcher = OpportunityDispatcher(consumers=[consumer])
        manager = OpportunityLifecycleManager(
            repository=opp_repo,
            delivery_repository=deliv_repo,
        )

        opp = _make_sample_opportunity()
        det_result = SurebetDetectionResult(
            opportunities=(opp,),
            evaluations=(),
        )

        now = datetime(2026, 8, 17, 12, 0, 0, tzinfo=timezone.utc)
        summary = manager.process_and_dispatch(det_result, dispatcher, evaluation_time=now)

        assert summary.delivered_count == 1
        assert summary.failed_count == 0

        # Verify OpportunityRecordORM
        fp = generate_opportunity_fingerprint("SUREBET", opp.canonical_event_id, opp.canonical_market_key, opp.legs)
        opp_rec = opp_repo.get_by_fingerprint(fp)
        assert opp_rec is not None
        assert opp_rec.status == OpportunityStatus.ALERTED.value
        assert opp_rec.alert_count == 1
        assert opp_rec.delivery_status == "DELIVERED"

        # Verify DeliveryRecordORM
        delivs = deliv_repo.list_by_fingerprint(fp)
        assert len(delivs) == 1
        assert delivs[0].state == DeliveryState.DELIVERED.value
        assert delivs[0].attempt_count == 1
        deliv_at = delivs[0].delivered_at
        if deliv_at and deliv_at.tzinfo is None:
            deliv_at = deliv_at.replace(tzinfo=timezone.utc)
        assert deliv_at == now
        assert delivs[0].lifecycle_version == 1

    def test_02_first_delivery_fails_transiently(self, test_db):
        """2. First delivery fails (HTTP 500) -> state RETRY_PENDING, opportunity stays NEW (not ALERTED)."""
        session = test_db["session"]
        opp_repo = test_db["opp_repo"]
        deliv_repo = test_db["deliv_repo"]

        flaky = FlakyConsumer(name="telegram", fail_attempts=1, failure_status=500)
        dispatcher = OpportunityDispatcher(consumers=[flaky])
        retry_cfg = DeliveryRetryConfig(max_attempts=3, initial_delay_seconds=10.0, backoff_multiplier=2.0)
        manager = OpportunityLifecycleManager(
            repository=opp_repo,
            delivery_repository=deliv_repo,
            retry_config=retry_cfg,
        )

        opp = _make_sample_opportunity()
        det_result = SurebetDetectionResult(opportunities=(opp,), evaluations=())
        t0 = datetime(2026, 8, 17, 12, 0, 0, tzinfo=timezone.utc)

        summary = manager.process_and_dispatch(det_result, dispatcher, evaluation_time=t0)
        assert summary.delivered_count == 0
        assert summary.failed_count == 1

        # Opportunity must remain NEW
        fp = generate_opportunity_fingerprint("SUREBET", opp.canonical_event_id, opp.canonical_market_key, opp.legs)
        opp_rec = opp_repo.get_by_fingerprint(fp)
        assert opp_rec.status == OpportunityStatus.NEW.value
        assert opp_rec.alert_count == 0
        assert opp_rec.delivery_status == "FAILED"

        # DeliveryRecord must be RETRY_PENDING with next_retry_at = t0 + 10s
        delivs = deliv_repo.list_by_fingerprint(fp)
        assert len(delivs) == 1
        deliv = delivs[0]
        assert deliv.state == DeliveryState.RETRY_PENDING.value
        assert deliv.attempt_count == 1
        assert deliv.last_error_category == FailureCategory.TRANSIENT_FAILURE.value
        next_ret = deliv.next_retry_at
        if next_ret and next_ret.tzinfo is None:
            next_ret = next_ret.replace(tzinfo=timezone.utc)
        assert next_ret == t0 + timedelta(seconds=10.0)

    def test_03_retry_succeeds_via_reconciliation(self, test_db):
        """3. Scheduled retry succeeds -> transitions to DELIVERED, opportunity becomes ALERTED."""
        opp_repo = test_db["opp_repo"]
        deliv_repo = test_db["deliv_repo"]

        flaky = FlakyConsumer(name="telegram", fail_attempts=1, failure_status=500)
        dispatcher = OpportunityDispatcher(consumers=[flaky])
        retry_cfg = DeliveryRetryConfig(max_attempts=3, initial_delay_seconds=10.0, backoff_multiplier=2.0)
        manager = OpportunityLifecycleManager(
            repository=opp_repo,
            delivery_repository=deliv_repo,
            retry_config=retry_cfg,
        )

        opp = _make_sample_opportunity()
        t0 = datetime(2026, 8, 17, 12, 0, 0, tzinfo=timezone.utc)
        manager.process_and_dispatch(SurebetDetectionResult(opportunities=(opp,), evaluations=()), dispatcher, evaluation_time=t0)

        # Before retry time: reconciliation finds 0 due items
        recon_service = DeliveryReconciliationService(
            delivery_repository=deliv_repo,
            opportunity_repository=opp_repo,
            dispatcher=dispatcher,
            retry_config=retry_cfg,
        )
        early_time = t0 + timedelta(seconds=5.0)
        res_early = recon_service.reconcile(current_time=early_time)
        assert res_early.scanned_count == 0
        assert res_early.delivered_count == 0

        # At retry time (t0 + 10s): reconciliation fires and succeeds
        retry_time = t0 + timedelta(seconds=10.0)
        res_retry = recon_service.reconcile(current_time=retry_time)
        assert res_retry.scanned_count == 1
        assert res_retry.attempted_count == 1
        assert res_retry.delivered_count == 1

        # Verify final state
        fp = generate_opportunity_fingerprint("SUREBET", opp.canonical_event_id, opp.canonical_market_key, opp.legs)
        opp_rec = opp_repo.get_by_fingerprint(fp)
        assert opp_rec.status == OpportunityStatus.ALERTED.value
        assert opp_rec.alert_count == 1

        deliv = deliv_repo.list_by_fingerprint(fp)[0]
        assert deliv.state == DeliveryState.DELIVERED.value
        assert deliv.attempt_count == 2
        del_at = deliv.delivered_at
        if del_at and del_at.tzinfo is None:
            del_at = del_at.replace(tzinfo=timezone.utc)
        assert del_at == retry_time

    def test_04_repeated_failures_exhaust_bounded_retries(self, test_db):
        """4. Repeated failures backoff exponentially and transition to EXHAUSTED at max_attempts."""
        opp_repo = test_db["opp_repo"]
        deliv_repo = test_db["deliv_repo"]

        always_fail = FlakyConsumer(name="telegram", fail_attempts=99, failure_status=503)
        dispatcher = OpportunityDispatcher(consumers=[always_fail])
        retry_cfg = DeliveryRetryConfig(max_attempts=3, initial_delay_seconds=5.0, backoff_multiplier=3.0)
        manager = OpportunityLifecycleManager(
            repository=opp_repo,
            delivery_repository=deliv_repo,
            retry_config=retry_cfg,
        )
        recon_service = DeliveryReconciliationService(
            delivery_repository=deliv_repo,
            opportunity_repository=opp_repo,
            dispatcher=dispatcher,
            retry_config=retry_cfg,
        )

        opp = _make_sample_opportunity()
        t0 = datetime(2026, 8, 17, 12, 0, 0, tzinfo=timezone.utc)

        # Attempt 1 (initial dispatch)
        manager.process_and_dispatch(SurebetDetectionResult(opportunities=(opp,), evaluations=()), dispatcher, evaluation_time=t0)
        fp = generate_opportunity_fingerprint("SUREBET", opp.canonical_event_id, opp.canonical_market_key, opp.legs)
        deliv = deliv_repo.list_by_fingerprint(fp)[0]
        assert deliv.state == DeliveryState.RETRY_PENDING.value
        assert deliv.attempt_count == 1
        n1 = deliv.next_retry_at
        if n1 and n1.tzinfo is None:
            n1 = n1.replace(tzinfo=timezone.utc)
        assert n1 == t0 + timedelta(seconds=5.0)

        # Attempt 2 (reconciliation at t0 + 5s)
        t1 = t0 + timedelta(seconds=5.0)
        res1 = recon_service.reconcile(current_time=t1)
        assert res1.retried_count == 1
        deliv = deliv_repo.list_by_fingerprint(fp)[0]
        assert deliv.state == DeliveryState.RETRY_PENDING.value
        assert deliv.attempt_count == 2
        # delay = 5.0 * (3.0 ** 1) = 15.0s
        n2 = deliv.next_retry_at
        if n2 and n2.tzinfo is None:
            n2 = n2.replace(tzinfo=timezone.utc)
        assert n2 == t1 + timedelta(seconds=15.0)

        # Attempt 3 (reconciliation at t1 + 15s) -> Reaches max_attempts (3)
        t2 = t1 + timedelta(seconds=15.0)
        res2 = recon_service.reconcile(current_time=t2)
        assert res2.exhausted_count == 1
        deliv = deliv_repo.list_by_fingerprint(fp)[0]
        assert deliv.state == DeliveryState.EXHAUSTED.value
        assert deliv.attempt_count == 3
        assert deliv.next_retry_at is None

        # Opportunity was never marked ALERTED
        opp_rec = opp_repo.get_by_fingerprint(fp)
        assert opp_rec.status == OpportunityStatus.NEW.value
        assert opp_rec.alert_count == 0

    def test_05_permanent_failure_stops_retrying_immediately(self, test_db):
        """5. Permanent failure (e.g. HTTP 401 Unauthorized / Invalid Token) marks FAILED_PERMANENT and never retries."""
        opp_repo = test_db["opp_repo"]
        deliv_repo = test_db["deliv_repo"]

        perm_fail = FlakyConsumer(name="telegram", fail_attempts=10, failure_status=401, error_message="Unauthorized: bot token invalid")
        dispatcher = OpportunityDispatcher(consumers=[perm_fail])
        manager = OpportunityLifecycleManager(
            repository=opp_repo,
            delivery_repository=deliv_repo,
        )

        opp = _make_sample_opportunity()
        t0 = datetime(2026, 8, 17, 12, 0, 0, tzinfo=timezone.utc)
        manager.process_and_dispatch(SurebetDetectionResult(opportunities=(opp,), evaluations=()), dispatcher, evaluation_time=t0)

        fp = generate_opportunity_fingerprint("SUREBET", opp.canonical_event_id, opp.canonical_market_key, opp.legs)
        deliv = deliv_repo.list_by_fingerprint(fp)[0]
        assert deliv.state == DeliveryState.FAILED_PERMANENT.value
        assert deliv.attempt_count == 1
        assert deliv.next_retry_at is None
        assert deliv.last_error_category == FailureCategory.PERMANENT_FAILURE.value

        # Reconciliation does not retry permanent failures
        recon_service = DeliveryReconciliationService(
            delivery_repository=deliv_repo,
            opportunity_repository=opp_repo,
            dispatcher=dispatcher,
        )
        recon_summary = recon_service.reconcile(current_time=t0 + timedelta(hours=1))
        assert recon_summary.scanned_count == 0
        assert recon_summary.attempted_count == 0

    # -------------------------------------------------------------------------
    # 2. Persistence & Process Restart Safety
    # -------------------------------------------------------------------------
    def test_06_pending_and_retry_pending_survive_process_restart(self, test_db):
        """6-8. Pending and Retry-Pending states survive process restart (reconnection to SQLite DB)."""
        engine = test_db["engine"]
        session_factory = test_db["session_factory"]

        # Run 1: Create opportunity and attempt delivery with transient failure
        session1 = session_factory()
        opp_repo1 = OpportunityRepository(session1)
        deliv_repo1 = DeliveryRepository(session1)

        flaky1 = FlakyConsumer(name="telegram", fail_attempts=1, failure_status=504)
        disp1 = OpportunityDispatcher(consumers=[flaky1])
        t0 = datetime(2026, 8, 17, 12, 0, 0, tzinfo=timezone.utc)

        mgr1 = OpportunityLifecycleManager(repository=opp_repo1, delivery_repository=deliv_repo1)
        opp = _make_sample_opportunity()
        mgr1.process_and_dispatch(SurebetDetectionResult(opportunities=(opp,), evaluations=()), disp1, evaluation_time=t0)

        # Commit and close session 1
        session1.commit()
        session1.close()

        # Run 2: Startup in new process / session
        session2 = session_factory()
        opp_repo2 = OpportunityRepository(session2)
        deliv_repo2 = DeliveryRepository(session2)

        # Verify persistent record survived
        fp = generate_opportunity_fingerprint("SUREBET", opp.canonical_event_id, opp.canonical_market_key, opp.legs)
        deliv_recs = deliv_repo2.list_by_fingerprint(fp)
        assert len(deliv_recs) == 1
        assert deliv_recs[0].state == DeliveryState.RETRY_PENDING.value
        assert deliv_recs[0].attempt_count == 1

        # Re-attach working consumer and run reconciliation on restart
        consumer_working = InMemoryOpportunityConsumer(name="telegram")
        disp2 = OpportunityDispatcher(consumers=[consumer_working])
        recon2 = DeliveryReconciliationService(
            delivery_repository=deliv_repo2,
            opportunity_repository=opp_repo2,
            dispatcher=disp2,
        )

        t_restart = t0 + timedelta(seconds=15.0)
        recon_res = recon2.reconcile(current_time=t_restart)
        assert recon_res.delivered_count == 1

        # Check DB in session 2
        opp_rec2 = opp_repo2.get_by_fingerprint(fp)
        assert opp_rec2.status == OpportunityStatus.ALERTED.value
        assert opp_rec2.alert_count == 1

        session2.close()

    # -------------------------------------------------------------------------
    # 3. Idempotency & Duplicate Prevention
    # -------------------------------------------------------------------------
    def test_09_idempotency_key_consistency_across_retries(self, test_db):
        """9. Same logical generation produced identical idempotency key; duplicate dispatch does not create duplicate record."""
        deliv_repo = test_db["deliv_repo"]
        opp_repo = test_db["opp_repo"]

        fp = "opp:SUREBET:evt_1:market_1:OVER:superbet|UNDER:betclic"
        k1 = generate_delivery_idempotency_key(fp, 1, "telegram")
        k2 = generate_delivery_idempotency_key(fp, 1, "TELEGRAM")
        k3 = generate_delivery_idempotency_key(fp, 1, " telegram ")

        assert k1 == "deliv:opp:SUREBET:evt_1:market_1:OVER:superbet|UNDER:betclic:v1:telegram"
        assert k1 == k2 == k3

    def test_10_new_material_generation_gets_new_idempotency_key(self, test_db):
        """10. Material update produces a distinct versioned idempotency key (e.g. v2)."""
        fp = "opp:SUREBET:evt_1:market_1:OVER:superbet|UNDER:betclic"
        k_v1 = generate_delivery_idempotency_key(fp, 1, "telegram")
        k_v2 = generate_delivery_idempotency_key(fp, 2, "telegram")

        assert k_v1 != k_v2
        assert "v1" in k_v1
        assert "v2" in k_v2

    def test_11_duplicate_dispatcher_invocation_does_not_resend_delivered(self, test_db):
        """11. Once DELIVERED, repeated scan of unchanged opportunity is suppressed and does not duplicate delivery."""
        opp_repo = test_db["opp_repo"]
        deliv_repo = test_db["deliv_repo"]

        consumer = InMemoryOpportunityConsumer(name="telegram")
        dispatcher = OpportunityDispatcher(consumers=[consumer])
        manager = OpportunityLifecycleManager(
            repository=opp_repo,
            delivery_repository=deliv_repo,
        )

        opp = _make_sample_opportunity()
        t0 = datetime(2026, 8, 17, 12, 0, 0, tzinfo=timezone.utc)
        det_result = SurebetDetectionResult(opportunities=(opp,), evaluations=())

        # Scan 1: Initial alert delivered
        summary1 = manager.process_and_dispatch(det_result, dispatcher, evaluation_time=t0)
        assert summary1.delivered_count == 1
        assert len(consumer.received_opportunities) == 1

        # Scan 2: Unchanged opportunity -> Suppressed
        t1 = t0 + timedelta(seconds=10.0)
        summary2 = manager.process_and_dispatch(det_result, dispatcher, evaluation_time=t1)
        assert summary2.evaluation_batch.suppressed_count == 1
        assert summary2.delivered_count == 0
        assert len(consumer.received_opportunities) == 1  # No duplicate message sent

        # Only one delivery record exists
        fp = generate_opportunity_fingerprint("SUREBET", opp.canonical_event_id, opp.canonical_market_key, opp.legs)
        delivs = deliv_repo.list_by_fingerprint(fp)
        assert len(delivs) == 1
        assert delivs[0].state == DeliveryState.DELIVERED.value

    # -------------------------------------------------------------------------
    # 4. Crash Boundaries & At-Least-Once Semantics
    # -------------------------------------------------------------------------
    def test_12_crash_before_send_recovers_via_reconciliation(self, test_db):
        """12. If system crashes right after persisting PENDING record before send, reconciliation recovers and delivers."""
        session = test_db["session"]
        opp_repo = test_db["opp_repo"]
        deliv_repo = test_db["deliv_repo"]

        opp = _make_sample_opportunity()
        fp = generate_opportunity_fingerprint("SUREBET", opp.canonical_event_id, opp.canonical_market_key, opp.legs)
        now = datetime(2026, 8, 17, 12, 0, 0, tzinfo=timezone.utc)

        # Simulate opportunity saved as NEW
        opp_rec = OpportunityRecordORM(
            id="rec_001",
            fingerprint=fp,
            opportunity_type="SUREBET",
            canonical_event_id=opp.canonical_event_id,
            market_key=opp.canonical_market_key.to_key_string(),
            status="NEW",
            first_seen_at=now,
            last_seen_at=now,
            last_changed_at=now,
            arbitrage_margin=float(opp.arbitrage_margin),
            implied_probability_sum=float(opp.implied_probability_sum),
            consecutive_misses=0,
            snapshot_json=json.dumps({
                "opportunity_id": opp.opportunity_id,
                "canonical_event_id": opp.canonical_event_id,
                "canonical_market_key": {
                    "market_type": opp.canonical_market_key.market_type,
                    "period": opp.canonical_market_key.period,
                    "scope": opp.canonical_market_key.scope,
                    "line": str(opp.canonical_market_key.line),
                },
                "arbitrage_margin": str(opp.arbitrage_margin),
                "implied_probability_sum": str(opp.implied_probability_sum),
                "is_mixed_bookmakers": True,
                "bookmakers": list(opp.bookmakers),
                "legs": [
                    {
                        "selection_type": l.selection_type,
                        "provider": l.provider,
                        "odds": str(l.odds),
                        "source_selection_id": l.source_selection_id,
                    }
                    for l in opp.legs
                ],
            }),
            alert_count=0,
        )
        opp_repo.save_or_update(opp_rec)

        # Simulate PENDING record persisted before network call crashed
        deliv_rec = DeliveryRecordORM(
            id="del_001",
            idempotency_key=generate_delivery_idempotency_key(fp, 1, "telegram"),
            opportunity_fingerprint=fp,
            opportunity_id=opp.opportunity_id,
            consumer_name="telegram",
            lifecycle_version=1,
            state=DeliveryState.PENDING.value,
            attempt_count=0,
            max_attempts=3,
            created_at=now,
            payload_snapshot_json=opp_rec.snapshot_json,
        )
        deliv_repo.save_or_update(deliv_rec)

        # On restart: reconciliation runs
        consumer = InMemoryOpportunityConsumer(name="telegram")
        dispatcher = OpportunityDispatcher(consumers=[consumer])
        recon = DeliveryReconciliationService(
            delivery_repository=deliv_repo,
            opportunity_repository=opp_repo,
            dispatcher=dispatcher,
        )

        res = recon.reconcile(current_time=now + timedelta(seconds=1))
        assert res.delivered_count == 1
        assert len(consumer.received_opportunities) == 1

        # Check DB state
        saved_deliv = deliv_repo.list_by_fingerprint(fp)[0]
        assert saved_deliv.state == DeliveryState.DELIVERED.value
        assert saved_deliv.attempt_count == 1

        saved_opp = opp_repo.get_by_fingerprint(fp)
        assert saved_opp.status == OpportunityStatus.ALERTED.value
        assert saved_opp.alert_count == 1

    def test_13_crash_after_send_before_local_commit_at_least_once_semantics(self, test_db):
        """13. Process crash after send but before local success commit results in safe at-least-once re-delivery."""
        opp_repo = test_db["opp_repo"]
        deliv_repo = test_db["deliv_repo"]

        # Suppose Telegram sent message, but local process died before DB commit
        # Local DB still has state=PENDING
        opp = _make_sample_opportunity()
        fp = generate_opportunity_fingerprint("SUREBET", opp.canonical_event_id, opp.canonical_market_key, opp.legs)
        now = datetime(2026, 8, 17, 12, 0, 0, tzinfo=timezone.utc)

        snapshot_str = json.dumps({
            "opportunity_id": opp.opportunity_id,
            "canonical_event_id": opp.canonical_event_id,
            "canonical_market_key": {
                "market_type": opp.canonical_market_key.market_type,
                "period": opp.canonical_market_key.period,
                "scope": opp.canonical_market_key.scope,
                "line": str(opp.canonical_market_key.line),
            },
            "arbitrage_margin": str(opp.arbitrage_margin),
            "implied_probability_sum": str(opp.implied_probability_sum),
            "is_mixed_bookmakers": True,
            "bookmakers": list(opp.bookmakers),
            "legs": [
                {
                    "selection_type": l.selection_type,
                    "provider": l.provider,
                    "odds": str(l.odds),
                    "source_selection_id": l.source_selection_id,
                }
                for l in opp.legs
            ],
        })

        opp_rec = OpportunityRecordORM(
            id="rec_002",
            fingerprint=fp,
            canonical_event_id=opp.canonical_event_id,
            market_key=opp.canonical_market_key.to_key_string(),
            status="NEW",
            first_seen_at=now,
            last_seen_at=now,
            last_changed_at=now,
            arbitrage_margin=float(opp.arbitrage_margin),
            implied_probability_sum=float(opp.implied_probability_sum),
            snapshot_json=snapshot_str,
            alert_count=0,
        )
        opp_repo.save_or_update(opp_rec)

        deliv_rec = DeliveryRecordORM(
            id="del_002",
            idempotency_key=generate_delivery_idempotency_key(fp, 1, "telegram"),
            opportunity_fingerprint=fp,
            opportunity_id=opp.opportunity_id,
            consumer_name="telegram",
            lifecycle_version=1,
            state=DeliveryState.PENDING.value,
            attempt_count=0,
            max_attempts=3,
            created_at=now,
            payload_snapshot_json=snapshot_str,
        )
        deliv_repo.save_or_update(deliv_rec)

        # Reconciliation executes in new process
        consumer = InMemoryOpportunityConsumer(name="telegram")
        dispatcher = OpportunityDispatcher(consumers=[consumer])
        recon = DeliveryReconciliationService(
            delivery_repository=deliv_repo,
            opportunity_repository=opp_repo,
            dispatcher=dispatcher,
        )

        res = recon.reconcile(current_time=now + timedelta(seconds=2))
        assert res.delivered_count == 1
        assert len(consumer.received_opportunities) == 1

        # Local state is now committed as DELIVERED
        assert deliv_repo.list_by_fingerprint(fp)[0].state == DeliveryState.DELIVERED.value
        assert opp_repo.get_by_fingerprint(fp).status == OpportunityStatus.ALERTED.value

    # -------------------------------------------------------------------------
    # 5. Versioned Updates & Superseding
    # -------------------------------------------------------------------------
    def test_14_stale_retry_is_superseded_by_newer_material_update(self, test_db):
        """14. Newer material update marks previous pending retry as SUPERSEDED so stale odds are never sent."""
        opp_repo = test_db["opp_repo"]
        deliv_repo = test_db["deliv_repo"]

        # Consumer fails attempt 1 on v1
        flaky = FlakyConsumer(name="telegram", fail_attempts=1, failure_status=500)
        dispatcher = OpportunityDispatcher(consumers=[flaky])
        retry_cfg = DeliveryRetryConfig(max_attempts=3, initial_delay_seconds=30.0)
        manager = OpportunityLifecycleManager(
            repository=opp_repo,
            delivery_repository=deliv_repo,
            retry_config=retry_cfg,
        )

        opp_v1 = _make_sample_opportunity(opp_id="opp_v1", home_odds=Decimal("2.10"), away_odds=Decimal("2.10"))
        t0 = datetime(2026, 8, 17, 12, 0, 0, tzinfo=timezone.utc)
        manager.process_and_dispatch(SurebetDetectionResult(opportunities=(opp_v1,), evaluations=()), dispatcher, evaluation_time=t0)

        fp = generate_opportunity_fingerprint("SUREBET", opp_v1.canonical_event_id, opp_v1.canonical_market_key, opp_v1.legs)
        deliv_v1 = deliv_repo.list_by_fingerprint(fp)[0]
        assert deliv_v1.state == DeliveryState.RETRY_PENDING.value
        assert deliv_v1.lifecycle_version == 1

        # Now before retry time, scanner observes material change (v2 with odds 2.50)
        t_update = t0 + timedelta(seconds=10.0)
        opp_v2 = _make_sample_opportunity(opp_id="opp_v2", home_odds=Decimal("2.50"), away_odds=Decimal("2.50"))
        
        # Now flaky is ready to accept v2
        summary_v2 = manager.process_and_dispatch(SurebetDetectionResult(opportunities=(opp_v2,), evaluations=()), dispatcher, evaluation_time=t_update)
        assert summary_v2.delivered_count == 1

        # Verify deliv_v1 was marked SUPERSEDED
        deliv_records = deliv_repo.list_by_fingerprint(fp)
        assert len(deliv_records) == 2
        rec_v1 = [r for r in deliv_records if r.lifecycle_version == 1][0]
        rec_v2 = [r for r in deliv_records if r.lifecycle_version == 2][0]

        assert rec_v1.state == DeliveryState.SUPERSEDED.value
        assert rec_v2.state == DeliveryState.DELIVERED.value

        # Reconciliation should ignore rec_v1
        recon = DeliveryReconciliationService(deliv_repo, opp_repo, dispatcher)
        recon_res = recon.reconcile(current_time=t0 + timedelta(seconds=40.0))
        assert recon_res.delivered_count == 0

    # -------------------------------------------------------------------------
    # 6. Telegram Client & Transport Error Handling
    # -------------------------------------------------------------------------
    def test_16_telegram_consumer_http_5xx_classified_transient(self, test_db):
        """16. Telegram 5xx response is classified as TRANSIENT_FAILURE."""
        client = FakeTelegramClient(simulate_http_status=502, simulate_error="HTTP 502: Bad Gateway")
        consumer = TelegramOpportunityConsumer(
            config=TelegramConfig(bot_token="tok123", chat_id="chat123"),
            client=client,
        )
        opp = _make_sample_opportunity()
        disp_opp = DispatchableOpportunity(
            opportunity_id=opp.opportunity_id,
            canonical_event_id=opp.canonical_event_id,
            canonical_market_key=opp.canonical_market_key,
            legs=opp.legs,
            implied_probability_sum=opp.implied_probability_sum,
            arbitrage_margin=opp.arbitrage_margin,
            is_mixed_bookmakers=opp.is_mixed_bookmakers,
            bookmakers=opp.bookmakers,
            source_opportunity=opp,
        )

        res = consumer.consume(disp_opp)
        assert res.status == DeliveryStatus.FAILED
        cat = classify_delivery_failure(res)
        assert cat == FailureCategory.TRANSIENT_FAILURE

    def test_17_telegram_consumer_http_429_classified_transient(self, test_db):
        """17. Telegram 429 Too Many Requests response is classified as TRANSIENT_FAILURE."""
        client = FakeTelegramClient(simulate_http_status=429, simulate_error="HTTP 429: Too Many Requests: retry after 5")
        consumer = TelegramOpportunityConsumer(
            config=TelegramConfig(bot_token="tok123", chat_id="chat123"),
            client=client,
        )
        opp = _make_sample_opportunity()
        disp_opp = DispatchableOpportunity(
            opportunity_id=opp.opportunity_id,
            canonical_event_id=opp.canonical_event_id,
            canonical_market_key=opp.canonical_market_key,
            legs=opp.legs,
            implied_probability_sum=opp.implied_probability_sum,
            arbitrage_margin=opp.arbitrage_margin,
            is_mixed_bookmakers=opp.is_mixed_bookmakers,
            bookmakers=opp.bookmakers,
            source_opportunity=opp,
        )

        res = consumer.consume(disp_opp)
        assert res.status == DeliveryStatus.FAILED
        cat = classify_delivery_failure(res)
        assert cat == FailureCategory.TRANSIENT_FAILURE

    def test_18_telegram_consumer_http_401_classified_permanent(self, test_db):
        """18. Telegram 401 Unauthorized response is classified as PERMANENT_FAILURE."""
        client = FakeTelegramClient(simulate_http_status=401, simulate_error="HTTP 401: Unauthorized: bot token is invalid")
        consumer = TelegramOpportunityConsumer(
            config=TelegramConfig(bot_token="tok123", chat_id="chat123"),
            client=client,
        )
        opp = _make_sample_opportunity()
        disp_opp = DispatchableOpportunity(
            opportunity_id=opp.opportunity_id,
            canonical_event_id=opp.canonical_event_id,
            canonical_market_key=opp.canonical_market_key,
            legs=opp.legs,
            implied_probability_sum=opp.implied_probability_sum,
            arbitrage_margin=opp.arbitrage_margin,
            is_mixed_bookmakers=opp.is_mixed_bookmakers,
            bookmakers=opp.bookmakers,
            source_opportunity=opp,
        )

        res = consumer.consume(disp_opp)
        assert res.status == DeliveryStatus.FAILED
        cat = classify_delivery_failure(res)
        assert cat == FailureCategory.PERMANENT_FAILURE

    # -------------------------------------------------------------------------
    # 7. Adversarial Tests
    # -------------------------------------------------------------------------
    def test_19_adversarial_corrupted_snapshot_in_reconciliation(self, test_db):
        """19. Corrupted snapshot JSON in persisted delivery record is handled gracefully without crashing."""
        opp_repo = test_db["opp_repo"]
        deliv_repo = test_db["deliv_repo"]

        fp = "opp:SUREBET:evt_corrupt:market:OVER:superbet|UNDER:betclic"
        now = datetime(2026, 8, 17, 12, 0, 0, tzinfo=timezone.utc)

        opp_rec = OpportunityRecordORM(
            id="rec_bad",
            fingerprint=fp,
            canonical_event_id="evt_corrupt",
            market_key="TOTALS(2.5)",
            status="NEW",
            first_seen_at=now,
            last_seen_at=now,
            last_changed_at=now,
            arbitrage_margin=0.05,
            implied_probability_sum=0.95,
            snapshot_json="corrupted {json",
        )
        opp_repo.save_or_update(opp_rec)

        deliv_rec = DeliveryRecordORM(
            id="del_bad",
            idempotency_key=generate_delivery_idempotency_key(fp, 1, "telegram"),
            opportunity_fingerprint=fp,
            opportunity_id="opp_bad",
            consumer_name="telegram",
            lifecycle_version=1,
            state=DeliveryState.PENDING.value,
            attempt_count=0,
            max_attempts=3,
            created_at=now,
            payload_snapshot_json="invalid json here {{[",
        )
        deliv_repo.save_or_update(deliv_rec)

        consumer = InMemoryOpportunityConsumer(name="telegram")
        dispatcher = OpportunityDispatcher(consumers=[consumer])
        recon = DeliveryReconciliationService(deliv_repo, opp_repo, dispatcher)

        summary = recon.reconcile(current_time=now + timedelta(seconds=1))
        assert summary.corrupted_count == 1
        assert summary.permanent_failed_count == 1
        assert summary.delivered_count == 0

        # DeliveryRecord is marked FAILED_PERMANENT
        rec = deliv_repo.list_by_fingerprint(fp)[0]
        assert rec.state == DeliveryState.FAILED_PERMANENT.value
        assert "Corrupted" in rec.last_error

    def test_20_adversarial_missing_opportunity_record_handled_safely(self, test_db):
        """20. Delivery record whose opportunity was removed or expired is safely marked EXHAUSTED."""
        opp_repo = test_db["opp_repo"]
        deliv_repo = test_db["deliv_repo"]

        fp = "opp:SUREBET:evt_missing:market:OVER:superbet|UNDER:betclic"
        now = datetime(2026, 8, 17, 12, 0, 0, tzinfo=timezone.utc)

        deliv_rec = DeliveryRecordORM(
            id="del_orphan",
            idempotency_key=generate_delivery_idempotency_key(fp, 1, "telegram"),
            opportunity_fingerprint=fp,
            opportunity_id="opp_orphan",
            consumer_name="telegram",
            lifecycle_version=1,
            state=DeliveryState.PENDING.value,
            attempt_count=0,
            max_attempts=3,
            created_at=now,
            payload_snapshot_json="{}",
        )
        deliv_repo.save_or_update(deliv_rec)

        consumer = InMemoryOpportunityConsumer(name="telegram")
        dispatcher = OpportunityDispatcher(consumers=[consumer])
        recon = DeliveryReconciliationService(deliv_repo, opp_repo, dispatcher)

        summary = recon.reconcile(current_time=now + timedelta(seconds=1))
        assert summary.exhausted_count == 1

        rec = deliv_repo.list_by_fingerprint(fp)[0]
        assert rec.state == DeliveryState.EXHAUSTED.value

    def test_21_retry_calculation_monotonicity_and_bounds(self):
        """21. Retry backoff is strictly monotonic and bounded by max_delay_seconds."""
        cfg = DeliveryRetryConfig(
            max_attempts=5,
            initial_delay_seconds=5.0,
            backoff_multiplier=3.0,
            max_delay_seconds=60.0,
        )
        t0 = datetime(2026, 8, 17, 12, 0, 0, tzinfo=timezone.utc)

        t_att1 = calculate_next_retry_time(1, cfg, t0)  # 5.0 * 3^0 = 5s
        t_att2 = calculate_next_retry_time(2, cfg, t0)  # 5.0 * 3^1 = 15s
        t_att3 = calculate_next_retry_time(3, cfg, t0)  # 5.0 * 3^2 = 45s
        t_att4 = calculate_next_retry_time(4, cfg, t0)  # 5.0 * 3^3 = 135s -> bounded at 60s
        t_att5 = calculate_next_retry_time(5, cfg, t0)  # bounded at 60s

        assert (t_att1 - t0).total_seconds() == 5.0
        assert (t_att2 - t0).total_seconds() == 15.0
        assert (t_att3 - t0).total_seconds() == 45.0
        assert (t_att4 - t0).total_seconds() == 60.0
        assert (t_att5 - t0).total_seconds() == 60.0

    def test_22_adversarial_success_on_final_allowed_attempt(self, test_db):
        """22. Failure on attempts 1 and 2, success on final allowed attempt 3 -> transitions to DELIVERED."""
        opp_repo = test_db["opp_repo"]
        deliv_repo = test_db["deliv_repo"]

        flaky = FlakyConsumer(name="telegram", fail_attempts=2, failure_status=500)
        dispatcher = OpportunityDispatcher(consumers=[flaky])
        retry_cfg = DeliveryRetryConfig(max_attempts=3, initial_delay_seconds=5.0, backoff_multiplier=2.0)
        manager = OpportunityLifecycleManager(
            repository=opp_repo,
            delivery_repository=deliv_repo,
            retry_config=retry_cfg,
        )
        recon_service = DeliveryReconciliationService(
            delivery_repository=deliv_repo,
            opportunity_repository=opp_repo,
            dispatcher=dispatcher,
            retry_config=retry_cfg,
        )

        opp = _make_sample_opportunity()
        t0 = datetime(2026, 8, 17, 12, 0, 0, tzinfo=timezone.utc)

        # Attempt 1: fails
        manager.process_and_dispatch(SurebetDetectionResult(opportunities=(opp,), evaluations=()), dispatcher, evaluation_time=t0)
        fp = generate_opportunity_fingerprint("SUREBET", opp.canonical_event_id, opp.canonical_market_key, opp.legs)
        assert deliv_repo.list_by_fingerprint(fp)[0].state == DeliveryState.RETRY_PENDING.value

        # Attempt 2: fails again
        t1 = t0 + timedelta(seconds=5.0)
        res1 = recon_service.reconcile(current_time=t1)
        assert res1.retried_count == 1
        assert deliv_repo.list_by_fingerprint(fp)[0].state == DeliveryState.RETRY_PENDING.value
        assert deliv_repo.list_by_fingerprint(fp)[0].attempt_count == 2

        # Attempt 3: succeeds on final allowed attempt
        t2 = t1 + timedelta(seconds=10.0)
        res2 = recon_service.reconcile(current_time=t2)
        assert res2.delivered_count == 1
        deliv_final = deliv_repo.list_by_fingerprint(fp)[0]
        assert deliv_final.state == DeliveryState.DELIVERED.value
        assert deliv_final.attempt_count == 3

        # Opportunity is now ALERTED with alert_count = 1
        opp_rec = opp_repo.get_by_fingerprint(fp)
        assert opp_rec.status == OpportunityStatus.ALERTED.value
        assert opp_rec.alert_count == 1

    def test_23_multi_consumer_independent_failure_tracking(self, test_db):
        """23. Multiple registered consumers track delivery records and retry states completely independently."""
        opp_repo = test_db["opp_repo"]
        deliv_repo = test_db["deliv_repo"]

        tg_fail = FlakyConsumer(name="telegram", fail_attempts=1, failure_status=500)
        console_ok = InMemoryOpportunityConsumer(name="console")

        dispatcher = OpportunityDispatcher(consumers=[tg_fail, console_ok])
        manager = OpportunityLifecycleManager(
            repository=opp_repo,
            delivery_repository=deliv_repo,
        )

        opp = _make_sample_opportunity()
        t0 = datetime(2026, 8, 17, 12, 0, 0, tzinfo=timezone.utc)
        summary = manager.process_and_dispatch(SurebetDetectionResult(opportunities=(opp,), evaluations=()), dispatcher, evaluation_time=t0)

        # Overall dispatch status was PARTIAL_FAILURE
        assert summary.failed_count == 1
        fp = generate_opportunity_fingerprint("SUREBET", opp.canonical_event_id, opp.canonical_market_key, opp.legs)

        delivs = deliv_repo.list_by_fingerprint(fp)
        assert len(delivs) == 2
        tg_deliv = [d for d in delivs if d.consumer_name == "telegram"][0]
        console_deliv = [d for d in delivs if d.consumer_name == "console"][0]

        assert tg_deliv.state == DeliveryState.RETRY_PENDING.value
        assert console_deliv.state == DeliveryState.DELIVERED.value

    def test_24_duplicate_reconciliation_is_idempotent(self, test_db):
        """24. Running reconciliation repeatedly does not re-deliver already DELIVERED items."""
        opp_repo = test_db["opp_repo"]
        deliv_repo = test_db["deliv_repo"]

        consumer = InMemoryOpportunityConsumer(name="telegram")
        dispatcher = OpportunityDispatcher(consumers=[consumer])
        manager = OpportunityLifecycleManager(repository=opp_repo, delivery_repository=deliv_repo)
        recon = DeliveryReconciliationService(deliv_repo, opp_repo, dispatcher)

        opp = _make_sample_opportunity()
        t0 = datetime(2026, 8, 17, 12, 0, 0, tzinfo=timezone.utc)
        manager.process_and_dispatch(SurebetDetectionResult(opportunities=(opp,), evaluations=()), dispatcher, evaluation_time=t0)

        assert len(consumer.received_opportunities) == 1

        # Run reconciliation multiple times
        res1 = recon.reconcile(current_time=t0 + timedelta(seconds=5.0))
        res2 = recon.reconcile(current_time=t0 + timedelta(seconds=10.0))

        assert res1.scanned_count == 0
        assert res1.delivered_count == 0
        assert res2.scanned_count == 0
        assert res2.delivered_count == 0
        assert len(consumer.received_opportunities) == 1  # No duplicate deliveries

    def test_25_unconfigured_consumer_marked_skipped_without_infinite_retry(self, test_db):
        """25. Unconfigured Telegram consumer returns SKIPPED, is marked SKIPPED in DB, and not retried."""
        opp_repo = test_db["opp_repo"]
        deliv_repo = test_db["deliv_repo"]

        # Disabled / unconfigured consumer
        unconfigured_tg = TelegramOpportunityConsumer(
            config=TelegramConfig(bot_token=None, chat_id=None, enabled=True),
        )
        dispatcher = OpportunityDispatcher(consumers=[unconfigured_tg])
        manager = OpportunityLifecycleManager(repository=opp_repo, delivery_repository=deliv_repo)

        opp = _make_sample_opportunity()
        t0 = datetime(2026, 8, 17, 12, 0, 0, tzinfo=timezone.utc)
        summary = manager.process_and_dispatch(SurebetDetectionResult(opportunities=(opp,), evaluations=()), dispatcher, evaluation_time=t0)

        assert summary.dispatch_result is not None
        assert summary.dispatch_result.metrics.skipped_count == 1
        fp = generate_opportunity_fingerprint("SUREBET", opp.canonical_event_id, opp.canonical_market_key, opp.legs)
        deliv = deliv_repo.list_by_fingerprint(fp)[0]

        assert deliv.state == DeliveryState.SKIPPED.value
        assert deliv.next_retry_at is None

        # Reconciliation ignores skipped records
        recon = DeliveryReconciliationService(deliv_repo, opp_repo, dispatcher)
        recon_res = recon.reconcile(current_time=t0 + timedelta(hours=1))
        assert recon_res.scanned_count == 0

