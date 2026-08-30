"""
Stage 6.3: Comprehensive Unit, Integration, and Adversarial Test Suite
for Opportunity Lifecycle & Persistent Deduplication.

Tests verify:
- Deterministic fingerprint formula and invariance under pricing changes
- Adversarial fingerprint distinctions (lines, markets, events, selections, bookmakers)
- Leg ordering independence (permutation invariance)
- Lifecycle state transitions (NEW -> ALERTED -> UPDATED -> EXPIRED)
- Persistence and process restart recovery across sessions
- Exact duplicate suppression across scan cycles
- Delivery failure safety (failed dispatch never falsely marks ALERTED)
- Conservative expiration (scanner failure immunity vs confirmed absence expiration)
- Resurrected opportunity handling
- Full end-to-end integration with OpportunityDispatcher and TelegramOpportunityConsumer
"""

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional
import pytest

from database.connection import DatabaseManager
from database.config import DatabaseConfig
from database.repositories.opportunity_repository import OpportunityRepository
from domain.models import MatchEvidence
from normalization.dispatcher import (
    DeliveryStatus,
    DispatchResult,
    DispatchStatus,
    InMemoryOpportunityConsumer,
    OpportunityConsumer,
    OpportunityDispatcher,
)
from normalization.lifecycle import (
    LifecycleAction,
    LifecycleDispatchSummary,
    OpportunityLifecycleManager,
    OpportunityStatus,
    generate_opportunity_fingerprint,
)
from normalization.market_identity import (
    CanonicalMarketKey,
    CanonicalMarketType,
    MarketPeriod,
    MarketScope,
)
from normalization.selection_identity import (
    CanonicalSelectionKey,
    CanonicalSelectionType,
)
from normalization.surebet import (
    MarketCompletenessStatus,
    MarketSurebetEvaluation,
    SurebetDetectionMetrics,
    SurebetDetectionResult,
    SurebetLeg,
    SurebetOpportunity,
    SurebetStatus,
)
from notifications.telegram_client import FakeTelegramClient
from notifications.telegram_consumer import TelegramConfig, TelegramOpportunityConsumer


# ---------------------------------------------------------------------------
# Test Fixtures & Helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def db_manager():
    """Provides an isolated in-memory SQLite database manager for each test."""
    config = DatabaseConfig.default_sqlite_in_memory()
    manager = DatabaseManager(config)
    manager.create_tables()
    return manager


@pytest.fixture
def opp_repo(db_manager):
    """Provides an OpportunityRepository backed by the isolated in-memory DB."""
    session = db_manager.get_session()
    repo = OpportunityRepository(session)
    yield repo
    session.close()


@pytest.fixture
def lifecycle_mgr(opp_repo):
    """Provides an OpportunityLifecycleManager instance."""
    return OpportunityLifecycleManager(opp_repo)


