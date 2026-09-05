"""
Opportunity Repository Implementation for Persistent Lifecycle Tracking
"""

from typing import List, Optional, Set
from datetime import datetime, timezone
from sqlalchemy.exc import IntegrityError
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
        """Persists a new opportunity or updates an existing record.

        P1-NEW-008: check-then-insert races (two writers observing no row,
        then both flushing) surface as IntegrityError on the UNIQUE
        fingerprint. Recover by rolling back the failed flush, re-reading
        the winner, and applying this write as an update — last-writer-wins,
        never a 500, never a duplicate.
        """
        existing = self.get_by_fingerprint(record.fingerprint)
        if not existing:
            self.session.add(record)
            try:
                self.session.flush()
            except IntegrityError:
                self.session.rollback()
                existing = self.get_by_fingerprint(record.fingerprint)
                if existing is None:
                    raise
                return self._apply_update(existing, record)
            return record

        return self._apply_update(existing, record)

    def _apply_update(
        self, existing: OpportunityRecordORM, record: OpportunityRecordORM
    ) -> OpportunityRecordORM:
        """Applies record fields onto an existing row and flushes."""
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
        if (record.alert_count or 0) > 0:
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

    def batch_process_market_misses(
        self,
        scanned_markets: dict,  # Dict[Tuple[str, str], Set[str]]
        max_misses: int = 2,
        miss_time: Optional[datetime] = None,
    ) -> List[OpportunityRecordORM]:
        """Batch processes market misses across all active opportunities in a single database round-trip.

        Inverts the N+1 market loop: loads active opportunities once upfront.
        If no active opportunities exist, returns immediately without issuing further queries or flushes.
        """
        now = miss_time or datetime.now(timezone.utc)
        active_records = self.list_active()
        if not active_records:
            return []

        expired_records: List[OpportunityRecordORM] = []
        any_modified = False

        for record in active_records:
            mkt_tuple = (record.canonical_event_id, record.market_key)
            if mkt_tuple not in scanned_markets:
                # Market was not scanned in this cycle, so not treated as absence
                continue

            active_fps = scanned_markets[mkt_tuple]
            if record.fingerprint not in active_fps:
                record.consecutive_misses = (record.consecutive_misses or 0) + 1
                any_modified = True
                if record.consecutive_misses >= max_misses:
                    record.status = "EXPIRED"
                    record.expired_at = now
                    expired_records.append(record)

        if any_modified:
            self.session.flush()

        return expired_records

