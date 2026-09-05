"""P0-001 regression: SKIPPED must never become DELIVERED/ALERTED.

Minimal realistic coverage for the dispatch truth table + lifecycle safety.
"""
from decimal import Decimal

from database.config import DatabaseConfig
from database.connection import DatabaseManager
from database.repositories.opportunity_repository import OpportunityRepository
from normalization.dispatcher import (
    ConsumerDeliveryResult,
    DeliveryStatus,
    DispatchStatus,
    OpportunityDispatcher,
)
from normalization.lifecycle import (
    OpportunityLifecycleManager,
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


def _make_opp(opp_id="sb_p0001"):
    mkt = CanonicalMarketKey(
        market_type=CanonicalMarketType.ONE_X_TWO.value,
        period=MarketPeriod.FULL_TIME.value,
        scope=MarketScope.MATCH.value,
    )
    hk = CanonicalSelectionKey(market_key=mkt, selection_type=CanonicalSelectionType.HOME.value, participant_role="HOME")
    dk = CanonicalSelectionKey(market_key=mkt, selection_type=CanonicalSelectionType.DRAW.value)
    ak = CanonicalSelectionKey(market_key=mkt, selection_type=CanonicalSelectionType.AWAY.value, participant_role="AWAY")
    legs = (
        SurebetLeg(canonical_selection_key=hk, selection_type="HOME", provider="superbet",
                   odds=Decimal("2.15"), source_selection_id="s1", source_event_id="e1",
                   source_market_id="m1", implied_probability=Decimal("1") / Decimal("2.15")),
        SurebetLeg(canonical_selection_key=dk, selection_type="DRAW", provider="betclic",
                   odds=Decimal("3.80"), source_selection_id="s2", source_event_id="e1",
                   source_market_id="m1", implied_probability=Decimal("1") / Decimal("3.80")),
        SurebetLeg(canonical_selection_key=ak, selection_type="AWAY", provider="superbet",
                   odds=Decimal("4.20"), source_selection_id="s3", source_event_id="e1",
                   source_market_id="m1", implied_probability=Decimal("1") / Decimal("4.20")),
    )
    s = sum(l.implied_probability for l in legs)
    m = Decimal("1") / s - Decimal("1")
    return SurebetOpportunity(
        opportunity_id=opp_id, canonical_event_id="evt_p0001", canonical_market_key=mkt,
        legs=legs, implied_probability_sum=s, arbitrage_margin=m,
        status=SurebetStatus.SUREBET, is_mixed_bookmakers=True,
        bookmakers=("betclic", "superbet"),
    )


class _Skip:
    def __init__(self, name):
        self._name = name

    @property
    def name(self):
        return self._name

    def consume(self, opp):
        return ConsumerDeliveryResult(consumer_name=self.name, status=DeliveryStatus.SKIPPED,
                                      metadata={"reason": "disabled"})


class _Deliver:
    def __init__(self, name):
        self._name = name

    @property
    def name(self):
        return self._name

    def consume(self, opp):
        return ConsumerDeliveryResult(consumer_name=self.name, status=DeliveryStatus.DELIVERED)


class _Fail:
    def __init__(self, name):
        self._name = name

    @property
    def name(self):
        return self._name

    def consume(self, opp):
        return ConsumerDeliveryResult(consumer_name=self.name, status=DeliveryStatus.FAILED,
                                      error="boom")


def test_skipped_only_is_skipped_not_delivered():
    d = OpportunityDispatcher(consumers=[_Skip("s1"), _Skip("s2")])
    res = d.dispatch(_make_opp())
    assert res.status == DispatchStatus.SKIPPED
    assert all(x.status == DeliveryStatus.SKIPPED for x in res.deliveries)


def test_dispatch_truth_table():
    assert OpportunityDispatcher(consumers=[_Deliver("a")]).dispatch(_make_opp("x1")).status == DispatchStatus.DELIVERED
    assert OpportunityDispatcher(consumers=[_Fail("a")]).dispatch(_make_opp("x2")).status == DispatchStatus.FAILED
    assert OpportunityDispatcher(consumers=[_Deliver("a"), _Fail("b")]).dispatch(_make_opp("x3")).status == DispatchStatus.PARTIAL_FAILURE
    assert OpportunityDispatcher(consumers=[_Skip("a")]).dispatch(_make_opp("x4")).status == DispatchStatus.SKIPPED


def test_skipped_only_never_alerts_lifecycle():
    config = DatabaseConfig.default_sqlite_in_memory()
    mgr = DatabaseManager(config)
    mgr.create_tables()
    sess = mgr.get_session()
    try:
        repo = OpportunityRepository(sess)
        lm = OpportunityLifecycleManager(repo)
        opp = _make_opp("sb_p0001_lc")
        detection = SurebetDetectionResult(
            opportunities=[opp],
            evaluations=[MarketSurebetEvaluation(
                canonical_event_id=opp.canonical_event_id,
                canonical_market_key=opp.canonical_market_key,
                status=SurebetStatus.SUREBET,
                completeness_status=MarketCompletenessStatus.COMPLETE,
                required_selection_types=("HOME", "DRAW", "AWAY"),
                available_selection_types=("HOME", "DRAW", "AWAY"),
                missing_selection_types=(),
                best_legs=opp.legs,
                opportunity=opp,
            )],
            metrics=SurebetDetectionMetrics(surebet_count=1),
        )
        summary = lm.process_and_dispatch(detection, OpportunityDispatcher(consumers=[_Skip("s1"), _Skip("s2")]))
        assert summary.dispatch_result.results[0].status == DispatchStatus.SKIPPED
        assert summary.delivered_count == 0
        assert summary.skipped_count == 1
        fp = generate_opportunity_fingerprint("SUREBET", opp.canonical_event_id, opp.canonical_market_key, opp.legs)
        persisted = repo.get_by_fingerprint(fp)
        assert persisted.status != "ALERTED"
        assert persisted.alert_count == 0
        assert persisted.last_alerted_at is None
        assert persisted.delivery_status == "SKIPPED"
    finally:
        sess.close()
