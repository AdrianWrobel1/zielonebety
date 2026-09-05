"""P1-006 regression: commit failure must not be masked by undefined logger."""
from decimal import Decimal

from database.config import DatabaseConfig
from database.connection import DatabaseManager
from database.repositories.opportunity_repository import OpportunityRepository
from normalization.dispatcher import InMemoryOpportunityConsumer, OpportunityDispatcher
from normalization.lifecycle import OpportunityLifecycleManager
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


def _make_opp(opp_id="sb_p1006"):
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
        opportunity_id=opp_id, canonical_event_id="evt_p1006", canonical_market_key=mkt,
        legs=legs, implied_probability_sum=s, arbitrage_margin=m,
        status=SurebetStatus.SUREBET, is_mixed_bookmakers=True,
        bookmakers=("betclic", "superbet"),
    )


class _FailingCommitSession:
    def __init__(self, real):
        self._real = real

    def __getattr__(self, name):
        return getattr(self._real, name)

    def commit(self):
        raise RuntimeError("simulated commit failure")


def test_commit_failure_not_masked_by_nameerror(caplog):
    import logging
    cfg = DatabaseConfig.default_sqlite_in_memory()
    dm = DatabaseManager(cfg)
    dm.create_tables()
    sess = dm.get_session()
    try:
        repo = OpportunityRepository(sess)
        repo.session = _FailingCommitSession(sess)
        lm = OpportunityLifecycleManager(repo)
        opp = _make_opp()
        det = SurebetDetectionResult(
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
        with caplog.at_level(logging.WARNING, logger="normalization.lifecycle"):
            summary = lm.process_and_dispatch(det, OpportunityDispatcher(consumers=[InMemoryOpportunityConsumer("mem")]))
        # No NameError; dispatch still completes; commit failure is logged, not raised as NameError
        assert summary.delivered_count == 1
        assert any("Could not commit" in r.message for r in caplog.records)
        assert not any("logger" in r.message and "not defined" in r.message for r in caplog.records)
    finally:
        sess.close()