def build_1x2_opportunity(
    event_id: str = "evt_lifecycle_001",
    home_odds: Decimal = Decimal("2.10"),
    draw_odds: Decimal = Decimal("3.60"),
    away_odds: Decimal = Decimal("4.20"),
    home_bookmaker: str = "superbet",
    draw_bookmaker: str = "superbet",
    away_bookmaker: str = "betclic",
    opp_id: Optional[str] = None,
) -> SurebetOpportunity:
    """Helper to construct a valid 1X2 SurebetOpportunity."""
    actual_opp_id = opp_id or f"opp_{event_id}"
    mkt_key = CanonicalMarketKey(

        market_type=CanonicalMarketType.ONE_X_TWO.value,
        period=MarketPeriod.FULL_TIME.value,
        scope=MarketScope.MATCH.value,
    )
    home_key = CanonicalSelectionKey(
        market_key=mkt_key,
        selection_type=CanonicalSelectionType.HOME.value,
        participant_role="HOME",
    )
    draw_key = CanonicalSelectionKey(
        market_key=mkt_key,
        selection_type=CanonicalSelectionType.DRAW.value,
    )
    away_key = CanonicalSelectionKey(
        market_key=mkt_key,
        selection_type=CanonicalSelectionType.AWAY.value,
        participant_role="AWAY",
    )

    leg_h = SurebetLeg(
        canonical_selection_key=home_key,
        selection_type="HOME",
        provider=home_bookmaker,
        odds=home_odds,
        source_selection_id=f"{home_bookmaker}_h",
        implied_probability=Decimal("1.0") / home_odds,
    )
    leg_d = SurebetLeg(
        canonical_selection_key=draw_key,
        selection_type="DRAW",
        provider=draw_bookmaker,
        odds=draw_odds,
        source_selection_id=f"{draw_bookmaker}_d",
        implied_probability=Decimal("1.0") / draw_odds,
    )
    leg_a = SurebetLeg(
        canonical_selection_key=away_key,
        selection_type="AWAY",
        provider=away_bookmaker,
        odds=away_odds,
        source_selection_id=f"{away_bookmaker}_a",
        implied_probability=Decimal("1.0") / away_odds,
    )


    legs = (leg_h, leg_d, leg_a)
    s = sum(l.implied_probability for l in legs)
    margin = (Decimal("1.0") / s) - Decimal("1.0")

    books = tuple(sorted(set([home_bookmaker, draw_bookmaker, away_bookmaker])))

    return SurebetOpportunity(
        opportunity_id=actual_opp_id,
        canonical_event_id=event_id,
        canonical_market_key=mkt_key,

        legs=legs,
        implied_probability_sum=s,
        arbitrage_margin=margin,
        status=SurebetStatus.SUREBET,
        is_mixed_bookmakers=len(books) > 1,
        bookmakers=books,
        event_evidence=MatchEvidence(
            source_provider="superbet",
            target_provider="betclic",
            source_event_id="sb_e1",
            target_event_id="bc_e1",
            decision="MATCH",
            total_score=0.95,
            orientation="DIRECT",
            evidence={
                "home_team": "Arsenal",
                "away_team": "Chelsea",
                "competition_name": "Premier League",
            },
        ),
    )



def build_totals_opportunity(
    event_id: str = "evt_totals_001",
    line: Decimal = Decimal("2.5"),
    over_odds: Decimal = Decimal("2.05"),
    under_odds: Decimal = Decimal("2.10"),
    over_bookmaker: str = "superbet",
    under_bookmaker: str = "betclic",
    opp_id: str = "opp_tot_001",
) -> SurebetOpportunity:
    """Helper to construct a valid Totals (Over/Under) SurebetOpportunity."""
    mkt_key = CanonicalMarketKey(
        market_type=CanonicalMarketType.TOTALS.value,
        line=line,
        period=MarketPeriod.FULL_TIME.value,
        scope=MarketScope.MATCH.value,
    )
    over_key = CanonicalSelectionKey(
        market_key=mkt_key,
        selection_type=CanonicalSelectionType.OVER.value,
    )
    under_key = CanonicalSelectionKey(
        market_key=mkt_key,
        selection_type=CanonicalSelectionType.UNDER.value,
    )

    leg_o = SurebetLeg(
        canonical_selection_key=over_key,
        selection_type="OVER",
        provider=over_bookmaker,
        odds=over_odds,
        source_selection_id=f"{over_bookmaker}_over",
        implied_probability=Decimal("1.0") / over_odds,
    )
    leg_u = SurebetLeg(
        canonical_selection_key=under_key,
        selection_type="UNDER",
        provider=under_bookmaker,
        odds=under_odds,
        source_selection_id=f"{under_bookmaker}_under",
        implied_probability=Decimal("1.0") / under_odds,
    )

    legs = (leg_o, leg_u)
    s = sum(l.implied_probability for l in legs)
    margin = (Decimal("1.0") / s) - Decimal("1.0")
    books = tuple(sorted(set([over_bookmaker, under_bookmaker])))

    return SurebetOpportunity(
        opportunity_id=opp_id,
        canonical_event_id=event_id,
        canonical_market_key=mkt_key,
        legs=legs,
        implied_probability_sum=s,
        arbitrage_margin=margin,
        status=SurebetStatus.SUREBET,
        is_mixed_bookmakers=len(books) > 1,
        bookmakers=books,
    )


# ---------------------------------------------------------------------------
# Section 1: Fingerprint Determinism & Adversarial Testing
# ---------------------------------------------------------------------------

