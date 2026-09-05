"""
Unit tests for ValuebetLifecycleManager, Fingerprint stability, Transitions, and Alert Budgeting (Stage 9.2).
"""

from datetime import datetime, timezone, timedelta
from decimal import Decimal
from typing import Optional
import pytest

from database.connection import DatabaseManager
from database.models import OpportunityRecordORM
from database.repositories.opportunity_repository import OpportunityRepository
from normalization.lifecycle import LifecycleAction
from valuebets.lifecycle import (
    ValuebetLifecycleBatch,
    ValuebetLifecycleManager,
    generate_valuebet_fingerprint,
)
from valuebets.models import ValueBetCandidate
from valuebets.quality_policy import ValuebetQualityPolicy


from database.config import DatabaseConfig


def _make_candidate(
    event_id: str = "ev_01",
    market_type: str = "1X2",
    line: Optional[Decimal] = None,
    selection_type: str = "HOME",
    bookmaker: str = "superbet",
    reference_source: str = "the_odds_api",
    value_percent: Decimal = Decimal("5.0"),
    odds: Decimal = Decimal("2.10"),
    fair_odds: Decimal = Decimal("2.00"),
    fair_probability: Decimal = Decimal("0.50"),
) -> ValueBetCandidate:
    return ValueBetCandidate(
        candidate_id=f"cand_{event_id}_{market_type}_{selection_type}",
        canonical_event_id=event_id,
        event_name="Team A vs Team B",
        sport="soccer",
        competition_name="Premier League",
        kickoff="2026-08-18T18:00:00Z",
        market_type=market_type,
        line=line,
        selection_type=selection_type,
        bookmaker=bookmaker,
        bookmaker_odds=odds,
        bookmaker_implied_prob=Decimal("1") / odds if odds > 0 else Decimal("0"),
        reference_source=reference_source,
        reference_bookmaker="pinnacle",
        reference_raw_odds=Decimal("2.00"),
        reference_overround=Decimal("1.0573"),
        reference_fair_probability=fair_probability,
        reference_fair_odds=fair_odds,
        value_edge=value_percent / Decimal("100"),
        value_percent=value_percent,
        is_qualified=True,
        reference_timestamp="2026-08-17T12:00:00Z",
    )


@pytest.fixture
def db_session():
    db = DatabaseManager(DatabaseConfig(db_url="sqlite:///:memory:"))
    db.create_tables()
    with db.get_session() as session:
        yield session


