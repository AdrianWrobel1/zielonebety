"""
Unit and Integration Tests for Stage 6.1: Opportunity Dispatcher & Delivery Boundary

Covers complete Test Matrix (A through T), Performance Benchmarks, Edge Cases,
and Architectural Invariants.
"""

import time
from decimal import Decimal
from typing import Any, Dict, List, Optional
import pytest

from normalization.dispatcher import (
    BatchDispatchResult,
    ConsoleOpportunityConsumer,
    ConsumerDeliveryResult,
    DeliveryStatus,
    DispatchMetrics,
    DispatchResult,
    DispatchStatus,
    DispatchableOpportunity,
    InMemoryOpportunityConsumer,
    OpportunityConsumer,
    OpportunityDispatcher,
    validate_dispatchable_opportunity,
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
    SurebetDetectionMetrics,
    SurebetDetectionResult,
    SurebetLeg,
    SurebetOpportunity,
    SurebetStatus,
)


# ---------------------------------------------------------------------------
# Test Helpers and Fixtures
# ---------------------------------------------------------------------------

def create_valid_1x2_opportunity(
    event_id: str = "evt_dispatch_001",
    home_odds: Decimal = Decimal("2.15"),
    draw_odds: Decimal = Decimal("3.80"),
    away_odds: Decimal = Decimal("4.20"),
    opp_id: Optional[str] = None,
) -> SurebetOpportunity:
    """Helper to construct a mathematically valid 1X2 SurebetOpportunity."""
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

    leg_home = SurebetLeg(
        canonical_selection_key=home_key,
        selection_type="HOME",
        provider="superbet",
        odds=home_odds,
        source_selection_id="sb_sel_1",
        source_event_id="sb_evt_1",
        source_market_id="sb_mkt_1",
        implied_probability=Decimal("1.0") / home_odds,
    )
    leg_draw = SurebetLeg(
        canonical_selection_key=draw_key,
        selection_type="DRAW",
        provider="betclic",
        odds=draw_odds,
        source_selection_id="bc_sel_X",
        source_event_id="bc_evt_1",
        source_market_id="bc_mkt_1",
        implied_probability=Decimal("1.0") / draw_odds,
    )
    leg_away = SurebetLeg(
        canonical_selection_key=away_key,
        selection_type="AWAY",
        provider="superbet",
        odds=away_odds,
        source_selection_id="sb_sel_2",
        source_event_id="sb_evt_1",
        source_market_id="sb_mkt_1",
        implied_probability=Decimal("1.0") / away_odds,
    )

    legs = (leg_home, leg_draw, leg_away)
    s = sum(leg.implied_probability for leg in legs)
    margin = (Decimal("1.0") / s) - Decimal("1.0")

    computed_id = opp_id or f"sb:{event_id}:{mkt_key.to_key_string()}:HOME:superbet:sb_sel_1_{home_odds}_DRAW:betclic:bc_sel_X_{draw_odds}_AWAY:superbet:sb_sel_2_{away_odds}"

    return SurebetOpportunity(
        opportunity_id=computed_id,
        canonical_event_id=event_id,
        canonical_market_key=mkt_key,
        legs=legs,
        implied_probability_sum=s,
        arbitrage_margin=margin,
        status=SurebetStatus.SUREBET,
        is_mixed_bookmakers=True,
        bookmakers=("betclic", "superbet"),
    )


class FailingOpportunityConsumer:
    """Mock consumer that raises an exception during consumption."""
    def __init__(self, name: str = "failing_consumer", exception_to_raise: Exception = RuntimeError("Downstream API timeout")):
        self._name = name
        self._exc = exception_to_raise
        self.invocation_count = 0

    @property
    def name(self) -> str:
        return self._name

    def consume(self, opportunity: DispatchableOpportunity) -> ConsumerDeliveryResult:
        self.invocation_count += 1
        raise self._exc


class ErrorReturningOpportunityConsumer:
    """Mock consumer that returns FAILED status without raising."""
    def __init__(self, name: str = "error_returning_consumer", error_msg: str = "Rate limit exceeded"):
        self._name = name
        self._error_msg = error_msg
        self.invocation_count = 0

    @property
    def name(self) -> str:
        return self._name

    def consume(self, opportunity: DispatchableOpportunity) -> ConsumerDeliveryResult:
        self.invocation_count += 1
        return ConsumerDeliveryResult(
            consumer_name=self.name,
            status=DeliveryStatus.FAILED,
            error=self._error_msg,
        )