class TestOpportunityFingerprint:
    """Adversarial and property tests for opportunity identity / fingerprinting."""

    def test_fingerprint_stability_across_tiny_odds_change(self):
        """Tiny odds change produces the exact same fingerprint."""
        opp1 = build_1x2_opportunity(home_odds=Decimal("2.1000"))
        opp2 = build_1x2_opportunity(home_odds=Decimal("2.1005"))

        fp1 = generate_opportunity_fingerprint("SUREBET", opp1.canonical_event_id, opp1.canonical_market_key, opp1.legs)
        fp2 = generate_opportunity_fingerprint("SUREBET", opp2.canonical_event_id, opp2.canonical_market_key, opp2.legs)

        assert fp1 == fp2

    def test_fingerprint_stability_across_large_odds_change(self):
        """Large odds shift on multiple legs still produces identical fingerprint."""
        opp1 = build_1x2_opportunity(home_odds=Decimal("2.10"), draw_odds=Decimal("3.60"), away_odds=Decimal("4.20"))
        opp2 = build_1x2_opportunity(home_odds=Decimal("2.50"), draw_odds=Decimal("3.80"), away_odds=Decimal("4.50"))

        fp1 = generate_opportunity_fingerprint("SUREBET", opp1.canonical_event_id, opp1.canonical_market_key, opp1.legs)
        fp2 = generate_opportunity_fingerprint("SUREBET", opp2.canonical_event_id, opp2.canonical_market_key, opp2.legs)

        assert fp1 == fp2

    def test_fingerprint_order_independence_of_legs(self):
        """Permuting the leg array order produces the exact same fingerprint."""
        opp1 = build_1x2_opportunity()
        # Create opp2 with permuted leg order [AWAY, HOME, DRAW]
        legs_permuted = (opp1.legs[2], opp1.legs[0], opp1.legs[1])
        opp2 = SurebetOpportunity(
            opportunity_id="opp_permuted",
            canonical_event_id=opp1.canonical_event_id,
            canonical_market_key=opp1.canonical_market_key,
            legs=legs_permuted,
            implied_probability_sum=opp1.implied_probability_sum,
            arbitrage_margin=opp1.arbitrage_margin,
            bookmakers=opp1.bookmakers,
        )

        fp1 = generate_opportunity_fingerprint("SUREBET", opp1.canonical_event_id, opp1.canonical_market_key, opp1.legs)
        fp2 = generate_opportunity_fingerprint("SUREBET", opp2.canonical_event_id, opp2.canonical_market_key, opp2.legs)

        assert fp1 == fp2

    def test_fingerprint_distinction_on_market_line_change(self):
        """Changing market line (Totals 2.5 vs 3.5) changes the fingerprint."""
        opp_2_5 = build_totals_opportunity(line=Decimal("2.5"))
        opp_3_5 = build_totals_opportunity(line=Decimal("3.5"))

        fp_2_5 = generate_opportunity_fingerprint("SUREBET", opp_2_5.canonical_event_id, opp_2_5.canonical_market_key, opp_2_5.legs)
        fp_3_5 = generate_opportunity_fingerprint("SUREBET", opp_3_5.canonical_event_id, opp_3_5.canonical_market_key, opp_3_5.legs)

        assert fp_2_5 != fp_3_5

    def test_fingerprint_distinction_on_bookmaker_change(self):
        """Changing the participating bookmaker for a leg changes the fingerprint."""
        opp_sb = build_1x2_opportunity(away_bookmaker="betclic")
        opp_sts = build_1x2_opportunity(away_bookmaker="sts")

        fp_sb = generate_opportunity_fingerprint("SUREBET", opp_sb.canonical_event_id, opp_sb.canonical_market_key, opp_sb.legs)
        fp_sts = generate_opportunity_fingerprint("SUREBET", opp_sts.canonical_event_id, opp_sts.canonical_market_key, opp_sts.legs)

        assert fp_sb != fp_sts

    def test_fingerprint_distinction_on_event_change(self):
        """Different canonical events have distinct fingerprints."""
        opp_e1 = build_1x2_opportunity(event_id="evt_001")
        opp_e2 = build_1x2_opportunity(event_id="evt_002")

        fp_e1 = generate_opportunity_fingerprint("SUREBET", opp_e1.canonical_event_id, opp_e1.canonical_market_key, opp_e1.legs)
        fp_e2 = generate_opportunity_fingerprint("SUREBET", opp_e2.canonical_event_id, opp_e2.canonical_market_key, opp_e2.legs)

        assert fp_e1 != fp_e2

    def test_fingerprint_distinction_on_market_type_change(self):
        """1X2 market vs BTTS on same event has distinct fingerprints."""
        opp_1x2 = build_1x2_opportunity(event_id="evt_same")
        opp_tot = build_totals_opportunity(event_id="evt_same")

        fp_1x2 = generate_opportunity_fingerprint("SUREBET", opp_1x2.canonical_event_id, opp_1x2.canonical_market_key, opp_1x2.legs)
        fp_tot = generate_opportunity_fingerprint("SUREBET", opp_tot.canonical_event_id, opp_tot.canonical_market_key, opp_tot.legs)

        assert fp_1x2 != fp_tot