class TestValuebetLifecycleManager:
    def test_fingerprint_stability(self):
        c1 = _make_candidate(value_percent=Decimal("5.0"), odds=Decimal("2.10"))
        c2 = _make_candidate(value_percent=Decimal("8.5"), odds=Decimal("2.35"))
        
        # Volatile values changed, but core identity is identical
        assert generate_valuebet_fingerprint(c1) == generate_valuebet_fingerprint(c2)
        assert generate_valuebet_fingerprint(c1).startswith("opp:VALUEBET:")

    def test_first_seen_candidate_transitions_to_new_and_alerted(self, db_session):
        repo = OpportunityRepository(db_session)
        mgr = ValuebetLifecycleManager(repository=repo, max_alerts_per_scan=5)

        cand = _make_candidate(value_percent=Decimal("4.5"))
        now = datetime(2026, 8, 17, 12, 0, 0, tzinfo=timezone.utc)

        batch = mgr.evaluate_candidates([cand], evaluation_time=now)
        assert batch.new_count == 1
        assert len(batch.to_dispatch) == 1
        assert batch.evaluations[0].action == LifecycleAction.DISPATCH_INITIAL

        # Verify DB record
        fp = generate_valuebet_fingerprint(cand)
        rec = repo.get_by_fingerprint(fp)
        assert rec is not None
        assert rec.opportunity_type == "VALUEBET"
        assert rec.status == "NEW"

    def test_material_ev_increase_transitions_to_updated(self, db_session):
        repo = OpportunityRepository(db_session)
        mgr = ValuebetLifecycleManager(repository=repo, max_alerts_per_scan=5)
        now = datetime(2026, 8, 17, 12, 0, 0, tzinfo=timezone.utc)

        cand1 = _make_candidate(value_percent=Decimal("4.0"))
        mgr.evaluate_candidates([cand1], evaluation_time=now)

        # Mark delivery recorded
        fp = generate_valuebet_fingerprint(cand1)
        repo.record_delivery_result(fingerprint=fp, success=True, delivery_status="DELIVERED", alert_time=now)

        # 2nd scan: EV jumps by 2.5% (material change >= 1.0%)
        cand2 = _make_candidate(value_percent=Decimal("6.5"), odds=Decimal("2.20"))
        batch2 = mgr.evaluate_candidates([cand2], evaluation_time=now + timedelta(seconds=60))

        assert batch2.updated_count == 1
        assert len(batch2.to_dispatch) == 1
        assert batch2.evaluations[0].action == LifecycleAction.DISPATCH_UPDATE

    def test_minor_ev_change_is_suppressed(self, db_session):
        repo = OpportunityRepository(db_session)
        mgr = ValuebetLifecycleManager(repository=repo, max_alerts_per_scan=5)
        now = datetime(2026, 8, 17, 12, 0, 0, tzinfo=timezone.utc)

        cand1 = _make_candidate(value_percent=Decimal("4.0"))
        mgr.evaluate_candidates([cand1], evaluation_time=now)
        fp = generate_valuebet_fingerprint(cand1)
        repo.record_delivery_result(fingerprint=fp, success=True, delivery_status="DELIVERED", alert_time=now)

        # 2nd scan: EV shifts only slightly by 0.3% (< 1.0% material threshold)
        cand2 = _make_candidate(value_percent=Decimal("4.3"), odds=Decimal("2.11"))
        batch2 = mgr.evaluate_candidates([cand2], evaluation_time=now + timedelta(seconds=60))

        assert batch2.suppressed_count == 1
        assert len(batch2.to_dispatch) == 0
        assert batch2.evaluations[0].action == LifecycleAction.SUPPRESS_INSIGNIFICANT

    def test_notification_budget_capped(self, db_session):
        repo = OpportunityRepository(db_session)
        # Cap at 2 alerts per scan
        mgr = ValuebetLifecycleManager(repository=repo, max_alerts_per_scan=2)
        now = datetime(2026, 8, 17, 12, 0, 0, tzinfo=timezone.utc)

        c1 = _make_candidate(event_id="ev_01", value_percent=Decimal("8.0"))
        c2 = _make_candidate(event_id="ev_02", value_percent=Decimal("6.0"))
        c3 = _make_candidate(event_id="ev_03", value_percent=Decimal("4.0"))

        batch = mgr.evaluate_candidates([c1, c2, c3], evaluation_time=now)
        assert len(batch.to_dispatch) == 2
        # Highest ranked 2 candidates are dispatched
        dispatched_ids = [c.canonical_event_id for c in batch.to_dispatch]
        assert "ev_01" in dispatched_ids
        assert "ev_02" in dispatched_ids
        assert "ev_03" not in dispatched_ids

    def test_expiration_of_missing_candidates(self, db_session):
        repo = OpportunityRepository(db_session)
        mgr = ValuebetLifecycleManager(repository=repo)
        now = datetime(2026, 8, 17, 12, 0, 0, tzinfo=timezone.utc)

        cand = _make_candidate(event_id="ev_disappearing", value_percent=Decimal("5.0"))
        mgr.evaluate_candidates([cand], evaluation_time=now)

        fp = generate_valuebet_fingerprint(cand)
        assert repo.get_by_fingerprint(fp).status == "NEW"

        # Missing for 3 scans (max_misses=3)
        mgr.expire_missing_candidates(evaluated_fingerprints=set(), evaluation_time=now, max_misses=3)
        assert repo.get_by_fingerprint(fp).consecutive_misses == 1

        mgr.expire_missing_candidates(evaluated_fingerprints=set(), evaluation_time=now, max_misses=3)
        assert repo.get_by_fingerprint(fp).consecutive_misses == 2

        expired = mgr.expire_missing_candidates(evaluated_fingerprints=set(), evaluation_time=now, max_misses=3)
        assert len(expired) == 1
        assert repo.get_by_fingerprint(fp).status == "EXPIRED"

    def test_resurrection_of_expired_candidate(self, db_session):
        repo = OpportunityRepository(db_session)
        mgr = ValuebetLifecycleManager(repository=repo)
        now = datetime(2026, 8, 17, 12, 0, 0, tzinfo=timezone.utc)

        cand = _make_candidate(event_id="ev_resurrect", value_percent=Decimal("5.0"))
        mgr.evaluate_candidates([cand], evaluation_time=now)
        fp = generate_valuebet_fingerprint(cand)

        # Expire it
        rec = repo.get_by_fingerprint(fp)
        assert rec is not None
        rec.status = "EXPIRED"
        rec.expired_at = now
        repo.save_or_update(rec)
        assert repo.get_by_fingerprint(fp).status == "EXPIRED"

        # Re-evaluate with strong positive EV
        batch = mgr.evaluate_candidates([cand], evaluation_time=now + timedelta(minutes=10))
        assert batch.evaluations[0].action == LifecycleAction.DISPATCH_INITIAL
        assert len(batch.to_dispatch) == 1
        assert repo.get_by_fingerprint(fp).status == "NEW"