class SkippedOpportunityConsumer:
    """Mock consumer that intentionally skips opportunities."""
    def __init__(self, name: str = "skipped_consumer"):
        self._name = name
        self.invocation_count = 0

    @property
    def name(self) -> str:
        return self._name

    def consume(self, opportunity: DispatchableOpportunity) -> ConsumerDeliveryResult:
        self.invocation_count += 1
        return ConsumerDeliveryResult(
            consumer_name=self.name,
            status=DeliveryStatus.SKIPPED,
            metadata={"reason": "Filter threshold not met"},
        )


class MutationAttemptingOpportunityConsumer:
    """Mock consumer that attempts to illegally mutate the opportunity."""
    def __init__(self, name: str = "mutator_consumer"):
        self._name = name
        self.mutation_failed = False

    @property
    def name(self) -> str:
        return self._name

    def consume(self, opportunity: DispatchableOpportunity) -> ConsumerDeliveryResult:
        try:
            # Attempt mutation
            opportunity.arbitrage_margin = Decimal("0.99")  # type: ignore
        except Exception:
            self.mutation_failed = True

        try:
            # Attempt mutating source opportunity
            opportunity.source_opportunity.status = SurebetStatus.NO_SUREBET  # type: ignore
        except Exception:
            self.mutation_failed = True

        return ConsumerDeliveryResult(
            consumer_name=self.name,
            status=DeliveryStatus.DELIVERED,
        )


# ---------------------------------------------------------------------------
# Test Suite: Matrix A - T
# ---------------------------------------------------------------------------