# ---------------------------------------------------------------------------
# Section 2: Lifecycle Transitions & Deduplication Logic
# ---------------------------------------------------------------------------

class TestOpportunityLifecycleTransitions:
    """Tests for state transitions: NEW -> ALERTED -> UPDATED -> EXPIRED."""

    def test_first_observation_creates_new_opportunity(self, lifecycle_mgr, opp_repo):
        """First observation creates a record with status NEW and action DISPATCH_INITIAL."""
        opp = build_1x2_opportunity()
        eval_res = lifecycle_mgr.evaluate_opportunity(opp)

        assert eval_res.action == LifecycleAction.DISPATCH_INITIAL
        assert eval_res.previous_status is None
        assert eval_res.current_status == OpportunityStatus.NEW.value

        # Verify persisted in repository
        persisted = opp_repo.get_by_fingerprint(eval_res.fingerprint)
        assert persisted is not None
        assert persisted.status == "NEW"
        assert persisted.alert_count == 0

    def test_exact_duplicate_suppression_after_alerted(self, lifecycle_mgr, opp_repo):
        """Repeated scan with identical odds suppresses duplicate alerts."""
        opp = build_1x2_opportunity()
        eval_res1 = lifecycle_mgr.evaluate_opportunity(opp)
        assert eval_res1.action == LifecycleAction.DISPATCH_INITIAL

        # Simulate successful delivery
        opp_repo.record_delivery_result(eval_res1.fingerprint, success=True, delivery_status="DELIVERED")

        # Scan cycle 2: Exact same opportunity
        eval_res2 = lifecycle_mgr.evaluate_opportunity(opp)
        assert eval_res2.action == LifecycleAction.SUPPRESS_DUPLICATE
        assert eval_res2.current_status == OpportunityStatus.ALERTED.value
        assert eval_res2.odds_changed is False
        assert eval_res2.margin_changed is False

    def test_odds_update_triggers_updated_transition(self, lifecycle_mgr, opp_repo):
        """Scan with changed odds transitions from ALERTED to UPDATED."""
        opp1 = build_1x2_opportunity(home_odds=Decimal("2.10"))
        eval_res1 = lifecycle_mgr.evaluate_opportunity(opp1)
        opp_repo.record_delivery_result(eval_res1.fingerprint, success=True, delivery_status="DELIVERED")

        # Scan cycle 2: Odds improved on Home
        opp2 = build_1x2_opportunity(home_odds=Decimal("2.25"))
        eval_res2 = lifecycle_mgr.evaluate_opportunity(opp2)

        assert eval_res2.action == LifecycleAction.DISPATCH_UPDATE
        assert eval_res2.previous_status == OpportunityStatus.ALERTED.value
        assert eval_res2.current_status == OpportunityStatus.UPDATED.value
        assert eval_res2.odds_changed is True

        # Verify updated snapshot persisted
        persisted = opp_repo.get_by_fingerprint(eval_res2.fingerprint)
        assert persisted.status == "UPDATED"

    def test_batch_evaluation_filters_out_suppressed(self, lifecycle_mgr, opp_repo):
        """Batch evaluation puts only NEW and UPDATED into to_dispatch."""
        opp_new = build_1x2_opportunity(event_id="evt_new")
        opp_existing = build_1x2_opportunity(event_id="evt_existing")

        # Pre-seed opp_existing as ALERTED
        res = lifecycle_mgr.evaluate_opportunity(opp_existing)
        opp_repo.record_delivery_result(res.fingerprint, success=True, delivery_status="DELIVERED")

        # Batch evaluate both
        batch = lifecycle_mgr.evaluate_opportunities([opp_new, opp_existing])

        assert len(batch.evaluations) == 2
        assert len(batch.to_dispatch) == 1
        assert batch.to_dispatch[0].canonical_event_id == "evt_new"
        assert batch.new_count == 1
        assert batch.suppressed_count == 1
        assert batch.updated_count == 0


# ---------------------------------------------------------------------------
# Section 3: Process Restart & Persistence Resilience
# ---------------------------------------------------------------------------

