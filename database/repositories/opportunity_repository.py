"""
Opportunity Repository Implementation for Persistent Lifecycle Tracking
"""

from typing import List, Optional, Set
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from database.repositories.base_repository import BaseRepository
from database.models import OpportunityRecordORM


class OpportunityRepository(BaseRepository[OpportunityRecordORM]):
    """Repository for persisting, querying, and updating Opportunity lifecycle records."""

    def __init__(self, session: Session):
        super().__init__(session, OpportunityRecordORM)

    def get_by_fingerprint(self, fingerprint: str) -> Optional[OpportunityRecordORM]:
        """Retrieve an opportunity record by its deterministic fingerprint."""
        return (
            self.session.query(OpportunityRecordORM)
            .filter(OpportunityRecordORM.fingerprint == fingerprint)
            .first()
        )

    def save_or_update(self, record: OpportunityRecordORM) -> OpportunityRecordORM:
        """Persists a new opportunity or updates an existing record."""
        existing = self.get_by_fingerprint(record.fingerprint)
        if not existing:
            self.session.add(record)
            self.session.flush()
            return record

        # Update existing record fields
        existing.status = record.status
        existing.last_seen_at = record.last_seen_at
        existing.last_changed_at = record.last_changed_at
        existing.arbitrage_margin = record.arbitrage_margin
        existing.implied_probability_sum = record.implied_probability_sum
        existing.consecutive_misses = record.consecutive_misses
        existing.snapshot_json = record.snapshot_json
        if record.last_alerted_at is not None:
            existing.last_alerted_at = record.last_alerted_at
        if record.expired_at is not None:
            existing.expired_at = record.expired_at
        if record.delivery_status is not None:
            existing.delivery_status = record.delivery_status
        if record.alert_count > 0:
            existing.alert_count = record.alert_count

        self.session.flush()
        return existing

    def list_active(self, event_id: Optional[str] = None) -> List[OpportunityRecordORM]:
        """Fetch all non-expired opportunity records, optionally filtered by canonical_event_id."""
        query = self.session.query(OpportunityRecordORM).filter(
            OpportunityRecordORM.status != "EXPIRED"
        )
        if event_id:
            query = query.filter(OpportunityRecordORM.canonical_event_id == event_id)
        return query.all()

    def list_by_status(self, status: str) -> List[OpportunityRecordORM]:
        """Fetch opportunity records with a specific status."""
        return (
            self.session.query(OpportunityRecordORM)
            .filter(OpportunityRecordORM.status == status)
            .all()
        )

    def record_delivery_result(
        self,
        fingerprint: str,
        success: bool,
        delivery_status: str,
        alert_time: Optional[datetime] = None,
    ) -> Optional[OpportunityRecordORM]:
        """Records notification delivery result with strict safety semantics.

        If success is True: transitions state to ALERTED, sets last_alerted_at and increments alert_count.
        If success is False: preserves current status (e.g. NEW or UPDATED) and records delivery_status without marking ALERTED.
        """
        record = self.get_by_fingerprint(fingerprint)
        if not record:
            return None

        record.delivery_status = delivery_status
        now = alert_time or datetime.now(timezone.utc)

        if success:
            record.status = "ALERTED"
            record.last_alerted_at = now
            record.alert_count = (record.alert_count or 0) + 1

        self.session.flush()
        return record

    def increment_misses_for_market(
        self,
        canonical_event_id: str,
        market_key: str,
        active_fingerprints: Set[str],
        max_misses: int = 2,
        miss_time: Optional[datetime] = None,
    ) -> List[OpportunityRecordORM]:
        """Increments miss count for active opportunities on a successfully scanned market that were not present.

        When consecutive_misses reaches max_misses, the opportunity transitions to EXPIRED.
        """
        now = miss_time or datetime.now(timezone.utc)
        active_records = (
            self.session.query(OpportunityRecordORM)
            .filter(
                OpportunityRecordORM.canonical_event_id == canonical_event_id,
                OpportunityRecordORM.market_key == market_key,
                OpportunityRecordORM.status != "EXPIRED",
            )
            .all()
        )

        expired_records: List[OpportunityRecordORM] = []
        for record in active_records:
            if record.fingerprint not in active_fingerprints:
                record.consecutive_misses = (record.consecutive_misses or 0) + 1
                if record.consecutive_misses >= max_misses:
                    record.status = "EXPIRED"
                    record.expired_at = now
                    expired_records.append(record)

        self.session.flush()
        return expired_records