class TestOpportunityDispatcherMatrix:
    """Authoritative test suite fulfilling requirements A through T."""

    def test_a_valid_surebet_consumer_receives_it(self):
        """Test A: Valid surebet -> consumer receives it successfully."""
        opp = create_valid_1x2_opportunity()
        consumer = InMemoryOpportunityConsumer("test_consumer")
        dispatcher = OpportunityDispatcher(consumers=[consumer])

        result = dispatcher.dispatch(opp)

        assert result.status == DispatchStatus.DELIVERED
        assert result.opportunity_id == opp.opportunity_id
        assert len(result.deliveries) == 1
        assert result.deliveries[0].status == DeliveryStatus.DELIVERED
        assert result.deliveries[0].consumer_name == "test_consumer"
        assert len(consumer.received_opportunities) == 1
        received = consumer.received_opportunities[0]
        assert received.opportunity_id == opp.opportunity_id
        assert received.canonical_event_id == opp.canonical_event_id
        assert received.arbitrage_margin == opp.arbitrage_margin

    def test_b_multiple_consumers_all_receive_it(self):
        """Test B: Multiple consumers -> all receive it in deterministic order."""
        opp = create_valid_1x2_opportunity()
        c1 = InMemoryOpportunityConsumer("consumer_1")
        c2 = InMemoryOpportunityConsumer("consumer_2")
        c3 = InMemoryOpportunityConsumer("consumer_3")

        dispatcher = OpportunityDispatcher()
        dispatcher.register_consumer(c1)
        dispatcher.register_consumer(c2)
        dispatcher.register_consumer(c3)

        result = dispatcher.dispatch(opp)

        assert result.status == DispatchStatus.DELIVERED
        assert len(result.deliveries) == 3
        assert [d.consumer_name for d in result.deliveries] == ["consumer_1", "consumer_2", "consumer_3"]
        assert all(d.status == DeliveryStatus.DELIVERED for d in result.deliveries)

        assert len(c1.received_opportunities) == 1
        assert len(c2.received_opportunities) == 1
        assert len(c3.received_opportunities) == 1

    def test_c_one_consumer_fails_other_consumer_still_receives_it(self):
        """Test C: One consumer fails -> other consumer still receives it (Fault Isolation)."""
        opp = create_valid_1x2_opportunity()
        c_fail = FailingOpportunityConsumer("fail_consumer", RuntimeError("Network drop"))
        c_ok = InMemoryOpportunityConsumer("ok_consumer")

        dispatcher = OpportunityDispatcher(consumers=[c_fail, c_ok])
        result = dispatcher.dispatch(opp)

        assert result.status == DispatchStatus.PARTIAL_FAILURE
        assert len(result.deliveries) == 2

        d_fail = result.deliveries[0]
        assert d_fail.consumer_name == "fail_consumer"
        assert d_fail.status == DeliveryStatus.FAILED
        assert "RuntimeError: Network drop" in (d_fail.error or "")

        d_ok = result.deliveries[1]
        assert d_ok.consumer_name == "ok_consumer"
        assert d_ok.status == DeliveryStatus.DELIVERED

        assert len(c_ok.received_opportunities) == 1

    def test_d_invalid_opportunity_rejected_before_consumers(self):
        """Test D: Invalid opportunity -> rejected before consumers, zero delivery attempts."""
        valid_opp = create_valid_1x2_opportunity()
        # Create an opportunity with status NO_SUREBET
        invalid_opp = SurebetOpportunity(
            opportunity_id="sb_invalid_status",
            canonical_event_id=valid_opp.canonical_event_id,
            canonical_market_key=valid_opp.canonical_market_key,
            legs=valid_opp.legs,
            implied_probability_sum=valid_opp.implied_probability_sum,
            arbitrage_margin=valid_opp.arbitrage_margin,
            status=SurebetStatus.NO_SUREBET,
            is_mixed_bookmakers=True,
            bookmakers=valid_opp.bookmakers,
        )

        consumer = InMemoryOpportunityConsumer("test_consumer")
        dispatcher = OpportunityDispatcher(consumers=[consumer])

        result = dispatcher.dispatch(invalid_opp)

        assert result.status == DispatchStatus.REJECTED
        assert "must be 'SUREBET'" in (result.rejection_reason or "")
        assert len(result.deliveries) == 0
        assert len(consumer.received_opportunities) == 0

    def test_e_duplicate_opportunity_in_same_batch_delivered_once(self):
        """Test E: Duplicate opportunity in same batch -> delivered once, second SKIPPED_DUPLICATE."""
        opp1 = create_valid_1x2_opportunity(event_id="evt_dup_1", opp_id="sb_unique_dup_id")
        opp2 = create_valid_1x2_opportunity(event_id="evt_dup_1", opp_id="sb_unique_dup_id")  # identical ID

        consumer = InMemoryOpportunityConsumer("test_consumer")
        dispatcher = OpportunityDispatcher(consumers=[consumer])

        batch_result = dispatcher.dispatch_batch([opp1, opp2])

        assert len(batch_result.results) == 2
        assert batch_result.results[0].status == DispatchStatus.DELIVERED
        assert batch_result.results[1].status == DispatchStatus.SKIPPED_DUPLICATE
        assert "Duplicate opportunity_id" in (batch_result.results[1].rejection_reason or "")

        assert len(consumer.received_opportunities) == 1
        assert batch_result.metrics.duplicate_count == 1
        assert batch_result.metrics.valid_opportunity_count == 1
        assert batch_result.metrics.delivered_count == 1

    def test_f_empty_batch_valid_empty_result(self):
        """Test F: Empty batch -> valid empty result with zero counts, no exceptions."""
        consumer = InMemoryOpportunityConsumer("test_consumer")
        dispatcher = OpportunityDispatcher(consumers=[consumer])

        batch_result = dispatcher.dispatch_batch([])

        assert isinstance(batch_result, BatchDispatchResult)
        assert len(batch_result.results) == 0
        assert batch_result.metrics.input_opportunity_count == 0
        assert batch_result.metrics.valid_opportunity_count == 0
        assert batch_result.metrics.delivered_count == 0
        assert batch_result.metrics.failed_count == 0
        assert batch_result.metrics.rejected_opportunity_count == 0
        assert batch_result.metrics.duplicate_count == 0
        assert batch_result.metrics.dispatch_duration_ms >= 0.0

    def test_g_batch_partial_failure_individual_statuses_preserved(self):
        """Test G: Batch partial failure -> individual statuses preserved."""
        opp_ok = create_valid_1x2_opportunity(event_id="evt_ok", opp_id="sb_ok")
        opp_bad_odds = SurebetOpportunity(
            opportunity_id="sb_bad_odds",
            canonical_event_id="evt_bad",
            canonical_market_key=opp_ok.canonical_market_key,
            legs=(
                SurebetLeg(
                    canonical_selection_key=opp_ok.legs[0].canonical_selection_key,
                    selection_type="HOME",
                    provider="superbet",
                    odds=Decimal("0.95"),  # INVALID ODDS <= 1.0
                    source_selection_id="s1",
                ),
            ),
            implied_probability_sum=Decimal("0.9"),
            arbitrage_margin=Decimal("0.1"),
            status=SurebetStatus.SUREBET,
        )

        consumer = InMemoryOpportunityConsumer("consumer_a")
        dispatcher = OpportunityDispatcher(consumers=[consumer])

        batch_res = dispatcher.dispatch_batch([opp_ok, opp_bad_odds])

        assert len(batch_res.results) == 2
        assert batch_res.results[0].status == DispatchStatus.DELIVERED
        assert batch_res.results[1].status == DispatchStatus.REJECTED
        assert batch_res.metrics.input_opportunity_count == 2
        assert batch_res.metrics.valid_opportunity_count == 1
        assert batch_res.metrics.rejected_opportunity_count == 1
        assert len(consumer.received_opportunities) == 1

    def test_h_deterministic_order_repeated_runs(self):
        """Test H: Deterministic order -> repeated runs preserve order."""
        opp_a = create_valid_1x2_opportunity(event_id="evt_A", opp_id="sb_A")
        opp_b = create_valid_1x2_opportunity(event_id="evt_B", opp_id="sb_B")
        opp_c = create_valid_1x2_opportunity(event_id="evt_C", opp_id="sb_C")

        input_batch = [opp_a, opp_b, opp_c]

        for _ in range(5):
            consumer = InMemoryOpportunityConsumer("test_consumer")
            dispatcher = OpportunityDispatcher(consumers=[consumer])
            res = dispatcher.dispatch_batch(input_batch)
            assert [r.opportunity_id for r in res.results] == ["sb_A", "sb_B", "sb_C"]
            assert [o.opportunity_id for o in consumer.received_opportunities] == ["sb_A", "sb_B", "sb_C"]

    def test_i_consumer_order_registration_order_preserved(self):
        """Test I: Consumer order -> registration order preserved."""
        order_tracker: List[str] = []

        class TrackingConsumer:
            def __init__(self, name: str):
                self._name = name
            @property
            def name(self) -> str:
                return self._name
            def consume(self, opp: DispatchableOpportunity) -> ConsumerDeliveryResult:
                order_tracker.append(self.name)
                return ConsumerDeliveryResult(consumer_name=self.name, status=DeliveryStatus.DELIVERED)

        dispatcher = OpportunityDispatcher()
        c_names = ["alpha", "beta", "gamma", "delta"]
        for name in c_names:
            dispatcher.register_consumer(TrackingConsumer(name))

        opp = create_valid_1x2_opportunity()
        dispatcher.dispatch(opp)

        assert order_tracker == ["alpha", "beta", "gamma", "delta"]

    def test_j_consumer_exception_captured_as_failed(self):
        """Test J: Consumer exception -> captured as FAILED without crashing dispatcher."""
        c_err = FailingOpportunityConsumer("crash_consumer", ValueError("Simulated ValueError"))
        dispatcher = OpportunityDispatcher(consumers=[c_err])

        opp = create_valid_1x2_opportunity()
        result = dispatcher.dispatch(opp)

        assert result.status == DispatchStatus.FAILED
        assert len(result.deliveries) == 1
        assert result.deliveries[0].status == DeliveryStatus.FAILED
        assert "ValueError: Simulated ValueError" in (result.deliveries[0].error or "")

    def test_k_opportunity_immutability_source_unchanged(self):
        """Test K: Opportunity immutability -> source SurebetOpportunity unchanged after dispatch."""
        opp = create_valid_1x2_opportunity()
        initial_margin = opp.arbitrage_margin
        initial_status = opp.status
        initial_legs = tuple(opp.legs)

        consumer = InMemoryOpportunityConsumer("test_consumer")
        dispatcher = OpportunityDispatcher(consumers=[consumer])

        dispatcher.dispatch(opp)

        assert opp.arbitrage_margin == initial_margin
        assert opp.status == initial_status
        assert opp.legs == initial_legs

    def test_l_consumer_mutation_attempt_raises_and_source_intact(self):
        """Test L: Consumer mutation attempt -> modifying frozen dataclasses fails and leaves source unchanged."""
        opp = create_valid_1x2_opportunity()
        mutator = MutationAttemptingOpportunityConsumer("mutator")
        dispatcher = OpportunityDispatcher(consumers=[mutator])

        dispatcher.dispatch(opp)

        assert mutator.mutation_failed is True
        assert opp.status == SurebetStatus.SUREBET
        assert opp.arbitrage_margin > Decimal("0.0")

    def test_m_lineage_preserved_full_provider_ids(self):
        """Test M: Lineage preserved -> consumer sees full provider IDs, selection IDs, native odds."""
        opp = create_valid_1x2_opportunity()
        consumer = InMemoryOpportunityConsumer("test_consumer")
        dispatcher = OpportunityDispatcher(consumers=[consumer])

        dispatcher.dispatch(opp)

        delivered_opp = consumer.received_opportunities[0]
        assert len(delivered_opp.legs) == 3

        home_leg = next(l for l in delivered_opp.legs if l.selection_type == "HOME")
        assert home_leg.provider == "superbet"
        assert home_leg.source_selection_id == "sb_sel_1"
        assert home_leg.source_event_id == "sb_evt_1"
        assert home_leg.source_market_id == "sb_mkt_1"
        assert home_leg.odds == Decimal("2.15")
        assert home_leg.canonical_selection_key.selection_type == "HOME"

        draw_leg = next(l for l in delivered_opp.legs if l.selection_type == "DRAW")
        assert draw_leg.provider == "betclic"
        assert draw_leg.source_selection_id == "bc_sel_X"
        assert draw_leg.source_event_id == "bc_evt_1"
        assert draw_leg.odds == Decimal("3.80")

    def test_n_decimal_values_preserved_exactly(self):
        """Test N: Decimal values preserved exactly (no floating point conversions)."""
        opp = create_valid_1x2_opportunity()
        consumer = InMemoryOpportunityConsumer("test_consumer")
        dispatcher = OpportunityDispatcher(consumers=[consumer])

        dispatcher.dispatch(opp)

        delivered_opp = consumer.received_opportunities[0]
        assert isinstance(delivered_opp.implied_probability_sum, Decimal)
        assert isinstance(delivered_opp.arbitrage_margin, Decimal)
        assert delivered_opp.implied_probability_sum == opp.implied_probability_sum
        assert delivered_opp.arbitrage_margin == opp.arbitrage_margin
        for leg in delivered_opp.legs:
            assert isinstance(leg.odds, Decimal)
            assert isinstance(leg.implied_probability, Decimal)

    def test_o_no_network_calls(self, monkeypatch):
        """Test O: No network calls (completely offline execution)."""
        # Block socket connections to strictly verify no network access
        import socket
        def guard(*args, **kwargs):
            raise AssertionError("Attempted network socket access during dispatch test!")
        monkeypatch.setattr(socket, "socket", guard)

        opp = create_valid_1x2_opportunity()
        consumer = InMemoryOpportunityConsumer("offline_consumer")
        dispatcher = OpportunityDispatcher(consumers=[consumer])

        res = dispatcher.dispatch(opp)
        assert res.status == DispatchStatus.DELIVERED

    def test_p_multiple_opportunities_metrics_consistent(self):
        """Test P: Multiple opportunities -> metrics consistent."""
        opp1 = create_valid_1x2_opportunity(event_id="evt_1", opp_id="sb_1")
        opp2 = create_valid_1x2_opportunity(event_id="evt_2", opp_id="sb_2")
        opp3 = create_valid_1x2_opportunity(event_id="evt_1", opp_id="sb_1")  # duplicate of 1
        opp4 = create_valid_1x2_opportunity(event_id="evt_3", opp_id="sb_3")

        c1 = InMemoryOpportunityConsumer("c1")
        c2 = FailingOpportunityConsumer("c2", RuntimeError("c2 fail"))

        dispatcher = OpportunityDispatcher(consumers=[c1, c2])
        batch_res = dispatcher.dispatch_batch([opp1, opp2, opp3, opp4])

        m = batch_res.metrics
        assert m.input_opportunity_count == 4
        assert m.valid_opportunity_count == 3
        assert m.duplicate_count == 1
        assert m.rejected_opportunity_count == 0
        assert m.consumer_count == 2

        # 3 valid opportunities * 2 consumers = 6 delivery attempts
        assert m.delivery_attempt_count == 6
        assert m.delivered_count == 3
        assert m.failed_count == 3
        assert m.skipped_count == 0

        # Per consumer metrics
        assert m.per_consumer["c1"]["attempts"] == 3
        assert m.per_consumer["c1"]["delivered"] == 3
        assert m.per_consumer["c1"]["failed"] == 0

        assert m.per_consumer["c2"]["attempts"] == 3
        assert m.per_consumer["c2"]["delivered"] == 0
        assert m.per_consumer["c2"]["failed"] == 3

    def test_q_no_registered_consumers_explicit_result_no_crash(self):
        """Test Q: No registered consumers -> returns NO_CONSUMERS, no crash."""
        opp = create_valid_1x2_opportunity()
        dispatcher = OpportunityDispatcher(consumers=[])

        res = dispatcher.dispatch(opp)

        assert res.status == DispatchStatus.NO_CONSUMERS
        assert len(res.deliveries) == 0
        assert res.opportunity is not None
        assert res.rejection_reason == "No consumers registered"

    def test_r_duplicate_opportunity_ids_deterministic(self):
        """Test R: Duplicate opportunity IDs -> deterministic behavior across multiple identical batches."""
        opp_dup1 = create_valid_1x2_opportunity(event_id="evt_r", opp_id="sb_dup_r")
        opp_dup2 = create_valid_1x2_opportunity(event_id="evt_r", opp_id="sb_dup_r")
        opp_dup3 = create_valid_1x2_opportunity(event_id="evt_r", opp_id="sb_dup_r")

        batch = [opp_dup1, opp_dup2, opp_dup3]

        for _ in range(3):
            consumer = InMemoryOpportunityConsumer("c")
            dispatcher = OpportunityDispatcher(consumers=[consumer])
            res = dispatcher.dispatch_batch(batch)

            assert len(res.results) == 3
            assert res.results[0].status == DispatchStatus.DELIVERED
            assert res.results[1].status == DispatchStatus.SKIPPED_DUPLICATE
            assert res.results[2].status == DispatchStatus.SKIPPED_DUPLICATE
            assert len(consumer.received_opportunities) == 1

    def test_s_malformed_opportunity_no_consumer_invocation(self):
        """Test S: Malformed opportunity variations -> rejected with zero consumer invocation."""
        valid_opp = create_valid_1x2_opportunity()
        consumer = InMemoryOpportunityConsumer("consumer")
        dispatcher = OpportunityDispatcher(consumers=[consumer])

        # Case 1: Empty opportunity_id
        bad_id_opp = SurebetOpportunity(
            opportunity_id="",
            canonical_event_id=valid_opp.canonical_event_id,
            canonical_market_key=valid_opp.canonical_market_key,
            legs=valid_opp.legs,
            implied_probability_sum=valid_opp.implied_probability_sum,
            arbitrage_margin=valid_opp.arbitrage_margin,
            status=SurebetStatus.SUREBET,
        )
        res1 = dispatcher.dispatch(bad_id_opp)
        assert res1.status == DispatchStatus.REJECTED
        assert len(consumer.received_opportunities) == 0

        # Case 2: Implied probability sum >= 1.0 (margin <= 0)
        bad_sum_opp = SurebetOpportunity(
            opportunity_id="sb_bad_sum",
            canonical_event_id=valid_opp.canonical_event_id,
            canonical_market_key=valid_opp.canonical_market_key,
            legs=valid_opp.legs,
            implied_probability_sum=Decimal("1.05"),
            arbitrage_margin=Decimal("-0.05"),
            status=SurebetStatus.SUREBET,
        )
        res2 = dispatcher.dispatch(bad_sum_opp)
        assert res2.status == DispatchStatus.REJECTED
        assert "implied_probability_sum" in (res2.rejection_reason or "")
        assert len(consumer.received_opportunities) == 0

        # Case 3: Empty legs
        bad_legs_opp = SurebetOpportunity(
            opportunity_id="sb_bad_legs",
            canonical_event_id=valid_opp.canonical_event_id,
            canonical_market_key=valid_opp.canonical_market_key,
            legs=(),
            implied_probability_sum=Decimal("0.9"),
            arbitrage_margin=Decimal("0.1"),
            status=SurebetStatus.SUREBET,
        )
        res3 = dispatcher.dispatch(bad_legs_opp)
        assert res3.status == DispatchStatus.REJECTED
        assert "empty or non-sequence legs" in (res3.rejection_reason or "")
        assert len(consumer.received_opportunities) == 0

        # Case 4: Duplicate selection type in legs
        bad_dup_type_opp = SurebetOpportunity(
            opportunity_id="sb_dup_types",
            canonical_event_id=valid_opp.canonical_event_id,
            canonical_market_key=valid_opp.canonical_market_key,
            legs=(valid_opp.legs[0], valid_opp.legs[0]),
            implied_probability_sum=Decimal("0.9"),
            arbitrage_margin=Decimal("0.1"),
            status=SurebetStatus.SUREBET,
        )
        res4 = dispatcher.dispatch(bad_dup_type_opp)
        assert res4.status == DispatchStatus.REJECTED
        assert "Duplicate selection_type" in (res4.rejection_reason or "")
        assert len(consumer.received_opportunities) == 0

        # Case 5: Leg market_type mismatch with canonical market key
        totals_mkt_key = CanonicalMarketKey(
            market_type=CanonicalMarketType.TOTALS.value,
            line=Decimal("2.5"),
            period=MarketPeriod.FULL_TIME.value,
            scope=MarketScope.MATCH.value,
        )
        mismatched_key = CanonicalSelectionKey(
            market_key=totals_mkt_key,
            selection_type="OVER",
        )
        bad_mkt_leg = SurebetLeg(
            canonical_selection_key=mismatched_key,
            selection_type="OVER",
            provider="superbet",
            odds=Decimal("2.10"),
            source_selection_id="s_mismatch",
        )
        bad_mkt_opp = SurebetOpportunity(
            opportunity_id="sb_mkt_mismatch",
            canonical_event_id=valid_opp.canonical_event_id,
            canonical_market_key=valid_opp.canonical_market_key,
            legs=(bad_mkt_leg, valid_opp.legs[1]),
            implied_probability_sum=Decimal("0.9"),
            arbitrage_margin=Decimal("0.1"),
            status=SurebetStatus.SUREBET,
        )
        res5 = dispatcher.dispatch(bad_mkt_opp)
        assert res5.status == DispatchStatus.REJECTED
        assert "does not match market key" in (res5.rejection_reason or "")
        assert len(consumer.received_opportunities) == 0

    def test_t_serialization_no_precision_loss(self):
        """Test T: Serialization to_dict() -> deterministic field ordering, string Decimals, no loss of lineage."""
        opp = create_valid_1x2_opportunity()
        consumer = InMemoryOpportunityConsumer("test_consumer")
        dispatcher = OpportunityDispatcher(consumers=[consumer])

        dispatcher.dispatch(opp)

        disp_opp = consumer.received_opportunities[0]
        serialized = disp_opp.to_dict()

        assert isinstance(serialized, dict)
        assert serialized["opportunity_id"] == opp.opportunity_id
        assert serialized["canonical_event_id"] == opp.canonical_event_id
        assert serialized["canonical_market_key"]["market_type"] == CanonicalMarketType.ONE_X_TWO.value
        assert isinstance(serialized["implied_probability_sum"], str)
        assert isinstance(serialized["arbitrage_margin"], str)
        assert isinstance(serialized["arbitrage_margin_pct"], str)
        assert serialized["implied_probability_sum"] == str(opp.implied_probability_sum)
        assert serialized["arbitrage_margin"] == str(opp.arbitrage_margin)

        assert len(serialized["legs"]) == 3
        for leg_dict in serialized["legs"]:
            assert "selection_type" in leg_dict
            assert "canonical_selection_key" in leg_dict
            assert "provider" in leg_dict
            assert "odds" in leg_dict
            assert "source_selection_id" in leg_dict
            assert isinstance(leg_dict["odds"], str)
            assert float(leg_dict["odds"]) > 1.0