class TestProcessRestartResilience:
    """Tests that lifecycle state persists across new manager/session instances."""

    def test_process_restart_recovers_state_and_suppresses_duplicates(self, db_manager):
        """Creating an opportunity in session 1 and restarting in session 2 preserves deduplication."""
        opp = build_1x2_opportunity()

        # Session 1: Process and alert opportunity
        with db_manager.get_session() as session1:
            repo1 = OpportunityRepository(session1)
            mgr1 = OpportunityLifecycleManager(repo1)
            res1 = mgr1.evaluate_opportunity(opp)
            repo1.record_delivery_result(res1.fingerprint, success=True, delivery_status="DELIVERED")
            session1.commit()

        # Simulate complete process restart (Session 2, new repository, new manager)
        with db_manager.get_session() as session2:
            repo2 = OpportunityRepository(session2)
            mgr2 = OpportunityLifecycleManager(repo2)

            # Observe the exact same opportunity again
            res2 = mgr2.evaluate_opportunity(opp)

            assert res2.action == LifecycleAction.SUPPRESS_DUPLICATE
            assert res2.current_status == OpportunityStatus.ALERTED.value

            # Persisted record matches
            persisted = repo2.get_by_fingerprint(res2.fingerprint)
            assert persisted is not None
            assert persisted.status == "ALERTED"
            assert persisted.alert_count == 1


# ---------------------------------------------------------------------------
# Section 4: Conservative Expiration & Failure Safety
# ---------------------------------------------------------------------------

class TestExpirationAndFailureSafety:
    """Tests expiration semantics and delivery failure safety."""

    def test_delivery_failure_never_marks_alerted(self, lifecycle_mgr, opp_repo):
        """Failed delivery keeps opportunity status as NEW (or UPDATED) without marking ALERTED."""
        opp = build_1x2_opportunity()
        eval_res = lifecycle_mgr.evaluate_opportunity(opp)

        # Simulate delivery failure
        opp_repo.record_delivery_result(eval_res.fingerprint, success=False, delivery_status="FAILED")

        persisted = opp_repo.get_by_fingerprint(eval_res.fingerprint)
        assert persisted.status == "NEW"  # NOT ALERTED!
        assert persisted.delivery_status == "FAILED"
        assert persisted.last_alerted_at is None
        assert persisted.alert_count == 0

        # In next cycle, it is still eligible for dispatch
        eval_res2 = lifecycle_mgr.evaluate_opportunity(opp)
        assert eval_res2.action == LifecycleAction.DISPATCH_INITIAL

    def test_scanner_exception_does_not_expire_opportunity(self, lifecycle_mgr, opp_repo):
        """When a scanner run fails or encounters exceptions, opportunities are NOT expired."""
        opp = build_1x2_opportunity()
        eval_res = lifecycle_mgr.evaluate_opportunity(opp)
        opp_repo.record_delivery_result(eval_res.fingerprint, success=True, delivery_status="DELIVERED")

        # Suppose scanner crashes on the event (no evaluations returned for that market)
        # Calling process_market_evaluations with empty or unrelated evaluations
        expired = lifecycle_mgr.process_market_evaluations([])
        assert len(expired) == 0

        persisted = opp_repo.get_by_fingerprint(eval_res.fingerprint)
        assert persisted.status == "ALERTED"
        assert persisted.consecutive_misses == 0

    def test_confirmed_market_absence_expires_opportunity(self, lifecycle_mgr, opp_repo):
        """Consecutive successful scans where market has no surebet expires the opportunity."""
        opp = build_1x2_opportunity(event_id="evt_expire")
        eval_res = lifecycle_mgr.evaluate_opportunity(opp)
        opp_repo.record_delivery_result(eval_res.fingerprint, success=True, delivery_status="DELIVERED")

        # Successful scan evaluates the same market, but finds NO_SUREBET (arbitrage margin collapsed)
        no_sb_eval = MarketSurebetEvaluation(
            canonical_event_id="evt_expire",
            canonical_market_key=opp.canonical_market_key,
            status=SurebetStatus.NO_SUREBET,
            completeness_status=MarketCompletenessStatus.COMPLETE,
            required_selection_types=("HOME", "DRAW", "AWAY"),
            available_selection_types=("HOME", "DRAW", "AWAY"),
            missing_selection_types=(),
            best_legs=(),
        )

        # First successful absence scan: consecutive_misses becomes 1 (threshold is 2)
        exp1 = lifecycle_mgr.process_market_evaluations([no_sb_eval], max_misses=2)
        assert len(exp1) == 0
        persisted1 = opp_repo.get_by_fingerprint(eval_res.fingerprint)
        assert persisted1.status == "ALERTED"
        assert persisted1.consecutive_misses == 1

        # Second successful absence scan: consecutive_misses becomes 2 -> Transitions to EXPIRED!
        exp2 = lifecycle_mgr.process_market_evaluations([no_sb_eval], max_misses=2)
        assert len(exp2) == 1
        persisted2 = opp_repo.get_by_fingerprint(eval_res.fingerprint)
        assert persisted2.status == "EXPIRED"
        assert persisted2.expired_at is not None

    def test_resurrection_after_expiration(self, lifecycle_mgr, opp_repo):
        """Expired opportunity re-detected later is resurrected as NEW and dispatched."""
        opp = build_1x2_opportunity(event_id="evt_resurrect")
        eval_res = lifecycle_mgr.evaluate_opportunity(opp)
        opp_repo.record_delivery_result(eval_res.fingerprint, success=True, delivery_status="DELIVERED")

        # Expire it manually in DB
        record = opp_repo.get_by_fingerprint(eval_res.fingerprint)
        record.status = "EXPIRED"
        opp_repo.save_or_update(record)

        # Re-detect opportunity in next cycle
        eval_res_resurrect = lifecycle_mgr.evaluate_opportunity(opp)
        assert eval_res_resurrect.action == LifecycleAction.DISPATCH_INITIAL
        assert eval_res_resurrect.previous_status == OpportunityStatus.EXPIRED.value
        assert eval_res_resurrect.current_status == OpportunityStatus.NEW.value


