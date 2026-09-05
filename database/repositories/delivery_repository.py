"""
Delivery Repository Implementation for Persistent Notification Delivery & Retry Tracking
"""

from typing import List, Optional
from datetime import datetime, timezone
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from database.repositories.base_repository import BaseRepository
from database.models import DeliveryRecordORM


class DeliveryRepository(BaseRepository[DeliveryRecordORM]):
    """Repository for persisting, querying, and updating notification delivery records."""

    def __init__(self, session: Session):
        super().__init__(session, DeliveryRecordORM)

    def get_by_idempotency_key(self, idempotency_key: str) -> Optional[DeliveryRecordORM]:
        """Retrieve a delivery record by its deterministic idempotency key."""
        return (
            self.session.query(DeliveryRecordORM)
            .filter(DeliveryRecordORM.idempotency_key == idempotency_key)
            .first()
        )

    def save_or_update(self, record: DeliveryRecordORM) -> DeliveryRecordORM:
        """Persists a new delivery record or updates an existing record.

        P1-NEW-008: same IntegrityError recovery as OpportunityRepository —
        rollback, re-read the winning row by idempotency key, apply as update.
        """
        existing = self.get_by_idempotency_key(record.idempotency_key)
        if not existing:
            self.session.add(record)
            try:
                self.session.flush()
            except IntegrityError:
                self.session.rollback()
                existing = self.get_by_idempotency_key(record.idempotency_key)
                if existing is None:
                    raise
            else:
                return record

        existing.state = record.state
        existing.attempt_count = record.attempt_count
        existing.max_attempts = record.max_attempts
        existing.first_attempt_at = record.first_attempt_at
        existing.last_attempt_at = record.last_attempt_at
        existing.next_retry_at = record.next_retry_at
        existing.delivered_at = record.delivered_at
        existing.last_error = record.last_error
        existing.last_error_category = record.last_error_category
        existing.payload_snapshot_json = record.payload_snapshot_json

        self.session.flush()
        return existing

    def list_by_fingerprint(self, fingerprint: str) -> List[DeliveryRecordORM]:
        """Retrieve all delivery records for an opportunity fingerprint."""
        return (
            self.session.query(DeliveryRecordORM)
            .filter(DeliveryRecordORM.opportunity_fingerprint == fingerprint)
            .order_by(DeliveryRecordORM.lifecycle_version.asc(), DeliveryRecordORM.created_at.asc())
            .all()
        )

    def list_pending_or_retryable(
        self,
        current_time: Optional[datetime] = None,
        limit: int = 100,
    ) -> List[DeliveryRecordORM]:
        """Queries for delivery records that are either PENDING (initial) or RETRY_PENDING with due next_retry_at."""
        now = current_time or datetime.now(timezone.utc)
        return (
            self.session.query(DeliveryRecordORM)
            .filter(
                DeliveryRecordORM.state.in_(["PENDING", "RETRY_PENDING"]),
                (DeliveryRecordORM.next_retry_at == None) | (DeliveryRecordORM.next_retry_at <= now),  # noqa: E711
            )
            .order_by(DeliveryRecordORM.created_at.asc())
            .limit(limit)
            .all()
        )

    def mark_superseded_for_fingerprint(
        self,
        fingerprint: str,
        current_version: int,
    ) -> int:
        """Marks older pending or retry-pending delivery records for the given opportunity as SUPERSEDED.

        Ensures stale update retries are never delivered when a newer material snapshot exists.
        """
        records = (
            self.session.query(DeliveryRecordORM)
            .filter(
                DeliveryRecordORM.opportunity_fingerprint == fingerprint,
                DeliveryRecordORM.lifecycle_version < current_version,
                DeliveryRecordORM.state.in_(["PENDING", "RETRY_PENDING"]),
            )
            .all()
        )
        count = 0
        for rec in records:
            rec.state = "SUPERSEDED"
            rec.next_retry_at = None
            rec.last_error = f"Superseded by newer lifecycle version {current_version}"
            count += 1

        if count > 0:
            self.session.flush()
        return count