# ---------------------------------------------------------------------------
# Additional Boundary and Registration Tests
# ---------------------------------------------------------------------------

class TestOpportunityDispatcherBoundaryAndRegistration:
    """Tests consumer registration, unregistration, polymorphic dispatch, and debug consumer."""

    def test_register_duplicate_consumer_name_raises(self):
        """Registering two consumers with the same name raises ValueError."""
        c1 = InMemoryOpportunityConsumer("my_consumer")
        c2 = InMemoryOpportunityConsumer("my_consumer")
        dispatcher = OpportunityDispatcher()
        dispatcher.register_consumer(c1)

        with pytest.raises(ValueError, match="already registered"):
            dispatcher.register_consumer(c2)

    def test_register_invalid_consumer_type_raises(self):
        """Registering an object that does not adhere to the protocol raises TypeError."""
        dispatcher = OpportunityDispatcher()
        with pytest.raises(TypeError, match="must implement OpportunityConsumer protocol"):
            dispatcher.register_consumer("invalid_consumer_string")  # type: ignore

    def test_unregister_consumer(self):
        """Unregistering a consumer by name removes it deterministically."""
        c1 = InMemoryOpportunityConsumer("c1")
        c2 = InMemoryOpportunityConsumer("c2")
        dispatcher = OpportunityDispatcher(consumers=[c1, c2])

        assert len(dispatcher.get_registered_consumers()) == 2
        assert dispatcher.unregister_consumer("c1") is True
        assert len(dispatcher.get_registered_consumers()) == 1
        assert dispatcher.get_registered_consumers()[0].name == "c2"
        assert dispatcher.unregister_consumer("non_existent") is False

    def test_console_consumer_execution(self):
        """ConsoleOpportunityConsumer formats message and outputs to sink without error."""
        messages: List[str] = []
        console_consumer = ConsoleOpportunityConsumer("console", sink=messages.append)

        dispatcher = OpportunityDispatcher(consumers=[console_consumer])
        opp = create_valid_1x2_opportunity()
        res = dispatcher.dispatch(opp)

        assert res.status == DispatchStatus.DELIVERED
        assert len(messages) == 1
        assert "[SUREBET DISPATCH]" in messages[0]
        assert opp.opportunity_id in messages[0]

    def test_dispatch_surebet_detection_result_polymorphic(self):
        """Dispatcher accepts a Stage 5.5 SurebetDetectionResult directly."""
        opp1 = create_valid_1x2_opportunity(event_id="evt_poly_1", opp_id="sb_poly_1")
        opp2 = create_valid_1x2_opportunity(event_id="evt_poly_2", opp_id="sb_poly_2")

        detection_result = SurebetDetectionResult(
            opportunities=[opp1, opp2],
            metrics=SurebetDetectionMetrics(surebet_count=2),
        )

        consumer = InMemoryOpportunityConsumer("poly_consumer")
        dispatcher = OpportunityDispatcher(consumers=[consumer])

        batch_res = dispatcher.dispatch_batch(detection_result, dispatch_run_id="run_100")

        assert batch_res.dispatch_run_id == "run_100"
        assert len(batch_res.results) == 2
        assert all(r.status == DispatchStatus.DELIVERED for r in batch_res.results)
        assert len(consumer.received_opportunities) == 2