# ---------------------------------------------------------------------------
# Section 5: Full Pipeline Integration with Dispatcher & Telegram Consumer
# ---------------------------------------------------------------------------

class TestFullLifecyclePipelineIntegration:
    """Full pipeline test with SurebetDetectionResult, OpportunityDispatcher, and TelegramOpportunityConsumer."""

    def test_full_pipeline_happy_path(self, lifecycle_mgr, opp_repo):
        """Full pipeline evaluates detection result, dispatches via dispatcher, delivers to Telegram, and updates DB."""
        opp = build_1x2_opportunity(event_id="evt_full_001")

        # 1. Setup Fake Telegram Consumer & Dispatcher
        fake_client = FakeTelegramClient()
        config = TelegramConfig(bot_token="test_token", chat_id="123456", enabled=True)
        telegram_consumer = TelegramOpportunityConsumer(config=config, client=fake_client)

        dispatcher = OpportunityDispatcher([telegram_consumer])

        # 2. Build SurebetDetectionResult with 1 opportunity and 1 evaluation
        eval_mkt = MarketSurebetEvaluation(
            canonical_event_id=opp.canonical_event_id,
            canonical_market_key=opp.canonical_market_key,
            status=SurebetStatus.SUREBET,
            completeness_status=MarketCompletenessStatus.COMPLETE,
            required_selection_types=("HOME", "DRAW", "AWAY"),
            available_selection_types=("HOME", "DRAW", "AWAY"),
            missing_selection_types=(),
            best_legs=opp.legs,
            opportunity=opp,
        )
        detection_result = SurebetDetectionResult(
            opportunities=[opp],
            evaluations=[eval_mkt],
            metrics=SurebetDetectionMetrics(surebet_count=1),
        )

        # 3. Process Cycle 1: Brand NEW opportunity
        summary1 = lifecycle_mgr.process_and_dispatch(detection_result, dispatcher)

        assert summary1.delivered_count == 1
        assert summary1.failed_count == 0
        assert len(fake_client.sent_messages) == 1

        # Check DB status is ALERTED
        fp = generate_opportunity_fingerprint("SUREBET", opp.canonical_event_id, opp.canonical_market_key, opp.legs)
        persisted1 = opp_repo.get_by_fingerprint(fp)
        assert persisted1.status == "ALERTED"
        assert persisted1.delivery_status == "DELIVERED"
        assert persisted1.alert_count == 1

        # 4. Process Cycle 2: Exact same detection result -> Should suppress duplicate Telegram message
        summary2 = lifecycle_mgr.process_and_dispatch(detection_result, dispatcher)

        assert summary2.delivered_count == 0
        assert summary2.evaluation_batch.suppressed_count == 1
        assert len(fake_client.sent_messages) == 1  # No new message sent!

        # 5. Process Cycle 3: Odds changed -> Dispatches update alert
        opp_updated = build_1x2_opportunity(event_id="evt_full_001", home_odds=Decimal("2.30"))
        eval_mkt_updated = MarketSurebetEvaluation(
            canonical_event_id=opp_updated.canonical_event_id,
            canonical_market_key=opp_updated.canonical_market_key,
            status=SurebetStatus.SUREBET,
            completeness_status=MarketCompletenessStatus.COMPLETE,
            required_selection_types=("HOME", "DRAW", "AWAY"),
            available_selection_types=("HOME", "DRAW", "AWAY"),
            missing_selection_types=(),
            best_legs=opp_updated.legs,
            opportunity=opp_updated,
        )
        detection_result_updated = SurebetDetectionResult(
            opportunities=[opp_updated],
            evaluations=[eval_mkt_updated],
            metrics=SurebetDetectionMetrics(surebet_count=1),
        )

        summary3 = lifecycle_mgr.process_and_dispatch(detection_result_updated, dispatcher)

        assert summary3.delivered_count == 1
        assert summary3.evaluation_batch.updated_count == 1
        assert len(fake_client.sent_messages) == 2  # Update message sent!

        persisted3 = opp_repo.get_by_fingerprint(fp)
        assert persisted3.status == "ALERTED"
        assert persisted3.alert_count == 2