# ---------------------------------------------------------------------------
# Performance Sanity Benchmark
# ---------------------------------------------------------------------------

class TestOpportunityDispatcherPerformanceSanity:
    """Basic engineering sanity throughput check for 10, 100, and 1000 opportunities."""

    def test_throughput_10_opportunities(self):
        consumer = InMemoryOpportunityConsumer("perf_10")
        dispatcher = OpportunityDispatcher(consumers=[consumer])
        opps = [create_valid_1x2_opportunity(event_id=f"evt_{i}", opp_id=f"sb_perf10_{i}") for i in range(10)]

        res = dispatcher.dispatch_batch(opps)
        assert res.metrics.delivered_count == 10
        assert res.metrics.dispatch_duration_ms < 50.0  # Under 50ms for 10 opportunities

    def test_throughput_100_opportunities(self):
        consumer = InMemoryOpportunityConsumer("perf_100")
        dispatcher = OpportunityDispatcher(consumers=[consumer])
        opps = [create_valid_1x2_opportunity(event_id=f"evt_{i}", opp_id=f"sb_perf100_{i}") for i in range(100)]

        res = dispatcher.dispatch_batch(opps)
        assert res.metrics.delivered_count == 100
        assert res.metrics.dispatch_duration_ms < 200.0  # Under 200ms for 100 opportunities

    def test_throughput_1000_opportunities(self):
        consumer = InMemoryOpportunityConsumer("perf_1000")
        dispatcher = OpportunityDispatcher(consumers=[consumer])
        opps = [create_valid_1x2_opportunity(event_id=f"evt_{i}", opp_id=f"sb_perf1000_{i}") for i in range(1000)]

        res = dispatcher.dispatch_batch(opps)
        assert res.metrics.delivered_count == 1000
        assert res.metrics.dispatch_duration_ms < 1000.0  # Under 1000ms for 1000 opportunities