# ---------------------------------------------------------------------------
# Section 6: Stage 6.4 Alert Policy & Change Significance Integration
# ---------------------------------------------------------------------------

class TestOpportunityLifecycleAlertPolicyIntegration:
    """Integration tests combining OpportunityLifecycleManager with OpportunityAlertPolicy."""

    def test_insignificant_price_movement_updates_persisted_snapshot_without_dispatch(
        self,
        lifecycle_mgr,
        opp_repo,
    ):
        """Insignificant odds movement updates the persisted DB snapshot but suppresses dispatch."""
        opp1 = build_1x2_opportunity(home_odds=Decimal("2.10"))
        eval_res1 = lifecycle_mgr.evaluate_opportunity(opp1)
        opp_repo.record_delivery_result(eval_res1.fingerprint, success=True, delivery_status="DELIVERED")

        # Scan cycle 2: Minor odds jitter (2.10 -> 2.11, ~0.48% relative change)
        opp2 = build_1x2_opportunity(home_odds=Decimal("2.11"))
        eval_res2 = lifecycle_mgr.evaluate_opportunity(opp2)

        assert eval_res2.action == LifecycleAction.SUPPRESS_INSIGNIFICANT
        assert eval_res2.current_status == OpportunityStatus.ALERTED.value
        assert eval_res2.change_evaluation is not None
        assert eval_res2.change_evaluation.is_material is False

        # Invariant check: DB record must have new snapshot and new margin, but alert_count unchanged
        persisted = opp_repo.get_by_fingerprint(eval_res2.fingerprint)
        assert persisted.status == "ALERTED"
        assert persisted.alert_count == 1
        assert "2.11" in persisted.snapshot_json

    def test_chained_insignificant_updates_prevent_cumulative_drift_alert(
        self,
        lifecycle_mgr,
        opp_repo,
    ):
        """Stepwise minor fluctuations (A->B->C->D) each update the snapshot without false alerts."""
        opp_a = build_1x2_opportunity(home_odds=Decimal("2.100"))
        res_a = lifecycle_mgr.evaluate_opportunity(opp_a)
        opp_repo.record_delivery_result(res_a.fingerprint, success=True, delivery_status="DELIVERED")

        odds_sequence = [
            Decimal("2.108"),  # +0.38% rel change -> Insignificant
            Decimal("2.116"),  # +0.38% rel change -> Insignificant
            Decimal("2.124"),  # +0.38% rel change -> Insignificant
            Decimal("2.132"),  # +0.38% rel change -> Insignificant
        ]

        for odds_val in odds_sequence:
            opp_step = build_1x2_opportunity(home_odds=odds_val)
            res_step = lifecycle_mgr.evaluate_opportunity(opp_step)
            assert res_step.action == LifecycleAction.SUPPRESS_INSIGNIFICANT

            # Verify snapshot updated to current step
            persisted = opp_repo.get_by_fingerprint(res_a.fingerprint)
            assert str(odds_val) in persisted.snapshot_json
            assert persisted.alert_count == 1

    def test_material_update_after_noise_sequence_triggers_alert(
        self,
        lifecycle_mgr,
        opp_repo,
    ):
        """Material change following several noise cycles correctly triggers an alert."""
        opp_init = build_1x2_opportunity(home_odds=Decimal("2.10"))
        res_init = lifecycle_mgr.evaluate_opportunity(opp_init)
        opp_repo.record_delivery_result(res_init.fingerprint, success=True, delivery_status="DELIVERED")

        # 3 cycles of noise
        for noise_odds in [Decimal("2.11"), Decimal("2.10"), Decimal("2.12")]:
            opp_noise = build_1x2_opportunity(home_odds=noise_odds)
            res_noise = lifecycle_mgr.evaluate_opportunity(opp_noise)
            assert res_noise.action == LifecycleAction.SUPPRESS_INSIGNIFICANT

        # Cycle 5: Material jump to 2.35 (+10.8% relative change)
        opp_material = build_1x2_opportunity(home_odds=Decimal("2.35"))
        res_mat = lifecycle_mgr.evaluate_opportunity(opp_material)

        assert res_mat.action == LifecycleAction.DISPATCH_UPDATE
        assert res_mat.current_status == OpportunityStatus.UPDATED.value
        assert res_mat.change_evaluation is not None
        assert res_mat.change_evaluation.is_material is True

    def test_end_to_end_simulation_noise_reduction_with_telegram(
        self,
        lifecycle_mgr,
        opp_repo,
    ):
        """Simulates 10 scanner cycles verifying Telegram spam prevention."""
        fake_client = FakeTelegramClient()
        config = TelegramConfig(bot_token="test_tok", chat_id="123", enabled=True)
        telegram_consumer = TelegramOpportunityConsumer(config=config, client=fake_client)
        dispatcher = OpportunityDispatcher([telegram_consumer])

        cycle_odds = [
            Decimal("2.10"),   # Cycle 1: Initial alert -> Sent (msg count: 1)
            Decimal("2.11"),   # Cycle 2: Noise -> Suppressed (msg count: 1)
            Decimal("2.10"),   # Cycle 3: Noise -> Suppressed (msg count: 1)
            Decimal("2.115"),  # Cycle 4: Noise -> Suppressed (msg count: 1)
            Decimal("2.12"),   # Cycle 5: Noise -> Suppressed (msg count: 1)
            Decimal("2.11"),   # Cycle 6: Noise -> Suppressed (msg count: 1)
            Decimal("2.35"),   # Cycle 7: Material change -> Alert sent (msg count: 2)
            Decimal("2.36"),   # Cycle 8: Noise -> Suppressed (msg count: 2)
            Decimal("2.35"),   # Cycle 9: Noise -> Suppressed (msg count: 2)
            Decimal("2.35"),   # Cycle 10: Exact duplicate -> Suppressed (msg count: 2)
        ]

        for cycle_idx, odds in enumerate(cycle_odds, start=1):
            opp = build_1x2_opportunity(event_id="evt_sim_001", home_odds=odds)
            eval_mkt = MarketSurebetEvaluation(
                canonical_event_id=opp.canonical_event_id,
                canonical_market_key=opp.canonical_market_key,
                status=SurebetStatus.SUREBET,
                completeness_status=MarketCompletenessStatus.COMPLETE,
                required_selection_types=("HOME", "DRAW", "AWAY"),
                available_selection_types=("HOME", "DRAW", "AWAY"),
                missing_selection_types=(),
                best_legs=opp.legs,
                opportunity=opp,
            )
            detection_res = SurebetDetectionResult(
                opportunities=[opp],
                evaluations=[eval_mkt],
                metrics=SurebetDetectionMetrics(surebet_count=1),
            )

            summary = lifecycle_mgr.process_and_dispatch(detection_res, dispatcher)

            if cycle_idx == 1:
                assert summary.delivered_count == 1
                assert len(fake_client.sent_messages) == 1
            elif cycle_idx == 7:
                assert summary.delivered_count == 1
                assert len(fake_client.sent_messages) == 2
            else:
                assert summary.delivered_count == 0

        # Across 10 cycles, exactly 2 Telegram messages were sent (80% noise suppression)
        assert len(fake_client.sent_messages) == 2

